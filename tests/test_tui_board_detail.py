"""Wiring: the detail panel follows focus around the board and renders through it."""

import json
import os
import re
from datetime import UTC, datetime, timedelta

import pytest
from textual.widgets.markdown import MarkdownBlock

from autonomouse.board import commands as board_commands
from autonomouse.board.schema import Phase, Stack
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem
from autonomouse.tui.card_detail import NO_SELECTION, CardDetail, CardLine
from autonomouse.tui.decision import AnswerModal
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.reading import ReadingView
from test_board_stopped_phase import hold_claim
from tui_wait import board_settled, focus_lands, legend_shows, pause_until


def _new_project(root, slug: str, priority: int) -> None:
    board_commands.project_new(
        root,
        slug=slug,
        name=slug.title(),
        goal=f"goal for {slug}",
        repo=f"https://github.com/test/{slug}",
        repo_path=f"/home/test/{slug}",
        default_branch="main",
        priority=priority,
    )


def _patch_card(root, card_id: str, **fields) -> None:
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    _new_project(tmp_path, "system", 1)
    board_commands.card_new(
        tmp_path,
        card_id="sys-a1",
        project="system",
        title="a card",
        goal="the first-column goal",
        test="the first-column test",
        position=0,
    )
    # decision_needed is STACK_ORDER's first column, so this card gets the
    # initial focus; todo (the next column) is left empty on purpose
    _patch_card(tmp_path, "sys-a1", stack="decision_needed")

    board_commands.card_new(
        tmp_path,
        card_id="sys-b1",
        project="system",
        title="working card",
        goal="the in-progress goal",
        test="the in-progress test",
        position=0,
    )
    _patch_card(
        tmp_path,
        "sys-b1",
        stack="in_progress",
        phase="testing",
        iteration=2,
        assigned={
            "model": "sonnet",
            "effort": "high",
            "agent_type": "worker-high",
            "session_id": "s1",
            "pid": 1,
            "picked_up_at": "2026-09-22T06:00:00Z",
        },
    )
    # a live wake works the project, so sys-b1 shows its phase, not stopped
    hold_claim(tmp_path, "system", pid=os.getpid())
    return tmp_path


async def _open_system_board(pilot):
    from textual.widgets import DataTable

    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    # the board, once its first focus has landed (the keys a test presses go to it)
    # and the panel shows that card
    return await pause_until(pilot, lambda: pilot.app.screen, board_settled)


def _detail_text(screen) -> str:
    return screen.query_one(CardDetail).source


def _line_text(screen) -> str:
    """The pane's opening line as drawn: the card's id, title, pill, badge and times."""
    line = screen.query_one(CardDetail).query_one(CardLine)
    return "\n".join(row.plain for row in line.rows(line.size.width))


