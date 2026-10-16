"""The icons the board viewer shows, in two forms: Nerd Font glyphs, and plain Unicode
symbols that a terminal font without Nerd Font glyphs still draws.

An icon marks a state (a card's stack or phase, passed or failed, waiting on the human,
a planning thread's turn, a project's wakes paused or stopped), a kind (a project, a
card, a pull request, a planning thread, a wake, a worker, a model, an attachment), a
fact (the time, time gone by, a size, an iteration), or a group of facts under its
heading (a goal, a card's progress, its history, a project's settings, a card's limits,
its test, a note's step, a decision's brief and its recommendation).
It always sits next to a word that says the same thing, as colour does
(`autonomouse.tui.look`), so an icon never carries meaning alone.

`viewer.nerd_font` in the config picks the form (`docs/config.md`). The viewer runs on
the machine autonomouse runs on, and the terminal the human views it from, often over
ssh, draws its glyphs, so nothing on this machine can tell whether that terminal has a
Nerd Font: the human says so in the config. On, the viewer shows `NERD`: Material Design
glyphs (`nf-md-*`) from Nerd Fonts 3, all in the Supplementary Private Use Area, which
only a Nerd Font draws. Off, the default, it shows `PLAIN` in the same places, and no
character of any view is in a Private Use Area. Every icon of both forms is one
terminal cell wide.

A form also holds the two ends of a pill (`autonomouse.tui.look.pill`): the Nerd Font
half circles that round it off, or nothing in the plain form, where a pill is a plain
filled block.

`AutonomouseApp` holds the chosen form as `icons`; a screen reads it from its app, and
each text-building function takes it as `icons`, `PLAIN` when not given.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autonomouse.board.plans import ThreadStatus
from autonomouse.board.schema import Phase, Stack


@dataclass(frozen=True)
class Icons:
    """One form of every icon the viewer shows."""

    stack: Mapping[Stack, str]  # a card's stack; decision_needed's is `waiting`
    phase: Mapping[Phase, str]  # an in_progress card's phase
    passed: str  # an answered question
    failed: str  # a read that failed
    waiting: str  # waiting on the human: a decision to make, a thread to answer
    live: str  # the human is in a planning thread live
    thread_status: Mapping[ThreadStatus, str]  # whose turn it is on a planning thread
    card: str
    pr: str  # a pull request
    thread: str  # a planning thread
    wake: str  # a wake: one scheduled run of the orchestrator on a project
    goal: str  # what a card or a project is for
    progress: str  # where a card's work stands: its worker, notes and pull request
    history: str  # a card's moves from stack to stack
    settings: str  # a project's slug, repository, branch and priority
    project: str
    worker: str  # the agent working a card
    clock: str  # the time of day
    elapsed: str  # time gone by, such as `3m ago`
    size: str  # a card's size estimate
    model: str  # the model a worker runs on
    iteration: str  # how many attempts at a card have failed
    paused: str  # a project's wakes are paused
    stopped: str  # a wake died and still holds its project
    limits: str  # what a card's work must not do
    test: str  # what proves a card done
    attachment: str  # a file attached to a card
    step: str  # a worker's last or next step
    brief: str  # a decision's brief
    recommendation: str  # the answer a decision's brief recommends
    pill_ends: tuple[str, str]  # a pill's left and right end; not icons (see above)


def _icons(
    *,
    stack: Mapping[Stack, str],
    phase: Mapping[Phase, str],
    passed: str,
    failed: str,
    live: str,
    card: str,
    pr: str,
    thread: str,
    wake: str,
    goal: str,
    progress: str,
    history: str,
    settings: str,
    project: str,
    worker: str,
    clock: str,
    elapsed: str,
    size: str,
    model: str,
    iteration: str,
    paused: str,
    stopped: str,
    limits: str,
    test: str,
    attachment: str,
    step: str,
    brief: str,
    recommendation: str,
    pill_ends: tuple[str, str],
) -> Icons:
    """An icon set whose shared icons come from one place: waiting on the human is the
    decision_needed stack's icon, a planner at work is in_progress's, a closed thread
    is done's."""
    waiting = stack[Stack.DECISION_NEEDED]
    working = stack[Stack.IN_PROGRESS]
    return Icons(
        stack=stack,
        phase=phase,
        passed=passed,
        failed=failed,
        waiting=waiting,
        live=live,
        thread_status={
            ThreadStatus.WAITING_HUMAN: waiting,
            ThreadStatus.OPEN: working,
            ThreadStatus.WAITING_PLANNER: working,
            ThreadStatus.HUMAN_JOINED: live,
            ThreadStatus.CLOSED: stack[Stack.DONE],
        },
        card=card,
        pr=pr,
        thread=thread,
        wake=wake,
        goal=goal,
        progress=progress,
        history=history,
        settings=settings,
        project=project,
        worker=worker,
        clock=clock,
        elapsed=elapsed,
        size=size,
        model=model,
        iteration=iteration,
        paused=paused,
        stopped=stopped,
        limits=limits,
        test=test,
        attachment=attachment,
        step=step,
        brief=brief,
        recommendation=recommendation,
        pill_ends=pill_ends,
    )


