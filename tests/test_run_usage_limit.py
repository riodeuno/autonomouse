"""`autonomouse run` and usage limits: every session gets `CLAUDE_CODE_RETRY_WATCHDOG=1`; a
limit hit is recorded on the run with its reset time; while that reset time is ahead, the next
wake starts no session and writes a run record saying why, with Jev on or off (the
controller's own check, sys-j6); once it has passed, the next wake's Jev state carries the hit;
with `jev.enabled`, a failed session is followed by Jev's `recover` question and its answer is
applied, `retry` below its floor or on a failed request, and a retry past
`ceilings.retries_per_wake` ends the wake as `next_wake` with no notify. The board, claude and Jev
fakes come from test_run_controller and test_run_jev.
"""

import json
from datetime import UTC, datetime, timedelta

from autonomouse import controller
from autonomouse.board.commands import project_show
from autonomouse.board.runs import load_run, save_run
from autonomouse.config import DEFAULTS
from autonomouse.jev.client import ChoiceAnswer, JevRequestError
from test_run_controller import _value, env  # noqa: F401  (env is a fixture)
from test_run_jev import _answers, _entry, jev  # noqa: F401  (jev is a fixture)


def _limit_result(env, call: int = 0, *, text: str) -> None:  # noqa: F811
    """Make fake claude's `call`-th session end the way a usage limit ends one."""
    (env.fake_dir / f"result.{call}").write_text(
        json.dumps({"type": "result", "is_error": True, "result": text, "total_cost_usd": 0.4})
    )
    (env.fake_dir / f"exit.{call}").write_text("1")


def _next_utc(hour: int, minute: int) -> str:
    """The next `hour:minute` UTC at or after a minute ago, as the record writes it."""
    now = datetime.now(UTC)
    at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at < now - timedelta(minutes=1):
        at += timedelta(days=1)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_ids(env) -> list[str]:  # noqa: F811
    return [line.split("=", 1)[1] for line in env.lines if line.startswith("run_id=")]


LIMIT = "You've hit your session limit · resets 3:45pm (UTC)"
# The one message every wake that started a session ends with (sys-w23): not a failure
# notice.
REPORT = (
    "<b>The wake on high has ended</b>\n"
    "Finished: 0 cards\n"
    "Waiting for you to merge: 0 pull requests"
)


def test_every_session_gets_the_retry_watchdog(env):  # noqa: F811
    assert env.wake() == 0
    assert (env.fake_dir / "watchdog").read_text() == "1\n"


def test_a_limit_hit_is_recorded_with_its_reset_time_and_the_next_jev_state_has_it(
    env, jev  # noqa: F811
):
    # Jev off: the hit is recorded, and nothing is asked.
    _limit_result(env, text=f"Working on hi-a1.\n{LIMIT}")
    assert env.wake() == 1
    first = load_run(env.root, _value(env.lines, "run_id"))
    assert first.usage_limit is not None
    assert first.usage_limit["reset_at"] == _next_utc(15, 45)
    assert first.usage_limit["text"] == LIMIT
    assert first.usage_limit["hit_at"] >= first.started_at
    assert first.jev == []
    assert _value(env.lines, "usage_limit").startswith(
        f"hit at {first.usage_limit['hit_at']}, resets {_next_utc(15, 45)}"
    )

    # Once the reset time has passed, the next wake, Jev on: `go` reads the hit in
    # `last_runs`. (Moved back so that no wall-clock minute holds the wake back.)
    first.usage_limit = {**first.usage_limit, "reset_at": "2026-01-01T00:00:00Z"}
    save_run(env.root, first)
    env.lines.clear()
    fake, settings = jev(_answers(go=ChoiceAnswer("skip", {}, 0.9)))
    assert env.wake(jev=settings) == 0
    assert (env.fake_dir / "args.1").exists()  # a go of skip is advice: the wake started
    state, _ = fake.calls[0]
    newest = state["last_runs"][0]
    assert newest["id"] == first.id
    assert newest["usage_limit"] == first.usage_limit
    assert newest["result"]["result_tail"].endswith(LIMIT)


