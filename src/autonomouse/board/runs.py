"""Run records, claim liveness, the project claim, and starting a card.

A run record is `runs/<id>.json` (shape in `docs/board-schema.md`), one per agent session
that touches a project, so every session's cost is on the board. Its `kind` says who
started the session: `wake` (`autonomouse run`), `planner` (`autonomouse plan step`, `plan
new` or `plan join`, one record per claude process, naming its planning `thread`), or
`session` (any other agent session that registered itself with `run start`, such as the
human's proxy). `new_run_id` makes a record's id; `run_start` writes it with `status:
alive`; `run_end` marks the clean end and copies in the JSON result `claude -p` printed.
`record_refusal` appends a command's refusal to the record of the run it was run for,
`alive_planner_run` finds the alive planner record for a thread id when a refusal's
`--run-id` names no record of its own, and `run_refused` writes the record of a wake
refused before it started a session.
`liveness` answers whether a claim's session is alive, stalled, stopped or crashed from
three pieces of evidence: the pid, the last write time of the session's transcript, and
whether the run recorded an end. `project_claim` and
`project_release` write a run onto a project, taking over the claim of a stopped or crashed
run and marking that run `taken_over`. `card_start` applies the same liveness rule to a
card's own claim (its `assigned` session), which is why it lives here next to
`project_claim` rather than in `board.commands` — importing `project_show` the other way
round would be circular.

Nothing here commits the board; the orchestrator does that after its writes.
"""

from __future__ import annotations

import enum
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autonomouse import __version__
from autonomouse.board.commands import (
    CommandError,
    _find_card_path,
    card_list,
    project_show,
)
from autonomouse.board.history import append_history, now_iso
from autonomouse.board.paths import worktrees_root as default_worktrees_root
from autonomouse.board.schema import (
    Assigned,
    BoardValidationError,
    Card,
    Claim,
    Project,
    Stack,
    _check_keys,
)
from autonomouse.board.store import (
    _atomic_write_json,
    load_card,
    save_card,
    save_project,
)
from autonomouse.board.worktrees import WorktreeError
from autonomouse.board.worktrees import create as create_worktree_default
from autonomouse.board.worktrees import remove as remove_worktree_default

# The schema's liveness thresholds (docs/board-schema.md, "Liveness of a claim").
ALIVE_WITHIN_S = 10 * 60
STALLED_AFTER_S = 30 * 60

_RUN_ID_RE = re.compile(r"^(\d{8}-\d{6}-[0-9a-f]{4}|seed)$")

_RUN_KEYS = {
    "id",
    "kind",
    "project",
    "thread",
    "session_id",
    "pid",
    "model",
    "effort",
    "started_at",
    "ended_at",
    "cards",
    "jev",
    "notify_failures",
    "refusals",
    "result",
    "usage_limit",
    "exit_code",
    "leftover_processes",
    "end_reason",
    "status",
    "summary",
    "version",
    "interim_branch",
    "interim_pr",
    "waiting_for",
}


class RunStatus(str, enum.Enum):
    """The `status` a run record carries."""

    ALIVE = "alive"
    STOPPED = "stopped"
    CRASHED = "crashed"
    TAKEN_OVER = "taken_over"


class Liveness(str, enum.Enum):
    """What the evidence says about a claim's session right now."""

    ALIVE = "alive"
    STALLED = "stalled"
    STOPPED = "stopped"
    CRASHED = "crashed"
    TAKEN_OVER = "taken_over"


# A claim in one of these states may be taken over; alive and stalled ones are left alone.
TAKEOVER_ALLOWED = {Liveness.STOPPED, Liveness.CRASHED, Liveness.TAKEN_OVER}


class RunKind(str, enum.Enum):
    """The `kind` a run record carries: who started the session it records."""

    WAKE = "wake"
    PLANNER = "planner"
    SESSION = "session"


def new_run_id() -> str:
    """A run record's id: the UTC time and four random hex digits."""
    return f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{os.urandom(2).hex()}"


@dataclass
class Run:
    id: str
    project: str | None
    session_id: str | None
    pid: int | None
    model: str | None
    started_at: str | None
    ended_at: str | None
    cards: list[str]
    jev: list[dict[str, Any]]
    notify_failures: list[dict[str, Any]]
    result: dict[str, Any] | None
    end_reason: str | None
    status: RunStatus
    summary: str | None
    effort: str | None = None
    usage_limit: dict[str, Any] | None = None
    # The orchestrator session's own process exit code (`proc.wait()`), written by
    # `autonomouse run` beside `result` once the process has actually exited; null while
    # it runs, and null on records written before this field existed.
    exit_code: int | None = None
    # How many processes the session left alive that `autonomouse run` ended after it
    # exited (`autonomouse.controller`, sys-u4); null while it runs, and on records
    # written before this field existed.
    leftover_processes: int | None = None
    version: str | None = None
    # The wake's interim branch `wake/<run id>` once the orchestrator made it with
    # `run_interim` (`autonomouse run interim`), and the final PR from it to the default
    # branch once the orchestrator opens it (`docs/orchestrator/conflict-check.md`); both
    # null otherwise.
    interim_branch: str | None = None
    interim_pr: str | None = None
    # The project a waiting wake waited for. Versions before one wake per machine wrote it
    # when a wake started on a project an alive or stalled run held; none writes it now,
    # and records that carry it still load.
    waiting_for: str | None = None
    # Who started the session: a record written before the field existed is a wake's.
    kind: RunKind = RunKind.WAKE
    # The planning thread a `planner` record's session worked on; null on every other kind.
    thread: str | None = None
    # One `{at, command, reason}` per board command refused while it carried this run's
    # `--run-id` (`record_refusal`); `[]` on records written before the field existed.
    refusals: list[dict[str, Any]] = dataclass_field(default_factory=list)
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "project": self.project,
            "thread": self.thread,
            "session_id": self.session_id,
            "pid": self.pid,
            "model": self.model,
            "effort": self.effort,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "cards": list(self.cards),
            "jev": list(self.jev),
            "notify_failures": list(self.notify_failures),
            "refusals": list(self.refusals),
            "result": self.result,
            "usage_limit": self.usage_limit,
            "exit_code": self.exit_code,
            "leftover_processes": self.leftover_processes,
            "end_reason": self.end_reason,
            "status": self.status.value,
            "summary": self.summary,
            "version": self.version,
            "interim_branch": self.interim_branch,
            "interim_pr": self.interim_pr,
            "waiting_for": self.waiting_for,
            **self.extra,
        }


