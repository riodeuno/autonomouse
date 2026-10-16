"""The card details pane reads as a document (card sys-v12).

The human's goal (2026-09-28): the text reads like a document, "a centred column of
comfortable width, headings that stand out from the body, and clear boundaries between
sections, lists and tables", on every reading screen. On the board's details pane,
each field is a section with its own heading, under the group it belongs to (Progress,
Goal, History; sys-w105), and Markdown inside a field renders as Markdown. Each test drives the app with a Textual pilot on a temporary board
(`AUTONOMOUSE_HOME`). Textual keeps the block classes (`MarkdownH2`,
`MarkdownBulletList` and the rest) private, so the tests find them by their type names.
"""

from __future__ import annotations

import json

import pytest
from textual.color import Color
from textual.widgets import DataTable
from textual.widgets.markdown import MarkdownBlock

from autonomouse.board import commands as board_commands
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.card_detail import CardDetail, CardLine
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.look import DECISION, QUESTION_COLOUR, question_pill
from autonomouse.tui.reading import COLUMN_WIDTH
from test_tui_look import resolve

DOCS = "https://example.com/widget-docs"
GOAL = f"""\
The board shows each card's details as a document.

- first point of the goal
- second point of the goal

See [the docs]({DOCS}) for more."""
LAST_STEP = "Ran `card progress` as card progress <id> --run-id <run id>."


def _patch_card(root, card_id: str, **fields) -> None:
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "notify-config"))
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
        tmp_path,
        card_id="sys-a1",
        project="system",
        title="a card",
        goal=GOAL,
        test="A Pilot test opens the pane.",
        limits=["docs/rules.md applies", "keep it **small**"],
        position=0,
        stack="todo",
    )
    _patch_card(
        tmp_path,
        "sys-a1",
        branch="card/sys-a1",
        pr="https://github.com/test/system/pull/7",
        handoff={
            "last_step": LAST_STEP,
            "next_step": "open the PR",
            "updated_at": None,
            "run_id": None,
        },
    )
    return tmp_path


@pytest.fixture
def decision_board(board):
    board_commands.card_new(
        board,
        card_id="sys-b1",
        project="system",
        title="a card with a question",
        goal="g",
        test="t",
        position=0,
    )
    _patch_card(board, "sys-b1", stack="in_progress")
    board_commands.card_ask(
        board,
        "sys-b1",
        brief="The card is stuck.\n\n- it has *two* plans\n- neither is chosen",
        question="which plan?",
        options=["plan a", "plan b"],
        recommendation="plan a",
        project="system",
    )
    return board


async def _open_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, BoardScreen)
    return screen


async def _pane_showing(pilot, card_id: str) -> CardDetail:
    """Focus `card_id` on the board and wait until the pane has drawn its details: its
    first line names the card, and the headings on screen are the ones its text has
    (not the ones of the card shown before)."""
    screen = await _open_board(pilot)
    item = next(i for i in screen.query(BoardItem) if i.card and i.card.id == card_id)
    item.focus()
    pane = screen.query_one(CardDetail)
    icons = pilot.app.icons
    for _ in range(100):
        await pilot.pause(0.05)
        card_line = pane.query_one(CardLine)
        line = "".join(r.plain for r in card_line.rows(card_line.size.width)[:1])
        drawn = [_text(h) for h in pane.document.query("MarkdownH2, MarkdownH3")]
        written = [
            text.split(" ", 1)[1]
            for text in pane.source.splitlines()
            if text.startswith(("## ", "### "))
        ]
        if line.startswith(f"{icons.card} {card_id} ") and drawn and drawn == written:
            await pilot.pause()
            return pane
    raise AssertionError(f"the pane never drew {card_id}")


def _text(widget) -> str:
    return str(widget.content)


