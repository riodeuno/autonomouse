"""Reference numbers on Telegram messages (sys-e10): every message about a card or a
planning thread opens with a two-digit number and the project's name, the board's
reference register (`autonomouse.board.references`) holds what each number is about, and
`autonomouse notify poll` (`autonomouse.inbox`) routes the human's reply by it.

Telegram is a fake `urllib.request.urlopen` that gives each sent message its own id and
serves the human's messages, hand-built to the Bot API's getUpdates shape, as the
updates. The board is a temporary folder (`AUTONOMOUSE_HOME`); no real message is sent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomouse import inbox
from autonomouse.board import references
from autonomouse.board.commands import (
    CommandError,
    card_ask,
    card_new,
    card_show,
    project_new,
)
from autonomouse.board.plans import (
    Author,
    Message,
    Thread,
    ThreadStatus,
    load_thread,
    save_thread,
    thread_list,
)
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card
from autonomouse.notify import send_about

CHAT = "555000111"
TOKEN = "123456:fake-token-only-in-the-secrets-file"
CONFIG = {"notify": {"channel": "telegram", "telegram_chat_id": CHAT}}
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN}
THREAD = "plan-20260927-120000-abcd"


class FakeTelegram:
    """Answers getUpdates with `updates`; each sendMessage gets the next message id,
    from 1001, and its text is kept."""

    def __init__(self) -> None:
        self.updates: list[dict] = []
        self.sent: list[dict] = []

    def __call__(self, request, timeout=None):
        method = request.full_url.rsplit("/", 1)[1]
        body = json.loads(request.data)
        if method == "getUpdates":
            result: object = [] if "offset" in body else self.updates
        else:
            assert method == "sendMessage"
            assert body["chat_id"] == CHAT
            self.sent.append(body)
            result = {"message_id": 1000 + len(self.sent)}
        return _Response(json.dumps({"ok": True, "result": result}).encode())

    @property
    def texts(self) -> list[str]:
        return [body["text"] for body in self.sent]


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
def telegram(tmp_path, monkeypatch):
    """The board at tmp_path/board, a config folder with a chat id and a secrets file
    holding a fake token, and Telegram faked for every send, so `card ask` and the CLI
    send through the fake."""
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / ".env").write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\n")
    (folder / "config.toml").write_text(f'[notify]\ntelegram_chat_id = "{CHAT}"\n')
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(folder))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    fake = FakeTelegram()
    with patch("urllib.request.urlopen", side_effect=fake):
        yield fake


@pytest.fixture
def root(tmp_path) -> Path:
    """Projects widgets and gadgets; card wid-a1 in progress on widgets, and THREAD on
    gadgets, open, its planner working."""
    board = tmp_path / "board"
    for slug, name in (("widgets", "Widgets"), ("gadgets", "Gadgets")):
        project_new(
            board,
            slug=slug,
            name=name,
            goal="g",
            repo=f"https://github.com/test/{slug}",
            repo_path=f"/home/test/{slug}",
            default_branch="main",
            priority=1,
        )
    _card(board, "wid-a1")
    (board / "plans").mkdir()
    save_thread(
        board,
        Thread(
            id=THREAD,
            project="gadgets",
            opened_at="2026-09-27T12:00:00Z",
            session_id="s-1",
            status=ThreadStatus.WAITING_PLANNER,
            messages=[Message("2026-09-27T12:00:00Z", Author.HUMAN, "an export command")],
        ),
    )
    return board


def _card(root: Path, card_id: str) -> None:
    card_new(root, card_id=card_id, project="widgets", title="t", goal="g", test="t")
    path = root / "projects" / "widgets" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    save_card(path, card)


def _ask(root: Path, card_id: str = "wid-a1") -> None:
    """Card `card_id` asks which spend limit; `card ask` sends the brief."""
    card_ask(
        root,
        card_id,
        brief="the brief",
        question="Which spend limit?",
        options=["$10 a day", "$50 a day"],
        recommendation="$10 a day",
    )


def _cli(monkeypatch, *argv: str) -> None:
    from autonomouse import cli

    monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
    cli.main()


def _planner_asks(root: Path, monkeypatch, text: str) -> None:
    """The planner's question on THREAD: `plan say`, then `notify --about`."""
    thread = load_thread(root, THREAD)
    thread.messages.append(Message("2026-09-27T12:01:00Z", Author.PLANNER, text))
    thread.status = ThreadStatus.WAITING_HUMAN
    save_thread(root, thread)
    _cli(monkeypatch, "notify", "--about", THREAD, text)