def _type_error(source: str, field: str, want: str, got: Any) -> BoardValidationError:
    return BoardValidationError(
        f"{source}: '{field}' must be {want} — got {type(got).__name__}"
    )


def run_from_dict(data: dict[str, Any], *, source: str) -> Run:
    """Parse and validate a run record: every one of the schema's fields, and any other
    field kept as it is in `Run.extra`.

    `notify_failures` and `refusals` default to `[]`, `kind` to `wake`, and `effort`, `usage_limit`,
    `exit_code`, `leftover_processes`, `version`, `interim_branch`, `interim_pr`, `waiting_for` and
    `thread` to `None` when absent, so a run record written before they existed still
    loads; `save_run` writes them back on the next save. A `kind` outside `RunKind` is
    refused, like an unknown `status`.

    A field this code does not know is kept, not refused, because processes running
    different versions of autonomouse write the same record: when a merge lands while a
    wake runs, the orchestrator's `autonomouse` commands run the new code and the wake's
    long-lived `autonomouse run` still runs the old one. Run 20260922-180917-e38d lost its
    result that way: the old controller refused the `effort` field the new `run end` had
    written (sys-d31).
    """
    if not isinstance(data, dict):
        raise BoardValidationError(f"{source}: run record must be a JSON object")
    extra = {k: v for k, v in data.items() if k not in _RUN_KEYS}
    data = {k: v for k, v in data.items() if k in _RUN_KEYS}
    for name in ("notify_failures", "refusals"):
        if name not in data:
            data = {**data, name: []}
    if "effort" not in data:
        data = {**data, "effort": None}
    if "usage_limit" not in data:
        data = {**data, "usage_limit": None}
    for name in ("exit_code", "leftover_processes"):
        if name not in data:
            data = {**data, name: None}
    if "version" not in data:
        data = {**data, "version": None}
    for name in ("interim_branch", "interim_pr", "waiting_for", "thread"):
        if name not in data:
            data = {**data, name: None}
    if "kind" not in data:
        data = {**data, "kind": RunKind.WAKE.value}
    _check_keys(data, _RUN_KEYS, kind="run", source=source)

    for field in (
        "project",
        "session_id",
        "model",
        "effort",
        "started_at",
        "ended_at",
        "end_reason",
        "summary",
        "version",
        "interim_branch",
        "interim_pr",
        "waiting_for",
        "thread",
    ):
        value = data[field]
        if value is not None and not isinstance(value, str):
            raise _type_error(source, field, "string or null", value)
    if not isinstance(data["id"], str):
        raise _type_error(source, "id", "string", data["id"])
    pid = data["pid"]
    if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool)):
        raise _type_error(source, "pid", "integer or null", pid)
    exit_code = data["exit_code"]
    if exit_code is not None and (
        not isinstance(exit_code, int) or isinstance(exit_code, bool)
    ):
        raise _type_error(source, "exit_code", "integer or null", exit_code)
    leftovers = data["leftover_processes"]
    if leftovers is not None and (
        not isinstance(leftovers, int) or isinstance(leftovers, bool)
    ):
        raise _type_error(source, "leftover_processes", "integer or null", leftovers)
    cards = data["cards"]
    if not isinstance(cards, list) or not all(isinstance(c, str) for c in cards):
        raise BoardValidationError(f"{source}: 'cards' must be a list of strings")
    jev = data["jev"]
    if not isinstance(jev, list) or not all(isinstance(j, dict) for j in jev):
        raise BoardValidationError(f"{source}: 'jev' must be a list of objects")
    notify_failures = data["notify_failures"]
    if not isinstance(notify_failures, list) or not all(
        isinstance(n, dict) for n in notify_failures
    ):
        raise BoardValidationError(
            f"{source}: 'notify_failures' must be a list of objects"
        )
    refusals = data["refusals"]
    if not isinstance(refusals, list) or not all(isinstance(r, dict) for r in refusals):
        raise BoardValidationError(f"{source}: 'refusals' must be a list of objects")
    result = data["result"]
    if result is not None and not isinstance(result, dict):
        raise _type_error(source, "result", "object or null", result)
    usage_limit = data["usage_limit"]
    if usage_limit is not None and not (
        isinstance(usage_limit, dict)
        and set(usage_limit) == {"hit_at", "reset_at", "text"}
        and isinstance(usage_limit["hit_at"], str)
        and isinstance(usage_limit["text"], str)
        and (usage_limit["reset_at"] is None or isinstance(usage_limit["reset_at"], str))
    ):
        raise BoardValidationError(
            f"{source}: 'usage_limit' must be null or {{hit_at, reset_at, text}} "
            f"(strings; reset_at may be null) — got {usage_limit!r}"
        )
    try:
        status = RunStatus(data["status"])
    except ValueError:
        raise BoardValidationError(
            f"{source}: 'status' must be one of "
            f"{', '.join(s.value for s in RunStatus)} — got {data['status']!r}"
        )
    try:
        kind = RunKind(data["kind"])
    except ValueError:
        raise BoardValidationError(
            f"{source}: 'kind' must be one of "
            f"{', '.join(k.value for k in RunKind)} — got {data['kind']!r}"
        )

    return Run(
        id=data["id"],
        project=data["project"],
        session_id=data["session_id"],
        pid=pid,
        model=data["model"],
        effort=data["effort"],
        started_at=data["started_at"],
        ended_at=data["ended_at"],
        cards=list(cards),
        jev=list(jev),
        notify_failures=list(notify_failures),
        refusals=list(refusals),
        result=result,
        end_reason=data["end_reason"],
        status=status,
        summary=data["summary"],
        usage_limit=usage_limit,
        exit_code=exit_code,
        leftover_processes=leftovers,
        version=data["version"],
        interim_branch=data["interim_branch"],
        interim_pr=data["interim_pr"],
        waiting_for=data["waiting_for"],
        kind=kind,
        thread=data["thread"],
        extra=extra,
    )


