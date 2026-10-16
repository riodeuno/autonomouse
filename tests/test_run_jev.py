"""`autonomouse run` with `jev.enabled`: one Jev request (`go` and one `size` per ready card),
its answers in the run record, the tier each card's size maps to in the orchestrator's prompt,
a `split` size handed to the orchestrator, every ready card handed over with the ones past the
ceiling waiting for a free slot, and a wake picked for a sweep finding alone started (sys-s22).
Since sys-j6 every `go` answer (`skip` and `wait_30m` included) is advice: recorded with
`applied` null, and the wake starts whatever it says. A fake Jev; the board and claude
fakes come from test_run_controller."""

import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonomouse import controller
from autonomouse.board.commands import (
    card_cancel,
    card_edit,
    card_move,
    card_new,
)
from autonomouse.board.runs import RunKind, RunStatus, load_run, save_run
from autonomouse.board.runs import run_start as start_record
from autonomouse.board.store import commit_board
from autonomouse.config import DEFAULTS
from autonomouse.jev.client import ChoiceAnswer, JevConfigError
from autonomouse.jev.wake import GO_ADVICE
from autonomouse.sweep import Line
from test_run_controller import _prompt, _value, env  # noqa: F401  (env is a fixture)

CARDS = ("hi-a1", "hi-a2")


def _answers(**overrides) -> dict:
    """Usable answers for every question; `overrides` replaces some by key."""
    answers = {"go": ChoiceAnswer("go", {}, 0.9)}
    for card in CARDS:
        answers[f"size:{card}"] = ChoiceAnswer("s", {"xs": 0.1, "s": 0.7, "m": 0.2}, 0.7)
    answers.update(overrides)
    return answers


DOCS = Path(__file__).resolve().parent.parent / "docs"


class FakeJev:
    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.calls = answers, error, []
        self.keys_seen = []  # TYPESAFE_API_KEY as this process saw it on each call

    def __call__(self, state, questions, *, timeout):
        self.calls.append((state, questions))
        self.keys_seen.append(os.environ.get("TYPESAFE_API_KEY"))
        if self.error is not None:
            raise self.error
        return {k: v for k, v in self.answers.items() if k in questions}


@pytest.fixture
def jev(env, monkeypatch):  # noqa: F811
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    config = copy.deepcopy(DEFAULTS)
    config["jev"]["enabled"] = True

    def make(answers=None, error=None, workers_per_wake=2):
        config["ceilings"]["workers_per_wake"] = workers_per_wake
        fake = FakeJev(answers if answers is not None else _answers(), error)
        settings = controller.Jev(config=config, ask=fake)
        return fake, settings

    return make


def _run(env):  # noqa: F811
    return load_run(env.root, _value(env.lines, "run_id"))


def _entry(run, question, card=None):
    return next(e for e in run.jev if e["question"] == question and e["card"] == card)


def test_flag_off_asks_nothing_and_the_prompt_is_unchanged(env):  # noqa: F811
    assert env.wake() == 0
    run = _run(env)
    assert run.jev == []
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    # Nothing follows the start sentence but the Situation block (sys-s7).
    assert "Start these ready cards: hi-a1, hi-a2.\n\nSituation:\n" in prompt
    assert "Jev" not in prompt


def test_one_request_answers_land_in_the_record_and_the_tier_in_the_prompt(env, jev):  # noqa: F811
    fake, settings = jev(_answers(**{"size:hi-a2": ChoiceAnswer("m", {}, 0.9)}))
    assert env.wake(jev=settings) == 0

    # One request carrying every question, over the whole board and both ready cards.
    assert len(fake.calls) == 1
    state, questions = fake.calls[0]
    assert set(questions) == {"go", "size:hi-a1", "size:hi-a2"}
    assert set(state["cards"]) == set(CARDS)
    assert state["ceilings"]["workers_per_wake"] == 2

    run = _run(env)
    assert run.cards == ["hi-a1", "hi-a2"]
    assert all(e["source"] == "jev" for e in run.jev)
    assert all(e["reason"] is None for e in run.jev if e["question"] == "size")
    assert _entry(run, "go") == {
        "question": "go", "card": None, "source": "jev", "answer": "go", "confidence": 0.9,
        "probabilities": {}, "applied": None, "reason": GO_ADVICE,
    }
    assert _entry(run, "width") == {
        "question": "width", "card": None, "source": "jev", "answer": None,
        "confidence": None, "probabilities": None, "applied": "2",
        "reason": "every resumed and ready card, 2 of 2",
    }
    assert [e["question"] for e in run.jev] == ["go", "width", "size", "size"]
    assert _entry(run, "size", "hi-a1") == {
        "question": "size", "card": "hi-a1", "source": "jev", "answer": "s",
        "confidence": 0.7, "applied": "s", "tier": "opus/xhigh", "reason": None,
        "probabilities": {"xs": 0.1, "s": 0.7, "m": 0.2},
    }
    assert _entry(run, "size", "hi-a2")["tier"] == "fable/high"

    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "docs/orchestrator/tier-rubric.md" in prompt
    assert "Do not answer size" not in prompt
    assert "fits" not in prompt
    assert (
        "Start hi-a1 with tier opus/xhigh (size s): card start --model opus --effort xhigh "
        "--agent-type worker-xhigh, and spawn its worker with Agent model opus and "
        "subagent_type worker-xhigh."
    ) in prompt
    assert "--model fable --effort high --agent-type worker-high" in prompt
    assert _value(env.lines, "jev") == (
        "go go (advice), width 2, hi-a1 s opus/xhigh, hi-a2 m fable/high"
    )


