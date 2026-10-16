"""The project details page: `i` on the project list or on a project's board.

The page has the look the human chose on 2026-09-30 (card sys-w117): a header with the
project's name, its status pill and its description; the reading column in the viewer's
frame; each stack's count as its pill; the framed footer with the slug and the version.

Each test drives the app with a Textual pilot on a temporary board
(`AUTONOMOUSE_HOME`). The page reads the repository's open pull requests through
`autonomouse.tui.project.gh_open_prs`; every test replaces it with a fake reader, so
no test calls gh.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from textual.color import Color
from textual.widgets import DataTable, Footer, Static
from textual.widgets._footer import FooterKey

import autonomouse
from autonomouse.board import commands
from autonomouse.board.runs import Run, RunStatus, save_run
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card
from autonomouse.config import pause_path
from autonomouse.github import GitHubError
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.footer import AppFooter, footer_label
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.live import POLL_INTERVAL
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import (
    FRAME,
    STACK_ORDER,
    ProjectStatus,
    project_status_pill,
    stack_pill,
)
from autonomouse.tui.project import PULLS_LOADING, ProjectScreen
from autonomouse.tui.project_header import description, status_line
from autonomouse.tui.reading import ReadingView
from tui_wait import legend_shows, pause_until

REPO_ROOT = Path(__file__).resolve().parents[1]

ALPHA_GOAL = "Alpha builds the widget factory for the whole team."
BETA_GOAL = "Beta sharpens the tools."
ALPHA_REPO = "https://github.com/acme/widgets"
MATCHED_PR = "https://github.com/acme/widgets/pull/7"
OTHER_PR = "https://github.com/acme/widgets/pull/9"
GH_REASON = "gh pr list failed: HTTP 401: Bad credentials"

# alpha's cards: stack -> ids; alp-a2 carries MATCHED_PR
ALPHA_CARDS = {
    Stack.DECISION_NEEDED: ["alp-a1"],
    Stack.IN_PROGRESS: ["alp-a2", "alp-a3"],
    Stack.TODO: ["alp-a4", "alp-a5", "alp-a6"],
    Stack.DONE: ["alp-a7"],
}


def _run_record(root: Path, run_id: str, project: str, summary: str) -> None:
    started = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T{run_id[9:11]}:00:00Z"
    ended = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T{run_id[9:11]}:45:00Z"
    save_run(
        root,
        Run(
            id=run_id,
            project=project,
            session_id=None,
            pid=None,
            model="opus",
            started_at=started,
            ended_at=ended,
            cards=[],
            jev=[],
            notify_failures=[],
            result=None,
            end_reason=None,
            status=RunStatus.STOPPED,
            summary=summary,
        ),
    )


@pytest.fixture
def board(tmp_path, tmp_path_factory, monkeypatch):
    """beta (priority 1) lists first, so opening alpha's page needs the cursor on it.
    The header reads whether the PAUSE file exists, so the config folder is a temporary
    one, outside the board, and this machine's own is never read."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path_factory.mktemp("config")))
    commands.project_new(
        tmp_path,
        slug="beta",
        name="Beta Tools",
        goal=BETA_GOAL,
        repo="https://github.com/acme/tools",
        repo_path="/tmp/tools",
        default_branch="main",
        priority=1,
    )
    commands.project_new(
        tmp_path,
        slug="alpha",
        name="Alpha Works",
        goal=ALPHA_GOAL,
        repo=ALPHA_REPO,
        repo_path="/tmp/widgets",
        priority=2,
        default_branch="trunk",
    )
    for stack, ids in ALPHA_CARDS.items():
        for position, card_id in enumerate(ids):
            commands.card_new(
                tmp_path,
                card_id=card_id,
                project="alpha",
                title=f"title of {card_id}",
                goal="g",
                test="t",
                position=position,
            )
            path = tmp_path / "projects" / "alpha" / "cards" / f"{card_id}.json"
            card = load_card(path)
            card.stack = stack
            card.pr = MATCHED_PR if card_id == "alp-a2" else None
            save_card(path, card)
    _run_record(tmp_path, "20260925-080000-aaaa", "alpha", "alpha's older wake")
    _run_record(tmp_path, "20260926-090000-bbbb", "alpha", "alpha's newer wake")
    _run_record(tmp_path, "20260926-100000-cccc", "beta", "beta's wake")
    return tmp_path


def _fake_reader(monkeypatch, reader) -> list[str]:
    """Replace the page's pull-request reader; returns the repos it was asked for."""
    asked: list[str] = []

    def fake(repo: str) -> list[dict[str, str]]:
        asked.append(repo)
        return reader(repo)

    monkeypatch.setattr("autonomouse.tui.project.gh_open_prs", fake)
    return asked


