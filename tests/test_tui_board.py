"""The board view: stack order, sideways scrolling, arrow-key reach, every legend key,
and its frames: framed columns and details pane, heavy card borders, a bright chosen card."""

import asyncio
import itertools
import json
import time

import pytest
from textual import events
from textual.color import Color
from textual.command import CommandPalette
from textual.widgets import DataTable, Static

import autonomouse
from autonomouse.board import commands as board_commands
from autonomouse.board.schema import Stack
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import (
    MIN_COLUMN_WIDTH,
    STACK_ORDER,
    BoardItem,
    BoardScreen,
    Stacks,
)
from autonomouse.tui.card_detail import CardDetail
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import FRAME, STACK_LOOK
from autonomouse.tui.plan import ThreadsScreen
from autonomouse.tui.project import ProjectScreen
from tui_wait import board_settled, focus_lands, focus_of, legend_shows, pause_until

# stack -> card ids in it, in position order; cancelled stays empty on purpose
CARDS = {
    "decision_needed": ["sys-a1"],
    "todo": ["sys-b1", "sys-b2", "sys-b3"],
    "in_progress": ["sys-c1", "sys-c2"],
    "done": ["sys-d1"],
    "backlog": ["sys-e1", "sys-e2"],
    "cancelled": [],
}
ALL_IDS = {card_id for ids in CARDS.values() for card_id in ids}


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


def _put_in_stack(root, card_id: str, stack: str) -> None:
    # test setup only: place the card straight into a stack the commands would
    # need a full run (claim, worktree, finish) to reach
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data["stack"] = stack
    path.write_text(json.dumps(data))


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    _new_project(tmp_path, "system", 1)
    _new_project(tmp_path, "integrations", 2)
    for stack, ids in CARDS.items():
        for position, card_id in enumerate(ids):
            board_commands.card_new(
                tmp_path,
                card_id=card_id,
                project="system",
                title=f"title of {card_id}",
                goal="g",
                test="t",
                position=position,
            )
            _put_in_stack(tmp_path, card_id, stack)
    # a card of another project must not show on the system board
    board_commands.card_new(
        tmp_path,
        card_id="int-a1",
        project="integrations",
        title="x",
        goal="g",
        test="t",
    )
    return tmp_path


async def _open_system_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    # the board, once its stacks are laid out (tests read where they sit on screen)
    # and its first focus has landed (the keys a test presses go to it)
    return await pause_until(
        pilot,
        lambda: pilot.app.screen,
        lambda screen: board_settled(screen)
        and screen.query_one("#stacks").region.width > 0,
    )


def _focused_id(app) -> str | None:
    item = app.focused
    assert isinstance(item, BoardItem)
    return item.card.id if item.card is not None else None


