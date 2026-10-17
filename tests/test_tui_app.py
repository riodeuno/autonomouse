"""The app shell: opens, lists projects, `q` quits, the legend shows every key; its
header counts the projects, what waits on the human and the wakes running, led by the
PAUSED pill while the PAUSE file exists; each row's wake cell is the project's status
pill, which follows its wake's liveness; the table sits in the viewer's frame (sys-w116).
"""

import json
import subprocess
import time

import pytest
from textual.color import Color
from textual.widgets import DataTable, Static
from textual.widgets._footer import FooterKey

import autonomouse
from autonomouse.board import commands as board_commands
from autonomouse.board.schema import Stack
from autonomouse.tui import app as app_module
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp, ProjectTable
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import FRAME, STACK_LOOK, ProjectStatus, project_status_pill
from test_board_stopped_phase import hold_claim
from tui_wait import legend_shows


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


def _decision_card(root, slug: str, card_id: str) -> None:
    board_commands.card_new(
        root, card_id=card_id, project=slug, title=card_id, goal="g", test="t"
    )
    # test setup only: moves the card as a worker's `card decide` would
    path = root / "projects" / slug / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data["stack"] = "decision_needed"
    path.write_text(json.dumps(data))


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Projects system and integrations, no card, no claim; the PAUSE file lives in the
    test's own config folder, never the human's."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    _new_project(root, "system", 1)
    _new_project(root, "integrations", 2)
    return root


def _pause_file(root):
    return root.parent / "config" / "PAUSE"


@pytest.fixture
def wake_process():
    """A process that stands in for a wake: alive until the test kills it by its pid."""
    proc = subprocess.Popen(["sleep", "300"])
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


async def _pause_until(pilot, read, ok, *, timeout: float = 10.0, step: float = 0.1):
    """Pause in short steps, reading with `read()` after each, until `ok(value)` is
    true, for up to `timeout` seconds total. Raises with the last value `read()` saw.

    A fixed pause guesses how long the live-refresh poll
    (`autonomouse.tui.live.POLL_INTERVAL`) takes to notice a change and redraw; on a
    loaded machine that guess can fall short. Waiting for the change itself removes
    the guess.
    """
    deadline = time.monotonic() + timeout
    value = read()
    while not ok(value):
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"condition never held within {timeout}s; last saw: {value!r}"
            )
        await pilot.pause(step)
        value = read()
    return value


def _status(app):
    """The header's status line, as shown."""
    return app.query_one(AppHeader).query_one("#header-status", Static).content


def _style_of(line, text: str) -> str:
    """The style of the span that covers `text` in `line`."""
    start = line.plain.index(text)
    styles = [
        str(span.style)
        for span in line.spans
        if span.start <= start and start + len(text) <= span.end
    ]
    assert styles, f"no span covers {text!r}"
    return styles[-1]


def _wake_cell(app, slug: str):
    return app.query_one(DataTable).get_row(slug)[2]


async def test_opens_and_lists_projects(board):
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        rows = [table.get_row_at(i) for i in range(table.row_count)]
        assert {row[0] for row in rows} == {"System", "Integrations"}  # the name
        assert {row[3] for row in rows} == {"system", "integrations"}  # the slug
        assert [str(c.label) for c in table.ordered_columns] == [
            "project",
            "waiting on you",
            "wake",
            "slug",
        ]


