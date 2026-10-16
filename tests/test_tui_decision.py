"""Answering and cancelling a `decision_needed` card from the board screen.

Builds its `decision_needed` fixture card through the real `card_ask` command
(not `test_tui_board.py`'s hand-patched-stack shortcut) so it carries a real,
unanswered `Decision` — the only way to exercise both halves of the `a` gate
(right stack, open decision) rather than just the stack half.
"""

import json
import re
from pathlib import Path

import pytest
from textual.containers import Vertical
from textual.widgets import DataTable, Input, OptionList, Static

from autonomouse.board import commands as board_commands
from autonomouse.board.schema import Phase, Stack
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.card_detail import CardDetail
from autonomouse.tui.decision import AnswerModal, CancelModal
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.look import FRAME, question_pill
from autonomouse.tui.reading import ReadingView
from test_tui_look import resolve
from tui_wait import board_settled, focus_lands, legend_shows, pause_until

BRIEF = "This card adds a colour picker. It is stuck on which of two plans to use."


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
    # test setup only: place the card straight into a stack/field a full run
    # (claim, worktree, finish) would otherwise be needed to reach — the same
    # shortcut test_tui_board.py's own `_put_in_stack` takes
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


def _card_data(root, card_id: str) -> dict:
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    return json.loads(path.read_text())


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    # card_ask below sends a real notify; keep it off this machine's real
    # config.toml and real credentials so this test can never make a live send.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "notify-config"))
    _new_project(tmp_path, "system", 1)

    # sys-a1: a real decision_needed card with a real, unanswered Decision —
    # card_new starts it in backlog, a hand-patch (test setup only) gets it to
    # in_progress, then the real card_ask records the Decision and moves it to
    # decision_needed
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
        brief=BRIEF,
        question="which approach?",
        options=["plan a", "plan b"],
        project="system",
    )

    # sys-b1: an ordinary todo card, to prove the keys hide off decision_needed
    board_commands.card_new(
        tmp_path,
        card_id="sys-b1",
        project="system",
        title="b card",
        goal="g",
        test="t",
        position=0,
    )
    _patch_card(tmp_path, "sys-b1", stack="todo")

    return tmp_path


@pytest.fixture
def board_with_other(tmp_path, monkeypatch):
    # a lone decision_needed card with one option, so "other: tell me" (which
    # `card_ask` appends, docs/rules.md item 8) is one "down" away
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
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
        question="which token?",
        options=["plan a"],
        project="system",
    )
    return tmp_path


async def _open_system_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    # the board, once its first focus has landed and the panel shows that card
    return await pause_until(pilot, lambda: pilot.app.screen, board_settled)


async def test_a_opens_modal_with_question_and_options(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)  # sys-a1, decision_needed, gets initial focus
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AnswerModal)
        assert "which approach?" in app.screen.query_one(ReadingView).source
        option_list = app.screen.query_one("#options", OptionList)
        assert option_list.option_count == 3
        assert [
            option_list.get_option_at_index(i).prompt for i in range(3)
        ] == ["plan a", "plan b", "other: tell me"]


