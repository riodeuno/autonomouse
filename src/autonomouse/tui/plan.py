"""Planning threads on the board screen: the `p` key's list, and the request and reply
prompts.

A planning thread is one conversation between the human and the planner about one
request. `p` on the board opens `ThreadsScreen`, the list of the project's planning
threads, in two sections. The open threads come first, the ones waiting on the human at
the top and then the one changed last first; each row says whose turn it is as that
turn's pill (`autonomouse.tui.look.thread_pill`: WAITING ON YOU, PLANNER WORKING, LIVE),
when the thread last changed and the request's first line. The closed threads sit below
in their own list, each row led by the CLOSED pill. The board's header names an open
thread's turn in longer words (`STATUS_WORDS`, `status_cell`), which board.py takes from
here.

The screen has the look every screen of the viewer has. Its header (`AppHeader`) is titled `Planning threads` and shows the
project's status line and description (`autonomouse.tui.project_header`), the same two
lines as the project's board, then the list's own title and the keys' hint. Its footer
(`AppFooter`) ends with the project's slug and the version. The open list, the closed
list and the message pane each sit in the viewer's frame (`autonomouse.tui.look.FRAME`),
each list's heading on its frame's top edge, and the one that holds focus takes the
focus colour; the two prompts sit in the frame in its focus colour, since a prompt holds
the focus while it is open. The title, the section headings and the two prompts carry
the planning thread's icon, or a closed thread's (`autonomouse.tui.icons`, in the form
the app holds). Under the lists, the selected thread shows in
full as a document, in the reading view every screen that shows text to read uses
(`autonomouse.tui.reading.ReadingView`): each message is its own block under a heading
that names who wrote it and when (`thread_markdown`). The pane opens on the newest
message and scrolls back to the older ones. Four keys act on it:

- enter on an open thread runs `autonomouse plan join <thread-id>`, the live planner
  chat, in this terminal;
- `r` on an open thread opens `ReplyModal`, which shows the planner's latest message the
  same way and where the human types an answer to it; it is appended to the thread as the human's message
  (`board_plans.thread_reply`, the call `autonomouse plan reply` makes), so the thread
  waits on the planner and the next `plan step` answers it;
- `n` runs `autonomouse plan new <project>` the same way as enter;
- `m` opens `PlanModal`, where the human types a request the unattended planner
  answers on the next wake (`board_plans.thread_open`, the call `autonomouse plan open`
  makes).

Enter and `r` refuse a closed thread with the same words (`CLOSED_REFUSAL`) and run or
write nothing.

The list's first read is its watch's first poll (`LiveRefresh`), so a thread file
already bad when `p` opens it leaves the list empty, with the error in the status line,
until a read succeeds (sys-s99). Each change the watch sees also redraws the header's
status line, whose count of what waits on the human includes the threads; the header
redraws it every minute as well.

The live commands run as they are, from `PATH`: the screen suspends itself
(`App.suspend`), hands the terminal to the command, and comes back when it exits,
then reads the threads again. The screen never starts claude and never writes a
thread file itself. While the command runs, ctrl-c belongs to it, so the screen
ignores SIGINT until it exits. A command that fails
leaves its message on the terminal until the human presses enter, because resuming the
screen would paint over it.

Like the modals in `autonomouse.tui.decision`, `PlanModal` and `ReplyModal` write
nothing: each dismisses with the typed text (or `None` on escape), and `ThreadsScreen`
opens the thread or appends the reply.
"""

from __future__ import annotations

import signal
import subprocess
from datetime import UTC, datetime
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.events import DescendantFocus
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Input, Static

from autonomouse.board import plans as board_plans
from autonomouse.board.commands import CommandError
from autonomouse.board.paths import board_root
from autonomouse.board.plans import Author, Message, Thread, ThreadStatus

# The module, not its functions: project_header imports this module for
# `last_changed`, so when it is imported first, it is only part way through its own
# import when this line runs.
from autonomouse.tui import project_header
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.format import elapsed
from autonomouse.tui.icons import Icons
from autonomouse.tui.live import LiveRefresh, ReadError
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import FRAME, thread_pill
from autonomouse.tui.reading import COLUMN_WIDTH, ReadingView, plain

# the command the live keys run, looked up on PATH when a key is pressed
AUTONOMOUSE = "autonomouse"

# whose turn it is, in the words a row shows
STATUS_WORDS: dict[ThreadStatus, str] = {
    ThreadStatus.WAITING_HUMAN: "waiting on you",
    ThreadStatus.OPEN: "the planner is working",
    ThreadStatus.WAITING_PLANNER: "you answered; the planner is working",
    ThreadStatus.HUMAN_JOINED: "you are in it live",
    ThreadStatus.CLOSED: "closed",
}