def _limit_in_force(env) -> str:  # noqa: F811
    """Run one wake whose session hits a usage limit, then move its reset an hour ahead so
    that it is in force whatever the clock says. Returns the reset time."""
    _limit_result(env, text=LIMIT)
    assert env.wake() == 1
    first = load_run(env.root, _value(env.lines, "run_id"))
    reset = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    first.usage_limit = {**first.usage_limit, "reset_at": reset}
    save_run(env.root, first)
    env.lines.clear()
    return reset


def test_a_limit_in_force_starts_no_session_and_writes_a_run_record(env, jev):  # noqa: F811
    """sys-j6: the usage-limit check is the controller's own, before any session and before
    Jev: Jev is asked nothing, no claude starts, and the no-start still writes a run record."""
    reset = _limit_in_force(env)
    fake, settings = jev(_answers(go=ChoiceAnswer("go", {"go": 0.9}, 0.9)))
    assert env.wake(jev=settings) == 0
    assert fake.calls == []  # Jev is asked nothing
    assert not (env.fake_dir / "args.1").exists()  # no second claude
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.session_id is None and run.cards == [] and run.jev == []
    assert run.status.value == "stopped" and run.project == "high"
    assert run.summary == f"not started: a usage limit is in force until {reset}"
    assert f"not started: a usage limit is in force until {reset}" in env.lines
    assert project_show(env.root, "high").claim.run_id is None


def test_a_limit_in_force_holds_back_a_wake_with_jev_off_too(env):  # noqa: F811
    reset = _limit_in_force(env)
    assert env.wake() == 0
    assert not (env.fake_dir / "args.1").exists()
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.session_id is None
    assert run.summary == f"not started: a usage limit is in force until {reset}"


def test_a_clean_session_asks_no_recover(env, jev):  # noqa: F811
    fake, settings = jev()
    assert env.wake(jev=settings) == 0
    assert len(fake.calls) == 1
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.usage_limit is None
    assert not [e for e in run.jev if e["question"] == "recover"]


def test_recover_is_asked_with_the_limit_and_next_wake_ends_the_run(env, jev):  # noqa: F811
    _limit_result(env, text=LIMIT)
    probabilities = {"retry": 0.05, "next_wake": 0.9, "raise": 0.05}
    fake, settings = jev(_answers(recover=ChoiceAnswer("next_wake", probabilities, 0.9)))
    assert env.wake(jev=settings) == 1

    assert len(fake.calls) == 2
    state, questions = fake.calls[1]
    assert set(questions) == {"recover"}
    assert state["failure"]["reset_at"] == _next_utc(15, 45)
    assert state["failure"]["attempts"] == 1
    assert state["failure"]["error"] == (
        f"session exited 1; result is_error true: {LIMIT}"
    )
    assert state["last_runs"][0]["usage_limit"]["reset_at"] == _next_utc(15, 45)

    assert not (env.fake_dir / "args.1").exists()  # no second session
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert _entry(run, "recover") == {
        "question": "recover", "card": None, "source": "jev", "answer": "next_wake",
        "confidence": 0.9, "probabilities": probabilities, "applied": "next_wake",
        "reason": None,
    }
    assert _value(env.lines, "recover") == (
        f"next_wake (attempts 1, reset_at {_next_utc(15, 45)}, source jev)"
    )
    assert project_show(env.root, "high").claim.run_id is None