async def test_the_pane_shows_one_heading_per_field_and_a_list_as_a_list(board):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 60)) as pilot:
        pane = await _pane_showing(pilot, "sys-a1")
        document = pane.document

        # the card's id and title are the pane's first line (sys-w115), not a heading
        assert not document.query("MarkdownH1")
        assert [_text(h) for h in document.query("MarkdownH2")] == [
            f"{PLAIN.progress} Progress",
            f"{PLAIN.goal} Goal",
            f"{PLAIN.history} History",
        ]
        assert [_text(h) for h in document.query("MarkdownH3")] == [
            f"{PLAIN.step} Last step",
            f"{PLAIN.step} Next step",
            f"{PLAIN.limits} Limits",
            f"{PLAIN.test} Test",
        ]

        # the goal's list is a list block, one item per line; the limit's bold is bold
        lists = list(document.query("MarkdownBulletList"))
        items = [[_text(p) for p in lst.query("MarkdownParagraph")] for lst in lists]
        assert ["first point of the goal", "second point of the goal"] in items
        assert ["keep it small"] in items
        assert "docs/rules.md applies" not in pane.source

        # text in angle brackets is shown as written, not dropped as HTML
        paragraphs = [_text(p) for p in document.query("MarkdownParagraph")]
        assert (
            "Ran card progress as card progress <id> --run-id <run id>." in paragraphs
        )


async def test_a_decision_cards_pane_shows_its_decision_as_sections(decision_board):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 60)) as pilot:
        pane = await _pane_showing(pilot, "sys-b1")
        document = pane.document

        assert [_text(h) for h in document.query("MarkdownH2")] == [
            f"{PLAIN.waiting} Your decision"
        ]
        assert [_text(h) for h in document.query("MarkdownH3")] == [
            "Question",
            f"{PLAIN.brief} Brief",
            "Options",
            f"{PLAIN.recommendation} Recommendation",
            "Answer",
        ]
        (brief_list,) = document.query("MarkdownBulletList")
        assert [_text(p) for p in brief_list.query("MarkdownParagraph")] == [
            "it has two plans",
            "neither is chosen",
        ]
        (options,) = document.query("MarkdownOrderedList")
        assert [_text(p) for p in options.query("MarkdownParagraph")] == [
            "plan a",
            "plan b",
            "other: tell me",
        ]


@pytest.mark.parametrize(("width", "centred"), [(140, True), (80, False)])
async def test_the_panes_column_is_centred_when_wide_and_full_width_when_narrow(
    board, width, centred
):
    app = AutonomouseApp()
    async with app.run_test(size=(width, 50)) as pilot:
        pane = await _pane_showing(pilot, "sys-a1")
        column = pane.document.region
        space = pane.scrollable_content_region  # without the scrollbar
        if centred:
            assert column.width == COLUMN_WIDTH
            left = column.x - space.x
            right = space.right - column.right
            assert abs(left - right) <= 1, (left, right)
        else:
            assert column.width == space.width
            assert column.x == space.x


async def test_clicking_the_pane_opens_nothing_and_changes_nothing(board, monkeypatch):
    """Rule 11: a screen's test clicks what a mouse can click. A link in a field and a
    section heading each take a click; neither opens a browser (over ssh it would
    open on the server), and the pane keeps showing the same card."""
    opened: list[str] = []
    app = AutonomouseApp()
    monkeypatch.setattr(app, "open_url", lambda url, **_kw: opened.append(url))
    async with app.run_test(size=(140, 60)) as pilot:
        pane = await _pane_showing(pilot, "sys-a1")
        source = pane.source
        link = next(
            p
            for p in pane.document.query("MarkdownParagraph")
            if "the docs" in _text(p)
        )
        heading = next(
            h
            for h in pane.document.query("MarkdownH2")
            if _text(h) == f"{PLAIN.history} History"
        )
        for widget, offset in ((link, (5, 0)), (heading, (1, 0))):
            widget.scroll_visible(animate=False, immediate=True)
            await pilot.pause()
            assert await pilot.click(widget, offset=offset)
            await pilot.pause()
            assert app.is_running
            assert isinstance(app.screen, BoardScreen)
            assert pane.card.id == "sys-a1"
            assert pane.source == source
            assert pane.document.query(MarkdownBlock)
        assert opened == []


# ------------------------------------------------------------ sys-w115 --


