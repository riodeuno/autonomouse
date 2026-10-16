"""`autonomouse notify poll` (`autonomouse.inbox`) on a temp board.

Telegram is replaced by a fake `urllib.request.urlopen` that answers the first
`getUpdates` with tests/fixtures/telegram/getUpdates_replies.json (hand-built to the Bot
API's shape; its `recorded` field says so), records the confirming `getUpdates` and every
`sendMessage`, so the tests see the real request bodies the module builds.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomouse import inbox
from autonomouse.board import references
from autonomouse.board.commands import (
    card_answer,
    card_ask,
    card_cancel,
    card_move,
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
from autonomouse.config import DEFAULTS
from autonomouse.home import Home

FIXTURE = Path(__file__).parent / "fixtures" / "telegram" / "getUpdates_replies.json"
CONFIG = {"notify": {"channel": "telegram", "telegram_chat_id": "555000111"}}
ENV = {"TELEGRAM_BOT_TOKEN": "123456789:AAtest-token"}
THREAD = "plan-20260922-120000-abcd"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No live send: `card_ask` sends with the real config and environment otherwise."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "no-config"))


class FakeTelegram:
    def __init__(self, updates: list[dict]):
        self.updates = updates
        self.confirmed: list[dict] = []
        self.sent: list[str] = []

    def __call__(self, request, timeout=None):
        method = request.full_url.rsplit("/", 1)[1]
        body = json.loads(request.data)
        if method == "getUpdates" and "offset" in body:
            self.confirmed.append(body)
            result = {"ok": True, "result": []}
        elif method == "getUpdates":
            result = {"ok": True, "result": self.updates}
        else:
            assert method == "sendMessage"
            assert body["chat_id"] == "555000111"
            self.sent.append(body["text"])
            result = {"ok": True, "result": {"message_id": 99}}
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


def _board(root: Path) -> None:
    for slug, name in (("widgets", "Widgets"), ("gadgets", "Gadgets")):
        project_new(
            root,
            slug=slug,
            name=name,
            goal="g",
            repo=f"https://github.com/test/{slug}",
            repo_path=f"/home/test/{slug}",
            default_branch="main",
            priority=1,
        )
    asks = {
        "wid-a1": ("Which spend limit?", ["$10 a day", "$50 a day"]),
        "wid-a2": ("Ship the release notes as they are?", ["yes", "no, rewrite them"]),
    }
    for card_id, (question, options) in asks.items():
        card_new(root, card_id=card_id, project="widgets", title="t", goal="g", test="t")
        path = root / "projects" / "widgets" / "cards" / f"{card_id}.json"
        card = load_card(path)
        card.stack = Stack.IN_PROGRESS
        save_card(path, card)
        card_ask(
            root, card_id, brief="the brief", question=question, options=options
        )
    (root / "plans").mkdir()
    save_thread(
        root,
        Thread(
            id=THREAD,
            project="gadgets",
            opened_at="2026-09-22T12:00:00Z",
            session_id="s-1",
            status=ThreadStatus.WAITING_HUMAN,
            messages=[
                Message("2026-09-22T12:00:00Z", Author.HUMAN, "an export command"),
                Message(
                    "2026-09-22T12:01:00Z",
                    Author.PLANNER,
                    "Should the export include cancelled cards?",
                ),
            ],
        ),
    )


def _poll(root: Path, updates: list[dict], out: list[str]) -> FakeTelegram:
    fake = FakeTelegram(updates)
    with patch("urllib.request.urlopen", side_effect=fake):
        code = inbox.poll(root, config=CONFIG, env=ENV, out=out.append)
    assert code == 0
    return fake


def test_poll_routes_the_recorded_updates_onto_the_board(tmp_path) -> None:
    _board(tmp_path)
    updates = json.loads(FIXTURE.read_text())["response"]["result"]
    out: list[str] = []

    fake = _poll(tmp_path, updates, out)

    first = card_show(tmp_path, "wid-a1")
    assert first.stack == Stack.TODO
    assert first.position == 0
    assert first.decisions[-1].answer == "$50 a day"
    assert first.history[-1].run_id == "telegram"

    second = card_show(tmp_path, "wid-a2")
    assert second.stack == Stack.DONE
    assert second.decisions[-1].answer == "done"

    thread = load_thread(tmp_path, THREAD)
    assert thread.status is ThreadStatus.WAITING_PLANNER
    assert [m.from_ for m in thread.messages[2:]] == [Author.HUMAN]
    assert thread.messages[-1].text == "Yes, include them, marked as cancelled."

    opened = [t for t in thread_list(tmp_path) if t.id != THREAD]
    assert len(opened) == 1
    # no project named: the most recent moment is the reply this poll wrote to THREAD
    # just before, on gadgets, though it often shares its second with wid-a2's decision
    assert opened[0].project == "gadgets"
    assert opened[0].messages[0].text == "Add a dark mode to the board screen"
    assert opened[0].status is ThreadStatus.OPEN

    assert fake.confirmed == [{"offset": 900000016, "limit": 1, "timeout": 0}]
    assert out[-1] == (
        "poll: 2 decisions answered, 0 replies to answered decisions recorded, "
        "0 replies to cancelled decisions recorded, 1 thread replies, 1 threads opened, "
        "0 not routed, 1 from other chats ignored"
    )
    # each acknowledgement opens with its reference number and the project's name, and
    # the register holds the card or thread each number is about
    assert [text.splitlines()[0] for text in fake.sent] == [
        "01 Widgets",
        "02 Widgets",
        "03 Gadgets",
        "04 Gadgets",
    ]
    assert [(r.number, r.about) for r in references.load(tmp_path)] == [
        (1, "wid-a1"),
        (2, "wid-a2"),
        (3, THREAD),
        (4, opened[0].id),
    ]


def _message(text: str, reply_to: str | None = None, update_id: int = 1) -> dict:
    message = {
        "message_id": update_id,
        "chat": {"id": 555000111, "type": "private"},
        "date": 1758535200,
        "text": text,
    }
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": 7, "text": reply_to}
    return {"update_id": update_id, "message": message}


def test_a_message_starting_with_a_card_id_answers_that_card(tmp_path) -> None:
    _board(tmp_path)

    _poll(tmp_path, [_message("WID-A1: raise it to 20 dollars")], [])

    card = card_show(tmp_path, "wid-a1")
    assert card.decisions[-1].answer == "raise it to 20 dollars"
    assert card.stack == Stack.TODO


def test_a_fresh_message_naming_a_project_opens_its_thread_there(tmp_path) -> None:
    _board(tmp_path)

    _poll(tmp_path, [_message("gadgets: a battery gauge")], [])

    opened = [t for t in thread_list(tmp_path) if t.id != THREAD]
    assert [t.project for t in opened] == ["gadgets"]


def test_a_fresh_message_goes_to_the_project_of_the_newest_decision(tmp_path) -> None:
    _board(tmp_path)  # the decisions are asked now; THREAD's messages are from 2026-09-22

    _poll(tmp_path, [_message("a battery gauge")], [])

    opened = [t for t in thread_list(tmp_path) if t.id != THREAD]
    assert [t.project for t in opened] == ["widgets"]


def test_the_thread_this_poll_wrote_to_last_wins_a_tie(tmp_path) -> None:
    _board(tmp_path)
    updates = [
        _message("widgets: a dark mode", update_id=1),
        _message("a nightly export", reply_to=f"[{THREAD}] a question", update_id=2),
        _message("a battery gauge", update_id=3),
    ]

    _poll(tmp_path, updates, [])

    # the widgets thread and THREAD's reply are written in one poll, usually in one
    # second; the reply came last, so the fresh message goes to THREAD's project
    fresh = [t for t in thread_list(tmp_path) if t.messages[0].text == "a battery gauge"]
    assert [t.project for t in fresh] == ["gadgets"]


def test_a_reply_to_a_closed_thread_opens_a_new_one_on_its_project(tmp_path) -> None:
    _board(tmp_path)
    thread = load_thread(tmp_path, THREAD)
    thread.status = ThreadStatus.CLOSED
    save_thread(tmp_path, thread)

    _poll(tmp_path, [_message("one more thing", reply_to=f"[{THREAD}] closed")], [])

    assert load_thread(tmp_path, THREAD).messages[-1].text != "one more thing"
    opened = [t for t in thread_list(tmp_path) if t.id != THREAD]
    assert [(t.project, t.messages[0].text) for t in opened] == [
        ("gadgets", "one more thing")
    ]


def test_an_unroutable_message_is_reported_and_still_confirmed(tmp_path) -> None:
    _board(tmp_path)
    out: list[str] = []

    fake = _poll(tmp_path, [_message("7", reply_to="[wid-a1] decision needed")], out)

    assert card_show(tmp_path, "wid-a1").stack == Stack.DECISION_NEEDED
    assert "options 1 to 3; got 7" in out[0]
    assert fake.sent[0] == (
        "01 Widgets\nNothing was recorded: this question has options 1 to 3; got 7."
    )
    assert fake.confirmed == [{"offset": 2, "limit": 1, "timeout": 0}]
    assert "1 not routed" in out[-1]


def test_the_number_of_other_asks_for_the_humans_own_words(tmp_path) -> None:
    _board(tmp_path)
    out: list[str] = []

    fake = _poll(tmp_path, [_message("3", reply_to="[wid-a1] decision needed")], out)

    card = card_show(tmp_path, "wid-a1")
    assert card.stack == Stack.DECISION_NEEDED
    assert card.decisions[-1].answer is None
    assert "reply with your answer in your own words" in fake.sent[0]


def _ask(root: Path, card_id: str, options: list[str]) -> None:
    """Ask a decision with these options on a new widgets card."""
    card_new(root, card_id=card_id, project="widgets", title="t", goal="g", test="t")
    path = root / "projects" / "widgets" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    save_card(path, card)
    card_ask(
        root, card_id, brief="the brief", question="Is the login in place?", options=options
    )


# The line `card answer` sends to Telegram when a decision is answered from a shell
# (`board.commands._follow_up`); the human's reply to it carries its `[sys-x1]` tag.
FOLLOW_UP = (
    "[sys-x1] decision answered by someone at the command line: yes. "
    "The card is now in the top of todo."
)


def _answered(root: Path) -> None:
    """The board, plus sys-x1, whose decision was answered from a shell."""
    _board(root)
    _ask(root, "sys-x1", ["yes", "no"])
    card_answer(root, "sys-x1", answer="yes")


def test_a_reply_to_an_answered_decision_is_recorded_on_it_and_opens_no_thread(
    tmp_path,
) -> None:
    # Telegram message 56 of 2026-09-26 confirmed an answer given from a shell; the
    # reply was refused as not routed, and nothing of it reached the board
    _answered(tmp_path)
    before = card_show(tmp_path, "sys-x1")
    out: list[str] = []

    fake = _poll(
        tmp_path, [_message("Yes. Push it after each wake", reply_to=FOLLOW_UP)], out
    )

    card = card_show(tmp_path, "sys-x1")
    decision = card.decisions[-1]
    assert decision.answer == "yes"
    assert decision.answered_at == before.decisions[-1].answered_at
    assert decision.reply == "Yes. Push it after each wake"
    assert decision.replied_at == card.history[-1].at
    assert (card.stack, card.position) == (before.stack, before.position)
    row = card.history[-1]
    assert (row.run_id, row.from_, row.to) == ("telegram", "todo", "todo")
    assert row.note == "reply to answered decision d1: Yes. Push it after each wake"
    assert [t.id for t in thread_list(tmp_path)] == [THREAD]
    assert fake.sent == [
        (
            '01 Widgets\nrecorded as a reply to the answered question "Is the login in '
            'place?". Its answer stays "yes" and the card does not move; to ask for '
            "more work, send a message that is not a reply."
        )
    ]
    assert out[-1] == (
        "poll: 0 decisions answered, 1 replies to answered decisions recorded, "
        "0 replies to cancelled decisions recorded, 0 thread replies, 0 threads opened, "
        "0 not routed, 0 from other chats ignored"
    )


def test_a_later_reply_replaces_the_recorded_one_and_the_history_keeps_both(
    tmp_path,
) -> None:
    _answered(tmp_path)

    _poll(tmp_path, [_message("Yes.", reply_to=FOLLOW_UP)], [])
    _poll(tmp_path, [_message("SYS-X1: and after each wake", update_id=2)], [])

    card = card_show(tmp_path, "sys-x1")
    assert card.decisions[-1].reply == "and after each wake"
    assert [h.note for h in card.history[-2:]] == [
        "reply to answered decision d1: Yes.",
        "reply to answered decision d1: and after each wake",
    ]


# The line `card cancel` sends to Telegram when a card's decision was still open
# (`board.commands._follow_up`); the human's reply to it carries its `[sys-x2]` tag.
CANCEL_FOLLOW_UP = "[sys-x2] the question is no longer needed: cancelled"


def _cancelled(root: Path) -> None:
    """The board, plus sys-x2, whose decision was still open when the card was
    cancelled from a shell."""
    _board(root)
    _ask(root, "sys-x2", ["yes", "no"])
    card_cancel(root, "sys-x2", reason="superseded by sys-x9")


def test_a_reply_to_a_cancelled_cards_follow_up_is_recorded_not_refused(
    tmp_path,
) -> None:
    # the cancel follow-up leaves the decision unanswered; a reply to it was refused
    # "[not routed] ... Nothing was recorded" instead of being recorded on the card
    _cancelled(tmp_path)
    before = card_show(tmp_path, "sys-x2")
    out: list[str] = []

    fake = _poll(
        tmp_path, [_message("Ok, understood.", reply_to=CANCEL_FOLLOW_UP)], out
    )

    card = card_show(tmp_path, "sys-x2")
    decision = card.decisions[-1]
    assert decision.answer is None
    assert decision.reply == "Ok, understood."
    assert decision.replied_at == card.history[-1].at
    assert card.stack == Stack.CANCELLED
    assert card.history[:-1] == before.history
    row = card.history[-1]
    assert (row.run_id, row.from_, row.to) == ("telegram", "cancelled", "cancelled")
    assert row.note == "reply to cancelled decision d1: Ok, understood."
    assert [t.id for t in thread_list(tmp_path)] == [THREAD]
    assert fake.sent == [
        (
            '01 Widgets\nrecorded as a reply to the cancelled question "Is the login '
            'in place?". The card is cancelled; a message that is not a reply asks '
            "for new work."
        )
    ]
    assert not fake.sent[0].lower().startswith("01 widgets\nnothing was recorded")
    assert out[-1] == (
        "poll: 0 decisions answered, 0 replies to answered decisions recorded, "
        "1 replies to cancelled decisions recorded, 0 thread replies, 0 threads opened, "
        "0 not routed, 0 from other chats ignored"
    )


def test_a_reply_to_a_card_with_no_decision_is_still_not_routed(tmp_path) -> None:
    _board(tmp_path)
    card_new(tmp_path, card_id="wid-a4", project="widgets", title="t", goal="g", test="t")
    before = card_show(tmp_path, "wid-a4")
    out: list[str] = []

    fake = _poll(tmp_path, [_message("go ahead", reply_to="[wid-a4] done")], out)

    card = card_show(tmp_path, "wid-a4")
    assert (card.decisions, card.history) == ([], before.history)
    assert [t.id for t in thread_list(tmp_path)] == [THREAD]
    assert fake.sent[0].startswith(
        "01 Widgets\nNothing was recorded: this card has no question waiting for an "
        "answer. The questions waiting for you: "
    )
    assert "1 not routed" in out[-1]


ACCESS_OPTIONS = ["done", "leave this card for now"]


@pytest.mark.parametrize("reply", ["done", "Done "])
def test_a_reply_equal_to_an_option_named_done_answers_with_it(tmp_path, reply) -> None:
    # an access question (docs/orchestrator/access-checks.md) offers `done`: the login
    # is in place, so the card goes back to work, not to the done stack
    _board(tmp_path)
    _ask(tmp_path, "wid-a3", ACCESS_OPTIONS)

    _poll(tmp_path, [_message(reply, reply_to="[wid-a3] decision needed")], [])

    card = card_show(tmp_path, "wid-a3")
    assert card.stack == Stack.TODO
    assert card.position == 0
    assert card.decisions[-1].answer == "done"


def test_done_to_a_question_with_no_done_option_still_finishes_the_card(
    tmp_path,
) -> None:
    _board(tmp_path)  # wid-a2's options are "yes" and "no, rewrite them"

    _poll(tmp_path, [_message("done", reply_to="[wid-a2] decision needed")], [])

    card = card_show(tmp_path, "wid-a2")
    assert card.decisions[-1].answer == "done"
    assert card.stack == Stack.DONE


def test_a_reply_equal_to_another_option_records_that_option(tmp_path) -> None:
    _board(tmp_path)
    _ask(tmp_path, "wid-a3", ACCESS_OPTIONS)

    _poll(
        tmp_path,
        [_message("Leave this card for NOW", reply_to="[wid-a3] decision needed")],
        [],
    )

    card = card_show(tmp_path, "wid-a3")
    assert card.decisions[-1].answer == "leave this card for now"
    assert card.stack == Stack.TODO
    assert card.position == 0


# What the human replies to: the acknowledgement of their Telegram answer
# 'leave this card for now', tagged with the card's id.
SET_ASIDE_ACK = (
    "[wid-a3] answer recorded: leave this card for now. The card is now in the top of "
    "todo."
)


def _set_aside(root: Path) -> None:
    """The board, plus wid-a3, whose access question the human answered 'leave this card
    for now' on Telegram, and which the orchestrator then moved to backlog with the
    human's words (docs/orchestrator/access-checks.md)."""
    _board(root)
    _ask(root, "wid-a3", ACCESS_OPTIONS)
    card_answer(root, "wid-a3", answer="leave this card for now", run_id="telegram")
    card_move(root, "wid-a3", "backlog", note="leave this card for now", run_id="run-1")