async def test_initial_focus_shows_its_card_in_the_panel(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        # sys-a1 sits in decision_needed, STACK_ORDER's first column
        assert "the first-column goal" in _detail_text(screen)


async def test_moving_focus_updates_the_panel(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed -> todo, empty here
        await focus_lands(pilot, None, column=1)
        await pilot.press("right")  # todo -> in_progress: sys-b1
        await focus_lands(pilot, "sys-b1")
        assert "the in-progress goal" in _detail_text(screen)


async def test_in_progress_card_shows_the_five_extra_facts_through_the_app(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed -> todo, empty here
        await focus_lands(pilot, None, column=1)
        await pilot.press("right")  # todo -> in_progress: sys-b1
        await focus_lands(pilot, "sys-b1")
        text = _detail_text(screen)
        assert f"**{PLAIN.model} Model:** sonnet" in text
        assert "**Reasoning:** high" in text
        assert f"**Phase:** {PLAIN.phase[Phase.TESTING]} testing" in text
        assert f"**{PLAIN.iteration} Iteration:** 2" in text
        assert f"**{PLAIN.elapsed} Picked up:**" in text


async def test_title_edited_elsewhere_shows_in_the_panel(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed -> todo, empty here
        await focus_lands(pilot, None, column=1)
        await pilot.press("right")  # todo -> in_progress: sys-b1
        await focus_lands(pilot, "sys-b1")
        assert "working card" in _line_text(screen)

        # as `autonomouse card edit sys-b1 --title …` from another terminal
        board_commands.card_edit(board, "sys-b1", title="renamed card")
        text = await pause_until(
            pilot, lambda: _line_text(screen), lambda text: "renamed card" in text
        )

        assert "renamed card" in text
        assert "working card" not in text


async def test_title_edited_elsewhere_shows_even_on_a_slow_poll(board, monkeypatch):
    """A live-refresh poll slower than any one fixed wait must still show the change.

    sys-t34: four tests waited a fixed 1.2 seconds against the 0.5s
    `autonomouse.tui.live.POLL_INTERVAL`, which a loaded machine can outrun. Patching
    `POLL_INTERVAL` to 3 seconds reproduces that with margin to spare: on the base
    branch's fixed `pilot.pause(1.2)` the poll has not fired yet, so the panel still
    shows the old title and the test fails. Waiting for the title itself to change,
    as `test_title_edited_elsewhere_shows_in_the_panel` now does, survives the slower
    poll.
    """
    monkeypatch.setattr("autonomouse.tui.live.POLL_INTERVAL", 3.0)
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed -> todo, empty here
        await focus_lands(pilot, None, column=1)
        await pilot.press("right")  # todo -> in_progress: sys-b1
        await focus_lands(pilot, "sys-b1")
        assert "working card" in _line_text(screen)

        # as `autonomouse card edit sys-b1 --title …` from another terminal
        board_commands.card_edit(board, "sys-b1", title="renamed card")
        text = await pause_until(
            pilot, lambda: _line_text(screen), lambda text: "renamed card" in text
        )

        assert "renamed card" in text
        assert "working card" not in text


async def test_empty_stack_marker_shows_the_placeholder(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed -> todo, which is empty here
        await focus_lands(pilot, None, column=1)
        assert _detail_text(screen) == NO_SELECTION
        # no card, no first line
        assert not screen.query_one(CardDetail).query_one("#card-line").display


@pytest.fixture
def board_with_a_long_card(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    _new_project(tmp_path, "system", 1)
    board_commands.card_new(
        tmp_path,
        card_id="sys-a1",
        project="system",
        title="a long card",
        goal="line " * 2000,  # far longer than the pane, so it must scroll
        test="t",
        position=0,
        stack="todo",
    )
    return tmp_path


async def test_tab_reaches_the_pane_pagedown_scrolls_it_escape_returns(
    board_with_a_long_card,
):
    """The card's own test (`sys-t9`): a headless pilot opens a card whose
    details are longer than the pane, presses Tab then PageDown, and the
    pane's scroll offset is greater than zero; Escape returns focus to the
    stacks. On the base branch `Tab` cycles through every stack column and
    empty marker before ever reaching the pane (11 presses in a small board),
    so a single `Tab` here leaves focus on a `BoardItem`, not the pane, and
    this fails.
    """
    app = AutonomouseApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed (empty) -> todo: sys-a1
        await focus_lands(pilot, "sys-a1")
        detail = screen.query_one(CardDetail)

        await pilot.press("tab")
        await pause_until(pilot, lambda: screen.focused, lambda focused: focused is detail)

        await pilot.press("pagedown")
        await pause_until(pilot, lambda: detail.scroll_offset.y, lambda y: y > 0)

        await pilot.press("escape")
        await focus_lands(pilot, "sys-a1")


async def test_shift_tab_also_moves_focus_between_the_stacks_and_the_pane(
    board_with_a_long_card,
):
    app = AutonomouseApp()
    async with app.run_test(size=(80, 24)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")
        await focus_lands(pilot, "sys-a1")
        detail = screen.query_one(CardDetail)

        await pilot.press("shift+tab")  # stacks -> pane
        await pause_until(pilot, lambda: screen.focused, lambda focused: focused is detail)

        await pilot.press("shift+tab")  # pane -> stacks, same card as before
        await focus_lands(pilot, "sys-a1")


@pytest.fixture
def board_with_open_decision(tmp_path, monkeypatch):
    """A `decision_needed` card with a real, unanswered `Decision` (via the real
    `card_ask`, not a hand-patched stack), the only way to exercise `a`'s
    `check_action` gate rather than just land on the right stack with nothing to
    answer. `card_ask` sends a real notify, so this keeps it off this machine's
    own config.toml and credentials the same way `tests/test_tui_decision.py`'s
    `board` fixture does."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "notify-config"))
    _new_project(tmp_path, "system", 1)
    board_commands.card_new(
        tmp_path,
        card_id="sys-a1",
        project="system",
        title="a card",
        goal="g",
        test="t",
        position=0,
    )
    _patch_card(tmp_path, "sys-a1", stack="in_progress")
    board_commands.card_ask(
        tmp_path,
        "sys-a1",
        brief="the brief",
        question="which approach?",
        options=["plan a", "plan b"],
        project="system",
    )
    return tmp_path


async def test_a_from_the_pane_opens_the_answer_dialog(board_with_open_decision):
    """The card's own test (`sys-v5`): on main, `a` is bound only on `BoardItem`,
    so once `Tab` moves focus into the details pane the key reaches nothing and
    the dialog never opens — this fails there. `CardDetail` carrying the same `a`
    binding, gated the same way through `BoardScreen.check_action`, makes it pass.
    """
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)  # sys-a1, decision_needed, initial focus
        detail = screen.query_one(CardDetail)
        await pilot.press("tab")
        await pilot.pause()
        assert screen.focused is detail

        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AnswerModal)
        assert "which approach?" in app.screen.query_one(ReadingView).source


async def test_legend_shows_a_while_the_pane_holds_focus(board_with_open_decision):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        detail = screen.query_one(CardDetail)
        await pilot.press("tab")
        await pause_until(pilot, lambda: screen.focused, lambda focused: focused is detail)
        await legend_shows(pilot, lambda keys: "a" in keys)


def _ago(**delta) -> str:
    return (datetime.now(UTC) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def three_stacks(tmp_path, monkeypatch):
    """A todo, an in_progress (testing, a live wake on the project) and a done card,
    each created two days ago and last updated three hours ago."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    _new_project(tmp_path, "system", 1)
    for card_id, title, stack, size, extra in (
        ("sys-t1", "a todo card", "todo", "s", {}),
        ("sys-p1", "a busy card", "in_progress", "m", {"phase": "testing"}),
        ("sys-d1", "a done card", "done", "xs", {}),
    ):
        board_commands.card_new(
            tmp_path,
            card_id=card_id,
            project="system",
            title=title,
            goal=f"goal of {card_id}",
            test="t",
            position=0,
        )
        _patch_card(
            tmp_path,
            card_id,
            stack=stack,
            size=size,
            created_at=_ago(days=2),
            updated_at=_ago(hours=3, minutes=5),
            **extra,
        )
    hold_claim(tmp_path, "system", pid=os.getpid())
    return tmp_path


def _first_row(widget) -> str:
    """The widget's first row as drawn on screen, without its padding."""
    return "".join(segment.text for segment in widget.render_line(0)).strip()


@pytest.mark.parametrize(
    ("card_id", "title", "pill", "size"),
    [
        ("sys-t1", "a todo card", f"{PLAIN.stack[Stack.TODO]} TODO", "S"),
        ("sys-p1", "a busy card", f"{PLAIN.phase[Phase.TESTING]} TESTING", "M"),
        ("sys-d1", "a done card", f"{PLAIN.stack[Stack.DONE]} DONE", "XS"),
    ],
)
async def test_the_panes_first_line_holds_id_title_pill_size_and_both_times(
    three_stacks, card_id, title, pill, size
):
    """sys-w115: the pane opens with one line, as the sample's does: the card's id and
    title, its stack as a pill (an in_progress card's phase), its size as a badge,
    and at the right how long ago it was created and updated."""
    app = AutonomouseApp()
    async with app.run_test(size=(200, 50)) as pilot:
        screen = await _open_system_board(pilot)
        next(
            i for i in screen.query(BoardItem) if i.card and i.card.id == card_id
        ).focus()
        await focus_lands(pilot, card_id)
        pane = screen.query_one(CardDetail)
        line = pane.query_one(CardLine)
        await pause_until(pilot, lambda: title in _first_row(line), bool)

        def drawn() -> bool:
            """The document shows this card's goal, and every block is laid out."""
            blocks = list(pane.document.query_children(MarkdownBlock))
            return any(f"goal of {card_id}" in str(b.content) for b in blocks) and all(
                b.region.height for b in blocks
            )

        await pause_until(pilot, drawn, bool)

        # the line is the pane's first row of text, above every block of the document
        assert line.content_region.y == pane.content_region.y
        assert all(
            block.region.y > line.region.y
            for block in pane.document.query_children(MarkdownBlock)
        )
        row = _first_row(line)
        assert row.startswith(f"{PLAIN.card} {card_id}  {title}  ")
        assert f" {pill} " in row
        assert f" {PLAIN.size} {size} " in row
        assert row.index(pill) < row.index(f" {PLAIN.size} {size} ")
        assert re.search(
            rf"  {PLAIN.elapsed} created 2d 0h ago \| updated 3h \d+m ago$", row
        ), row
