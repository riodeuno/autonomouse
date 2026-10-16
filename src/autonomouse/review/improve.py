"""The improvement step: patterns that recur in the behaviour review become proposed
instruction changes, as backlog cards the human promotes or cancels.

`autonomouse review improve` reads the latest behaviour review
(`reviews/behaviour-<date>.md`, written by `autonomouse review behaviour`; `--review`
names another) and sorts the rows of its tables into the patterns below. A pattern whose
rows name at least `improve.min_runs` runs (config, default 2) gets one card in the
`backlog` of the board project `improve.project` names (config, default `system`: the
project whose repository is autonomouse's own, where these docs live), proposing the
change: which doc, which section, what the sentence should say, and the review's own rows
as evidence. A pattern that already has an
open card (any stack but `done` and `cancelled`) with the same title gets none. It never
edits a doc; a worker does, once the human moves the card to `todo`.

The patterns:

- each rule-breach class of the review: hand edits of cards, and times on the board that
  cannot be true;
- the wait pattern: a worker or a worker's helper idle for 30 minutes or more at once,
  past the 30 minutes the worker's charter gives a check to finish (the orchestrator waits
  on its workers by design, so its idle time is no pattern);
- PRs in the state `conflicting` when their run's log was derived: sibling PRs of one
  wake that change the same file.

The review is the only input: the proposal quotes its rows as they are. The cards are
written, never committed: the board's next commit carries them.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autonomouse.board.commands import card_list, card_new, project_list
from autonomouse.board.schema import Stack
from autonomouse.config import load_config

# The letter of the series for the orchestrator's and workers' instructions
# (sys-d7 wrote them; the d cards since change how a wake behaves).
SERIES = "d"
RUN_ID = "improve"
TITLE = "improve: "
EVIDENCE_ROWS = 10
IDLE_MINUTES = 30

HAND_EDITS = "### Hand edits of cards"
CLOCK = "### Times on the board that cannot be true"
WAITS = "## Waits"
PRS = "## PR state at finish"

WORKER = "docs/worker.md"
ORCHESTRATOR = "docs/orchestrator.md"
RULES = "docs/rules.md"
BOARD_WRITES_RULE = "1. **Write to the board only through `autonomouse` commands**"


@dataclass(frozen=True)
class Row:
    cells: dict[str, str]
    line: str


@dataclass(frozen=True)
class Pattern:
    name: str
    table: str
    matches: Callable[[Row], bool]
    doc: str
    section: str
    sentence: str


@dataclass(frozen=True)
class Outcome:
    pattern: Pattern
    runs: list[str]
    card: str | None
    action: str  # "created", "open already", "below threshold"


def _role(row: Row) -> str:
    return row.cells.get("agent", "").split(" ")[0]


def _by_workers(row: Row) -> bool:
    return _role(row) in ("worker", "helper")


def _idle_minutes(text: str) -> float:
    """`52m10s` or `1h02m` (the review's durations) in minutes; 0 for `-`."""
    match = re.fullmatch(r"(?:(\d+)h)?(\d+)m(?:(\d+)s)?", text)
    if match is None:
        return 0.0
    hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return hours * 60 + minutes + seconds / 60


PATTERNS = [
    Pattern(
        "hand edits of cards",
        HAND_EDITS,
        lambda row: True,
        RULES,
        BOARD_WRITES_RULE,
        "A write whose `autonomouse` command exists is made only through that command, "
        "never by editing a board file by hand, with `jq`, an editor or a script.",
    ),
    Pattern(
        "times on the board that cannot be true",
        CLOCK,
        lambda row: True,
        RULES,
        BOARD_WRITES_RULE,
        "No agent writes a time into the board by hand: every time on it, a history row's "
        "or a decision's, comes from an `autonomouse` command, which reads the clock.",
    ),
    Pattern(
        f"workers or their helpers idle {IDLE_MINUTES} minutes or more",
        WAITS,
        lambda row: (
            _by_workers(row)
            and _idle_minutes(row.cells.get("longest idle", "")) >= IDLE_MINUTES
        ),
        WORKER,
        "## The loop",
        f"No wait of yours lasts past {IDLE_MINUTES} minutes, and you wait only for your "
        "own PR's checks, never for the base branch or another card: a check still running "
        f"after {IDLE_MINUTES} minutes ends your work with a **Stopped** report.",
    ),
    Pattern(
        "PRs conflicting at finish",
        PRS,
        lambda row: row.cells.get("state") == "conflicting",
        ORCHESTRATOR,
        "## A wake",
        "Before you spawn the batch's workers, compare their cards' goals: when two name "
        "the same file, spawn the second only once the first has opened its PR, and start "
        "it with `--base-branch` set to the first card's branch.",
    ),
]


# ------------------------------------------------------------------ the review --


def latest_review(root: Path) -> Path:
    """The newest `reviews/behaviour-<date>.md` under the board at `root`."""
    reviews = sorted((root / "reviews").glob("behaviour-*.md"))
    if not reviews:
        raise ValueError(
            f"no behaviour review in {root / 'reviews'}: "
            "run `autonomouse review behaviour --since <days>` first"
        )
    return reviews[-1]


def project_missing(root: Path, project: str) -> bool:
    """Whether `project` — the value of `improve.project` (config) — names no project
    on the board at `root`. A fresh install's board has no `system` project until one is
    made (`autonomouse project new`), and `improve.project` defaults to `system`."""
    return project not in {p.slug for p in project_list(root)}


def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip())[1:-1]
    return [p.strip().replace("\\|", "|") for p in parts]