def test_two_cards_whose_goals_name_the_same_file_both_start(env, jev):  # noqa: F811
    """Two ready cards that would edit one file are both started; their branches are the
    orchestrator's business while they run, not Jev's."""
    for card_id in CARDS:
        card_edit(
            env.root, card_id, goal="`card move` in src/autonomouse/cli.py", note="n"
        )
    commit_board(env.root, "both goals name cli.py")
    _, settings = jev(_answers())
    assert env.wake(jev=settings) == 0
    run = _run(env)
    assert run.cards == ["hi-a1", "hi-a2"]
    assert _entry(run, "width")["applied"] == "2"
    assert _entry(run, "width")["reason"] == "every resumed and ready card, 2 of 2"
    assert [e for e in run.jev if e["question"] in ("conflicts", "fits")] == []
    for card_id in CARDS:
        assert _entry(run, "size", card_id)["tier"] == "opus/xhigh"
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Start hi-a1 with tier opus/xhigh" in prompt and "Start hi-a2 with tier" in prompt


def test_width_is_capped_by_workers_per_wake(env, jev):  # noqa: F811
    _, settings = jev(_answers(), workers_per_wake=1)
    assert env.wake(jev=settings, workers_per_wake=1) == 0
    run = _run(env)
    width = _entry(run, "width")
    assert (width["answer"], width["applied"]) == (None, "1")
    assert width["reason"] == (
        "every resumed and ready card, 1 of 2; ceilings.workers_per_wake is 1"
    )
    assert run.cards == ["hi-a1"]
    assert _entry(run, "size", "hi-a2")["reason"] == "waits for a free slot"


def test_a_split_size_hands_the_card_to_the_orchestrator(env, jev):  # noqa: F811
    """sys-i29: `split` is the size question's fourth answer, larger than `m`."""
    _, settings = jev(_answers(**{
        "size:hi-a1": ChoiceAnswer("split", {"m": 0.2, "split": 0.8}, 0.8)
    }))
    assert env.wake(jev=settings) == 0
    run = _run(env)
    assert run.cards == ["hi-a1", "hi-a2"]  # the split card keeps its slot for its first child
    assert _entry(run, "size", "hi-a1") == {
        "question": "size", "card": "hi-a1", "source": "jev", "answer": "split",
        "confidence": 0.8, "probabilities": {"m": 0.2, "split": 0.8}, "applied": "split",
        "tier": None, "reason": "larger than m: the orchestrator splits it",
    }
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert (
        "Split hi-a1: Jev sized it larger than m. Size its children by your tier rubric."
    ) in prompt
    assert "Start hi-a1 with" not in prompt
    assert "Start hi-a2 with tier opus/xhigh (size s)" in prompt
    assert _value(env.lines, "jev") == "go go (advice), width 2, hi-a1 split, hi-a2 s opus/xhigh"


def test_a_low_confidence_split_is_applied(env, jev):  # noqa: F811
    """sys-j6: before, a split at 0.4 fell below the 0.5 floor and kept the card's own size.
    Now `size` has no floor, and the orchestrator splits the card."""
    _, settings = jev(_answers(**{"size:hi-a1": ChoiceAnswer("split", {"split": 0.4}, 0.4)}))
    assert env.wake(jev=settings) == 0
    entry = _entry(_run(env), "size", "hi-a1")
    assert (entry["source"], entry["answer"], entry["applied"], entry["tier"]) == (
        "jev", "split", "split", None
    )
    assert entry["reason"] == "larger than m: the orchestrator splits it"
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Split hi-a1: Jev sized it larger than m." in prompt