# what enter and `r` say on a closed thread, which neither joins nor answers
CLOSED_REFUSAL = "This thread is closed; there is no planner to join."

# who wrote a message, in the words its heading shows
AUTHOR_WORDS: dict[Author, str] = {Author.PLANNER: "The planner", Author.HUMAN: "You"}
NO_MESSAGES = "This thread has no messages yet."
NO_ANSWER = "The planner has not answered this thread yet."


def last_changed(thread: Thread) -> str:
    """When the thread last changed: its newest message, its live join, or its opening.
    Board times all share one format, so the newest is the largest string."""
    times = [thread.opened_at, *(m.at for m in thread.messages)]
    if thread.joined is not None:
        times.append(thread.joined["at"])
    return max(times)


def status_cell(status: ThreadStatus, *, icons: Icons) -> str:
    """Whose turn it is, as a line of the board's header says it: the turn's icon and
    its words."""
    return f"{icons.thread_status[status]} {STATUS_WORDS[status]}"


def turn_width(icons: Icons) -> int:
    """The width of the `whose turn` column: its widest pill. A table sizes a column by
    its text, and a pill is not text to it, so the column is given this width."""
    return max(thread_pill(status, icons=icons).cell_length for status in ThreadStatus)


def request_line(thread: Thread) -> str:
    """The first line of the thread's first message: the request."""
    lines = thread.messages[0].text.strip().splitlines() if thread.messages else []
    return lines[0] if lines else "(no request text)"


def project_threads(threads: list[Thread], project: str) -> list[Thread]:
    """The project's threads, the one changed last first."""
    mine = [t for t in threads if t.project == project]
    return sorted(mine, key=lambda t: (last_changed(t), t.id), reverse=True)


def open_first(threads: list[Thread]) -> tuple[list[Thread], list[Thread]]:
    """`threads` (newest first) split into the open ones, those waiting on the human at
    the top, and the closed ones; each part keeps its newest-first order."""
    open_ = [t for t in threads if t.status is not ThreadStatus.CLOSED]
    open_.sort(key=lambda t: t.status is not ThreadStatus.WAITING_HUMAN)
    return open_, [t for t in threads if t.status is ThreadStatus.CLOSED]


def latest_planner_message(thread: Thread) -> Message | None:
    """The planner's newest message on the thread, or None before it has said anything."""
    return next((m for m in reversed(thread.messages) if m.from_ is Author.PLANNER), None)


def message_heading(message: Message) -> str:
    """Who wrote the message and when: `The planner, 2026-09-28 09:14` for a message at
    `2026-09-28T09:14:20Z`, the time as the project page shows it."""
    return f"{AUTHOR_WORDS[message.from_]}, {message.at[:16].replace('T', ' ')}"


def message_markdown(message: Message) -> str:
    """One message as a Markdown block: its heading (`message_heading`), then its text.
    The planner writes Markdown, which goes in as it is; the human's text goes through
    `plain`, so it shows as typed."""
    if message.from_ is Author.PLANNER:
        text = message.text.strip()
    else:
        text = plain(message.text.strip())
    return f"## {plain(message_heading(message))}\n\n{text}"


def thread_markdown(thread: Thread) -> str:
    """The whole thread as one Markdown text, its messages oldest first."""
    if not thread.messages:
        return f"*{NO_MESSAGES}*\n"
    return "\n\n".join(message_markdown(m) for m in thread.messages) + "\n"


