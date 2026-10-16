"""What the human reads on Telegram (sys-e11): a decision's brief, the planner's messages
and its closing summary name no board id, no code term and no command; each reads as
the context, then the question with its options numbered and the recommendation, then one
sentence on how to answer; and each uses Telegram's rich text, the project name and the
question in bold.

Telegram is the fake from `test_telegram_references`: it keeps every sendMessage body and
serves hand-built getUpdates messages. The board is a temporary folder; no real message
is sent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import test_telegram_references
from autonomouse import controller, human_messages
from autonomouse.board import references
from autonomouse.board.commands import (
    card_answer,
    card_ask,
    card_cancel,
    card_new,
    card_show,
    project_new,
)
from autonomouse.board.plans import (
    Author,
    Message,
    Thread,
    ThreadStatus,
    save_thread,
    thread_add_cards,
    thread_close,
)
from autonomouse.board.runs import RunKind, run_start
from autonomouse.board.schema import Decision, Stack
from autonomouse.board.store import load_card, save_card
from autonomouse.config import load_config
from autonomouse.human_messages import with_bold
from autonomouse.jev.client import ChoiceAnswer
from test_telegram_references import THREAD, _cli, _message, _poll

REPO = Path(__file__).resolve().parents[1]

# The board with projects widgets and gadgets, card wid-a1 and planning thread THREAD, and
# the fake Telegram every send goes through.
root = test_telegram_references.root
telegram = test_telegram_references.telegram

# What no text the human reads may hold. The first group is a board id, the second a
# code term, the third a command to type.
BOARD_ID = {
    "card id": r"\b[a-z0-9]+-[a-z][0-9]+\b",
    "thread id": r"\bplan-\d{8}-\d{6}-[0-9a-f]{4}\b",
    "run id": r"\b\d{8}-\d{6}-[0-9a-f]{4}\b",
    "bracketed tag": r"\[[^\]\n]*\]",
    "card number": r"\bcard \d+\b",
    # An absolute path (sys-e13): a `/` not itself part of `://`, then one path segment or
    # more, the board's own runs folder and the run record's file included.
    "file path": r"(?<![\w/:])/(?:[\w.-]+/)+[\w.-]+",
}
CODE_TERM = {
    "file extension": r"\.(?:py|md|json|jsonl|toml|sh|txt|ya?ml)\b",
    "backtick identifier": r"`[^`\n]+`",
    "snake_case": r"\b_*[a-z0-9]+(?:_[a-z0-9]+)+\b",
    "dotted token": r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*",
    "function call": r"\b\w+\(\)",
}
COMMAND = {
    "autonomouse command": (
        r"(?i)\bautonomouse (?:board|card|config|github|init|jev|list|log|notify"
        r"|plan|project|release|review|run|show|timer)\b"
    ),
    "subcommand": r"\b(?:card (?:answer|ask|move|new)|plan (?:close|open|reply|say)|notify poll)\b",
}
BANNED = {**BOARD_ID, **CODE_TERM, **COMMAND}


def _found(text: str, patterns: dict[str, str]) -> dict[str, list[str]]:
    """Each pattern that matches `text`, with what it matched."""
    found = {name: re.findall(pattern, text) for name, pattern in patterns.items()}
    return {name: hits for name, hits in found.items() if hits}


# (a) the decision brief card ask sends -------------------------------------------------

BRIEF = (
    "Sign-ups & renewals both charge the saved card. A daily spend limit stops a "
    "fault from charging it again and again."
)
QUESTION = "Which daily spend limit should the account have?"


def _card_in_progress(root: Path, card_id: str) -> None:
    card_new(root, card_id=card_id, project="widgets", title="t", goal="g", test="t")
    path = root / "projects" / "widgets" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    save_card(path, card)


def _ask(root: Path, options: list[str], card_id: str = "wid-a1") -> None:
    card_ask(
        root,
        card_id,
        brief=BRIEF,
        question=QUESTION,
        options=options,
        recommendation=options[0],
    )


def test_the_brief_reads_question_context_options_recommendation_then_how_to_answer(
    root, telegram
) -> None:
    _ask(root, ["$10 a day", "$50 a day"])

    [body] = telegram.sent
    assert body["parse_mode"] == "HTML"
    assert body["text"] == (
        "<b>01 Widgets</b>\n"
        "<b>Which daily spend limit should the account have?</b>\n"
        "\n"
        "Sign-ups &amp; renewals both charge the saved card. A daily spend limit stops a "
        "fault from charging it again and again.\n"
        "\n"
        "1. $10 a day\n"
        "2. $50 a day\n"
        "3. other: tell me\n"
        "Recommendation: $10 a day\n"
        "\n"
        "Reply to this message with an option number or your own answer, or answer on the "
        "board screen; a reply of 'done' means the work is finished and needs nothing more."
    )
    assert _found(body["text"], BANNED) == {}
    assert "wid-a1" not in body["text"]


def test_a_brief_keeps_its_own_paragraphs_under_the_bold_question() -> None:
    """sys-w40: the owner reads the decision on a phone. The question is the bold heading
    line; the brief's labelled parts keep their line breaks and one blank line between
    them (trailing spaces and extra blank lines dropped); the options follow, one per
    line."""
    decision = Decision(
        id="d1",
        question="Should the card's test accept a missing check-run read?",
        options=["Change the test line", "Switch to a classic token", "other: tell me"],
        recommendation="Change the test line",
        asked_at="2026-09-29T11:00:00Z",
        answer=None,
        answered_at=None,
        brief=(
            "What happened: setup's access check is built.  \n"
            "One permission reads as missing.\n"
            "\n\n\n"
            "Why it needs you: the test and the token disagree.\n"
            "\n"
            "What each option costs: a test line, or a token change.\n"
        ),
    )

    assert human_messages.decision_brief(decision) == (
        "<b>Should the card's test accept a missing check-run read?</b>\n"
        "\n"
        "What happened: setup's access check is built.\n"
        "One permission reads as missing.\n"
        "\n"
        "Why it needs you: the test and the token disagree.\n"
        "\n"
        "What each option costs: a test line, or a token change.\n"
        "\n"
        "1. Change the test line\n"
        "2. Switch to a classic token\n"
        "3. other: tell me\n"
        "Recommendation: Change the test line\n"
        "\n"
        "Reply to this message with an option number or your own answer, or answer on the "
        "board screen; a reply of 'done' means the work is finished and needs nothing more."
    )


def test_the_brief_is_routed_by_its_number_alone(root, telegram) -> None:
    """The card's id goes to notify as `about`, for the register, never into the text;
    the human's reply to the brief still answers the card."""
    _ask(root, ["$10 a day", "$50 a day"])
    assert [(r.number, r.about) for r in references.load(root)] == [(1, "wid-a1")]

    _poll(root, telegram, _message("1", 1, reply_to=1001))

    assert card_show(root, "wid-a1").decisions[-1].answer == "$10 a day"