async def test_choosing_an_option_asks_where_the_card_goes(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")  # highlight moves from "plan a" to "plan b"
        await pilot.press("enter")
        await pilot.pause()

        # not dismissed yet: the modal now shows the answer and the two landings,
        # each naming its stack, with "more work needed" highlighted
        assert isinstance(app.screen, AnswerModal)
        assert app.screen.query_one("#options", OptionList).display is False
        assert "Answer: plan b" in str(app.screen.query_one("#chosen", Static).content)
        landing = app.screen.query_one("#landing", OptionList)
        assert landing.display is True
        await pause_until(pilot, lambda: landing.has_focus, bool)  # focus lands later
        assert [landing.get_option_at_index(i).prompt for i in range(2)] == [
            f"{PLAIN.stack[Stack.TODO]} more work needed — the card goes to the top of todo",
            f"{PLAIN.stack[Stack.DONE]} the work is already finished — the card goes to done",
        ]
        assert landing.highlighted == 0
        assert _card_data(board, "sys-a1")["stack"] == "decision_needed"


async def test_default_landing_answers_and_sends_the_card_to_top_of_todo(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")  # "plan b"
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # the default: more work needed
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        data = _card_data(board, "sys-a1")
        assert data["stack"] == "todo"
        assert data["position"] == 0
        assert data["decisions"][-1]["answer"] == "plan b"


async def test_finished_landing_answers_and_sends_the_card_to_done(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")  # "plan a"
        await pilot.pause()
        await pilot.press("down")  # the work is already finished
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        data = _card_data(board, "sys-a1")
        assert data["stack"] == "done"
        assert data["decisions"][-1]["answer"] == "plan a"
        assert data["decisions"][-1]["answered_at"] is not None
        last = data["history"][-1]
        assert (last["from"], last["to"], last["note"]) == (
            "decision_needed",
            "done",
            "answered: plan a",
        )


async def test_escape_at_the_landing_step_backs_out_without_writing(board):
    before = _card_data(board, "sys-a1")
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("enter")  # "plan a"
        await pilot.pause()
        assert app.screen.query_one("#landing", OptionList).display is True
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        assert _card_data(board, "sys-a1") == before


async def test_escape_backs_out_without_writing(board):
    before = _card_data(board, "sys-a1")
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.screen, AnswerModal)
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, BoardScreen)
        assert _card_data(board, "sys-a1") == before


async def test_choosing_other_opens_a_text_field(board_with_other):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")  # highlight moves from "plan a" to "other: tell me"
        await pilot.press("enter")
        await pilot.pause()

        # not dismissed — the modal is still open, now showing a text field
        assert isinstance(app.screen, AnswerModal)
        assert app.screen.query_one("#options", OptionList).display is False
        text_field = app.screen.query_one(Input)
        assert text_field.display is True
        await pause_until(pilot, lambda: text_field.has_focus, bool)  # focus lands later


async def test_other_empty_submit_leaves_card_in_decision_needed(board_with_other):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # submit with the text field empty
        await pilot.pause()

        assert isinstance(app.screen, AnswerModal)
        assert _card_data(board_with_other, "sys-a1")["stack"] == "decision_needed"


async def test_other_typed_answer_is_what_the_card_records(board_with_other):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        answer = "useplanc"
        await pilot.press(*answer)
        await pilot.press("enter")
        await pilot.pause()
        assert "Answer: useplanc" in str(app.screen.query_one("#chosen", Static).content)
        await pilot.press("enter")  # the default: more work needed
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        data = _card_data(board_with_other, "sys-a1")
        assert data["stack"] == "todo"
        assert data["decisions"][-1]["answer"] == answer


async def test_a_decision_recorded_without_other_still_takes_a_typed_answer(
    board_with_other,
):
    # a decision written before `card_ask` appended "other: tell me"
    data = _card_data(board_with_other, "sys-a1")
    data["decisions"][-1]["options"] = ["plan a"]
    _patch_card(board_with_other, "sys-a1", decisions=data["decisions"])

    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        option_list = app.screen.query_one("#options", OptionList)
        assert option_list.get_option_at_index(1).prompt == "other: tell me"
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press(*"planc")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # the default: more work needed
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        assert _card_data(board_with_other, "sys-a1")["decisions"][-1]["answer"] == "planc"


async def test_escape_from_other_field_backs_out_without_writing(board_with_other):
    before = _card_data(board_with_other, "sys-a1")
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        assert _card_data(board_with_other, "sys-a1") == before


async def test_x_opens_reason_prompt_and_cancels(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, CancelModal)

        reason = "duplicate"
        await pilot.press(*reason)
        await pilot.press("enter")
        await pilot.pause()

        assert isinstance(app.screen, BoardScreen)
        data = _card_data(board, "sys-a1")
        assert data["stack"] == "cancelled"
        assert data["history"][-1]["note"] == reason


async def test_empty_reason_does_not_submit(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, CancelModal)
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, CancelModal)
        assert _card_data(board, "sys-a1")["stack"] == "decision_needed"