@pytest.fixture
def worked_board(board):
    """sys-p1: an in_progress card with every heading and fact the pane gives an icon
    to, and two questions, one answered and one still open."""
    board_commands.card_new(
        board,
        card_id="sys-p1",
        project="system",
        title="a worked card",
        goal="g",
        test="the test",
        limits=["docs/rules.md applies", "stay small"],
        position=0,
    )
    question = {
        "options": ["a", "b"],
        "recommendation": "a",
        "asked_at": "2026-09-22T07:00:00Z",
        "brief": "a brief",
    }
    _patch_card(
        board,
        "sys-p1",
        stack="in_progress",
        phase="writing",
        iteration=1,
        assigned={
            "model": "opus",
            "effort": "high",
            "agent_type": "worker-high",
            "session_id": "s1",
            "pid": 1,
            "picked_up_at": "2026-09-22T06:00:00Z",
        },
        handoff={
            "last_step": "wrote it",
            "next_step": "test it",
            "updated_at": None,
            "run_id": None,
        },
        attachments=["docs/shot.png"],
        decisions=[
            {"id": "d1", "question": "which colour?", "answer": "b",
             "answered_at": "2026-09-22T07:05:00Z", **question},
            {"id": "d2", "question": "which size?", "answer": None,
             "answered_at": None, **question},
        ],
    )
    return board


@pytest.mark.parametrize("nerd_font", [False, True])
async def test_each_heading_and_fact_carries_its_icon_in_the_pane(worked_board, nerd_font):
    icons = NERD if nerd_font else PLAIN
    app = AutonomouseApp(nerd_font=nerd_font)
    async with app.run_test(size=(140, 60)) as pilot:
        pane = await _pane_showing(pilot, "sys-p1")
        document = pane.document
        h3 = [_text(h) for h in document.query("MarkdownH3")]
        for heading in (
            f"{icons.step} Last step",
            f"{icons.step} Next step",
            f"{icons.attachment} Attachments",
            f"{icons.limits} Limits",
            f"{icons.test} Test",
        ):
            assert heading in h3, (heading, h3)
        paragraphs = [_text(p) for p in document.query("MarkdownParagraph")]
        for fact in (
            f"{icons.model} Model: opus",
            f"{icons.iteration} Iteration: 1",
            f"{icons.elapsed} Picked up: ",
        ):
            assert any(p.startswith(fact) for p in paragraphs), (fact, paragraphs)


def _cells(widget) -> list[tuple[str, object]]:
    return [(c, seg.style) for seg in widget.render_line(0) for c in seg.text]


@pytest.mark.parametrize("nerd_font", [False, True])
async def test_each_question_in_the_pane_opens_with_its_filled_pill(
    worked_board, nerd_font
):
    """The questions group draws each question's `OPEN` or `ANSWERED` pill filled with
    its colour, as the pill every screen shows (`autonomouse.tui.look`)."""
    icons = NERD if nerd_font else PLAIN
    app = AutonomouseApp(nerd_font=nerd_font)
    async with app.run_test(size=(140, 60)) as pilot:
        pane = await _pane_showing(pilot, "sys-p1")
        pills = {}
        for paragraph in pane.document.query("MarkdownParagraph"):
            for answered in (False, True):
                if _text(paragraph) == question_pill(answered=answered, icons=icons).plain:
                    pills[answered] = paragraph
        assert set(pills) == {False, True}
        # the answered question is listed first, as on the card
        assert pills[True].region.y < pills[False].region.y
        left, _right = icons.pill_ends
        for answered, paragraph in pills.items():
            fill = resolve(app.theme, QUESTION_COLOUR[answered])
            word = "ANSWERED" if answered else "OPEN"
            cells = _cells(paragraph)
            text = "".join(c for c, _ in cells)
            assert text.startswith(f"{left} ") and word in text
            start = text.index(word)
            for _char, style in cells[start : start + len(word)]:
                assert Color.from_rich_color(style.bgcolor) == fill, word
        assert QUESTION_COLOUR[False] == DECISION
