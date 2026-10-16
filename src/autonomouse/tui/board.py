"""One project's board: six stacks side by side, scrolled sideways, driven by arrow keys.

Stacks run left to right in `STACK_ORDER` (`autonomouse.tui.look`, which the project
page's count of cards follows too): decisions first, where the human's eye lands, then
the working stacks, then backlog and cancelled. A column is a quarter of the
terminal wide, never narrower than `MIN_COLUMN_WIDTH`, so on a normal terminal the first
four fill the screen and the rest sit off to the right; on a narrow one the board scrolls
instead of squeezing the cards.

Every column holds at least one focusable item (a card, or an "(empty)" marker), so the
arrow keys always move exactly one column left or right and focusing an item scrolls it
into view. The mouse works (a click focuses a card) and is never needed.

A long stack's column mounts only its first cards, and the next ones as the human
reaches them with the arrow keys or scrolls to its end (`Stacks`), so a board of 400
cards opens as fast as a small one and the splash over it keeps moving.

Each card is two widgets stacked inside one `BoardItem`: a title (`#card-title`) and,
under a thin rule, a status strip (`#card-strip`, `_strip_content`) — first the one
fact that says where the card stands (an in_progress card's phase, a decision_needed
card's open questions, a done card's pull request, the cards a todo card waits on),
then its size badge, its iteration count once above zero, and, while a worker holds
it, the worker's model-tier initial and how long it has held the card; a card it waits
on, and a done card's pull request, carry their kind's icon. An in_progress card whose project no wake
works now (the run holding the claim stopped or crashed,
`autonomouse.overview.wake_working`) shows `stopped` in place of its phase, and in
the card pane too, so it does not read as a worker still on it; its recorded phase
shows again once a live wake holds the claim (sys-w108). The strip never repeats
the card's stack, which its column's title and its border already name. The strip's
marks come from `autonomouse.tui.look`, their icons from `autonomouse.tui.icons` in
the form the app holds (Nerd Font or plain), and fall back to ASCII when the running
console reports no Unicode support
(`Console.options.ascii_only`), computed once per board and threaded through so the
strip can still be built and checked as a pure function of that flag and the icons.

While a card is in_progress and a wake works it, its strip's mark also animates: one
timer on this screen (`BoardScreen._advance_spinner`, two frames a second) ticks a
shared frame counter and redraws (`BoardItem.animate_strip`) only the strips of cards
currently in_progress, so the human can tell at a glance that a wake is working. A
`stopped` mark never moves.
Every other stack's strip never moves, and a card's own mark stops moving the
moment it leaves in_progress.

Below the stacks, a `CardDetail` panel (`autonomouse.tui.card_detail`) shows the
focused card in full, as a document in the shared reading view, its fields in four
groups under headings with icons: progress (phase, pull request, worker, last and next
step, attachments), goal (goal, limits, test), questions, history. A
`decision_needed` card shows its decision instead — question, brief, options,
recommendation, answer — and `d` shows or hides the rest under it. The panel follows
focus through the `DescendantFocus` event every `BoardItem` bubbles up.

`Tab` moves keyboard focus into that panel, so a terminal with no mouse can still
read a card too long for it; `Shift+Tab` and `Escape` both move focus back to
whichever card or empty marker it left (`_toggle_pane_focus`). The pane sits in the
viewer's frame (`autonomouse.tui.look.FRAME`), which takes the focus colour while the
pane holds focus, the same colour the chosen card's border takes.
Inside the pane, the arrow keys, `j`/`k`, `PageUp`/`PageDown` and `Home`/`End` scroll
it, and `a` answers whatever `decision_needed` card the pane is showing — the same
dialog and the same `check_action` gate as pressing `a` on the card itself, so the
human can answer a decision without leaving the pane they Tabbed into to read it.
The stacks' other keys (the arrows that move between cards, `x`, `d`) still apply
only while a card holds focus, not the pane.

Above the stacks, the header (`autonomouse.tui.logo.AppHeader`) is the sample's framed
box: the project's name as the screen's title,
the clock, then the status line and the description the project page shows too
(`autonomouse.tui.project_header`): the project's status pill, its slug, what its
workers do, what waits on the human and the last change; then the one sentence that
says what the project is about. Under those, inside the same frame, come the planning
thread lines (`#project-header`, below). The footer is the framed one
(`autonomouse.tui.footer.AppFooter`), with the project's slug and the version at the
right of the keys.

Every stack has an icon, a word and a colour (`autonomouse.tui.look`). The column
title and the card borders in that column use them together, so colour never carries
meaning alone. Each column sits in the viewer's frame (`look.FRAME`), and every card
has a heavy border in its stack's colour. The chosen card's heavy border takes the
frame's bright focus colour, on a raised background; its icon, its column and the
colour of its title (the border's title, the card's id) still name its stack. An empty
stack says what its emptiness means ("Nothing is waiting on you.") in a dashed box,
which is no card.

The screen also watches the board folder (`autonomouse.tui.live.LiveRefresh`): a card
moved, edited or added from another terminal reappears here without a keypress, with
the focused card kept in focus. It watches the PAUSE file too
(`autonomouse.config.pause_path`), so the status pill turns to paused, and back, on
the next poll, and the liveness of the run holding the project's claim, so the
in_progress cards turn to `stopped` when that wake stops or crashes, though no board
file changed. A change touches only the cards that changed (`Stacks.show`): a card
whose content changed is rewritten in place, one that moved within its stack is moved,
and only a card that left or joined a stack is removed or added; the focused card keeps
focus, in whichever stack it now sits. The header's title, its status and description
lines and its planning thread lines are rewritten in place; the header
(`autonomouse.tui.logo.AppHeader`), detail panel and footer stay mounted.
Rebuilding the whole screen once remounted Textual's `Header` on every change, and a second change arriving mid-rebuild removed that `Header` before the title
update it had queued on the screen ran, which crashed the app with `NoMatches`. A read
that fails (a card file left invalid by a hand edit) keeps the last board on screen and
shows the error in a status line under the header until a read succeeds again.
The screen's first read is that watch's first poll: a file already bad when the board
opens leaves the header with the slug for its title and no status line, and the stacks
hidden, with the error in the status line,
until a read succeeds (sys-s99).

On a `decision_needed` card, `a` opens its open decision in a modal (`autonomouse.
tui.decision.AnswerModal`), `x` opens a cancel-reason modal (`CancelModal`) and `d`
toggles the details; the three keys vanish from the footer on any other card, via
`check_action`. After the answer,
the modal asks where the card goes: more work needed (the top of `todo`, the default)
or the work is already finished (`done`). Confirming that, or typing a cancel reason,
calls `board_commands.card_answer` / `card_cancel` here —
the modals themselves never touch a file — and the grid redraws immediately rather
than waiting for the next live-refresh poll.

Anywhere on the board, `p` opens the project's planning threads (`autonomouse.tui.plan.
ThreadsScreen`): from there the human reads the planner's latest message and answers
it, joins a planner live, starts one, or leaves a request for the next wake. When that
list closes, the board reads the board folder at once (`on_screen_resume`), so a thread
started, joined or answered there shows in the header without waiting for the next
poll. The header gives one line, under its status line and description, to every
planning thread on the project that is not closed, in the list's order, so a thread waiting on the human comes first: the planning
thread's icon, its request, and its status in the list's plain words with that
status's icon, in the decisions' colour while it waits on the human. The first line
ends with the key that opens the list, said once rather than on every line.

Anywhere on the board, `i` opens the project's details page (`autonomouse.tui.project.
ProjectScreen`): card counts, open pull requests, the wakes, goal, settings and the
brief. Escape there comes back to the board.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import HorizontalScroll, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Static

from autonomouse.board import commands as board_commands
from autonomouse.board import plans as board_plans
from autonomouse.board.brief import Brief, load_brief
from autonomouse.board.paths import board_root
from autonomouse.board.runs import claim_liveness
from autonomouse.board.schema import Card, Project, Stack
from autonomouse.config import pause_path
from autonomouse.overview import STOPPED, wake_working
from autonomouse.tui.card_detail import CardDetail
from autonomouse.tui.decision import Answer, AnswerModal, CancelModal
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.format import elapsed
from autonomouse.tui.icons import PLAIN, Icons
from autonomouse.tui.live import LiveRefresh, ReadError
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import (
    FRAME,
    PHASE_MARK,
    STACK_LOOK,
    STACK_ORDER,
    open_questions_mark,
    render_mark,
    spinner_frame,
    stack_badge,
)
from autonomouse.tui.plan import (
    ThreadsScreen,
    open_first,
    project_threads,
    request_line,
    status_cell,
)
from autonomouse.tui.project import ProjectScreen
from autonomouse.tui.project_header import description, status_line

MIN_COLUMN_WIDTH = 32

# seconds; two frames a second. Each frame repaints the screen around the strips it
# changes, which four frames a second made most of an idle viewer's CPU use (sys-w90).
ANIMATION_INTERVAL = 0.5

# how many of a column's cards it mounts at first, and past the card focus moves to or
# the end it is scrolled to (`Stacks`). A card is at least five rows tall, so ten fill
# the columns of a terminal fifty rows tall; a taller column mounts more until they
# fill it (`StackColumn.on_resize`).
MOUNT_AHEAD = 10


class BoardItem(Vertical, can_focus=True):
    """One focusable entry in a column: a card, or the marker of an empty stack.

    A card is two child widgets, not one: `#card-title` (just the title) and,
    under a rule, `#card-strip` (`_strip_content`) — kept apart so the strip
    reads as its own row rather than a second line of the title, and so a test
    can find "the strip" without parsing the title's text. The empty-stack
    marker has no strip, only its one line of text.

    The arrow keys are bound here, on the focused widget, so they reach the board
    before the scroll containers around it turn them into plain scrolling.
    """

    # one legend entry for all four arrows, to keep the legend short: tests/test_tui_board.py
    # checks that every key shows whole, none under another, at 90 columns
    _MOVE = Binding.Group("Move", compact=True)
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding(
            "left", "screen.move(-1, 0)", "Stack left", key_display="←", group=_MOVE
        ),
        Binding(
            "right", "screen.move(1, 0)", "Stack right", key_display="→", group=_MOVE
        ),
        Binding("up", "screen.move(0, -1)", "Card up", key_display="↑", group=_MOVE),
        Binding("down", "screen.move(0, 1)", "Card down", key_display="↓", group=_MOVE),
        Binding("a", "screen.answer_decision", "Answer", key_display="a"),
        Binding("x", "screen.cancel_card", "Cancel", key_display="x"),
        Binding("d", "screen.toggle_details", "Details", key_display="d"),
    ]

    def __init__(
        self,
        title: str | Content,
        *,
        card: Card | None,
        ascii_only: bool,
        icons: Icons,
        column: int,
        row: int,
        now: datetime | None = None,
        working: bool = True,
    ) -> None:
        super().__init__(classes="card" if card is not None else "empty")
        self._title = title
        self.card = card
        # whether a wake works the card's project now; an in_progress card shows
        # `stopped` in place of its phase while none does
        self.working = working
        self.ascii_only = ascii_only
        self.icons = icons
        self.column = column
        self.row = row
        self._now = now
        if card is not None:
            self.border_title = f"{icons.stack[card.stack]} {card.id}"

    def compose(self) -> ComposeResult:
        yield Static(self._title, classes="card-title", id="card-title")
        if self.card is not None:
            strip = _strip_content(
                self.card,
                ascii_only=self.ascii_only,
                icons=self.icons,
                now=self._now,
                working=self.working,
            )
            yield Static(strip, classes="card-strip", id="card-strip")

    def show_card(self, card: Card, *, working: bool = True) -> None:
        """Show a new version of this item's card (same id, same stack), or the same
        card now that a wake started or stopped working it, in place: title, strip
        and border title are rewritten, the widget stays, and so does its focus
        (`Stacks.show`). A card mounted but not yet composed picks the new card up
        when it composes."""
        self.card = card
        self.working = working
        self._title = Content.styled(card.title, "bold")
        self.border_title = f"{self.icons.stack[card.stack]} {card.id}"
        strip = _strip_content(
            card,
            ascii_only=self.ascii_only,
            icons=self.icons,
            now=self._now,
            working=working,
        )
        try:
            self.query_one("#card-title", Static).update(self._title)
            self.query_one("#card-strip", Static).update(strip)
        except NoMatches:
            pass

    def animate_strip(self, frame: int) -> None:
        """Advance this card's strip to animation tick `frame`. Called only for an
        in_progress card, by the screen's one animation timer
        (`BoardScreen._advance_spinner`); every other stack's strip never moves,
        because the timer never calls this on it.

        Named `animate_strip`, not `animate`: every `Widget` already has a public
        `animate()` for CSS-property animation (Textual's own `_animator.py`), which
        a same-named override here would silently break.

        A tick can catch a card that a board change has just added (`Stacks.show`)
        before its `#card-strip` child is composed, or one it has just removed:
        `NoMatches` there means this tick simply missed that card, so it is skipped
        rather than crashing the app.

        A tick redraws only a strip that is on screen, and one that stays as wide as
        it was redraws alone. Either kind of redraw otherwise maps every widget of the
        screen (a strip scrolled out of sight, or `Static.update`'s default layout),
        which costs a tenth of a second on a board of 400 cards; four a second kept an
        idle viewer busy (sys-w90). A strip scrolled out of sight shows its next tick
        once it is back in view. A strip that grows or shrinks (the first spinner
        glyph, a longer elapsed time) may wrap to another line, so it still asks for a
        layout.

        A card no wake works (`working` false) shows `stopped`, which never moves.
        """
        if not self.working:
            return
        try:
            shown = self.query_one("#card-strip", Static)
        except NoMatches:
            return
        if not self.screen.can_view_partial(shown):
            return
        strip = _strip_content(
            self.card, ascii_only=self.ascii_only, icons=self.icons, now=self._now, frame=frame
        )
        before = shown.content
        same_width = isinstance(before, Content) and before.cell_length == strip.cell_length
        shown.update(strip, layout=not same_width)


def _strip_content(
    card: Card,
    *,
    ascii_only: bool,
    icons: Icons = PLAIN,
    now: datetime | None = None,
    frame: int | None = None,
    working: bool = True,
) -> Content:
    """The status strip: first the one fact that says where the card stands (an
    in_progress card's phase, how many questions a decision_needed card asks, a
    done card's pull request, or the cards a todo or backlog card waits on), then
    its size badge, its iteration count once above zero, and, while a worker holds
    it, the worker's model-tier initial and how long it has held the card — the
    facts the human needs to see who is on a card, in which phase, and for how
    long, without opening it. A card it waits on and a done card's pull request
    each carry their kind's icon.

    The strip never names the card's stack: the column's title and the card's
    border already do, so a third "done" or "decision needed" would only push the
    strip onto a second line.

    `frame`, given only for an in_progress card (`BoardItem.animate_strip`),
    appends that tick's spinner glyph to its mark so the strip visibly moves;
    left `None` (every first render, and every card that is not in_progress) the
    strip renders exactly as it always has.

    With `working` false (no wake works the card's project now), an in_progress
    card shows `stopped` in place of its phase, and no spinner.
    """
    now = now or datetime.now(UTC)
    parts: list[Content] = []

    if card.stack == Stack.IN_PROGRESS:
        if not working:
            parts.append(Content.styled(STOPPED, "bold $text-muted"))
        elif card.phase is not None:
            parts.append(
                render_mark(
                    PHASE_MARK[card.phase],
                    icons.phase[card.phase],
                    ascii_only=ascii_only,
                    frame=frame,
                )
            )
        else:
            starting = "starting"
            if frame is not None:
                starting += f" {spinner_frame(frame, ascii_only=ascii_only)}"
            parts.append(Content.styled(starting, "$text-muted"))
    elif card.stack == Stack.DECISION_NEEDED:
        open_ = sum(1 for d in card.decisions if d.answer is None)
        parts.append(
            render_mark(open_questions_mark(open_), icons.waiting, ascii_only=ascii_only)
        )
    elif card.stack == Stack.DONE and card.pr:
        number = card.pr.rsplit("/", 1)[-1]
        parts.append(Content.styled(f"{icons.pr} PR {number}", "$text-muted"))
    elif card.depends_on:  # todo / backlog waiting on another card
        after = ", ".join(f"{icons.card} {card_id}" for card_id in card.depends_on)
        parts.append(Content.styled(f"after {after}", "$text-muted"))

    parts.append(Content.styled(f"[{card.size.value}]", "bold $text-muted"))

    if card.iteration:
        parts.append(Content.styled(f"iter {card.iteration}", "$text-muted"))

    if card.stack == Stack.IN_PROGRESS and card.assigned.model:
        parts.append(Content.styled(card.assigned.model[0].upper(), "bold $text-primary"))
        parts.append(
            Content.styled(elapsed(card.assigned.picked_up_at, now=now), "$text-muted")
        )

    return Content("  ").join(parts)


def _thread_line(
    thread: board_plans.Thread, *, icons: Icons = PLAIN, hint: bool = False
) -> Content:
    """One planning thread that is not closed: the planning thread's icon, its
    request, and its status in the plain words of the `p` list with that status's
    icon, in the decision_needed stack's colour while it waits on the human, as a
    decision does. With `hint`, the key that opens that list follows."""
    waiting = thread.status is board_plans.ThreadStatus.WAITING_HUMAN
    status = STACK_LOOK[Stack.DECISION_NEEDED].text if waiting else "$text-muted"
    return Content.assemble(
        (f"{icons.thread} plan: ", "$text-muted"),
        (request_line(thread), "bold"),
        (" · ", "$text-muted"),
        (status_cell(thread.status, icons=icons), f"bold {status}" if waiting else status),
        *(((" · press ", "$text-muted"), ("p", "bold"), (" for planning", "$text-muted"))
          if hint else ()),
    )


def _thread_lines(
    project: Project,
    threads: list[board_plans.Thread] | tuple[board_plans.Thread, ...] = (),
    *,
    icons: Icons = PLAIN,
) -> Content:
    """The header's lines under its status line and description: one per planning
    thread on the project that is not closed, those waiting on the human first, as the
    `p` list orders them, with the `p` hint on the first only; empty when there is
    none."""
    open_, _closed = open_first(project_threads(list(threads), project.slug))
    return Content("\n").join(
        _thread_line(thread, icons=icons, hint=n == 0) for n, thread in enumerate(open_)
    )


class StackColumn(VerticalScroll):
    """One stack's column. Scrolled to its end, or with its cards all in view, it
    mounts its next cards (`Stacks.more`)."""

    def __init__(self, stack: Stack, column: int) -> None:
        super().__init__(classes="stack", id=f"stack-{stack.value}")
        self.column = column

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if new_value >= self.max_scroll_y:
            self._more()

    def on_resize(self, event: events.Resize) -> None:
        # a terminal taller than the cards mounted so far: nothing to scroll
        if self.max_scroll_y == 0:
            self._more()

    def _more(self) -> None:
        stacks = self.parent
        if isinstance(stacks, Stacks):
            stacks.more(self.column)


class Stacks(HorizontalScroll):
    """The six stack columns, kept in step with the board card by card.

    `grid` holds the focusable items column by column, so the arrow keys can address
    "column 2, row 1" directly.

    A column mounts only its first cards: `MOUNT_AHEAD` of them at first, and more
    whenever focus moves to within `MOUNT_AHEAD` of the last one mounted (`reach`), the
    column is scrolled to its end, or its mounted cards do not fill it (`more`). Every
    item of `grid` is made; only the mounted ones are styled, laid out and drawn.
    Textual spends about a millisecond a widget doing that, and a board of 400 cards
    mounted whole held the one thread for three seconds, during which the splash over
    the board (`autonomouse.tui.splash`) drew no frame (sys-w109). A column's title
    counts every card in its stack, mounted or not.

    A board change (`show`) touches only the cards that changed: a card still in the
    same stack keeps its `BoardItem`, rewritten in place when its content changed and
    moved when its position did; a card that left a stack is removed from it, and a
    card that arrived is added. Nothing else is detached, so a click during a wake
    rarely lands on a detached widget (Textual issue 6643),
    the screen does not flicker, and the
    focused card keeps focus.
    """

    def __init__(self, cards: list[Card]) -> None:
        super().__init__(id="stacks")
        self.cards = cards
        self.working = True  # a wake works the project now (`show`)
        self.grid: list[list[BoardItem]] = []
        self._shown = False  # set by the first `show`, which focuses the first item
        # per column, how many of its first items are mounted, at least
        self._reach = [MOUNT_AHEAD] * len(STACK_ORDER)

    def compose(self) -> ComposeResult:
        self.grid = []
        for column, stack in enumerate(STACK_ORDER):
            in_stack = [c for c in self.cards if c.stack == stack]
            items = [self._item(stack, card, column, row) for row, card in enumerate(in_stack)]
            items = items or [self._item(stack, None, column, 0)]
            self.grid.append(items)
            with StackColumn(stack, column):
                yield Static(
                    stack_badge(stack, count=len(in_stack), icons=self.app.icons),
                    classes="stack-title",
                )
                yield from items[: self._reach[column]]

    def more(self, column: int) -> None:
        """Mount `column`'s next `MOUNT_AHEAD` items, when it has any not mounted."""
        mounted = self._reach[column]
        if mounted < len(self.grid[column]):
            self.reach(column, mounted)

    def reach(self, column: int, row: int) -> None:
        """Mount `column`'s items through row `row` + `MOUNT_AHEAD`, after the ones it
        has mounted already."""
        wanted = row + MOUNT_AHEAD
        if wanted <= self._reach[column]:
            return
        self._reach[column] = wanted
        new = [item for item in self.grid[column][:wanted] if item.parent is None]
        if new:
            self.query_one(f"#stack-{STACK_ORDER[column].value}", StackColumn).mount(*new)

    def _item(self, stack: Stack, card: Card | None, column: int, row: int) -> BoardItem:
        """A new item: `card`, or the empty marker of `stack` when it is `None`."""
        return BoardItem(
            Content.styled(card.title, "bold") if card is not None else STACK_LOOK[stack].empty,
            card=card,
            ascii_only=self.app.console.options.ascii_only,
            icons=self.app.icons,
            column=column,
            row=row,
            working=self.working,
        )

    def show(self, cards: list[Card], *, working: bool = True) -> None:
        """Bring the columns in step with `cards`, touching only what changed.
        `working` says whether a wake works the project now: when it changes, every
        card is rewritten in place, so the in_progress ones show `stopped` or their
        phase.

        Focus stays on the focused card while that card is on the board, in whichever
        stack it now sits; when it is gone, focus goes to the same column and row,
        clamped to the new grid's edges the way the arrow keys clamp (`focus_at`).
        Focus in the details pane stays there. The first call focuses the first item.
        """
        self.cards = cards
        self.working = working
        focused = self.screen.focused
        was_on_item = isinstance(focused, BoardItem)
        position = (focused.column, focused.row) if was_on_item else (0, 0)
        focused_id = focused.card.id if was_on_item and focused.card else None

        old = {item.card.id: item for items in self.grid for item in items if item.card}
        grid: list[list[BoardItem]] = []
        for column, stack in enumerate(STACK_ORDER):
            before = self.grid[column]
            in_stack = [card for card in cards if card.stack == stack]
            items: list[BoardItem] = []
            for row, card in enumerate(in_stack):
                item = old.get(card.id)
                if item is None or item.column != column:
                    item = self._item(stack, card, column, row)
                elif item.card != card or item.working != working:
                    item.show_card(card, working=working)
                items.append(item)
            if not items:
                marker = before[0] if before[0].card is None else None
                items = [marker or self._item(stack, None, column, 0)]
            self._place(column, before, items, count=len(in_stack))
            grid.append(items)
        self.grid = grid

        by_id = {item.card.id: item for items in grid for item in items if item.card}
        if focused_id in by_id:
            if by_id[focused_id] is not focused:
                self.focus_at(by_id[focused_id].column, by_id[focused_id].row)
        elif was_on_item or not self._shown:
            self.focus_at(*position)
        self._shown = True

    def _place(
        self, column: int, before: list[BoardItem], items: list[BoardItem], *, count: int
    ) -> None:
        """Make `column` show `items`, in order, under its title (whose count becomes
        `count`): remove the items of `before` it no longer holds, add the new ones, move
        the rest into place. It mounts the items up to its reach (`reach`), which grows
        to cover every item it has mounted already, so the mounted ones stay the
        column's first."""
        stack = STACK_ORDER[column]
        container = self.query_one(f"#stack-{stack.value}", StackColumn)
        title = container.query_one(".stack-title", Static)
        title.update(stack_badge(stack, count=count, icons=self.app.icons))
        keep = set(map(id, items))
        for item in before:
            if id(item) not in keep and item.parent is not None:
                item.remove()
        for row, item in enumerate(items):
            item.row = row
            if item.parent is not None:
                self._reach[column] = max(self._reach[column], row + 1)
        previous: Static | BoardItem = title
        for item in items[: self._reach[column]]:
            if item.parent is None:
                container.mount(item, after=previous)
            else:
                container.move_child(item, after=previous)
            previous = item

    def focus_at(self, column: int, row: int) -> None:
        """Focus the item at `(column, row)`, clamped to the grid's actual edges,
        mounting the column's items up to `MOUNT_AHEAD` past it first."""
        column = min(max(column, 0), len(self.grid) - 1)
        items = self.grid[column]
        row = min(max(row, 0), len(items) - 1)
        self.reach(column, row)
        items[row].focus()


def _stack_css() -> str:
    """Per-stack colours: the column title and every card border in that column, in
    the frame's border type (`FRAME`); the chosen card's border in the frame's focus
    colour, its title still in its stack's colour."""
    rules = []
    for stack, look in STACK_LOOK.items():
        column = f"BoardScreen #stack-{stack.value}"
        rules.append(f"""
    {column} .stack-title {{ color: {look.text}; border-bottom: solid {look.colour}; }}
    {column} BoardItem.card {{
        border: {FRAME.border} {look.colour};
        border-title-color: {look.text};
    }}
    {column} BoardItem.card:focus {{ border: {FRAME.focused}; }}""")
    return "".join(rules)


class BoardScreen(LiveRefresh, Screen):
    """The board of one project."""

    DEFAULT_CSS = f"""
    BoardScreen #project-header {{
        height: auto;
        max-height: 6;
    }}
    BoardScreen #stacks {{
        height: 2fr;
        scrollbar-size-horizontal: 1;
    }}
    BoardScreen #detail {{
        height: 1fr;
        border: {FRAME.rest};
    }}
    BoardScreen #detail:focus {{
        border: {FRAME.focused};
    }}
    BoardScreen .stack {{
        width: 25vw;
        min-width: {MIN_COLUMN_WIDTH};
        height: 1fr;
        border: {FRAME.rest};
        padding: 0 1;
        scrollbar-size-vertical: 1;
    }}
    BoardScreen .stack-title {{
        width: 1fr;
    }}
    BoardScreen BoardItem {{
        width: 1fr;
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        border-title-align: left;
    }}
    BoardScreen BoardItem.empty {{
        color: $text-muted;
        text-style: italic;
        border: dashed $panel-lighten-2;
    }}
    BoardScreen BoardItem.empty:focus {{
        border: heavy $panel-lighten-2;
    }}
    BoardScreen BoardItem:focus {{
        background: $boost;
    }}
    BoardScreen .card-title {{
        height: auto;
    }}
    BoardScreen .card-strip {{
        height: auto;
        margin-top: 1;
        border-top: solid $panel-lighten-2;
        color: $text-muted;
    }}
    {_stack_css()}
    """

    # one legend entry for both, to keep the legend short (as `BoardItem._MOVE` does
    # for the four arrows): tests/test_tui_board.py checks it shows whole at 90 columns.
    # Two action names, not one, on purpose: the footer collapses several keys bound
    # to the very same action into a single entry (it groups distinct actions that
    # share a `Binding.Group`, not distinct keys), so `Tab` and `Shift+Tab` need their
    # own action names to both show, even though both do the identical toggle.
    _FOCUS = Binding.Group("Focus", compact=True)
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Projects", key_display="esc"),
        Binding("tab", "focus_pane_forward", "Focus", key_display="⇥", group=_FOCUS),
        Binding(
            "shift+tab", "focus_pane_backward", "Focus", key_display="⇤", group=_FOCUS
        ),
        Binding("p", "plan", "Planning", key_display="p"),
        Binding("i", "project_details", "Info", key_display="i"),
    ]

    def __init__(self, slug: str) -> None:
        super().__init__()
        self.slug = slug
        # the stacks position `_toggle_pane_focus` returns to when Shift+Tab or
        # Escape leaves the details pane; updated each time Tab leaves a card
        self._last_position: tuple[int, int] = (0, 0)

    @property
    def grid(self) -> list[list[BoardItem]]:
        return self.query_one(Stacks).grid

    def compose(self) -> ComposeResult:
        # no board yet: the first poll (`_start_live_refresh`) reads it and draws it
        # through `_redraw`, so a bad file when the board opens is shown, not raised;
        # the slug stands in for the project's name until then (`on_mount`)
        threads = Static(id="project-header")
        threads.display = False  # until a planning thread is open
        yield AppHeader(
            threads,
            status=lambda: status_line(board_root(), self.slug, icons=self.app.icons),
            description=lambda: description(board_root(), self.slug),
        )
        yield ReadError()
        stacks = Stacks([])
        # hidden until a read succeeds: its empty columns would say that no card waits
        stacks.display = False
        yield stacks
        yield CardDetail(id="detail")
        yield AppFooter(slug=self.slug)

    def on_mount(self) -> None:
        self.title = self.slug  # until the first read gives the project's name
        self._start_live_refresh()
        self._animation_frame = 0
        self.set_interval(ANIMATION_INTERVAL, self._advance_spinner)

    def _advance_spinner(self) -> None:
        """One tick of the in_progress spinner: advance the shared frame counter
        and redraw only the strips of cards currently in_progress. A card that has
        left in_progress is skipped here, and the board change that moved it
        (`Stacks.show`, on the next live-refresh poll) drew it fresh in its new
        stack without a frame, so its mark stops moving as soon as its stack
        changes."""
        self._animation_frame += 1
        # the in_progress column of the grid, not a query of the screen: a query walks
        # every widget of the board four times a second (sys-w90)
        for item in self.grid[STACK_ORDER.index(Stack.IN_PROGRESS)]:
            if item.card is not None and item.card.stack == Stack.IN_PROGRESS:
                item.animate_strip(self._animation_frame)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Keep the detail panel in step with whichever card holds focus."""
        if isinstance(event.widget, BoardItem):
            self._show_detail(event.widget.card)

    def _show_detail(self, card: Card | None) -> None:
        self.query_one(CardDetail).show(card)

    def _focused_card(self) -> Card | None:
        """The selected card: under focus on a `BoardItem`, or, once `Tab` has
        moved focus into the details pane, whichever card that pane is
        showing (`CardDetail.card`, kept in step with focus by `_show_detail`).
        `None` off a `BoardItem` and off the pane (on the empty marker, or
        before anything has focus)."""
        focused = self.focused
        if isinstance(focused, (BoardItem, CardDetail)):
            return focused.card
        return None

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Hide `a`/`x`/`d` from the footer unless the focused card can take them.

        Returning `False` drops a binding out of `active_bindings` entirely — what
        the footer reads to build its legend — rather than merely greying it out
        (`None` would do that, per `DOMNode.check_action`'s own docstring): a key
        that cannot fire on this card has no business sitting in the legend.
        """
        if action in ("answer_decision", "cancel_card", "toggle_details"):
            card = self._focused_card()
            if card is None or card.stack != Stack.DECISION_NEEDED:
                return False
            if action == "answer_decision":
                return any(d.answer is None for d in card.decisions)
            if action == "toggle_details":
                # without a decision the panel already shows the whole card
                return bool(card.decisions)
            return True  # cancel needs no open decision, just the right stack
        return super().check_action(action, parameters)

    def action_answer_decision(self) -> None:
        """Open the focused card's open decision in a modal; the callback below
        does the actual `board_commands.card_answer` once it dismisses.

        Defensive, not load-bearing: `check_action` already keeps this key off the
        footer unless there is an open decision, but a stale keypress could still
        reach here between a redraw and the next check, so this re-checks rather
        than assuming the gate always ran first.

        Uses `push_screen`'s `callback` form, not `wait_for_dismiss=True` — the
        latter only works from inside a worker (`App.push_screen`'s own docstring),
        which a plain key-bound action is not; a callback needs neither an `async`
        action method nor a worker.
        """
        card = self._focused_card()
        if card is None:
            return
        open_decision = next((d for d in card.decisions if d.answer is None), None)
        if open_decision is None:
            return
        card_id, project = card.id, card.project
        self.app.push_screen(
            AnswerModal(card_id, open_decision),
            callback=lambda answer: self._answered(card_id, project, answer),
        )

    def _answered(self, card_id: str, project: str, answer: Answer | None) -> None:
        """`AnswerModal`'s callback: write the answer — to `done` when the human
        said the work is already finished, else to the top of `todo` — unless it
        dismissed with `None` (escape — back out, write nothing)."""
        if answer is None:
            return
        board_commands.card_answer(
            board_root(),
            card_id,
            answer=answer.text,
            done=answer.done,
            project=project,
            run_id=board_commands.SCREEN_RUN_ID,
        )
        self._poll_board()  # redraw now, not on the next poll

    def action_toggle_details(self) -> None:
        """Show or hide the worker fields under a `decision_needed` card's decision."""
        self.query_one(CardDetail).toggle_details()

    def action_cancel_card(self) -> None:
        """Open a cancel-reason modal; the callback below does the actual
        `board_commands.card_cancel` once it dismisses (see `action_answer_decision`
        for why this uses `push_screen`'s `callback` form)."""
        card = self._focused_card()
        if card is None:
            return
        card_id, project = card.id, card.project
        self.app.push_screen(
            CancelModal(card_id),
            callback=lambda reason: self._cancelled(card_id, project, reason),
        )

    def _cancelled(self, card_id: str, project: str, reason: str | None) -> None:
        """`CancelModal`'s callback: write the cancellation, unless it dismissed
        with `None` (escape — back out, write nothing)."""
        if reason is None:
            return
        board_commands.card_cancel(
            board_root(),
            card_id,
            reason=reason,
            project=project,
            run_id=board_commands.SCREEN_RUN_ID,
        )
        self._poll_board()  # redraw now, not on the next poll

    def action_plan(self) -> None:
        """Open the project's planning threads."""
        self.app.push_screen(ThreadsScreen(self.slug))

    def action_project_details(self) -> None:
        """Open the project's details page."""
        self.app.push_screen(ProjectScreen(self.slug))

    def action_focus_pane_forward(self) -> None:
        """`Tab`: move focus between the stacks and the details pane — see
        `_toggle_pane_focus`, which does the actual work."""
        self._toggle_pane_focus()

    def action_focus_pane_backward(self) -> None:
        """`Shift+Tab`: the same toggle as `action_focus_pane_forward`, under
        its own action name only so the footer names both keys (see the
        comment on `BINDINGS`)."""
        self._toggle_pane_focus()

    def _toggle_pane_focus(self) -> None:
        """Move focus between the stacks and the details pane.

        Away from a card, focus goes to the pane; away from the pane, it goes
        back to whichever card or empty marker it left, remembered in
        `_last_position` rather than always the first one. `CardDetail`'s own
        `escape` binding calls `action_focus_pane_backward`, so leaving the
        pane by either key lands in the same place.
        """
        detail = self.query_one(CardDetail)
        if isinstance(self.focused, BoardItem):
            self._last_position = (self.focused.column, self.focused.row)
            detail.focus()
        else:
            self.query_one(Stacks).focus_at(*self._last_position)

    def on_screen_resume(self) -> None:
        """Back from the planning threads (or any screen above): show what changed
        meanwhile at once, instead of on the next poll."""
        self._poll_board()

    def action_move(self, columns: int, rows: int) -> None:
        """Focus the item `columns` stacks and `rows` cards away, stopping at the edges."""
        current = self.focused
        if not isinstance(current, BoardItem):
            self.grid[0][0].focus()
            return
        self.query_one(Stacks).focus_at(current.column + columns, current.row + rows)

    def _board_stamp(self) -> object:
        # the claim's liveness also turns on the claiming process and its transcript,
        # which live outside the board folder, so the board reads again when the wake
        # stops or crashes (as the project page does, `autonomouse.tui.project`)
        root = board_root()
        report, _ = claim_liveness(root, board_commands.project_show(root, self.slug))
        return super()._board_stamp(), report and report.status

    def _board_fingerprint(
        self,
    ) -> tuple[
        Project,
        tuple[Card, ...],
        tuple[board_plans.Thread, ...],
        Brief | None,
        bool,
        bool,
    ]:
        # the project, its threads, its brief and the PAUSE file too, so a claim taken,
        # a thread joined, a brief written or a pause elsewhere redraws the header;
        # and whether a wake works the project, so its in_progress cards show stopped
        root = board_root()
        project = board_commands.project_show(root, self.slug)
        report, _ = claim_liveness(root, project)
        return (
            project,
            tuple(board_commands.card_list(root, project=self.slug)),
            tuple(board_plans.thread_list(root)),
            load_brief(root, self.slug),
            pause_path().exists(),
            wake_working(report),
        )

    def _board_changed(self) -> None:
        # `_poll_board` has just stored the fresh fingerprint (`_board_fingerprint`)
        self._redraw(*self._live_fingerprint)

    def _redraw(
        self,
        project: Project,
        cards: tuple[Card, ...],
        threads: tuple[board_plans.Thread, ...],
        brief: Brief | None,
        paused: bool,
        working: bool,
    ) -> None:
        """Show a new board state: rewrite the header's title, its status and
        description lines and its planning thread lines in place, update the cards that
        changed (`Stacks.show`), and show the new version of the card in the details
        pane. Nothing else on the screen is rebuilt (see the module docstring)."""
        self.title = project.name
        lines = _thread_lines(project, threads, icons=self.app.icons)
        shown = self.query_one("#project-header", Static)
        shown.update(lines)
        shown.display = bool(lines.plain)
        # the status line and description read the board themselves; the brief, the
        # PAUSE file and the wake's liveness are in the fingerprint for their sake
        self.query_one(AppHeader).refresh_status()
        stacks = self.query_one(Stacks)
        stacks.display = True
        # the first call focuses the first item
        stacks.show(list(cards), working=working)
        detail = self.query_one(CardDetail)
        was_working, detail.working = detail.working, working
        if detail.card is not None:
            shown = next((card for card in cards if card.id == detail.card.id), None)
            if shown is not None and (shown != detail.card or was_working != working):
                detail.show(shown)
