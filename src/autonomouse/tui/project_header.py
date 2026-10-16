"""The status line and the description a project's screens show in their header: its
board, its page and its planning threads show the same two lines (sys-w113).

The status line reads, left to right:

    ACTIVE · system | cards ✎ 2 writing · ⚗ 1 testing | waiting on you 1 | last change 3m ago

- the project's status pill (`project_status`, drawn by `look.project_status_pill`);
- the slug;
- `cards` and, for each word `overview.phase_word` gives the project's in_progress
  cards (writing, testing, ci, review, starting, stopped), that word's icon, how many
  cards have it and the word, leaving out the words no card has; or `no card in
  progress`;
- `waiting on you N`: the decision_needed cards plus the planning threads whose turn is
  the human's, in the decision colour when N is above zero;
- `last change <elapsed> ago`, from the newest of the project's `updated_at`, its cards'
  `updated_at` and its planning threads' last change (`tui.plan.last_changed`).

The description is the one sentence that says what the project is about: the goal
sentence of its brief when it has one, else the first sentence of its goal.

Each function reads the board itself, so a screen hands its header a function with no
argument, such as `lambda: status_line(root, slug, icons=icons)`. A board file that is
not valid or cannot be read raises as every other reader's does
(`autonomouse.tui.live.READ_ERRORS`), and the screen handles it. Nothing here writes
the board.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from textual.content import Content

from autonomouse.board import commands as board_commands
from autonomouse.board import plans as board_plans
from autonomouse.board.brief import load_brief
from autonomouse.board.plans import ThreadStatus
from autonomouse.board.schema import Card, Phase, Project, Stack
from autonomouse.config import pause_path
from autonomouse.overview import STARTING, STOPPED, phase_word, project_working

# The module, not its function: the planning threads screen (tui/plan.py) will import
# this module for its own header, and a module imported part way can still be named.
from autonomouse.tui import plan as plan_screen
from autonomouse.tui.format import elapsed
from autonomouse.tui.icons import PLAIN, Icons
from autonomouse.tui.look import STACK_LOOK, ProjectStatus, project_status_pill

# The order the line counts the phase words in: the phases in the order a card moves
# through them, then a card whose worker has written no phase yet, then the cards no
# wake works.
PHASE_WORDS: tuple[str, ...] = (*(phase.value for phase in Phase), STARTING, STOPPED)

NO_CARD = "no card in progress"
SEPARATOR = " | "
COUNT_SEPARATOR = " · "

# Words use the readable `$text-*` shade of a colour (`autonomouse.tui.look`).
_DECISION_TEXT = STACK_LOOK[Stack.DECISION_NEEDED].text
_WORKING_TEXT = STACK_LOOK[Stack.IN_PROGRESS].text
_MUTED = "$text-muted"


@dataclass(frozen=True)
class _Read:
    """One read of what the header shows about a project."""

    project: Project
    cards: list[Card]
    threads: list[board_plans.Thread]
    working: bool  # a wake works the project now (`overview.project_working`)
    paused: bool  # the PAUSE file exists


def _read(root: Path, slug: str) -> _Read:
    project = board_commands.project_show(root, slug)
    return _Read(
        project=project,
        cards=board_commands.card_list(root, project=slug),
        threads=[t for t in board_plans.thread_list(root) if t.project == slug],
        working=project_working(root, project),
        paused=pause_path().exists(),
    )


def _status(read: _Read) -> ProjectStatus:
    """The first that holds: ACTIVE while a wake works the project (alive or stalled);
    PAUSED while the PAUSE file exists; STOPPED while a wake that no longer works still
    holds the project's claim; DONE when no card is in todo or in_progress; else
    WAITING."""
    if read.working:
        return ProjectStatus.ACTIVE
    if read.paused:
        return ProjectStatus.PAUSED
    if read.project.claim.run_id is not None:
        return ProjectStatus.STOPPED
    if not any(c.stack in (Stack.TODO, Stack.IN_PROGRESS) for c in read.cards):
        return ProjectStatus.DONE
    return ProjectStatus.WAITING


def project_status(root: Path, slug: str) -> ProjectStatus:
    """Where project `slug`'s wakes stand (`_status`)."""
    return _status(_read(root, slug))


def _phase_icon(word: str, icons: Icons) -> str:
    if word == STOPPED:
        return icons.stopped
    if word == STARTING:
        return icons.stack[Stack.IN_PROGRESS]
    return icons.phase[Phase(word)]


def _cards_part(read: _Read, icons: Icons) -> Content:
    counts = Counter(
        phase_word(card, working=read.working)
        for card in read.cards
        if card.stack is Stack.IN_PROGRESS
    )
    shown = [word for word in PHASE_WORDS if counts[word] > 0]
    if not shown:
        return Content.assemble(("cards ", _MUTED), (NO_CARD, _MUTED))
    counted = [
        Content.styled(
            f"{_phase_icon(word, icons)} {counts[word]} {word}",
            f"bold {_MUTED if word == STOPPED else _WORKING_TEXT}",
        )
        for word in shown
    ]
    return Content.styled("cards ", _MUTED) + Content.styled(
        COUNT_SEPARATOR, _MUTED
    ).join(counted)


def _waiting_on_you(read: _Read) -> int:
    decisions = sum(1 for c in read.cards if c.stack is Stack.DECISION_NEEDED)
    threads = sum(1 for t in read.threads if t.status is ThreadStatus.WAITING_HUMAN)
    return decisions + threads


def _last_change(read: _Read) -> str:
    # Board times all share one format, so the newest is the largest string.
    return max(
        [
            read.project.updated_at,
            *(c.updated_at for c in read.cards),
            *(plan_screen.last_changed(t) for t in read.threads),
        ]
    )


def status_line(
    root: Path, slug: str, *, icons: Icons = PLAIN, now: datetime | None = None
) -> Content:
    """Project `slug`'s status line (see the module docstring). `now` is the clock the
    elapsed time is measured to; the current time when not given."""
    read = _read(root, slug)
    waiting = _waiting_on_you(read)
    waiting_style = f"bold {_DECISION_TEXT}" if waiting else _MUTED
    since = elapsed(_last_change(read), now=now or datetime.now(UTC))
    separator = Content.styled(SEPARATOR, _MUTED)
    return Content.assemble(
        project_status_pill(_status(read), icons=icons),
        (COUNT_SEPARATOR, _MUTED),
        (read.project.slug, "bold"),
        separator,
        _cards_part(read, icons),
        separator,
        (f"waiting on you {waiting}", waiting_style),
        separator,
        (f"last change {since}", _MUTED),
    )


# up to the first `.`, `!` or `?` that a space or the end follows, so `24.04` goes on
_FIRST_SENTENCE = re.compile(r".*?[.!?](?=\s|$)", re.DOTALL)


def description(root: Path, slug: str) -> str:
    """What project `slug` is about, in one sentence: the goal sentence of its brief
    when it has one, else the first sentence of its goal. Double quotes around the whole
    sentence are dropped: the brief quotes the goal sentence the human confirmed."""
    project = board_commands.project_show(root, slug)
    brief = load_brief(root, slug)
    if brief is not None and brief.goal:
        text = brief.goal
    else:
        found = _FIRST_SENTENCE.match(project.goal.strip())
        text = found.group() if found else project.goal
    text = " ".join(text.split())
    if len(text) > 1 and text[0] in '"“' and text[-1] in '"”':
        text = text[1:-1]
    return text