# ---------------------------------------------------------------------------- every ready card


def _third_ready_card(env, **fields):  # noqa: F811
    card_new(env.root, card_id="hi-a3", project="high", title="t", goal="g", test="t",
             position=2, **fields)
    card_move(env.root, "hi-a3", to="todo")
    commit_board(env.root, "a third ready card")


def test_every_ready_card_is_sized_and_named_and_the_ceiling_cuts_only_the_batch(env, jev):  # noqa: F811
    """sys-i29: with more ready cards than `ceilings.workers_per_wake`, the pick no longer
    drops the rest. Jev sizes all of them, the prompt names the first ones as started and
    the rest as waiting for a free slot, and the run record's `cards` holds the first ones."""
    _third_ready_card(env)
    fake, settings = jev(_answers(**{
        "size:hi-a3": ChoiceAnswer("m", {"m": 0.9}, 0.9),
    }), workers_per_wake=2)
    assert env.wake(jev=settings, workers_per_wake=2) == 0

    _, questions = fake.calls[0]
    assert set(questions) == {"go", "size:hi-a1", "size:hi-a2", "size:hi-a3"}

    run = _run(env)
    assert run.cards == ["hi-a1", "hi-a2"]
    assert _entry(run, "width")["applied"] == "2"
    assert _entry(run, "width")["reason"] == (
        "every resumed and ready card, 2 of 3; ceilings.workers_per_wake is 2"
    )
    third = _entry(run, "size", "hi-a3")
    assert (third["applied"], third["tier"], third["reason"]) == (
        "m", "fable/high", "waits for a free slot"
    )

    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Start these ready cards: hi-a1, hi-a2." in prompt
    assert "These ready cards wait for a free slot, in pick order: hi-a3." in prompt
    assert prompt.index("Start these ready cards") < prompt.index("wait for a free slot")
    assert "Start hi-a1 with tier opus/xhigh (size s)" in prompt
    assert "Start hi-a2 with tier opus/xhigh (size s)" in prompt
    assert "Start hi-a3 with tier fable/high (size m)" in prompt
    assert _value(env.lines, "ready") == "hi-a1,hi-a2"
    assert _value(env.lines, "waiting") == "hi-a3"
    assert _value(env.lines, "jev") == (
        "go go (advice), width 2, hi-a1 s opus/xhigh, hi-a2 s opus/xhigh, hi-a3 m fable/high"
    )


def test_a_waiting_card_that_shares_a_file_with_a_started_one_gets_no_interim_branch(env, jev):  # noqa: F811
    """Two ready cards that share a file, one started and one waiting, are handed over as
    any others: the run controller makes no interim branch and names none in the prompt;
    the orchestrator makes one with `run interim` only when it must (sys-o3)."""
    card_edit(env.root, "hi-a1", files=["src/autonomouse/controller.py"])
    card_edit(env.root, "hi-a2", files=["src/autonomouse/cli.py"])
    _third_ready_card(env, files=["src/autonomouse/controller.py"])
    _, settings = jev(_answers(**{"size:hi-a3": ChoiceAnswer("s", {}, 0.9)}), workers_per_wake=2)
    assert env.wake(jev=settings, workers_per_wake=2) == 0
    run = _run(env)
    assert run.cards == ["hi-a1", "hi-a2"] and run.interim_branch is None
    assert not [line for line in env.lines if line.startswith("interim=")]
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "These ready cards wait for a free slot, in pick order: hi-a3." in prompt
    assert "change the same files" not in prompt and "--base-branch wake/" not in prompt


