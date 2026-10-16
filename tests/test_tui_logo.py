"""The header every screen of the viewer shows in place of Textual's one-line title bar
(card sys-w111): a box in the viewer's frame, the screen's title at the left of its
first line and the local date and time at the right, then a status line and a
description line when the screen hands in functions that return them, then the
screen's own widgets. The clock's minute tick calls the functions again, a function
that cannot read the board leaves its line as it was, and a long status line wraps.

The header shows no logo (card sys-w99: the human asked on 2026-09-30 that the board
views show no logo in their header). The art stays for the splash; the dark theme shows
it white on dark and the light theme draws its white strokes in the theme's dark
foreground. The art the viewer uses ships in the package (`autonomouse.tui.logo`).

No assertion here depends on the words a screen puts in its title or on the widgets it
hands the header: later cards change both."""

import io
import re
from datetime import UTC, datetime, timedelta

import pytest
from rich.console import Console
from rich.text import Text
from textual.app import ComposeResult
from textual.color import Color
from textual.screen import Screen
from textual.widgets import DataTable, Header, Static

from autonomouse.board import commands as board_commands
from autonomouse.board.schema import BoardValidationError
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.logo import (
    ASSETS,
    FRAMES,
    WHITE,
    AppHeader,
    art_text,
)
from autonomouse.tui.look import FRAME
from autonomouse.tui.plan import ThreadsScreen
from autonomouse.tui.project import ProjectScreen
from tui_wait import pause_until

# The art is drawn in Unicode quadrant and half blocks (U+2580 to U+259F); no text a
# header writes uses them, so any one of them in the header's rows is the logo.
BLOCKS = {chr(c) for c in range(0x2580, 0x25A0)}

# the right end of the title line: the clock icon, the date and the time
CLOCK_AT_RIGHT = re.compile(r"(\S) (\d{4}-\d{2}-\d{2} \d{2}:\d{2})$")


@pytest.fixture
def board(tmp_path, monkeypatch):
    """One project with one card in todo."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    board_commands.project_new(
        root,
        slug="system",
        name="Autonomouse system",
        goal="This project builds the system itself.",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    board_commands.card_new(
        root, card_id="sys-a1", project="system", title="a card", goal="g", test="t"
    )
    # `i` opens the project page, which would ask gh for the open pull requests
    monkeypatch.setattr("autonomouse.tui.project.gh_open_prs", lambda repo: [])
    return root


class Clock:
    """A clock a test moves by hand."""

    def __init__(self, time: datetime) -> None:
        self.time = time

    def __call__(self) -> datetime:
        return self.time


class HeaderScreen(Screen):
    """A screen with nothing but the header it is given and a line of body text."""

    def __init__(self, header: AppHeader) -> None:
        super().__init__()
        self._header = header

    def compose(self) -> ComposeResult:
        yield self._header
        yield Static("the body of the screen", id="body")


def _colours(text: Text) -> set[tuple[int, int, int]]:
    """Every foreground and background colour the text's spans use, as RGB."""
    found = set()
    for span in text.spans:
        for colour in (span.style.color, span.style.bgcolor):
            if colour is not None and colour.triplet is not None:
                found.add(tuple(colour.triplet))
    return found


def _header_rows(app: AutonomouseApp) -> list[str]:
    """The rows of the screen the header covers, as the terminal shows them."""
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        record=True,
        legacy_windows=False,
    )
    console.print(
        app.screen._compositor.render_update(
            full=True, screen_stack=app._background_screens
        )
    )
    region = app.screen.query_one(AppHeader).region
    return console.export_text().splitlines()[region.y : region.bottom]


def _inside(row: str) -> str:
    """A header row without its frame's sides and the padding inside them."""
    assert row[0] == row[-1] == "┃", row
    return row[1:-1].strip()


def _minutes_around(before: datetime, after: datetime) -> set[str]:
    return {f"{before:%Y-%m-%d %H:%M}", f"{after:%Y-%m-%d %H:%M}"}