def test_go_ahead_to_a_set_aside_card_moves_it_from_backlog_to_todo(tmp_path) -> None:
    # a human who reads only Telegram had no way to make a set-aside card ready again:
    # only `card move <id> --to todo` in a shell moved it
    _set_aside(tmp_path)
    before = card_show(tmp_path, "wid-a3")
    assert before.stack == Stack.BACKLOG
    out: list[str] = []

    fake = _poll(tmp_path, [_message("go ahead now", reply_to=SET_ASIDE_ACK)], out)

    card = card_show(tmp_path, "wid-a3")
    assert card.stack == Stack.TODO
    assert card.position == before.position
    decision = card.decisions[-1]
    assert decision.answer == "leave this card for now"
    assert decision.reply == "go ahead now"
    assert card.history[:-1] == before.history
    row = card.history[-1]
    assert (row.run_id, row.from_, row.to) == ("telegram", "backlog", "todo")
    assert row.note == "reply to answered decision d1: go ahead now"
    assert decision.replied_at == row.at
    assert [t.id for t in thread_list(tmp_path)] == [THREAD]
    assert fake.sent == [
        (
            '01 Widgets\nrecorded as a reply to the answered question "Is the login in '
            'place?". The card you set aside is back in todo, ready for a wake.'
        )
    ]
    assert out[-1] == (
        "poll: 0 decisions answered, 1 replies to answered decisions recorded, "
        "0 replies to cancelled decisions recorded, 0 thread replies, 0 threads opened, "
        "0 not routed, 0 from other chats ignored"
    )