async def test_keys_hidden_off_a_decision_needed_card(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right")  # decision_needed -> todo: sys-b1
        # the legend of a todo card: the board's keys, and no a, x or d
        await legend_shows(
            pilot,
            lambda keys: keys
            == {"left", "right", "up", "down", "tab", "shift+tab"}
            | {"escape", "p", "i", "t", "ctrl+p", "q"},
        )
        assert screen.check_action("answer_decision", ()) is False
        assert screen.check_action("cancel_card", ()) is False
        assert screen.check_action("toggle_details", ()) is False


async def test_a_hidden_with_no_open_decision_x_still_shown(board):
    # a closed, answered decision, but the stack hand-patched back to
    # decision_needed — an edge case exercising the "open decision" half of
    # the `a` gate independently of the "right stack" half
    board_commands.card_answer(board, "sys-a1", answer="plan a", project="system")
    _patch_card(board, "sys-a1", stack="decision_needed")

    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        assert not screen.check_action("answer_decision", ())
        assert screen.check_action("cancel_card", ())


def test_tui_modules_never_write_a_file_directly():
    # regression guard for the card's limit: the screen's write path is only
    # `board_commands.card_answer` / `card_cancel` and `board_plans.thread_open` /
    # `thread_reply`, never a direct file write (`\b` so `thread_open(` is not read
    # as `open(`)
    tui_dir = Path(__file__).resolve().parent.parent / "src" / "autonomouse" / "tui"
    pattern = re.compile(r"\bopen\(|\.write_text\(|json\.dump\(|\.write\(")
    hits = [
        f"{path}: {line}"
        for path in tui_dir.glob("*.py")
        for line in path.read_text().splitlines()
        if pattern.search(line)
    ]
    assert hits == []


def _detail_text(screen) -> str:
    return screen.query_one(CardDetail).source


async def test_the_panel_asks_the_question_first_and_hides_worker_fields_until_d(board):
    # worker fields the panel must keep behind the details toggle (test setup only)
    _patch_card(
        board,
        "sys-a1",
        limits=["docs/rules.md applies", "keep the picker small"],
        phase="review",
        iteration=3,
        branch="card/sys-a1",
        pr="https://github.com/test/system/pull/7",
        handoff={
            "last_step": "worker note: ran the tests",
            "next_step": "worker note: open the PR",
            "updated_at": None,
            "run_id": None,
        },
    )
    app = AutonomouseApp()
    async with app.run_test(size=(240, 60)) as pilot:
        screen = await _open_system_board(pilot)  # sys-a1 has the initial focus
        text = _detail_text(screen)
        # the question first, as on Telegram (docs/rules.md rule 8; sys-w105)
        order = [
            text.index("which approach?"),
            text.index(BRIEF),
            text.index("1. plan a"),
            text.index("3. other: tell me"),
            text.index(f"### {PLAIN.recommendation} Recommendation"),
            text.index("### Answer"),
        ]
        assert order == sorted(order)
        for hidden in (
            "keep the picker small",
            "worker note",
            "card/sys-a1",
            "pull/7",
            "Iteration",
        ):
            assert hidden not in text
        await legend_shows(pilot, lambda keys: "d" in keys)

        await pilot.press("d")
        text = await pause_until(
            pilot, lambda: _detail_text(screen), lambda text: "keep the picker small" in text
        )
        assert text.index(BRIEF) < text.index("keep the picker small")
        for shown in (
            "worker note: ran the tests",
            "worker note: open the PR",
            "**Branch:** card/sys-a1",
            f"**{PLAIN.pr} Pull request:** https://github.com/test/system/pull/7",
            f"**Phase:** {PLAIN.phase[Phase.REVIEW]} review",
            f"**{PLAIN.iteration} Iteration:** 3",
        ):
            assert shown in text
        assert "docs/rules.md applies" not in text

        await pilot.press("d")
        await pause_until(
            pilot,
            lambda: _detail_text(screen),
            lambda text: BRIEF in text and "keep the picker small" not in text,
        )


async def test_details_are_off_again_for_the_next_card(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 60)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("d")
        await pause_until(pilot, lambda: screen.query_one(CardDetail).details, bool)
        await pilot.press("right")  # away to sys-b1
        await focus_lands(pilot, "sys-b1")
        await pilot.press("left")  # and back to sys-a1
        await focus_lands(pilot, "sys-a1")
        assert screen.query_one(CardDetail).details is False
        assert "## Goal" not in _detail_text(screen)


# ------------------------------------------------------------ sys-w115 --


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
@pytest.mark.parametrize(("key", "modal"), [("a", AnswerModal), ("x", CancelModal)])
async def test_both_dialogs_have_the_frame_in_both_themes(board, theme, key, modal):
    """The answer and the cancel dialog sit in the frame every panel has
    (`autonomouse.tui.look.FRAME`), heavy and in the focus colour a dialog holds."""
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        app.theme = theme
        await _open_system_board(pilot)
        await pilot.press(key)
        dialog = await pause_until(pilot, lambda: app.screen, lambda s: isinstance(s, modal))
        await pilot.pause()
        box = dialog.query_one(Vertical)
        assert FRAME.focused == f"heavy {FRAME.focus}"
        for edge in (box.styles.border_top, box.styles.border_bottom,
                     box.styles.border_left, box.styles.border_right):
            kind, colour = edge
            assert kind == "heavy"
            assert colour == resolve(theme, FRAME.focus), (theme, colour)


def _state(app) -> str:
    return str(app.screen.query_one("#state", Static).content)


async def test_the_dialog_shows_open_then_answered_and_a_click_chooses_an_option(board):
    """The answer dialog opens on the question's `OPEN` pill; a mouse click on an
    option chooses it, as enter does, and the pill turns to `ANSWERED`. Once the card
    lands, the pane's questions group shows the same question as answered."""
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        # the pane's decision opens with its OPEN pill too
        assert question_pill(answered=False).plain.strip() in _detail_text(screen)
        await pilot.press("a")
        modal = await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, AnswerModal)
        )
        assert _state(app) == question_pill(answered=False).plain
        options = modal.query_one("#options", OptionList)
        await pause_until(pilot, lambda: options.region.height, bool)
        # the second option, "plan b": the list's border takes the first row
        assert await pilot.click(options, offset=(4, 2))
        chosen = modal.query_one("#chosen", Static)
        await pause_until(pilot, lambda: chosen.display, bool)
        assert "Answer: plan b" in str(chosen.content)
        assert _state(app) == question_pill(answered=True).plain

        await pilot.press("enter")  # more work needed: to the top of todo
        await pause_until(pilot, lambda: app.screen, lambda s: isinstance(s, BoardScreen))
        assert _card_data(board, "sys-a1")["decisions"][-1]["answer"] == "plan b"
        await focus_lands(pilot, "sys-a1")
        text = await pause_until(
            pilot,
            lambda: _detail_text(screen),
            lambda t: question_pill(answered=True).plain.strip() in t,
        )
        assert question_pill(answered=False).plain.strip() not in text
