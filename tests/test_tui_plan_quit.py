"""Leaving the threads screen while its message pane is still drawing a thread.

Choosing a thread starts a worker (`ThreadsScreen._show_thread`) that puts the thread's
text in the pane and then scrolls to its newest message. When the human quits the viewer,
or goes back to the board, before that worker ends, Textual removes the pane's widgets
while the worker still runs. The worker then finds no document in the pane; before
sys-w98 it raised `NoMatches`, which crashed the viewer and wrote the error log.

On a fast machine the worker ends before the quit gets its turn. On a CPU-starved one
it does not (wake 20260929-120520-7e9d). Here the pane's update ends only once the quit
has removed the pane's document, so every run takes the starved machine's order.
"""

import asyncio
import os
import time
from pathlib import Path

import pytest
from textual.await_complete import AwaitComplete
from textual.widgets import DataTable

from autonomouse.board import commands as board_commands
from autonomouse.board.plans import thread_open, thread_reply, thread_say
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.plan import ThreadsScreen, ThreadTable
from autonomouse.tui.reading import ReadingView

TIMEOUT = 10  # seconds a wait below gives the screen before the test fails


async def _until(condition, what: str) -> None:
    end = time.monotonic() + TIMEOUT
    while not condition():
        assert time.monotonic() < end, f"timed out waiting until {what}"
        await asyncio.sleep(0.01)


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Two open threads on `system`, so moving the cursor starts a new pane update."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    board_commands.project_new(
        root,
        slug="system",
        name="System",
        goal="goal for system",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    first = thread_open(root, "system", "add a dark mode")
    thread_say(root, first.id, "which screens?")
    second = thread_open(root, "system", "add a light mode")
    thread_say(root, second.id, "which screens?")
    thread_reply(root, second.id, "all of them")
    return root


@pytest.fixture
def slow_update(monkeypatch):
    """Make the pane's update end only once its document has left the screen, as the
    quit's removal overtakes it on a starved machine. `started` is set when an update
    that the test holds begins."""
    started = asyncio.Event()
    hold = False
    real_update = ReadingView.update

    def update(self: ReadingView, markdown: str) -> AwaitComplete:
        document = self.document
        drawn = real_update(self, markdown)
        if not hold:
            return drawn

        async def finish() -> None:
            await drawn
            started.set()
            while document.parent is not None:  # the quit has not removed it yet
                await asyncio.sleep(0)

        return AwaitComplete(finish())

    def hold_next() -> asyncio.Event:
        nonlocal hold
        hold = True
        return started

    monkeypatch.setattr(ReadingView, "update", update)
    return hold_next


async def _mid_update(pilot, hold_next) -> ThreadsScreen:
    """Open the threads screen, let the first thread draw, then choose the other thread
    and return while its pane update is still running."""
    app = pilot.app
    await app.push_screen(ThreadsScreen("system"))
    await _until(
        lambda: isinstance(app.screen, ThreadsScreen)
        and app.screen.query_one(ThreadTable).row_count == 2,
        "the threads screen lists both threads",
    )
    screen = app.screen
    await app.workers.wait_for_complete()
    started = hold_next()
    table = screen.query_one(ThreadTable)
    table.move_cursor(row=1 - table.cursor_row)
    await asyncio.wait_for(started.wait(), TIMEOUT)
    assert any(w.name == "_show_thread" and w.is_running for w in app.workers)
    return screen


def _errors_logged() -> str:
    log = Path(os.environ["AUTONOMOUSE_ERRORS_LOG"])
    return log.read_text() if log.exists() else ""


async def test_quitting_while_the_pane_updates_raises_and_logs_nothing(board, slow_update):
    """The viewer quits the way the test driver's own quit does, and the way the sys-w91
    worker saw it fail: `App._shutdown` removes the screens while the worker still runs,
    before the app cancels its workers."""
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _until(lambda: app.query_one(DataTable).row_count, "the project list shows")
        await _mid_update(pilot, slow_update)
    # leaving `run_test` quits the app and re-raises any exception it or a worker raised
    assert app._exception is None
    assert _errors_logged() == ""


async def test_leaving_the_screen_while_the_pane_updates_raises_and_logs_nothing(
    board, slow_update
):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _until(lambda: app.query_one(DataTable).row_count, "the project list shows")
        screen = await _mid_update(pilot, slow_update)
        await pilot.press("escape")
        await _until(lambda: not screen.is_attached, "the threads screen is gone")
        await app.workers.wait_for_complete()
        assert app._exception is None
        assert not app._exit
    assert app._exception is None
    assert _errors_logged() == ""
