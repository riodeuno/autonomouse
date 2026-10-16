"""Tests for `autonomouse review daily`: one seeded day of activity, every section filled."""

import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from autonomouse.board.history import progress_note
from autonomouse.review.daily import build_review, load_board

DAY = date(2026, 9, 21)
NOW = datetime(2026, 9, 22, 6, 0, tzinfo=UTC)


def _card(card_id: str, **overrides) -> dict:
    card = {
        "id": card_id,
        "project": "system",
        "title": card_id,
        "stack": "todo",
        "position": 0,
        "size": "s",
        "goal": "g",
        "limits": [],
        "test": "t",
        "depends_on": [],
        "assigned": {
            "model": None,
            "effort": None,
            "agent_type": None,
            "session_id": None,
            "pid": None,
            "picked_up_at": None,
        },
        "worktree": None,
        "branch": None,
        "base_branch": None,
        "pr": None,
        "phase": None,
        "iteration": 0,
        "handoff": {"last_step": None, "next_step": None, "updated_at": None, "run_id": None},
        "decisions": [],
        "history": [
            {"at": "2026-09-20T10:00:00Z", "run_id": "seed", "from": None, "to": "todo", "note": "seeded"}
        ],
        "created_at": "2026-09-20T10:00:00Z",
        "updated_at": "2026-09-21T12:00:00Z",
    }
    card.update(overrides)
    return card


def _h(at: str, from_: str | None, to: str, note: str, run: str = "20260921-080000-aaaa") -> dict:
    return {"at": at, "run_id": run, "from": from_, "to": to, "note": note}


def _seed_day(root: Path) -> None:
    """A day of activity: decisions answered and open, a card sent back, one cancelled,
    a merge conflict in a handoff, a run with Jev answers; plus an untouched card and an
    activity row on another day that must stay out of the report."""
    seeded = _h("2026-09-20T10:00:00Z", None, "todo", "seeded", "seed")
    cards = [
        _card(
            "sys-x1",
            stack="done",
            pr="https://github.com/o/r/pull/7",
            iteration=2,
            decisions=[
                {
                    "id": "d1",
                    "question": "Which notify channel?",
                    "options": ["telegram"],
                    "recommendation": "telegram",
                    "asked_at": "2026-09-21T08:30:00Z",
                    "answer": "telegram",
                    "answered_at": "2026-09-21T11:15:00Z",
                }
            ],
            history=[
                seeded,
                _h("2026-09-21T08:05:00Z", "todo", "in_progress", "started"),
                _h("2026-09-21T08:30:00Z", "in_progress", "decision_needed", "asked d1"),
                _h("2026-09-21T11:15:00Z", "decision_needed", "done", "answered --done"),
            ],
        ),
        _card(
            "sys-x2",
            stack="in_progress",
            phase="ci",
            iteration=4,
            decisions=[
                {
                    "id": "d2",
                    "question": "Which NAS path?",
                    "options": ["/a", "/b"],
                    "recommendation": "/a",
                    "asked_at": "2026-09-21T20:00:00Z",
                    "answer": None,
                    "answered_at": None,
                }
            ],
            history=[
                seeded,
                _h("2026-09-21T08:05:00Z", "todo", "in_progress", "started"),
                _h("2026-09-21T09:40:00Z", "in_progress", "todo", "reclaimed: run crashed"),
                _h("2026-09-21T10:00:00Z", "todo", "in_progress", "started again"),
            ],
            handoff={
                "last_step": "rebased on main; merge conflict in cli.py resolved",
                "next_step": "wait for checks",
                "updated_at": "2026-09-21T13:00:00Z",
                "run_id": "20260921-080000-aaaa",
            },
        ),
        _card(
            "sys-x3",
            stack="cancelled",
            history=[seeded, _h("2026-09-21T14:00:00Z", "todo", "cancelled", "superseded by sys-x2")],
        ),
        _card(
            "sys-x4",
            history=[_h("2026-09-21T07:00:00Z", None, "todo", "created that day, never worked")],
            decisions=[
                {
                    "id": "d3",
                    "question": "Hand-edited times?",
                    "options": ["a"],
                    "recommendation": None,
                    "asked_at": "2026-09-21T09:00:00Z",
                    "answer": "a",
                    "answered_at": "2026-09-21T08:00:00Z",
                }
            ],
        ),
        _card(
            "sys-x5",
            stack="todo",
            history=[seeded, _h("2026-09-22T01:00:00Z", "in_progress", "todo", "next day, not in report")],
        ),
    ]
    for card in cards:
        path = root / "projects" / "system" / "cards" / f"{card['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(card))
    runs = root / "runs"
    runs.mkdir()
    (runs / "20260921-080000-aaaa.json").write_text(
        json.dumps(
            {
                "id": "20260921-080000-aaaa",
                "project": "system",
                "started_at": "2026-09-21T08:00:00Z",
                "status": "stopped",
                "summary": "sys-x1 done",
                "jev": [
                    {"question": "go", "card": None, "source": "jev", "answer": "go", "confidence": 0.9, "applied": "go"},
                    {"question": "tier", "card": "sys-x1", "source": "orchestrator", "answer": "sonnet/high", "confidence": None, "applied": "sonnet/high"},
                ],
            }
        )
    )
    (runs / "20260920-080000-bbbb.json").write_text(
        json.dumps({"id": "20260920-080000-bbbb", "started_at": "2026-09-20T08:00:00Z", "jev": [
            {"question": "go", "card": None, "source": "jev", "answer": "skip", "confidence": 0.5, "applied": "skip"}
        ]})
    )