def read_tables(text: str) -> dict[str, list[Row]]:
    """The first table under each `##` and `###` heading, by the heading line; a
    heading whose section holds no table (the review writes `None.`) has no rows."""
    tables: dict[str, list[Row]] = {}
    heading = None
    header: list[str] | None = None
    for line in text.splitlines():
        if line.startswith(("## ", "### ")):
            heading, header = line.strip(), None
            tables[heading] = []
            continue
        if heading is None:
            continue
        if not line.startswith("|"):
            if header is not None:
                heading = None  # the first table has ended
            continue
        cells = _cells(line)
        if header is None:
            header = cells
        elif set(line) <= set("|-"):
            continue
        else:
            tables[heading].append(Row(dict(zip(header, cells, strict=False)), line))
    return tables


def find_patterns(
    tables: dict[str, list[Row]], review: Path
) -> list[tuple[Pattern, list[Row], list[str]]]:
    """Each pattern with rows in the review: its rows and the runs they name, in order."""
    found = []
    for pattern in PATTERNS:
        if pattern.table not in tables:
            raise ValueError(
                f"the behaviour review {review} has no section {pattern.table!r}: "
                "write it again with `autonomouse review behaviour`"
            )
        rows = [row for row in tables[pattern.table] if pattern.matches(row)]
        if rows:
            runs = list(dict.fromkeys(row.cells.get("run", "?") for row in rows))
            found.append((pattern, rows, runs))
    return found


# ---------------------------------------------------------------- the cards --


def _title(pattern: Pattern) -> str:
    return f"{TITLE}{pattern.name}"


def _goal(
    pattern: Pattern,
    rows: list[Row],
    runs: list[str],
    review: Path,
    min_runs: int,
    header: str,
) -> str:
    shown = rows[:EVIDENCE_ROWS]
    more = len(rows) - len(shown)
    return "\n".join(
        [
            (
                f"Proposed instruction change, written by `autonomouse review improve`; "
                f"no doc changes until the human moves this card to todo. The pattern "
                f"'{pattern.name}' shows in {len(runs)} runs of the behaviour review "
                f"{review} (the threshold is {min_runs}): {', '.join(runs)}."
            ),
            "",
            (
                f'Change: in {pattern.doc}, section "{pattern.section}", add this '
                f"sentence, or reword the one that says the same, so it reads: "
                f'"{pattern.sentence}"'
            ),
            "",
            (
                f'Evidence, the review\'s rows under "{pattern.table}"'
                + (
                    f" ({len(shown)} of {len(rows)}; the rest are in the review)"
                    if more
                    else ""
                )
                + ":"
            ),
            "",
            header,
            *(row.line for row in shown),
        ]
    )


def _test(pattern: Pattern) -> str:
    return (
        f'{pattern.doc}, section "{pattern.section}", holds the proposed sentence or a '
        "rewording the human agreed to when promoting the card. uv run pytest -q and "
        "uv run ruff check src tests pass."
    )


def _header(text: str, pattern: Pattern) -> str:
    """The table's header and separator lines, so the quoted rows read as a table."""
    lines = text.splitlines()
    start = lines.index(pattern.table)
    table = [line for line in lines[start + 1 :] if line.startswith("|")][:2]
    return "\n".join(table)


def _next_id(root: Path, project: str) -> str:
    """`<prefix>-d<n>`: the prefix `project`'s cards use (its slug's first three letters
    when it has none), and the next free number of the d series across every project."""
    cards = card_list(root)
    own = [c.id.split("-")[0] for c in cards if c.project == project]
    prefix = max(set(own), key=own.count) if own else project[:3]
    series = re.compile(rf"{re.escape(prefix)}-{SERIES}(\d+)")
    taken = [int(m.group(1)) for c in cards if (m := series.fullmatch(c.id))]
    return f"{prefix}-{SERIES}{max(taken, default=0) + 1}"


def improve(root: Path, review: Path, min_runs: int) -> list[Outcome]:
    """Create one backlog card per pattern of `review` that recurs in `min_runs` runs or
    more and has no open card yet, in the project `improve.project` names (config).
    Returns what happened to each pattern found."""
    if isinstance(min_runs, bool) or not isinstance(min_runs, int) or min_runs < 1:
        raise ValueError(f"improve.min_runs must be 1 or more — got {min_runs}")
    project = load_config()["improve"]["project"]
    text = review.read_text()
    closed = (Stack.DONE, Stack.CANCELLED)
    outcomes = []
    for pattern, rows, runs in find_patterns(read_tables(text), review):
        if len(runs) < min_runs:
            outcomes.append(Outcome(pattern, runs, None, "below threshold"))
            continue
        open_cards = [
            c
            for c in card_list(root, project=project)
            if c.title == _title(pattern) and c.stack not in closed
        ]
        if open_cards:
            outcomes.append(Outcome(pattern, runs, open_cards[0].id, "open already"))
            continue
        card = card_new(
            root,
            card_id=_next_id(root, project),
            project=project,
            title=_title(pattern),
            goal=_goal(pattern, rows, runs, review, min_runs, _header(text, pattern)),
            test=_test(pattern),
            size="xs",
            limits=["Changes only the named section of the named doc; no code"],
            run_id=RUN_ID,
        )
        outcomes.append(Outcome(pattern, runs, card.id, "created"))
    return outcomes


def format_outcomes(outcomes: list[Outcome], review: Path) -> str:
    lines = [
        f"{o.action}: {o.card or '-'} {_title(o.pattern)} — {len(o.runs)} "
        f"run{'' if len(o.runs) == 1 else 's'}: {', '.join(o.runs)}"
        for o in outcomes
    ]
    created = sum(1 for o in outcomes if o.action == "created")
    lines.append(
        f"{review}: {len(outcomes)} pattern{'' if len(outcomes) == 1 else 's'}, "
        f"{created} card{'' if created == 1 else 's'} created"
    )
    return "\n".join(lines)
