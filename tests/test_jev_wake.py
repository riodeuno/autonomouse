"""Tests for autonomouse.jev.wake.consult: every ready card is sized and handed over.

Jev answers `go`, and one `size` per ready card (xs, s, m, or split: larger than m). The batch
is the resumed cards, then the ready cards in pick order, capped by `ceilings.workers_per_wake`
(the number of workers alive at once); the ready cards past the cap wait for a free slot and
still get their size. Jev is not asked about conflicts, and a recent failed session no longer
halves the batch: which branches may collide is the orchestrator's business while its workers
run. A wake picked for a sweep finding alone carries no card: Jev's `go` reads the finding as
its work (sys-s22). Since sys-j6 `go` is advice: its entry keeps Jev's answer with `applied`
null and the plan starts whatever Jev said, and any size Jev names applies whatever its
confidence. A fake `ask` stands in for Jev.
"""

from __future__ import annotations

import copy
import json

from autonomouse.config import DEFAULTS
from autonomouse.jev.client import ChoiceAnswer, JevConfigError
from autonomouse.jev.questions import go_state
from autonomouse.jev.wake import (
    GO_ADVICE,
    SPLIT_REASON,
    WAITING_REASON,
    CardPlan,
    consult,
)
from autonomouse.sweep import Line
from jev_situations import CLEAN_RUNS, DONE, NOW, card, run


def _config(workers_per_wake: int) -> dict:
    config = copy.deepcopy(DEFAULTS)
    config["ceilings"]["workers_per_wake"] = workers_per_wake
    return config


def _ready(n: int, goal=lambda i: f"module_{i}.py gets a new function") -> list[dict]:
    return [card(f"sys-w{i}", position=i, goal=goal(i)) for i in range(n)]


def _fake(answers: dict | None = None, *, error=None):
    """A Jev `ask`: `go` go and every card sized `s` at 0.8; `answers` overrides by key."""
    calls = []

    def ask(state, questions, *, timeout):
        calls.append((state, questions))
        if error is not None:
            raise error
        out = {"go": ChoiceAnswer("go", {"go": 0.9}, 0.9)}
        for key in questions:
            if key.partition(":")[0] == "size":
                out[key] = ChoiceAnswer("s", {"s": 0.8}, 0.8)
        out.update(answers or {})
        return out

    ask.calls = calls
    return ask


def _consult(ready, *, cap=8, runs=CLEAN_RUNS, resume=(), findings=(), ask=None):
    return consult(
        now=NOW,
        config=_config(cap),
        cards=DONE + ready,
        runs=runs,
        resume=list(resume),
        ready=ready,
        findings=findings,
        ask=ask or _fake(),
    )


def _entry(plan, question, card_id=None):
    return next(
        e for e in plan.entries if e["question"] == question and e["card"] == card_id
    )


def test_eight_ready_cards_fill_a_ceiling_of_eight():
    ready = _ready(8)
    plan = _consult(ready, cap=8)
    assert plan.width == 8
    assert plan.batch == [c["id"] for c in ready]
    assert plan.waiting == []
    width = _entry(plan, "width")
    assert width["applied"] == "8" and width["source"] == "jev"
    assert width["reason"] == "every resumed and ready card, 8 of 8"


def test_eight_ready_cards_under_a_ceiling_of_five_start_five_and_three_wait():
    ready = _ready(8)
    plan = _consult(ready, cap=5)
    assert plan.width == 5
    assert plan.batch == [c["id"] for c in ready[:5]]
    assert plan.waiting == ["sys-w5", "sys-w6", "sys-w7"]
    assert _entry(plan, "width")["reason"] == (
        "every resumed and ready card, 5 of 8; ceilings.workers_per_wake is 5"
    )
    # A waiting card is sized like a started one: the orchestrator starts it whenever a slot
    # is free, with the tier its size maps to.
    assert set(plan.cards) == {c["id"] for c in ready}
    waiting = _entry(plan, "size", "sys-w5")
    assert (waiting["applied"], waiting["tier"]) == ("s", "opus/xhigh")
    assert waiting["reason"] == WAITING_REASON == "waits for a free slot"
    assert _entry(plan, "size", "sys-w4")["reason"] is None