def _message(text: str, update_id: int, reply_to: int | None = None) -> dict:
    message: dict = {
        "message_id": update_id,
        "chat": {"id": int(CHAT), "type": "private"},
        "date": 1758974400,
        "text": text,
    }
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to, "text": "a bot message"}
    return {"update_id": update_id, "message": message}


def _poll(root: Path, telegram: FakeTelegram, *updates: dict) -> list[str]:
    """Run `notify poll` on `updates`; return the acknowledgements it sent."""
    telegram.updates = list(updates)
    before = len(telegram.sent)
    assert inbox.poll(root, config=CONFIG, env=ENV, out=lambda _: None) == 0
    return telegram.texts[before:]


# (a) ------------------------------------------------------------------------------------


def test_notify_about_a_card_then_a_thread_numbers_them_and_registers_each(
    root, telegram, monkeypatch, capsys
) -> None:
    _cli(monkeypatch, "notify", "--about", "wid-a1", "The build is green.")
    _cli(monkeypatch, "notify", "--about", THREAD, "Should the export skip cancelled cards?")

    assert telegram.texts == [
        "<b>01 Widgets</b>\nThe build is green.",
        "<b>02 Gadgets</b>\nShould the export skip cancelled cards?",
    ]
    assert capsys.readouterr().out == "sent.\nsent.\n"
    assert [
        (r.number, r.message_id, r.about, r.project) for r in references.load(root)
    ] == [(1, 1001, "wid-a1", "widgets"), (2, 1002, THREAD, "gadgets")]


def test_notify_about_an_id_the_board_does_not_hold_sends_nothing(
    root, telegram, monkeypatch, capsys
) -> None:
    with pytest.raises(SystemExit) as exit_:
        _cli(monkeypatch, "notify", "--about", "wid-z9", "hello")

    assert exit_.value.code == 1
    assert "wid-z9" in capsys.readouterr().err
    assert telegram.sent == []
    assert references.load(root) == []


def test_the_decision_brief_opens_with_its_number_and_project(root, telegram) -> None:
    _ask(root)

    lines = telegram.texts[0].splitlines()
    assert lines[:4] == ["<b>01 Widgets</b>", "<b>Which spend limit?</b>", "", "the brief"]
    assert card_show(root, "wid-a1").decisions[-1].message_id == 1001
    assert [(r.number, r.about) for r in references.load(root)] == [(1, "wid-a1")]


# (b) ------------------------------------------------------------------------------------


def test_a_reply_to_the_second_questions_message_answers_the_second(
    root, telegram, monkeypatch
) -> None:
    _ask(root)  # 01, message 1001
    _planner_asks(root, monkeypatch, "Should the export skip cancelled cards?")  # 02

    acks = _poll(root, telegram, _message("keep them, marked", 1, reply_to=1002))

    thread = load_thread(root, THREAD)
    assert thread.messages[-1].text == "keep them, marked"
    assert thread.status is ThreadStatus.WAITING_PLANNER
    assert card_show(root, "wid-a1").decisions[-1].answer is None
    assert acks == [
        (
            '03 Gadgets\nyour reply is added to the planning conversation "an export '
            'command". The planner answers here when it has read it.'
        )
    ]
    assert references.load(root)[-1].about == THREAD


@pytest.mark.parametrize("text", ["07 2", "#07 2"])
def test_a_message_naming_a_number_answers_the_question_registered_as_it(
    root, telegram, monkeypatch, text
) -> None:
    for n in range(6):  # 01 to 06, about the thread
        _cli(monkeypatch, "notify", "--about", THREAD, f"progress {n}")
    _ask(root)  # 07
    _planner_asks(root, monkeypatch, "Should the export skip cancelled cards?")  # 08
    assert references.by_number(references.load(root), 7).about == "wid-a1"

    acks = _poll(root, telegram, _message(text, 1))

    card = card_show(root, "wid-a1")
    assert card.decisions[-1].answer == "$50 a day"
    assert card.stack == Stack.TODO
    assert load_thread(root, THREAD).status is ThreadStatus.WAITING_HUMAN
    assert acks == [
        (
            '09 Widgets\nanswer recorded on the question "Which spend limit?": $50 a day. '
            "The card is now in the top of todo."
        )
    ]