def _assert_framed_header(app: AutonomouseApp, *, before: datetime) -> None:
    """The header of the current screen: no logo, the frame round it, the screen's
    title in bold at the left of its first line and the time at the right."""
    screen = app.screen
    name = type(screen).__name__
    assert not screen.query(Header), f"{name} still has Textual's Header"
    header = screen.query_one(AppHeader)
    rows = _header_rows(app)
    shown = "\n".join(rows)
    assert not {ch for row in rows for ch in row} & BLOCKS, (
        f"{name}'s header shows the logo:\n{shown}"
    )

    # the viewer's frame, drawn all round the box
    colour = Color.parse(app.theme_variables[FRAME.colour.removeprefix("$")])
    for edge in ("top", "right", "bottom", "left"):
        assert getattr(header.styles, f"border_{edge}") == (FRAME.border, colour), edge
    assert rows[0][0] + rows[0][-1] == "┏┓", shown
    assert rows[-1][0] + rows[-1][-1] == "┗┛", shown

    # the first line: the title at the left, in bold, then the time at the right
    title = header.query_one("#header-title", Static)
    assert str(title.visual), f"{name} shows no title"
    assert title.styles.text_style.bold
    first = _inside(rows[1])
    assert first.startswith(str(title.visual)), shown
    clock = CLOCK_AT_RIGHT.search(first)
    assert clock, shown
    assert clock[1] == PLAIN.clock
    assert clock[2] in _minutes_around(before, datetime.now().astimezone()), shown


async def test_every_screen_shows_the_framed_header_with_title_and_time(board):
    """The project list, a project's board, its details page and its planning threads
    each show the header in its frame, the title at the left, the time at the right,
    and no logo."""
    before = datetime.now().astimezone()
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _assert_framed_header(app, before=before)

        table = app.query_one(DataTable)
        table.move_cursor(row=table.get_row_index("system"))
        await pilot.press("enter")
        await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, BoardScreen)
        )
        await pilot.pause()
        _assert_framed_header(app, before=before)

        await pilot.press("i")
        await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, ProjectScreen)
        )
        await pilot.pause()
        _assert_framed_header(app, before=before)
        await pilot.press("escape")
        await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, BoardScreen)
        )

        await pilot.press("p")
        await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, ThreadsScreen)
        )
        await pilot.pause()
        _assert_framed_header(app, before=before)


