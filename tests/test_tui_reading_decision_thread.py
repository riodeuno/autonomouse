"""The decision screen and the planning threads read as documents (sys-v13).

The human's goal (2026-09-28): the text reads like a document, "a centred column of
comfortable width, headings that stand out from the body, and clear boundaries between
sections, lists and tables", on every reading screen. Here that is the screen `a` opens
on a decision, and the pane under the list `p` opens: both show their text in the shared
reading view (`autonomouse.tui.reading.ReadingView`), and each message of a thread is its
own block under a heading that names who wrote it and when.

Each test drives the app with a Textual pilot on a temporary board (`AUTONOMOUSE_HOME`),
with notifications kept off this machine's real config. Textual keeps the block classes
(`MarkdownH2`, `MarkdownBulletList` and the rest) private, so the tests find them by
their type names.
"""

from __future__ import annotations

import json

import pytest
from textual.color import Color
from textual.containers import Vertical
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.markdown import MarkdownBlock

from autonomouse.board import commands as board_commands
from autonomouse.board.plans import thread_open, thread_path, thread_say
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.decision import AnswerModal
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.look import FRAME, QUESTION_COLOUR, question_pill
from autonomouse.tui.plan import ThreadsScreen
from autonomouse.tui.reading import ReadingView
from test_tui_look import resolve

# a brief with a list, and a placeholder the parser would read as an HTML tag
BRIEF = """\
What this card is for: the board gets a colour picker.

What is stuck: the two plans differ in what you see.

- Plan a keeps eight fixed colours.
- Plan b takes any colour you type, with `card colour <id>`.

Run autonomouse card show <id> to see both."""

PLANNER = """\
Two questions before I plan anything:

1. Which screens should the dark mode cover?
2. Where does the toggle live?

Answer with autonomouse plan reply <thread-id> "<your answer>"."""


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
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
    return tmp_path


@pytest.fixture
def decision(board):
    """sys-a1, waiting on a decision whose brief has a list."""
    board_commands.card_new(
        board, card_id="sys-a1", project="system", title="a card", goal="g", test="t",
        position=0,
    )
    # test setup only: `card_ask` asks from in_progress, which a full run would reach
    path = board / "projects" / "system" / "cards" / "sys-a1.json"
    data = json.loads(path.read_text())
    data["stack"] = "in_progress"
    path.write_text(json.dumps(data))
    board_commands.card_ask(
        board,
        "sys-a1",
        brief=BRIEF,
        question="Which plan should the colour picker use?",
        options=["plan a", "plan b"],
        recommendation="Plan a, eight fixed colours.",
        project="system",
    )
    return board


@pytest.fixture
def thread(board):
    """A thread with two messages: the human's request, then the planner's question."""
    opened = thread_open(board, "system", "add a dark mode, with a *toggle*")
    thread_say(board, opened.id, PLANNER)
    # test setup only: fixed times, so each heading's time is known
    path = thread_path(board, opened.id)
    data = json.loads(path.read_text())
    for message, at in zip(
        data["messages"], ("2026-09-28T08:10:05Z", "2026-09-28T08:17:40Z"), strict=True
    ):
        message["at"] = at
    path.write_text(json.dumps(data))
    return opened.id


async def _open_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(pilot.app.screen, BoardScreen)
    return pilot.app.screen


async def _drawn(pilot, view: ReadingView) -> None:
    """Wait until the view's blocks for its current text are on screen."""
    for _ in range(50):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.05)
        if view.document.query(MarkdownBlock) and view.source:
            await pilot.pause()
            return
    raise AssertionError("the reading view drew no blocks")


def _text(widget) -> str:
    return str(widget.content)


async def test_the_decision_screen_shows_brief_question_and_recommendation_as_a_document(
    decision,
):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_board(pilot)  # sys-a1 is the only card, and has the focus
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AnswerModal)
        view = app.screen.query_one(ReadingView)
        await _drawn(pilot, view)
        document = view.document

        assert [_text(h) for h in document.query("MarkdownH1")] == [
            f"{PLAIN.waiting} sys-a1 needs your decision"
        ]
        # the question first, then the brief, as on Telegram (rules, item 8; sys-w105)
        assert [_text(h) for h in document.query("MarkdownH2")] == [
            "Question",
            f"{PLAIN.brief} Brief",
            f"{PLAIN.recommendation} Recommendation",
        ]
        paragraphs = [_text(p) for p in document.query("MarkdownParagraph")]
        assert "Which plan should the colour picker use?" in paragraphs
        assert "Plan a, eight fixed colours." in paragraphs
        # the brief's list is a list block, one item per line, and the placeholders
        # in it show as written, inside code and outside it
        [brief_list] = document.query("MarkdownBulletList")
        assert [_text(p) for p in brief_list.query("MarkdownParagraph")] == [
            "Plan a keeps eight fixed colours.",
            "Plan b takes any colour you type, with card colour <id>.",
        ]
        assert "Run autonomouse card show <id> to see both." in paragraphs
        # the column is the reading view's own, and the options still sit below it
        assert view.document.region.width <= 88
        assert app.screen.query_one("#options").region.y >= view.region.bottom


