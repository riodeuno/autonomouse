"""How the viewer looks on screen: each stack's icon, word, theme colour and empty-state
line, and the order every view lists the stacks in (`STACK_ORDER`); the pill every
state is shown in and the colour of every state a pill shows; and the frame every
panel and card is drawn in (`FRAME`). A screen takes its pills, colours and frame from
here and picks none of its own.

Colour never carries meaning alone. Wherever a stack's colour appears (a column
title, a card border, a badge in the detail panel), its icon and its word appear
with it, so the board reads the same in monochrome, in either theme, and to a reader
who cannot tell the colours apart. The icons themselves, in their Nerd Font and plain
forms, are `autonomouse.tui.icons`.

The colours are Textual theme variables, so the dark and the light theme each
supply their own shade; the frame's focus colour is the one variable the viewer adds
to them (`theme_variables`). Words use the `$text-*` shade of a colour, which each theme
tunes to stay readable on its background (plain `$warning` amber is faint on the light
theme's grey); borders use the plain colour. `$accent` is left out on purpose: the built-in themes give it
the same amber as `$warning`, which belongs to the decisions the human must make.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from textual.content import Content

from autonomouse.board.plans import ThreadStatus
from autonomouse.board.schema import Phase, Size, Stack
from autonomouse.tui.icons import PLAIN, Icons


@dataclass(frozen=True)
class StackLook:
    colour: str  # a Textual theme variable for borders and rules, e.g. "$warning"
    text: str  # its readable shade for words on the background, e.g. "$text-warning"
    empty: str  # what an empty column says instead of showing nothing


# The order every view of the viewer lists the stacks in: the board's columns left to
# right, and the project page's count of cards. Decisions come first, because they wait
# on the human; then the working stacks in the order a card moves through them; then
# backlog and cancelled.
STACK_ORDER: tuple[Stack, ...] = (
    Stack.DECISION_NEEDED,
    Stack.TODO,
    Stack.IN_PROGRESS,
    Stack.DONE,
    Stack.BACKLOG,
    Stack.CANCELLED,
)


def stack_label(stack: Stack | str) -> str:
    """The stack's name as a reader sees it: `in_progress` -> `in progress`."""
    value = stack.value if isinstance(stack, Stack) else stack
    return value.replace("_", " ")


STACK_LOOK: dict[Stack, StackLook] = {
    Stack.DECISION_NEEDED: StackLook(
        "$warning", "$text-warning", "Nothing is waiting on you."
    ),
    Stack.TODO: StackLook("$foreground", "$foreground", "Nothing is queued."),
    Stack.IN_PROGRESS: StackLook(
        "$primary", "$text-primary", "No card is being worked on."
    ),
    Stack.DONE: StackLook("$success", "$text-success", "Nothing has finished yet."),
    Stack.BACKLOG: StackLook(
        "$foreground-muted", "$foreground-muted", "The backlog is empty."
    ),
    Stack.CANCELLED: StackLook("$error", "$text-error", "Nothing was cancelled."),
}


def stack_badge(
    stack: Stack, *, count: int | None = None, icons: Icons = PLAIN
) -> Content:
    """`? decision needed`, or `? decision needed 2` with a count, in the stack's colour."""
    look = STACK_LOOK[stack]
    text = f"{icons.stack[stack]} {stack_label(stack)}"
    if count is not None:
        text += f" {count}"
    return Content.styled(text, f"bold {look.text}")


# ------------------------------------------------------------------------ pills --
#
# A pill is a state's icon and its word in capitals, one space either side, on a
# background filled with the state's colour: `pill(icon, word, colour)`. Its text is
# Textual's `auto` colour, which the theme works out for the filled background as it
# draws: black on a light fill, white on a dark one, so the pill reads in the dark and
# the light theme alike. With `viewer.nerd_font` on (`icons` is `NERD`) the pill has the
# Nerd Font half circles for ends, drawn in its colour; off (`PLAIN`) it is the plain
# filled block. The functions after it build the pill of each kind of state, so a
# screen names the state and nothing else.

NEUTRAL = "$foreground-muted"
"""The colour of a pill that marks no state to act on: a size, a project between
wakes, a closed planning thread."""