# (c) ------------------------------------------------------------------------------------


def test_a_bare_yes_answers_the_one_open_question(root, telegram) -> None:
    _ask(root)  # the thread's planner is working, so this is the one open question

    acks = _poll(root, telegram, _message("yes", 1))

    assert card_show(root, "wid-a1").decisions[-1].answer == "yes"
    assert [t.id for t in thread_list(root)] == [THREAD]
    assert acks[0].startswith('02 Widgets\nanswer recorded on the question "Which')


def test_a_bare_yes_opens_a_thread_when_two_questions_are_open(
    root, telegram, monkeypatch
) -> None:
    _ask(root)
    _planner_asks(root, monkeypatch, "Should the export skip cancelled cards?")

    acks = _poll(root, telegram, _message("yes", 1))

    assert card_show(root, "wid-a1").decisions[-1].answer is None
    assert load_thread(root, THREAD).messages[-1].from_ is Author.PLANNER
    opened = [t for t in thread_list(root) if t.id != THREAD]
    assert [t.messages[0].text for t in opened] == ["yes"]
    assert acks[0].splitlines()[0] == f"03 {opened[0].project.capitalize()}"
    assert "opened a new planning conversation" in acks[0]


def test_a_message_starting_with_a_project_opens_a_thread_there_while_one_is_open(
    root, telegram
) -> None:
    _ask(root)

    _poll(root, telegram, _message("gadgets: a battery gauge", 1))

    assert card_show(root, "wid-a1").decisions[-1].answer is None
    opened = [t for t in thread_list(root) if t.id != THREAD]
    assert [(t.project, t.messages[0].text) for t in opened] == [
        ("gadgets", "gadgets: a battery gauge")
    ]


# (d) ------------------------------------------------------------------------------------


def _board_files(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): p.read_text() for p in root.rglob("*") if p.is_file()
    }


def test_a_number_the_register_does_not_hold_records_nothing_and_lists_the_open_ones(
    root, telegram, monkeypatch
) -> None:
    _ask(root)  # 01
    _planner_asks(root, monkeypatch, "Should the export skip cancelled cards?")  # 02
    before = _board_files(root)

    acks = _poll(root, telegram, _message("42 go ahead", 1))

    assert _board_files(root) == before
    assert acks == [
        (
            "Nothing was recorded: no message carries the number 42 now. The questions "
            "waiting for you: 01 Widgets, 02 Gadgets. Reply to one of those messages, or "
            "start your message with its number."
        )
    ]


def test_a_reply_to_a_card_with_no_question_lists_the_open_ones(
    root, telegram, monkeypatch
) -> None:
    _cli(monkeypatch, "notify", "--about", "wid-a1", "The build is green.")  # 01
    _planner_asks(root, monkeypatch, "Should the export skip cancelled cards?")  # 02
    card_before = card_show(root, "wid-a1")

    acks = _poll(root, telegram, _message("thanks", 1, reply_to=1001))

    assert card_show(root, "wid-a1") == card_before
    assert load_thread(root, THREAD).messages[-1].from_ is Author.PLANNER
    assert acks == [
        (
            "03 Widgets\nNothing was recorded: this card has no question waiting for an "
            "answer. The questions waiting for you: 02 Gadgets. Reply to one of those "
            "messages, or start your message with its number."
        )
    ]


# (e) ------------------------------------------------------------------------------------


def test_after_99_messages_the_next_number_is_01_and_replaces_the_oldest(
    root, telegram
) -> None:
    for n in range(100):
        send_about(root, "wid-a1", f"message {n}", config=CONFIG, env=ENV)

    assert telegram.texts[98].startswith("99 Widgets\n")
    assert telegram.texts[99] == "01 Widgets\nmessage 99"
    entries = references.load(root)
    assert len(entries) == 99
    assert references.by_number(entries, 1).message_id == 1100
    assert references.by_message(entries, 1001) is None
    assert references.next_number(entries) == 2