def run_path(root: Path, run_id: str) -> Path:
    return root / "runs" / f"{run_id}.json"


def load_run(root: Path, run_id: str) -> Run:
    path = run_path(root, run_id)
    if not path.is_file():
        raise CommandError(f"no run record {run_id!r} at {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise BoardValidationError(f"{path}: not valid JSON — {e}") from e
    return run_from_dict(data, source=str(path))


def save_run(root: Path, run: Run) -> None:
    path = run_path(root, run.id)
    data = run.to_dict()
    run_from_dict(data, source=str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, data)


# -------------------------------------------------------------- run records --


def run_start(
    root: Path,
    *,
    run_id: str,
    session_id: str,
    pid: int,
    model: str,
    effort: str | None = None,
    kind: RunKind | str = RunKind.SESSION,
    project: str | None = None,
    thread: str | None = None,
) -> Run:
    """Write `runs/<run_id>.json` with `status: alive` and the installed autonomouse
    version. `effort` is the session's reasoning effort when the caller knows it
    (`autonomouse run` does; a hand-started `run start` records null). `kind` is who
    started the session: `autonomouse run` passes `wake`, the planner `planner` with its
    thread's `project` and `thread`, and a hand-started `run start` keeps the default,
    `session`. Refused if the record exists."""
    if not _RUN_ID_RE.fullmatch(run_id):
        raise CommandError(
            f"run id must look like YYYYMMDD-HHMMSS-<4 hex> — got {run_id!r}"
        )
    if run_path(root, run_id).exists():
        raise CommandError(f"run record {run_id!r} already exists")
    run = Run(
        id=run_id,
        kind=RunKind(kind),
        project=project,
        thread=thread,
        session_id=session_id,
        pid=pid,
        model=model,
        effort=effort,
        started_at=now_iso(),
        ended_at=None,
        cards=[],
        jev=[],
        notify_failures=[],
        result=None,
        end_reason=None,
        status=RunStatus.ALIVE,
        summary=None,
        version=__version__,
    )
    save_run(root, run)
    return run


def record_notify_failure(root: Path, run_id: str, *, text: str, error: str) -> Run:
    """Append a failed send to the run record's `notify_failures`.

    Called by `notify()` (`autonomouse.notify`) so a bad channel, a missing chat id,
    or a network problem never stops a wake — the run's own record carries the
    failure instead of raising back into the caller.
    """
    run = load_run(root, run_id)
    run.notify_failures.append({"at": now_iso(), "text": text, "error": error})
    save_run(root, run)
    return run


def record_refusal(root: Path, run_id: str, command: str, reason: str) -> Run:
    """Append `{at, command, reason}` to the run record's `refusals`: `command` (such as
    `card ask`) refused with `reason` while it carried this run's id.

    The CLI calls it for every refusal of a command given `--run-id` (`cli._fail`). Raises `CommandError` when `run_id` names no
    record, and `BoardValidationError` when the record fails validation; the caller still
    reports the original refusal.
    """
    run = load_run(root, run_id)
    run.refusals.append({"at": now_iso(), "command": command, "reason": reason})
    save_run(root, run)
    return run


def alive_planner_run(root: Path, thread: str) -> Run | None:
    """The one alive run record of kind `planner` whose `thread` is `thread`, or None.

    A planner session runs its board commands with `--run-id <thread-id>` (`docs/planner.md`),
    not its own run record's id, which `new_run_id` gives a different shape from a thread's
    (`docs/board-schema.md`): `record_refusal`'s exact-id lookup finds no record there. `cli._fail`
    calls this lookup next, so a refusal a planner session's command raises still lands
    somewhere: on the run record of the claude process now working that thread, which
    `run_start` wrote with `kind=planner, thread=<thread-id>` (`autonomouse.planner`). A
    record that fails to parse is skipped, since the refusal must still print either way.
    """
    folder = root / "runs"
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.json")):
        try:
            run = run_from_dict(json.loads(path.read_text()), source=str(path))
        except (BoardValidationError, ValueError, OSError):
            continue
        if (
            run.kind == RunKind.PLANNER
            and run.thread == thread
            and run_liveness(run).status == Liveness.ALIVE
        ):
            return run
    return None


def run_refused(
    root: Path,
    *,
    command: str,
    reason: str,
    model: str | None,
    effort: str | None,
) -> Run:
    """Write the record of a wake `autonomouse run` refused before it started a session
    (a config value off its allowed set): kind `wake`, no session, no project, `stopped`
    at once, with the one refusal in `refusals` and in `summary`."""
    at = now_iso()
    run = Run(
        id=new_run_id(),
        kind=RunKind.WAKE,
        project=None,
        session_id=None,
        pid=None,
        model=model,
        effort=effort,
        started_at=at,
        ended_at=at,
        cards=[],
        jev=[],
        notify_failures=[],
        result=None,
        end_reason=None,
        status=RunStatus.STOPPED,
        summary=f"refused: {command} - {reason}",
        version=__version__,
    )
    save_run(root, run)
    return record_refusal(root, run.id, command, reason)


def run_set_end_reason(root: Path, run_id: str, reason: str) -> Run:
    """Set a run record's `end_reason`.

    Called by the `SessionEnd` hook (`autonomouse.session_end_hook`) once its own
    session ends, keyed by the run id the hook read from `AUTONOMOUSE_RUN_ID` (set by
    `autonomouse run` on every orchestrator session it launches, `controller.py`'s
    `_start`). Values in practice: `completed` or `interrupted` (the hook's own read of
    whether the session's last turn finished cleanly — see that module's docstring for
    why: a headless session's own hook payload reports `other` alike for a clean exit
    and one cut short by a signal), or, for an interactive session, whatever Claude
    Code's hook payload names (`clear`, `logout`, `prompt_input_exit`). A run a crashed
    claim's takeover already marked `crashed` (`project_claim`, no hook ever having
    fired) is left as it is — the hook runs once, at most, per real session end, and
    only ever adds a reason where there was none.
    """
    run = load_run(root, run_id)
    if run.end_reason is None:
        run.end_reason = reason
        save_run(root, run)
    return run


_JEV_ENTRY_REQUIRED = (
    "question",
    "card",
    "source",
    "answer",
    "confidence",
    "applied",
)
_JEV_ENTRY_OPTIONAL = {"reason", "tier"}


def _validated_jev_entry(data: Any) -> dict[str, Any]:
    """Check a `--jev` payload against the run record's `jev` entry shape
    (`docs/board-schema/run.md`) before it is appended: an object holding exactly
    `question, card, source, answer, confidence, applied`, plus the optional
    `reason` (defaults to `None` when absent, since the schema allows it null or
    missing) and, on a `size` entry, `tier` — the `model/effort` that size maps to.
    `tier` is kept only when the caller gives it, since no other question carries
    one. No type checks beyond "is this a JSON object" — `save_run`'s own
    `run_from_dict` call already requires every `jev` entry to be an object; the
    orchestrator, not this command, knows what a `question` or `source` value means.
    """
    if not isinstance(data, dict):
        raise CommandError(f"--jev must be a JSON object — got {data!r}")
    present = set(data)
    missing = set(_JEV_ENTRY_REQUIRED) - present
    if missing:
        raise CommandError(
            f"--jev is missing field(s): {', '.join(sorted(missing))}"
        )
    extra = present - set(_JEV_ENTRY_REQUIRED) - _JEV_ENTRY_OPTIONAL
    if extra:
        raise CommandError(
            f"--jev has unexpected field(s): {', '.join(sorted(extra))}"
        )
    entry = {key: data[key] for key in _JEV_ENTRY_REQUIRED}
    entry["reason"] = data.get("reason")
    if "tier" in data:
        entry["tier"] = data["tier"]
    return entry


def run_note(
    root: Path,
    run_id: str,
    *,
    card: str | None = None,
    jev: dict[str, Any] | None = None,
    interim_pr: str | None = None,
) -> Run:
    """Append to a run record's `cards` and/or `jev` lists, and/or set its `interim_pr` —
    the fields `docs/board-schema.md` gives no other writer than `autonomouse run` (for
    `cards`, at wake start) and the orchestrator (after that): before this, every later
    append was a hand `jq` edit of `runs/<id>.json` (`docs/orchestrator.md`'s "What the
    commands do not do yet", `sys-b11`). `interim_pr` is the final PR the orchestrator
    opens from the wake's interim branch at the wake's end (sys-i21,
    `docs/orchestrator/conflict-check.md`).

    Refused when none of `card`, `jev` and `interim_pr` is given, when `run_id` names no
    run record (`load_run`), or when `jev` fails `_validated_jev_entry`. Several may be
    given in one call; each applies independently, and the record is saved once, still
    passing `run_from_dict`'s validation (`save_run` runs it before writing).
    """
    if card is None and jev is None and interim_pr is None:
        raise CommandError("run note needs --card, --jev or --interim-pr")
    run = load_run(root, run_id)
    if card is not None:
        run.cards.append(card)
    if jev is not None:
        run.jev.append(_validated_jev_entry(jev))
    if interim_pr is not None:
        run.interim_pr = interim_pr
    save_run(root, run)
    return run


def run_interim(root: Path, run_id: str) -> Run:
    """Make the wake's interim branch `wake/<run id>` on the origin of the run's project,
    from `origin/<default_branch>`, and write it as the run record's `interim_branch`
    (`autonomouse run interim`). The orchestrator runs it only when the three reshaping
    moves of `docs/orchestrator/conflict-check.md` failed; nothing else makes the branch.

    A branch already on origin is left as it is, and a record that already names it is not
    written again, so a second call changes nothing. Refused, with nothing written, when
    `run_id` names no run record, when the record names no project, or when git fails
    (`_push_interim_branch`). Commits nothing; the orchestrator commits the board.
    """
    run = load_run(root, run_id)
    if run.project is None:
        raise CommandError(
            f"run {run_id!r} names no project, so there is no repository to make its "
            "interim branch in; a run that waits for a project claims it first"
        )
    project = project_show(root, run.project)
    branch = f"wake/{run_id}"
    _push_interim_branch(
        Path(project.repo_path), branch=branch, default_branch=project.default_branch
    )
    if run.interim_branch != branch:
        run.interim_branch = branch
        save_run(root, run)
    return run


def _push_interim_branch(repo_path: Path, *, branch: str, default_branch: str) -> None:
    """Create `branch` on `repo_path`'s `origin` from `origin/<default_branch>`, so `card
    start`'s worktree creation (`origin/<base_branch>`) and the workers' `git merge
    origin/<base_branch>` find it; do nothing when origin already has it. Raises
    `CommandError` with git's own message when git fails."""

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=repo_path, capture_output=True, text=True, check=False
        )

    def fail(args: tuple[str, ...], done: subprocess.CompletedProcess[str]) -> CommandError:
        return CommandError(
            f"could not create the interim branch {branch} on origin of {repo_path}: "
            f"git {' '.join(args)}: {done.stderr.strip()}"
        )

    # `--exit-code`: 0 when origin has the branch, 2 when it has not.
    probe = ("ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}")
    done = git(*probe)
    if done.returncode == 0:
        return
    if done.returncode != 2:
        raise fail(probe, done)
    for args in (
        ("fetch", "-q", "origin"),
        ("push", "-q", "origin", f"origin/{default_branch}:refs/heads/{branch}"),
    ):
        done = git(*args)
        if done.returncode != 0:
            raise fail(args, done)


