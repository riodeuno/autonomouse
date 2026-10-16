"""A board change updates only the cards that changed (sys-t11).

Before sys-t11 every change rebuilt all six columns: every card widget was detached and
replaced, which let a click land on a detached widget (Textual issue 6643), made the screen flicker, and put focus back by
column and row, not by card. Each test here changes the board from "another shell" (a
direct edit of a card file), waits for the live-refresh poll to draw the change, and
checks which widgets survived and where focus is.
"""

import json

import pytest
from textual.widgets import DataTable, Static

from autonomouse.board import commands as board_commands
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.card_detail import CardDetail
from tui_wait import board_settled, focus_lands, pause_until

TODO = 1  # the todo column's index in `STACK_ORDER`
IDS = ["sys-b1", "sys-b2", "sys-b3"]


@pytest.fixture
def board(tmp_path, monkeypatch):
    """One project, `system`, with three cards in todo, in the order of `IDS`."""
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
    for position, card_id in enumerate(IDS):
        board_commands.card_new(
            tmp_path,
            card_id=card_id,
            project="system",
            title=f"title of {card_id}",
            goal="g",
            test="t",
            position=position,
            stack="todo",
        )
    return tmp_path


def _edit_card(root, card_id: str, **fields) -> None:
    # as if another shell wrote the card: the viewer sees it only through its poll
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


async def _open_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    # the board, once it has drawn the cards and its first focus has landed
    return await pause_until(
        pilot,
        lambda: pilot.app.screen,
        lambda screen: board_settled(screen) and set(_items_by_id(screen)) == set(IDS),
    )


async def _focus_sys_b2(pilot) -> BoardItem:
    """Move focus to sys-b2, the todo column's row 1, one key at a time."""
    await pilot.press("right")
    await focus_lands(pilot, "sys-b1")
    await pilot.press("down")
    await focus_lands(pilot, "sys-b2")
    return pilot.app.focused


def _items_by_id(screen) -> dict[str, BoardItem]:
    return {i.card.id: i for i in screen.query(BoardItem) if i.card is not None}


def _ids(column: list[BoardItem]) -> list[str]:
    return [i.card.id for i in column if i.card]


def _title_text(item: BoardItem) -> str:
    return str(item.query_one("#card-title", Static).content)


async def test_title_change_updates_that_card_alone_and_keeps_focus(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_board(pilot)
        focused = await _focus_sys_b2(pilot)
        before = _items_by_id(screen)
        assert focused is before["sys-b2"]

        _edit_card(board, "sys-b1", title="a new title")
        await pause_until(
            pilot,
            lambda: _title_text(_items_by_id(screen)["sys-b1"]),
            lambda title: title == "a new title",
        )
        await pilot.pause()  # a focus change the redraw asked for lands by now

        after = _items_by_id(screen)
        # the two cards that did not change are the very same widgets as before
        assert after["sys-b2"] is before["sys-b2"]
        assert after["sys-b3"] is before["sys-b3"]
        assert app.focused is focused


async def test_focused_card_keeps_focus_when_a_card_above_it_leaves(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_board(pilot)
        focused = await _focus_sys_b2(pilot)  # row 1

        _edit_card(board, "sys-b1", stack="backlog")
        await pause_until(
            pilot,
            lambda: (_ids(screen.grid[TODO]), _ids(screen.grid[4])),  # todo, backlog
            lambda ids: ids == (["sys-b2", "sys-b3"], ["sys-b1"]),
        )

        # sys-b2 moved up to row 0; focus followed the card, not the old row
        assert await focus_lands(pilot, "sys-b2") == ("sys-b2", TODO, 0)
        assert app.focused is focused


async def test_focused_card_follows_its_card_to_another_stack(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_board(pilot)
        await _focus_sys_b2(pilot)

        _edit_card(board, "sys-b2", stack="backlog")

        # the poll reads the change, moves the card and focus follows it to backlog
        await focus_lands(pilot, "sys-b2", column=4)
        assert screen.query_one(CardDetail).card.id == "sys-b2"


async def test_focused_card_change_shows_in_the_detail_pane(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_board(pilot)
        focused = await _focus_sys_b2(pilot)

        _edit_card(board, "sys-b2", title="retitled while focused")
        await pause_until(
            pilot,
            lambda: (_title_text(focused), screen.query_one(CardDetail).card.title),
            lambda titles: titles == ("retitled while focused", "retitled while focused"),
        )
        await pilot.pause()  # a focus change the redraw asked for lands by now

        assert app.focused is focused


async def test_board_change_leaves_focus_in_the_detail_pane(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_board(pilot)
        await _focus_sys_b2(pilot)
        await pilot.press("tab")  # the pane, showing sys-b2
        detail = screen.query_one(CardDetail)
        await pause_until(pilot, lambda: app.focused, lambda focused: focused is detail)

        _edit_card(board, "sys-b1", title="a new title")
        await pause_until(
            pilot,
            lambda: _title_text(_items_by_id(screen)["sys-b1"]),
            lambda title: title == "a new title",
        )
        await pilot.pause()  # a focus change the redraw asked for lands by now

        assert app.focused is detail


async def test_emptied_and_filled_stacks_show_the_right_markers(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_board(pilot)
        for card_id in IDS:
            _edit_card(board, card_id, stack="backlog")

        def drawn():
            return (
                [i.card for i in screen.grid[TODO]],  # [None]: the "(empty)" marker
                _ids(screen.grid[4]),
                len(screen.query("#stack-todo BoardItem")),
                len(screen.query("#stack-backlog BoardItem.empty")),
                str(screen.query_one("#stack-backlog .stack-title", Static).content)[-1],
            )

        await pause_until(pilot, drawn, lambda state: state == ([None], IDS, 1, 0, "3"))
