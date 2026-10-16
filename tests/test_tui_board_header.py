"""The board header (sys-w114): the sample's framed box, with the project's name as the
screen's title, the project's status line and description (`autonomouse.tui.
project_header`), then the planning thread lines inside the same frame; no separate
name box and no old status word."""

import json
import os

import pytest
from textual.widgets import DataTable, Static

from autonomouse.board import commands as board_commands
from autonomouse.board.brief import brief_path
from autonomouse.board.plans import thread_open
from autonomouse.board.schema import Phase
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import ProjectStatus, project_status_pill
from autonomouse.tui.project_header import description, status_line
from test_board_stopped_phase import hold_claim
from tui_wait import board_settled, pause_until

NAME = "Autonomouse system"
GOAL = "This project builds the system itself: the board and the screen. It also wires the rest."
FIRST_SENTENCE = "This project builds the system itself: the board and the screen."


def _pill(status: ProjectStatus) -> str:
    return project_status_pill(status, icons=PLAIN).plain


def _patch(path, **fields) -> None:
    # test setup only: write fields a full wake would need to reach through the commands
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


def _card_json(root, card_id: str):
    return root / "projects" / "system" / "cards" / f"{card_id}.json"


@pytest.fixture
def board(tmp_path, monkeypatch):
    """One project, one card in todo and one in backlog, no claim, no PAUSE file."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    board_commands.project_new(
        root,
        slug="system",
        name=NAME,
        goal=GOAL,
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    for card_id, stack in (("sys-a1", "todo"), ("sys-a2", "backlog")):
        board_commands.card_new(
            root,
            card_id=card_id,
            project="system",
            title=card_id,
            goal="g",
            test="t",
            stack=stack,
        )
    return root


def _pause_file(board):
    return board.parent / "config" / "PAUSE"


async def _open_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    return await pause_until(pilot, lambda: pilot.app.screen, board_settled)


def _line(screen, line_id: str) -> str:
    """What one line of the header shows, as plain text."""
    return str(screen.query_one(AppHeader).query_one(line_id, Static).visual)


def _status(screen) -> str:
    return _line(screen, "#header-status")


async def test_the_header_shows_the_name_as_its_title_then_the_status_and_description(
    board,
):
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_board(pilot)

        # the project's name, as written, is the screen's title; no box of its own
        assert _line(screen, "#header-title") == NAME
        assert not screen.query("#project-name")
        assert not screen.query("#project-title")

        # cards to work and no wake: waiting; nothing in progress and nothing waits
        status = _status(screen)
        assert status.startswith(
            f"{_pill(ProjectStatus.WAITING)} · system | cards no card in progress"
            " | waiting on you 0 | last change "
        )
        # the line the project page shows too, up to the elapsed time
        line = status_line(board, "system", icons=PLAIN).plain
        assert status.rsplit(" | last change ", 1)[0] == line.rsplit(" | last change ", 1)[0]

        # the description is the goal's first sentence, as the project page's is
        assert _line(screen, "#header-description") == FIRST_SENTENCE
        assert _line(screen, "#header-description") == description(board, "system")

        # no old status word under the header, no priority and no claim line
        header = "\n".join(
            _line(screen, line_id)
            for line_id in ("#header-title", "#header-status", "#header-description")
        )
        for gone in ("priority", "claimed", "free", f"{PLAIN.wake} waiting"):
            assert gone not in header, gone
        # no planning thread is open, so the thread lines take no room
        assert not screen.query_one("#project-header", Static).display


async def test_the_pill_says_paused_then_active_as_the_board_changes(board):
    """A PAUSE file written while the board is open, then a wake that works the
    project, each show in the pill on the next poll, not a minute later."""
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_board(pilot)
        assert _status(screen).startswith(_pill(ProjectStatus.WAITING))

        _pause_file(board).touch()
        await pause_until(
            pilot,
            lambda: _status(screen),
            lambda status: status.startswith(_pill(ProjectStatus.PAUSED)),
        )

        # a wake that works the project is active, paused machine or not
        hold_claim(board, "system", pid=os.getpid())
        await pause_until(
            pilot,
            lambda: _status(screen),
            lambda status: status.startswith(_pill(ProjectStatus.ACTIVE)),
        )
        assert "20260930" not in _status(screen)  # the run id is on no line


async def test_no_card_left_to_work_and_no_wake_is_done(board):
    _patch(_card_json(board, "sys-a1"), stack="done")
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_board(pilot)
        assert _status(screen).startswith(_pill(ProjectStatus.DONE))

        _patch(_card_json(board, "sys-a2"), stack="todo")
        await pause_until(
            pilot,
            lambda: _status(screen),
            lambda status: status.startswith(_pill(ProjectStatus.WAITING)),
        )


async def test_the_status_line_counts_the_cards_by_phase_and_what_waits_on_you(board):
    hold_claim(board, "system", pid=os.getpid())
    _patch(_card_json(board, "sys-a1"), stack="in_progress", phase="writing")
    _patch(_card_json(board, "sys-a2"), stack="decision_needed")
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_board(pilot)
        status = _status(screen)
        assert status.startswith(
            f"{_pill(ProjectStatus.ACTIVE)} · system"
            f" | cards {PLAIN.phase[Phase.WRITING]} 1 writing | waiting on you 1 | last change "
        ), status


async def test_the_planning_thread_lines_sit_in_the_header_under_the_description(board):
    thread_open(board, "system", "add a dark mode")
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_board(pilot)
        header = screen.query_one(AppHeader)
        threads = header.query_one("#project-header", Static)
        assert threads.display
        text = str(threads.visual)
        assert text.startswith(f"{PLAIN.thread} plan: add a dark mode")
        assert "press p for planning" in text
        # inside the frame, under the description
        described = header.query_one("#header-description", Static)
        assert threads.region.y > described.region.y
        assert header.region.contains_region(threads.region)


async def test_the_brief_goal_sentence_replaces_the_goal_and_the_new_name_shows(board):
    """The project's brief gives the description, without the quotes the brief puts
    around the goal sentence; a rename from another terminal shows in the title."""
    brief_path(board, "system").write_text(
        "# system — project brief\n\n"
        "## Why this project exists\n\nBecause.\n\n"
        '## Goal\n\n"You install it with one command\nand it works your cards."\n\n'
        "## Intent\n\nThe creator's time.\n"
    )
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_board(pilot)
        assert (
            _line(screen, "#header-description")
            == "You install it with one command and it works your cards."
        )

        board_commands.project_edit(board, "system", name="Autonomouse board")
        await pause_until(
            pilot,
            lambda: _line(screen, "#header-title"),
            lambda title: title == "Autonomouse board",
        )