async def test_each_message_of_a_thread_is_its_own_block_under_a_heading(thread):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_board(pilot)
        await pilot.press("p")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ThreadsScreen)
        pane = screen.query_one("#message-pane", ReadingView)
        await _drawn(pilot, pane)
        document = pane.document

        # one heading per message, naming who wrote it and when, oldest first
        assert [_text(h) for h in document.query("MarkdownH2")] == [
            "You, 2026-09-28 08:10",
            "The planner, 2026-09-28 08:17",
        ]
        # the human's text shows as typed; the planner's Markdown list is a list
        # block, and its placeholders show as written
        paragraphs = [_text(p) for p in document.query("MarkdownParagraph")]
        assert "add a dark mode, with a *toggle*" in paragraphs
        [questions] = document.query("MarkdownOrderedList")
        assert [_text(p) for p in questions.query("MarkdownParagraph")] == [
            "Which screens should the dark mode cover?",
            "Where does the toggle live?",
        ]
        assert (
            'Answer with autonomouse plan reply <thread-id> "<your answer>".' in paragraphs
        )


async def test_the_pane_opens_on_the_newest_messages_own_heading(board):
    """A planner's message may hold headings of its own; the pane still opens with the
    heading that names the newest message's author and time at its top."""
    opened = thread_open(board, "system", "plan the dark mode")
    steps = "\n".join(f"{n}. step {n}" for n in range(1, 31))
    thread_say(board, opened.id, f"Here is my plan.\n\n## Next steps\n\n{steps}")
    app = AutonomouseApp()
    async with app.run_test(size=(140, 45)) as pilot:
        await _open_board(pilot)
        await pilot.press("p")
        await pilot.pause()
        pane = app.screen.query_one("#message-pane", ReadingView)
        await _drawn(pilot, pane)
        headings = list(pane.document.query("MarkdownH2"))
        assert [_text(h).split(",")[0] for h in headings] == [
            "You",
            "The planner",
            "Next steps",
        ]
        planner = headings[1]
        assert planner.region.y == pane.content_region.y + planner.styles.margin.top
        assert pane.scroll_y > 0


# ------------------------------------------------------------ sys-w115 --


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
@pytest.mark.parametrize("nerd_font", [False, True])
async def test_the_decision_screen_is_framed_with_its_pill_and_heading_icons(
    decision, theme, nerd_font
):
    """The decision screen sits in the frame every panel has, opens with the question's
    filled `OPEN` pill above the document, and its Brief and Recommendation headings
    carry their icons, in both themes and both icon forms. A click on an answer
    option chooses it, and the pill turns to `ANSWERED`."""
    icons = NERD if nerd_font else PLAIN
    app = AutonomouseApp(nerd_font=nerd_font)
    async with app.run_test(size=(140, 45)) as pilot:
        app.theme = theme
        await _open_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, AnswerModal)
        view = modal.query_one(ReadingView)
        await _drawn(pilot, view)

        kind, colour = modal.query_one(Vertical).styles.border_top
        assert (kind, colour) == ("heavy", resolve(theme, FRAME.focus))

        headings = [_text(h) for h in view.document.query("MarkdownH2")]
        assert headings == [
            "Question",
            f"{icons.brief} Brief",
            f"{icons.recommendation} Recommendation",
        ]

        state = modal.query_one("#state", Static)
        assert state.region.bottom <= view.region.y  # above the document
        cells = [(c, seg.style) for seg in state.render_line(0) for c in seg.text]
        text = "".join(c for c, _ in cells)
        assert text.startswith(question_pill(answered=False, icons=icons).plain)
        start = text.index("OPEN")
        fill = resolve(theme, QUESTION_COLOUR[False])
        for _char, style in cells[start : start + len("OPEN")]:
            assert Color.from_rich_color(style.bgcolor) == fill

        options = modal.query_one("#options", OptionList)
        assert await pilot.click(options, offset=(4, 1))  # "plan a", the first option
        chosen = modal.query_one("#chosen", Static)
        for _ in range(50):
            if chosen.display:
                break
            await pilot.pause(0.05)
        assert "Answer: plan a" in str(chosen.content)
        assert str(state.content) == question_pill(answered=True, icons=icons).plain