def _section(text: str, title: str) -> str:
    return text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0].strip()


def test_every_section_filled_from_a_seeded_day(tmp_path):
    _seed_day(tmp_path)
    text = build_review(load_board(tmp_path), DAY, NOW)

    decisions = _section(text, "Decisions asked")
    assert "| sys-x1 | Which notify channel? | 2026-09-21T08:30:00Z | telegram | 2h 45m |" in decisions
    assert "| sys-x2 | Which NAS path? | 2026-09-21T20:00:00Z | open | 10h 0m so far |" in decisions
    assert "| a | unknown: answered_at 2026-09-21T08:00:00Z is before asked_at |" in decisions

    sent_back = _section(text, "Cards sent back or cancelled")
    assert "| sys-x2 | 09:40 | in_progress → todo |" in sent_back
    assert "reclaimed: run crashed" in sent_back
    assert "| sys-x3 | 14:00 | todo → cancelled |" in sent_back
    assert "next day" not in sent_back

    iterations = _section(text, "Iteration counts")
    assert "| sys-x2 | 4 | in_progress | ci |" in iterations
    assert "| sys-x1 | 2 | done | - |" in iterations
    assert "sys-x4" not in iterations and "sys-x5" not in iterations

    clashes = _section(text, "Merge clashes")
    assert clashes.startswith("1 found.")
    assert "| sys-x2 | handoff last step | rebased on main; merge conflict in cli.py resolved |" in clashes

    jev = _section(text, "Jev answers against what happened next")
    assert "| 20260921-080000-aaaa | go | (wake) | jev | go | 0.9 | go | run stopped: sys-x1 done |" in jev
    assert "| tier | sys-x1 | orchestrator | sonnet/high | - | sonnet/high |" in jev
    assert "decision_needed → done 09-21 11:15. Now done, iteration 2, PR https://github.com/o/r/pull/7" in jev
    assert "bbbb" not in jev


def _decision(dec_id: str, question: str, asked_at: str, **overrides) -> dict:
    decision = {
        "id": dec_id,
        "question": question,
        "options": ["a"],
        "recommendation": None,
        "asked_at": asked_at,
        "answer": None,
        "answered_at": None,
    }
    decision.update(overrides)
    return decision


def _write_card(root: Path, card: dict) -> None:
    path = root / "projects" / "system" / "cards" / f"{card['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(card))


# Verbatim `question` text from three real decisions the system asked on 2026-09-22, which
# the human objected to as hands-on steps rather than strategic questions.
SYS_C11_QUESTION = (
    "What this card is for: the board screen (the 'autonomouse board' command) crashed "
    "with a 'No nodes match HeaderTitle' error when a scheduled run wrote to the board "
    "while the screen was open. What was done: the worker found the cause. On every board "
    "change, the screen rebuilt itself whole, including its title bar. When two rebuilds "
    "overlapped, the second one removed a title bar that was still waiting to update its "
    "text, and the update then crashed. The fix rebuilds only the six card columns and "
    "keeps the title bar, the card detail panel and the footer in place. A new automated "
    "test reproduces the crash on the old code every time and passes on the fixed code. "
    "All tests pass, and the pull request's automatic check is green: "
    "https://github.com/riodeuno/autonomouse/pull/76. What remains: the card's test ends "
    "with you watching the real screen. Check out the pull request's branch (card/sys-c11) "
    "or merge it, run 'autonomouse board' while a scheduled run is working, and watch it "
    "for five minutes. Did it stay up?"
)