def test_retry_starts_a_new_session_from_the_notes(env, jev):  # noqa: F811
    _limit_result(env, text="API Error: Request timed out (ETIMEDOUT)")
    fake, settings = jev(_answers(recover=ChoiceAnswer("retry", {}, 0.8)))
    assert env.wake(jev=settings) == 0  # the retried session succeeds

    assert len(fake.calls) == 2  # go/width/fits/tier, then one recover
    assert fake.calls[1][0]["failure"]["reset_at"] is None
    failed, final = (load_run(env.root, r) for r in _run_ids(env))
    assert failed.usage_limit is None
    assert _entry(failed, "recover")["applied"] == "retry"
    assert final.result["is_error"] is False
    assert final.cards == ["hi-a1", "hi-a2"]
    second = (env.fake_dir / "args.1").read_text().splitlines()
    assert "--resume" not in second
    assert second[second.index("--session-id") + 1] == final.session_id != failed.session_id
    assert project_show(env.root, "high").claim.run_id is None


def test_the_retry_cap_is_the_config_default_two():
    assert DEFAULTS["ceilings"]["retries_per_wake"] == 2
    assert not hasattr(controller, "RETRIES_PER_WAKE")


def test_a_retry_past_the_cap_ends_the_wake_as_next_wake_with_no_notify(env, jev, monkeypatch):  # noqa: F811
    """sys-j6: before, the retry past the cap was raised to the human. Now the cap is
    `ceilings.retries_per_wake` (2), and the retry past it is `next_wake`: the reason is on
    the run record and nobody is notified."""
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append((text, kw)))
    monkeypatch.setenv("FAKE_EXIT", "3")
    fake, settings = jev(_answers(recover=ChoiceAnswer("retry", {}, 0.8)))
    assert settings.config["ceilings"]["retries_per_wake"] == 2
    assert env.wake(jev=settings) == 3

    runs = [load_run(env.root, r) for r in _run_ids(env)]
    assert len(runs) == 3
    assert [fake.calls[i][0]["failure"]["attempts"] for i in (1, 2, 3)] == [1, 2, 3]
    assert [_entry(r, "recover")["applied"] for r in runs] == ["retry", "retry", "next_wake"]
    last = _entry(runs[-1], "recover")
    assert last["answer"] == "retry" and last["source"] == "fallback"
    assert last["reason"] == "2 retries this wake already (ceilings.retries_per_wake is 2)"
    assert [text for text, _ in sent] == [REPORT]
    assert not (env.fake_dir / "args.3").exists()  # no fourth session
    assert project_show(env.root, "high").claim.run_id is None


def test_the_retry_cap_reads_the_config(env, jev, monkeypatch):  # noqa: F811
    monkeypatch.setattr(controller, "notify", lambda text, **kw: None)
    monkeypatch.setenv("FAKE_EXIT", "3")
    _, settings = jev(_answers(recover=ChoiceAnswer("retry", {}, 0.8)))
    settings.config["ceilings"]["retries_per_wake"] = 0
    assert env.wake(jev=settings) == 3
    (run,) = [load_run(env.root, r) for r in _run_ids(env)]
    assert _entry(run, "recover")["applied"] == "next_wake"
    assert _entry(run, "recover")["reason"] == (
        "0 retries this wake already (ceilings.retries_per_wake is 0)"
    )


def test_a_raise_that_cannot_be_sent_is_logged_on_the_run(env, jev, monkeypatch):  # noqa: F811
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    _limit_result(env, text="API Error: 401 authentication_error")
    _, settings = jev(_answers(recover=ChoiceAnswer("raise", {}, 0.9)))
    assert env.wake(jev=settings) == 1
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert _entry(run, "recover")["applied"] == "raise"
    failure, report = run.notify_failures  # the failure notice, then the wake's report
    assert failure["text"].startswith("<b>The wake on high failed and needs you</b>\n")
    assert "TELEGRAM_BOT_TOKEN" in failure["error"]
    assert report["text"] == REPORT


