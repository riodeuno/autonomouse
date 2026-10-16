"""The reading view (`autonomouse.tui.reading`): the project page reads as a document.

The human's goal (2026-09-28): the text reads like a document, "a centred column of
comfortable width, headings that stand out from the body, and clear boundaries between
sections, lists and tables". Each test drives the app with a Textual pilot on a
temporary board (`AUTONOMOUSE_HOME`), and the page's pull-request reader is a fake, so
no test calls gh. Textual keeps the block classes (`MarkdownH2`, `MarkdownTable` and the
rest) private, so the tests find them by their type names.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DataTable
from textual.widgets.markdown import MarkdownBlock

from autonomouse.board import commands
from autonomouse.board.brief import brief_path
from autonomouse.board.schema import Stack
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.look import stack_pill
from autonomouse.tui.project import ProjectScreen
from autonomouse.tui.reading import COLUMN_WIDTH, ReadingView, plain

PULL = "https://github.com/acme/widgets/pull/7"
DOCS = "https://example.com/widget-docs"

BRIEF = f"""\
## Why this project exists

The team loses a day a week to making widgets by hand.

## Who uses it and where

- The widget team, on their laptops.
- The night shift, over ssh.

## How it works

| Step | What happens |
|---|---|
| Order | One command reads the order file. |
| Build | The factory builds each widget. |

See [the docs]({DOCS}) for the order file.
"""


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    commands.project_new(
        tmp_path,
        slug="alpha",
        name="Alpha Works",
        goal="Alpha builds the widget factory.",
        repo="https://github.com/acme/widgets",
        repo_path="/tmp/widgets",
        default_branch="trunk",
        priority=1,
    )
    brief_path(tmp_path, "alpha").write_text(BRIEF)
    monkeypatch.setattr(
        "autonomouse.tui.project.gh_open_prs",
        lambda _repo: [{"url": PULL, "title": "Add the red widget"}],
    )
    return tmp_path


async def _drawn(pilot, view: ReadingView) -> None:
    """Wait until the view's blocks for its current text are on screen."""
    for _ in range(50):
        await pilot.pause(0.05)
        if view.document.query(MarkdownBlock) and view.source:
            await pilot.pause()
            return
    raise AssertionError("the reading view drew no blocks")


async def _open_page(pilot) -> ReadingView:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("alpha"))
    await pilot.press("i")
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    screen = pilot.app.screen
    assert isinstance(screen, ProjectScreen)
    view = screen.query_one("#project-details", ReadingView)
    await _drawn(pilot, view)
    return view


def _text(widget) -> str:
    return str(widget.content)


async def test_the_brief_and_the_details_render_as_heading_list_and_table_blocks(
    board,
):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 50)) as pilot:
        view = await _open_page(pilot)
        document = view.document

        # the project's name, after the project icon, is the page's one title, and every
        # section is a heading
        titles = [_text(h) for h in document.query("MarkdownH1")]
        assert titles == [f"{PLAIN.project} Alpha Works"]
        sections = [_text(h) for h in document.query("MarkdownH2")]
        # where the project stands first, its goal and settings, then the brief (sys-w105)
        assert sections == [
            f"{PLAIN.card} Cards",
            f"{PLAIN.pr} Open pull requests (1)",
            f"{PLAIN.wake} Wakes",
            f"{PLAIN.goal} Goal",
            f"{PLAIN.settings} Settings",
            "Why this project exists",
            "Who uses it and where",
            "How it works",
        ]

        # the brief's list is a list block, one item per line
        (details_list, brief_list) = document.query("MarkdownBulletList")
        items = [_text(p) for p in brief_list.query("MarkdownParagraph")]
        assert items == [
            "The widget team, on their laptops.",
            "The night shift, over ssh.",
        ]
        # the project's fields are a list too
        fields = [_text(p) for p in details_list.query("MarkdownParagraph")]
        assert "Default branch: trunk" in fields
        assert "Repository: https://github.com/acme/widgets" in fields

        # the brief's table, the count per stack and the pull requests are tables
        tables = [
            t.query_one("MarkdownTableContent") for t in document.query("MarkdownTable")
        ]
        assert len(tables) == 3
        stacks, pulls, brief_table = tables
        assert [str(h) for h in brief_table.headers] == ["Step", "What happens"]
        assert [str(c) for c in brief_table.rows[0]] == [
            "Order",
            "One command reads the order file.",
        ]
        assert [str(h) for h in stacks.headers] == ["Stack", "Cards"]
        # each stack's count row starts with that stack's pill (sys-w117)
        pill = stack_pill(Stack.DECISION_NEEDED, icons=PLAIN).plain
        assert [pill, "0"] in [[str(c) for c in r] for r in stacks.rows]
        assert [[str(c) for c in r] for r in pulls.rows] == [
            ["no card", PULL, "Add the red widget"]
        ]