def test_the_brief_says_done_picks_the_option_named_done(root, telegram) -> None:
    _ask(root, ["Done", "leave this card for now"])

    closing = telegram.texts[0].split("\n\n")[-1]
    assert closing == (
        "Reply to this message with an option number or your own answer, or answer on the "
        "board screen; a reply of 'done' picks option 1."
    )


def test_the_follow_ups_name_no_card_run_or_thread_id(root, telegram) -> None:
    """An orchestrator's answer, and a cancel whose reason names another card: neither
    the run id nor the card ids reach the human."""
    run_id = "20260927-075439-2ee2"
    _ask(root, ["$10 a day", "$50 a day"])
    card_answer(root, "wid-a1", answer="$10 a day", run_id=run_id)
    _card_in_progress(root, "wid-a2")
    _ask(root, ["$10 a day", "$50 a day"], card_id="wid-a2")
    card_cancel(root, "wid-a2", reason="superseded by wid-a1", run_id=run_id)

    answered, cancelled = telegram.texts[1], telegram.texts[3]
    assert answered == (
        "<b>02 Widgets</b>\n"
        "<b>Answered: Which daily spend limit should the account have?</b>\n"
        "Answer: $10 a day\n"
        "Answered by: the orchestrator\n"
        "The card is now in the top of todo."
    )
    assert cancelled == (
        "<b>04 Widgets</b>\n"
        "<b>No longer needed: Which daily spend limit should the account have?</b>\n"
        "The card was cancelled by the orchestrator."
    )
    for text in telegram.texts:
        assert _found(text, BANNED) == {}
        assert run_id not in text