def test_a_recover_below_the_floor_retries(env, jev, monkeypatch):  # noqa: F811
    """sys-j6: before, an answer below `recover`'s 0.6 floor fell back to `raise` and
    notified the human. Now it falls back to `retry`: a new session from the notes."""
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    _limit_result(env, text=LIMIT)
    _, settings = jev(_answers(recover=ChoiceAnswer("raise", {"raise": 0.4}, 0.4)))
    assert env.wake(jev=settings) == 0  # the retried session succeeds
    failed, final = (load_run(env.root, r) for r in _run_ids(env))
    entry = _entry(failed, "recover")
    assert (entry["source"], entry["answer"], entry["applied"]) == ("fallback", "raise", "retry")
    assert entry["reason"] == "confidence 0.40 below floor 0.60"
    assert (env.fake_dir / "args.1").exists()  # a second session
    assert final.result["is_error"] is False
    assert sent == [REPORT]


def test_recover_below_the_floor_past_the_cap_ends_as_next_wake(env, jev, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    monkeypatch.setenv("FAKE_EXIT", "3")
    _, settings = jev(_answers(recover=ChoiceAnswer("raise", {"raise": 0.4}, 0.4)))
    assert env.wake(jev=settings) == 3
    runs = [load_run(env.root, r) for r in _run_ids(env)]
    assert [_entry(r, "recover")["applied"] for r in runs] == ["retry", "retry", "next_wake"]
    assert _entry(runs[-1], "recover")["reason"] == (
        "confidence 0.40 below floor 0.60; "
        "2 retries this wake already (ceilings.retries_per_wake is 2)"
    )
    assert sent == [REPORT]


def test_a_usable_raise_still_notifies(env, jev, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    _limit_result(env, text=LIMIT)
    _, settings = jev(_answers(recover=ChoiceAnswer("raise", {"raise": 0.9}, 0.9)))
    assert env.wake(jev=settings) == 1
    entry = _entry(load_run(env.root, _value(env.lines, "run_id")), "recover")
    assert (entry["source"], entry["applied"], entry["reason"]) == ("jev", "raise", None)
    assert len(sent) == 2  # the failure notice, then the wake's report (sys-w23)
    assert sent[0].startswith("<b>The wake on high failed and needs you</b>\n")
    assert sent[1] == REPORT
    assert not (env.fake_dir / "args.1").exists()


def test_a_failed_recover_request_retries(env, jev, monkeypatch):  # noqa: F811
    """sys-j6: before, a failed `recover` request ended the wake as `next_wake`. Now it is
    `retry`, and the retried session starts."""
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    _limit_result(env, text=LIMIT)
    fake, settings = jev()
    calls = {"n": 0}

    def ask(state, questions, *, timeout):
        calls["n"] += 1
        if "recover" in questions:
            raise JevRequestError(503, "unavailable")
        return fake(state, questions, timeout=timeout)

    settings = controller.Jev(config=settings.config, ask=ask)
    assert env.wake(jev=settings) == 0  # the retried session succeeds
    assert calls["n"] == 2
    failed, final = (load_run(env.root, r) for r in _run_ids(env))
    entry = _entry(failed, "recover")
    assert (entry["source"], entry["applied"], entry["answer"]) == ("fallback", "retry", None)
    assert entry["reason"].startswith("Jev request failed: JevRequestError(")
    assert (env.fake_dir / "args.1").exists()
    assert final.result["is_error"] is False
    assert sent == [REPORT]


def test_attempts_count_the_projects_failed_sessions_in_a_row():
    def run(started_at, result, project="high", session_id="s"):
        return {"project": project, "session_id": session_id, "started_at": started_at,
                "result": result}

    failed, clean = {"is_error": True}, {"is_error": False}
    runs = [
        run("2026-09-22T01:00:00Z", failed),
        run("2026-09-22T02:00:00Z", clean),
        run("2026-09-22T03:00:00Z", failed),
        run("2026-09-22T04:00:00Z", None, session_id=None),  # a wake that started no session
        run("2026-09-22T05:00:00Z", clean, project="low"),
        run("2026-09-22T06:00:00Z", None),  # printed no result
    ]
    assert controller._attempts(runs, "high") == 2
    assert controller._attempts(runs, "low") == 1  # never fewer than the failure at hand