def test_shared_files_and_a_recent_failure_leave_every_ready_card_in_the_batch():
    # Every goal names the same file, and the last run's session crashed: the two rules that
    # used to leave most of these cards idle. Now all eight start, and no conflicts question
    # is asked at all.
    ready = _ready(8, goal=lambda i: "`card move` in src/autonomouse/cli.py")
    crashed = run("20260922-090000-ffff", started_at="2026-09-22T09:00:05Z", ended_at=None,
                  status="crashed", is_error=True, end_reason="crashed")
    ask = _fake()
    plan = _consult(ready, cap=8, runs=CLEAN_RUNS + [crashed], ask=ask)
    assert plan.width == 8
    assert plan.batch == [c["id"] for c in ready]
    assert set(plan.cards) == {c["id"] for c in ready}
    assert _entry(plan, "width")["reason"] == "every resumed and ready card, 8 of 8"
    assert not [e for e in plan.entries if e["question"] == "conflicts"]
    _, questions = ask.calls[0]
    assert not [key for key in questions if key.startswith("conflicts")]


def test_resumed_cards_count_and_come_first():
    ready = _ready(3)
    plan = _consult(ready, cap=8, resume=["sys-r1"])
    assert plan.width == 4
    assert plan.batch == ["sys-r1", "sys-w0", "sys-w1", "sys-w2"]
    assert _entry(plan, "width")["reason"] == "every resumed and ready card, 4 of 4"


def test_resumed_cards_take_slots_before_ready_ones():
    plan = _consult(_ready(3), cap=2, resume=["sys-r1", "sys-r2"])
    assert plan.batch == ["sys-r1", "sys-r2"]
    assert plan.waiting == ["sys-w0", "sys-w1", "sys-w2"]


def test_a_failed_request_starts_every_picked_card_up_to_the_ceiling():
    plan = _consult(_ready(3), cap=2, ask=_fake(error=JevConfigError("no key")))
    assert (plan.width, plan.batch) == (2, ["sys-w0", "sys-w1"])
    assert plan.waiting == ["sys-w2"]
    go = _entry(plan, "go")
    assert (go["source"], go["answer"], go["applied"]) == ("jev", None, None)
    assert go["reason"] == f"Jev request failed: JevConfigError('no key'); {GO_ADVICE}"
    width = _entry(plan, "width")
    assert width["source"] == "fallback"
    assert width["reason"].startswith("Jev request failed: JevConfigError('no key'); ")
    sized = _entry(plan, "size", "sys-w2")
    assert (sized["applied"], sized["tier"]) == ("s", "opus/xhigh")  # the card's own size
    assert sized["reason"] == (
        "Jev request failed: JevConfigError('no key'); the card's own size s; waits for a "
        "free slot"
    )


def test_every_jev_entry_carries_its_probabilities():
    plan = _consult(_ready(2), ask=_fake())
    assert _entry(plan, "go", None)["probabilities"] == {"go": 0.9}
    assert _entry(plan, "size", "sys-w0")["probabilities"] == {"s": 0.8}
    assert _entry(plan, "width")["probabilities"] is None  # counted, not asked


def test_one_request_asks_go_and_one_size_per_card_and_nothing_else():
    ask = _fake()
    _consult(_ready(2), ask=ask)
    assert len(ask.calls) == 1
    _, questions = ask.calls[0]
    assert set(questions) == {"go", "size:sys-w0", "size:sys-w1"}
    assert not [key for key in questions if key.split(":")[0] in ("fits", "conflicts", "tier")]


# ---------------------------------------------------------------------------- split


def test_a_split_answer_hands_the_card_to_the_orchestrator():
    ready = _ready(2)
    plan = _consult(
        ready, ask=_fake({"size:sys-w0": ChoiceAnswer("split", {"split": 0.9, "m": 0.1}, 0.9)})
    )
    assert plan.batch == ["sys-w0", "sys-w1"]  # the split card keeps its slot
    assert plan.cards["sys-w0"] == CardPlan("sys-w0", "split", None, None)
    assert plan.cards["sys-w0"].split and plan.cards["sys-w0"].tier is None
    assert _entry(plan, "size", "sys-w0") == {
        "question": "size", "card": "sys-w0", "source": "jev", "answer": "split",
        "confidence": 0.9, "probabilities": {"split": 0.9, "m": 0.1}, "applied": "split",
        "tier": None, "reason": SPLIT_REASON,
    }
    assert SPLIT_REASON == "larger than m: the orchestrator splits it"
    assert not plan.cards["sys-w1"].split


def test_a_low_confidence_split_is_applied_too():
    """sys-j6: `size` has no floor; a split at 0.4 is Jev's answer and the orchestrator splits
    the card (it may override any size, docs/orchestrator/tier-rubric.md)."""
    plan = _consult(
        _ready(1), ask=_fake({"size:sys-w0": ChoiceAnswer("split", {"split": 0.4}, 0.4)})
    )
    assert plan.cards["sys-w0"] == CardPlan("sys-w0", "split", None, None)
    entry = _entry(plan, "size", "sys-w0")
    assert (entry["source"], entry["answer"], entry["applied"], entry["tier"]) == (
        "jev", "split", "split", None
    )
    assert entry["reason"] == SPLIT_REASON


