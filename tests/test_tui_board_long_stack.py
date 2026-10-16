"""A stack longer than its column mounts only its first cards, and the rest as the human
reaches them: by the arrow keys, or by scrolling the column with the mouse wheel and
clicking a card (`autonomouse.tui.board.Stacks`, sys-w109). Mounting a board of 400
cards whole held the viewer's one thread for three seconds."""

import json

import pytest
from textual import events
from textual.widgets import DataTable

from autonomouse.board import commands as board_commands
from autonomouse.board.schema import Stack
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import MOUNT_AHEAD, STACK_ORDER, BoardItem, StackColumn
from tui_wait import board_settled, focus_lands, pause_until

DONE = 4 * MOUNT_AHEAD
COLUMN = STACK_ORDER.index(Stack.DONE)


@pytest.fixture
def long_board(tmp_path, monkeypatch):
    """One todo card and `DONE` done cards, `sys-d0` on top."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    board_commands.project_new(
        tmp_path,
        slug="system",
        name="System",
        goal="goal for system",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    board_commands.card_new(
        tmp_path, card_id="sys-a1", project="system", title="a card", goal="g", test="t"
    )
    cards = tmp_path / "projects" / "system" / "cards"
    template = json.loads((cards / "sys-a1.json").read_text())
    for index in range(DONE):
        card = dict(template, id=f"sys-d{index}", title=f"done {index}", position=index)
        card["stack"] = "done"
        (cards / f"sys-d{index}.json").write_text(json.dumps(card))
    return tmp_path


async def _open(pilot):
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    await pause_until(pilot, lambda: pilot.app.screen, board_settled)
    return pilot.app.screen


def _mounted(screen) -> list[str]:
    column = screen.query_one("#stack-done", StackColumn)
    return [item.card.id for item in column.query(BoardItem) if item.card]


async def test_the_arrow_keys_reach_the_last_card_of_a_long_stack(long_board):
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open(pilot)
        # every card is on the board; only the first of a long stack are mounted
        assert [item.card.id for item in screen.grid[COLUMN]] == [
            f"sys-d{index}" for index in range(DONE)
        ]
        assert len(_mounted(screen)) < DONE

        for _ in range(COLUMN):
            await pilot.press("right")
            await pilot.pause()
        await focus_lands(pilot, "sys-d0", COLUMN)
        for index in range(1, DONE):
            await pilot.press("down")
            await focus_lands(pilot, f"sys-d{index}", COLUMN)

        # the mounted cards are the column's cards, in order, down to the last
        assert _mounted(screen) == [f"sys-d{index}" for index in range(DONE)]


async def test_the_mouse_wheel_scrolls_a_long_stack_to_its_last_card_to_click(
    long_board,
):
    app = AutonomouseApp()
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open(pilot)
        column = screen.query_one("#stack-done", StackColumn)
        last = screen.grid[COLUMN][-1]

        def wheel_down_until_last_shows():
            return last.parent is not None and screen.can_view_entire(last)

        for _ in range(40 * DONE):  # a few rows a turn of the wheel
            if wheel_down_until_last_shows():
                break
            await pilot._post_mouse_events([events.MouseScrollDown], column)
            await pilot.pause()
        assert wheel_down_until_last_shows(), f"mounted: {_mounted(screen)}"
        assert _mounted(screen) == [f"sys-d{index}" for index in range(DONE)]

        assert await pilot.click(last)
        await focus_lands(pilot, f"sys-d{DONE - 1}", COLUMN)
