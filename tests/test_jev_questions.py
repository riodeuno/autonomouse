"""Tests for autonomouse.jev.questions: known-answer replays, every fallback path, the state.

Since sys-j6 only `recover` has a confidence floor, and its fallback is `retry`; `size` applies
any size Jev names, whatever its confidence; `go` has no floor and no fallback, and the wake
records it as advice (autonomouse.jev.wake).

The replays read tests/fixtures/jev/situations.json, written by one live run of
tests/record_jev_situations.py. Each replay first checks that the request the catalogue builds
today is byte-for-byte the recorded one: a changed rubric or state builder fails here until the
situations are recorded again, so a saved reply is never replayed against a question it did not
answer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomouse.config import DEFAULTS
from autonomouse.jev.client import (
    KEY_ENV_VAR,
    ChoiceAnswer,
    NoulAnswer,
    ScoreAnswer,
    ask,
)
from autonomouse.jev.questions import (
    LAST_RUNS,
    PLANNER_SESSION,
    PLANNER_SESSION_DESCRIPTIONS,
    RECOVER,
    RESULT_TEXT_TAIL,
    SIZE_DESCRIPTIONS,
    STACKS,
    TIER_RULE,
    Verdict,
    base_state,
    card_state,
    decide_all,
    go,
    planner_state,
    questions_body,
    recover_state,
    run_outcome,
    size,
)
from autonomouse.jev.wake import GO_ADVICE, consult
from jev_situations import CLEAN_RUNS, DONE, NOW, SITUATIONS, card, run

RECORDED = json.loads(
    (Path(__file__).parent / "fixtures" / "jev" / "situations.json").read_text()
)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


# ---------------------------------------------------------------------------- replays


def test_every_situation_is_recorded_and_each_question_has_three_to_seven():
    assert sorted(RECORDED) == sorted(s.name for s in SITUATIONS)
    per_question: dict[str, int] = {}
    for s in SITUATIONS:
        per_question[s.question] = per_question.get(s.question, 0) + 1
    assert set(per_question) == {"go", "size", "recover"}  # no fits since sys-i29
    assert all(3 <= n <= 7 for n in per_question.values()), per_question
    assert [s.expected for s in SITUATIONS if s.question == "size"].count("split") == 2


@pytest.mark.parametrize("situation", SITUATIONS, ids=lambda s: s.name)
def test_recorded_reply_gives_the_known_answer(situation, monkeypatch):
    monkeypatch.setenv(KEY_ENV_VAR, "sk-test-key")
    record = RECORDED[situation.name]
    body = json.dumps(record["response"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)) as urlopen:
        answers = ask(situation.state, questions_body(situation.entries))

    sent = json.loads(urlopen.call_args[0][0].data)
    assert sent == record["request"], "request changed since recording; re-record this one"

    verdict = situation.entry.decide(answers[situation.entry.key])
    assert verdict.fallback_reason is None
    assert verdict.pick == situation.expected
    assert verdict.applied == situation.expected


# ---------------------------------------------------------------------------- fallbacks

FALLBACKS = [
    (size("int-j2", "m"), "m"),
    (RECOVER, "retry"),
    (PLANNER_SESSION, "complex"),
]


@pytest.mark.parametrize(("entry", "safe"), FALLBACKS, ids=lambda x: getattr(x, "key", x))
def test_no_answer_falls_back_to_the_safe_option(entry, safe):
    verdict = entry.decide(None)
    assert verdict == Verdict(entry.key, None, None, safe, "no answer")


def test_recover_below_its_floor_falls_back_to_retry():
    """sys-j6: `recover`'s fallback is `retry`, a new session from the notes, not `raise`."""
    probabilities = {"raise": 0.59, "retry": 0.41}
    verdict = RECOVER.decide(ChoiceAnswer("raise", probabilities, 0.59))
    assert verdict.applied == "retry"
    assert verdict.pick == "raise"
    assert verdict.fallback_reason == "confidence 0.59 below floor 0.60"
    assert verdict.probabilities == probabilities  # kept, so the low answer can be read later


def test_recover_at_its_floor_applies_the_pick():
    probabilities = {"raise": 0.6, "retry": 0.4}
    assert RECOVER.decide(ChoiceAnswer("raise", probabilities, 0.6)) == Verdict(
        "recover", "raise", 0.6, "raise", None, probabilities
    )