def test_the_config_doc_names_the_questions_jev_answers():
    """docs/config.md's `jev.enabled` row names go, recover and size and no fits; its
    `ceilings.workers_per_wake` row says the ceiling is the workers alive at once, and that
    a ready card starts whenever a slot is free, a slot no worker has filled yet included
    (sys-s72). Since sys-j6 the row says `go` is advice with no floor or fallback, `size`
    applies whatever its confidence and `recover` falls back to `retry`, and the run
    record's rows say the same. Asserted here; decision B retired the doc-quote assertion
    sys-i30 wrote in the same wake."""
    config = (DOCS / "config.md").read_text().splitlines()
    jev_row = next(line for line in config if line.startswith("| `jev.enabled` |"))
    assert "asks Jev `go` and one `size` per ready card, started or waiting" in jev_row
    assert "and `recover` after a failed session" in jev_row
    assert "off means the orchestrator answers `size` itself" in jev_row
    assert "`split`, which means larger than `m`" in jev_row
    assert "fits" not in jev_row
    assert "Jev's `go` is advice, with no floor and no fallback" in jev_row
    assert (
        "applies Jev's size whenever it names one of the four sizes, whatever its confidence "
        "(no floor), and the card's own size only when the answer is missing or malformed"
    ) in jev_row
    assert "An answer below `recover`'s 0.6 floor, or a request that fails, is `retry`" in jev_row
    assert "only a usable `raise` from Jev notifies the human" in jev_row
    assert "below its floor" not in jev_row and "clears the floor" not in jev_row
    retries = next(line for line in config if line.startswith("| `ceilings.retries_per_wake` |"))
    assert retries.startswith("| `ceilings.retries_per_wake` | `2` |")
    assert "ends the wake as `next_wake`" in retries and "notifies nobody" in retries
    ceiling = next(line for line in config if line.startswith("| `ceilings.workers_per_wake` |"))
    assert ceiling.startswith(
        "| `ceilings.workers_per_wake` | `8` | the number of workers alive at once, at most."
    )
    assert "hands the orchestrator every ready card of the project" in ceiling
    assert (
        "at every event of its watch loop, the orchestrator starts every ready card a free "
        "slot can take, a slot no worker has filled yet included"
    ) in ceiling

    schema = (DOCS / "board-schema" / "run.md").read_text().splitlines()
    jev_list = next(line for line in schema if line.startswith("| jev |"))
    assert "`question` is `go`, `width`, `size` or `recover`" in jev_list
    assert "`fits` on records written before sys-i29" in jev_list
    assert (
        "a `size` answer of `split` (larger than `m`) is applied as `split` with `tier` null, "
        "whatever its confidence"
    ) in jev_list
    assert "for `fits` the safe option" not in jev_list
    assert "`go` has no floor and no fallback" in jev_list
    assert "its `applied` null, and its `reason` `advice, never applied:" in jev_list
    assert "for `recover` `retry`, a new session from the notes" in jev_list
    assert "a `retry` past `ceilings.retries_per_wake`" in jev_list
    assert "is applied as `next_wake`" in jev_list and "nobody is notified" in jev_list
    assert "RETRIES_PER_WAKE" not in jev_list
    refusals = next(line for line in schema if line.startswith("| refusals |"))
    assert refusals.startswith("| refusals | list of `{at, command, reason}`")
    for words in ("`command` `PAUSE`", "`command` `project claim`",
                  "`command` `claude --max-budget-usd`", "its result names no budget"):
        assert words in refusals
    interim = next(line for line in schema if line.startswith("| interim_branch |"))
    assert "`autonomouse run interim <run id>`" in interim
    assert "only after the three reshaping moves failed" in interim
    assert "written by `autonomouse run` before the session starts" not in interim


def test_a_failed_request_falls_back_to_the_no_jev_wake(env, jev):  # noqa: F811
    """The Jev request itself failing (bad key, network, timeout, ...) must not be treated
    as a usable `skip` answer: the wake falls back to what it would do with no Jev at
    all -- go, width at the ceiling, and each card's own size and its tier -- not the
    individual questions' own low-confidence fallbacks."""
    _, settings = jev(error=JevConfigError("TYPESAFE_API_KEY is not set."))
    assert env.wake(jev=settings) == 0
    assert (env.fake_dir / "args").exists()  # claude started, unlike a real `skip`
    run = _run(env)
    assert run.cards == ["hi-a1", "hi-a2"]
    reason = "Jev request failed: JevConfigError('TYPESAFE_API_KEY is not set.')"
    assert run.jev and all(e["source"] == "fallback" for e in run.jev if e["question"] != "go")
    go = _entry(run, "go")  # sys-j6: advice with no answer; nothing falls back
    assert (go["source"], go["answer"], go["applied"]) == ("jev", None, None)
    assert go["reason"] == f"{reason}; {GO_ADVICE}"
    assert _entry(run, "width")["applied"] == "2"  # every picked card, up to the ceiling
    assert _entry(run, "width")["reason"] == (
        f"{reason}; every resumed and ready card, 2 of 2"
    )
    for card_id in CARDS:  # each card's own size, `s`
        entry = _entry(run, "size", card_id)
        assert (entry["applied"], entry["tier"]) == ("s", "opus/xhigh")
        assert entry["reason"] == f"{reason}; the card's own size s"
    assert env.lines.count(f"jev_fallback=width: {reason}; every resumed and ready card, 2 of 2") == 1
    assert not [line for line in env.lines if line.startswith("jev_fallback=go")]
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Start hi-a1 with tier opus/xhigh (size s)" in prompt


