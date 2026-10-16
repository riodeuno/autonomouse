"""The probe runner (sys-w25): a small agent checks that this machine can do on a service
what the agents will do there, and says each step to the owner as it goes.

A probe is an agent charter, `docs/probes/<service>.md` under the autonomouse home
(`autonomouse.home`): it says which steps to take and which permissions they prove, and
which of those permissions setup cannot go on without. `run_probe` knows no service. It:

1. starts `claude -p` with Sonnet at high effort and `--dangerously-skip-permissions` in
   the given working folder (for GitHub, the project's clone), its prompt the charter,
   the facts the probe needs (such as the repository and the branch) and the reporting
   rules below (`PROTOCOL`);
2. reads claude's `--output-format stream-json` events as they arrive and hands each line
   of the agent's text that starts with `STEP: ` to `say`, in order, while the agent works;
3. reads the agent's last message, the JSON report, into a `Report`: each permission is
   `ok`, `missing` with the exact fix, or `unchecked` with why; and the `notes` the
   charter asks for, plain lines the caller prints for the owner (the Linear probe's
   says how it removed its test issue).

The caller stops setup on `Report.blocking` (a required permission not proven, with its
fix) and warns on `Report.warnings` (an optional one). A session that ends without a
valid report raises `ProbeError`, which says so.

Secrets reach the session only through its environment, which it inherits: no argv
entry, prompt or printed line is built from one.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from autonomouse.home import Home, locate

MODEL = "sonnet"
EFFORT = "high"
STEP_PREFIX = "STEP: "
STATUSES = ("ok", "missing", "unchecked")

PROTOCOL = f"""\
## How you report

The program that started you reads your text; the owner sees your step lines as you work.

- Before each step, and after it with what you found, write one plain line for the owner,
  on a line of its own that starts with `{STEP_PREFIX}`. The owner has not read any code:
  say what you do and why in everyday words.
- Never write a secret value (a token, a key, a password) anywhere, not even in part.
- List every permission your charter names, each once, with `required` as your charter
  says. `status` is `ok` when you proved it, `missing` when the service refused it, and
  `unchecked` when you could not try it. `fix` is empty for `ok`; for `missing` it is the
  exact fix the owner makes (the setting and the permission to grant); for `unchecked` it
  says why you could not try it.
- When your charter asks for a note, add it to the report's `notes` list as one plain
  line for the owner; leave `notes` out when your charter asks for none.
- Your last message is only this JSON report, with no other text:
  {{"permissions": [{{"name": "...", "required": true, "status": "ok", "fix": ""}}], "notes": []}}
"""


class ProbeError(Exception):
    """The probe gave no answer: its charter is missing, its session failed, or it ended
    without a valid report. The message says which, and holds no secret."""


@dataclass(frozen=True)
class Permission:
    name: str
    required: bool
    status: str  # one of STATUSES
    fix: str  # the exact fix when missing; why when unchecked; empty when ok


@dataclass(frozen=True)
class Report:
    permissions: tuple[Permission, ...]
    notes: tuple[str, ...] = ()  # plain lines the charter asked for, for the owner

    @property
    def blocking(self) -> tuple[Permission, ...]:
        """Required permissions the probe did not prove: setup stops, naming each fix."""
        return tuple(p for p in self.permissions if p.required and p.status != "ok")

    @property
    def warnings(self) -> tuple[Permission, ...]:
        """Optional permissions the probe did not prove: setup goes on and warns."""
        return tuple(p for p in self.permissions if not p.required and p.status != "ok")


def _print(line: str) -> None:
    print(line, flush=True)


def prompt(charter: str, facts: Mapping[str, str]) -> str:
    """The session's whole prompt: the charter, the facts, the reporting rules."""
    lines = "\n".join(f"- {key}: {value}" for key, value in facts.items())
    return f"{charter.rstrip()}\n\n## Facts\n\n{lines}\n\n{PROTOCOL}"


def run_probe(
    service: str,
    *,
    cwd: Path,
    facts: Mapping[str, str],
    home: Home | None = None,
    say: Callable[[str], None] = _print,
    claude: str = "claude",
) -> Report:
    """Run the probe `docs/probes/<service>.md` in `cwd` and return its report."""
    charter = (home or locate()).charter(f"probes/{service}.md")
    if not charter.is_file():
        raise ProbeError(f"{service} probe: no charter at {charter}")
    argv = [
        claude,
        "-p",
        prompt(charter.read_text(), facts),
        "--model",
        MODEL,
        "--effort",
        EFFORT,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",  # claude -p prints stream-json only with --verbose
    ]
    result: dict | None = None
    with tempfile.TemporaryFile("w+") as stderr:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )
        try:
            for raw in proc.stdout:
                event = _event(raw)
                if event.get("type") == "assistant":
                    for step in _steps(event):
                        say(step)
                elif event.get("type") == "result":
                    result = event
            proc.wait()
        except BaseException:
            proc.kill()  # claude never outlives the probe, Ctrl-C included
            proc.wait()
            raise
        stderr.seek(0)
        err = stderr.read().strip().splitlines()[-5:]
    return _report(service, proc.returncode, result, err)


def _event(raw: str) -> dict:
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return event if isinstance(event, dict) else {}


def _steps(event: dict) -> list[str]:
    """The `STEP: ` lines of an assistant event's text, without the prefix."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    steps = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") == "text":
            for line in str(block.get("text", "")).splitlines():
                line = line.strip()
                if line.startswith(STEP_PREFIX) and line[len(STEP_PREFIX) :].strip():
                    steps.append(line[len(STEP_PREFIX) :].strip())
    return steps


def _report(service: str, code: int, result: dict | None, err: list[str]) -> Report:
    head = f"{service} probe ended without a report (claude exited {code}"
    if result is None:
        tail = "; ".join(err) or "it printed nothing"
        raise ProbeError(f"{head}; it printed no result: {tail})")
    text = result.get("result") if isinstance(result.get("result"), str) else ""
    if result.get("is_error") is True:
        raise ProbeError(f"{head}; the session failed: {text[-500:]})")
    start, end = text.find("{"), text.rfind("}")
    try:
        parsed = json.loads(text[start : end + 1]) if start != -1 else None
    except json.JSONDecodeError:
        parsed = None
    permissions = parsed.get("permissions") if isinstance(parsed, dict) else None
    if not isinstance(permissions, list) or not permissions:
        raise ProbeError(
            f"{head}; its last message holds no JSON report: {text[-500:]!r})"
        )
    notes = parsed.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        raise ProbeError(
            f"{service} probe: its report's notes are not a list of lines: {notes!r}"
        )
    return Report(
        tuple(_permission(service, entry) for entry in permissions),
        tuple(n.strip() for n in notes if n.strip()),
    )


def _permission(service: str, entry: object) -> Permission:
    ok = (
        isinstance(entry, dict)
        and isinstance(entry.get("name"), str)
        and isinstance(entry.get("required"), bool)
        and entry.get("status") in STATUSES
        and isinstance(entry.get("fix", ""), str)
    )
    if not ok or (entry["status"] != "ok" and not entry.get("fix", "").strip()):
        raise ProbeError(
            f"{service} probe: its report has an entry this runner cannot read: {entry!r}"
        )
    return Permission(
        entry["name"], entry["required"], entry["status"], entry.get("fix", "")
    )
