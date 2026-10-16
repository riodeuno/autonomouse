"""SessionEnd hook: writes this session's end reason onto its run record (sys-d5).

`autonomouse run` registers it on every orchestrator session it starts, with claude's
`--settings` flag (`settings` below, passed by `controller.py`'s `_start`), and it runs as
`python -m autonomouse.session_end_hook` with the Python that runs autonomouse — so it
fires the same way from a repository checkout and from the installed wheel (sys-f11). A
worker runs as a sub-agent inside that same session, spawned with the Agent tool, never
as a `claude` process of its own, so it never triggers this hook a second time.

Claude Code passes this hook a JSON object on stdin: `session_id`, `transcript_path`,
`cwd`, `hook_event_name`, and `reason` — one of `clear`, `logout`, `prompt_input_exit`
(interactive endings) or `other` (everything else). Verified live against this Claude
Code build (2.1.278, 2026-09-22): a headless `claude -p` session reports `other` for
*both* a clean exit and one killed with `SIGTERM` — the hook fires either way (its
graceful-shutdown path runs even on SIGTERM), but its own payload carries nothing that
tells the two apart. So for `other` (and the interactive `resume`, which is not really
an ending), this script reads the session's own transcript instead: a clean `-p`
completion's last turn always ends with the model choosing to stop
(`stop_reason: "end_turn"`, the condition that makes the CLI print its final result and
exit) — a session cut off mid-turn (SIGTERM, a crash, anything else) leaves either no
model reply since the last prompt, or one still mid tool-use, expecting a result that
never came.

The run id comes from `AUTONOMOUSE_RUN_ID`, set by `autonomouse run` in the
environment of every orchestrator session it starts. Absent — a session not started by
the controller, e.g. a human running `claude` by hand — there is no
run record to attach a reason to, and this script writes nothing.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

from autonomouse.board.commands import CommandError
from autonomouse.board.paths import board_root
from autonomouse.board.runs import run_set_end_reason
from autonomouse.board.schema import BoardValidationError

# Reasons Claude Code's own hook payload already names accurately; passed through as is.
_PASSTHROUGH_REASONS = {"clear", "logout", "prompt_input_exit"}


def settings() -> str:
    """claude's `--settings` value that registers this hook as the session's
    `SessionEnd` hook, run by the Python that runs this code."""
    command = f"{shlex.quote(sys.executable)} -m autonomouse.session_end_hook"
    hook = {"type": "command", "command": command, "timeout": 10}
    return json.dumps({"hooks": {"SessionEnd": [{"hooks": [hook]}]}})


def transcript_reason(transcript_path: str) -> str:
    """`completed` when the transcript's last assistant turn ended cleanly
    (`stop_reason: "end_turn"`); `interrupted` when the session has no assistant reply
    at all, or its last one was still mid tool-use when the session ended."""
    path = Path(transcript_path) if transcript_path else None
    if path is None or not path.is_file():
        return "interrupted"
    last_stop_reason = None
    try:
        with path.open() as transcript:
            for line in transcript:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "assistant":
                    last_stop_reason = entry.get("message", {}).get("stop_reason")
    except OSError:
        return "interrupted"
    return "completed" if last_stop_reason == "end_turn" else "interrupted"


def end_reason(payload: dict) -> str:
    reason = payload.get("reason")
    if reason in _PASSTHROUGH_REASONS:
        return reason
    return transcript_reason(payload.get("transcript_path", ""))


def main() -> int:
    run_id = os.environ.get("AUTONOMOUSE_RUN_ID")
    if not run_id:
        return 0
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"session_end_hook: stdin was not JSON: {e}", file=sys.stderr)
        return 0
    reason = end_reason(payload)
    try:
        run_set_end_reason(board_root(), run_id, reason)
    except (CommandError, BoardValidationError, OSError) as e:
        # The session is exiting either way; a board that cannot be written to must
        # never block its shutdown.
        print(f"session_end_hook: could not record end_reason: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