def test_a_size_at_confidence_0_2_is_applied_and_maps_to_its_tier():
    plan = _consult(
        _ready(1), ask=_fake({"size:sys-w0": ChoiceAnswer("m", {"m": 0.2, "s": 0.19}, 0.2)})
    )
    assert plan.cards["sys-w0"] == CardPlan("sys-w0", "m", "fable", "high")
    entry = _entry(plan, "size", "sys-w0")
    assert (entry["source"], entry["applied"], entry["tier"], entry["reason"]) == (
        "jev", "m", "fable/high", None
    )


def test_a_malformed_size_keeps_the_cards_own_size():
    plan = _consult(
        _ready(1), ask=_fake({"size:sys-w0": ChoiceAnswer("huge", {"huge": 0.9}, 0.9)})
    )
    assert plan.cards["sys-w0"] == CardPlan("sys-w0", "s", "opus", "xhigh")
    entry = _entry(plan, "size", "sys-w0")
    assert (entry["source"], entry["applied"]) == ("fallback", "s")
    assert entry["reason"].endswith("; the card's own size s")


def test_a_waiting_card_can_be_split_too():
    plan = _consult(
        _ready(3), cap=2,
        ask=_fake({"size:sys-w2": ChoiceAnswer("split", {"split": 0.9}, 0.9)}),
    )
    assert plan.waiting == ["sys-w2"] and plan.cards["sys-w2"].split
    assert _entry(plan, "size", "sys-w2")["reason"] == f"{SPLIT_REASON}; {WAITING_REASON}"


def test_a_go_of_skip_at_confidence_0_9_is_advice_and_the_plan_starts():
    """sys-j6: before, a `skip` stopped the wake and applied no size. Now the answer is
    recorded as advice and every card is started or waits for a slot, sized as usual."""
    plan = _consult(_ready(2), cap=1, ask=_fake({"go": ChoiceAnswer("skip", {"skip": 0.9}, 0.9)}))
    assert (plan.batch, plan.waiting) == (["sys-w0"], ["sys-w1"])
    assert set(plan.cards) == {"sys-w0", "sys-w1"}
    go = _entry(plan, "go")
    assert go == {
        "question": "go", "card": None, "source": "jev", "answer": "skip", "confidence": 0.9,
        "probabilities": {"skip": 0.9}, "applied": None, "reason": GO_ADVICE,
    }
    assert GO_ADVICE == "advice, never applied: the wake starts whenever it has work"
    assert _entry(plan, "size", "sys-w0")["applied"] == "s"


def test_a_wait_30m_answer_is_advice_and_the_plan_starts():
    """sys-s15: nothing schedules a deferred wake, so `wait_30m` is applied as `go` with no
    daily cap: the plan starts every card up to the ceiling, even after wakes that applied
    `wait_30m` earlier the same day."""
    today = NOW.strftime("%Y-%m-%d")
    advised = [
        {**run(f"20260922-0{i}3000-cafe", started_at=f"{today}T0{i}:30:00Z",
               ended_at=f"{today}T0{i}:30:00Z"),
         "session_id": None, "result": None,
         "jev": [{"question": "go", "answer": "wait_30m", "applied": "wait_30m"}]}
        for i in range(3)
    ]
    plan = _consult(_ready(3), cap=2, runs=CLEAN_RUNS + advised,
                    ask=_fake({"go": ChoiceAnswer("wait_30m", {"wait_30m": 0.8}, 0.8)}))
    assert plan.batch == ["sys-w0", "sys-w1"] and plan.waiting == ["sys-w2"]
    assert set(plan.cards) == {"sys-w0", "sys-w1", "sys-w2"}
    go = _entry(plan, "go")
    # sys-j6: like every `go` answer, advice that applies nothing.
    assert (go["source"], go["answer"], go["applied"]) == ("jev", "wait_30m", None)
    assert go["reason"] == GO_ADVICE


# ---------------------------------------------------------------------------- sweep findings
#
# sys-s22: `pick` picks a project with no resumable or ready card when the sweep finds an open
# pull request autonomouse opened that needs work (sys-s8). That wake carries no card; before
# this, Jev read an empty `ready_cards` and the `go` fallback answered `skip` with "no card is
# ready", so the wake never started while Jev was on. Since sys-j6 no `go` fallback exists:
# such a wake starts whatever Jev answers.

