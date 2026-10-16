"""The daily review: one markdown report per UTC day, built from the board.

`autonomouse review daily` reads every card and every run record under the board root and
writes `reviews/<date>.md` for one day (yesterday, UTC, by default). The report has seven
sections, each one a place where a human had to decide or where work came back:

1. Decisions: every decision asked or answered that day, and how long it waited.
2. Answers outside the listed options: every decision answered that day whose answer is
   none of its options ("other: tell me" does not count as one). Each is a question whose
   options missed what the human wanted, so the agent that asked framed it badly.
3. Asks the system should have settled: decisions whose question named a hands-on step —
   a command to run, a screen to watch, a message or reply to send, a step to click
   through an app — instead of a strategic question (a credential, a spend, a fork in the
   goal, a judgment of taste, or a goal sentence the human has not confirmed — the kinds
   docs/rules.md item 8, at PR 192, names outright; it also waves at "every other kind
   principle 6 of your global `CLAUDE.md` names", which this text match does not
   separately detect). Nothing on the board records this as its own event yet, so this is a
   text match over each decision's question, plus any history or handoff note that already
   says "trivial ask" itself; every hit is quoted so the reader can judge it.
4. Sent back or cancelled: every history row that day moving a card back to `todo` or
   into `cancelled`.
5. Iterations: every card that moved or got a handoff note that day, with its iteration count and stack now.
6. Merge clashes: history notes and handoff notes from that day that mention a conflict
   or a clash. Nothing on the board records a clash as its own event yet, so this is a
   text match; every hit is quoted so the reader can judge it.
7. Jev answers: every answer in a run that started that day, next to what the card (or
   the run) did afterwards.

The report is written, never committed: the board's next commit carries it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from autonomouse.board.schema import OTHER_OPTION, Card, HistoryEntry
from autonomouse.board.store import load_card

CLASH = re.compile(r"\b(conflict|clash)", re.IGNORECASE)
BACK_TO_TODO_FROM = {"in_progress", "decision_needed", "done"}


def _clause_start(words: list[str]) -> str:
    """Regex fragment: any of `words` right at the start of the text, or right after a
    sentence/clause boundary (`.`, `,` or `;` plus whitespace)."""
    alt = "|".join(re.escape(w) for w in words)
    return rf"(?:^|(?<=[.,;])\s+)(?:{alt})\b"


# Each pair names a kind of hands-on step the system asked the human to perform by hand,
# matched over a decision's question text, case-insensitive.
HANDS_ON_TRIGGERS: list[tuple[str, re.Pattern[str]]] = [
    (
        "only you can do it",
        re.compile(
            r"only you can|needs you to|ends with you|waits for you to", re.IGNORECASE
        ),
    ),
    (
        "a command to run",
        re.compile(
            _clause_start(["run"])
            + r"|\b(?:First|Then|After that),?\s+run\b|for you to run",
            re.IGNORECASE,
        ),
    ),
    (
        "something to watch or look at",
        re.compile(
            _clause_start(["watch", "look at", "check out", "check that"])
            + r"|watch it",
            re.IGNORECASE,
        ),
    ),
    (
        "a message to send or a reply to make",
        re.compile(_clause_start(["reply", "send"]), re.IGNORECASE),
    ),
    (
        "a step to perform in an app",
        re.compile(
            _clause_start(["merge", "click", "open", "visit", "paste", "type"]),
            re.IGNORECASE,
        ),
    ),
]

# The categories the gate allows (docs/rules.md item 8, at PR 192): a question that names
# one of these is a fair ask even when it also names a hands-on step.
CATEGORY_TRIGGERS: list[tuple[str, re.Pattern[str]]] = [
    ("credential", re.compile(r"\bcredential", re.IGNORECASE)),
    ("spend", re.compile(r"\bspend", re.IGNORECASE)),
    ("fork in the goal", re.compile(r"fork in the goal", re.IGNORECASE)),
    ("taste", re.compile(r"\btaste\b|judgment of taste", re.IGNORECASE)),
    ("goal sentence", re.compile(r"goal sentence", re.IGNORECASE)),
]

# Built from CATEGORY_TRIGGERS so the report can never name a category the code does not
# actually match.
_CATEGORY_NAMES = ", ".join(name for name, _ in CATEGORY_TRIGGERS)

# An agent recording the finding itself, in a history note or a handoff note.
TRIVIAL_ASK_NOTE = re.compile(r"trivial ask", re.IGNORECASE)


def _hands_on_triggers(text: str) -> list[str]:
    return [name for name, pattern in HANDS_ON_TRIGGERS if pattern.search(text)]


def _category(text: str) -> str:
    for name, pattern in CATEGORY_TRIGGERS:
        if pattern.search(text):
            return name
    return "none"


def parse_time(value: str) -> datetime:
    """Parse the board's `YYYY-MM-DDTHH:MM:SSZ` format into an aware UTC datetime."""
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _on(value: str | None, day: date) -> bool:
    return value is not None and parse_time(value).date() == day