def test_a_reply_to_an_open_decision_answers_it_after_100_later_sends(
    root, telegram
) -> None:
    _ask(root)  # 01, message 1001: the decision stays open through every send below
    for n in range(100):
        send_about(root, THREAD, f"progress {n}", config=CONFIG, env=ENV)

    later = telegram.texts[1:]
    # the human's reply to the brief, as Telegram delivers it: the replied message's
    # id and its text
    update = _message("2", 1, reply_to=1001)
    update["message"]["reply_to_message"]["text"] = telegram.texts[0]

    _poll(root, telegram, update)

    assert card_show(root, "wid-a1").decisions[-1].answer == "$50 a day"
    # no later message took the open decision's number 01
    assert not any(t.startswith("01 ") for t in later)


def test_a_reply_to_a_planner_question_reaches_its_thread_after_100_later_sends(
    root, telegram, monkeypatch
) -> None:
    _planner_asks(root, monkeypatch, "Should the export skip cancelled cards?")  # 01
    for n in range(100):
        send_about(root, "wid-a1", f"progress {n}", config=CONFIG, env=ENV)

    later = telegram.texts[1:]

    _poll(root, telegram, _message("keep them, marked", 1, reply_to=1001))

    assert load_thread(root, THREAD).messages[-1].text == "keep them, marked"
    assert not any(t.startswith("01 ") for t in later)


def test_once_the_decision_is_answered_its_number_is_given_out_again(
    root, telegram
) -> None:
    _ask(root)  # 01
    for n in range(98):  # 02 to 99
        send_about(root, THREAD, f"progress {n}", config=CONFIG, env=ENV)
    _poll(root, telegram, _message("2", 1, reply_to=1001))  # its acknowledgement is 01

    assert telegram.texts[-1].startswith("01 Widgets\nanswer recorded")


def test_when_every_number_is_on_an_open_question_none_is_given_out() -> None:
    entries = [
        references.Reference(n, 1000 + n, "wid-a1", "widgets", "2026-09-27T12:00:00Z")
        for n in range(1, 100)
    ]
    held = frozenset(e.message_id for e in entries)

    assert references.next_number(entries, held - {1050}) == 50
    with pytest.raises(CommandError, match="every reference number"):
        references.next_number(entries, held)


def test_a_failed_send_takes_no_number(root, telegram) -> None:
    from autonomouse.notify import notify

    notify("[wid-a1] hello", root=root, config={"notify": {}}, env=ENV)  # no chat id
    send_about(root, "wid-a1", "hello", config=CONFIG, env=ENV)

    assert telegram.texts == ["01 Widgets\nhello"]


# replies the register cannot place ---------------------------------------------------


def test_a_reply_to_a_brief_whose_number_was_reused_resolves_by_its_tag(
    root, telegram
) -> None:
    _ask(root)
    # a brief sent before sys-e11 carried the card's id under its number line
    brief = "01 Widgets\n[wid-a1] decision needed\nWhich spend limit?"
    update = _message("2", 1, reply_to=5)  # a message id the register does not hold
    update["message"]["reply_to_message"]["text"] = brief

    _poll(root, telegram, update)

    assert card_show(root, "wid-a1").decisions[-1].answer == "$50 a day"


def test_a_reply_to_an_unplaced_message_opens_a_thread_and_answers_nothing(
    root, telegram
) -> None:
    _ask(root)  # the one open question

    _poll(root, telegram, _message("what happened?", 1, reply_to=5))

    assert card_show(root, "wid-a1").decisions[-1].answer is None
    opened = [t for t in thread_list(root) if t.id != THREAD]
    assert [t.messages[0].text for t in opened] == ["what happened?"]


def test_an_unreadable_card_never_stops_the_poll_from_acknowledging(root, telegram) -> None:
    # listing the open questions reads every card: a malformed one must not raise out
    # of the poll, or the batch is never confirmed and routed again on the next poll
    (root / "projects" / "widgets" / "cards" / "wid-z9.json").write_text("{")

    acks = _poll(root, telegram, _message("42 hi", 1))

    assert acks == ["Nothing was recorded: no message carries the number 42 now."]