def test_go_ahead_to_a_set_aside_card_still_in_todo_leaves_it_ready(tmp_path) -> None:
    # answering an access question with 'leave this card for now' puts the card in
    # todo (docs/orchestrator/access-checks.md); before the orchestrator moves it to
    # backlog, or after a first "go ahead" has already moved it there (the test above),
    # a "go ahead" reply should say the card is ready, not that "it does not move"
    _board(tmp_path)
    _ask(tmp_path, "wid-a3", ACCESS_OPTIONS)
    card_answer(tmp_path, "wid-a3", answer="leave this card for now", run_id="telegram")
    before = card_show(tmp_path, "wid-a3")
    assert before.stack == Stack.TODO

    fake = _poll(tmp_path, [_message("go ahead", reply_to=SET_ASIDE_ACK)], [])

    card = card_show(tmp_path, "wid-a3")
    assert (card.stack, card.position) == (Stack.TODO, before.position)
    decision = card.decisions[-1]
    assert decision.answer == "leave this card for now"
    assert decision.reply == "go ahead"
    row = card.history[-1]
    assert (row.run_id, row.from_, row.to) == ("telegram", "todo", "todo")
    assert row.note == "reply to answered decision d1: go ahead"
    assert fake.sent == [
        (
            '01 Widgets\nrecorded as a reply to the answered question "Is the login in '
            'place?". The card you set aside stays in todo, ready for a wake.'
        )
    ]