# (b) the planner's closing summary -------------------------------------------------------

# A card's real goal, as the planner reads it on the card (sys-b18 on the system board),
# and the sentence the closing summary gives the human for it. A reviewer checks the
# standard here by eye: the goal is written for the worker, the sentence for someone who
# has not read the code.
B18_GOAL = (
    "The summary of a run record that src/autonomouse/jev/questions.py builds for Jev's "
    "situations (the keys of claude's result it keeps, line 209 today) keeps `is_error`, "
    "`stop_reason`, `num_turns`, `total_cost_usd` and `result_tail` and drops "
    "`duration_ms`: the summary already carries the record's `started_at` and `ended_at`, "
    "and that pair is a session's duration, while claude's `duration_ms` measured 1.9 "
    "minutes against 53 minutes of wall clock in the rehearsal (the record never had the "
    "field: `result` holds claude's JSON whole and stays so, and the one reader is this "
    "summary)."
)
B18_SENTENCE = (
    "The automatic review of each wake stops quoting how long a session took by Claude's "
    "own timer, which read 2 minutes for a session that ran 53, and goes by when the "
    "session started and ended instead."
)

# The part of sys-t19's real goal that says what changes.
T19_GOAL = (
    "a card that depends on itself is never ready, never runs, and nothing says why. "
    "_check_depends_on (src/autonomouse/board/commands.py:90-103) refuses commas and "
    "unknown ids but not the card's own id, which passes because it is on the board "
    "(:771) or in the import (:439). Refuse card_id inside depends_on with a message in "
    "the same form as the unknown-id message."
)
T19_SENTENCE = (
    "The board refuses a card that is set to wait for itself, and says why, so such a "
    "card no longer sits forever without starting and without a word to you."
)

CLOSING_SUMMARY = (
    "The plan for Autonomouse is finished. Two pieces of work are ready for the next "
    "wake:\n"
    "\n"
    f"• {B18_SENTENCE}\n"
    f"• {T19_SENTENCE}"
)


def test_the_goals_hold_the_code_terms_the_summary_must_not() -> None:
    """The fixture's goals trip the patterns, so the summary's clean result means
    something."""
    assert set(_found(B18_GOAL, CODE_TERM)) >= {
        "file extension",
        "backtick identifier",
        "snake_case",
    }
    assert set(_found(T19_GOAL, CODE_TERM)) >= {"file extension", "snake_case"}
    assert _found("sys-b18 and sys-t19", BOARD_ID) == {"card id": ["sys-b18", "sys-t19"]}