def test_a_size_at_confidence_0_2_is_applied():
    """sys-j6: `size` has no floor. Any size Jev names applies, whatever its confidence."""
    probabilities = {"xs": 0.2, "s": 0.19, "m": 0.19, "split": 0.42}
    verdict = size("int-j2", "m").decide(ChoiceAnswer("xs", probabilities, 0.2))
    assert verdict == Verdict("size:int-j2", "xs", 0.2, "xs", None, probabilities)


@pytest.mark.parametrize(("entry", "safe"), FALLBACKS, ids=lambda x: getattr(x, "key", x))
def test_pick_outside_the_options_falls_back(entry, safe):
    answer = ChoiceAnswer(pick="banana", probabilities={}, confidence=0.99)
    verdict = entry.decide(answer)
    assert verdict.applied == safe
    assert verdict.pick is None
    assert "not one of" in verdict.fallback_reason


@pytest.mark.parametrize("answer", [NoulAnswer(probability=0.9), ScoreAnswer(value=2.0)])
def test_non_choice_answer_falls_back(answer):
    verdict = size("sys-b4", "xs").decide(answer)
    assert verdict.applied == "xs"
    assert "expected a choice answer" in verdict.fallback_reason


def test_go_has_no_floor_and_no_fallback():
    """sys-j6: a low `go` is still Jev's answer, and an unusable one has no fallback; the
    wake records either as advice and never applies it (autonomouse.jev.wake)."""
    low = go(4).decide(ChoiceAnswer(pick="skip", probabilities={}, confidence=0.25))
    assert low == Verdict("go", "skip", 0.25, "skip", None, {})
    assert go(4).decide(None).applied is None


def test_go_question_names_the_wakes_own_interval_hours():
    """sys-t33: the go question's instructions describe wake.interval_hours, not a fixed
    four hours, so a project that changes the key gets advice built on its own cadence."""
    assert "wakes every four hours" in go(4).question.instructions
    assert "wakes every six hours" in go(6).question.instructions


# ---------------------------------------------------------------------------- run outcomes

_CLEAN = {"is_error": False, "stop_reason": "end_turn", "result": "Done."}


def _session(run_id="20260922-100000-aaaa", *, result=_CLEAN, status="stopped", **extra):
    return {"id": run_id, "session_id": f"s-{run_id}", "started_at": "2026-09-22T10:00:00Z",
            "ended_at": "2026-09-22T10:30:00Z", "status": status, "result": result,
            "end_reason": None, "jev": [], **extra}


def _jev_skip(run_id, *, applied="skip", reason="confidence 0.25 below floor 0.60",
              source="fallback", started_at="2026-09-22T11:00:00Z"):
    go = {"question": "go", "card": None, "source": source, "answer": "go",
          "confidence": 0.25, "applied": applied, "reason": reason}
    return {"id": run_id, "session_id": None, "started_at": started_at,
            "ended_at": started_at, "status": "stopped", "result": None, "end_reason": None,
            "jev": [go]}


@pytest.mark.parametrize(("run_record", "outcome"), [
    (_session(), "session ended cleanly"),
    (_session(result={"is_error": True, "stop_reason": "end_turn"}),
     "session failed (is_error; stop_reason end_turn)"),
    (_session(result=None, status="crashed", end_reason="crashed"),
     "session failed (no result; status crashed, end_reason crashed)"),
    (_session(result=None, status="alive", ended_at=None), "session still running"),
    # Its own SessionEnd hook recorded a finished last turn; the result was lost afterwards.
    (_session(result=None, status="taken_over", end_reason="completed"),
     "session ended cleanly"),
    (_session(result=None, status="taken_over", end_reason="interrupted"),
     "session failed (no result; status taken_over, end_reason interrupted)"),
])
def test_a_session_outcome_reads_the_session_only(run_record, outcome):
    assert run_outcome(run_record, []) == {"outcome": outcome}


def test_the_controllers_own_failures_are_not_the_sessions_outcome():
    """A clean session whose wake then failed to back up or notify still ended cleanly."""
    record = _session(
        summary="backup failed: remote rejected",
        notify_failures=[{"at": "2026-09-22T10:31:00Z", "text": "t", "error": "no chat id"}],
    )
    assert run_outcome(record, []) == {"outcome": "session ended cleanly"}