# Each glyph's Nerd Fonts name is beside it; https://www.nerdfonts.com/cheat-sheet finds it.
NERD = _icons(
    stack={
        Stack.DECISION_NEEDED: "\U000f0b59",  # nf-md-account_question
        Stack.TODO: "\U000f0130",  # nf-md-checkbox_blank_circle_outline
        Stack.IN_PROGRESS: "\U000f0996",  # nf-md-progress_clock
        Stack.DONE: "\U000f05e0",  # nf-md-check_circle
        Stack.BACKLOG: "\U000f0687",  # nf-md-inbox
        Stack.CANCELLED: "\U000f0159",  # nf-md-close_circle
    },
    phase={
        Phase.WRITING: "\U000f03eb",  # nf-md-pencil
        Phase.TESTING: "\U000f0093",  # nf-md-flask
        Phase.CI: "\U000f04e6",  # nf-md-sync
        Phase.REVIEW: "\U000f0208",  # nf-md-eye
    },
    passed="\U000f012c",  # nf-md-check
    failed="\U000f0026",  # nf-md-alert
    live="\U000f05cb",  # nf-md-account_voice
    card="\U000f0b78",  # nf-md-card_text_outline
    pr="\U000f04c2",  # nf-md-source_pull
    thread="\U000f0822",  # nf-md-forum_outline
    wake="\U000f0020",  # nf-md-alarm
    goal="\U000f08c9",  # nf-md-bullseye_arrow
    progress="\U000f15ab",  # nf-md-list_status
    history="\U000f02da",  # nf-md-history
    settings="\U000f08bb",  # nf-md-cog_outline
    project="\U000f0256",  # nf-md-folder_outline
    worker="\U000f167a",  # nf-md-robot_outline
    clock="\U000f0150",  # nf-md-clock_outline
    elapsed="\U000f051b",  # nf-md-timer_outline
    size="\U000f046d",  # nf-md-ruler
    model="\U000f09d1",  # nf-md-brain
    iteration="\U000f0456",  # nf-md-repeat
    paused="\U000f03e6",  # nf-md-pause_circle_outline
    stopped="\U000f0667",  # nf-md-stop_circle_outline
    limits="\U000f179a",  # nf-md-fence
    test="\U000f08a8",  # nf-md-clipboard_check_outline
    attachment="\U000f03e2",  # nf-md-paperclip
    step="\U000f01b7",  # nf-md-debug_step_over
    brief="\U000f09ee",  # nf-md-file_document_outline
    recommendation="\U000f0514",  # nf-md-thumb_up_outline
    # nf-ple-left_half_circle_thick, nf-ple-right_half_circle_thick
    pill_ends=("\ue0b6", "\ue0b4"),
)

PLAIN = _icons(
    stack={
        Stack.DECISION_NEEDED: "?",
        Stack.TODO: "○",
        Stack.IN_PROGRESS: "◐",
        Stack.DONE: "✓",
        Stack.BACKLOG: "·",
        Stack.CANCELLED: "✗",
    },
    phase={
        Phase.WRITING: "✎",
        Phase.TESTING: "⚗",
        Phase.CI: "⟳",
        # not ☰ (U+2630): Unicode 16 made it two cells wide, and most terminals draw one
        Phase.REVIEW: "≡",
    },
    passed="✓",
    failed="⚠",
    live="●",
    card="▤",
    pr="⇄",
    thread="✉",
    wake="☼",
    goal="◎",
    progress="➤",
    history="↺",
    settings="⚙",
    project="⌂",
    worker="⚒",
    clock="◷",
    elapsed="◔",
    size="⇔",
    model="◆",
    iteration="#",
    paused="‖",
    stopped="■",
    limits="⊘",
    test="☑",
    attachment="⊕",
    step="»",
    brief="¶",
    recommendation="★",
    pill_ends=("", ""),
)


def icon_set(*, nerd_font: bool) -> Icons:
    """`NERD` when the terminal has a Nerd Font (`viewer.nerd_font`), else `PLAIN`."""
    return NERD if nerd_font else PLAIN