class ThreadTable(DataTable):
    """The open threads; its own enter binding shows in the legend as "Join live"."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "select_cursor", "Join live", key_display="enter"),
    ]


HINT = (
    "Each row is one conversation between you and the planner about one request.\n"
    "enter  join the chosen open thread: talk with its planner live, in this terminal\n"
    "r      answer the planner's latest message on the chosen open thread\n"
    "n      start a new live chat with the planner, in this terminal\n"
    "m      leave a request here; the planner answers it on the next wake"
)


class ThreadsScreen(LiveRefresh, Screen):
    """The list of one project's planning threads, open ones above closed ones, and the
    keys that join, answer, start or leave one.

    The list also watches the board folder (`LiveRefresh`), so a planner's answer or a
    join from another terminal changes a row without a keypress.
    """

    # The two lists take what they need, up to half the screen between them, and the
    # pane the rest. The frames are the viewer's (`look.FRAME`); a list's heading
    # sits on its frame's top edge.
    DEFAULT_CSS = f"""
    ThreadsScreen #threads-title {{
        text-style: bold;
    }}
    ThreadsScreen .hint {{
        color: $text-muted;
    }}
    ThreadsScreen #lists {{
        height: auto;
    }}
    ThreadsScreen .frame {{
        margin: 0 2;
        border: {FRAME.rest};
        border-title-color: $foreground;
        border-title-style: bold;
    }}
    ThreadsScreen .frame:focus {{
        border: {FRAME.focused};
    }}
    ThreadsScreen DataTable {{
        height: auto;
    }}
    ThreadsScreen #threads {{
        max-height: 30vh;
    }}
    ThreadsScreen #closed {{
        max-height: 20vh;
    }}
    ThreadsScreen #threads-empty {{
        display: none;  /* until a read finds no open thread (`_show`) */
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }}
    ThreadsScreen #message-pane {{
        display: none;  /* until a read finds a thread to select (`_show_message`) */
        height: 1fr;
        margin: 0 2 1 2;
        border-title-color: $text-muted;
        border-title-style: none;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "app.pop_screen", "Board", key_display="esc"),
        Binding("r", "reply", "Reply", key_display="r"),
        Binding("n", "new", "New", key_display="n"),
        Binding("m", "message", "Request", key_display="m"),
    ]

    TITLE = "Planning threads"
    SUB_TITLE = ""  # the status line under the title names the project

    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project
        self.threads: list[Thread] = []
        # the thread and messages the pane shows, to tell a new one from a refresh
        self._shown: tuple[str, list[Message]] | None = None

    def compose(self) -> ComposeResult:
        icons = self.app.icons
        yield AppHeader(
            Static(
                Content(f"{icons.thread} The list of planning threads on {self.project}"),
                id="threads-title",
            ),
            Static(HINT, classes="hint"),
            status=lambda: project_header.status_line(
                board_root(), self.project, icons=icons
            ),
            description=lambda: project_header.description(board_root(), self.project),
        )
        yield ReadError()
        open_heading = Content(f"{icons.thread} Open threads")
        # a pill keeps its own text colour on the cursor's row: the cursor's would be
        # the colour of the CLOSED pill's fill, and its word would vanish
        pill_colour = "renderable"
        with Vertical(id="lists"):
            table = ThreadTable(
                id="threads",
                cursor_type="row",
                zebra_stripes=True,
                cursor_foreground_priority=pill_colour,
                classes="frame",
            )
            table.border_title = open_heading
            yield table
            empty = Static(
                "No open planning threads on this project.",
                id="threads-empty",
                classes="frame",
            )
            empty.border_title = open_heading
            yield empty
            closed = DataTable(
                id="closed",
                cursor_type="row",
                cursor_foreground_priority=pill_colour,
                classes="frame",
            )
            closed.border_title = Content(
                f"{icons.thread_status[ThreadStatus.CLOSED]} Closed threads"
            )
            yield closed
        pane = ReadingView(id="message-pane", classes="frame")
        pane.border_title = "the chosen thread; scroll up for older messages"
        yield pane
        yield AppFooter(self.project)

    def on_mount(self) -> None:
        width = turn_width(self.app.icons)
        table = self.query_one(ThreadTable)
        table.add_column("whose turn", width=width)
        table.add_columns("changed", "request")
        closed = self.query_one("#closed", DataTable)
        closed.add_column("whose turn", width=width)
        closed.add_columns("changed", "request")
        table.focus()
        self._start_live_refresh()  # its first poll reads the threads and shows them

    def _board_fingerprint(self) -> tuple[Thread, ...]:
        return tuple(
            project_threads(board_plans.thread_list(board_root()), self.project)
        )

    def _board_changed(self) -> None:
        self._show(self._live_fingerprint)
        # the status line counts the threads that wait on the human
        self.query_one(AppHeader).refresh_status()

    def _show(self, threads: tuple[Thread, ...]) -> None:
        """Refill both sections from `threads`. The open table's cursor stays on the
        thread it was on, so a thread that moves up the list when it changes stays the
        one `r` answers, or on the same row number when that thread is gone; the closed
        table's cursor stays on its row number."""
        table = self.query_one(ThreadTable)
        row = table.cursor_row
        was_on = self._selected_id()
        self.threads = list(threads)
        open_, closed = open_first(self.threads)
        now = datetime.now(UTC)
        icons = self.app.icons
        table.clear()
        for thread in open_:
            table.add_row(
                thread_pill(thread.status, icons=icons),
                elapsed(last_changed(thread), now=now),
                request_line(thread),
                key=thread.id,
            )
        ids = [thread.id for thread in open_]
        if ids:
            table.move_cursor(row=ids.index(was_on) if was_on in ids else min(row, len(ids) - 1))
        _fill(
            self.query_one("#closed", DataTable),
            [
                (
                    thread_pill(t.status, icons=icons),
                    elapsed(last_changed(t), now=now),
                    request_line(t),
                    t.id,
                )
                for t in closed
            ],
        )
        self.query_one("#threads").display = bool(open_)
        self.query_one("#threads-empty").display = not open_
        self.query_one("#closed").display = bool(closed)
        self._show_message()

    def _selected_id(self) -> str | None:
        """The id of the thread under the cursor of whichever table has focus, open or
        closed; the open table when focus is on neither, or None when that table is
        empty."""
        table = self.focused if isinstance(self.focused, DataTable) else self.query_one(
            ThreadTable
        )
        if table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    def _selected(self) -> Thread | None:
        """The thread under the cursor of whichever table has focus, or None when it is
        empty."""
        selected = self._selected_id()
        return next((t for t in self.threads if t.id == selected), None)

    def _show_message(self) -> None:
        """Show the selected thread in the pane. A thread, or a message, the pane did
        not show before opens with the newest message's heading at the top; a refresh
        that leaves the thread as it was keeps the place the human scrolled to."""
        thread = self._selected()
        pane = self.query_one("#message-pane", ReadingView)
        pane.display = thread is not None
        if thread is None:
            return
        shown = (thread.id, list(thread.messages))
        if shown == self._shown:
            return
        self._shown = shown
        newest = message_heading(thread.messages[-1]) if thread.messages else None
        self.run_worker(self._show_thread(pane, thread_markdown(thread), newest))

    async def _show_thread(
        self, pane: ReadingView, markdown: str, newest_heading: str | None
    ) -> None:
        """Replace the pane's text, then scroll the newest message's heading to the top
        once the heading has its place on screen. The heading is found by its words,
        since a planner's message may hold headings of its own. A newer text in the
        pane, or a pane hidden meanwhile, ends the wait: the scroll belongs to that
        newer text. The screen closing or the app quitting meanwhile removes the pane's
        document before this worker is cancelled, and ends the work too (sys-w98)."""
        document = pane.document
        await pane.update(markdown)
        if not document.is_attached:
            return
        headings = [
            h
            for h in document.query("MarkdownH2")
            if str(h.content) == newest_heading
        ]
        if not headings:
            return
        newest = headings[-1]
        while not newest.size:
            if not document.is_attached or not pane.display or document.source != markdown:
                return
            await pane.wait_for_refresh()
        if not document.is_attached:
            return
        pane.scroll_to_widget(newest, top=True, animate=False)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._show_message()

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        """Tab moving focus between the open and closed tables changes which thread is
        selected without moving a cursor, so it gets no `RowHighlighted` of its own."""
        if isinstance(event.widget, DataTable):
            self._show_message()

    def _reload(self) -> None:
        self._poll_board()  # now, not on the next poll; a bad file shows, not raises

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()  # the app's own handler would open a board named after the thread
        thread = next(t for t in self.threads if t.id == event.row_key.value)
        if thread.status is ThreadStatus.CLOSED:
            self.notify(CLOSED_REFUSAL)
            return
        self._run("plan", "join", thread.id)

    def action_reply(self) -> None:
        """Open `ReplyModal` on the selected thread; the callback below appends the
        reply. A closed thread gets enter's refusal and no prompt."""
        thread = self._selected()
        if thread is None:
            return
        if thread.status is ThreadStatus.CLOSED:
            self.notify(CLOSED_REFUSAL)
            return
        self.app.push_screen(
            ReplyModal(thread), callback=lambda text: self._replied(thread.id, text)
        )

    def action_new(self) -> None:
        self._run("plan", "new", self.project)

    def _run(self, *args: str) -> None:
        """Run `autonomouse <args>` in this terminal with the screen suspended, then read
        the threads again. The command keeps ctrl-c: the screen ignores SIGINT only once
        the command has started, so the command itself starts with the default."""
        command = " ".join((AUTONOMOUSE, *args))
        with self.app.suspend():
            try:
                proc = subprocess.Popen([AUTONOMOUSE, *args])
            except OSError as e:
                failure = f"`{command}` could not start: {e}"
            else:
                previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
                try:
                    code = proc.wait()
                finally:
                    signal.signal(signal.SIGINT, previous)
                failure = None if code == 0 else f"`{command}` exited with code {code}."
            if failure is not None:
                print(f"\n{failure}", flush=True)
                try:
                    input("Press enter to return to the board. ")
                except EOFError:
                    pass
        self._reload()

    def _replied(self, thread_id: str, text: str | None) -> None:
        """`ReplyModal`'s callback: append the human's reply (`autonomouse plan reply`'s
        own call), unless it dismissed with `None` (escape — write nothing). The planner
        may have closed the thread while the human typed; then the reply is refused
        with enter's words, as `r` on a closed thread is."""
        if text is None:
            return
        root = board_root()
        try:
            board_plans.thread_reply(root, thread_id, text)
        except CommandError:
            # `thread_reply` checks the status under the plans lock, so no close slips
            # in between; any other refusal is not this one and stays loud
            if board_plans.load_thread(root, thread_id).status is not ThreadStatus.CLOSED:
                raise
            self._reload()
            self.notify(CLOSED_REFUSAL)
            return
        self._reload()
        self.notify("Your reply is on the thread; the planner answers it on its next step.")

    def action_message(self) -> None:
        self.app.push_screen(PlanModal(self.project), callback=self._requested)

    def _requested(self, text: str | None) -> None:
        """`PlanModal`'s callback: open the planning thread (`autonomouse plan open`'s
        own call), unless it dismissed with `None` (escape — write nothing)."""
        if text is None:
            return
        thread = board_plans.thread_open(board_root(), self.project, text)
        self._reload()
        self.notify(
            f"Planning thread {thread.id} opened; the planner answers on the next wake."
        )


