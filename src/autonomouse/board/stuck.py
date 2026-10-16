"""Is a running worker stuck? The three signals of `docs/orchestrator/while-workers-run.md`,
read from its card (sys-d35).

The orchestrator runs `autonomouse card stuck <id> --agent-id <agentId>` on each running
card each time its Monitor prints a change or ends. `stuck` returns the first signal that
fires, in the section's order, or None:

1. **repeat**: the worker's newest next step is one it already wrote in the same
   `iteration`, word for word with numbers aside, so the step did not happen.
2. **climb**: the card has no PR, and a next step of this `iteration` names review round 3
   or later (rule 9 allows two per attempt), or the worker started more than ten
   sub-agents since its last note (two review rounds of five passes).
3. **wait**: the newest next step waits on something outside the card: the project's
   default branch or the base branch turning green, another card, the human, or a later
   wake.

What it reads: the card's history rows, each `card progress` row with the text of the next
step it wrote (`board.history.progress_note`); `handoff.next_step`; `pr`; `base_branch`;
the project's `default_branch`, which the caller passes; and the number of sub-agents,
which the board does not record (`subagents_since` counts them from Claude Code's session
folder). Only the running worker's notes count: the rows before the card's
latest `card start` were another worker's.

The wordings it knows are the ones the charters prescribe and wake 33 showed. A note that
says the same in other words is stuck too; the orchestrator reads the notes for that.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autonomouse.board.history import read_progress_note
from autonomouse.board.schema import Card, HistoryEntry, Stack

ROUNDS_PER_ATTEMPT = 2  # docs/rules.md, item 9
SUBAGENTS_PER_NOTE = 10  # two review rounds of five passes


@dataclass(frozen=True)
class Signal:
    """One stuck signal: `name` is `repeat`, `climb` or `wait`; `detail` quotes what fired it."""

    name: str
    detail: str

    def __str__(self) -> str:
        return f"{self.name}: {self.detail}"


@dataclass(frozen=True)
class _Note:
    at: str
    sets_next_step: bool
    next_step: str | None


def stuck(card: Card, *, default_branch: str, subagents: int | None = None) -> Signal | None:
    """The first stuck signal that fires on `card`'s running worker, or None.

    `default_branch` is the `default_branch` of `card`'s project. `subagents` is how many
    sub-agents the worker started since its last note; None leaves that half of **climb**
    out. A card that is not `in_progress` has no running worker, and nothing fires on it.
    """
    if card.stack != Stack.IN_PROGRESS:
        return None
    notes = _worker_notes(card.history)
    # The handoff's next step is the running worker's once it has written one: no other
    # writer touches it between a `card start` and the worker's notes.
    newest = card.handoff.next_step if any(n.sets_next_step for n in notes) else None
    return (
        _repeat(notes)
        or _climb(card, notes, newest, subagents)
        or _wait(card, newest, default_branch)
    )


def _worker_notes(history: list[HistoryEntry]) -> list[_Note]:
    """The `card progress` rows the running worker wrote in its current `iteration`."""
    iteration = 0  # `card new` starts every card at 0
    notes: list[_Note] = []
    for row in history:
        if _is_start(row):
            notes = []
            continue
        progress = read_progress_note(row.note)
        if progress is None:
            continue
        if progress.iteration is not None and progress.iteration != iteration:
            iteration, notes = progress.iteration, []
        notes.append(_Note(row.at, progress.sets_next_step, progress.next_step))
    return notes


def _is_start(row: HistoryEntry) -> bool:
    """A row `board.runs.card_start` wrote: todo -> in_progress (`started`), or an
    in_progress card taken over from a dead claim (`resumed; …`)."""
    if row.to != Stack.IN_PROGRESS.value:
        return False
    return row.from_ != Stack.IN_PROGRESS.value or (row.note or "").startswith("resumed")


def _repeat(notes: list[_Note]) -> Signal | None:
    written = [n for n in notes if n.next_step is not None and _words(n.next_step)]
    if len(written) < 2:
        return None
    newest = written[-1]
    for earlier in written[:-1]:
        if _words(earlier.next_step) == _words(newest.next_step):
            return Signal(
                "repeat",
                f'the next step "{newest.next_step}" is the one already written at '
                f"{earlier.at}",
            )
    return None


def _words(text: str) -> str:
    """A next step's words in lower case, every number as `#`: `If round 3 clean: push`
    and `if round 4 clean - push` are the same step."""
    tokens = re.findall(r"[a-z]+|\d+", text.lower())
    return " ".join("#" if t.isdigit() else t for t in tokens)


def _climb(
    card: Card, notes: list[_Note], newest: str | None, subagents: int | None
) -> Signal | None:
    if card.pr:
        return None
    steps = [n.next_step for n in notes if n.next_step] + ([newest] if newest else [])
    named = [(number, step) for step in steps for number in _rounds(step)]
    if named:
        top, step = max(named, key=lambda pair: pair[0])
        if top > ROUNDS_PER_ATTEMPT:
            return Signal(
                "climb",
                f"no PR, and a next step of this attempt names review round {top} "
                f'(rule 9 allows {ROUNDS_PER_ATTEMPT} per attempt): "{step}"',
            )
    if subagents is not None and subagents > SUBAGENTS_PER_NOTE:
        return Signal(
            "climb",
            f"no PR, and {subagents} sub-agents started since the last note at "
            f"{card.handoff.updated_at} (more than {SUBAGENTS_PER_NOTE}, two review "
            "rounds of five passes)",
        )
    return None


_NUMBER_WORDS = {
    "one": 1, "first": 1, "two": 2, "second": 2, "three": 3, "third": 3,
    "four": 4, "fourth": 4, "five": 5, "fifth": 5, "six": 6, "sixth": 6,
    "seven": 7, "seventh": 7, "eight": 8, "eighth": 8, "nine": 9, "ninth": 9,
    "ten": 10, "tenth": 10,
}  # fmt: skip
_NUMBER = "|".join([r"\d+", *sorted(_NUMBER_WORDS, key=len, reverse=True)])
# `round 3`, `Round-4`, `rounds 8-11`
_ROUND_NUMBER = re.compile(
    rf"\brounds?\s*[-#:]?\s*({_NUMBER})\b(?:\s*(?:-|–|to|and|through)\s*(\d+)\b)?",
    re.IGNORECASE,
)
# `third round`, `3rd review round`, `two review rounds`
_NUMBER_ROUND = re.compile(
    rf"\b({_NUMBER})(?:st|nd|rd|th)?\s+(?:review\s+)?rounds?\b", re.IGNORECASE
)


def _rounds(text: str) -> list[int]:
    """Every review round number `text` names."""
    found = []
    for pattern in (_ROUND_NUMBER, _NUMBER_ROUND):
        for match in pattern.finditer(text):
            for group in match.groups():
                if group:
                    found.append(int(group) if group.isdigit() else _NUMBER_WORDS[group.lower()])
    return found


_CARD_ID = r"[a-z0-9]+-[a-z]\d+"
_OTHER_CARD = re.compile(
    r"\b(?:wait(?:s|ing)?\s+(?:for|on|until)|once|until|after|when|blocked\s+(?:on|by)"
    rf"|depends?\s+on|behind)\s+(?:the\s+)?(?:card\s+)?({_CARD_ID})\b",
    re.IGNORECASE,
)
# Waiting on the human, or the human having to act: not a feature that serves the human
# ("when the human replies, route it"), which cards on this board describe all the time.
_HUMAN = re.compile(
    r"\b(?:wait(?:s|ing)?\s+(?:for|on)|asks?|until)\s+(?:the\s+)?human\b(?!-)"
    r"|\bhuman\b(?!-)\s+(?:still\s+)?(?:to|must|should|needs?\s+to|has\s+to)\b",
    re.IGNORECASE,
)
# Waiting for a later wake: not a wake as the subject of the work ("test that the next
# wake picks it up").
_LATER_WAKE = re.compile(
    r"\b(?:wait(?:s|ing)?\s+(?:for|until)|until|in|at|on|for|during)\s+(?:a|the|another)"
    r"\s+(?:(?:next|later|future|following|new)\s+)?wake\b",
    re.IGNORECASE,
)


def _branch_patterns(default_branch: str, base_branch: str | None) -> list[re.Pattern[str]]:
    """The project's default branch or the base branch turning green or red, or waited
    for. A plan to check it (`if main is red, report blocked`) is not a wait. The names
    are the two branches themselves, `base branch` and `base`, never a literal `main`: on
    a project whose default branch is another, `main` is no branch the card waits on."""
    names = ["base branch", "base"]
    for name in (default_branch, base_branch):
        if name and name not in names:
            names.insert(0, name)
    branch = r"(?:origin/)?(?:" + "|".join(re.escape(n) for n in names) + ")"
    verbs = (
        r"(?:(?:is|are|turns?|goes|gets|becomes?|to|turn|go|be|get|being|stays?|still"
        r"|again|back|not)\s+)*"
    )
    return [
        re.compile(
            rf"(?<!/)(?<!\bif\s)(?<!\bif\sthe\s)\b{branch}(?:'s)?"
            rf"(?:\s+(?:branch|ci|checks?|build|tests?))?\s+{verbs}(?:green|red|fixed)\b",
            re.IGNORECASE,
        ),
        re.compile(rf"\b(?:red|green|broken)\s+{branch}\b", re.IGNORECASE),
        re.compile(
            rf"\bwait(?:s|ing)?\s+(?:for|on|until)\s+(?:the\s+)?{branch}\b", re.IGNORECASE
        ),
    ]


def _wait(card: Card, newest: str | None, default_branch: str) -> Signal | None:
    if not newest:
        return None

    def fired(what: str, words: str) -> Signal:
        return Signal("wait", f'the next step waits on {what} ("{words}"): "{newest}"')

    for pattern in _branch_patterns(default_branch, card.base_branch):
        if match := pattern.search(newest):
            return fired(f"{default_branch} or the base branch", match.group(0))
    for match in _OTHER_CARD.finditer(newest):
        if match.group(1).lower() != card.id:
            return fired(f"another card, {match.group(1)}", match.group(0))
    if match := _HUMAN.search(newest):
        return fired("the human", match.group(0))
    if match := _LATER_WAKE.search(newest):
        return fired("a later wake", match.group(0))
    return None


def subagents_since(card: Card, agent_id: str, projects_dir: Path) -> int:
    """How many sub-agents the worker `agent_id` started after the card's last note
    (`handoff.updated_at`).

    Claude Code writes `agent-<agentId>.meta.json` when an agent starts, into
    `<projects_dir>/<cwd-slug>/<session id>/subagents/`, with the id of the agent that
    spawned it as `parentAgentId`. The session is the orchestrator's, the card's
    `assigned.session_id`; every `<cwd-slug>` folder is searched, as
    `board.runs.transcript_last_write` does.
    """
    session = card.assigned.session_id
    if not session or not card.handoff.updated_at:
        return 0
    since = datetime.fromisoformat(card.handoff.updated_at).timestamp()
    count = 0
    for meta in projects_dir.glob(f"*/{session}/subagents/agent-*.meta.json"):
        if meta.stat().st_mtime <= since:
            continue
        if json.loads(meta.read_text()).get("parentAgentId") == agent_id:
            count += 1
    return count
