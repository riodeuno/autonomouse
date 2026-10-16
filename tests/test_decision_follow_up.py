"""A decision that leaves `decision_needed` by any path but the human's own Telegram
reply is followed up on Telegram (`card_answer`, `card_cancel`, `_follow_up`).

Telegram is replaced by a fake `urllib.request.urlopen` that records every
`sendMessage` request body and answers each with the next message id, so the tests
see the real payloads `autonomouse.notify` builds: the brief `card_ask` sends, the id
it stores on the decision, and the follow-up that replies to it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import DataTable

from autonomouse import inbox
from autonomouse.board import commands as board_commands
from autonomouse.board.commands import card_answer, card_ask, card_cancel, card_new
from autonomouse.board.runs import RunKind, run_start
from autonomouse.board.schema import Stack, card_from_dict
from autonomouse.board.store import load_card, save_card
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardScreen

CHAT = "555000111"
CONFIG = {"notify": {"channel": "telegram", "telegram_chat_id": CHAT}}
TOKEN = "123456789:AAtest-token"
RUN = "20260922-191659-0fe5"


class FakeTelegram:
    """Records every `sendMessage` body; answers with message ids 100, 101, …"""

    def __init__(self, updates: list[dict] | None = None):
        self.updates = updates or []
        self.sent: list[dict] = []

    def __call__(self, request, timeout=None):
        method = request.full_url.rsplit("/", 1)[1]
        body = json.loads(request.data)
        if method == "getUpdates" and "offset" in body:
            result = {"ok": True, "result": []}
        elif method == "getUpdates":
            result = {"ok": True, "result": self.updates}
        else:
            assert method == "sendMessage"
            assert body["chat_id"] == CHAT
            self.sent.append(body)
            result = {"ok": True, "result": {"message_id": 99 + len(self.sent)}}
        return _Response(json.dumps(result).encode())


class _Response:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def telegram(monkeypatch, tmp_path):
    """A temp config naming the fake chat, a fake token, and the fake Telegram."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(
        f'[notify]\nchannel = "telegram"\ntelegram_chat_id = "{CHAT}"\n'
    )
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    fake = FakeTelegram()
    with patch("urllib.request.urlopen", side_effect=fake):
        yield fake


