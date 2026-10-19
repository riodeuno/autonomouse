import json
import os
from datetime import UTC, datetime, timedelta

from autonomouse.board.runs import load_run, save_run, transcript_last_write
from autonomouse.config import DEFAULTS, effective_config, planner_tiers, worker_tiers
from autonomouse.usage_limit import fallback_tier, transcript_limit
from test_run_controller import _value, env  # noqa: F401


def error_line(at, *, model="claude-fable-5-1", status=429):
    return {"type": "system", "subtype": "api_error", "timestamp": at.isoformat(),
            "error": {"status": status, "model": model, "rateLimits": {
                "rateLimitType": "seven_day_overage_included", "resetsAt": 1792818000}}}


def test_observed_429_shape_and_old_resume_turns(tmp_path):
    path = tmp_path / "session.jsonl"
    at = datetime(2026, 10, 18, 19, 26, 43, tzinfo=UTC)
    path.write_text(json.dumps(error_line(at)) + '\n{"incomplete":')
    hit = transcript_limit(path, "fable")
    assert hit["model"] == "claude-fable-5-1"
    assert hit["rateLimitType"] == "seven_day_overage_included"
    assert hit["reset_at"] == "2026-10-24T05:00:00Z"
    assert transcript_limit(path, "fable", since=at + timedelta(seconds=1)) is None
    path.write_text(json.dumps(error_line(at, status=529)))
    assert transcript_limit(path, "fable") is None


def test_retry_writes_do_not_refresh_liveness(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    path = folder / "session.jsonl"
    at = datetime.now(UTC) - timedelta(hours=1)
    path.write_text(json.dumps({"type": "assistant", "timestamp": at.isoformat()}) + "\n" +
                    json.dumps(error_line(datetime.now(UTC))))
    os.utime(path, None)
    assert transcript_last_write("session", tmp_path) == at.timestamp()


def test_fable_limit_switches_next_wake_and_all_tiers_then_expires(env):  # noqa: F811
    assert env.wake(model="fable") == 0
    run = load_run(env.root, _value(env.lines, "run_id"))
    now = datetime.now(UTC)
    run.usage_limit = {"hit_at": now.isoformat(), "reset_at": (now + timedelta(days=5)).isoformat(),
                       "text": "429", "model": "claude-fable-5-1",
                       "rateLimitType": "seven_day_overage_included"}
    save_run(env.root, run)
    assert fallback_tier(env.root, "fable", "medium") == ("opus", "xhigh")
    assert worker_tiers(effective_config(DEFAULTS, env.root))["m"] == ("opus", "xhigh")
    assert planner_tiers(effective_config(DEFAULTS, env.root))["complex"] == ("opus", "xhigh")
    assert fallback_tier(env.root, "fable", "medium", now=now + timedelta(days=6)) == ("fable", "medium")
    env.lines.clear()
    assert env.wake(model="fable") == 0
    assert _value(env.lines, "model") == "opus"
    assert _value(env.lines, "effort") == "xhigh"
    args = (env.fake_dir / "args.1").read_text()
    assert "--resume" not in args


def test_live_quota_retry_ends_wake_and_releases_claim(env, monkeypatch):  # noqa: F811
    from autonomouse.board.commands import project_show
    monkeypatch.setenv("FAKE_WAIT", str(env.fake_dir / "never"))
    monkeypatch.setenv("FAKE_ON_NEW", "echo '" + json.dumps(error_line(datetime.now(UTC))) +
                       "' >> \"$CLAUDE_CONFIG_DIR/projects/-fake/$sid.jsonl\"")
    assert env.wake(model="fable") != 0
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.usage_limit["rateLimitType"] == "seven_day_overage_included"
    assert project_show(env.root, "high").claim.run_id is None