def test_a_run_record_with_a_wrong_typed_field_no_longer_stops_the_wake(env, jev):  # noqa: F811
    """sys-t17: the usage-limit check (3b) and `consult_jev` (3c) both read every run
    record under `runs/` before the wake's claim. A record whose `pid` a merge or an
    older version left a wrong type fails validation (`run_from_dict` raises
    `BoardValidationError`), and that used to raise straight out of both, before the wake
    claimed the project or started a session -- one bad file under `runs/` stopped every
    later wake, Jev on or off. Now each prints the record and its error and leaves it out
    of its scan; with Jev on, `plan` stays null and nothing is asked or recorded, the same
    as `jev.enabled` false, and the session still starts."""
    bad_id = "20260101-000000-bad0"
    start_record(env.root, run_id=bad_id, session_id="s", pid=1, model="m", kind=RunKind.WAKE)
    path = env.root / "runs" / f"{bad_id}.json"
    data = json.loads(path.read_text())
    data["pid"] = "not-an-int"
    path.write_text(json.dumps(data))

    _, settings = jev()
    assert env.wake(jev=settings) == 0
    assert (env.fake_dir / "args").exists()  # the session still started

    lines = [line for line in env.lines if line.startswith("run_record_unreadable=")]
    assert lines
    assert all(
        str(path) in line and "'pid' must be integer or null" in line for line in lines
    )
    run = _run(env)
    assert run.jev == []  # as with jev.enabled false: nothing asked or recorded
    assert run.cards == ["hi-a1", "hi-a2"]  # the pick and the batch are unaffected


def test_a_go_of_skip_at_confidence_0_9_still_starts_a_session(env, jev):  # noqa: F811
    """sys-j6: before, a successful `skip` wrote a record and started nothing. Now `go` is
    advice: the answer is recorded and the wake starts its session and cards."""
    _, settings = jev(_answers(go=ChoiceAnswer("skip", {"skip": 0.9}, 0.9)))
    assert env.wake(jev=settings) == 0
    assert (env.fake_dir / "args").exists()  # claude started
    run = _run(env)
    assert run.session_id is not None and run.cards == ["hi-a1", "hi-a2"]
    assert _entry(run, "go") == {
        "question": "go", "card": None, "source": "jev", "answer": "skip", "confidence": 0.9,
        "probabilities": {"skip": 0.9}, "applied": None, "reason": GO_ADVICE,
    }
    assert _value(env.lines, "jev").startswith("go skip (advice), width 2")
    assert not (run.summary or "").startswith("jev: skip")


# ---------------------------------------------------------------------------- go's fallback
#
# The replay of runs 20260922-103440-fe48, -103503-a3ee and -103553-1320: a card's session
# ended because it needed the human, the human answered, and then Jev answered `go` at 0.25
# three wakes in a row. The old fallback applied `skip` each time, and each skip added one
# more run that read like a failure. Now the skips are counted apart from the sessions
# (sys-t40) and the blocker reads as resolved; since sys-j6 no fallback reads the last
# started session: every wake starts, whatever `go` says and however the last session
# ended.

LOW_GO = "confidence 0.25 below floor 0.60"


def _past_run(env, run_id, *, at, session=True, result=None, go=None):  # noqa: F811
    """A run record started at `at` (HH:MM on 2026-09-01): a session run with `result`
    (None: it printed none) or, with `go`, a wake Jev skipped with that go entry."""
    record = start_record(
        env.root, run_id=run_id, session_id=f"s-{run_id}" if session else None,
        pid=None, model="m" if session else None, kind="wake",
    )
    record.started_at = record.ended_at = f"2026-09-01T{at}:00Z"
    record.status = RunStatus.STOPPED
    record.project = "high"
    record.result = result
    record.summary = "sys: something done" if session else "jev: skip; no session started"
    if go is not None:
        record.jev = [go]
    save_run(env.root, record)