def _asked_in(run_id, *, answered_at):
    blocked = card("int-g1", project="integrations", stack="todo")
    blocked["decisions"] = [
        {"id": "d1", "question": "old", "options": ["a"], "recommendation": None,
         "asked_at": "2026-09-22T07:07:28Z", "answer": "a",
         "answered_at": "2026-09-22T09:00:00Z"},
        {"id": "d2", "question": "token?", "options": ["a"], "recommendation": None,
         "asked_at": "2026-09-22T10:20:00Z", "answer": None, "answered_at": answered_at},
    ]
    blocked["history"] = [
        {"at": "2026-09-22T07:07:28Z", "run_id": "an-older-run", "from": "in_progress",
         "to": "decision_needed", "note": "asked: old"},
        {"at": "2026-09-22T10:20:00Z", "run_id": run_id, "from": "in_progress",
         "to": "decision_needed", "note": "asked: token?"},
    ]
    return blocked


def test_a_run_that_needed_the_human_says_so_and_whether_it_was_answered():
    record = _session()
    answered = run_outcome(record, [_asked_in(record["id"], answered_at="2026-09-22T10:34:33Z")])
    assert answered == {
        "outcome": "session ended cleanly; a card needed the human, and the decision has "
        "since been answered",
        "needed_human": [{"card": "int-g1", "decision": "d2", "asked_at": "2026-09-22T10:20:00Z",
                          "answered_at": "2026-09-22T10:34:33Z"}],
    }
    still_open = run_outcome(record, [_asked_in(record["id"], answered_at=None)])
    assert still_open["outcome"].endswith("a decision is still unanswered")
    assert still_open["needed_human"][0]["answered_at"] is None


def test_last_runs_carry_the_outcome():
    record = _session()
    older = _session("20260922-080000-bbbb", started_at="2026-09-22T08:00:00Z")
    state = base_state(now=NOW, config=DEFAULTS, runs=[older, record, _jev_skip("r2")],
                       cards=[_asked_in(record["id"], answered_at="2026-09-22T10:34:33Z")])
    assert [r["outcome"] for r in state["last_runs"]] == [
        ("session ended cleanly; a card needed the human, and the decision has since been "
        "answered"),
        "session ended cleanly",
    ]
    assert "needed_human" not in state["last_runs"][1]
    assert state["wakes_since_last_session"] == 1  # the wake Jev skipped


def _refused(run_id, *, started_at):
    """A wake `autonomouse run` refused before it started a session (sys-j5)."""
    return {"id": run_id, "kind": "wake", "session_id": None, "started_at": started_at,
            "ended_at": started_at, "status": "stopped", "result": None, "end_reason": None,
            "jev": [], "summary": "refused: run - orchestrator.effort must be one of high",
            "refusals": [{"at": started_at, "command": "run", "reason": "orchestrator.effort"}]}


def test_wakes_that_started_no_session_never_push_a_session_out_of_last_runs():
    """Six sessions, the newest failed at 05:00; after it, four refused wakes and one Jev
    skipped; between two older sessions, one more refused wake. Jev still reads the five
    newest sessions, the failed one first, and counts the five wakes after it only."""
    failed = {"is_error": True, "stop_reason": "end_turn", "result": "API Error: 500"}
    sessions = [
        _session(f"20260922-0{h}0000-aaaa", started_at=f"2026-09-22T0{h}:00:00Z")
        for h in range(5)
    ] + [_session("20260922-050000-aaaa", started_at="2026-09-22T05:00:00Z", result=failed)]
    runs = [
        *sessions,
        _refused("20260922-043000-cccc", started_at="2026-09-22T04:30:00Z"),
        _refused("20260922-060000-cccc", started_at="2026-09-22T06:00:00Z"),
        _refused("20260922-070000-cccc", started_at="2026-09-22T07:00:00Z"),
        _jev_skip("20260922-080000-dddd", started_at="2026-09-22T08:00:00Z"),
        _refused("20260922-090000-cccc", started_at="2026-09-22T09:00:00Z"),
        _refused("20260922-100000-cccc", started_at="2026-09-22T10:00:00Z"),
    ]
    state = base_state(now=NOW, config=DEFAULTS, cards=[], runs=runs)
    assert [r["id"] for r in state["last_runs"]] == [
        "20260922-050000-aaaa", "20260922-040000-aaaa", "20260922-030000-aaaa",
        "20260922-020000-aaaa", "20260922-010000-aaaa",
    ]
    assert state["last_runs"][0]["outcome"] == "session failed (is_error; stop_reason end_turn)"
    assert state["wakes_since_last_session"] == 5


