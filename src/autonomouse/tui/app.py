"""The app shell: a project list, a key legend at the bottom, `q` quits.

The list answers where the human is needed before any board is opened. Its header
(`autonomouse.tui.logo.AppHeader`) reads `autonomouse`, the clock, and one status line
over every project (`status_line`):

    PAUSED · projects 2 | waiting on you 1 | wakes running 1

led by the `PAUSED` pill only while the PAUSE file exists, the waiting count in the
decision colour when above zero, and the wakes running count in the working colour when
above zero. Under the header, the table sits in the viewer's frame (`look.FRAME`, in its
focus colour while the table holds focus), under the heading `Projects` with the project
icon. Each row names the project, then what waits on the human there (its
decision_needed cards and its planning threads waiting on the human, after the waiting
icon), then its wake: the project's status pill (`ACTIVE`, `PAUSED`, `STOPPED`, `DONE`
or `WAITING`, `autonomouse.tui.project_header.project_status`), the same pill the
project's own screens lead their status line with, then the slug the commands take. A
run id and the priority are left out: neither helps choose a board, and the project's
details page (`i`) shows both. The footer is the viewer's framed one
(`autonomouse.tui.footer.AppFooter`), with the version at the right and no slug: the
list belongs to no one project.

This is the first screen the board opens on (`autonomouse` with no arguments).
Enter on a project opens that project's board
(`autonomouse.tui.board.BoardScreen`); escape there comes back here. Opening a board
first plays the logo animation for three seconds (`autonomouse.tui.splash`), which any
key skips; `SPLASH` turns it off, as the test suite does. `i` on a
project opens its details page (`autonomouse.tui.project.ProjectScreen`); escape
there comes back here too.

The project list also watches the board folder (`autonomouse.tui.live.LiveRefresh`)
so a claim taken or released, a card moved or a thread answered from another terminal
shows up here without a keypress. Each read takes the projects, every card and every
planning thread, since the rows count what waits on the human. A wake's liveness turns
on its process and its transcript, which live outside the board folder, so the list's
stamp (`_board_stamp`) also holds each project's claim liveness, as the project details
page's does: a row's pill turns from `ACTIVE` to `STOPPED` on the next poll after its
wake dies, though no board file changed. Its first read is the watch's first poll, so a
bad project, card or thread file at startup leaves the list empty with the error in its
status line, and the list fills once the file reads.

The app keeps the error log (`autonomouse.errors_log`): an exception nothing in the
viewer catches is appended to it before Textual prints it and exits, and a board read a
screen's poll could not make is appended once per distinct message.

The app also holds the icons every screen shows (`icons`, `autonomouse.tui.icons`): the
Nerd Font form when it is started with `nerd_font` (the config's `viewer.nerd_font`,
which the `autonomouse` command reads), else the plain one.
"""

from __future__ import annotations

from typing import ClassVar, NamedTuple

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import DataTable, Static

from autonomouse import errors_log
from autonomouse.board import commands as board_commands
from autonomouse.board import plans as board_plans
from autonomouse.board.paths import board_root
from autonomouse.board.runs import claim_liveness
from autonomouse.board.schema import Project, Stack
from autonomouse.config import pause_path
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.icons import Icons, icon_set
from autonomouse.tui.live import LiveRefresh, ReadError
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import (
    FRAME,
    STACK_LOOK,
    ProjectStatus,
    project_status_pill,
    theme_variables,
)
from autonomouse.tui.project import ProjectScreen
from autonomouse.tui.project_header import (
    COUNT_SEPARATOR,
    SEPARATOR,
    project_status,
)
from autonomouse.tui.splash import SplashScreen

DARK_THEME = "textual-dark"
LIGHT_THEME = "textual-light"

_COLUMNS = ("project", "waiting on you", "wake", "slug")


# Words use the readable `$text-*` shade of a colour (`autonomouse.tui.look`), as the
# project's own status line does (`autonomouse.tui.project_header`).
_DECISION_TEXT = STACK_LOOK[Stack.DECISION_NEEDED].text
_WORKING_TEXT = STACK_LOOK[Stack.IN_PROGRESS].text
_MUTED = "$text-muted"


class ProjectRow(NamedTuple):
    """What one row of the list shows about a project."""

    project: Project
    decisions: int  # its decision_needed cards
    threads: int  # its planning threads waiting on the human
    status: ProjectStatus  # `project_header.project_status`: its wake's pill