def _ask_and_answer(env, card_id, run_id, *, asked, answered):  # noqa: F811
    """`card_id` asked the human a decision in `run_id` at `asked`, answered at `answered`."""
    path = env.root / "projects" / "high" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data["decisions"].append({
        "id": "d1", "question": "token?", "options": ["a", "other: tell me"],
        "recommendation": "a", "asked_at": f"2026-09-01T{asked}:00Z", "answer": "a",
        "answered_at": f"2026-09-01T{answered}:00Z",
    })
    data["history"] += [
        {"at": f"2026-09-01T{asked}:00Z", "run_id": run_id, "from": "in_progress",
         "to": "decision_needed", "note": "asked: token?"},
        {"at": f"2026-09-01T{answered}:00Z", "run_id": "human", "from": "decision_needed",
         "to": "todo", "note": "answered: a"},
    ]
    path.write_text(json.dumps(data, indent=2) + "\n")


def _replay_board(env, *, blocked_result):  # noqa: F811
    """Last five runs, oldest first: a clean session, a session that ended with hi-a1 asking
    the human (answered since), and three wakes Jev skipped at go confidence 0.25."""
    clean = {"is_error": False, "stop_reason": "end_turn", "result": "Done."}
    _past_run(env, "20260901-090000-0001", at="09:00", result=clean)
    _past_run(env, "20260901-100000-0002", at="10:00", result=blocked_result)
    _ask_and_answer(env, "hi-a1", "20260901-100000-0002", asked="10:04", answered="10:25")
    skipped = {"question": "go", "card": None, "source": "fallback", "answer": "go",
               "confidence": 0.25, "applied": "skip", "reason": LOW_GO}
    for n, at in enumerate(("10:30", "10:35", "10:40"), start=3):
        _past_run(env, f"20260901-{at.replace(':', '')}00-000{n}", at=at, session=False,
                  go=skipped)


def test_a_low_confidence_go_after_skips_and_an_answered_blocker_goes(env, jev):  # noqa: F811
    _replay_board(env, blocked_result={"is_error": False, "stop_reason": "end_turn",
                                       "result": "blocked: hi-a1 asked the human"})
    fake, settings = jev(_answers(go=ChoiceAnswer("go", {}, 0.25)))
    assert env.wake(jev=settings) == 0

    # The facts Jev read: the skips are counted, not listed; the blocker is resolved.
    state = fake.calls[0][0]
    last_runs = state["last_runs"]
    assert [r["outcome"] for r in last_runs] == [
        ("session ended cleanly; a card needed the human, and the decision has since been "
        "answered"),
        "session ended cleanly",
    ]
    assert state["wakes_since_last_session"] == 3
    assert last_runs[0]["needed_human"] == [{
        "card": "hi-a1", "decision": "d1", "asked_at": "2026-09-01T10:04:00Z",
        "answered_at": "2026-09-01T10:25:00Z",
    }]

    run = _run(env)
    go = _entry(run, "go")
    assert (go["source"], go["answer"], go["confidence"], go["applied"]) == (
        "jev", "go", 0.25, None
    )
    assert go["reason"] == GO_ADVICE
    assert run.session_id is not None and (env.fake_dir / "args").exists()  # claude started


@pytest.mark.parametrize("blocked_result", [
    {"is_error": True, "stop_reason": "end_turn", "result": "API Error: 500"},
    None,  # the session printed no result: it crashed
])
def test_a_low_confidence_go_after_a_failed_session_still_starts(env, jev, blocked_result):  # noqa: F811
    """sys-j6: before, `go`'s fallback skipped after a failed session. Now nothing does."""
    _replay_board(env, blocked_result=blocked_result)
    _, settings = jev(_answers(go=ChoiceAnswer("go", {}, 0.25)))
    assert env.wake(jev=settings) == 0
    assert (env.fake_dir / "args").exists()  # claude started
    run = _run(env)
    assert _entry(run, "go")["applied"] is None
    assert run.session_id is not None and run.cards == ["hi-a1", "hi-a2"]


def test_five_refused_wakes_leave_the_failed_session_first_in_jevs_last_runs(
    env, jev, monkeypatch, capsys  # noqa: F811
):
    """sys-t40: a session failed, then five wakes in a row were refused for their config,
    and sys-j5 wrote each a stopped wake record with no session. Once the config is fixed,
    the failed session is still the first of Jev's last runs, and Jev reads that five
    wakes came after it with no session."""
    from autonomouse import cli

    failed = {"is_error": True, "stop_reason": "end_turn", "result": "API Error: 500"}
    _past_run(env, "20260901-090000-0001", at="09:00", result=failed)

    config_dir = env.tmp / "config-dir"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[orchestrator]\neffort = "low"\n')
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setattr("sys.argv", ["autonomouse", "run"])
    for _ in range(5):
        with pytest.raises(SystemExit) as exit_:
            cli.main()
        assert exit_.value.code == 1
    assert capsys.readouterr().err.count("error: orchestrator.effort") == 5
    refused = [
        r for r in (load_run(env.root, p.stem) for p in (env.root / "runs").glob("*.json"))
        if r.refusals
    ]
    assert len(refused) == 5 and all(r.session_id is None for r in refused)

    # The config is fixed: the next wake asks Jev.
    fake, settings = jev()
    assert env.wake(jev=settings) == 0
    state = fake.calls[0][0]
    assert [r["id"] for r in state["last_runs"]] == ["20260901-090000-0001"]
    assert state["last_runs"][0]["outcome"] == (
        "session failed (is_error; stop_reason end_turn)"
    )
    assert state["wakes_since_last_session"] == 5