def test_the_closing_summary_describes_each_card_in_plain_words(
    root, telegram, monkeypatch
) -> None:
    project_new(
        root,
        slug="system",
        name="Autonomouse",
        goal="g",
        repo="https://github.com/test/autonomouse",
        repo_path="/home/test/autonomouse",
        default_branch="main",
        priority=1,
    )
    thread_id = "plan-20260927-130000-beef"
    save_thread(
        root,
        Thread(
            id=thread_id,
            project="system",
            opened_at="2026-09-27T13:00:00Z",
            session_id="s-2",
            status=ThreadStatus.WAITING_PLANNER,
            messages=[Message("2026-09-27T13:00:00Z", Author.HUMAN, "two small fixes")],
        ),
    )
    for card_id, goal in (("sys-b18", B18_GOAL), ("sys-t19", T19_GOAL)):
        card_new(root, card_id=card_id, project="system", title="t", goal=goal, test="t")
    thread_add_cards(root, thread_id, ["sys-b18", "sys-t19"])

    # The planner's close: `plan close`, then `notify --about` with the same text.
    thread_close(root, thread_id, CLOSING_SUMMARY)
    _cli(monkeypatch, "notify", "--about", thread_id, CLOSING_SUMMARY)

    [text] = telegram.texts
    assert text == f"<b>01 Autonomouse</b>\n{CLOSING_SUMMARY}"
    lines = text.splitlines()
    assert f"• {B18_SENTENCE}" in lines
    assert f"• {T19_SENTENCE}" in lines
    assert _found(text, BANNED) == {}


# (c) the planner's question, sent with its question in bold ----------------------------

PLANNER_QUESTION = (
    "You asked for an export of the board that leaves out cancelled work. Today the "
    "export writes every card, so work nobody will do shows up in the file you share.\n"
    "\n"
    "Should the export leave out cancelled cards?\n"
    "1. Leave them out\n"
    "2. Keep them & mark them cancelled\n"
    "3. other: tell me\n"
    "My recommendation: 1, because a cancelled card is work nobody will do.\n"
    "\n"
    "Answer by replying to this message on Telegram or on the board screen."
)


def test_the_planners_question_goes_out_as_rich_text(root, telegram, monkeypatch) -> None:
    _cli(
        monkeypatch,
        "notify",
        "--about",
        THREAD,
        "--bold",
        "Should the export leave out cancelled cards?",
        PLANNER_QUESTION,
    )

    [body] = telegram.sent
    assert body["parse_mode"] == "HTML"
    text = body["text"]
    context, question, closing = text.split("\n\n")
    assert context.splitlines()[0] == "<b>01 Gadgets</b>"
    assert question.splitlines() == [
        "<b>Should the export leave out cancelled cards?</b>",
        "1. Leave them out",
        "2. Keep them &amp; mark them cancelled",
        "3. other: tell me",
        "My recommendation: 1, because a cancelled card is work nobody will do.",
    ]
    assert closing == "Answer by replying to this message on Telegram or on the board screen."
    assert _found(text, BANNED) == {}


def test_the_brief_is_rich_text(root, telegram) -> None:
    _ask(root, ["$10 a day", "$50 a day"])

    heading, context, options, closing = telegram.texts[0].split("\n\n")
    assert heading == f"<b>01 Widgets</b>\n<b>{QUESTION}</b>"
    assert context.startswith("Sign-ups &amp; renewals")
    assert [line.split(". ", 1)[0] for line in options.splitlines()[:3]] == ["1", "2", "3"]
    assert closing.startswith("Reply to this message")


def test_bold_is_found_in_the_text_as_written_never_inside_an_escaped_character() -> None:
    assert with_bold("Fish & chips: is amp the word?", "amp") == (
        "Fish &amp; chips: is <b>amp</b> the word?"
    )
    assert with_bold("Keep <b> or & as typed?", "<b> or &") == (
        "Keep <b>&lt;b&gt; or &amp;</b> as typed?"
    )


def test_bold_text_the_message_does_not_hold_sends_nothing(
    root, telegram, monkeypatch, capsys
) -> None:
    with pytest.raises(SystemExit) as exit_:
        _cli(monkeypatch, "notify", "--about", THREAD, "--bold", "Which one?", "hello")

    assert exit_.value.code == 1
    assert "not in the message" in capsys.readouterr().err
    assert telegram.sent == []
    assert references.load(root) == []