def _asked(root: Path, card_id: str = "sys-a1") -> None:
    """A `decision_needed` card asked through the real `card_ask`."""
    board_commands.project_new(
        root,
        slug="system",
        name="System",
        goal="g",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    card_new(root, card_id=card_id, project="system", title="t", goal="g", test="t")
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    save_card(path, card)
    card_ask(
        root,
        card_id,
        brief="the brief",
        question="which approach?",
        options=["plan a", "plan b"],
        recommendation="plan a",
        run_id=RUN,
    )


def _card(root: Path, card_id: str = "sys-a1"):
    return load_card(root / "projects" / "system" / "cards" / f"{card_id}.json")


def test_ask_stores_the_briefs_message_id(tmp_path, telegram):
    _asked(tmp_path)
    assert len(telegram.sent) == 1
    assert telegram.sent[0]["text"].startswith(
        "<b>01 System</b>\n<b>which approach?</b>\n\nthe brief\n"
    )
    assert _card(tmp_path).decisions[-1].message_id == 100


def test_answer_on_the_screen_follows_up_as_a_reply(tmp_path, telegram):
    _asked(tmp_path)
    card_answer(
        tmp_path, "sys-a1", answer="plan b", run_id=board_commands.SCREEN_RUN_ID
    )

    assert len(telegram.sent) == 2
    follow_up = telegram.sent[1]
    assert follow_up["reply_parameters"] == {
        "message_id": 100,
        "allow_sending_without_reply": True,
    }
    assert follow_up["text"] == (
        "<b>02 System</b>\n<b>Answered: which approach?</b>\nAnswer: plan b\n"
        "Answered by: you, on the board screen\nThe card is now in the top of todo."
    )


async def test_answering_through_the_board_screen_sends_the_follow_up(
    tmp_path, telegram, monkeypatch
):
    """The real screen path: `a`, pick the second option, enter, then enter
    again on the default landing (more work needed, the top of todo)."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    _asked(tmp_path)
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        table = pilot.app.query_one(DataTable)
        table.move_cursor(row=table.get_row_index("system"))
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(pilot.app.screen, BoardScreen)
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert _card(tmp_path).decisions[-1].answer == "plan b"
    assert len(telegram.sent) == 2
    assert telegram.sent[1]["reply_parameters"]["message_id"] == 100
    assert "Answer: plan b\nAnswered by: you, on the board screen" in telegram.sent[1]["text"]


def test_answer_by_the_retired_run_id_human_names_only_the_run(tmp_path, telegram):
    _asked(tmp_path)
    card_answer(tmp_path, "sys-a1", answer="Yes to both", done=True, run_id="human")
    assert telegram.sent[1]["text"] == (
        "<b>02 System</b>\n<b>Answered: which approach?</b>\nAnswer: Yes to both\n"
        "Answered by: an agent\nThe card is now in done."
    )


def test_a_telegram_reply_gets_no_follow_up(tmp_path, telegram):
    _asked(tmp_path)
    telegram.updates = [
        {
            "update_id": 1,
            "message": {
                "message_id": 7,
                "chat": {"id": int(CHAT)},
                "text": "2",
                "reply_to_message": {"message_id": 100, "text": "[sys-a1] decision"},
            },
        }
    ]
    assert inbox.poll(tmp_path, config=CONFIG, env={"TELEGRAM_BOT_TOKEN": TOKEN}) == 0

    assert _card(tmp_path).decisions[-1].answer == "plan b"
    texts = [body["text"] for body in telegram.sent[1:]]
    assert texts == [
        (
            '02 System\nanswer recorded on the question "which approach?": plan b. The '
            "card is now in the top of todo."
        )
    ]


def test_cancel_of_an_open_decision_follows_up_without_the_reason(tmp_path, telegram):
    # the reason is written for the board and here names two cards by their ids, which
    # the human never reads (sys-e11); it stays in the card's history
    _asked(tmp_path)
    # The orchestrator's wake has a run record of kind wake; a run id with none is
    # named "an agent".
    run_start(tmp_path, run_id=RUN, session_id="s", pid=1, model="opus", kind=RunKind.WAKE)
    card_cancel(
        tmp_path, "sys-a1", reason="split into sys-a2 and sys-a3\nbecause big", run_id=RUN
    )
    follow_up = telegram.sent[1]
    assert follow_up["reply_parameters"]["message_id"] == 100
    assert follow_up["text"] == (
        "<b>02 System</b>\n<b>No longer needed: which approach?</b>\n"
        "The card was cancelled by the orchestrator."
    )
    assert _card(tmp_path).history[-1].note == "split into sys-a2 and sys-a3\nbecause big"


def test_cancel_without_an_open_decision_sends_nothing(tmp_path, telegram):
    _asked(tmp_path)
    card_answer(tmp_path, "sys-a1", answer="plan a", run_id="telegram")
    card_cancel(tmp_path, "sys-a1", reason="not needed", run_id=RUN)
    assert len(telegram.sent) == 1  # only the brief


def test_without_a_stored_message_id_the_follow_up_is_a_plain_message(
    tmp_path, telegram
):
    _asked(tmp_path)
    path = tmp_path / "projects" / "system" / "cards" / "sys-a1.json"
    card = load_card(path)
    card.decisions[-1].message_id = None
    save_card(path, card)

    card_answer(tmp_path, "sys-a1", answer="plan a", run_id="plan-20260922-120000-abcd")
    follow_up = telegram.sent[1]
    assert "reply_parameters" not in follow_up
    assert follow_up["text"].startswith(
        "<b>02 System</b>\n<b>Answered: which approach?</b>\nAnswer: plan a\n"
        "Answered by: the planner\n"
    )


def test_a_decision_written_before_message_id_existed_still_loads(tmp_path, telegram):
    _asked(tmp_path)
    path = tmp_path / "projects" / "system" / "cards" / "sys-a1.json"
    data = json.loads(path.read_text())
    del data["decisions"][-1]["message_id"]
    assert card_from_dict(data, source="old").decisions[-1].message_id is None