def run_end(
    root: Path,
    run_id: str,
    *,
    result: dict[str, Any] | None = None,
    summary: str | None = None,
    exit_code: int | None = None,
    leftover_processes: int | None = None,
) -> Run:
    """Record a run's end: `ended_at` (first end only), the captured `result`, a `summary`,
    the process's `exit_code`, and `leftover_processes`, the count of processes the session
    left alive that `autonomouse run` ended.

    Both the orchestrator's clean end and `autonomouse run`'s copy of the printed result
    call this, in either order; an `alive` record becomes `stopped`. A record another run
    already marked `crashed` or `taken_over` keeps that status and still gets the result.
    `exit_code` is only known once the process has actually exited (`proc.wait()`), so the
    orchestrator's own clean-end call never passes it; `autonomouse run`'s call does.
    """
    run = load_run(root, run_id)
    if run.ended_at is None:
        run.ended_at = now_iso()
    if result is not None:
        run.result = result
    if summary is not None:
        run.summary = summary
    if exit_code is not None:
        run.exit_code = exit_code
    if leftover_processes is not None:
        run.leftover_processes = leftover_processes
    if run.status == RunStatus.ALIVE:
        run.status = RunStatus.STOPPED
    save_run(root, run)
    return run


def run_end_unchecked(
    root: Path,
    run_id: str,
    *,
    result: dict[str, Any] | None,
    summary: str,
    usage_limit: dict[str, Any] | None,
    exit_code: int | None = None,
    leftover_processes: int | None = None,
) -> None:
    """`run_end` for a record `run_from_dict` refuses: the same end written straight into
    the record's JSON, every other field left exactly as it is.

    Sets `ended_at` when unset, `result` when there is one, `summary` only when the
    record has none, `usage_limit`, `exit_code` and `leftover_processes` when there is one, and
    `status` `stopped` when it was `alive`. `autonomouse run` calls it when a record
    written by another version of autonomouse fails this version's validation, so the
    session's result still reaches the record. Raises `BoardValidationError` when the file
    is not a JSON object.
    """
    path = run_path(root, run_id)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise BoardValidationError(f"{path}: not valid JSON — {e}") from e
    if not isinstance(data, dict):
        raise BoardValidationError(f"{path}: run record must be a JSON object")
    if data.get("ended_at") is None:
        data["ended_at"] = now_iso()
    if result is not None:
        data["result"] = result
    if data.get("summary") is None:
        data["summary"] = summary
    if usage_limit is not None:
        data["usage_limit"] = usage_limit
    if exit_code is not None:
        data["exit_code"] = exit_code
    if leftover_processes is not None:
        data["leftover_processes"] = leftover_processes
    if data.get("status") == RunStatus.ALIVE.value:
        data["status"] = RunStatus.STOPPED.value
    _atomic_write_json(path, data)