@pytest.mark.parametrize("reply", ["not yet", "don't go ahead yet"])
def test_a_reply_that_does_not_go_ahead_leaves_a_set_aside_card_in_backlog(
    tmp_path, reply
) -> None:
    _set_aside(tmp_path)
    before = card_show(tmp_path, "wid-a3")

    fake = _poll(tmp_path, [_message(reply, reply_to=SET_ASIDE_ACK)], [])

    card = card_show(tmp_path, "wid-a3")
    assert (card.stack, card.position) == (Stack.BACKLOG, before.position)
    assert card.decisions[-1].reply == reply
    row = card.history[-1]
    assert (row.run_id, row.from_, row.to) == ("telegram", "backlog", "backlog")
    assert row.note == f"reply to answered decision d1: {reply}"
    assert fake.sent == [
        (
            '01 Widgets\nrecorded as a reply to the answered question "Is the login in '
            'place?". Its answer stays "leave this card for now" and the card stays '
            'in backlog; reply "go ahead" to make it ready again.'
        )
    ]


def test_go_ahead_moves_only_a_card_set_aside_in_backlog(tmp_path) -> None:
    # sys-x1's answer is "yes", not 'leave this card for now': a go-ahead is a reply
    # like any other, and a card in backlog for another reason stays there
    _answered(tmp_path)
    card_move(tmp_path, "sys-x1", "backlog", note="parked", run_id="run-1")

    _poll(tmp_path, [_message("go ahead", reply_to=FOLLOW_UP)], [])

    card = card_show(tmp_path, "sys-x1")
    assert card.stack == Stack.BACKLOG
    assert card.decisions[-1].reply == "go ahead"