def test_bold_without_about_is_refused(telegram, monkeypatch, capsys) -> None:
    with pytest.raises(SystemExit) as exit_:
        _cli(monkeypatch, "notify", "--bold", "hello", "hello")

    assert exit_.value.code == 1
    assert "--bold needs --about" in capsys.readouterr().err
    assert telegram.sent == []


# (d) the charters that write to the human --------------------------------------------

DESCRIBE_BY_WHAT_IT_DOES = (
    "Describe each card or planning thread to the human by what its work changes and for "
    "whom, in plain words that stand on their own for a reader who has not read the code "
    '("it stops the wake report from naming a card\'s internal id"), never by its board id '
    '(such as `<prefix>-<letter><n>`) or by a file, function, command or other code term '
    '("it updates board/commands.py"); and write no command into anything the human reads.'
)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _section(path: str, heading: str) -> str:
    text = (REPO / path).read_text()
    start = text.index(f"\n## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else None]


def _close_rule() -> str:
    text = (REPO / "docs/planner.md").read_text()
    start = text.index("\n- **Close:**")
    return text[start : text.index("\n\n", start)]


@pytest.mark.parametrize(
    "part",
    ["planner Asking", "planner Close", "super-orchestrator Report"],
)
def test_each_charter_that_writes_to_the_human_holds_the_plain_words_rule(part) -> None:
    text = {
        "planner Asking": lambda: _section("docs/planner.md", "Asking"),
        "planner Close": _close_rule,
        "super-orchestrator Report": lambda: _section("docs/super-orchestrator.md", "Report"),
    }[part]()

    assert _one_line(DESCRIBE_BY_WHAT_IT_DOES) in _one_line(text)
    assert "plan reply" not in text
    assert "card move" not in text


# (e) the wake-failure alert (sys-e13) --------------------------------------------------
#
# The run controller's alert, sent through `autonomouse.controller._recover` (a session Jev
# says needs a human). It is driven directly, past the heavy machinery `autonomouse run`
# itself needs (a fake `claude`, a fake Jev over HTTP): `_recover` is given a `Jev` whose
# `ask` returns the answer directly. It goes out through the fake Telegram
# `test_telegram_references` gives every test in this file, exactly as a real send would.

WAKE_FAILURE_ERROR = "the session's API call kept timing out"


def _raised_wake_failure(root: Path) -> str:
    """A session that failed three times in a row, and a Jev answer of `raise` at full
    confidence: `_recover`'s one path that sends the wake-failure alert. Returns the run
    id the alert must not name."""
    run_id = "20260927-101112-ab12"
    run_start(
        root,
        run_id=run_id,
        session_id="s-fail",
        pid=4242,
        model="m",
        kind=RunKind.WAKE,
        project="widgets",
    )
    started = controller.Started(
        run_id=run_id,
        session_id="s-fail",
        resumed=None,
        proc=None,
        result_path=Path("/no-such-result"),
        stderr_path=Path("/no-such-stderr"),
        wake_log=root / "wake-log.md",
    )
    jev = controller.Jev(
        config=load_config(),
        ask=lambda *a, **kw: {"recover": ChoiceAnswer("raise", {"raise": 0.9}, 0.9)},
    )
    controller._recover(
        root,
        started,
        project="widgets",
        code=1,
        result={"result": WAKE_FAILURE_ERROR},
        retries=2,
        jev=jev,
        secrets=root / "no-such-secrets",
        out=lambda _line: None,
    )
    return run_id


def test_the_wake_failure_alert_names_no_run_id_or_file_path(root, telegram) -> None:
    run_id = _raised_wake_failure(root)

    [text] = telegram.texts
    assert text == (
        "<b>The wake on widgets failed and needs you</b>\n"
        f"What went wrong: session exited 1: {WAKE_FAILURE_ERROR}\n"
        "It has failed 3 times in a row."
    )
    assert _found(text, BANNED) == {}
    assert run_id not in text