def test_with_no_session_yet_every_wake_is_counted():
    runs = [_refused("r1", started_at="2026-09-22T01:00:00Z"), _jev_skip("r2")]
    state = base_state(now=NOW, config=DEFAULTS, cards=[], runs=runs)
    assert state["last_runs"] == []
    assert state["wakes_since_last_session"] == 2


# ---------------------------------------------------------------------------- go is advice
#
# Before sys-j6 an unusable `go` fell back by the run records (`go_fallback`): `skip` after a
# failed session, while a usage limit was in force, or with nothing to work. Now nothing reads
# the run records to hold a wake back: the wake starts whatever Jev answered, and a usage limit
# is the controller's own check before any session starts (tests/test_run_usage_limit.py).


def _plan(runs, answers):
    return consult(
        now=NOW, config=DEFAULTS, cards=DONE + [card("sys-b4")], runs=runs, resume=[],
        ready=[card("sys-b4")], ask=lambda state, questions, *, timeout: answers,
    )


def test_go_falls_back_to_nothing_and_the_wake_starts_after_a_clean_session():
    runs = [_session(), _jev_skip("r2"), _jev_skip("r3", started_at="2026-09-22T11:05:00Z")]
    plan = _plan(runs, {})
    assert plan.batch == ["sys-b4"]
    go = plan.entries[0]
    assert (go["answer"], go["applied"], go["source"]) == (None, None, "jev")
    assert go["reason"] == f"no answer; {GO_ADVICE}"


def test_go_has_no_fallback_function_left():
    from autonomouse.jev import questions

    assert not hasattr(questions, "go_fallback")
    assert not hasattr(questions, "usage_limit_in_force")


def test_a_go_of_skip_at_confidence_0_9_with_nothing_run_yet_still_starts():
    plan = _plan([_jev_skip("r1")], {"go": ChoiceAnswer("skip", {"skip": 0.9}, 0.9)})
    assert plan.batch == ["sys-b4"]
    assert (plan.entries[0]["answer"], plan.entries[0]["applied"]) == ("skip", None)


def test_the_wake_starts_when_the_last_started_session_failed():
    older = _session("20260922-080000-aaaa")
    older["started_at"] = "2026-09-22T08:00:00Z"
    failed = _session(result=None, status="crashed")
    plan = _plan([older, failed, _jev_skip("r3")], {})
    assert plan.batch == ["sys-b4"]


@pytest.mark.parametrize("text", [
    "Claude usage limit reached. Your limit will reset at 2026-09-22T17:00:00Z.",
    "Claude AI usage limit reached|1790000000",
    "You've hit your limit",
])
def test_a_usage_limit_in_a_result_text_holds_no_plan_back(text):
    """The run records' usage limit is the controller's check (`usage_limit_in_force` in
    autonomouse.controller, over `usage_limit.reset_at`), never Jev's `go`."""
    record = _session(result={**_CLEAN, "result": text})
    plan = _plan([record], {"go": ChoiceAnswer("skip", {}, 0.95)})
    assert plan.batch == ["sys-b4"]
    assert plan.entries[0]["applied"] is None


def test_size_question_quotes_the_humans_rule_word_for_word():
    # sys-d23: the human's sentence (2026-09-22), pinned here as written; sys-i18 moved it
    # from the tier question to the size question, which replaced it.
    assert TIER_RULE == (
        "delegate to capable orchestrators and workers; do not cheap out on their "
        "capabilities, do not underestimate the tasks, do not overestimate what the agents can "
        "do, and do not overspend."
    )
    quote = f'"{TIER_RULE}"'
    instructions = size("x", "s").question.instructions
    assert quote in instructions
    assert "hardest decision" in instructions
    assert "a one-line fix whose cause must first be found is `s` or `m`" in instructions
    assert "`split` is not a degree of difficulty" in instructions
    recorded = [s.name for s in SITUATIONS if s.question == "size"]
    assert recorded
    for name in recorded:
        for question in RECORDED[name]["request"]["questions"].values():
            assert quote in question["instructions"], name


