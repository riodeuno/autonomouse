"""The behaviour log of a wake: how each agent behaved, never what it built.

`derive` reads the run's orchestrator transcript and its sub-agents' (`transcript`) and
the board's cards, and returns the events and the summary. `write` puts them in
`<logs>/<run_id>.jsonl` (one event per line, in time order) and
`<logs>/<run_id>.summary.json`, where `<logs>` is the `logs` folder beside the board
(`~/.local/share/autonomouse/logs`). `format_summary` is what `autonomouse log show`
prints. `docs/behaviour-log.md` lists every row and where it comes from.

Every row comes from data that needs no reading of shell commands: the transcripts'
records (who each agent is, its messages' times and token usage, its tool calls by
name), the board's card histories and decisions, the run record, and `gh`. The log holds
classes, counts, ids, timestamps and URLs only: no code, diffs, file contents, command
arguments, notes or agent prose. The transcripts and the board are only read.

What counts as what:

- **Roles.** The session's own agent is the orchestrator. A sub-agent the orchestrator
  spawned with a prompt naming a card file (`…/cards/<id>.json`) is that card's worker;
  every other sub-agent is a helper, on its spawner's card.
- **Turns** are the agent's model responses; **duration** runs from its first message to
  its last; the **longest idle gap** is the longest time between two of its messages;
  **tools** counts its tool calls by tool name.
- **Cost.** Each agent's cost, and the run's cost per model, are priced from the token
  usage of the transcripts' messages (`price`); a model with no rates has no cost, and
  the summary names it (`cost.unpriced_models`). The run's total is claude's own, from
  the run record (`result.total_cost_usd`).
- **Board writes** are the history rows that name the run: every `autonomouse` command
  that writes a card appends one. The command is read from the start of the note the
  command wrote, and, when none is named there, from the row's stacks
  (`history_command`).
- **Stack transitions** are the board writes that moved a card from one stack to
  another.
- **Phase changes** and **iterations** come from `card progress` rows (`phase=`,
  `iteration=` in their note). An iteration counts when it is above the card's iteration
  before the row; its cause is the phase the card was in: testing → `test`, ci →
  `check`, review → `review`, anything else → `other`. A `card edit` row that reset the
  attempts sets both back.
- **Decisions** are the card decisions whose history row into `decision_needed`, written
  when they were asked, names the run; the kind comes from words in the question;
  `answered_by` is the run id of the history row that answered it, or `nobody yet`.
- **PR state at finish**, for each card of the run: from `gh pr view` on the card's `pr`
  at the time of the derivation: `open`, `mergeable`, `conflicting`, `merged`, `closed`,
  `none` (no PR) or `unknown` (the lookup failed).
- **Jev answers**: the run record's `jev` entries (question, card, source, answer,
  confidence, applied), without their `reason`, which is prose.
- **Times that cannot be true** (`clock`): a card's history row later than the row
  after it (`out_of_order`, on the log of either row's run); a history row of this run
  later than the run's last transcript message (`ahead_of_run_clock`), which no clock
  could have written; and a decision answered before it was asked
  (`answered_before_asked`, on the log of the run whose history row asked or answered
  it). A run without a transcript gets the first and the last only.
- **Usage-limit stalls** (`stalls`): every `system`/`api_error` record, in any agent's
  transcript, that is a 429 with `error.rateLimits.rateLimitType` set (Claude Code's own
  retry logic writes one each time a request is refused for the account's usage limit)
  groups, by its reset time, into one stall: the earliest such record's timestamp
  (`start`), the reset time (`reset`), and every agent whose transcript recorded one
  (`agents`).

The board's rows are read at the time of the derivation, and each list of them is null
when the board could not be read. A write that changed a card without a history row (a
hand edit) shows only in the board's git history, which the behaviour review reads
(`autonomouse.review.behaviour`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from autonomouse.behaviour import price
from autonomouse.behaviour.transcript import (
    Agent,
    Session,
    TranscriptUnavailable,
    load_session,
    parse_time,
)
from autonomouse.board.commands import CommandError, card_list, card_show
from autonomouse.board.history import read_progress_note
from autonomouse.board.runs import Run, claude_projects_dir, load_run
from autonomouse.board.schema import BoardValidationError, Card, HistoryEntry

_CARD_FILE = re.compile(r"cards/([a-z0-9][a-z0-9-]*)\.json")
_PHASE_CAUSE = {"testing": "test", "ci": "check", "review": "review"}
_DECISION_KINDS = [
    (
        "credential",
        r"credential|token|secret|api[ _-]?key|password|log ?in|auth|permission|\bscope|\bPAT\b",
    ),
    ("spend", r"spend|cost|budget|\bpay|price|billing|\$\s?\d|\busd\b|dollar"),
    (
        "goal_fork",
        r"\bgoal|reading|interpret|\bfork|two ways|which (?:way|of the two)|meant",
    ),
    ("default", r"\bdefault"),
    ("judgment", r"taste|judge?ment|prefer|style|look|wording|naming"),
]
_JEV_KEYS = ("question", "card", "source", "answer", "confidence", "applied")
# The writer of a history row that keeps the card's stack, by the start of the note it
# writes (board/commands.py, board/runs.py, inbox.py); any other such row is a
# `card edit`'s.
_NOTE_COMMANDS = (
    ("progress: ", "card progress"),
    ("resumed; ", "card start"),
    ("retargeted: ", "follow-up"),
    ("worktree removed (", "worktree cleanup"),
    ("reply to answered decision ", "notify poll"),
    ("reply to cancelled decision ", "notify poll"),
)
_RESET = "attempts reset"  # what `card edit` adds to a row that resets the attempts
BOARD_ROWS = ("board_writes", "transitions", "phases", "iterations", "decisions")
_BOARD_EVENTS = {
    "transitions": "transition",
    "phases": "phase",
    "iterations": "iteration",
    "decisions": "decision",
}


# ------------------------------------------------------------------- agents --


@dataclass
class Who:
    key: str
    role: str  # orchestrator | worker | helper
    card: str | None
    parent: str | None
    agent_type: str | None
    model: str | None
    effort: str | None
    agent: Agent


def _most_common(values: list[Any]) -> Any:
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else None


def _who(
    agent: Agent,
    role: str,
    card: str | None,
    parent: str | None,
    agent_type: str | None,
) -> Who:
    assistant = [r for r in agent.records if r.get("type") == "assistant"]
    models = [
        (r.get("message") or {}).get("model")
        for r in assistant
        if (r.get("message") or {}).get("model") != "<synthetic>"
    ]
    return Who(
        key=agent.key,
        role=role,
        card=card,
        parent=parent,
        agent_type=agent_type,
        model=_most_common(models),
        effort=_most_common([r.get("effort") for r in assistant]),
        agent=agent,
    )


def agents_of(session: Session) -> list[Who]:
    """Every agent of the run with its role and card, orchestrator first."""
    found = {
        "orchestrator": _who(session.orchestrator, "orchestrator", None, None, None)
    }
    for sub in session.subagents:
        spawner = found.get(sub.parent or "")
        prompt = str((sub.spawn.input if sub.spawn else {}).get("prompt", ""))
        named = _CARD_FILE.search(prompt)
        if spawner is not None and spawner.role == "orchestrator" and named:
            role, card = "worker", named.group(1)
        else:
            role, card = "helper", spawner.card if spawner else None
        requested = (sub.spawn.input if sub.spawn else {}).get("subagent_type")
        agent_type = sub.meta.get("agentType") or (
            requested if isinstance(requested, str) else None
        )
        found[sub.key] = _who(sub, role, card, sub.parent, agent_type)
    return list(found.values())


def _messages(agent: Agent) -> list[str]:
    return sorted(
        r["timestamp"]
        for r in agent.records
        if r.get("type") in ("assistant", "user") and r.get("timestamp")
    )


def _seconds(start: str, end: str) -> float:
    return round((parse_time(end) - parse_time(start)).total_seconds(), 3)


# -------------------------------------------------------------------- board --


def decision_kind(text: str) -> str:
    for kind, pattern in _DECISION_KINDS:
        if re.search(pattern, text, re.IGNORECASE):
            return kind
    return "other"


def history_command(row: HistoryEntry) -> str:
    """The writer of a history row, read from the start of its note first, since a
    command named there (`_NOTE_COMMANDS`) can move a card between stacks (`notify
    poll`'s go-ahead reply moves a card from `backlog` to `todo`); only when no
    command is named there do the row's stacks decide. `no command` for a move no
    command makes."""
    note = row.note or ""
    named = next(
        (command for start, command in _NOTE_COMMANDS if note.startswith(start)), None
    )
    if named is not None:
        return named
    if row.from_ is None:
        return "card new"
    if row.from_ == row.to:
        return "card edit"
    if row.to == "cancelled":
        return "card cancel"
    if row.to == "decision_needed":
        return "card ask"
    if row.from_ == "decision_needed":
        return "card answer"
    if (row.from_, row.to) == ("todo", "in_progress"):
        return "card start"
    if row.to == "todo":
        return "card move"
    if (row.from_, row.to) == ("in_progress", "done"):
        return "card finish"
    return "no command"


def _history_run(card: Card, at: str | None, *, into: bool) -> str | None:
    """The run id of the card's history row at `at` that moved it into (`into`) or out
    of `decision_needed`; None when it has none."""
    for row in card.history:
        moved = (
            row.from_ != row.to and (row.to if into else row.from_) == "decision_needed"
        )
        if moved and row.at == at:
            return row.run_id
    return None


def _decisions(card: Card, run_id: str) -> list[dict[str, Any]]:
    return [
        {
            "at": d.asked_at,
            "card": card.id,
            "decision": d.id,
            "kind": decision_kind(d.question),
            "answered_by": (
                "nobody yet"
                if d.answer is None
                else _history_run(card, d.answered_at, into=False)
            ),
            "answered_at": d.answered_at,
        }
        for d in card.decisions
        if _history_run(card, d.asked_at, into=True) == run_id
    ]


def board_rows(cards: list[Card], run_id: str) -> dict[str, list[dict[str, Any]]]:
    """The run's rows from the board's cards (see the module text): its board writes by
    command, and its stack transitions, phase changes, iterations and decisions, each
    list in time order."""
    writes: dict[str, dict[str, Any]] = {}
    rows: dict[str, list[dict[str, Any]]] = {key: [] for key in _BOARD_EVENTS}
    for card in cards:
        phase, iteration = None, 0
        for row in card.history:
            command = history_command(row)
            ours = row.run_id == run_id
            if ours:
                write = writes.setdefault(
                    command, {"means": command, "count": 0, "cards": []}
                )
                write["count"] += 1
                if card.id not in write["cards"]:
                    write["cards"].append(card.id)
                if command != "card new" and row.from_ != row.to:
                    rows["transitions"].append(
                        {
                            "at": row.at,
                            "card": card.id,
                            "from": row.from_,
                            "to": row.to,
                            "means": command,
                        }
                    )
            progress = (
                read_progress_note(row.note) if command == "card progress" else None
            )
            if progress is not None and progress.iteration is not None:
                if ours and progress.iteration > iteration:
                    rows["iterations"].append(
                        {
                            "at": row.at,
                            "card": card.id,
                            "iteration": progress.iteration,
                            "cause": _PHASE_CAUSE.get(phase or "", "other"),
                        }
                    )
                iteration = progress.iteration
            if progress is not None and progress.phase not in (None, phase):
                if ours:
                    rows["phases"].append(
                        {"at": row.at, "card": card.id, "phase": progress.phase}
                    )
                phase = progress.phase
            if command == "card edit" and _RESET in (row.note or ""):
                phase, iteration = None, 0
            elif command == "card move" and row.from_ == "done":
                phase = "writing"  # a reopened card is written again
        rows["decisions"] += _decisions(card, run_id)
    for found in rows.values():
        found.sort(key=lambda row: row["at"] or "")
    return {"board_writes": sorted(writes.values(), key=lambda w: w["means"]), **rows}


def _clock(
    cards: list[Card] | None, run_id: str, last_message: str | None
) -> list[dict[str, Any]] | None:
    """The times on the board this run wrote that cannot be true (see the module text);
    None when the board could not be read. `last_message` is the run's last transcript
    message; None skips the rows ahead of it."""
    if cards is None:
        return None
    end = parse_time(last_message).replace(microsecond=0) if last_message else None
    found: list[dict[str, Any]] = []
    for card in cards:
        for row, after in pairwise(card.history):
            if run_id in (row.run_id, after.run_id) and parse_time(
                after.at
            ) < parse_time(row.at):
                found.append(
                    {
                        "class": "out_of_order",
                        "card": card.id,
                        "at": row.at,
                        "run_id": row.run_id,
                        "next_at": after.at,
                        "next_run_id": after.run_id,
                    }
                )
        for row in card.history:
            if end is not None and row.run_id == run_id and parse_time(row.at) > end:
                found.append(
                    {
                        "class": "ahead_of_run_clock",
                        "card": card.id,
                        "at": row.at,
                        "to": row.to,
                        "last_message": last_message,
                    }
                )
        for d in card.decisions:
            if d.answered_at is None or parse_time(d.answered_at) >= parse_time(
                d.asked_at
            ):
                continue
            writers = {
                h.run_id
                for h in card.history
                if (h.to == "decision_needed" and h.at == d.asked_at)
                or (h.from_ == "decision_needed" and h.at == d.answered_at)
            }
            if run_id in writers:
                found.append(
                    {
                        "class": "answered_before_asked",
                        "card": card.id,
                        "decision": d.id,
                        "asked_at": d.asked_at,
                        "answered_at": d.answered_at,
                    }
                )
    return found


# ------------------------------------------------------------------- derive --


def gh_pr_state(url: str) -> str:
    """The PR's state now, by `gh pr view`: open, mergeable, conflicting, merged, closed,
    or unknown when gh cannot say."""
    try:
        done = subprocess.run(
            ["gh", "pr", "view", url, "--json", "state,mergeable"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        data = json.loads(done.stdout) if done.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        data = {}
    state = data.get("state")
    if state == "OPEN":
        return {"MERGEABLE": "mergeable", "CONFLICTING": "conflicting"}.get(
            data.get("mergeable"), "open"
        )
    return {"MERGED": "merged", "CLOSED": "closed"}.get(state, "unknown")


def _base_summary(run: Run) -> dict[str, Any]:
    result = run.result if isinstance(run.result, dict) else {}
    return {
        "run_id": run.id,
        "session_id": run.session_id,
        "project": run.project,
        "derived_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": None,
        "agents": [],
        "helpers_spawned_by_workers": [],
        **dict.fromkeys(BOARD_ROWS),
        "prs": [],
        "stalls": [],
        "cost": {
            "total_usd": result.get("total_cost_usd"),
            "by_model": {},
            "unpriced_models": [],
        },
        "jev": [{k: e.get(k) for k in _JEV_KEYS} for e in run.jev],
        "clock": [],
    }


def derive(
    root: Path,
    run_id: str,
    *,
    projects_dir: Path | None = None,
    pr_state: Callable[[str], str] = gh_pr_state,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The run's events, in time order, and its summary. A run without a session, or
    whose transcript is missing or unreadable, still gets its rows from the board, and
    its summary's reason says why it has no agents."""
    run = load_run(root, run_id)
    summary = _base_summary(run)
    try:
        cards: list[Card] | None = card_list(root)
    except (CommandError, BoardValidationError, OSError):
        cards = None
    board = board_rows(cards, run_id) if cards is not None else None
    for key in BOARD_ROWS:
        summary[key] = board[key] if board is not None else None
    events = [
        {"at": row["at"], "event": event, **row}
        for key, event in _BOARD_EVENTS.items()
        for row in (board or {}).get(key, [])
    ]

    session = None
    if not run.session_id:
        summary["reason"] = "no session started"
    else:
        try:
            session = load_session(
                run.session_id, run_id, projects_dir or claude_projects_dir()
            )
        except TranscriptUnavailable as e:
            summary["reason"] = "transcript unavailable"
            summary["detail"] = str(e)
    last = None
    if session is not None:
        agents = agents_of(session)
        events += _agent_rows(agents, summary)
        events += _pr_rows(root, run, agents, pr_state, summary)
        summary["stalls"] = _stalls(agents)
        stamps = [s for w in agents for s in _messages(w.agent)]
        last = max(stamps, key=parse_time) if stamps else None
        if session.whole_session:
            summary["note"] = (
                "no prompt in the session names a run id; the whole session was read"
            )
        if session.skipped_lines:
            summary["skipped_lines"] = session.skipped_lines
    summary["clock"] = _clock(cards, run_id, last)
    events.sort(key=lambda e: e["at"] or "")
    return events, summary


def _add_costs(cost: dict[str, Any], models: dict[str, float | None]) -> None:
    """Add one agent's cost per model to the run's."""
    for model, usd in models.items():
        cost["by_model"][model] = (
            None if usd is None else (cost["by_model"].get(model) or 0.0) + usd
        )
        if usd is None and model not in cost["unpriced_models"]:
            cost["unpriced_models"].append(model)


def _agent_rows(agents: list[Who], summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Each agent's row in the summary, its start and end events, and the helpers that
    workers spawned."""
    events: list[dict[str, Any]] = []
    by_key = {w.key: w for w in agents}
    for who in agents:
        stamps = _messages(who.agent)
        if not stamps:
            continue
        longest = max(
            ((_seconds(a, b), a) for a, b in pairwise(stamps)), default=(0.0, stamps[0])
        )
        models = price.cost_by_model(who.agent.records)
        _add_costs(summary["cost"], models)
        cost = None if None in models.values() else sum(models.values(), 0.0)
        turns = len(
            {
                (r.get("message") or {}).get("id")
                or r.get("requestId")
                or r.get("uuid")
                for r in who.agent.records
                if r.get("type") == "assistant"
            }
        )
        duration = _seconds(stamps[0], stamps[-1])
        tools = dict(sorted(Counter(c.name for c in who.agent.calls).items()))
        row = {
            "agent": who.key,
            "role": who.role,
            "card": who.card,
            "parent": who.parent,
            "agent_type": who.agent_type,
            "model": who.model,
            "effort": who.effort,
            "started_at": stamps[0],
            "ended_at": stamps[-1],
            "turns": turns,
            "duration_s": duration,
            "cost_usd": cost,
            "tools": tools,
            "longest_idle_s": longest[0],
            "longest_idle_from": longest[1],
            "subagents_spawned": sum(1 for w in agents if w.parent == who.key),
        }
        summary["agents"].append(row)
        events.append(
            {
                "at": stamps[0],
                "event": "agent_start",
                **{k: row[k] for k in ("agent", "role", "card", "parent")},
                **{k: row[k] for k in ("agent_type", "model", "effort")},
            }
        )
        events.append(
            {
                "at": stamps[-1],
                "event": "agent_end",
                **{k: row[k] for k in ("agent", "role", "card", "turns")},
                **{k: row[k] for k in ("duration_s", "cost_usd", "tools")},
                "longest_idle_s": longest[0],
            }
        )
        spawner = by_key.get(who.parent or "")
        if who.role == "helper" and spawner is not None and spawner.role == "worker":
            summary["helpers_spawned_by_workers"].append(
                {
                    "worker": spawner.key,
                    "card": spawner.card,
                    "agent": who.key,
                    "agent_type": who.agent_type,
                }
            )
    return events


def _pr_rows(
    root: Path,
    run: Run,
    agents: list[Who],
    pr_state: Callable[[str], str],
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """The PR state at finish of every card the run worked on: its workers' cards and
    the cards in its run record."""
    cards = [w.card for w in agents if w.role == "worker" and w.card] + list(run.cards)
    at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = []
    for card_id in dict.fromkeys(cards):
        try:
            url = card_show(root, card_id).pr
        except (CommandError, BoardValidationError, OSError):
            url = None
        state = pr_state(url) if url else "none"
        summary["prs"].append({"card": card_id, "url": url, "state": state})
        events.append(
            {"at": at, "event": "pr", "card": card_id, "url": url, "state": state}
        )
    return events


def _rate_limit_hits(who: Who) -> list[tuple[str, int]]:
    """The agent's own 429 rate-limit errors: each record's timestamp and the reset
    time its `rateLimits.rateLimitType` names, as a Unix epoch."""
    found = []
    for record in who.agent.records:
        if record.get("type") != "system" or record.get("subtype") != "api_error":
            continue
        error = record.get("error")
        timestamp = record.get("timestamp")
        if not isinstance(error, dict) or error.get("status") != 429 or not timestamp:
            continue
        limits = error.get("rateLimits")
        resets_at = limits.get("resetsAt") if isinstance(limits, dict) else None
        if (
            isinstance(limits, dict)
            and limits.get("rateLimitType")
            and isinstance(resets_at, (int, float))
        ):
            found.append((timestamp, int(resets_at)))
    return found


def _stalls(agents: list[Who]) -> list[dict[str, Any]]:
    """The run's usage-limit stalls (see the module text), earliest first."""
    by_reset: dict[int, dict[str, Any]] = {}
    for who in agents:
        for timestamp, resets_at in _rate_limit_hits(who):
            stall = by_reset.setdefault(
                resets_at,
                {
                    "start": timestamp,
                    "reset": datetime.fromtimestamp(resets_at, UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "agents": [],
                },
            )
            stall["start"] = min(stall["start"], timestamp)
            if who.key not in stall["agents"]:
                stall["agents"].append(who.key)
    return sorted(by_reset.values(), key=lambda s: s["start"])


# -------------------------------------------------------------------- files --


def logs_dir(root: Path) -> Path:
    """`logs` beside the board: `~/.local/share/autonomouse/logs` by default."""
    return root.parent / "logs"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write(root: Path, run_id: str, **kwargs: Any) -> Path:
    """Derive the run's log and write `<run_id>.jsonl` and `<run_id>.summary.json`;
    return the summary's path."""
    events, summary = derive(root, run_id, **kwargs)
    folder = logs_dir(root)
    _write_atomic(
        folder / f"{run_id}.jsonl", "".join(json.dumps(e) + "\n" for e in events)
    )
    path = folder / f"{run_id}.summary.json"
    _write_atomic(path, json.dumps(summary, indent=2) + "\n")
    return path


def read_summary(root: Path, run_id: str) -> dict[str, Any]:
    return json.loads((logs_dir(root) / f"{run_id}.summary.json").read_text())


# ------------------------------------------------------------------ display --


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    return (
        f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
        if seconds >= 3600
        else f"{seconds // 60}m{seconds % 60:02d}s"
    )


def _counts(counter: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in counter.items()) or "none"


def _name(row: dict[str, Any]) -> str:
    if row.get("role") == "orchestrator":
        return "orchestrator"
    return f"{row.get('role')} {row.get('card') or '-'} ({row.get('agent')})"


def clock_text(row: dict[str, Any]) -> str:
    """One `clock` row in words: the card, the class and the times."""
    if row["class"] == "out_of_order":
        return (
            f"{row['card']}: history row {row['at']} (run {row['run_id']}) comes before "
            f"the earlier row {row['next_at']} (run {row['next_run_id']})"
        )
    if row["class"] == "ahead_of_run_clock":
        return (
            f"{row['card']}: history row {row['at']} (to {row['to'] or '-'}) is later "
            f"than the run's last transcript message {row['last_message']}"
        )
    return (
        f"{row['card']}: decision {row['decision']} answered {row['answered_at']}, "
        f"before it was asked {row['asked_at']}"
    )


def _sections(
    lines: list[str],
    summary: dict[str, Any],
    sections: list[tuple[str, str, Callable[[dict[str, Any]], str]]],
) -> None:
    for title, key, line in sections:
        lines.append("")
        lines.append(title)
        if key in summary and summary[key] is None:
            lines.append("  not checked: the board could not be read")
            continue
        # A log derived before a list existed has no key for it.
        lines += [line(item) for item in summary.get(key) or []] or ["  none"]


def format_summary(summary: dict[str, Any]) -> str:
    """The summary as `autonomouse log show` prints it."""
    lines = [
        (
            f"run {summary['run_id']}  project {summary.get('project')}  session "
            f"{summary.get('session_id')}  derived {summary.get('derived_at')}"
        )
    ]
    if summary.get("reason"):
        lines.append(
            f"no transcript read: {summary['reason']}"
            + (f" ({summary['detail']})" if summary.get("detail") else "")
        )
    elif summary.get("note"):
        lines.append(f"note: {summary['note']}")
    lines.append("")
    lines.append("agents")
    for a in summary["agents"]:
        cost = f"${a['cost_usd']:.2f}" if a.get("cost_usd") is not None else "$-"
        lines.append(
            f"  {_name(a)}: {a.get('agent_type') or '-'} {a.get('model') or '-'} "
            f"{a.get('effort') or '-'}, {a['turns']} turns, {_duration(a['duration_s'])}, {cost}"
        )
        lines.append(f"    tools: {_counts(a['tools'])}")
        lines.append(
            f"    longest idle {_duration(a['longest_idle_s'])} from "
            f"{a.get('longest_idle_from') or '-'}; sub-agents spawned: {a['subagents_spawned']}"
        )
    if not summary["agents"]:
        lines.append("  none")
    sections = [
        (
            "board writes",
            "board_writes",
            lambda w: (
                f"  {w['means']}: {w['count']}"
                + (f" ({', '.join(w['cards'])})" if w.get("cards") else "")
            ),
        ),
        (
            "stack transitions",
            "transitions",
            lambda t: (
                f"  {t['at']} {t['card'] or '-'}: {t['from'] or '?'} -> {t['to'] or '?'} "
                f"by {t['means']}"
            ),
        ),
        (
            "phase changes",
            "phases",
            lambda p: f"  {p['at']} {p['card']}: {p['phase'] or 'cleared'}",
        ),
        (
            "iterations",
            "iterations",
            lambda i: (
                f"  {i['at']} {i['card']}: iteration {i['iteration']}, cause {i['cause']}"
            ),
        ),
        (
            "decisions",
            "decisions",
            lambda d: (
                f"  {d['at']} {d['card']} {d.get('decision') or '-'}: {d['kind']}, "
                f"answered by {d['answered_by'] or '-'}"
            ),
        ),
        (
            "sub-agents spawned by workers",
            "helpers_spawned_by_workers",
            lambda h: (
                f"  worker {h['card']} ({h['worker']}) spawned {h['agent_type'] or '-'} ({h['agent']})"
            ),
        ),
        (
            "PRs at finish",
            "prs",
            lambda p: (
                f"  {p['card']}: {p['state']}" + (f" {p['url']}" if p["url"] else "")
            ),
        ),
        (
            "usage-limit stalls",
            "stalls",
            lambda s: f"  {s['start']} - {s['reset']}: {', '.join(s['agents']) or '-'}",
        ),
        (
            "Jev answers",
            "jev",
            lambda j: (
                f"  {j['card'] or '(wake)'} {j['question']}: {j['source']} answered "
                f"{j['answer'] or '-'}, applied {j['applied'] or '-'}"
            ),
        ),
        ("times that cannot be true", "clock", lambda c: f"  {clock_text(c)}"),
    ]
    _sections(lines, summary, sections)
    cost = summary.get("cost") or {}
    lines.append("")
    by_model = ", ".join(
        f"{m} " + ("unknown" if c is None else f"${c:.2f}")
        for m, c in (cost.get("by_model") or {}).items()
    )
    total = cost.get("total_usd")
    shown = f"${total:.2f}" if total is not None else "unknown"
    lines.append(f"cost: {shown}" + (f" ({by_model})" if by_model else ""))
    if cost.get("unpriced_models"):
        lines.append(f"no rates for: {', '.join(cost['unpriced_models'])}")
    return "\n".join(lines)