async def test_stacks_run_in_order_with_their_cards(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        columns = list(screen.query(".stack"))
        assert [c.id for c in columns] == [f"stack-{s.value}" for s in STACK_ORDER]
        assert [s.value for s in STACK_ORDER] == list(CARDS)
        # columns sit left to right on screen in that order
        xs = [c.region.x for c in columns]
        assert xs == sorted(xs) and len(set(xs)) == len(xs)
        for column, stack in zip(columns, STACK_ORDER):
            ids = [i.card.id for i in column.query(BoardItem) if i.card is not None]
            assert ids == CARDS[stack.value]
        assert not screen.query("#stack-cancelled .card")
        assert screen.query("#stack-cancelled .empty")


async def test_narrow_terminal_scrolls_instead_of_squeezing(board):
    app = AutonomouseApp()
    async with app.run_test(size=(70, 30)) as pilot:
        screen = await _open_system_board(pilot)
        stacks = screen.query_one("#stacks")
        for column in screen.query(".stack"):
            assert column.outer_size.width >= MIN_COLUMN_WIDTH
        assert stacks.max_scroll_x > 0
        assert stacks.scroll_x == 0
        for column in range(1, len(STACK_ORDER)):
            await pilot.press("right")
            await pause_until(
                pilot,
                lambda: focus_of(screen),
                lambda focus, column=column: focus is not None and focus[1] == column,
            )
        # the last column scrolled into view
        last = screen.query_one("#stack-cancelled")
        await pause_until(
            pilot,
            lambda: (stacks.scroll_x, last.region),
            lambda state: state[0] > 0 and screen.region.contains_region(state[1]),
        )


async def test_wide_terminal_shows_the_first_four_stacks(board):
    app = AutonomouseApp()
    async with app.run_test(size=(200, 30)) as pilot:
        screen = await _open_system_board(pilot)
        visible = [
            c.id
            for c in screen.query(".stack")
            if screen.region.contains_region(c.region)
        ]
        assert visible == [f"stack-{s.value}" for s in STACK_ORDER[:4]]
        widths = {c.outer_size.width for c in screen.query(".stack")}
        assert min(widths) > MIN_COLUMN_WIDTH  # grows past the minimum on a wide screen


async def test_keyboard_reaches_every_card(board):
    app = AutonomouseApp()
    async with app.run_test(size=(70, 30)) as pilot:
        await _open_system_board(pilot)
        reached = set()
        for _ in STACK_ORDER:
            for _ in range(5):
                await pilot.press("up")
            for _ in range(5):
                reached.add(_focused_id(app))
                await pilot.press("down")
            await pilot.press("right")
        reached.discard(None)
        assert reached == ALL_IDS


async def test_arrows_stop_at_the_edges(board):
    app = AutonomouseApp()
    async with app.run_test(size=(70, 30)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("left", "up")
        assert _focused_id(app) == "sys-a1"


async def test_mouse_click_focuses_a_card(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        target = next(
            i for i in screen.query(BoardItem) if i.card and i.card.id == "sys-b2"
        )
        await pilot.click(target)
        assert app.focused is target


async def test_a_click_on_a_card_title_chooses_the_card(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        target = next(
            i for i in screen.query(BoardItem) if i.card and i.card.id == "sys-b2"
        )
        assert await pilot.click(target.query_one("#card-title", Static))
        await focus_lands(pilot, "sys-b2")
        assert app.focused is target


def _frame(widget) -> set[tuple[str, Color]]:
    """The border type and colour of each edge of `widget`."""
    return {
        getattr(widget.styles, f"border_{edge}")
        for edge in ("top", "right", "bottom", "left")
    }


def _colour(app, variable: str) -> Color:
    return Color.parse(app.theme_variables[variable.removeprefix("$")])


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
async def test_every_column_and_the_pane_sit_in_the_frame(board, theme):
    """Each stack column and the details pane are in the viewer's heavy frame, at rest
    in its resting colour; the pane takes the focus colour while it holds focus."""
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        app.theme = theme
        screen = await _open_system_board(pilot)
        rest = {(FRAME.border, _colour(app, FRAME.colour))}
        focused = {(FRAME.border, _colour(app, FRAME.focus))}
        columns = list(screen.query(".stack"))
        assert len(columns) == len(STACK_ORDER)
        for column in columns:
            assert _frame(column) == rest, column.id
        pane = screen.query_one(CardDetail)
        assert _frame(pane) == rest

        await pilot.press("tab")
        await pause_until(pilot, lambda: app.focused, lambda f: f is pane)
        await pause_until(pilot, lambda: _frame(pane), lambda f: f == focused)
        await pilot.press("shift+tab")
        await focus_lands(pilot, "sys-a1")
        await pause_until(pilot, lambda: _frame(pane), lambda f: f == rest)


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
async def test_every_card_has_a_heavy_border_and_the_chosen_one_the_focus_colour(
    board, theme
):
    """A resting card's heavy border is in its stack's colour; the chosen card's is in
    the frame's focus colour, on a raised background, its title (the card's id) still
    in its stack's colour. The marker of an empty stack keeps its dashed border."""
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        app.theme = theme
        screen = await _open_system_board(pilot)
        await focus_lands(pilot, "sys-a1")
        cards = {i.card.id: i for i in screen.query(BoardItem) if i.card is not None}
        chosen = cards.pop("sys-a1")
        for card_id, item in cards.items():
            look = STACK_LOOK[item.card.stack]
            assert _frame(item) == {(FRAME.border, _colour(app, look.colour))}, card_id
        decision = STACK_LOOK[Stack.DECISION_NEEDED]
        assert _frame(chosen) == {(FRAME.border, _colour(app, FRAME.focus))}
        assert chosen.styles.border_title_color == _colour(app, decision.text)
        assert chosen.styles.background == _colour(app, "$boost")  # raised, as before

        # choosing another card gives the first its stack's colour back
        await pilot.press("right")
        await focus_lands(pilot, "sys-b1")
        await pause_until(
            pilot,
            lambda: _frame(chosen),
            lambda f: f == {(FRAME.border, _colour(app, decision.colour))},
        )

        empty = screen.query_one("#stack-cancelled .empty", BoardItem)
        assert {edge[0] for edge in _frame(empty)} == {"dashed"}


async def test_one_whole_card_shows_between_header_and_pane_on_80x30(board):
    app = AutonomouseApp()
    async with app.run_test(size=(80, 30)) as pilot:
        screen = await _open_system_board(pilot)
        await focus_lands(pilot, "sys-a1")
        header = screen.query_one(AppHeader).region
        pane = screen.query_one(CardDetail).region
        card = await pause_until(
            pilot,
            lambda: screen.query_one("#stack-decision_needed BoardItem.card").region,
            lambda region: region.height > 0,
        )
        column = screen.query_one("#stack-decision_needed").region
        assert header.bottom <= card.y and card.bottom <= pane.y
        assert column.contains_region(card)
        # the whole card: its border, title, rule and strip
        item = screen.query_one("#stack-decision_needed BoardItem.card", BoardItem)
        assert card.height == item.outer_size.height >= 5


async def test_board_refreshes_when_a_card_moves_elsewhere(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        todo_col = list(CARDS).index("todo")
        in_progress_col = list(CARDS).index("in_progress")
        assert [i.card.id for i in screen.grid[todo_col] if i.card] == CARDS["todo"]

        _put_in_stack(board, "sys-b1", "in_progress")  # as if run from another shell
        in_progress_ids = await pause_until(
            pilot,
            lambda: [i.card.id for i in screen.grid[in_progress_col] if i.card],
            lambda ids: "sys-b1" in ids,
        )

        todo_ids = [i.card.id for i in screen.grid[todo_col] if i.card]
        assert todo_ids == ["sys-b2", "sys-b3"]
        assert "sys-b1" in in_progress_ids


async def test_live_refresh_keeps_focus_at_the_same_position(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")
        await focus_lands(pilot, "sys-b1")
        await pilot.press("down")  # todo column, row 1: sys-b2
        await focus_lands(pilot, "sys-b2")
        cancelled_col = list(CARDS).index("cancelled")

        # an unrelated change, in a different column, made from another shell
        _put_in_stack(board, "sys-e1", "cancelled")

        def _redrawn_and_settled():
            ids = [i.card.id for i in screen.grid[cancelled_col] if i.card]
            return ids, app.focused

        await pause_until(
            pilot,
            _redrawn_and_settled,
            lambda state: "sys-e1" in state[0] and isinstance(state[1], BoardItem),
        )

        assert isinstance(app.focused, BoardItem)
        assert (app.focused.column, app.focused.row) == (1, 1)
        assert _focused_id(app) == "sys-b2"


async def test_empty_board_row_opens_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, BoardScreen)


async def _until(pilot, read, expected):
    """Pause until `read()` returns `expected`: a key's effect lands on a later turn of
    the app's message loop than `pilot.press` waits for on a loaded machine."""
    await pause_until(pilot, read, lambda value: value == expected)


def _screen_type(app):
    return type(app.screen)


async def _palette_opens_and_closes(pilot) -> None:
    before = pilot.app.screen
    await pilot.press("ctrl+p")
    await _until(pilot, lambda: _screen_type(pilot.app), CommandPalette)
    await pilot.press("escape")
    await _until(pilot, lambda: pilot.app.screen, before)


async def test_every_legend_key_does_what_it_says(board, monkeypatch):
    # `i` opens the project page, which asks gh for the open pull requests
    monkeypatch.setattr("autonomouse.tui.project.gh_open_prs", lambda repo: [])
    app = AutonomouseApp()
    # 100 columns: the board's legend on a decision card needs 94, `i` and the
    # new `tab`/`shift+tab` focus keys included
    async with app.run_test(size=(100, 30)) as pilot:
        # project list: enter, i, t, ctrl+p, q
        await legend_shows(pilot, lambda keys: keys == {"enter", "i", "t", "ctrl+p", "q"})
        await _palette_opens_and_closes(pilot)
        await _until(pilot, lambda: isinstance(app.focused, DataTable), True)
        await pilot.press("i")  # the project's details; escape backs out of them
        await _until(pilot, lambda: _screen_type(app), ProjectScreen)
        await legend_shows(pilot, lambda keys: keys == {"escape", "t", "ctrl+p", "q"})
        await pilot.press("escape")
        await _until(pilot, lambda: isinstance(app.focused, DataTable), True)
        await pilot.press("t")
        await _until(pilot, lambda: app.theme, LIGHT_THEME)
        await pilot.press("t")
        await _until(pilot, lambda: app.theme, DARK_THEME)
        screen = await _open_system_board(pilot)  # presses enter

        # the legend is the framed footer, with the project's slug and the version;
        # the footer composes again whenever the keys change, so its label is read
        # once it is there
        def about() -> str | None:
            labels = screen.query_one(AppFooter).query(".footer-about")
            return str(labels.first(Static).visual) if labels else None

        await pause_until(
            pilot,
            about,
            lambda text: text == f"system   autonomouse v{autonomouse.__version__}",
        )

        # board: the four arrows, tab, shift+tab, escape, p, i, t, ctrl+p, q
        # sys-a1 (first-column focus) sits in decision_needed with no Decision
        # recorded (`_put_in_stack` only patches the stack field), so cancel
        # applies but answer has nothing open to answer
        board_keys = {
            "left",
            "right",
            "up",
            "down",
            "tab",
            "shift+tab",
            "escape",
            "p",
            "i",
            "t",
            "ctrl+p",
            "q",
            "x",
        }
        await legend_shows(pilot, lambda keys: keys == board_keys)
        await _palette_opens_and_closes(pilot)
        await focus_lands(pilot, "sys-a1")
        await pilot.press("i")
        await _until(pilot, lambda: _screen_type(app), ProjectScreen)
        await pilot.press("escape")
        await _until(pilot, lambda: app.screen, screen)
        await focus_lands(pilot, "sys-a1")
        await pilot.press("p")  # the planning threads; escape backs out of them
        await _until(pilot, lambda: _screen_type(app), ThreadsScreen)
        # enter, r, n and m act on threads (tests/test_tui_plan.py)
        thread_keys = {"enter", "escape", "r", "n", "m", "t", "ctrl+p", "q"}
        await legend_shows(pilot, lambda keys: keys == thread_keys)
        await pilot.press("escape")
        await _until(pilot, lambda: app.screen, screen)
        await focus_lands(pilot, "sys-a1")
        await pilot.press("right")
        await focus_lands(pilot, "sys-b1")
        await pilot.press("down")
        await focus_lands(pilot, "sys-b2")
        await pilot.press("up")
        await focus_lands(pilot, "sys-b1")
        await pilot.press("left")
        await focus_lands(pilot, "sys-a1")
        await pilot.press("t")
        await _until(pilot, lambda: app.theme, LIGHT_THEME)
        await pilot.press("escape")
        await _until(pilot, lambda: isinstance(app.focused, DataTable), True)
        assert app.screen is not screen
        await pilot.press("enter")
        await _until(pilot, lambda: _screen_type(app), BoardScreen)
        await pilot.press("q")
        await _until(pilot, lambda: app.is_running, False)


async def test_board_survives_changes_landing_mid_redraw(board, monkeypatch):
    """A wake writes cards while the screen is still redrawing the last change.

    The live-refresh poll runs in its own timer task, so a poll can find a new change
    while the screen is mid-redraw and queue a second redraw right behind the first.
    Before the fix every redraw recomposed the whole screen: the second one removed the
    `Header` the first had just mounted, before the title update that `Header` had
    queued on the screen ran, and the app died with `NoMatches: No nodes match
    HeaderTitle`. A short poll interval and writes every 10ms, faster than a redraw
    takes, make that overlap happen many times within two seconds.
    """
    monkeypatch.setattr("autonomouse.tui.live.POLL_INTERVAL", 0.01)
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        header = screen.query_one(AppHeader)
        # plain sleeps, not `pilot.pause`: a wake does not wait for the screen to settle
        stacks = itertools.cycle(("in_progress", "done", "todo"))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and app.is_running:
            _put_in_stack(board, "sys-b1", next(stacks))  # as if a wake wrote it
            await asyncio.sleep(0.01)
        _put_in_stack(board, "sys-b1", "in_progress")
        await pilot.pause(0.5)

        assert app.is_running
        assert app.screen is screen
        assert screen.query_one(AppHeader) is header  # the header is never recreated
        in_progress = [i.card.id for i in screen.grid[2] if i.card]
        assert "sys-b1" in in_progress


async def test_click_during_board_rebuild_raises_nothing(board):
    """A board change detaches the card-title `Static` of every card that left its
    stack (`Stacks.show`), but Textual's own screen map still resolves a
    click to that detached widget until the next layout — Textual issue 6643, open,
    present in the pinned 8.2.8 (the newest release; upgrading does not help). Before
    the fix, a `MouseDown` landing there crashed with `AttributeError: 'NoneType'
    object has no attribute 'region'` (`textual/screen.py`, building `SelectStart`
    from the detached widget's `None` parent). `AutonomouseApp.ALLOW_SELECT = False`
    keeps a click from ever reaching that branch.

    `stacks.recompose()`, which detaches every card at once, is awaited directly
    here rather than a board change through `pilot.pause()`: `pause()` gives
    Textual's own idle handling time to rebuild the screen map behind the removal,
    closing exactly the window the real bug hits.
    """
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        stacks = screen.query_one(Stacks)
        target = next(
            i for i in screen.query(BoardItem) if i.card and i.card.id == "sys-b1"
        )
        title = target.query_one(".card-title", Static)
        # where the title sits once the layout has placed it
        region = await pause_until(pilot, lambda: title.region, lambda r: r.width > 0)
        click_x, click_y = region.offset

        await stacks.recompose()  # detaches every card, as a card leaving its stack does
        assert title.parent is None  # the old title is detached, as in the bug
        stale_widget, _ = screen.get_widget_at(click_x, click_y)
        assert stale_widget is title  # the screen map still resolves to it

        mouse_kwargs = {
            "widget": title,
            "x": click_x,
            "y": click_y,
            "delta_x": 0,
            "delta_y": 0,
            "button": 1,
            "shift": False,
            "meta": False,
            "ctrl": False,
            "screen_x": click_x,
            "screen_y": click_y,
        }
        screen._forward_event(events.MouseDown(**mouse_kwargs))  # raised before the fix
        screen._forward_event(events.MouseUp(**mouse_kwargs))


def test_allow_select_is_off_for_the_known_textual_issue():
    assert AutonomouseApp.ALLOW_SELECT is False