class PlanModal(ModalScreen[str | None]):
    """Prompt for a planning request; dismiss with it once non-empty, or `None` on escape.

    `thread_open` refuses an empty request, so an empty submit keeps the modal open.
    """

    # in the viewer's frame, in its focus colour: the prompt holds the focus while open
    DEFAULT_CSS = f"""
    PlanModal {{
        align: center middle;
    }}
    PlanModal > Vertical {{
        width: 80;
        height: auto;
        border: {FRAME.focused};
        background: $surface;
        padding: 1 2;
    }}
    PlanModal Input {{
        margin-top: 1;
    }}
    PlanModal .hint {{
        color: $text-muted;
        margin-top: 1;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_none", "Back", key_display="esc"),
    ]

    def __init__(self, project: str) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                Content(
                    f"{self.app.icons.thread} Plan for {self.project}: "
                    "what should be built or changed?"
                )
            )
            yield Input(placeholder="your request, in your own words")
            yield Static(
                "enter: send to the planner (it answers on the next wake)   esc: back",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.dismiss(text)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ReplyModal(ModalScreen[str | None]):
    """Prompt for the human's answer to the planner's latest message on a thread, shown
    above the field; dismiss with the answer once non-empty, or `None` on escape.

    `thread_reply` refuses an empty message, so an empty submit keeps the modal open.
    """

    # in the viewer's frame, in its focus colour: the prompt holds the focus while open
    DEFAULT_CSS = f"""
    ReplyModal {{
        align: center middle;
    }}
    ReplyModal > Vertical {{
        width: {COLUMN_WIDTH + 3};  /* the reading column, the border, the scroll bar */
        max-width: 95%;
        height: auto;
        max-height: 90%;
        border: {FRAME.focused};
        background: $surface;
        padding: 1 0;
    }}
    ReplyModal #reply-title {{
        margin: 0 2;
    }}
    ReplyModal #reply-question {{
        height: auto;
        max-height: 60vh;
    }}
    ReplyModal Input, ReplyModal .hint {{
        margin: 1 2 0 2;
    }}
    ReplyModal .hint {{
        color: $text-muted;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_none", "Back", key_display="esc"),
    ]

    def __init__(self, thread: Thread) -> None:
        super().__init__()
        self.thread = thread

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                Content(
                    f"{self.app.icons.thread} Reply to the planner on: "
                    f"{request_line(self.thread)}"
                ),
                id="reply-title",
            )
            message = latest_planner_message(self.thread)
            yield ReadingView(
                message_markdown(message) if message else f"*{NO_ANSWER}*\n",
                id="reply-question",
            )
            yield Input(placeholder="your answer, in your own words")
            yield Static(
                "enter: send to the planner (it answers on its next step)   esc: back",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.dismiss(text)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


def _fill(table: DataTable, rows: list[tuple[object, ...]]) -> None:
    """Replace `table`'s rows with `rows` (cells, then the row key last), keeping the
    cursor on the same row number."""
    row = table.cursor_row
    table.clear()
    for *cells, key in rows:
        table.add_row(*cells, key=key)
    if rows:
        table.move_cursor(row=min(row, len(rows) - 1))