DECISION = STACK_LOOK[Stack.DECISION_NEEDED].colour
"""The colour of everything that waits on the human: a decision_needed card, an open
question, a planning thread whose turn is the human's."""


class ProjectStatus(str, enum.Enum):
    """What a project's wakes are doing, as the header's pill says it."""

    ACTIVE = "active"  # a wake is working the project
    PAUSED = "paused"  # the PAUSE file exists
    STOPPED = "stopped"  # a dead wake still holds the project
    DONE = "done"  # marked done (`Project.done_at`): no more wakes
    WAITING = "waiting"  # no wake on the project now, and it is not done


# The human named active green, paused yellow and done blue.
PROJECT_STATUS_COLOUR: dict[ProjectStatus, str] = {
    ProjectStatus.ACTIVE: "$success",
    ProjectStatus.PAUSED: "$warning",
    ProjectStatus.STOPPED: "$error",
    ProjectStatus.DONE: "$primary",
    ProjectStatus.WAITING: NEUTRAL,
}

# A question is open (waiting on the human) or answered.
QUESTION_COLOUR: dict[bool, str] = {False: DECISION, True: "$success"}

# Whose turn it is on a planning thread, in the pill's words. A thread just opened and
# one the human replied on both wait on the planner.
THREAD_WORD: dict[ThreadStatus, str] = {
    ThreadStatus.WAITING_HUMAN: "waiting on you",
    ThreadStatus.OPEN: "planner working",
    ThreadStatus.WAITING_PLANNER: "planner working",
    ThreadStatus.HUMAN_JOINED: "live",
    ThreadStatus.CLOSED: "closed",
}
THREAD_COLOUR: dict[ThreadStatus, str] = {
    ThreadStatus.WAITING_HUMAN: DECISION,
    ThreadStatus.OPEN: STACK_LOOK[Stack.IN_PROGRESS].colour,
    ThreadStatus.WAITING_PLANNER: STACK_LOOK[Stack.IN_PROGRESS].colour,
    ThreadStatus.HUMAN_JOINED: "$success",
    ThreadStatus.CLOSED: NEUTRAL,
}


def pill(icon: str, word: str, colour: str, *, icons: Icons = PLAIN) -> Content:
    """` <icon> <WORD> ` on a background filled with `colour` (a Textual theme
    variable), in the text colour that reads on it, between `icons`' pill ends."""
    left, right = icons.pill_ends
    return (
        Content.styled(left, colour)
        + Content.styled(f" {icon} {word.upper()} ", f"bold auto on {colour}")
        + Content.styled(right, colour)
    )


def stack_pill(stack: Stack, *, icons: Icons = PLAIN) -> Content:
    """`IN PROGRESS`, in its stack's colour."""
    return pill(
        icons.stack[stack], stack_label(stack), STACK_LOOK[stack].colour, icons=icons
    )


def project_status_pill(status: ProjectStatus, *, icons: Icons = PLAIN) -> Content:
    """`ACTIVE`, `PAUSED`, `STOPPED`, `DONE` or `WAITING`."""
    icon = {
        ProjectStatus.ACTIVE: icons.wake,
        ProjectStatus.PAUSED: icons.paused,
        ProjectStatus.STOPPED: icons.stopped,
        ProjectStatus.DONE: icons.stack[Stack.DONE],
        ProjectStatus.WAITING: icons.clock,
    }[status]
    return pill(icon, status.value, PROJECT_STATUS_COLOUR[status], icons=icons)


def question_pill(*, answered: bool, icons: Icons = PLAIN) -> Content:
    """`OPEN`, in the decision colour, or `ANSWERED`."""
    icon, word = (icons.passed, "answered") if answered else (icons.waiting, "open")
    return pill(icon, word, QUESTION_COLOUR[answered], icons=icons)


def thread_pill(status: ThreadStatus, *, icons: Icons = PLAIN) -> Content:
    """`WAITING ON YOU`, `PLANNER WORKING`, `LIVE` or `CLOSED`."""
    return pill(
        icons.thread_status[status],
        THREAD_WORD[status],
        THREAD_COLOUR[status],
        icons=icons,
    )


def size_badge(size: Size, *, icons: Icons = PLAIN) -> Content:
    """A card's size, `S`, in the neutral colour."""
    return pill(icons.size, size.value, NEUTRAL, icons=icons)


