import threading
import time

import pytest

from autonomouse import control, controller
from autonomouse.board.commands import CommandError, project_show
from autonomouse.board.runs import load_run, pid_exists
from test_plans import env as plan_env  # noqa: F401
from test_run_controller import _value, env  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_control_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_STATE", str(tmp_path / "state"))


def wait_for(test, timeout=10):
    deadline = time.monotonic() + timeout
    while not test():
        assert time.monotonic() < deadline, "controller did not reach expected state"
        time.sleep(0.05)


def test_pause_ends_only_selected_project_and_workers_and_next_wake_runs(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_WAIT", str(env.fake_dir / "never"))
    child = env.fake_dir / "worker.pid"
    monkeypatch.setenv("FAKE_ON_NEW", f"sleep 300 & echo $! > '{child}'")
    codes, failures = [], []

    def wake():
        try:
            codes.append(env.wake())
        except Exception as e:  # noqa: BLE001 — report exceptions from the test thread
            failures.append(e)

    thread = threading.Thread(target=wake)
    thread.start()
    try:
        wait_for(lambda: child.exists() and child.read_text().strip())
        assert control.pause(env.root, "low") == []
        assert thread.is_alive()
        selected = control.pause(env.root, "high")
        thread.join(timeout=15)
        assert not thread.is_alive()
        assert not failures
        assert codes == [0]
        assert len(selected) == 1
        run = load_run(env.root, selected[0])
        assert run.ended_at is not None
        assert run.leftover_processes >= 2
        assert not pid_exists(run.pid)
        assert not controller._leftovers(run.id, run.pid)
        assert project_show(env.root, "high").claim.run_id is None
        assert not control.pause_requested(run.id)
        assert control.pause(env.root, "high") == []  # idempotent
    finally:
        (env.fake_dir / "never").touch()
        thread.join(timeout=15)
    monkeypatch.delenv("FAKE_WAIT")
    monkeypatch.delenv("FAKE_ON_NEW")
    env.lines.clear()
    assert env.wake() == 0
    assert _value(env.lines, "resumed").startswith("no")
    assert not control.project_paused("high")


def test_pause_unknown_project_fails_without_control_files(env):  # noqa: F811
    with pytest.raises(CommandError):
        control.pause(env.root, "missing")
    assert not list(control.config_dir().glob("control/*"))


def test_pause_timeout_keeps_request_for_controller(env, monkeypatch):  # noqa: F811
    import os

    from autonomouse.board.runs import RunKind, run_start

    run = run_start(env.root, run_id="20261019-000000-abcd", session_id="test",
                    pid=os.getpid(), model="opus", kind=RunKind.WAKE, project="high")
    with pytest.raises(CommandError, match="requests remain pending"):
        control.pause(env.root, "high", timeout=0)
    assert control.pause_requested(run.id)


def test_paused_planner_keeps_request_pending_until_next_wake(plan_env, monkeypatch):  # noqa: F811
    from autonomouse.board.plans import load_thread, thread_open
    from test_plans import SPECIFIED

    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(plan_env.tmp / "config"))
    claude = plan_env.fake_dir / "claude"
    original = claude.read_text()
    ready = plan_env.fake_dir / "planner-ready"
    claude.write_text(f"#!/bin/bash\ntouch '{ready}'\nsleep 300 &\nwait\n")
    thread = thread_open(plan_env.root, "t", SPECIFIED)
    codes = []
    runner = threading.Thread(target=lambda: codes.append(plan_env.step()))
    runner.start()
    try:
        wait_for(lambda: ready.exists() and bool(list((plan_env.root / "runs").glob("*.json"))))
        assert len(control.pause(plan_env.root, "t")) == 1
        runner.join(timeout=15)
        assert not runner.is_alive()
        assert load_thread(plan_env.root, thread.id).unanswered()
        assert plan_env.step() == 0  # timer steps respect the project pause
        assert load_thread(plan_env.root, thread.id).unanswered()
    finally:
        control.resume_at_wake("t")
        claude.write_text(original)
    assert plan_env.step() == 0
    assert not load_thread(plan_env.root, thread.id).unanswered()