SYS_I2_QUESTION = (
    "What this card is for: when you reply to the Telegram bot, your reply should reach "
    "the board. A reply to a decision answers it, a reply to a planning conversation "
    "continues it, and a fresh message opens a new planning conversation. What was done: "
    "the worker built this. A recorded sample of bot messages proves all three kinds work "
    "on a test board. All tests pass, and the pull request's automatic check is green: "
    "https://github.com/riodeuno/autonomouse/pull/71. Your earlier answer is carried out: "
    "the 4 old messages waiting on the bot were marked read without opening any "
    "conversation, and the bot's queue is empty. What remains: the card's test ends with "
    "one real round trip, which only you can do. First, merge the pull request. Then, on "
    "Telegram, reply to one decision message, reply to one planning-conversation message, "
    "and send one fresh message that replies to nothing. The fresh message opens a "
    "planning conversation, which starts a paid planner session. After that, run 'uv run "
    "--project /home/user/projects/autonomouse autonomouse notify poll' and look at the "
    "board: the decision is answered, the conversation has your reply, and one new "
    "conversation is open. Did all three work?"
)

SYS_I3_QUESTION = (
    "What this card is for: a planning conversation is a chat between you and a planner "
    "session that turns a request into cards. You should be able to step into that chat "
    "live in your terminal, with the same session and memory, and step out again so it "
    "continues unattended. What was done: the worker built the 'autonomouse plan join' "
    "command. It marks the conversation as joined, so no scheduled run touches it while "
    "you are in. It opens the planner's own session in your terminal, and when you exit "
    "it hands the conversation back to the scheduled runs. An automated test with a "
    "stand-in for claude proves each of those steps. All tests pass, and the pull "
    "request's automatic check is green: https://github.com/riodeuno/autonomouse/pull/69. "
    "What remains: the card's test ends with one real join, which only you can do. First, "
    "merge the pull request. Then run 'uv run -q --project /home/user/projects/"
    "autonomouse autonomouse plan join <conversation id>' on a real conversation (the "
    "board screen lists them). Check that the chat continues where the planner left it. "
    "Exit, and check that the conversation shows as waiting for the planner again, or as "
    "closed if the planner closed it. Did it work?"
)


def test_trivial_asks_flags_three_real_hands_on_questions(tmp_path):
    _write_card(
        tmp_path,
        _card(
            "sys-c11",
            decisions=[_decision("d1", SYS_C11_QUESTION, "2026-09-21T09:00:00Z")],
        ),
    )
    _write_card(
        tmp_path,
        _card(
            "sys-i2",
            decisions=[_decision("d1", SYS_I2_QUESTION, "2026-09-21T09:05:00Z")],
        ),
    )
    _write_card(
        tmp_path,
        _card(
            "sys-i3",
            decisions=[_decision("d1", SYS_I3_QUESTION, "2026-09-21T09:10:00Z")],
        ),
    )

    text = build_review(load_board(tmp_path), DAY, NOW)
    section = _section(text, "Asks the system should have settled")

    for card_id in ("sys-c11", "sys-i2", "sys-i3"):
        row = next(
            line for line in section.splitlines() if line.startswith(f"| {card_id} |")
        )
        assert " | none | " in row
        # a trigger name is present between the "asked" and "category named" columns
        assert row.count("|") == 5 + 1


def test_trivial_asks_names_the_category_and_skips_pure_strategic_questions(tmp_path):
    _write_card(
        tmp_path,
        _card(
            "sys-y1",
            decisions=[
                _decision(
                    "d1",
                    "This is a credential that only you can supply. Run "
                    "'export API_KEY=xyz' to finish setup.",
                    "2026-09-21T09:00:00Z",
                )
            ],
        ),
    )
    _write_card(
        tmp_path,
        _card(
            "sys-y2",
            decisions=[
                _decision(
                    "d1",
                    "Which cloud region should new workers deploy in, us-east or eu-west?",
                    "2026-09-21T09:05:00Z",
                )
            ],
        ),
    )

    text = build_review(load_board(tmp_path), DAY, NOW)
    section = _section(text, "Asks the system should have settled")

    assert "| sys-y1 |" in section
    assert "| credential |" in section
    assert "sys-y2" not in section


def test_trivial_asks_counts_one_of_each_kind_rules_item_8_lists(tmp_path):
    """docs/rules.md item 8 (at PR 192) names five kinds only the human decides: a
    credential, a spend, a fork in the goal, a judgment of taste, and a goal sentence the
    human has not confirmed. One hands-on-worded ask per kind must land under that kind,
    not under 'none'."""
    kinds = {
        "sys-k1": (
            (
                "This is a credential that only you can supply. Run 'export API_KEY=xyz' "
                "to finish setup."
            ),
            "credential",
        ),
        "sys-k2": (
            (
                "This is a spend the team must approve. Run the payment script to send "
                "fifty dollars."
            ),
            "spend",
        ),
        "sys-k3": (
            (
                "This is a fork in the goal between two designs. Run the deploy script "
                "for design B."
            ),
            "fork in the goal",
        ),
        "sys-k4": (
            (
                "This is a judgment of taste about the color scheme. Run the theme "
                "script with blue."
            ),
            "taste",
        ),
        "sys-k5": (
            (
                "This asks about a goal sentence the human has not confirmed yet. Run "
                "the intake script to record the answer."
            ),
            "goal sentence",
        ),
    }
    for i, (card_id, (question, _category)) in enumerate(kinds.items()):
        _write_card(
            tmp_path,
            _card(
                card_id,
                decisions=[_decision("d1", question, f"2026-09-21T09:{i:02d}:00Z")],
            ),
        )

    text = build_review(load_board(tmp_path), DAY, NOW)
    section = _section(text, "Asks the system should have settled")

    for card_id, (_question, category) in kinds.items():
        row = next(
            line for line in section.splitlines() if line.startswith(f"| {card_id} |")
        )
        assert f" | {category} | " in row