async def test_status_and_description_show_on_their_own_lines_and_follow_the_clock(
    board,
):
    """The status line and the description line sit under the title line, above the
    screen's own widgets. The status line is redrawn when the clock moves a minute on,
    and on `refresh_status()`; a minute that has not moved on calls nothing. A status
    line whose height stays the same lays nothing out (sys-w90)."""
    clock = Clock(datetime(2026, 9, 30, 12, 34, 5, tzinfo=UTC))
    changed_at = clock.time
    note = ["last change"]
    calls = []

    def status() -> str:
        calls.append(clock.time)
        minutes = int((clock.time - changed_at).total_seconds() // 60)
        return f"{note[0]} {minutes}m ago"

    header = AppHeader(
        Static("a widget the screen hands in"),
        status=status,
        description=lambda: "A project that builds the system itself.",
        now=clock,
    )
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.push_screen(HeaderScreen(header))
        await pause_until(
            pilot, lambda: len(_header_rows(app)), lambda height: height == 6
        )
        rows = [_inside(row) for row in _header_rows(app)[1:-1]]
        assert rows[0].endswith(f"{PLAIN.clock} 2026-09-30 12:34")
        assert rows[1:] == [
            "last change 0m ago",
            "A project that builds the system itself.",
            "a widget the screen hands in",
        ]
        assert len(calls) == 1

        # within the same minute the header calls nothing again
        clock.time += timedelta(seconds=40)
        await pilot.pause(1.5)
        assert len(calls) == 1

        screen = app.screen
        layouts = []
        lay_out = screen._refresh_layout

        def counted(*args, **kwargs):
            layouts.append(args)
            return lay_out(*args, **kwargs)

        screen._refresh_layout = counted

        clock.time += timedelta(minutes=1)  # 12:35:45
        await pause_until(
            pilot,
            lambda: _inside(_header_rows(app)[2]),
            lambda line: line == "last change 1m ago",
        )
        assert _inside(_header_rows(app)[1]).endswith("2026-09-30 12:35")
        assert len(calls) == 2
        assert layouts == [], "the minute tick laid the screen out"

        # the screen asks for the lines again, with the clock where it was
        note[0] = "last move"
        header.refresh_status()
        await pause_until(
            pilot,
            lambda: _inside(_header_rows(app)[2]),
            lambda line: line == "last move 1m ago",
        )


async def test_a_function_that_cannot_read_the_board_leaves_its_line(
    board, monkeypatch
):
    """A status or description function that raises a board read error leaves the
    line it showed last; the app logs the error and the viewer runs on."""
    clock = Clock(datetime(2026, 9, 30, 12, 34, 5, tzinfo=UTC))
    broken = [False]

    def status() -> str:
        if broken[0]:
            raise BoardValidationError("sys-a1.json: field 'stack' is missing")
        return "cards 1 writing"

    def description() -> str:
        if broken[0]:
            raise OSError("Permission denied: 'project.json'")
        return "A project that builds the system itself."

    app = AutonomouseApp()
    logged: list[Exception] = []
    log = app.log_board_read_error

    def log_and_keep(error: Exception) -> None:
        logged.append(error)
        log(error)

    monkeypatch.setattr(app, "log_board_read_error", log_and_keep)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = HeaderScreen(
            AppHeader(status=status, description=description, now=clock)
        )
        await app.push_screen(screen)
        await pause_until(
            pilot, lambda: len(_header_rows(app)), lambda height: height == 5
        )

        broken[0] = True
        clock.time += timedelta(minutes=1)
        await pause_until(pilot, lambda: len(logged), lambda count: count == 2)
        await pilot.pause()

        assert app.is_running
        assert app.screen is screen
        rows = [_inside(row) for row in _header_rows(app)[1:-1]]
        assert rows[0].endswith("2026-09-30 12:35")
        assert rows[1:] == [
            "cards 1 writing",
            "A project that builds the system itself.",
        ]
        assert [type(error) for error in logged] == [BoardValidationError, OSError]


async def test_a_long_status_line_wraps_at_80_columns_and_loses_no_word(board):
    words = [f"word{i}" for i in range(30)]
    clock = Clock(datetime(2026, 9, 30, 12, 34, 5, tzinfo=UTC))
    app = AutonomouseApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.push_screen(
            HeaderScreen(AppHeader(status=lambda: " ".join(words), now=clock))
        )
        await pause_until(
            pilot, lambda: len(_header_rows(app)), lambda height: height >= 5
        )
        rows = _header_rows(app)
        assert all(len(row) == 80 for row in rows)
        assert _inside(rows[1]).endswith("2026-09-30 12:34")
        status_rows = [_inside(row) for row in rows[2:-1]]
        assert len(status_rows) >= 2, status_rows
        assert " ".join(status_rows).split() == words


async def test_a_screen_that_gives_no_line_shows_title_and_clock_only(board):
    clock = Clock(datetime(2026, 9, 30, 12, 34, 5, tzinfo=UTC))
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await app.push_screen(HeaderScreen(AppHeader(now=clock)))
        await pause_until(
            pilot, lambda: len(_header_rows(app)), lambda height: height == 3
        )
        (row,) = [_inside(row) for row in _header_rows(app)[1:-1]]
        assert row.endswith(f"{PLAIN.clock} 2026-09-30 12:34")


async def test_a_click_on_the_header_leaves_the_screen_as_it_was(board):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, BoardScreen)
        )
        await pilot.pause()
        screen = app.screen
        focused = screen.focused
        header = screen.query_one(AppHeader)
        assert await pilot.click(AppHeader, offset=(0, 0))  # the frame's corner
        assert await pilot.click("#header-title")
        assert await pilot.click("#header-clock")
        for widget in header.children:  # the title line and the board's own widgets
            if widget.display:  # a click on its middle may land on a widget inside it
                await pilot.click(offset=widget.region.center)
        await pilot.pause()
        assert app.is_running
        assert app.screen is screen
        assert screen.focused is focused

        clock = Clock(datetime(2026, 9, 30, 12, 34, 5, tzinfo=UTC))
        other = HeaderScreen(
            AppHeader(
                status=lambda: "a status", description=lambda: "a line", now=clock
            )
        )
        await app.push_screen(other)
        await pause_until(
            pilot, lambda: len(_header_rows(app)), lambda height: height == 5
        )
        assert await pilot.click("#header-status")
        assert await pilot.click("#header-description")
        await pilot.pause()
        assert app.is_running
        assert app.screen is other
        assert [_inside(row) for row in _header_rows(app)[2:-1]] == [
            "a status",
            "a line",
        ]


def test_art_text_swaps_white_only_on_a_light_theme():
    art = "\x1b[38;2;255;255;255m▐\x1b[0m\x1b[38;2;0;145;230m▌\x1b[48;2;255;255;255m \x1b[0m"
    assert _colours(art_text(art, dark=True, ink="#101010")) == {WHITE, (0, 145, 230)}
    assert _colours(art_text(art, dark=False, ink="#101010")) == {
        (16, 16, 16),
        (0, 145, 230),
    }


def test_the_art_ships_in_the_package():
    """Frames 00 to 07 are package files."""
    for name in FRAMES:
        assert (ASSETS / name).is_file(), name
    assert [f"animation/frame_{i:02d}.ans" for i in range(8)] == list(FRAMES)