COMMAND = "card progress <id> --run-id <run id>"


async def test_text_in_angle_brackets_on_the_project_page_shows_as_written(board):
    """Agents write placeholders such as `<id>` in board text. The page shows them as
    written, in a paragraph, in a list and in code, rather than reading them as HTML
    tags and dropping the words."""
    brief_path(board, "alpha").write_text(
        f"## How to report\n\nRun {COMMAND} after each step.\n\n- {COMMAND}\n\n"
        f"Or copy `{COMMAND}`.\n\n```\n{COMMAND}\n```\n"
    )
    app = AutonomouseApp()
    async with app.run_test(size=(140, 50)) as pilot:
        view = await _open_page(pilot)
        paragraphs = [_text(p) for p in view.document.query("MarkdownParagraph")]
        assert f"Run {COMMAND} after each step." in paragraphs
        assert COMMAND in paragraphs  # the list item
        assert f"Or copy {COMMAND}." in paragraphs
        [fence] = view.document.query("MarkdownFence")
        assert fence.code.strip() == COMMAND


@pytest.mark.parametrize(("width", "centred"), [(140, True), (80, False)])
async def test_the_column_is_centred_when_wide_and_full_width_when_narrow(
    board, width, centred
):
    app = AutonomouseApp()
    async with app.run_test(size=(width, 50)) as pilot:
        view = await _open_page(pilot)
        column = view.document.region
        space = view.scrollable_content_region  # without the scrollbar
        if centred:
            assert column.width == COLUMN_WIDTH
            left = column.x - space.x
            right = space.right - column.right
            assert abs(left - right) <= 1, (left, right)
        else:
            assert column.width == space.width
            assert column.x == space.x


async def test_clicking_the_page_opens_nothing_and_changes_nothing(board, monkeypatch):
    """Rule 11: a screen's test clicks what a mouse can click. A link, a table cell and a
    heading each take a click; none opens a browser (over ssh it would open on the
    server), and the page stays as it was."""
    opened: list[str] = []
    app = AutonomouseApp()
    monkeypatch.setattr(app, "open_url", lambda url, **_kw: opened.append(url))
    async with app.run_test(size=(140, 50)) as pilot:
        view = await _open_page(pilot)
        source = view.source
        link = next(
            p
            for p in view.document.query("MarkdownParagraph")
            if "the docs" in _text(p)
        )
        cell = next(
            c
            for c in view.document.query("MarkdownTableCellContents")
            if str(c.content) == PULL
        )
        heading = next(
            h for h in view.document.query("MarkdownHeader") if _text(h) == f"{PLAIN.card} Cards"
        )
        for widget, offset in ((link, (5, 0)), (cell, (2, 0)), (heading, (1, 0))):
            widget.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            assert await pilot.click(widget, offset=offset)
            await pilot.pause()
            assert app.is_running
            assert isinstance(app.screen, ProjectScreen)
            assert view.source == source
        assert opened == []


class _Plain(App):
    def __init__(self, markdown: str) -> None:
        super().__init__()
        self.markdown = markdown

    def compose(self) -> ComposeResult:
        yield ReadingView(self.markdown)


async def test_plain_board_text_shows_as_written_never_as_formatting():
    written = [
        "# not a heading",
        "- not a list item",
        "1. not a numbered item",
        "> not a quote",
        "| not | a table |",
        "*not emphasis* and `not code` and [not](a link) and a_b_c & <b>",
        "    not a code block",
    ]
    app = _Plain("\n\n".join(plain(line) for line in written))
    async with app.run_test(size=(120, 40)) as pilot:
        view = app.query_one(ReadingView)
        await _drawn(pilot, view)
        blocks = list(view.document.query(MarkdownBlock))
        assert [type(b).__name__ for b in blocks] == ["MarkdownParagraph"] * len(
            written
        )
        assert [_text(b) for b in blocks] == [line.strip() for line in written]