# ----------------------------------------------------------------- liveness --


def claude_projects_dir() -> Path:
    """Where Claude Code keeps session transcripts: `$CLAUDE_CONFIG_DIR/projects`,
    by default `~/.claude/projects`."""
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base) if base else Path.home() / ".claude") / "projects"


def transcript_last_write(session_id: str, projects_dir: Path) -> float | None:
    """Latest mtime (epoch seconds) of the session's transcript and its sub-agents'.

    The transcript is `<projects_dir>/<cwd-slug>/<session_id>.jsonl`. Every project
    folder is searched, because a session id is unique and the caller may not know
    the session's working directory. The files in `<session_id>/subagents/` count too:
    while the orchestrator waits on its workers, only their transcripts grow, and the
    orchestrator is not stalled. None when no transcript exists.
    """
    if not projects_dir.is_dir():
        return None
    times = []
    for slug_dir in projects_dir.iterdir():
        main = slug_dir / f"{session_id}.jsonl"
        if main.is_file():
            times.append(main.stat().st_mtime)
        subagents = slug_dir / session_id / "subagents"
        if subagents.is_dir():
            times.extend(p.stat().st_mtime for p in subagents.iterdir() if p.is_file())
    return max(times) if times else None


def pid_exists(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class LivenessReport:
    status: Liveness
    pid: int | None
    pid_alive: bool
    session_id: str | None
    transcript_age_s: float | None
    recorded_end: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "pid": self.pid,
            "pid_alive": self.pid_alive,
            "session_id": self.session_id,
            "transcript_age_s": (
                None if self.transcript_age_s is None else round(self.transcript_age_s)
            ),
            "recorded_end": self.recorded_end,
            "reason": self.reason,
        }