class ProjectList(NamedTuple):
    """One read of everything the list shows: a row per project, and whether the PAUSE
    file exists."""

    rows: tuple[ProjectRow, ...]
    paused: bool


class ProjectTable(DataTable):
    """The project list; its own enter and `i` bindings show in the legend as "Open
    board" and "Info"."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "select_cursor", "Open board", key_display="enter"),
        Binding("i", "app.project_details", "Info", key_display="i"),
    ]


class AutonomouseApp(LiveRefresh, App):
    """Lists every project on the board."""

    TITLE = "autonomouse"

    # whether opening a board plays the splash first; `tests/conftest.py` turns it off
    SPLASH: ClassVar[bool] = True

    # A board change removes the widgets of a card that left its stack
    # (`tui/board.py`'s `Stacks.show`); the old card-title `Static` is detached before
    # Textual's screen map is rebuilt, and a click landing in that window resolves to
    # the detached widget and crashes on `container.region` (Textual issue 6643, open,
    # present in the pinned 8.2.8). Turning off text selection keeps a click from ever
    # reaching that branch.
    ALLOW_SELECT = False

    CSS = f"""
    #projects-panel {{
        height: 1fr;
        border: {FRAME.rest};
        padding: 0 1;
    }}
    #projects-panel:focus-within {{
        border: {FRAME.focused};
    }}
    #projects-title {{
        padding-bottom: 1;
        text-style: bold;
    }}
    #projects {{
        height: auto;
        max-height: 1fr;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("t", "toggle_theme", "Theme", show=True),
    ]

    def __init__(self, *, nerd_font: bool = False) -> None:
        super().__init__()
        self._logged_read_errors: set[str] = set()
        self.icons = icon_set(nerd_font=nerd_font)

    def compose(self) -> ComposeResult:
        yield AppHeader(status=self._status_line)
        yield ReadError()
        with Vertical(id="projects-panel"):
            yield Static(f"{self.icons.project} Projects", id="projects-title")
            yield ProjectTable(id="projects", zebra_stripes=True)
        yield AppFooter()

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Textual's theme variables plus the viewer's own for the theme on screen
        (`look.theme_variables`), such as the frame's focus colour. Textual reads them
        again each time the theme changes."""
        return {
            **super().get_theme_variable_defaults(),
            **theme_variables(dark=self.current_theme.dark),
        }

    def on_mount(self) -> None:
        self.theme = DARK_THEME
        # looked up once: the app's own query would later also reach a pushed screen's
        self._header = self.query_one(AppHeader)
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        for column in _COLUMNS:
            # the table cannot measure a pill (a Textual `Content`), so the wake
            # column is as wide as the widest pill
            table.add_column(
                column, width=_pill_width(self.icons) if column == "wake" else None
            )
        self._start_live_refresh()  # its first poll reads the projects and fills the table

    def _status_line(self) -> Content:
        """The header's status line from the last read; empty before the first."""
        if self._live_fingerprint is None:
            return Content()
        return status_line(self._live_fingerprint, icons=self.icons)

    def _fill_projects(self, projects: tuple[ProjectRow, ...]) -> None:
        """(Re)fill the project table with `projects`, replacing whatever rows it had.

        A plain table rebuild rather than `self.refresh(recompose=True)`: recomposing
        the app would rebuild `self.screen`, which while the board screen is pushed on
        top is the board screen, not this one — see `App.recompose`.
        """
        table = self.query_one(DataTable)
        table.clear()
        if not projects:
            table.add_row("(no projects yet)", "", "", "")
            return
        for row in projects:
            table.add_row(*_project_cells(row, icons=self.icons), key=row.project.slug)

    def _board_stamp(self) -> object:
        # a wake's liveness also turns on its process and its transcript, which live
        # outside the board folder, so the list reads again when a wake dies
        root = board_root()
        wakes = []
        for project in board_commands.project_list(root):
            report, _ = claim_liveness(root, project)
            wakes.append((project.slug, report and report.status))
        return super()._board_stamp(), tuple(wakes)

    def _board_fingerprint(self) -> ProjectList:
        root = board_root()
        cards = board_commands.card_list(root)
        threads = board_plans.thread_list(root)
        rows = []
        for project in board_commands.project_list(root):
            mine = [card for card in cards if card.project == project.slug]
            rows.append(
                ProjectRow(
                    project=project,
                    decisions=sum(card.stack == Stack.DECISION_NEEDED for card in mine),
                    threads=sum(
                        thread.project == project.slug
                        and thread.status is board_plans.ThreadStatus.WAITING_HUMAN
                        for thread in threads
                    ),
                    status=project_status(root, project.slug),
                )
            )
        return ProjectList(rows=tuple(rows), paused=pause_path().exists())

    def _board_changed(self) -> None:
        self._fill_projects(self._live_fingerprint.rows)
        self._header.refresh_status()

    def log_board_read_error(self, error: Exception) -> None:
        """Append a failed board read to the error log, once per distinct message for
        the life of the viewer: every screen's poll fails on it each half second, and
        the project list and the board screen can both fail on one project file."""
        message = str(error)
        if message not in self._logged_read_errors:
            self._logged_read_errors.add(message)
            errors_log.record(error)

    def _handle_exception(self, error: Exception) -> None:
        """Textual's handler of an exception nothing in the viewer catches, which prints
        its traceback after the app exits: the same, then a copy in the error log."""
        super()._handle_exception(error)
        errors_log.record(error)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Open the chosen project's board; the empty-board placeholder opens nothing."""
        if event.row_key.value is not None:
            self.open_board(event.row_key.value)

    def open_board(self, slug: str) -> None:
        """Push the project's board, then the splash over it: the board reads its cards
        while the animation plays, and shows when the splash is dismissed."""
        self.push_screen(BoardScreen(slug))
        if self.SPLASH:
            self.push_screen(SplashScreen())

    def action_project_details(self) -> None:
        """Open the focused project's details page; the empty-board placeholder opens
        nothing, and neither does the list with no row while no read has succeeded."""
        table = self.query_one(ProjectTable)
        if table.row_count == 0:
            return
        slug = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        if slug is not None:
            self.push_screen(ProjectScreen(slug))

    def action_toggle_theme(self) -> None:
        """Switch between the dark and the light theme."""
        self.theme = LIGHT_THEME if self.theme == DARK_THEME else DARK_THEME