def test_ceilings_still_cut_the_batch_of_a_low_confidence_go(env, jev):  # noqa: F811
    """A wake that starts on a low `go` starts at most `ceilings.workers_per_wake` workers."""
    _replay_board(env, blocked_result={"is_error": False, "stop_reason": "end_turn"})
    _, settings = jev(_answers(go=ChoiceAnswer("go", {}, 0.25)), workers_per_wake=1)
    assert env.wake(jev=settings, workers_per_wake=1) == 0
    run = _run(env)
    assert _entry(run, "go")["applied"] is None
    assert _entry(run, "width")["applied"] == "1"
    assert run.cards == ["hi-a1"]


# ---------------------------------------------------------------------------- sweep findings

FINDING_PR = "https://github.com/t/t/pull/7"


def _finding_only(env):  # noqa: F811
    """Cancel both of `high`'s ready cards, so no project has a card to pick, and return a
    sweep that finds one pull request of `high` whose runs failed (sys-s8)."""
    for card_id in CARDS:
        card_cancel(env.root, card_id, reason="test")
    line = Line(number=7, url=FINDING_PR, head_branch="card/hi-a1", head_sha="a" * 40,
                card_id="hi-a1", mergeable="MERGEABLE", runs="fail", unanswered=0)
    return lambda root, slug: [line] if slug == "high" else []


def test_a_wake_picked_for_a_finding_alone_with_a_low_go_starts(env, jev):  # noqa: F811
    """sys-s22: the pick has no card, only the sweep's finding. Jev reads the finding as the
    wake's work; its low `go` answer is advice (sys-j6), so the orchestrator's session starts
    with no cards."""
    sweep = _finding_only(env)
    fake, settings = jev(_answers(go=ChoiceAnswer("go", {"go": 0.25}, 0.25)))
    assert env.wake(jev=settings, sweep=sweep) == 0

    assert (env.fake_dir / "args").exists()  # claude started
    state, questions = fake.calls[0]
    assert set(questions) == {"go"}
    assert state["sweep_findings"] == [
        {"card": "hi-a1", "pr": FINDING_PR, "wrong": "its runs failed"}
    ]
    run = _run(env)
    assert run.project == "high" and run.cards == [] and run.session_id is not None
    go = _entry(run, "go")
    assert (go["source"], go["answer"], go["applied"]) == ("jev", "go", None)
    assert go["reason"] == GO_ADVICE
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Resume these in_progress cards: none. Start these ready cards: none." in prompt


def test_a_wake_with_no_card_and_no_finding_starts_nothing_and_asks_no_jev(env, jev):  # noqa: F811
    for card_id in CARDS:
        card_cancel(env.root, card_id, reason="test")
    fake, settings = jev(_answers(go=ChoiceAnswer("go", {"go": 0.25}, 0.25)))
    assert env.wake(jev=settings, sweep=lambda root, slug: []) == 0
    assert env.lines[-1] == "nothing to do: no project has ready or reclaimable cards"
    assert fake.calls == [] and not (env.fake_dir / "args").exists()


def test_the_jev_request_gets_the_secrets_file(env, jev):  # noqa: F811
    """The controller's own process never sees the Jev key from claude's environment (it
    starts claude after the request, in a subprocess) -- it must load the secrets file
    itself, for the request only."""
    secrets = env.tmp / ".env"
    secrets.write_text(secrets.read_text() + "TYPESAFE_API_KEY=x\n")
    fake, settings = jev()
    assert env.wake(jev=settings) == 0
    assert fake.keys_seen == ["x"]
    assert "TYPESAFE_API_KEY" not in os.environ  # not left behind after the request