async def test_empty_board_shows_a_placeholder_row(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert table.row_count == 1
        assert table.get_row_at(0)[0] == "(no projects yet)"


async def test_q_quits(board):
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.is_running
        await pilot.press("q")
        assert not app.is_running


async def test_theme_toggle_switches_dark_and_light(board):
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == DARK_THEME
        await pilot.press("t")
        assert app.theme == LIGHT_THEME
        await pilot.press("t")
        assert app.theme == DARK_THEME


async def test_the_header_counts_projects_waiting_and_running_wakes(
    board, wake_process
):
    """Two projects, one decision card and one working wake; the PAUSED pill leads the
    line only while the PAUSE file exists."""
    _decision_card(board, "integrations", "int-a1")
    hold_claim(board, "system", pid=wake_process.pid)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        line = await _pause_until(
            pilot,
            lambda: _status(app),
            lambda c: c.plain == "projects 2 | waiting on you 1 | wakes running 1",
        )
        assert app.title == "autonomouse"
        decision = STACK_LOOK[Stack.DECISION_NEEDED].text
        assert decision in _style_of(line, "waiting on you 1")

        paused = project_status_pill(ProjectStatus.PAUSED, icons=PLAIN)
        _pause_file(board).write_text("")
        line = await _pause_until(
            pilot, lambda: _status(app), lambda c: c.plain.startswith(paused.plain)
        )
        assert line.plain == (
            f"{paused.plain} · projects 2 | waiting on you 1 | wakes running 1"
        )

        _pause_file(board).unlink()
        await _pause_until(
            pilot,
            lambda: _status(app).plain,
            lambda p: p == "projects 2 | waiting on you 1 | wakes running 1",
        )


async def test_nothing_waiting_is_not_in_the_decision_colour(board):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        line = await _pause_until(
            pilot,
            lambda: _status(app),
            lambda c: c.plain == "projects 2 | waiting on you 0 | wakes running 0",
        )
        decision = STACK_LOOK[Stack.DECISION_NEEDED].text
        assert decision not in _style_of(line, "waiting on you 0")


async def test_each_row_holds_its_pill_and_a_dead_wake_turns_stopped(
    board, wake_process
):
    """No board file changes when the wake's process dies: the list's stamp holds the
    wake's liveness, so the next poll reads the board again."""
    assert not hasattr(app_module, "wake_status")  # the old rule is gone from here
    hold_claim(board, "system", pid=wake_process.pid)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        active = project_status_pill(ProjectStatus.ACTIVE, icons=PLAIN)
        await _pause_until(
            pilot, lambda: _wake_cell(app, "system"), lambda c: c == active
        )
        # no wake on it and not marked done
        waiting = project_status_pill(ProjectStatus.WAITING, icons=PLAIN)
        assert _wake_cell(app, "integrations") == waiting

        wake_process.kill()
        wake_process.wait()
        stopped = project_status_pill(ProjectStatus.STOPPED, icons=PLAIN)
        await _pause_until(
            pilot, lambda: _wake_cell(app, "system"), lambda c: c == stopped
        )
        assert _status(app).plain.endswith("wakes running 0")


async def test_a_project_marked_done_shows_done_and_is_listed_last(board):
    """`system` has priority 1 and `integrations` 2; marking `system` done, from another
    terminal, moves its row below `integrations` on the next poll."""
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        waiting = project_status_pill(ProjectStatus.WAITING, icons=PLAIN)
        await _pause_until(
            pilot, lambda: _wake_cell(app, "system"), lambda c: c == waiting
        )
        table = app.query_one(DataTable)
        assert [table.get_row_at(i)[3] for i in range(table.row_count)] == [
            "system",
            "integrations",
        ]

        board_commands.project_done(board, "system")
        done = project_status_pill(ProjectStatus.DONE, icons=PLAIN)
        await _pause_until(
            pilot, lambda: _wake_cell(app, "system"), lambda c: c == done
        )
        assert [table.get_row_at(i)[3] for i in range(table.row_count)] == [
            "integrations",
            "system",
        ]


async def test_app_refreshes_when_the_board_changes_elsewhere(board, wake_process):
    app = AutonomouseApp()
    async with app.run_test() as pilot:
        waiting = project_status_pill(ProjectStatus.WAITING, icons=PLAIN)
        await _pause_until(
            pilot, lambda: _wake_cell(app, "system"), lambda c: c == waiting
        )

        # another terminal's wake takes the claim
        hold_claim(board, "system", pid=wake_process.pid)
        active = project_status_pill(ProjectStatus.ACTIVE, icons=PLAIN)
        await _pause_until(
            pilot, lambda: _wake_cell(app, "system"), lambda c: c == active
        )


async def test_the_wake_column_shows_the_whole_pill(board):
    """The table cannot measure a pill, so the column is sized to the widest one."""
    app = AutonomouseApp(nerd_font=True)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        wake = table.ordered_columns[2]
        widest = max(
            project_status_pill(s, icons=app.icons).cell_length for s in ProjectStatus
        )
        assert wake.get_render_width(table) >= widest


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
async def test_the_table_sits_in_the_frame_in_both_themes(board, theme):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        app.theme = theme
        await pilot.pause()
        panel = app.query_one("#projects-panel")
        table = app.query_one(ProjectTable)
        assert table.parent is panel
        heading = panel.query_one("#projects-title", Static)
        assert str(heading.content) == f"{PLAIN.project} Projects"
        # the table holds focus, so its frame takes the focus colour
        assert app.focused is table
        colour = Color.parse(app.theme_variables[FRAME.focus.removeprefix("$")])
        for edge in ("top", "right", "bottom", "left"):
            assert getattr(panel.styles, f"border_{edge}") == (FRAME.border, colour)


async def test_legend_shows_every_bound_key_with_the_version_at_the_right(board):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        bound = {b.key for b in (*AutonomouseApp.BINDINGS, *ProjectTable.BINDINGS)}
        keys = await legend_shows(pilot, lambda shown: bound <= shown)
        assert bound <= keys
        footer = app.query_one(AppFooter)
        about = footer.query_one(".footer-about", Static)
        assert (
            str(about.content) == f"autonomouse v{autonomouse.__version__}"
        )  # no slug
        last_key = max(key.region.right for key in footer.query(FooterKey))
        assert about.region.x >= last_key
        assert about.region.right == footer.content_region.right


async def test_a_click_on_a_row_opens_its_board(board):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one(ProjectTable)
        await _pause_until(pilot, lambda: table.row_count, lambda n: n == 2)
        row = table.get_row_index("integrations")
        # the first click moves the cursor to the row, the second opens it
        await pilot.click(ProjectTable, offset=(2, 1 + row))
        await pilot.click(ProjectTable, offset=(2, 1 + row))
        screen = await _pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, BoardScreen)
        )
        assert screen.slug == "integrations"