def liveness(
    *,
    session_id: str | None,
    pid: int | None,
    recorded_end: bool,
    taken_over: bool = False,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> LivenessReport:
    """Apply the schema's liveness rule to one session.

    - taken over (the record says so) → taken_over
    - pid gone → stopped when the run recorded an end (`ended_at` or `result`), else crashed
    - pid exists, transcript written in the last 10 minutes → alive
    - pid exists, transcript older than 30 minutes, or none at all → stalled
    - pid exists, transcript 10 to 30 minutes old → alive. The schema names no state for
      this gap; it is not stalled yet, and both alive and stalled claims are left alone.
    """
    projects_dir = projects_dir or claude_projects_dir()
    now = datetime.now(UTC).timestamp() if now is None else now
    alive_pid = pid_exists(pid)
    last = transcript_last_write(session_id, projects_dir) if session_id else None
    age = None if last is None else max(0.0, now - last)

    def report(status: Liveness, reason: str) -> LivenessReport:
        return LivenessReport(
            status=status,
            pid=pid,
            pid_alive=alive_pid,
            session_id=session_id,
            transcript_age_s=age,
            recorded_end=recorded_end,
            reason=reason,
        )

    if taken_over:
        return report(Liveness.TAKEN_OVER, "the run record says another run took over")
    if not alive_pid:
        if recorded_end:
            return report(Liveness.STOPPED, "pid gone; the run recorded its end")
        return report(Liveness.CRASHED, "pid gone; the run recorded no end")
    if age is None:
        return report(Liveness.STALLED, "pid exists; no transcript found")
    minutes = round(age / 60, 1)
    if age <= ALIVE_WITHIN_S:
        return report(
            Liveness.ALIVE, f"pid exists; transcript written {minutes} min ago"
        )
    if age > STALLED_AFTER_S:
        return report(
            Liveness.STALLED, f"pid exists; transcript silent for {minutes} min"
        )
    return report(
        Liveness.ALIVE,
        f"pid exists; transcript quiet for {minutes} min (under the 30 min stall limit)",
    )


def run_liveness(
    run: Run, *, projects_dir: Path | None = None, now: float | None = None
) -> LivenessReport:
    return liveness(
        session_id=run.session_id,
        pid=run.pid,
        recorded_end=run.ended_at is not None or run.result is not None,
        taken_over=run.status == RunStatus.TAKEN_OVER,
        projects_dir=projects_dir,
        now=now,
    )


def run_status(
    root: Path,
    run_id: str,
    *,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> LivenessReport:
    """alive / stalled / stopped / crashed / taken_over for one run record."""
    return run_liveness(load_run(root, run_id), projects_dir=projects_dir, now=now)


# ------------------------------------------------------------ project claim --


def claim_liveness(
    root: Path,
    project: Project,
    *,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> tuple[LivenessReport | None, Run | None]:
    """Liveness of the run holding `project`'s claim, and that run's record.

    `(None, None)` when the project is unclaimed. A claimant with no run record is
    judged from the claim's own session id and pid, as a run that recorded no end.
    """
    held_by = project.claim.run_id
    if held_by is None:
        return None, None
    if run_path(root, held_by).is_file():
        held_run = load_run(root, held_by)
        return run_liveness(held_run, projects_dir=projects_dir, now=now), held_run
    report = liveness(
        session_id=project.claim.session_id,
        pid=project.claim.pid,
        recorded_end=False,
        projects_dir=projects_dir,
        now=now,
    )
    return report, None


@dataclass
class ClaimOutcome:
    project: str
    run_id: str
    took_over: str | None  # the run id whose claim was taken over, if any
    previous: LivenessReport | None


def project_claim(
    root: Path,
    slug: str,
    *,
    run_id: str,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> ClaimOutcome:
    """Write `run_id` as the project's claim, copying session id, pid and model from its
    run record, and set the record's `project`.

    A claim held by another run is taken over only when that run is stopped, crashed or
    already taken over; its record is then marked `taken_over` (with `end_reason`
    `crashed` when it had none and crashed). An alive or stalled claim is refused.
    Claiming a project this run already holds changes nothing.
    """
    run = load_run(root, run_id)
    if run.status != RunStatus.ALIVE:
        raise CommandError(
            f"run {run_id!r} is {run.status.value}; only an alive run may claim"
        )
    project = project_show(root, slug)
    held_by = project.claim.run_id
    if held_by == run_id:
        return ClaimOutcome(project=slug, run_id=run_id, took_over=None, previous=None)
    if run.project is not None and run.project != slug:
        raise CommandError(
            f"run {run_id!r} already claims project {run.project!r}; one run works one project"
        )

    previous, previous_run = claim_liveness(
        root, project, projects_dir=projects_dir, now=now
    )
    if previous is not None and previous.status not in TAKEOVER_ALLOWED:
        raise CommandError(
            f"project {slug!r} is claimed by run {held_by!r}, which is "
            f"{previous.status.value} ({previous.reason}); alive and stalled claims "
            "are left alone"
        )

    ts = now_iso()
    if previous_run is not None and previous_run.status != RunStatus.TAKEN_OVER:
        if previous_run.end_reason is None and previous.status == Liveness.CRASHED:
            previous_run.end_reason = "crashed"
        previous_run.status = RunStatus.TAKEN_OVER
        save_run(root, previous_run)

    project.claim = Claim(
        run_id=run_id,
        session_id=run.session_id,
        pid=run.pid,
        model=run.model,
        claimed_at=ts,
    )
    project.updated_at = ts
    save_project(root / "projects" / slug / "project.json", project)
    run.project = slug
    save_run(root, run)
    return ClaimOutcome(
        project=slug, run_id=run_id, took_over=held_by, previous=previous
    )


def project_release(root: Path, slug: str, *, run_id: str) -> None:
    """Clear the project's claim. Refused unless `run_id` holds it."""
    project = project_show(root, slug)
    held_by = project.claim.run_id
    if held_by is None:
        raise CommandError(f"project {slug!r} is not claimed")
    if held_by != run_id:
        raise CommandError(
            f"project {slug!r} is claimed by run {held_by!r}, not {run_id!r}"
        )
    project.claim = Claim(
        run_id=None, session_id=None, pid=None, model=None, claimed_at=None
    )
    project.updated_at = now_iso()
    save_project(root / "projects" / slug / "project.json", project)


# -------------------------------------------------------------- card start --


def card_start(
    root: Path,
    card_id: str,
    *,
    branch: str,
    base_branch: str,
    session_id: str,
    pid: int,
    model: str,
    effort: str,
    agent_type: str,
    run_id: str,
    project: str | None = None,
    projects_dir: Path | None = None,
    now: float | None = None,
    worktrees_root: Path | None = None,
    create_worktree: Callable[[Path, Path, str, str], None] | None = None,
) -> Card:
    """Start a card: `todo` -> `in_progress`, or resume an `in_progress` card whose
    own claim has died.

    Needs a branch, base branch, session id, pid, model, effort and agent type —
    refused if any is missing (`card start` does not invent the branch name or
    compute the base branch itself; that is the caller's job, `int-g2`'s once it
    exists). The working folder is not one of the caller's inputs: every card gets
    one at the fixed path `<worktrees_root>/<project>/<card-id>`
    (`docs/board-schema.md`'s worktree lifecycle, `sys-e6`), which this creates from
    `base_branch` in the project's own repository (`repo_path`) the first time, and
    reuses as-is on every later start (an ordinary resume finds it already there). A
    card that already records a `worktree` keeps it, even when the root has moved
    since (`sys-s59` moved the default root): its branch is checked out there.

    Refused unless the card's project is claimed by `run_id` — `docs/board-schema.md`'s
    rule that only the run holding a project's claim may start its cards. Refused if the
    card sits anywhere but `todo`, or an `in_progress` card whose own assigned session the
    schema's liveness rule calls alive or stalled: only a claim that rule calls stopped,
    crashed or already taken over may be started again, which is how a resumed session
    picks the card back up from its own `handoff` without another session doing the same
    at the same time. Refused (`CommandError`, wrapping git's own message) when creating
    the working folder fails — a bad `repo_path`, an unfetchable `base_branch`, or
    `branch` already checked out elsewhere.

    On success, `assigned`, `worktree`, `branch` and `base_branch` are (re)written, the
    stack becomes `in_progress`, and `handoff.run_id`/`handoff.updated_at` record which
    run now holds it — `last_step`/`next_step` are left untouched, since a resume reads
    them to continue.
    """
    given = {
        "--branch": branch,
        "--base-branch": base_branch,
        "--session-id": session_id,
        "--model": model,
        "--effort": effort,
        "--agent-type": agent_type,
    }
    missing = [flag for flag, value in given.items() if not (value and value.strip())]
    if pid is None:
        missing.append("--pid")
    if missing:
        raise CommandError(f"card start needs {', '.join(missing)}")

    path = _find_card_path(root, card_id, project)
    card = load_card(path)

    project_obj = project_show(root, card.project)
    held_by = project_obj.claim.run_id
    if held_by != run_id:
        state = "unclaimed" if held_by is None else f"claimed by run {held_by!r}"
        raise CommandError(
            f"project {card.project!r} is {state}, not by run {run_id!r} — "
            "claim it first"
        )

    from_stack = card.stack
    note = "started"
    if from_stack == Stack.IN_PROGRESS:
        report = liveness(
            session_id=card.assigned.session_id,
            pid=card.assigned.pid,
            recorded_end=False,
            projects_dir=projects_dir,
            now=now,
        )
        if report.status not in TAKEOVER_ALLOWED:
            raise CommandError(
                f"card {card_id!r} is held by a live session "
                f"({card.assigned.session_id!r}, {report.status.value}: {report.reason})"
            )
        note = f"resumed; previous claim was {report.status.value} ({report.reason})"
    elif from_stack != Stack.TODO:
        raise CommandError(
            f"cannot start card {card_id!r} from {from_stack.value!r} — only `todo`, "
            "or `in_progress` under a dead claim, may be started"
        )

    if card.worktree:
        worktree_path = Path(card.worktree)
    else:
        root_wt = worktrees_root if worktrees_root is not None else default_worktrees_root()
        worktree_path = root_wt / card.project / card_id
    make_worktree = create_worktree if create_worktree is not None else create_worktree_default
    try:
        make_worktree(
            Path(project_obj.repo_path), worktree_path, branch=branch, base_branch=base_branch
        )
    except WorktreeError as e:
        raise CommandError(f"card start could not create the working folder: {e}") from e

    ts = now_iso()
    card.assigned = Assigned(
        model=model,
        effort=effort,
        agent_type=agent_type,
        session_id=session_id,
        pid=pid,
        picked_up_at=ts,
    )
    card.worktree = str(worktree_path)
    card.branch = branch
    card.base_branch = base_branch
    card.stack = Stack.IN_PROGRESS
    card.handoff.run_id = run_id
    card.handoff.updated_at = ts
    append_history(
        card,
        run_id=run_id,
        to=Stack.IN_PROGRESS.value,
        from_=from_stack.value,
        note=note,
    )
    save_card(path, card)
    return card


# --------------------------------------------------------- worktree cleanup --


def cleanup_worktrees(
    root: Path,
    *,
    pr_state: Callable[[str], str | None],
    remove_worktree: Callable[[Path, Path], None] | None = None,
    run_id: str = "cleanup",
    projects_dir: Path | None = None,
    now: float | None = None,
    out: Callable[[str], None] = lambda line: None,
) -> list[str]:
    """Remove the working folder of every `done` card whose PR has merged and of every
    `cancelled` card, and record it on the card (`docs/board-schema.md`, the `worktree`
    field: a worktree `card start` created stays until its card is done and its PR
    merged, `sys-e6`, or until its card is cancelled, `sys-h10`; the next
    `autonomouse run` then takes it back out). `git worktree remove` keeps the card's
    branch, so the commits on it survive: a split parent's children reuse them.

    Skips a card with no `worktree` recorded, or whose working folder is already gone
    (removed by an earlier run, or by hand).

    A `done` card with no PR (a no-code card) is skipped with no call to `pr_state`.
    `pr_state(card.pr)` decides the rest: anything but `"MERGED"`, including `None`
    (the check itself failed — no network, `gh` missing, not authenticated), leaves the
    card for a later run to try again; nothing is logged for that, since it is the
    ordinary case for a card still under review.

    A `cancelled` card has no worker and nothing left to merge, so its folder goes
    whatever its PR's state. The exception is a card whose project an alive or stalled
    run holds (`claim_liveness`): `card cancel` works from any stack, so a card
    cancelled during that run may still have its worker in the folder. The first run
    after that claim ends removes it.

    Never raises: a `WorktreeError` removing one card's folder is logged through
    `out` and skipped, and does not stop the rest. Returns the ids of the cards
    cleaned, each with a history row that stays in its stack (`done -> done` or
    `cancelled -> cancelled`) recording the removal.
    """
    remove = remove_worktree if remove_worktree is not None else remove_worktree_default
    cleaned = []
    candidates = card_list(root, stack=Stack.DONE.value) + card_list(
        root, stack=Stack.CANCELLED.value
    )
    for card in candidates:
        if not card.worktree:
            continue
        worktree_path = Path(card.worktree)
        if not worktree_path.exists():
            continue
        if card.stack == Stack.DONE:
            if not card.pr or pr_state(card.pr) != "MERGED":
                continue
            project_obj = project_show(root, card.project)
            why = f"PR {card.pr} merged"
        else:
            project_obj = project_show(root, card.project)
            holder, _ = claim_liveness(root, project_obj, projects_dir=projects_dir, now=now)
            if holder is not None and holder.status not in TAKEOVER_ALLOWED:
                continue
            why = "card cancelled"
        try:
            remove(Path(project_obj.repo_path), worktree_path)
        except WorktreeError as e:
            out(f"worktree_cleanup_failed card={card.id} error={e}")
            continue
        append_history(
            card,
            run_id=run_id,
            to=card.stack.value,
            from_=card.stack.value,
            note=f"worktree removed ({worktree_path}): {why}",
        )
        save_card(_find_card_path(root, card.id, card.project), card)
        cleaned.append(card.id)
        out(f"worktree_removed card={card.id} path={worktree_path}")
    return cleaned