def test_decide_all_falls_back_for_a_question_missing_from_the_answers():
    entries = [go(4), size("sys-b4", "s")]
    answers = {"go": ChoiceAnswer(pick="go", probabilities={}, confidence=0.9)}
    verdicts = decide_all(entries, answers)
    assert verdicts["go"].pick == "go"
    assert verdicts["size:sys-b4"] == Verdict("size:sys-b4", None, None, "s", "no answer")


# ---------------------------------------------------------------------------- catalogue shape


def test_options_and_floors():
    assert go(4).options == ("go", "wait_30m", "skip")
    assert size("x", "s").options == ("xs", "s", "m", "split") == tuple(SIZE_DESCRIPTIONS)
    assert RECOVER.options == ("retry", "next_wake", "raise")
    assert PLANNER_SESSION.options == ("complex", "simple", "collect-user-intent")
    assert PLANNER_SESSION.options == tuple(PLANNER_SESSION_DESCRIPTIONS)
    # sys-j6: of the wake's questions only `recover` keeps a floor; `go` is advice and `size`
    # is Jev's answer. sys-v7: `planner_session` has its own floor, below which the session
    # runs at the `complex` tier.
    assert (go(4).floor, size("x", "s").floor, RECOVER.floor) == (None, None, 0.6)
    assert PLANNER_SESSION.floor == 0.5
    for entry in (go(4), size("x", "s"), RECOVER, PLANNER_SESSION):
        assert all(isinstance(r, str) and r for r in entry.question.criteria.values())


def test_per_card_questions_are_keyed_and_point_at_their_card():
    assert size("int-j2", "m").key == "size:int-j2"
    assert "`cards.int-j2`" in size("int-j2", "m").question.instructions
    body = questions_body([size("a", "s"), size("b", "m")])
    assert list(body) == ["size:a", "size:b"]


def test_size_is_the_one_per_card_question_and_split_means_larger_than_m():
    """sys-i29: Jev's per-card question is size alone; `split` is its fourth option. Since
    sys-j6 a low-confidence split is applied like any other size Jev names."""
    from autonomouse.jev import questions

    assert not hasattr(questions, "fits")
    split = size("sys-z1", "m").decide(ChoiceAnswer("split", {"split": 0.9}, 0.9))
    assert (split.applied, split.fallback_reason) == ("split", None)
    low = size("sys-z1", "m").decide(ChoiceAnswer("split", {"split": 0.4}, 0.4))
    assert (low.pick, low.applied, low.fallback_reason) == ("split", "split", None)
    assert "more than one worker turn" in SIZE_DESCRIPTIONS["split"]
    assert "Larger than `m`" in SIZE_DESCRIPTIONS["split"]


def test_the_planner_question_judges_the_kind_never_the_tier():
    """sys-v7: Jev only judges the kind of planner session; the config maps it to a tier."""
    instructions = PLANNER_SESSION.question.instructions
    assert f'"{TIER_RULE}"' in instructions
    assert not any(m in instructions for m in ("fable", "opus", "sonnet", "xhigh"))
    assert "only collects the human's intent" in PLANNER_SESSION_DESCRIPTIONS[
        "collect-user-intent"
    ]


def test_planner_state_is_the_thread_and_the_messages_its_next_session_answers():
    thread = {
        "id": "plan-20260928-040000-abcd",
        "project": "system",
        "opened_at": "2026-09-28T04:00:00Z",
        "session_id": "s-1",
        "status": "waiting_planner",
        "joined": None,
        "messages": [
            {"at": "t1", "from": "human", "text": "a request"},
            {"at": "t2", "from": "planner", "text": "a question"},
            {"at": "t3", "from": "human", "text": "yes"},
            {"at": "t4", "from": "human", "text": "and one more thing"},
        ],
        "cards_written": [],
        "tiers": [{"applied": "complex"}],
    }
    state = planner_state(thread, ["yes", "and one more thing"])
    assert state["session"] == "resume"
    assert state["new_messages"] == ["yes", "and one more thing"]
    assert set(state["thread"]) == {"id", "project", "status", "messages", "cards_written"}
    assert planner_state({**thread, "session_id": None}, [])["session"] == "new"


# ---------------------------------------------------------------------------- state