def _duration(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _one_line(text: str | None) -> str:
    """Collapse whitespace so a note fits one table cell; escape the cell separator."""
    if not text:
        return ""
    return " ".join(text.split()).replace("|", "\\|")


def _move(entry: HistoryEntry) -> str:
    return f"{entry.from_ or 'new'} → {entry.to or 'same'}"


def _hhmm(value: str) -> str:
    return value[11:16]


@dataclass
class Board:
    cards: list[Card]
    runs: list[dict[str, Any]]


def load_board(root: Path) -> Board:
    """Load every card (validated) and every run record under `root`."""
    cards = [load_card(p) for p in sorted(root.glob("projects/*/cards/*.json"))]
    runs = []
    for path in sorted(root.glob("runs/*.json")):
        try:
            runs.append(json.loads(path.read_text()))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON — {e}") from e
    return Board(cards=cards, runs=runs)


def _decisions(board: Board, day: date, now: datetime) -> list[str]:
    rows = []
    for card in board.cards:
        for d in card.decisions:
            if not (_on(d.asked_at, day) or _on(d.answered_at, day)):
                continue
            asked = parse_time(d.asked_at)
            if d.answered_at:
                answered = parse_time(d.answered_at)
                if answered < asked:
                    waited = f"unknown: answered_at {d.answered_at} is before asked_at"
                else:
                    waited = _duration(answered - asked)
                answer = _one_line(d.answer)
            else:
                waited = f"{_duration(now - asked)} so far"
                answer = "open"
            rows.append(
                f"| {card.id} | {_one_line(d.question)} | {d.asked_at} | {answer} | {waited} |"
            )
    if not rows:
        return ["None."]
    return [
        "| card | question | asked | answer | waited |",
        "|---|---|---|---|---|",
        *rows,
    ]


def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def _off_list_answers(board: Board, day: date) -> list[str]:
    rows = []
    for card in board.cards:
        for d in card.decisions:
            if d.answer is None or not _on(d.answered_at, day):
                continue
            listed = [o for o in d.options if o != OTHER_OPTION]
            if any(_same_text(d.answer, o) for o in listed):
                continue
            rows.append(
                f"| {card.id} | {_one_line(d.question)} | {_one_line('; '.join(listed))} "
                f"| {_one_line(d.answer)} |"
            )
    lines = [
        (
            f"{len(rows)} found. Each is a decision answered that day whose answer is none of "
            "its listed options: the options missed what the human wanted, so the question was "
            "framed badly."
        )
    ]
    if rows:
        lines += [
            "",
            "| card | question | options | answer |",
            "|---|---|---|---|",
            *rows,
        ]
    return lines


def _trivial_asks(board: Board, day: date) -> list[str]:
    decisions: list[tuple[str, str]] = []  # (category, row)
    for card in board.cards:
        for d in card.decisions:
            if not (_on(d.asked_at, day) or _on(d.answered_at, day)):
                continue
            triggers = _hands_on_triggers(d.question)
            if not triggers:
                continue
            category = _category(d.question)
            decisions.append(
                (
                    category,
                    (
                        f"| {card.id} | {d.asked_at} | {'; '.join(triggers)} | {category} "
                        f"| {_one_line(d.question)} |"
                    ),
                )
            )
    decisions.sort(key=lambda pair: 0 if pair[0] == "none" else 1)

    notes: list[tuple[str, str]] = []
    for card in board.cards:
        for h in card.history:
            if _on(h.at, day) and h.note and TRIVIAL_ASK_NOTE.search(h.note):
                category = _category(h.note)
                notes.append(
                    (
                        category,
                        (
                            f"| {card.id} | {h.at} | recorded as a trivial ask | {category} "
                            f"| {_one_line(h.note)} |"
                        ),
                    )
                )
        handoff = card.handoff
        if _on(handoff.updated_at, day):
            for text in (handoff.last_step, handoff.next_step):
                if text and TRIVIAL_ASK_NOTE.search(text):
                    category = _category(text)
                    notes.append(
                        (
                            category,
                            (
                                f"| {card.id} | {handoff.updated_at} | recorded as a "
                                f"trivial ask | {category} | {_one_line(text)} |"
                            ),
                        )
                    )

    all_rows = decisions + notes
    none_count = sum(1 for category, _ in all_rows if category == "none")
    lines = [
        (
            f"{len(all_rows)} found. Matched on hands-on-step language (only you can do it, a "
            "command to run, something to watch or look at, a message to send or a reply to "
            "make, a step to perform in an app) in that day's decision questions, and on the "
            "words 'trivial ask' in that day's history notes and handoff notes; "
            f"{none_count} name none of the categories the gate allows ({_CATEGORY_NAMES}); "
            "read each quote before counting it."
        )
    ]
    if all_rows:
        lines += [
            "",
            "| card | asked | trigger | category named | quote |",
            "|---|---|---|---|---|",
            *(row for _, row in decisions),
            *(row for _, row in notes),
        ]
    return lines


def _sent_back(board: Board, day: date) -> list[str]:
    rows = []
    for card in board.cards:
        for h in card.history:
            if not _on(h.at, day):
                continue
            back = h.to == "todo" and h.from_ in BACK_TO_TODO_FROM
            if back or h.to == "cancelled":
                rows.append(
                    f"| {card.id} | {_hhmm(h.at)} | {_move(h)} | {h.run_id} | {_one_line(h.note)} |"
                )
    if not rows:
        return ["None."]
    return ["| card | time | move | run | note |", "|---|---|---|---|---|", *rows]


def _touched(card: Card, day: date) -> bool:
    """A card was worked that day: a stack move (not its creation row) or a handoff note."""
    moved = any(_on(h.at, day) and h.from_ is not None for h in card.history)
    return moved or _on(card.handoff.updated_at, day)


def _iterations(board: Board, day: date) -> list[str]:
    rows = []
    for card in sorted(board.cards, key=lambda c: (-c.iteration, c.id)):
        if not _touched(card, day):
            continue
        phase = card.phase.value if card.phase else "-"
        rows.append(f"| {card.id} | {card.iteration} | {card.stack.value} | {phase} |")
    if not rows:
        return ["No card was touched."]
    return [
        "Counts and stacks are as they stood when this report was written.",
        "",
        "| card | iteration | stack | phase |",
        "|---|---|---|---|",
        *rows,
    ]


def _clashes(board: Board, day: date) -> list[str]:
    rows = []
    for card in board.cards:
        day_notes = [h.note for h in card.history if _on(h.at, day) and h.note]
        for h in card.history:
            if _on(h.at, day) and h.note and CLASH.search(h.note):
                rows.append(f"| {card.id} | history {_hhmm(h.at)} | {_one_line(h.note)} |")
        handoff = card.handoff
        if _on(handoff.updated_at, day):
            for label, text in (("last step", handoff.last_step), ("next step", handoff.next_step)):
                if not text or not CLASH.search(text):
                    continue
                # A `card progress --next-step` row's note ends with this same text
                # (board/history.py's `progress_note`); when that row is already in
                # `day_notes` it is already quoted above, so this would count it twice.
                if label == "next step" and any(note.endswith(text) for note in day_notes):
                    continue
                rows.append(f"| {card.id} | handoff {label} | {_one_line(text)} |")
    lines = [
        (
            f"{len(rows)} found. Matched on the words conflict or clash in that day's history "
            "notes and in handoff notes last written that day; a handoff next step already "
            "quoted at the end of a history note that day is not counted again; read each "
            "quote before counting it."
        ),
    ]
    if rows:
        lines += ["", "| card | where | text |", "|---|---|---|", *rows]
    return lines


def _what_next(card: Card | None, since: str) -> str:
    if card is None:
        return "card not on the board"
    later = sorted((h for h in card.history if h.at >= since), key=lambda h: h.at)
    moves = [f"{_move(h)} {h.at[5:16].replace('T', ' ')}" for h in later]
    trail = "; ".join(moves) if moves else "no stack move"
    return f"{trail}. Now {card.stack.value}, iteration {card.iteration}" + (
        f", PR {card.pr}" if card.pr else ""
    )


def _jev(board: Board, day: date) -> list[str]:
    cards = {c.id: c for c in board.cards}
    rows = []
    for run in sorted(board.runs, key=lambda r: r.get("started_at") or ""):
        started = run.get("started_at")
        if not _on(started, day):
            continue
        for q in run.get("jev") or []:
            card_id = q.get("card")
            if card_id:
                nxt = _what_next(cards.get(card_id), started)
            else:
                nxt = f"run {run.get('status')}: {run.get('summary') or 'no summary'}"
            confidence = q.get("confidence")
            rows.append(
                f"| {run.get('id')} | {q.get('question')} | {card_id or '(wake)'} "
                f"| {q.get('source')} | {q.get('answer')} "
                f"| {'-' if confidence is None else confidence} | {q.get('applied')} "
                f"| {_one_line(nxt)} |"
            )
    if not rows:
        return ["None."]
    return [
        "| run | question | card | source | answer | confidence | applied | what happened next |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
    ]


def build_review(board: Board, day: date, now: datetime) -> str:
    """Render the review for `day` as markdown. `now` dates open decisions and the header."""
    sections = [
        ("Decisions asked", _decisions(board, day, now)),
        ("Answers outside the listed options", _off_list_answers(board, day)),
        ("Asks the system should have settled", _trivial_asks(board, day)),
        ("Cards sent back or cancelled", _sent_back(board, day)),
        ("Iteration counts", _iterations(board, day)),
        ("Merge clashes", _clashes(board, day)),
        ("Jev answers against what happened next", _jev(board, day)),
    ]
    out = [
        f"# Daily review — {day.isoformat()}",
        "",
        (
            f"Written {now.strftime('%Y-%m-%dT%H:%M:%SZ')} from {len(board.cards)} cards "
            f"and {len(board.runs)} run records. Times are UTC."
        ),
    ]
    for title, body in sections:
        out += ["", f"## {title}", "", *body]
    return "\n".join(out) + "\n"


def write_review(root: Path, day: date, now: datetime | None = None) -> Path:
    """Build the review for `day` from the board at `root` and write `reviews/<day>.md`."""
    now = now or datetime.now(UTC)
    text = build_review(load_board(root), day, now)
    path = root / "reviews" / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