def test_wait_30m_is_advice_and_the_wake_starts(env, jev):  # noqa: F811
    """sys-s15: nothing schedules a deferred wake (the scheduler owns the cadence, and
    nothing may call systemd). Since sys-j6 a `wait_30m` answer, like every `go` answer, is
    recorded as advice and applies nothing."""
    fake, settings = jev(_answers(go=ChoiceAnswer("wait_30m", {"wait_30m": 0.8}, 0.8)))
    assert env.wake(jev=settings) == 0
    assert (env.fake_dir / "args").exists()  # claude started
    assert set(fake.calls[0][1]) == {"go", "size:hi-a1", "size:hi-a2"}  # go is still asked
    run = _run(env)
    assert run.session_id is not None and run.cards == ["hi-a1", "hi-a2"]
    assert _entry(run, "go") == {
        "question": "go", "card": None, "source": "jev", "answer": "wait_30m",
        "confidence": 0.8, "probabilities": {"wait_30m": 0.8}, "applied": None,
        "reason": GO_ADVICE,
    }
    assert _entry(run, "width")["applied"] == "2"
    assert _entry(run, "size", "hi-a1")["tier"] == "opus/xhigh"
    assert _value(env.lines, "jev").startswith("go wait_30m (advice), width 2")
    assert not any(line.startswith("deferred=") for line in env.lines)
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Start hi-a1 with tier opus/xhigh (size s)" in prompt


def test_wait_30m_starts_however_many_wakes_it_advised_today(env, jev):  # noqa: F811
    """No daily cap on `wait_30m` remains: earlier `wait_30m` answers today change nothing."""
    today = datetime.now(UTC).strftime("%Y%m%d")
    for i in range(3):
        record = start_record(
            env.root, run_id=f"{today}-00000{i}-beef", session_id=None, pid=None, model=None,
            kind="wake",
        )
        record.jev = [{"question": "go", "card": None, "source": "jev", "answer": "wait_30m",
                       "confidence": 0.8, "applied": "wait_30m", "reason": None}]
        save_run(env.root, record)
    _, settings = jev(_answers(go=ChoiceAnswer("wait_30m", {}, 0.8)))
    assert env.wake(jev=settings) == 0
    go = _entry(_run(env), "go")
    assert (go["answer"], go["applied"]) == ("wait_30m", None)
    assert go["reason"] == GO_ADVICE
    assert (env.fake_dir / "args").exists()


def test_the_docs_drop_the_deferral_and_its_ceilings():
    """sys-s15: `autonomouse run` schedules no deferred wake, so docs/config.md drops the two
    ceilings that counted wakes, and the run record's rows stop naming `wait_30m` as a wake
    that starts no session. sys-j6: the attempt cap leaves the config too, `retries_per_wake`
    joins it, and a wake that starts no session is one a usage limit held back or the PAUSE
    file or a config value refused, never one Jev skipped."""
    config = (DOCS / "config.md").read_text()
    assert "`ceilings.wakes_per_day`" not in config
    assert "`ceilings.deferrals_per_day`" not in config
    assert "`ceilings.iterations_per_card`" not in config
    assert "| `ceilings.retries_per_wake` | `2` |" in config
    assert "systemd-run" not in config

    schema = (DOCS / "board-schema" / "run.md").read_text().splitlines()
    session = next(line for line in schema if line.startswith("| session_id, pid, model |"))
    assert "stay null for good in a wake that started no session" in session
    assert "a wake a usage limit held back" in session
    assert "a wake the PAUSE file or a config value refused" in session
    assert "wait_30m" not in session
    summary = next(line for line in schema if line.startswith("| summary |"))
    assert "`not started: a usage limit is in force until <reset_at>`" in summary
    assert "`refused: PAUSE - <path> exists`" in summary
    assert "wait_30m" not in summary
    jev_list = next(line for line in schema if line.startswith("| jev |"))
    assert "wait_30m is advice" not in jev_list


def test_the_cli_consults_jev_only_when_enabled(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(controller, "wake", lambda root, **kw: seen.update(kw) or 0)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    from autonomouse import cli

    monkeypatch.setattr("sys.argv", ["autonomouse", "run"])
    with pytest.raises(SystemExit):
        cli.main()
    assert seen["jev"] is None

    (config_dir / "config.toml").write_text("[jev]\nenabled = true\n")
    with pytest.raises(SystemExit):
        cli.main()
    assert isinstance(seen["jev"], controller.Jev)
    assert seen["jev"].config["ceilings"]["workers_per_wake"] == 8
    assert json.dumps(seen["jev"].config["jev"]) == '{"enabled": true}'