def _two_pulls(_repo: str) -> list[dict[str, str]]:
    return [
        {"url": MATCHED_PR, "title": "Add the red widget"},
        {"url": OTHER_PR, "title": "Opened by hand"},
    ]


def _page_text(app) -> str:
    """The page's Markdown text."""
    screen = app.screen
    assert isinstance(screen, ProjectScreen)
    return screen.query_one("#project-details", ReadingView).source


def _line_with(text: str, needle: str) -> str:
    return next(line for line in text.splitlines() if needle in line)


async def _page_from_list(pilot, slug: str) -> ProjectScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index(slug))
    await pilot.press("i")
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, ProjectScreen)
    return screen


async def test_i_on_the_project_list_opens_the_focused_projects_page(
    board, monkeypatch
):
    asked = _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        screen = await _page_from_list(pilot, "alpha")
        assert screen.slug == "alpha"
        text = _page_text(app)
        assert text.startswith(f"# {PLAIN.project} Alpha Works\n\n## {PLAIN.card} Cards")
        assert ALPHA_GOAL in text
        assert BETA_GOAL not in text
        assert _line_with(text, "Repository:") == f"- **Repository:** {ALPHA_REPO}"
        assert _line_with(text, "Default branch:") == "- **Default branch:** trunk"
        assert _line_with(text, "Priority:") == "- **Priority:** 2"
        assert _line_with(text, "Now:") == "**Now:** no wake holds the project."
        for row in (
            "| ? decision needed | 1 |",
            "| ◐ in progress | 2 |",
            "| ○ todo | 3 |",
            "| · backlog | 0 |",
            "| ✓ done | 1 |",
            "| ✗ cancelled | 0 |",
        ):
            assert row in text.split(f"## {PLAIN.card} Cards", 1)[1]
        # both of alpha's wakes, newest first, with when each started and ended
        assert text.index("alpha's newer wake") < text.index("alpha's older wake")
        assert "beta's wake" not in text
        assert (
            f"### {PLAIN.wake} 20260926-090000-bbbb\n\n"
            "Started 2026-09-26 09:00, ended 2026-09-26 09:45.\n\n"
            "alpha's newer wake"
        ) in text
        assert asked == [ALPHA_REPO]


async def test_open_pull_requests_show_with_their_card_ids(board, monkeypatch):
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await _page_from_list(pilot, "alpha")
        text = _page_text(app)
        assert PULLS_LOADING not in text
        assert "alp-a2" in _line_with(text, MATCHED_PR)
        assert "Add the red widget" in _line_with(text, MATCHED_PR)
        assert "no card" in _line_with(text, OTHER_PR)


async def test_a_later_board_change_redraws_without_asking_gh_again(board, monkeypatch):
    """The page also watches the board folder (sys-m9); once the pull-request list has
    loaded, a later change on the board (here, a new card) redraws with that change but
    keeps the already-loaded list rather than asking gh a second time."""
    asked = _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await _page_from_list(pilot, "alpha")
        text = _page_text(app)
        assert "| ○ todo | 3 |" in text
        assert asked == [ALPHA_REPO]

        commands.card_new(
            board,
            card_id="alp-a8",
            project="alpha",
            title="a later card",
            goal="g",
            test="t",
            stack="todo",
        )
        await pilot.pause(3 * POLL_INTERVAL + 0.1)

        text = _page_text(app)
        assert "| ○ todo | 4 |" in text  # the board change is redrawn
        assert "alp-a2" in _line_with(text, MATCHED_PR)  # the loaded list survived
        assert asked == [ALPHA_REPO]  # gh was not asked again


async def test_a_failing_reader_shows_its_reason(board, monkeypatch):
    def failing(_repo: str) -> list[dict[str, str]]:
        raise GitHubError(GH_REASON)

    _fake_reader(monkeypatch, failing)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await _page_from_list(pilot, "alpha")
        text = _page_text(app)
        assert GH_REASON in _line_with(text, "Could not read them")
        assert PULLS_LOADING not in text
        assert ALPHA_GOAL in text  # the rest of the page still shows