def _count(n: int, thing: str) -> str:
    return f"{n} {thing}" + ("" if n == 1 else "s")


def status_line(listing: ProjectList, *, icons: Icons) -> Content:
    """`projects N | waiting on you N | wakes running N`, led by the `PAUSED` pill while
    the PAUSE file exists. What waits on the human is every project's decision_needed
    cards and planning threads whose turn is the human's; a wake runs while it works its
    project (`ProjectStatus.ACTIVE`)."""
    waiting = sum(row.decisions + row.threads for row in listing.rows)
    running = sum(row.status is ProjectStatus.ACTIVE for row in listing.rows)
    separator = Content.styled(SEPARATOR, _MUTED)
    line = Content.assemble(
        (f"projects {len(listing.rows)}", _MUTED),
        separator,
        (f"waiting on you {waiting}", f"bold {_DECISION_TEXT}" if waiting else _MUTED),
        separator,
        (f"wakes running {running}", f"bold {_WORKING_TEXT}" if running else _MUTED),
    )
    if not listing.paused:
        return line
    return Content.assemble(
        project_status_pill(ProjectStatus.PAUSED, icons=icons),
        (COUNT_SEPARATOR, _MUTED),
        line,
    )


def _pill_width(icons: Icons) -> int:
    """The width of the widest project status pill in `icons`' form."""
    return max(project_status_pill(s, icons=icons).cell_length for s in ProjectStatus)


def _project_cells(row: ProjectRow, *, icons: Icons) -> tuple[str, Text, Content, str]:
    """The row's cells: the name, what waits on the human (bold, after the waiting icon;
    `nothing` when nothing does), the project's status pill, and the slug."""
    waiting = [
        *([_count(row.decisions, "decision")] if row.decisions else []),
        *([_count(row.threads, "planning thread")] if row.threads else []),
    ]
    needs_you = (
        Text(f"{icons.waiting} {', '.join(waiting)}", style="bold")
        if waiting
        else Text("nothing")
    )
    return (
        row.project.name,
        needs_you,
        project_status_pill(row.status, icons=icons),
        row.project.slug,
    )