PR = "https://github.com/t/t/pull/7"


def _finding(card_id="sys-w9", *, mergeable="MERGEABLE", runs="fail", unanswered=0) -> dict:
    """One sweep line that is a finding, as `autonomouse.sweep.Line.to_dict` writes it."""
    line = Line(number=7, url=PR, head_branch=f"card/{card_id}", head_sha="a" * 40,
                card_id=card_id, mergeable=mergeable, runs=runs, unanswered=unanswered)
    assert line.finding
    return line.to_dict()


def test_a_finding_reaches_jevs_state_named_in_words():
    findings = go_state({}, [_finding()])["sweep_findings"]
    assert findings == [{"card": "sys-w9", "pr": PR, "wrong": "its runs failed"}]


def test_a_conflict_with_the_default_branch_reaches_jev_naming_the_files():
    """A stacked PR GitHub reads as MERGEABLE against its base, whose branch conflicts with
    the default branch (sys-u7)."""
    line = Line(number=7, url=PR, head_branch="card/sys-w9", head_sha="a" * 40,
                card_id="sys-w9", mergeable="MERGEABLE", runs="pass", unanswered=0,
                conflicts=["src/a.py", "docs/b.md"])
    findings = go_state({}, [line.to_dict()])["sweep_findings"]
    assert findings == [{
        "card": "sys-w9", "pr": PR,
        "wrong": "it conflicts with the default branch in src/a.py, docs/b.md",
    }]


def test_with_no_card_and_a_finding_a_go_of_skip_still_starts_the_wake():
    """Before sys-j6, `go`'s fallback skipped with nothing to work; a finding-only wake now
    starts whatever `go` says."""
    plan = _consult([], findings=[_finding()], ask=_fake({"go": ChoiceAnswer("skip", {}, 0.9)}))
    assert (plan.width, plan.batch, plan.waiting, plan.cards) == (0, [], [], {})
    assert _entry(plan, "go")["applied"] is None


def test_a_finding_wake_starts_after_a_failed_last_session():
    """Before sys-j6, a failed last session made `go`'s fallback skip; now nothing does."""
    crashed = run("20260922-090000-ffff", started_at="2026-09-22T09:00:05Z", ended_at=None,
                  status="crashed", is_error=True, end_reason="crashed")
    plan = _consult([], findings=[_finding()], runs=CLEAN_RUNS + [crashed],
                    ask=_fake({"go": ChoiceAnswer("go", {}, 0.25)}))
    go = _entry(plan, "go")
    assert (go["answer"], go["applied"], go["reason"]) == ("go", None, GO_ADVICE)


def test_consult_with_no_card_and_a_finding_asks_go_with_the_finding_as_its_work():
    ask = _fake()
    finding = _finding(mergeable="CONFLICTING", runs="pass", unanswered=2)
    plan = _consult([], findings=[finding], ask=ask)

    assert len(ask.calls) == 1
    state, questions = ask.calls[0]
    assert set(questions) == {"go"}
    assert state["ready_cards"] == []
    assert state["sweep_findings"] == [{
        "card": "sys-w9", "pr": PR,
        "wrong": "it conflicts with its base branch, 2 review comment(s) unanswered",
    }]
    # The situation Jev reads names the finding's card id and pull request, and the `go`
    # question tells it a finding is work.
    situation = json.dumps(state)
    assert "sys-w9" in situation and PR in situation
    assert "`sweep_findings`" in questions["go"].instructions
    assert "`sweep_findings`" in questions["go"].criteria["go"]
    assert tuple(questions["go"].criteria) == ("go", "wait_30m", "skip")

    assert (plan.width, plan.batch, plan.waiting, plan.cards) == (0, [], [], {})
    assert _entry(plan, "go")["source"] == "jev"


def test_consult_with_no_card_a_finding_and_an_off_menu_go_records_it_and_starts():
    plan = _consult([], findings=[_finding()],
                    ask=_fake({"go": ChoiceAnswer("later", {"later": 0.9}, 0.9)}))
    go = _entry(plan, "go")
    assert (go["source"], go["answer"], go["applied"]) == ("jev", None, None)
    assert go["reason"] == (
        f"pick 'later' is not one of ('go', 'wait_30m', 'skip'); {GO_ADVICE}"
    )


def test_consult_with_ready_cards_sends_no_finding():
    ask = _fake()
    _consult(_ready(1), ask=ask)
    state, _ = ask.calls[0]
    assert state["sweep_findings"] == []