async def test_the_page_answers_keys_while_the_pull_requests_load(board, monkeypatch):
    release = threading.Event()

    def slow(repo: str) -> list[dict[str, str]]:
        assert release.wait(timeout=10), "the test never released the reader"
        return _two_pulls(repo)

    _fake_reader(monkeypatch, slow)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        table = app.query_one(DataTable)
        table.move_cursor(row=table.get_row_index("alpha"))
        await pilot.press("i")
        await pilot.pause()
        try:
            # the reader is still waiting: everything else shows, and a loading line
            text = _page_text(app)
            assert ALPHA_GOAL in text
            assert "alpha's newer wake" in text
            assert PULLS_LOADING in text
            assert MATCHED_PR not in text
            theme = app.theme
            await pilot.press("t")  # the screen still answers keys
            assert app.theme != theme
        finally:
            release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = _page_text(app)
        assert PULLS_LOADING not in text
        assert "alp-a2" in _line_with(text, MATCHED_PR)


async def test_i_on_a_projects_board_opens_the_same_page(board, monkeypatch):
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        from_list = await _page_from_list(pilot, "alpha")
        list_text = _page_text(app)
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("enter")  # the cursor is still on alpha
        await pilot.pause()
        board_screen = app.screen
        assert isinstance(board_screen, BoardScreen)
        await pilot.press("i")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)
        assert app.screen is not from_list
        assert app.screen.slug == "alpha"
        assert _page_text(app) == list_text

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is board_screen


async def test_escape_returns_to_the_project_list(board, monkeypatch):
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        project_list = app.screen
        await _page_from_list(pilot, "alpha")
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is project_list
        assert isinstance(app.focused, DataTable)
        table = app.query_one(DataTable)
        assert table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value == (
            "alpha"
        )


async def test_the_footer_names_i_on_both_screens(board, monkeypatch):
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.pause()

        def legend() -> dict[str, str]:
            footer = app.screen.query_one(Footer)
            return {key.key: key.description for key in footer.query(FooterKey)}

        assert legend()["i"] == "Info"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, BoardScreen)
        assert legend()["i"] == "Info"


async def test_the_page_writes_nothing(board, monkeypatch):
    _fake_reader(monkeypatch, _two_pulls)

    def snapshot() -> dict[Path, bytes]:
        return {p: p.read_bytes() for p in sorted(board.rglob("*")) if p.is_file()}

    before = snapshot()
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await _page_from_list(pilot, "alpha")
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("enter", "i")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)
    assert snapshot() == before


# ------------------------------------------------------------ sys-w117 --
# The page in the look of 2026-09-30.


def _header_line(screen: ProjectScreen, line: str) -> str:
    return str(screen.query_one(AppHeader).query_one(line, Static).visual)


async def test_the_header_shows_the_projects_name_status_pill_and_description(
    board, monkeypatch
):
    """The screen's title is the project's name alone (the slug is in the status line),
    and the status line and the description are the ones the project's board shows: the
    two functions of `autonomouse.tui.project_header`."""
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        screen = await _page_from_list(pilot, "alpha")
        assert _header_line(screen, "#header-title") == "Alpha Works"

        # alpha has cards to work and no wake: waiting; its two in_progress cards have
        # no wake to work them; its decision card waits on the human
        status = _header_line(screen, "#header-status")
        waiting = project_status_pill(ProjectStatus.WAITING, icons=PLAIN).plain
        assert status.startswith(f"{waiting} · alpha | cards {PLAIN.stopped} 2 stopped")
        assert " | waiting on you 1 | last change " in status
        # the same line as the function gives, up to the elapsed time (the minute may
        # have moved on between the two reads)
        line = status_line(board, "alpha", icons=PLAIN).plain
        assert status.rsplit(" | last change ", 1)[0] == line.rsplit(" | last change ", 1)[0]

        assert _header_line(screen, "#header-description") == ALPHA_GOAL
        assert _header_line(screen, "#header-description") == description(board, "alpha")


async def test_the_header_follows_the_board_when_only_the_header_shows_the_change(
    board, monkeypatch
):
    """The PAUSE file is on no line of the page's text, only in the header's status
    pill: the header reads again when the board's files change, not once a minute."""
    _fake_reader(monkeypatch, _two_pulls)
    paused = project_status_pill(ProjectStatus.PAUSED, icons=PLAIN).plain
    waiting = project_status_pill(ProjectStatus.WAITING, icons=PLAIN).plain
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        screen = await _page_from_list(pilot, "alpha")

        def status() -> str:
            return _header_line(screen, "#header-status")

        assert status().startswith(waiting)
        pause_path().touch()  # in the temporary config folder
        await pause_until(pilot, status, lambda line: line.startswith(paused))
        pause_path().unlink()
        await pause_until(pilot, status, lambda line: line.startswith(waiting))


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
async def test_the_reading_column_has_the_frame_in_both_themes(
    board, monkeypatch, theme
):
    """The column is in the viewer's heavy frame, in the focus colour while it holds
    focus (for as long as the page is open) and in the resting colour when it does not."""
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        app.theme = theme
        screen = await _page_from_list(pilot, "alpha")
        column = screen.query_one("#project-details", ReadingView)

        def frame() -> set[tuple[str, Color]]:
            return {
                getattr(column.styles, f"border_{edge}")
                for edge in ("top", "right", "bottom", "left")
            }

        def colour(variable: str) -> Color:
            return Color.parse(app.theme_variables[variable.removeprefix("$")])

        assert screen.focused is column
        assert frame() == {(FRAME.border, colour(FRAME.focus))}
        screen.set_focus(None)
        await pause_until(pilot, frame, lambda f: f == {(FRAME.border, colour(FRAME.colour))})


