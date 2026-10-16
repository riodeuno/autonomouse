"""Tests for board.runs: run records, liveness, project claim and release."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autonomouse.board.commands import (
    CommandError,
    card_cancel,
    card_finish,
    card_move,
    card_new,
    card_progress,
    card_show,
    project_new,
    project_show,
)
from autonomouse.board.paths import board_root, worktrees_root
from autonomouse.board.runs import (
    Liveness,
    RunStatus,
    card_start,
    cleanup_worktrees,
    load_run,
    project_claim,
    project_release,
    record_refusal,
    run_end,
    run_note,
    run_set_end_reason,
    run_start,
    run_status,
)
from autonomouse.board.schema import BoardValidationError, Stack
from autonomouse.board.worktrees import WorktreeError


def _dead_pid() -> int:
    """A pid that existed and is gone: a child that exited and was reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _transcript(projects_dir: Path, session_id: str, age_s: float) -> Path:
    path = projects_dir / "-home-test-repo" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type":"user"}\n')
    t = time.time() - age_s
    os.utime(path, (t, t))
    return path


def _project(root: Path, slug: str = "widgets") -> None:
    project_new(
        root,
        slug=slug,
        name="Widgets",
        goal="Build widgets.",
        repo="https://github.com/test/widgets",
        repo_path="/home/test/widgets",
        default_branch="main",
        priority=1,
    )


@pytest.fixture
def projects_dir(tmp_path: Path) -> Path:
    d = tmp_path / "claude-projects"
    d.mkdir()
    return d


def _fake_create_worktree(calls: list[tuple]):
    """A `create_worktree` double for `card_start`: records `(repo_path,
    worktree_path, branch, base_branch)` and makes the directory, like the real one
    does, without touching git — the claim/liveness tests below don't exercise git
    mechanics (`tests/test_board_worktrees.py` does)."""

    def make(repo_path: Path, worktree_path: Path, branch: str, base_branch: str) -> None:
        calls.append((repo_path, worktree_path, branch, base_branch))
        worktree_path.mkdir(parents=True, exist_ok=True)

    return make