def test_typing_the_other_option_asks_for_the_humans_own_words(tmp_path) -> None:
    _board(tmp_path)

    fake = _poll(
        tmp_path, [_message("Other: tell me", reply_to="[wid-a1] decision needed")], []
    )

    card = card_show(tmp_path, "wid-a1")
    assert card.stack == Stack.DECISION_NEEDED
    assert card.decisions[-1].answer is None
    assert "reply with your answer in your own words" in fake.sent[0]


def test_no_chat_id_reads_nothing(tmp_path) -> None:
    out: list[str] = []
    with patch("urllib.request.urlopen") as urlopen:
        code = inbox.poll(tmp_path, config={"notify": {}}, env=ENV, out=out.append)
    assert code == 1
    assert urlopen.call_count == 0
    assert "notify setup" in out[0]


def test_plan_step_polls_first_and_a_failed_poll_does_not_stop_it(tmp_path) -> None:
    from autonomouse import planner

    calls: list[str] = []

    def poll(out) -> None:
        calls.append("poll")
        raise RuntimeError("telegram down")

    out: list[str] = []
    code = planner.step(
        tmp_path,
        config=DEFAULTS,
        claude="claude-that-is-never-run",
        home=Home(tmp_path, "autonomouse-cli"),
        secrets=tmp_path / ".env",
        state_dir=tmp_path / "state",
        poll=poll,
        out=out.append,
    )

    assert code == 0
    assert calls == ["poll"]
    assert out == [
        "poll: failed: RuntimeError: telegram down",
        "no thread waits for the planner",
    ]
