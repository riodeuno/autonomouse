"""A board file that is bad when a screen first reads it: the viewer stays up (sys-s99).

sys-t3 made the screens' poll (`autonomouse.tui.live.LiveRefresh`) keep the viewer
running when a read fails. The first read of each screen ran before the poll started,
so the same bad file still quit the viewer: a bad project file when `autonomouse` starts,
a bad card file when a board opens, a bad planning-thread file when `p` opens the
threads. Each case here starts with the bad file, checks the viewer stays up and says
what it cannot read, then fixes the file and checks the screen draws.

The last tests run `autonomouse` with no arguments in a child process: a viewer that
crashes makes the command exit non-zero, and one the human quits exits 0.
"""

import os
import re
import subprocess
import sys

import pytest
from textual.widgets import DataTable

from autonomouse.board import commands as board_commands
from autonomouse.board.plans import thread_open, thread_path
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.live import POLL_INTERVAL, ReadError
from autonomouse.tui.plan import ThreadsScreen, ThreadTable
from autonomouse.tui.project import ProjectScreen
from autonomouse.tui.reading import ReadingView
from tui_wait import pause_until

ENTRY = re.compile(r"^=== \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.MULTILINE)
CUT_SHORT = '{"id": '  # a hand edit cut short: not valid JSON
POLLS = 3 * POLL_INTERVAL + 0.1


def _new_project(root, slug: str, priority: int) -> None:
    board_commands.project_new(
        root,
        slug=slug,
        name=slug.title(),
        goal=f"goal for {slug}",
        repo=f"https://github.com/test/{slug}",
        repo_path=f"/home/test/{slug}",
        priority=priority,
        default_branch="main",
    )


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Two projects; `system` (priority 1, the first row) has one card, in todo."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    _new_project(root, "system", 1)
    _new_project(root, "integrations", 2)
    board_commands.card_new(
        root,
        card_id="sys-a1",
        project="system",
        title="first title",
        goal="g",
        test="t",
        stack="todo",
    )
    return root


def _log_entries() -> int:
    log = os.environ["AUTONOMOUSE_ERRORS_LOG"]  # tests/conftest.py points it at a temp file
    return len(ENTRY.findall(open(log).read())) if os.path.exists(log) else 0


def _says_it_cannot_read(line: ReadError, file_name: str) -> None:
    assert line.display
    shown = str(line.render())
    assert "Cannot read the board" in shown
    assert "not valid JSON" in shown
    assert file_name in shown
    assert "Nothing is shown until a read succeeds" in shown
    assert "Showing the last board read" not in shown  # nothing was read yet


def _slugs(app: AutonomouseApp) -> set[str]:
    table = app.query_one(DataTable)
    return {table.get_row_at(i)[3] for i in range(table.row_count)}  # the slug column


def _position(focused) -> tuple[int, int] | None:
    """The focused board item's column and row; `None` while no board item has focus."""
    return (focused.column, focused.row) if isinstance(focused, BoardItem) else None


def _titles(screen: BoardScreen) -> list[str]:
    return [item.card.title for item in screen.query(BoardItem) if item.card]


async def test_a_bad_project_file_at_startup_keeps_the_viewer_up(board):
    path = board / "projects" / "integrations" / "project.json"
    good = path.read_text()
    path.write_text(CUT_SHORT)

    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.is_running
        _says_it_cannot_read(app.query_one(ReadError), "integrations/project.json")
        assert _slugs(app) == set()  # no row, not even "(no projects yet)"
        assert _log_entries() == 1

        await pilot.press("i", "enter")  # the keys of a row, on no row
        await pilot.pause()
        assert app.is_running
        assert app.screen is app.screen_stack[0]  # nothing opened

        await pilot.pause(POLLS)
        assert app.is_running
        assert _log_entries() == 1  # every poll fails on it; logged once

        path.write_text(good)
        await pilot.pause(POLLS)

        assert app.is_running
        assert not app.query_one(ReadError).display
        assert _slugs(app) == {"system", "integrations"}


async def test_a_bad_card_file_when_its_board_opens_keeps_the_viewer_up(board):
    path = board / "projects" / "system" / "cards" / "sys-a1.json"
    good = path.read_text()

    app = AutonomouseApp()
    async with app.run_test(size=(200, 40)) as pilot:
        await pause_until(pilot, lambda: _slugs(app), lambda slugs: slugs)
        assert _slugs(app) == {"system", "integrations"}  # read while the file is good
        # the list reads the cards too (its rows count the decisions); it keeps its rows
        # on a failed read, so enter still opens the board
        path.write_text(CUT_SHORT)
        await pilot.press("enter")  # the system board

        # the board screen, once its first read has failed and its line says so
        screen = await pause_until(
            pilot,
            lambda: app.screen,
            lambda screen: isinstance(screen, BoardScreen)
            and screen.query_one(ReadError).display,
        )
        assert app.is_running
        _says_it_cannot_read(screen.query_one(ReadError), "sys-a1.json")
        assert _titles(screen) == []
        # the one read error, and no second error from a screen that never drew
        assert _log_entries() == 1

        # the keys of a card, on a board with none drawn
        await pilot.press("right", "down", "tab", "shift+tab", "a", "x", "d")
        await pilot.pause()
        assert app.is_running
        assert app.screen is screen

        path.write_text(good)
        # the next poll reads the fixed file, draws the card and puts focus on the
        # first column's first item, where a board that opens on good files puts it
        await pause_until(
            pilot,
            lambda: (_titles(screen), _position(screen.focused)),
            lambda state: state == (["first title"], (0, 0)),
        )

        assert app.is_running
        assert not screen.query_one(ReadError).display
        await pilot.press("right")  # to the todo column: the keys work
        await pause_until(
            pilot,
            lambda: _position(screen.focused),
            lambda position: position == (1, 0),
        )
        assert screen.focused.card is not None
        assert screen.focused.card.id == "sys-a1"
        assert _log_entries() == 1


def _project_text(screen: ProjectScreen) -> str:
    return screen.query_one("#project-details", ReadingView).source


async def test_a_bad_card_file_when_the_project_page_opens_keeps_the_viewer_up(
    board, monkeypatch
):
    # the page's own gh worker never runs while the file is bad (its board read fails
    # first); mocked so the fix step below, which does let the worker run, calls no gh
    monkeypatch.setattr("autonomouse.tui.project.gh_open_prs", lambda repo: [])
    path = board / "projects" / "system" / "cards" / "sys-a1.json"
    good = path.read_text()

    app = AutonomouseApp()
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause()
        assert _slugs(app) == {"system", "integrations"}  # read while the file is good
        path.write_text(CUT_SHORT)  # the list keeps its rows on a failed read
        await pilot.press("i")  # system's project page (system is the first row)
        await pilot.pause(POLLS)

        screen = app.screen
        assert app.is_running
        assert isinstance(screen, ProjectScreen)
        _says_it_cannot_read(screen.query_one(ReadError), "sys-a1.json")
        assert _project_text(screen) == ""  # nothing drawn
        # the one read error, and no second error from a screen that never drew
        assert _log_entries() == 1

        path.write_text(good)
        await pilot.pause(POLLS)
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app.is_running
        assert not screen.query_one(ReadError).display
        text = _project_text(screen)
        assert "goal for system" in text
        assert "| ○ todo | 1 |" in text  # sys-a1 is back, in todo
        assert _log_entries() == 1


async def test_a_bad_card_file_at_startup_shows_on_the_project_list(board):
    path = board / "projects" / "system" / "cards" / "sys-a1.json"
    good = path.read_text()
    path.write_text(CUT_SHORT)

    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app.is_running
        _says_it_cannot_read(app.query_one(ReadError), "sys-a1.json")
        assert _slugs(app) == set()
        assert _log_entries() == 1

        path.write_text(good)
        await pause_until(
            pilot, lambda: _slugs(app), lambda slugs: slugs == {"system", "integrations"}
        )
        assert not app.query_one(ReadError).display


async def test_a_bad_thread_file_when_the_threads_open_keeps_the_viewer_up(board):
    thread = thread_open(board, "system", "a request for the planner")
    path = thread_path(board, thread.id)
    good = path.read_text()

    app = AutonomouseApp()
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.press("enter")  # the system board, read while the file is good
        await pause_until(
            pilot,
            lambda: app.screen,
            lambda screen: isinstance(screen, BoardScreen)
            and _titles(screen) == ["first title"],
        )

        path.write_text(CUT_SHORT)
        await pilot.press("p")
        # the threads screen, once its first read has failed and its line says so
        screen = await pause_until(
            pilot,
            lambda: app.screen,
            lambda screen: isinstance(screen, ThreadsScreen)
            and screen.query_one(ReadError).display,
        )
        assert app.is_running
        _says_it_cannot_read(screen.query_one(ReadError), f"{thread.id}.json")
        assert screen.query_one(ThreadTable).row_count == 0
        # "no threads yet", or an empty planner message, would be a claim the screen
        # cannot make
        assert not screen.query_one("#threads-empty").display
        assert not screen.query_one("#message-pane").display
        assert _log_entries() == 1  # the board's poll and this screen fail on one file

        await pilot.press("r", "enter")  # the keys of a thread, on no thread
        await pilot.pause()
        assert app.is_running
        assert app.screen is screen

        path.write_text(good)
        table = screen.query_one(ThreadTable)
        await pause_until(
            pilot,
            lambda: [table.get_row_at(i)[2] for i in range(table.row_count)],
            lambda requests: requests == ["a request for the planner"],
        )

        assert app.is_running
        assert not screen.query_one(ReadError).display


def _run_autonomouse(tmp_path, on_mount: str) -> subprocess.CompletedProcess:
    """Run `autonomouse` with no arguments, its viewer headless (a child process has no
    terminal), with `on_mount` as the body of the app's mount handler."""
    script = (
        "import sys\n"
        "from textual.app import App\n"
        "from autonomouse import cli\n"
        "from autonomouse.tui.app import AutonomouseApp\n"
        "def on_mount(self):\n"
        f"    {on_mount}\n"
        "AutonomouseApp.on_mount = on_mount\n"
        "AutonomouseApp.run = lambda self: App.run(self, headless=True)\n"
        "sys.argv = ['autonomouse']\n"
        "cli.main()\n"
    )
    env = {
        **os.environ,
        "AUTONOMOUSE_HOME": str(tmp_path / "board"),
        "AUTONOMOUSE_ERRORS_LOG": str(tmp_path / "errors.log"),
    }
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_viewer_that_crashes_makes_autonomouse_exit_non_zero(tmp_path):
    result = _run_autonomouse(tmp_path, "raise RuntimeError('viewer boom')")

    assert result.returncode == 1, result.stderr
    assert "RuntimeError: viewer boom" in (tmp_path / "errors.log").read_text()


def test_a_viewer_the_human_quits_makes_autonomouse_exit_zero(tmp_path):
    result = _run_autonomouse(tmp_path, "self.exit()")

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "errors.log").exists()