class TestRunRecord:
    def test_start_writes_alive_record(self, tmp_path):
        run = run_start(
            tmp_path,
            run_id="20260922-070000-abcd",
            session_id="s1",
            pid=42,
            model="opus",
        )
        data = json.loads((tmp_path / "runs" / "20260922-070000-abcd.json").read_text())
        assert data["status"] == "alive"
        assert data["session_id"] == "s1" and data["pid"] == 42
        assert data["result"] is None and data["ended_at"] is None
        assert run.started_at == data["started_at"]

    def test_start_refuses_existing_and_bad_id(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        with pytest.raises(CommandError, match="already exists"):
            run_start(
                tmp_path,
                run_id="20260922-070000-abcd",
                session_id="s",
                pid=1,
                model="m",
            )
        with pytest.raises(CommandError, match="run id"):
            run_start(tmp_path, run_id="Bad", session_id="s", pid=1, model="m")

    def test_end_records_result_and_stops(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        run = run_end(
            tmp_path,
            "20260922-070000-abcd",
            result={"is_error": False, "num_turns": 3},
            summary="sys-x1 done",
        )
        assert run.status == RunStatus.STOPPED
        assert run.ended_at is not None
        assert load_run(tmp_path, "20260922-070000-abcd").result == {
            "is_error": False,
            "num_turns": 3,
        }

    def test_end_twice_keeps_first_ended_at(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        first = run_end(tmp_path, "20260922-070000-abcd", summary="done").ended_at
        second = run_end(tmp_path, "20260922-070000-abcd", result={"x": 1})
        assert second.ended_at == first
        assert second.summary == "done" and second.result == {"x": 1}

    def test_record_with_a_field_this_version_does_not_know_keeps_it(self, tmp_path):
        """Another version of autonomouse may write a field this one does not know
        (sys-d31); it is loaded into `extra` and written back by the next save."""
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        data["newer"] = {"x": 1}
        path.write_text(json.dumps(data))
        assert load_run(tmp_path, "20260922-070000-abcd").extra == {"newer": {"x": 1}}
        run_end(tmp_path, "20260922-070000-abcd", summary="done")
        assert json.loads(path.read_text())["newer"] == {"x": 1}

    def test_record_missing_a_field_is_still_refused(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        del data["status"]
        path.write_text(json.dumps(data))
        with pytest.raises(BoardValidationError, match="missing field"):
            load_run(tmp_path, "20260922-070000-abcd")

    def test_record_predating_notify_failures_still_loads_and_self_heals(
        self, tmp_path
    ):
        """A run record written before `notify_failures` existed has no such key;
        it must still load (defaulted to `[]`), and the next save writes it in."""
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        del data["notify_failures"]
        path.write_text(json.dumps(data))

        run = load_run(tmp_path, "20260922-070000-abcd")
        assert run.notify_failures == []

        run_end(tmp_path, "20260922-070000-abcd", summary="done")
        assert "notify_failures" in json.loads(path.read_text())

    def test_record_with_a_removed_backup_field_still_loads_and_keeps_it(self, tmp_path):
        """Records written while `autonomouse backup` existed carry a `backup` field. The
        field is no longer part of the schema, so it loads as an unknown field and the
        next save writes it back as it was."""
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        data["backup"] = {"status": "pushed", "reason": None}
        path.write_text(json.dumps(data))

        run = load_run(tmp_path, "20260922-070000-abcd")
        assert run.extra == {"backup": {"status": "pushed", "reason": None}}

        run_end(tmp_path, "20260922-070000-abcd", summary="done")
        saved = json.loads(path.read_text())
        assert saved["backup"] == {"status": "pushed", "reason": None}

    def test_start_records_installed_version(self, tmp_path):
        """`run start` writes the installed package's version, the one
        `autonomouse --version` prints."""
        from importlib.metadata import version

        run = run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        assert json.loads(path.read_text())["version"] == version("autonomouse")
        assert run.version == version("autonomouse")

    def test_record_predating_version_still_loads_and_self_heals(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        del data["version"]
        path.write_text(json.dumps(data))

        assert load_run(tmp_path, "20260922-070000-abcd").version is None
        run_end(tmp_path, "20260922-070000-abcd", summary="done")
        assert "version" in json.loads(path.read_text())

    def test_record_predating_usage_limit_loads_as_null_and_a_bad_one_is_refused(
        self, tmp_path
    ):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        del data["usage_limit"]
        path.write_text(json.dumps(data))
        assert load_run(tmp_path, "20260922-070000-abcd").usage_limit is None

        hit = {"hit_at": "2026-09-22T11:00:00Z", "reset_at": None, "text": "limit"}
        path.write_text(json.dumps({**data, "usage_limit": hit}))
        assert load_run(tmp_path, "20260922-070000-abcd").usage_limit == hit

        path.write_text(json.dumps({**data, "usage_limit": {"reset_at": "soon"}}))
        with pytest.raises(BoardValidationError, match="usage_limit"):
            load_run(tmp_path, "20260922-070000-abcd")


class TestEndReason:
    def test_set_end_reason_writes_it(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        run = run_set_end_reason(tmp_path, "20260922-070000-abcd", "completed")
        assert run.end_reason == "completed"
        assert load_run(tmp_path, "20260922-070000-abcd").end_reason == "completed"

    def test_set_end_reason_keeps_the_first_one(self, tmp_path):
        """The hook runs once per real session end; a second write (or a takeover
        that already marked the run `crashed`) never overwrites the first reason."""
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        run_set_end_reason(tmp_path, "20260922-070000-abcd", "completed")
        run = run_set_end_reason(tmp_path, "20260922-070000-abcd", "interrupted")
        assert run.end_reason == "completed"


class TestRefusals:
    def test_record_refusal_appends_at_command_reason_and_persists(self, tmp_path):
        """sys-j5: each refusal a command raised while carrying the run's id lands on the
        run's record as `{at, command, reason}`, in order."""
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        record_refusal(tmp_path, "20260922-070000-abcd", "card ask", "ask needs --brief")
        record_refusal(tmp_path, "20260922-070000-abcd", "project claim", "held")
        refusals = load_run(tmp_path, "20260922-070000-abcd").refusals
        assert [(r["command"], r["reason"]) for r in refusals] == [
            ("card ask", "ask needs --brief"),
            ("project claim", "held"),
        ]
        assert set(refusals[0]) == {"at", "command", "reason"}
        assert refusals[0]["at"].endswith("Z")

    def test_record_refusal_on_a_run_with_no_record_is_refused(self, tmp_path):
        with pytest.raises(CommandError, match="no run record"):
            record_refusal(tmp_path, "20260922-070000-abcd", "card ask", "r")

    def test_record_predating_refusals_loads_with_an_empty_list(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        path = tmp_path / "runs" / "20260922-070000-abcd.json"
        data = json.loads(path.read_text())
        del data["refusals"]
        path.write_text(json.dumps(data))

        assert load_run(tmp_path, "20260922-070000-abcd").refusals == []
        run_end(tmp_path, "20260922-070000-abcd", summary="done")
        assert json.loads(path.read_text())["refusals"] == []


class TestNotifyFailures:
    def test_record_notify_failure_appends_and_persists(self, tmp_path):
        from autonomouse.board.runs import record_notify_failure

        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )

        record_notify_failure(
            tmp_path, "20260922-070000-abcd", text="hi", error="no chat id"
        )
        run = record_notify_failure(
            tmp_path, "20260922-070000-abcd", text="hi again", error="still no chat id"
        )

        assert [f["text"] for f in run.notify_failures] == ["hi", "hi again"]
        assert all("at" in f for f in run.notify_failures)
        on_disk = json.loads(
            (tmp_path / "runs" / "20260922-070000-abcd.json").read_text()
        )
        assert len(on_disk["notify_failures"]) == 2


class TestRunNote:
    def _jev_entry(self, **overrides) -> dict:
        entry = {
            "question": "fits",
            "card": "wid-a1",
            "source": "orchestrator",
            "answer": "fits",
            "confidence": None,
            "applied": "fits",
        }
        entry.update(overrides)
        return entry

    def test_note_appends_a_card_and_a_jev_entry(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )

        run = run_note(tmp_path, "20260922-070000-abcd", card="wid-a1")
        assert run.cards == ["wid-a1"]

        run = run_note(
            tmp_path, "20260922-070000-abcd", jev=self._jev_entry()
        )
        assert run.cards == ["wid-a1"]  # untouched by a jev-only call
        assert run.jev == [{**self._jev_entry(), "reason": None}]

        on_disk = json.loads(
            (tmp_path / "runs" / "20260922-070000-abcd.json").read_text()
        )
        assert on_disk["cards"] == ["wid-a1"]
        assert on_disk["jev"] == [{**self._jev_entry(), "reason": None}]

    def test_note_appends_card_and_jev_in_one_call(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        run = run_note(
            tmp_path,
            "20260922-070000-abcd",
            card="wid-a2",
            jev=self._jev_entry(card="wid-a2"),
        )
        assert run.cards == ["wid-a2"]
        assert run.jev == [{**self._jev_entry(card="wid-a2"), "reason": None}]

    def test_note_keeps_an_explicit_reason(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        run = run_note(
            tmp_path,
            "20260922-070000-abcd",
            jev=self._jev_entry(source="fallback", reason="confidence 0.2 below floor"),
        )
        assert run.jev[0]["reason"] == "confidence 0.2 below floor"

    def test_note_keeps_a_size_entrys_tier(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        entry = self._jev_entry(
            question="size", answer="m", applied="m", tier="opus/xhigh"
        )
        run = run_note(tmp_path, "20260922-070000-abcd", jev=entry)
        assert run.jev == [{**entry, "reason": None}]

        on_disk = json.loads(
            (tmp_path / "runs" / "20260922-070000-abcd.json").read_text()
        )
        assert on_disk["jev"] == [{**entry, "reason": None}]

    def test_note_needs_card_or_jev(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        with pytest.raises(CommandError, match="needs --card, --jev or --interim-pr"):
            run_note(tmp_path, "20260922-070000-abcd")

    def test_note_refuses_a_jev_entry_missing_a_field(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        entry = self._jev_entry()
        del entry["confidence"]
        with pytest.raises(CommandError, match="missing field.*confidence"):
            run_note(tmp_path, "20260922-070000-abcd", jev=entry)
        assert load_run(tmp_path, "20260922-070000-abcd").jev == []

    def test_note_refuses_a_jev_entry_with_an_unknown_field(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        entry = self._jev_entry(extra="nope")
        with pytest.raises(CommandError, match="unexpected field.*extra"):
            run_note(tmp_path, "20260922-070000-abcd", jev=entry)

    def test_note_refuses_a_jev_value_that_is_not_an_object(self, tmp_path):
        run_start(
            tmp_path, run_id="20260922-070000-abcd", session_id="s", pid=1, model="m"
        )
        with pytest.raises(CommandError, match="must be a JSON object"):
            run_note(tmp_path, "20260922-070000-abcd", jev="not an object")

    def test_note_refuses_an_unknown_run_id(self, tmp_path):
        with pytest.raises(CommandError, match="no run record"):
            run_note(tmp_path, "does-not-exist", card="wid-a1")


class TestStatus:
    def test_live_pid_fresh_transcript_is_alive(self, tmp_path, projects_dir):
        _transcript(projects_dir, "s-live", age_s=5)
        run_start(
            tmp_path,
            run_id="20260922-070000-aaaa",
            session_id="s-live",
            pid=os.getpid(),
            model="m",
        )
        report = run_status(tmp_path, "20260922-070000-aaaa", projects_dir=projects_dir)
        assert report.status == Liveness.ALIVE

    def test_subagent_writes_keep_the_session_alive(self, tmp_path, projects_dir):
        main = _transcript(projects_dir, "s-sub", age_s=3600)
        sub = main.parent / "s-sub" / "subagents" / "agent-1.jsonl"
        sub.parent.mkdir(parents=True)
        sub.write_text("{}\n")
        run_start(
            tmp_path,
            run_id="20260922-070000-aaab",
            session_id="s-sub",
            pid=os.getpid(),
            model="m",
        )
        report = run_status(tmp_path, "20260922-070000-aaab", projects_dir=projects_dir)
        assert report.status == Liveness.ALIVE

    def test_live_pid_silent_transcript_is_stalled(self, tmp_path, projects_dir):
        _transcript(projects_dir, "s-quiet", age_s=31 * 60)
        run_start(
            tmp_path,
            run_id="20260922-070000-aaac",
            session_id="s-quiet",
            pid=os.getpid(),
            model="m",
        )
        report = run_status(tmp_path, "20260922-070000-aaac", projects_dir=projects_dir)
        assert report.status == Liveness.STALLED

    def test_finished_session_is_stopped(self, tmp_path, projects_dir):
        _transcript(projects_dir, "s-done", age_s=120)
        run_start(
            tmp_path,
            run_id="20260922-070000-bbbb",
            session_id="s-done",
            pid=_dead_pid(),
            model="m",
        )
        run_end(tmp_path, "20260922-070000-bbbb", result={"is_error": False})
        report = run_status(tmp_path, "20260922-070000-bbbb", projects_dir=projects_dir)
        assert report.status == Liveness.STOPPED

    def test_dead_pid_old_transcript_no_end_is_crashed(self, tmp_path, projects_dir):
        _transcript(projects_dir, "s-dead", age_s=3 * 3600)
        run_start(
            tmp_path,
            run_id="20260922-070000-cccc",
            session_id="s-dead",
            pid=_dead_pid(),
            model="m",
        )
        report = run_status(tmp_path, "20260922-070000-cccc", projects_dir=projects_dir)
        assert report.status == Liveness.CRASHED
        assert report.pid_alive is False
        assert report.transcript_age_s >= 3 * 3600 - 5


class TestClaim:
    def _crashed_holder(self, root, projects_dir):
        _transcript(projects_dir, "s-old", age_s=3 * 3600)
        run_start(
            root,
            run_id="20260922-060000-0001",
            session_id="s-old",
            pid=_dead_pid(),
            model="m",
        )
        project_claim(
            root, "widgets", run_id="20260922-060000-0001", projects_dir=projects_dir
        )

    def test_claim_free_project_writes_run(self, tmp_path, projects_dir):
        _project(tmp_path)
        run_start(
            tmp_path, run_id="20260922-070000-dddd", session_id="s", pid=7, model="opus"
        )
        outcome = project_claim(tmp_path, "widgets", run_id="20260922-070000-dddd")
        claim = project_show(tmp_path, "widgets").claim
        assert outcome.took_over is None
        assert (claim.run_id, claim.session_id, claim.pid, claim.model) == (
            "20260922-070000-dddd",
            "s",
            7,
            "opus",
        )
        assert load_run(tmp_path, "20260922-070000-dddd").project == "widgets"

    def test_takeover_of_crashed_claim_succeeds(self, tmp_path, projects_dir):
        _project(tmp_path)
        self._crashed_holder(tmp_path, projects_dir)
        _transcript(projects_dir, "s-new", age_s=1)
        run_start(
            tmp_path,
            run_id="20260922-070000-0002",
            session_id="s-new",
            pid=os.getpid(),
            model="m",
        )

        outcome = project_claim(
            tmp_path,
            "widgets",
            run_id="20260922-070000-0002",
            projects_dir=projects_dir,
        )

        assert outcome.took_over == "20260922-060000-0001"
        assert outcome.previous.status == Liveness.CRASHED
        assert project_show(tmp_path, "widgets").claim.run_id == "20260922-070000-0002"
        old = load_run(tmp_path, "20260922-060000-0001")
        assert old.status == RunStatus.TAKEN_OVER
        assert old.end_reason == "crashed"

    def test_takeover_of_live_claim_is_refused(self, tmp_path, projects_dir):
        _project(tmp_path)
        _transcript(projects_dir, "s-live", age_s=5)
        run_start(
            tmp_path,
            run_id="20260922-060000-0001",
            session_id="s-live",
            pid=os.getpid(),
            model="m",
        )
        project_claim(
            tmp_path,
            "widgets",
            run_id="20260922-060000-0001",
            projects_dir=projects_dir,
        )
        run_start(
            tmp_path,
            run_id="20260922-070000-0002",
            session_id="s-new",
            pid=os.getpid(),
            model="m",
        )

        with pytest.raises(CommandError, match="alive"):
            project_claim(
                tmp_path,
                "widgets",
                run_id="20260922-070000-0002",
                projects_dir=projects_dir,
            )

        assert project_show(tmp_path, "widgets").claim.run_id == "20260922-060000-0001"
        assert load_run(tmp_path, "20260922-060000-0001").status == RunStatus.ALIVE

    def test_takeover_of_stalled_claim_is_refused(self, tmp_path, projects_dir):
        _project(tmp_path)
        _transcript(projects_dir, "s-quiet", age_s=40 * 60)
        run_start(
            tmp_path,
            run_id="20260922-060000-0001",
            session_id="s-quiet",
            pid=os.getpid(),
            model="m",
        )
        project_claim(
            tmp_path,
            "widgets",
            run_id="20260922-060000-0001",
            projects_dir=projects_dir,
        )
        run_start(
            tmp_path,
            run_id="20260922-070000-0002",
            session_id="s-new",
            pid=os.getpid(),
            model="m",
        )
        with pytest.raises(CommandError, match="stalled"):
            project_claim(
                tmp_path,
                "widgets",
                run_id="20260922-070000-0002",
                projects_dir=projects_dir,
            )

    def test_one_run_claims_one_project(self, tmp_path):
        _project(tmp_path, "widgets")
        _project(tmp_path, "gadgets")
        run_start(
            tmp_path, run_id="20260922-070000-0002", session_id="s", pid=1, model="m"
        )
        project_claim(tmp_path, "widgets", run_id="20260922-070000-0002")
        with pytest.raises(CommandError, match="one run works one project"):
            project_claim(tmp_path, "gadgets", run_id="20260922-070000-0002")

    def test_release_only_by_holder(self, tmp_path):
        _project(tmp_path)
        run_start(
            tmp_path, run_id="20260922-070000-0002", session_id="s", pid=1, model="m"
        )
        project_claim(tmp_path, "widgets", run_id="20260922-070000-0002")
        with pytest.raises(CommandError, match="not '20260922-070000-0009'"):
            project_release(tmp_path, "widgets", run_id="20260922-070000-0009")
        project_release(tmp_path, "widgets", run_id="20260922-070000-0002")
        assert project_show(tmp_path, "widgets").claim.run_id is None
        with pytest.raises(CommandError, match="not claimed"):
            project_release(tmp_path, "widgets", run_id="20260922-070000-0002")


def _card_in_todo(
    root: Path, project_slug: str = "widgets", card_id: str = "wid-a1"
) -> None:
    card_new(root, card_id=card_id, project=project_slug, title="t", goal="g", test="t")
    card_move(root, card_id, "todo", project=project_slug)


def _claimed_run(root: Path, run_id: str, *, session_id: str, pid: int) -> None:
    run_start(root, run_id=run_id, session_id=session_id, pid=pid, model="opus")
    project_claim(root, "widgets", run_id=run_id)


class TestCardStart:
    def test_start_once_succeeds(self, tmp_path, projects_dir):
        _project(tmp_path)
        _card_in_todo(tmp_path)
        _claimed_run(tmp_path, "20260922-070000-1111", session_id="s1", pid=os.getpid())
        calls: list[tuple] = []
        wt_root = tmp_path / "wt"

        started = card_start(
            tmp_path,
            "wid-a1",
            branch="card/wid-a1",
            base_branch="main",
            session_id="s1",
            pid=os.getpid(),
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            run_id="20260922-070000-1111",
            projects_dir=projects_dir,
            worktrees_root=wt_root,
            create_worktree=_fake_create_worktree(calls),
        )

        expected = wt_root / "widgets" / "wid-a1"
        assert started.stack == Stack.IN_PROGRESS
        assert started.worktree == str(expected)
        assert started.branch == "card/wid-a1"
        assert started.base_branch == "main"
        assert started.assigned.session_id == "s1"
        assert started.assigned.pid == os.getpid()
        assert started.assigned.model == "sonnet"
        assert started.handoff.run_id == "20260922-070000-1111"
        assert started.history[-1].from_ == "todo"
        assert started.history[-1].to == "in_progress"
        assert card_show(tmp_path, "wid-a1").stack == Stack.IN_PROGRESS
        assert expected.is_dir()  # the fake makes it, like the real one does
        assert calls == [(Path("/home/test/widgets"), expected, "card/wid-a1", "main")]

    def test_start_uses_the_default_worktrees_root_when_none_is_given(
        self, tmp_path, projects_dir, monkeypatch
    ):
        _project(tmp_path)
        _card_in_todo(tmp_path)
        _claimed_run(tmp_path, "20260922-070000-1111", session_id="s1", pid=os.getpid())
        env_root = tmp_path / "env-wt"
        monkeypatch.setenv("AUTONOMOUSE_WORKTREES", str(env_root))

        started = card_start(
            tmp_path,
            "wid-a1",
            branch="card/wid-a1",
            base_branch="main",
            session_id="s1",
            pid=os.getpid(),
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            run_id="20260922-070000-1111",
            projects_dir=projects_dir,
            create_worktree=_fake_create_worktree([]),
        )

        assert started.worktree == str(env_root / "widgets" / "wid-a1")

    def test_second_start_by_another_session_is_refused(self, tmp_path, projects_dir):
        _project(tmp_path)
        _card_in_todo(tmp_path)
        _claimed_run(tmp_path, "20260922-070000-1111", session_id="s1", pid=os.getpid())
        card_start(
            tmp_path,
            "wid-a1",
            branch="card/wid-a1",
            base_branch="main",
            session_id="s1",
            pid=os.getpid(),
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            run_id="20260922-070000-1111",
            projects_dir=projects_dir,
            worktrees_root=tmp_path / "wt",
            create_worktree=_fake_create_worktree([]),
        )

        with pytest.raises(CommandError, match="held by a live session"):
            card_start(
                tmp_path,
                "wid-a1",
                branch="card/wid-a1",
                base_branch="main",
                session_id="s2",
                pid=os.getpid(),
                model="sonnet",
                effort="high",
                agent_type="worker-high",
                run_id="20260922-070000-1111",
                projects_dir=projects_dir,
                worktrees_root=tmp_path / "wt",
                create_worktree=_fake_create_worktree([]),
            )

    def test_start_when_the_working_folder_cannot_be_created_is_refused(
        self, tmp_path, projects_dir
    ):
        _project(tmp_path)
        _card_in_todo(tmp_path)
        _claimed_run(tmp_path, "20260922-070000-1111", session_id="s1", pid=os.getpid())

        def failing(repo_path, worktree_path, branch, base_branch):
            raise WorktreeError("origin/main not found")

        with pytest.raises(CommandError, match="working folder"):
            card_start(
                tmp_path,
                "wid-a1",
                branch="card/wid-a1",
                base_branch="main",
                session_id="s1",
                pid=os.getpid(),
                model="sonnet",
                effort="high",
                agent_type="worker-high",
                run_id="20260922-070000-1111",
                projects_dir=projects_dir,
                worktrees_root=tmp_path / "wt",
                create_worktree=failing,
            )
        # a failed creation never moves the card off todo
        assert card_show(tmp_path, "wid-a1").stack == Stack.TODO

    def test_start_on_unclaimed_project_is_refused(self, tmp_path, projects_dir):
        _project(tmp_path)
        _card_in_todo(tmp_path)
        run_start(
            tmp_path,
            run_id="20260922-070000-1111",
            session_id="s1",
            pid=os.getpid(),
            model="opus",
        )

        with pytest.raises(CommandError, match="unclaimed"):
            card_start(
                tmp_path,
                "wid-a1",
                branch="card/wid-a1",
                base_branch="main",
                session_id="s1",
                pid=os.getpid(),
                model="sonnet",
                effort="high",
                agent_type="worker-high",
                run_id="20260922-070000-1111",
                projects_dir=projects_dir,
            )

    def test_start_refused_from_other_stacks(self, tmp_path, projects_dir):
        _project(tmp_path)
        card_new(
            tmp_path, card_id="wid-a1", project="widgets", title="t", goal="g", test="t"
        )
        _claimed_run(tmp_path, "20260922-070000-1111", session_id="s1", pid=os.getpid())

        with pytest.raises(CommandError, match="cannot start"):
            card_start(
                tmp_path,
                "wid-a1",
                branch="card/wid-a1",
                base_branch="main",
                session_id="s1",
                pid=os.getpid(),
                model="sonnet",
                effort="high",
                agent_type="worker-high",
                run_id="20260922-070000-1111",
                projects_dir=projects_dir,
            )

    def test_resume_of_a_crashed_claim_succeeds(self, tmp_path, projects_dir):
        _project(tmp_path)
        _card_in_todo(tmp_path)
        dead = _dead_pid()
        _transcript(projects_dir, "s-old", age_s=3 * 3600)
        _claimed_run(tmp_path, "20260922-060000-0001", session_id="s-old", pid=dead)
        wt_root = tmp_path / "wt"
        calls: list[tuple] = []
        card_start(
            tmp_path,
            "wid-a1",
            branch="card/wid-a1",
            base_branch="main",
            session_id="s-old",
            pid=dead,
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            run_id="20260922-060000-0001",
            projects_dir=projects_dir,
            worktrees_root=wt_root,
            create_worktree=_fake_create_worktree(calls),
        )

        _transcript(projects_dir, "s-new", age_s=1)
        run_start(
            tmp_path,
            run_id="20260922-070000-0002",
            session_id="s-new",
            pid=os.getpid(),
            model="opus",
        )
        project_claim(
            tmp_path,
            "widgets",
            run_id="20260922-070000-0002",
            projects_dir=projects_dir,
        )

        resumed = card_start(
            tmp_path,
            "wid-a1",
            branch="card/wid-a1",
            base_branch="main",
            session_id="s-new",
            pid=os.getpid(),
            model="opus",
            effort="xhigh",
            agent_type="worker-xhigh",
            run_id="20260922-070000-0002",
            projects_dir=projects_dir,
            worktrees_root=wt_root,
            create_worktree=_fake_create_worktree(calls),
        )

        assert resumed.stack == Stack.IN_PROGRESS
        assert resumed.assigned.session_id == "s-new"
        assert resumed.history[-1].from_ == "in_progress"
        assert resumed.history[-1].to == "in_progress"
        assert "resumed" in resumed.history[-1].note
        # the resume reuses the same working folder, not a fresh one
        assert resumed.worktree == str(wt_root / "widgets" / "wid-a1")
        assert len(calls) == 2

    def test_a_resume_after_the_root_moved_keeps_the_recorded_working_folder(
        self, tmp_path, projects_dir
    ):
        """sys-s59 moved the default worktree root. A card started under the old root
        keeps its recorded `worktree` on a resume: its branch is checked out there, and
        no card's `worktree` field is rewritten."""
        _project(tmp_path)
        _card_in_todo(tmp_path)
        dead = _dead_pid()
        _transcript(projects_dir, "s-old", age_s=3 * 3600)
        _claimed_run(tmp_path, "20260922-060000-0001", session_id="s-old", pid=dead)
        old_root, new_root = tmp_path / "old-root", tmp_path / "new-root"
        calls: list[tuple] = []
        common = {
            "branch": "card/wid-a1", "base_branch": "main", "model": "opus",
            "effort": "xhigh", "agent_type": "worker-xhigh", "projects_dir": projects_dir,
            "create_worktree": _fake_create_worktree(calls),
        }  # fmt: skip
        card_start(
            tmp_path, "wid-a1", session_id="s-old", pid=dead, run_id="20260922-060000-0001",
            worktrees_root=old_root, **common,
        )  # fmt: skip
        _transcript(projects_dir, "s-new", age_s=1)
        run_start(
            tmp_path, run_id="20260922-070000-0002", session_id="s-new", pid=os.getpid(),
            model="opus",
        )  # fmt: skip
        project_claim(tmp_path, "widgets", run_id="20260922-070000-0002", projects_dir=projects_dir)

        resumed = card_start(
            tmp_path, "wid-a1", session_id="s-new", pid=os.getpid(),
            run_id="20260922-070000-0002", worktrees_root=new_root, **common,
        )  # fmt: skip

        recorded = old_root / "widgets" / "wid-a1"
        assert resumed.worktree == str(recorded)
        assert [call[1] for call in calls] == [recorded, recorded]
        assert not new_root.exists()


def _done_card_with_pr(
    tmp_path: Path,
    projects_dir: Path,
    *,
    card_id: str = "wid-a1",
    pr: str = "https://github.com/test/widgets/pull/1",
    run_id: str = "20260922-070000-1111",
    new_project: bool = True,
) -> Path:
    """A `done` card with a `pr` and a `worktree` pointing at a real directory —
    everything `cleanup_worktrees` needs to consider it, reached through the real
    `card_start` / `card_finish` lifecycle so its fields are exactly what those
    commands write. Returns the worktree path.

    `new_project=False` adds a second card to a project and claim a previous call
    already set up (same `tmp_path`, same `run_id`), rather than trying to create
    both again."""
    if new_project:
        _project(tmp_path)
    _card_in_todo(tmp_path, card_id=card_id)
    if new_project:
        _claimed_run(tmp_path, run_id, session_id="s1", pid=os.getpid())
    wt_root = tmp_path / "wt"
    card_start(
        tmp_path,
        card_id,
        branch=f"card/{card_id}",
        base_branch="main",
        session_id="s1",
        pid=os.getpid(),
        model="sonnet",
        effort="high",
        agent_type="worker-high",
        run_id=run_id,
        projects_dir=projects_dir,
        worktrees_root=wt_root,
        create_worktree=_fake_create_worktree([]),
    )
    card_finish(
        tmp_path,
        card_id,
        last_step="verified",
        pr=pr,
        run_id=run_id,
    )
    return wt_root / "widgets" / card_id


class TestCleanupWorktrees:
    def test_removes_a_merged_cards_worktree_and_records_it(self, tmp_path, projects_dir):
        worktree = _done_card_with_pr(tmp_path, projects_dir)
        removed: list[tuple] = []

        def remove(repo_path, worktree_path):
            removed.append((repo_path, worktree_path))

        cleaned = cleanup_worktrees(
            tmp_path,
            pr_state=lambda pr: "MERGED",
            remove_worktree=remove,
            run_id="20260922-080000-2222",
        )

        assert cleaned == ["wid-a1"]
        assert removed == [(Path("/home/test/widgets"), worktree)]
        card = card_show(tmp_path, "wid-a1")
        note = card.history[-1]
        assert note.from_ == "done" and note.to == "done"
        assert "worktree removed" in note.note and card.pr in note.note
        assert note.run_id == "20260922-080000-2222"

    def test_skips_a_card_whose_pr_has_not_merged(self, tmp_path, projects_dir):
        _done_card_with_pr(tmp_path, projects_dir)
        removed = []

        cleaned = cleanup_worktrees(
            tmp_path,
            pr_state=lambda pr: "OPEN",
            remove_worktree=lambda repo, wt: removed.append(wt),
        )

        assert cleaned == [] and removed == []

    def test_skips_a_card_whose_pr_state_check_failed(self, tmp_path, projects_dir):
        _done_card_with_pr(tmp_path, projects_dir)
        cleaned = cleanup_worktrees(tmp_path, pr_state=lambda pr: None)
        assert cleaned == []

    def test_skips_a_card_whose_worktree_is_already_gone(self, tmp_path, projects_dir):
        worktree = _done_card_with_pr(tmp_path, projects_dir)
        worktree.rmdir()
        calls = []

        cleaned = cleanup_worktrees(
            tmp_path,
            pr_state=lambda pr: calls.append(pr) or "MERGED",
        )

        assert cleaned == [] and calls == []  # never even checked the PR

    def test_skips_a_no_code_done_card(self, tmp_path, projects_dir):
        # every card gets a worktree, a no-code one included (docs/orchestrator.md) —
        # this one still has no `pr`, which is what cleanup keys off.
        _project(tmp_path)
        _card_in_todo(tmp_path, card_id="wid-a1")
        _claimed_run(tmp_path, "20260922-070000-1111", session_id="s1", pid=os.getpid())
        card_start(
            tmp_path,
            "wid-a1",
            branch="card/wid-a1",
            base_branch="main",
            session_id="s1",
            pid=os.getpid(),
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            run_id="20260922-070000-1111",
            projects_dir=projects_dir,
            worktrees_root=tmp_path / "wt",
            create_worktree=_fake_create_worktree([]),
        )
        card_finish(tmp_path, "wid-a1", last_step="findings", no_code=True)

        cleaned = cleanup_worktrees(tmp_path, pr_state=lambda pr: "MERGED")

        assert cleaned == []

    def test_continues_past_a_failed_removal(self, tmp_path, projects_dir):
        w1 = _done_card_with_pr(tmp_path, projects_dir, card_id="wid-a1")
        w2 = _done_card_with_pr(
            tmp_path,
            projects_dir,
            card_id="wid-a2",
            pr="https://github.com/test/widgets/pull/2",
            new_project=False,
        )
        lines: list[str] = []

        def remove(repo_path, worktree_path):
            if worktree_path == w1:
                raise WorktreeError("worktree has uncommitted changes")

        cleaned = cleanup_worktrees(
            tmp_path,
            pr_state=lambda pr: "MERGED",
            remove_worktree=remove,
            out=lines.append,
        )

        assert cleaned == ["wid-a2"]
        assert any("wid-a1" in line and "worktree_cleanup_failed" in line for line in lines)
        assert card_show(tmp_path, "wid-a1").stack == Stack.DONE  # untouched, will retry
        assert str(w2) in card_show(tmp_path, "wid-a2").history[-1].note


def _cancelled_card_with_worktree(
    tmp_path: Path,
    projects_dir: Path,
    *,
    claim_pid: int,
    release: bool,
    run_id: str = "20260922-070000-1111",
) -> Path:
    """A split parent: started (so it has a worktree), given a PR by its worker, then
    cancelled with the split's reason. `release` ends the wake's claim on the project;
    `claim_pid` is the pid the claiming run records. Returns the worktree path."""
    _project(tmp_path)
    _card_in_todo(tmp_path, card_id="wid-a1")
    _claimed_run(tmp_path, run_id, session_id="s1", pid=claim_pid)
    wt_root = tmp_path / "wt"
    card_start(
        tmp_path,
        "wid-a1",
        branch="card/wid-a1",
        base_branch="main",
        session_id="s1",
        pid=claim_pid,
        model="sonnet",
        effort="high",
        agent_type="worker-high",
        run_id=run_id,
        projects_dir=projects_dir,
        worktrees_root=wt_root,
        create_worktree=_fake_create_worktree([]),
    )
    card_progress(
        tmp_path, "wid-a1", pr="https://github.com/test/widgets/pull/1", run_id=run_id
    )
    card_cancel(tmp_path, "wid-a1", reason="split into wid-a2, wid-a3: PR too big", run_id=run_id)
    if release:
        project_release(tmp_path, "widgets", run_id=run_id)
    return wt_root / "widgets" / "wid-a1"


class TestCleanupCancelledWorktrees:
    """sys-h10: a cancelled card, a split parent above all, has no worker and nothing
    left to merge, so its worktree goes the way a merged done card's does."""

    def test_removes_a_cancelled_cards_worktree_without_asking_about_its_pr(
        self, tmp_path, projects_dir
    ):
        worktree = _cancelled_card_with_worktree(
            tmp_path, projects_dir, claim_pid=os.getpid(), release=True
        )
        removed: list[tuple] = []
        asked: list[str] = []

        cleaned = cleanup_worktrees(
            tmp_path,
            pr_state=lambda pr: asked.append(pr) or "OPEN",
            remove_worktree=lambda repo, wt: removed.append((repo, wt)),
            run_id="20260922-080000-2222",
            projects_dir=projects_dir,
        )

        assert cleaned == ["wid-a1"]
        assert removed == [(Path("/home/test/widgets"), worktree)]
        assert asked == []  # the PR's state never decides a cancelled card
        card = card_show(tmp_path, "wid-a1")
        assert card.stack == Stack.CANCELLED
        row = card.history[-1]
        assert row.from_ == "cancelled" and row.to == "cancelled"
        assert row.note == f"worktree removed ({worktree}): card cancelled"
        assert row.run_id == "20260922-080000-2222"

    @pytest.mark.parametrize(
        "transcript_age_s",
        [0, None],  # alive: written just now; stalled: pid exists, no transcript
    )
    def test_leaves_it_while_a_live_run_holds_the_project_then_removes_it(
        self, tmp_path, projects_dir, transcript_age_s
    ):
        # `card cancel` works from any stack: a card cancelled mid-wake may still have
        # its worker in the folder, so the folder waits for the wake to end.
        worktree = _cancelled_card_with_worktree(
            tmp_path, projects_dir, claim_pid=os.getpid(), release=False
        )
        if transcript_age_s is not None:
            _transcript(projects_dir, "s1", age_s=transcript_age_s)
        removed: list[Path] = []

        def cleanup():
            return cleanup_worktrees(
                tmp_path,
                pr_state=lambda pr: "OPEN",
                remove_worktree=lambda repo, wt: removed.append(wt),
                projects_dir=projects_dir,
            )

        assert cleanup() == [] and removed == []

        project_release(tmp_path, "widgets", run_id="20260922-070000-1111")
        assert cleanup() == ["wid-a1"] and removed == [worktree]

    def test_removes_it_when_the_run_holding_the_project_crashed(
        self, tmp_path, projects_dir
    ):
        worktree = _cancelled_card_with_worktree(
            tmp_path, projects_dir, claim_pid=_dead_pid(), release=False
        )
        removed: list[Path] = []

        cleaned = cleanup_worktrees(
            tmp_path,
            pr_state=lambda pr: "OPEN",
            remove_worktree=lambda repo, wt: removed.append(wt),
            projects_dir=projects_dir,
        )

        assert cleaned == ["wid-a1"] and removed == [worktree]


def test_a_split_parents_worktree_goes_and_its_branch_stays(
    tmp_path, projects_dir, monkeypatch
):
    """End to end, with a temporary AUTONOMOUSE_HOME and AUTONOMOUSE_WORKTREES and a
    real git repository: `card start` makes the parent's worktree, its worker commits
    on its branch, the orchestrator cancels it as a split, and `cleanup_worktrees`
    with the real `git worktree remove` takes the folder out. The branch and its
    commit stay, for the children to reuse."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    monkeypatch.setenv("AUTONOMOUSE_WORKTREES", str(tmp_path / "worktrees"))
    root = board_root()
    repo = _real_repo(tmp_path)
    project_new(
        root,
        slug="widgets",
        name="Widgets",
        goal="Build widgets.",
        repo="https://github.com/test/widgets",
        repo_path=str(repo),
        default_branch="main",
        priority=1,
    )
    _card_in_todo(root, card_id="wid-a1")
    run_id = "20260922-070000-1111"
    _claimed_run(root, run_id, session_id="s1", pid=os.getpid())
    card = card_start(
        root,
        "wid-a1",
        branch="card/wid-a1",
        base_branch="main",
        session_id="s1",
        pid=os.getpid(),
        model="sonnet",
        effort="high",
        agent_type="worker-high",
        run_id=run_id,
        projects_dir=projects_dir,
    )
    worktree = Path(card.worktree)
    assert worktree == worktrees_root() / "widgets" / "wid-a1"
    (worktree / "part_one.py").write_text("x = 1\n")
    _git(["add", "-A"], worktree)
    _git(["commit", "-q", "-m", "wid-a1: part one"], worktree)
    card_cancel(root, "wid-a1", reason="split into wid-a2, wid-a3: PR too big", run_id=run_id)
    project_release(root, "widgets", run_id=run_id)

    cleaned = cleanup_worktrees(
        root, pr_state=lambda pr: pytest.fail("asked a PR state"), projects_dir=projects_dir
    )

    assert cleaned == ["wid-a1"]
    assert not worktree.exists()
    assert str(worktree) not in _git(["worktree", "list", "--porcelain"], repo)
    assert _git(["log", "-1", "--format=%s", "card/wid-a1"], repo).strip() == "wid-a1: part one"
    assert card_show(root, "wid-a1").history[-1].note == (
        f"worktree removed ({worktree}): card cancelled"
    )


def _cli(*args, env):
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _real_repo(tmp_path: Path, name: str = "repo") -> Path:
    """A minimal real git repo, `main` populated and pushed to an `origin` remote —
    what `card start`'s real worktree creation needs on `repo_path`
    (`git fetch origin`, then `git worktree add ... origin/main`)."""
    origin = tmp_path / f"{name}-origin.git"
    _git(["init", "-q", "--bare", "-b", "main", str(origin)], tmp_path)
    checkout = tmp_path / name
    _git(["clone", "-q", str(origin), str(checkout)], tmp_path)
    _git(["config", "user.email", "t@t"], checkout)
    _git(["config", "user.name", "t"], checkout)
    (checkout / "README.md").write_text(f"{name}\n")
    _git(["add", "-A"], checkout)
    _git(["commit", "-q", "-m", "seed"], checkout)
    _git(["push", "-q", "origin", "main"], checkout)
    return checkout


def test_cli_run_and_claim(tmp_path: Path) -> None:
    claude_dir = tmp_path / "claude"
    projects_dir = claude_dir / "projects"
    projects_dir.mkdir(parents=True)
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        CLAUDE_CONFIG_DIR=str(claude_dir),
    )
    _project(tmp_path / "board")

    _transcript(projects_dir, "s-old", age_s=3 * 3600)
    dead = _dead_pid()
    started = _cli(
        "run",
        "start",
        "20260922-060000-0001",
        "--session-id",
        "s-old",
        "--pid",
        str(dead),
        "--model",
        "opus",
        env=env,
    )
    assert started.returncode == 0, started.stderr
    assert (
        _cli(
            "project", "claim", "widgets", "--run-id", "20260922-060000-0001", env=env
        ).returncode
        == 0
    )

    status = _cli("run", "status", "20260922-060000-0001", "--json", env=env)
    assert json.loads(status.stdout)["status"] == "crashed"

    _transcript(projects_dir, "s-new", age_s=1)
    env_live = dict(env, CLAUDE_CODE_SESSION_ID="s-new", CLAUDE_PID=str(os.getpid()))
    assert (
        _cli(
            "run", "start", "20260922-070000-0002", "--model", "opus", env=env_live
        ).returncode
        == 0
    )
    assert _cli("run", "status", "20260922-070000-0002", env=env).stdout.startswith(
        "alive:"
    )
    claimed = _cli(
        "project", "claim", "widgets", "--run-id", "20260922-070000-0002", env=env
    )
    assert claimed.returncode == 0, claimed.stderr
    assert "took over 20260922-060000-0001" in claimed.stdout
    assert _cli("run", "status", "20260922-060000-0001", env=env).stdout.startswith(
        "taken_over:"
    )

    result_file = tmp_path / "result.json"
    result_file.write_text('{"is_error": false, "num_turns": 2}')
    ended = _cli(
        "run",
        "end",
        "20260922-070000-0002",
        "--result",
        str(result_file),
        "--summary",
        "nothing moved",
        "--json",
        env=env,
    )
    assert ended.returncode == 0, ended.stderr
    assert json.loads(ended.stdout)["result"]["num_turns"] == 2
    assert (
        _cli(
            "project", "release", "widgets", "--run-id", "20260922-070000-0002", env=env
        ).returncode
        == 0
    )

    refused = _cli(
        "project", "claim", "widgets", "--run-id", "20260922-070000-0002", env=env
    )
    assert refused.returncode == 1
    assert "only an alive run may claim" in refused.stderr


def test_cli_run_note(tmp_path: Path) -> None:
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir(parents=True)
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        CLAUDE_CONFIG_DIR=str(claude_dir),
    )
    started = _cli(
        "run", "start", "20260922-070000-0002", "--session-id", "s", "--pid", "1",
        "--model", "opus", env=env,
    )
    assert started.returncode == 0, started.stderr

    noted_card = _cli(
        "run", "note", "20260922-070000-0002", "--card", "wid-a1", "--json", env=env
    )
    assert noted_card.returncode == 0, noted_card.stderr
    assert json.loads(noted_card.stdout)["cards"] == ["wid-a1"]

    jev_entry = json.dumps(
        {
            "question": "fits",
            "card": "wid-a1",
            "source": "orchestrator",
            "answer": "fits",
            "confidence": None,
            "applied": "fits",
        }
    )
    noted_jev = _cli(
        "run", "note", "20260922-070000-0002", "--jev", jev_entry, "--json", env=env
    )
    assert noted_jev.returncode == 0, noted_jev.stderr
    jev = json.loads(noted_jev.stdout)["jev"]
    assert jev == [
        {
            "question": "fits",
            "card": "wid-a1",
            "source": "orchestrator",
            "answer": "fits",
            "confidence": None,
            "applied": "fits",
            "reason": None,
        }
    ]

    missing_field = _cli(
        "run", "note", "20260922-070000-0002", "--jev", '{"question": "fits"}', env=env
    )
    assert missing_field.returncode == 1
    assert "missing field" in missing_field.stderr

    unknown_run = _cli(
        "run", "note", "20260922-999999-zzzz", "--card", "wid-a1", env=env
    )
    assert unknown_run.returncode == 1
    assert "no run record" in unknown_run.stderr


def test_cli_card_start(tmp_path: Path) -> None:
    claude_dir = tmp_path / "claude"
    projects_dir = claude_dir / "projects"
    projects_dir.mkdir(parents=True)
    repo = _real_repo(tmp_path)
    wt_root = tmp_path / "worktrees"
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        CLAUDE_CONFIG_DIR=str(claude_dir),
        AUTONOMOUSE_WORKTREES=str(wt_root),
    )
    board = tmp_path / "board"
    project_new(
        board,
        slug="widgets",
        name="Widgets",
        goal="Build widgets.",
        repo="https://github.com/test/widgets",
        repo_path=str(repo),
        default_branch="main",
        priority=1,
    )
    _card_in_todo(board)

    _transcript(projects_dir, "s-new", age_s=1)
    env_live = dict(env, CLAUDE_CODE_SESSION_ID="s-new", CLAUDE_PID=str(os.getpid()))
    assert (
        _cli(
            "run", "start", "20260922-070000-0002", "--model", "opus", env=env_live
        ).returncode
        == 0
    )
    assert (
        _cli(
            "project", "claim", "widgets", "--run-id", "20260922-070000-0002", env=env
        ).returncode
        == 0
    )

    started = _cli(
        "card",
        "start",
        "wid-a1",
        "--branch",
        "card/wid-a1",
        "--base-branch",
        "main",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--agent-type",
        "worker-high",
        "--run-id",
        "20260922-070000-0002",
        "--json",
        env=env_live,
    )
    assert started.returncode == 0, started.stderr
    card_json = json.loads(started.stdout)
    assert card_json["stack"] == "in_progress"
    assert card_json["assigned"]["session_id"] == "s-new"
    assert card_json["assigned"]["pid"] == os.getpid()
    expected = wt_root / "widgets" / "wid-a1"
    assert card_json["worktree"] == str(expected)
    assert (expected / "README.md").is_file()  # a real worktree, checked out from main
    assert (expected / ".git").exists()

    refused = _cli(
        "card",
        "start",
        "wid-a1",
        "--branch",
        "card/wid-a1",
        "--base-branch",
        "main",
        "--model",
        "sonnet",
        "--effort",
        "high",
        "--agent-type",
        "worker-high",
        "--run-id",
        "20260922-070000-0002",
        env=dict(env, CLAUDE_CODE_SESSION_ID="s-other", CLAUDE_PID=str(os.getpid())),
    )
    assert refused.returncode == 1
    assert "held by a live session" in refused.stderr


@pytest.mark.skipif(
    not (os.environ.get("CLAUDE_CODE_SESSION_ID") and os.environ.get("CLAUDE_PID")),
    reason="needs a shell inside a Claude Code session",
)
def test_this_shells_own_session_is_alive(tmp_path: Path) -> None:
    """Against the real transcript folder: the Claude Code session running this shell."""
    run_start(
        tmp_path,
        run_id="20260922-070000-eeee",
        session_id=os.environ["CLAUDE_CODE_SESSION_ID"],
        pid=int(os.environ["CLAUDE_PID"]),
        model="m",
    )
    report = run_status(tmp_path, "20260922-070000-eeee")
    assert report.status == Liveness.ALIVE, report.reason