def test_base_state_carries_time_ceilings_counts_and_runs():
    state = base_state(now=NOW, config=DEFAULTS, cards=DONE, runs=CLEAN_RUNS)
    assert state["now"] == "2026-09-22T12:00:00Z"
    assert state["weekday"] == "Tuesday"
    # every ceiling but resume_tokens and retries_per_wake, which only the run controller
    # reads (sys-d15, sys-j6); the dead attempt and wake caps are gone (sys-s15, sys-j6)
    assert state["ceilings"] == {"workers_per_wake": DEFAULTS["ceilings"]["workers_per_wake"]}
    assert "iterations_per_card" not in state["ceilings"]
    assert "wakes_per_day" not in state["ceilings"]
    assert state["stack_counts"]["system"] == {**dict.fromkeys(STACKS, 0), "done": 3}
    assert state["stack_counts"]["integrations"]["done"] == 1
    assert [r["id"] for r in state["last_runs"]] == [r["id"] for r in reversed(CLEAN_RUNS)]


def test_base_state_converts_now_to_utc_and_refuses_naive_times():
    ist = timezone(timedelta(hours=5, minutes=30))
    state = base_state(
        now=datetime(2026, 9, 23, 2, 0, tzinfo=ist), config=DEFAULTS, cards=[], runs=[]
    )
    assert state["now"] == "2026-09-22T20:30:00Z"
    assert state["weekday"] == "Tuesday"
    with pytest.raises(ValueError):
        base_state(now=datetime(2026, 9, 22), config=DEFAULTS, cards=[], runs=[])  # noqa: DTZ001


def test_ready_cards_need_every_dependency_done_in_any_project():
    cards = DONE + [
        card("sys-x1", position=2, depends_on=("int-j1",)),  # dep done in another project
        card("sys-x2", position=1, depends_on=("sys-a1", "sys-x9")),  # one dep missing
        card("sys-x3", position=0, depends_on=("sys-x1",)),  # dep todo
        card("sys-x4", stack="backlog"),  # not todo
        card("sys-x5", position=1),  # no deps
    ]
    state = base_state(now=NOW, config=DEFAULTS, cards=cards, runs=[])
    assert [c["id"] for c in state["ready_cards"]] == ["sys-x5", "sys-x1"]
    assert set(state["ready_cards"][0]) == {"id", "project", "title", "size", "goal"}


def test_last_runs_keeps_the_five_newest_without_jev_log_and_with_a_trimmed_result():
    runs = [
        run(f"r{h}", started_at=f"2026-09-22T{h:02d}:00:00Z", ended_at=None,
            result_text="x" * 5000 + "limit resets at 13:00")
        for h in range(7)
    ]
    runs[3]["jev"] = [{"question": "go", "answer": "go"}]
    runs[4]["result"] = None
    state = base_state(now=NOW, config=DEFAULTS, cards=[], runs=runs)
    assert [r["id"] for r in state["last_runs"]] == ["r6", "r5", "r4", "r3", "r2"]
    assert len(state["last_runs"]) == LAST_RUNS
    assert all("jev" not in r for r in state["last_runs"])
    newest = state["last_runs"][0]["result"]
    assert newest["result_tail"].endswith("limit resets at 13:00")
    assert len(newest["result_tail"]) == RESULT_TEXT_TAIL
    assert set(newest) == {
        "is_error", "stop_reason", "num_turns", "total_cost_usd", "result_tail"
    }
    assert state["last_runs"][2]["result"] is None


def test_card_state_carries_the_whole_card_and_no_rubric():
    base = base_state(now=NOW, config=DEFAULTS, cards=DONE, runs=CLEAN_RUNS)
    judged = card("sys-b4", title="card move, card cancel")
    state = card_state(base, [judged])
    assert state == {**base, "cards": {"sys-b4": judged}}


def test_recover_state_carries_error_attempts_and_reset_time():
    base = base_state(now=NOW, config=DEFAULTS, cards=[], runs=[])
    state = recover_state(base, error="boom", attempts=2, reset_at="2026-09-22T15:00:00Z")
    assert state["failure"] == {"error": "boom", "attempts": 2, "reset_at": "2026-09-22T15:00:00Z"}
    assert state["now"] == base["now"]


def test_state_is_json_serialisable():
    for s in SITUATIONS:
        json.dumps(s.state)
    assert NOW.tzinfo is UTC