@pytest.mark.parametrize("nerd_font", [False, True])
async def test_each_stack_count_row_holds_its_pill(board, monkeypatch, nerd_font):
    """The count table's first cell of each row is that stack's pill, in the board's
    order, with the count beside it. The Markdown keeps the plain words, which a reader
    can copy."""
    _fake_reader(monkeypatch, _two_pulls)
    icons = NERD if nerd_font else PLAIN
    app = AutonomouseApp(nerd_font=nerd_font)
    async with app.run_test(size=(120, 50)) as pilot:
        screen = await _page_from_list(pilot, "alpha")
        table = screen.query_one(ReadingView).document.query("MarkdownTableContent").first()
        cells = {cell.name: cell for cell in table.query(".cell")}
        counts = {
            Stack.DECISION_NEEDED: 1,
            Stack.TODO: 3,
            Stack.IN_PROGRESS: 2,
            Stack.DONE: 1,
            Stack.BACKLOG: 0,
            Stack.CANCELLED: 0,
        }
        for row, stack in enumerate(STACK_ORDER, 1):
            assert cells[f"cell{row}.1"].content == stack_pill(stack, icons=icons)
            assert str(cells[f"cell{row}.2"].content) == str(counts[stack])
        assert len(cells) == 2 * len(STACK_ORDER)
        assert f"| {icons.stack[Stack.IN_PROGRESS]} in progress | 2 |" in _page_text(app)


async def test_the_footer_names_esc_and_shows_the_slug_and_the_version(
    board, monkeypatch
):
    """The page's footer is the framed one: its keys, then `<slug>   autonomouse
    v<version>` at the right. A click on its `esc` goes back to the project list. (`i`
    is a key of the two screens that open the page, which the test above checks.)"""
    _fake_reader(monkeypatch, _two_pulls)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 50)) as pilot:
        project_list = app.screen
        screen = await _page_from_list(pilot, "alpha")
        footer = screen.query_one(AppFooter)
        keys = await legend_shows(pilot, lambda keys: "escape" in keys)
        assert {"escape", "q", "t"} <= keys
        hints = {key.key: key for key in footer.query(FooterKey)}
        assert hints["escape"].description == "Back"

        about = footer.query_one(".footer-about", Static)
        assert str(about.visual) == footer_label("alpha")
        assert str(about.visual) == f"alpha   autonomouse v{autonomouse.__version__}"
        assert about.region.right == footer.content_region.right

        assert await pilot.click(hints["escape"])
        await pause_until(pilot, lambda: app.screen, lambda s: s is project_list)


async def test_a_click_on_the_page_leaves_it_as_it_was(board, monkeypatch):
    """Rule 11: a click on the header, on a stack's pill, on a heading, on the column's
    frame and on the footer's label opens nothing and changes nothing."""
    _fake_reader(monkeypatch, _two_pulls)
    opened: list[str] = []
    app = AutonomouseApp()
    monkeypatch.setattr(app, "open_url", lambda url, **_kw: opened.append(url))
    async with app.run_test(size=(120, 50)) as pilot:
        screen = await _page_from_list(pilot, "alpha")
        source = _page_text(app)
        column = screen.query_one("#project-details", ReadingView)
        pill = next(iter(column.document.query("MarkdownTableCellContents").filter(".row1")))
        heading = column.document.query("MarkdownH1").first()
        targets = [
            (screen.query_one(AppHeader), (1, 1)),
            (screen.query_one("#header-status", Static), (2, 0)),
            (pill, (2, 0)),
            (heading, (1, 0)),
            (column, (0, 0)),  # the frame's own corner
            (screen.query_one(".footer-about", Static), (1, 0)),
        ]
        for widget, offset in targets:
            assert await pilot.click(widget, offset=offset)
            await pilot.pause()
            assert app.is_running
            assert app.screen is screen
            assert column.source == source
        assert opened == []
        assert pill.content == stack_pill(STACK_ORDER[0], icons=PLAIN)