def test_trivial_asks_lists_a_note_that_already_records_the_finding(tmp_path):
    seeded = _h("2026-09-20T10:00:00Z", None, "todo", "seeded", "seed")
    _write_card(
        tmp_path,
        _card(
            "sys-y3",
            history=[
                seeded,
                _h(
                    "2026-09-21T09:00:00Z",
                    "todo",
                    "todo",
                    "trivial ask: the worker asked me to watch the screen, but it could "
                    "have polled the health endpoint itself.",
                ),
            ],
        ),
    )

    text = build_review(load_board(tmp_path), DAY, NOW)
    section = _section(text, "Asks the system should have settled")

    assert "| sys-y3 |" in section
    assert "recorded as a trivial ask" in section


def test_clashes_count_a_next_step_once_not_from_history_and_handoff_both(tmp_path):
    """`card progress --next-step "merge the conflict"` writes a history row whose note
    ends with that same text (`board.history.progress_note`) and sets `handoff.next_step`
    to it. The section quotes that one clash once, from the history row, not again from
    the handoff, which would double-count it."""
    at = "2026-09-21T09:00:00Z"
    _write_card(
        tmp_path,
        _card(
            "sys-y4",
            stack="in_progress",
            history=[
                _h(at, "in_progress", "in_progress", progress_note([], "merge the conflict"))
            ],
            handoff={
                "last_step": None,
                "next_step": "merge the conflict",
                "updated_at": at,
                "run_id": "20260921-080000-aaaa",
            },
        ),
    )

    text = build_review(load_board(tmp_path), DAY, NOW)
    clashes = _section(text, "Merge clashes")

    assert clashes.startswith("1 found.")
    assert "| sys-y4 | history 09:00 | progress: next_step: merge the conflict |" in clashes
    assert "| sys-y4 | handoff next step |" not in clashes


def test_an_answer_outside_the_listed_options_is_counted(tmp_path):
    options = ["$10 a day", "$50 a day", "other: tell me"]
    cards = {
        # picked none of the listed options: the one the report counts
        "sys-o1": ("raise it to 20 dollars", "2026-09-21T09:00:00Z"),
        # a listed option, typed with other case and spacing: not counted
        "sys-o2": ("$50  A DAY", "2026-09-21T09:00:00Z"),
        # outside the options, but answered on another day: not counted
        "sys-o3": ("something else", "2026-09-22T09:00:00Z"),
    }
    for card_id, (answer, answered_at) in cards.items():
        decision = _decision(
            "d1",
            "Which spend limit?",
            "2026-09-21T08:00:00Z",
            options=options,
            answer=answer,
            answered_at=answered_at,
        )
        _write_card(tmp_path, _card(card_id, decisions=[decision]))

    text = build_review(load_board(tmp_path), DAY, NOW)
    section = _section(text, "Answers outside the listed options")

    assert section.startswith("1 found.")
    assert (
        "| sys-o1 | Which spend limit? | $10 a day; $50 a day | raise it to 20 dollars |"
        in section
    )
    assert "sys-o2" not in section and "sys-o3" not in section


def test_quiet_day_says_none(tmp_path):
    _seed_day(tmp_path)
    text = build_review(load_board(tmp_path), date(2026, 9, 10), NOW)
    assert _section(text, "Decisions asked").strip() == "None."
    assert _section(text, "Merge clashes").startswith("0 found.")
    assert _section(text, "Answers outside the listed options").startswith("0 found.")
    assert _section(text, "Iteration counts").strip() == "No card was touched."


def test_cli_writes_the_file(tmp_path):
    _seed_day(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "review", "daily", "--date", "2026-09-21"],
        env={**os.environ, "AUTONOMOUSE_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    path = tmp_path / "reviews" / "2026-09-21.md"
    assert result.stdout.strip() == str(path)
    assert path.read_text().startswith("# Daily review — 2026-09-21\n")
