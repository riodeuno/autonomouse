"""Tests for `autonomouse.session_end_hook` (sys-d5): the `SessionEnd` hook that writes
a run record's `end_reason`.

Claude Code's own hook payload cannot tell a clean headless exit apart from one cut off
by `SIGTERM` — both report `reason: "other"` (verified live against a real `claude -p`
session and recorded on the card's notes). So the hook reads the session's transcript
instead; these fixtures reproduce, byte-for-byte in shape, the two transcripts that live
test produced: one whose last turn ended with the model choosing to stop
(`stop_reason: "end_turn"`), and one cut off mid tool-use (`stop_reason: "tool_use"`,
no further entries) — the second is exactly what a `SIGTERM` mid-session leaves behind.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from autonomouse import session_end_hook as hook
from autonomouse.board.runs import RunStatus, load_run, run_start


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def _assistant(stop_reason: str) -> dict:
    return {"type": "assistant", "message": {"stop_reason": stop_reason}}


class TestTranscriptReason:
    def test_completed_when_last_turn_ended_cleanly(self, tmp_path):
        path = tmp_path / "session.jsonl"
        _write_transcript(
            path,
            [
                {"type": "user", "message": {"content": "hi"}},
                _assistant("end_turn"),
            ],
        )
        assert hook.transcript_reason(str(path)) == "completed"

    def test_interrupted_mid_tool_use(self, tmp_path):
        """What a `SIGTERM` mid-session leaves: the model was still expecting a tool
        result when the process ended."""
        path = tmp_path / "session.jsonl"
        _write_transcript(
            path,
            [
                {"type": "user", "message": {"content": "hi"}},
                _assistant("end_turn"),
                {"type": "user", "message": {"content": "go on"}},
                _assistant("tool_use"),
            ],
        )
        assert hook.transcript_reason(str(path)) == "interrupted"

    def test_interrupted_with_no_assistant_reply_at_all(self, tmp_path):
        """What a `SIGTERM` right after the prompt leaves: no model turn logged yet."""
        path = tmp_path / "session.jsonl"
        _write_transcript(path, [{"type": "user", "message": {"content": "hi"}}])
        assert hook.transcript_reason(str(path)) == "interrupted"

    def test_interrupted_when_transcript_is_missing(self, tmp_path):
        assert hook.transcript_reason(str(tmp_path / "no-such-file.jsonl")) == "interrupted"

    def test_interrupted_when_no_path_given(self):
        assert hook.transcript_reason("") == "interrupted"

    def test_ignores_unparsable_lines(self, tmp_path):
        path = tmp_path / "session.jsonl"
        path.write_text('{"type": "user"}\nnot json\n' + json.dumps(_assistant("end_turn")))
        assert hook.transcript_reason(str(path)) == "completed"


class TestEndReason:
    def test_passes_through_an_interactive_reason(self, tmp_path):
        # A transcript that would read as interrupted, ignored: "logout" is already
        # exact, unlike headless mode's uninformative "other".
        path = tmp_path / "session.jsonl"
        _write_transcript(path, [{"type": "user", "message": {"content": "hi"}}])
        payload = {"reason": "logout", "transcript_path": str(path)}
        assert hook.end_reason(payload) == "logout"

    def test_other_falls_back_to_the_transcript(self, tmp_path):
        path = tmp_path / "session.jsonl"
        _write_transcript(path, [_assistant("end_turn")])
        payload = {"reason": "other", "transcript_path": str(path)}
        assert hook.end_reason(payload) == "completed"


class TestMain:
    """`main()` end to end: stdin JSON, `AUTONOMOUSE_RUN_ID` in the environment, a
    board under `AUTONOMOUSE_HOME` — the exact three inputs Claude Code and
    `controller.py`'s `_start` give the real hook."""

    def _board(self, tmp_path, *, run_id: str) -> Path:
        root = tmp_path / "board"
        run_start(root, run_id=run_id, session_id="s1", pid=1, model="opus")
        return root

    def _run_main(self, monkeypatch, capsys, *, root: Path, run_id: str | None, payload: dict):
        monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
        if run_id is None:
            monkeypatch.delenv("AUTONOMOUSE_RUN_ID", raising=False)
        else:
            monkeypatch.setenv("AUTONOMOUSE_RUN_ID", run_id)
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(payload)))
        exit_code = hook.main()
        assert exit_code == 0
        return capsys.readouterr()

    def test_normal_end_and_sigterm_end_record_different_reasons(
        self, tmp_path, monkeypatch, capsys
    ):
        """The card's own test: a session that ended normally and one cut off by
        `SIGTERM` land two different `end_reason` values on their run records."""
        normal_transcript = tmp_path / "normal.jsonl"
        _write_transcript(
            normal_transcript,
            [{"type": "user", "message": {"content": "hi"}}, _assistant("end_turn")],
        )
        sigterm_transcript = tmp_path / "sigterm.jsonl"
        _write_transcript(
            sigterm_transcript,
            [{"type": "user", "message": {"content": "hi"}}, _assistant("tool_use")],
        )

        normal_root = self._board(tmp_path / "normal", run_id="20260922-070000-aaaa")
        self._run_main(
            monkeypatch,
            capsys,
            root=normal_root,
            run_id="20260922-070000-aaaa",
            payload={
                "session_id": "s1",
                "transcript_path": str(normal_transcript),
                "reason": "other",
            },
        )

        sigterm_root = self._board(tmp_path / "sigterm", run_id="20260922-070000-bbbb")
        self._run_main(
            monkeypatch,
            capsys,
            root=sigterm_root,
            run_id="20260922-070000-bbbb",
            payload={
                "session_id": "s2",
                "transcript_path": str(sigterm_transcript),
                "reason": "other",
            },
        )

        normal_run = load_run(normal_root, "20260922-070000-aaaa")
        sigterm_run = load_run(sigterm_root, "20260922-070000-bbbb")
        assert normal_run.end_reason == "completed"
        assert sigterm_run.end_reason == "interrupted"
        assert normal_run.end_reason != sigterm_run.end_reason
        assert normal_run.status == RunStatus.ALIVE  # the hook sets only end_reason

    def test_without_run_id_writes_nothing(self, tmp_path, monkeypatch, capsys):
        root = self._board(tmp_path, run_id="20260922-070000-cccc")
        self._run_main(
            monkeypatch,
            capsys,
            root=root,
            run_id=None,
            payload={"session_id": "s1", "reason": "other", "transcript_path": ""},
        )
        assert load_run(root, "20260922-070000-cccc").end_reason is None

    def test_bad_stdin_json_does_not_raise(self, tmp_path, monkeypatch, capsys):
        root = self._board(tmp_path, run_id="20260922-070000-dddd")
        monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
        monkeypatch.setenv("AUTONOMOUSE_RUN_ID", "20260922-070000-dddd")
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("not json"))
        assert hook.main() == 0
        assert load_run(root, "20260922-070000-dddd").end_reason is None


def test_the_registered_command_records_the_end_reason_from_any_folder(tmp_path):
    """The hook as `autonomouse run` registers it with `--settings` (sys-f11): Claude Code
    runs its command in a shell, in the session's folder, with the payload on stdin. It
    needs no repository checkout around it, so it works from the installed wheel too."""
    (command,) = [
        h["command"]
        for group in json.loads(hook.settings())["hooks"]["SessionEnd"]
        for h in group["hooks"]
    ]
    root = tmp_path / "board"
    run_start(root, run_id="20260922-070000-eeee", session_id="s1", pid=1, model="opus")
    transcript = tmp_path / "t.jsonl"
    _write_transcript(transcript, [_assistant("end_turn")])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(
        ["sh", "-c", command],
        input=json.dumps({"reason": "other", "transcript_path": str(transcript)}),
        text=True,
        cwd=elsewhere,
        env={
            **os.environ,
            "AUTONOMOUSE_HOME": str(root),
            "AUTONOMOUSE_RUN_ID": "20260922-070000-eeee",
        },
        check=True,
    )
    assert load_run(root, "20260922-070000-eeee").end_reason == "completed"