# ----------------------------------------------------------------------- frames --


@dataclass(frozen=True)
class Frame:
    """The frame every panel and card is drawn in: a Textual border type, the colour
    of a frame at rest, and the colour of the one that holds focus. A screen's CSS
    writes `border: {FRAME.rest}` and, on focus, `border: {FRAME.focused}`."""

    border: str
    colour: str
    focus: str

    @property
    def rest(self) -> str:
        return f"{self.border} {self.colour}"

    @property
    def focused(self) -> str:
        return f"{self.border} {self.focus}"


# The focus colour is a violet, the one hue no stack uses: every stack's colour is the
# amber, grey, blue, green or red of a built-in theme, and the theme has no colour of
# its own a hue apart from all of them (sys-w125). The chosen card's border must tell
# apart from a resting card's in every column, the in_progress blue's included, so the
# viewer adds the violet as its own theme variable, `$frame-focus`: bright on the dark
# theme and deep on the light one, each at least 3:1 against the theme's grounds. No
# one colour reaches 3:1 on both themes' panels, so each theme has its own shade.
FOCUS_VARIABLE = "frame-focus"
FOCUS_COLOUR: dict[bool, str] = {True: "#D787FF", False: "#8E24AA"}  # dark, light


def theme_variables(*, dark: bool) -> dict[str, str]:
    """The theme variables the viewer adds to Textual's own, for a dark or a light
    theme: `AutonomouseApp.get_theme_variable_defaults` returns them."""
    return {FOCUS_VARIABLE: FOCUS_COLOUR[dark]}


# The resting frame is muted grey in both themes; the focus colour is the violet above,
# never the amber of the decisions.
FRAME = Frame(border="heavy", colour="$foreground-muted", focus=f"${FOCUS_VARIABLE}")


@dataclass(frozen=True)
class Mark:
    """One status-strip mark: the word beside its icon (`autonomouse.tui.icons`), and
    the ASCII letter that stands in for the icon — the same "colour never carries
    meaning alone" rule as `StackLook`, scaled down to a single glyph."""

    ascii: str
    word: str
    text: str  # a `$text-*` theme variable, e.g. "$text-primary"


PHASE_MARK: dict[Phase, Mark] = {
    Phase.WRITING: Mark("W", "writing", "$text-primary"),
    Phase.TESTING: Mark("T", "testing", "$text-primary"),
    Phase.CI: Mark("C", "ci", "$text-primary"),
    Phase.REVIEW: Mark("R", "review", "$text-primary"),
}


def open_questions_mark(count: int) -> Mark:
    """The mark a decision_needed card's strip leads with: how many of its questions
    wait on the human, in the decision_needed stack's own colour (`STACK_LOOK`), beside
    the waiting icon (`autonomouse.tui.icons`). The card's column already names its
    stack, so the mark says what the human has to do instead."""
    word = f"{count} open question" + ("" if count == 1 else "s")
    return Mark("?", word, STACK_LOOK[Stack.DECISION_NEEDED].text)


SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SPINNER_FRAMES_ASCII: tuple[str, ...] = ("|", "/", "-", "\\")


def spinner_frame(frame: int, *, ascii_only: bool = False) -> str:
    """The animation glyph for tick `frame`, cycling through a small frame set;
    `ascii_only` swaps in a plain set for a terminal that reports no Unicode
    support, the same rule `render_mark` already applies to a mark's own icon."""
    frames = SPINNER_FRAMES_ASCII if ascii_only else SPINNER_FRAMES
    return frames[frame % len(frames)]


def render_mark(
    mark: Mark, icon: str, *, ascii_only: bool = False, frame: int | None = None
) -> Content:
    """A mark's `icon` (its ASCII letter when the terminal reports no Unicode support)
    and word, in its own colour. `frame`, given only while the mark's card is
    in_progress, appends that tick's animation glyph so the mark visibly moves; left
    `None` (a mark's first render, and every mark of a card that is not in_progress)
    it renders exactly as before."""
    symbol = mark.ascii if ascii_only else icon
    text = f"{symbol} {mark.word}"
    if frame is not None:
        text += f" {spinner_frame(frame, ascii_only=ascii_only)}"
    return Content.styled(text, f"bold {mark.text}")

