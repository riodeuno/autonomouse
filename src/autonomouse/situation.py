"""The Situation block every orchestrator prompt ends with: the facts of the fire, for the
orchestrator to act on (`docs/orchestrator/fire.md`, sys-s16). This module reports and
decides nothing: whether to wait, claim or end is the orchestrator's.

For one project, at one moment:

- whether the project is held — its claim is a run the liveness rule calls alive or stalled
  (`docs/board-schema.md`, "Liveness of a claim") — and when it is, the holder's run id,
  pid, `started_at` and the project's `in_progress` cards. A claim held by a stopped or
  crashed run is not a hold: the run controller takes it over before the session starts.
  A run that waits for the project (`waiting_for`) holds no claim and never counts.
- the alive run already waiting for the project, if any: a run record with `waiting_for`
  naming the project, no recorded end, and a live pid.
- `wake.interval_hours` and the next fire of the wake's crontab line (`timer.next_fire`),
  named only when that line is in the user's crontab (`timer.installed`, sys-u9). With no
  `crontab` command or no wake line, the block says no timer is installed and names no fire;
  when `crontab -l` fails otherwise, it says the next fire is unknown and why. The
  orchestrator's 30-minute stop before a fire applies only to a named fire
  (`docs/orchestrator/fire.md`).
- the latest recorded usage-limit reset still in the future (`usage_limit.reset_at` on the
  run records), or none.
- the ready cards: `todo` with every `depends_on` done, in `position` order — the rule
  `controller.pick` uses (`ready`).

`build` reads the board; `Situation.text()` renders the block `autonomouse run` appends to
the prompt.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autonomouse.board.commands import card_list
from autonomouse.board.runs import (
    Liveness,
    Run,
    RunStatus,
    claim_liveness,
    load_run,
    run_liveness,
)
from autonomouse.board.schema import BoardValidationError, Card, Project, Stack

HELD_STATES = (Liveness.ALIVE, Liveness.STALLED)


@dataclass(frozen=True)
class Holder:
    """The run holding the project's claim: alive or stalled."""

    run_id: str
    pid: int | None
    started_at: str | None
    in_progress: list[str]  # the project's in_progress card ids


@dataclass(frozen=True)
class Waiting:
    """An alive run whose record says `waiting_for` this project."""

    run_id: str
    pid: int | None


@dataclass(frozen=True)
class Situation:
    project: str
    holder: Holder | None
    waiting: Waiting | None
    interval_hours: int
    next_fire: datetime | None  # None when no timer fires the wake, or the crontab is unreadable
    timer_error: str | None  # why the crontab could not be read, when it could not
    usage_limit_reset: str | None  # ISO, from a run record's `usage_limit.reset_at`
    ready: list[str]  # card ids

    def text(self) -> str:
        """The block the orchestrator's prompt ends with."""
        if self.holder is None:
            held = f"Project {self.project} is not held."
        else:
            held = (
                f"Project {self.project} is held by run {self.holder.run_id} "
                f"(pid {self.holder.pid}, started {self.holder.started_at}); its "
                f"in_progress cards: {', '.join(self.holder.in_progress) or 'none'}."
            )
        waiting = (
            "Waiting wake: none."
            if self.waiting is None
            else f"Waiting wake: run {self.waiting.run_id} (pid {self.waiting.pid})."
        )
        if self.next_fire is not None:
            when = f"next fire: {self.next_fire:%Y-%m-%d %H:%M %Z}."
        elif self.timer_error is None:
            when = "no timer is installed, so no fire is due."
        else:
            when = f"next fire unknown: {self.timer_error}."
        fire = f"Wake interval: every {self.interval_hours} hours; {when}"
        limit = (
            "Usage limit: none in force."
            if self.usage_limit_reset is None
            else f"Usage limit: resets at {self.usage_limit_reset}."
        )
        ready = f"Ready cards: {', '.join(self.ready) or 'none'}."
        return f"Situation:\n{held}\n{waiting}\n{fire}\n{limit}\n{ready}"


def ready(cards: list[Card], slug: str) -> list[Card]:
    """`slug`'s ready cards: `todo` with every `depends_on` done, in `position` order — the
    one rule for what a wake may start (`controller.pick` uses it too)."""
    done = {c.id for c in cards if c.stack == Stack.DONE}
    own = sorted((c for c in cards if c.project == slug), key=lambda c: (c.position, c.id))
    return [
        c for c in own if c.stack == Stack.TODO and all(d in done for d in c.depends_on)
    ]


def _runs(root: Path, out: Callable[[str], None] = print) -> list[Run]:
    """Every run record on the board. One that fails validation (`BoardValidationError`: a
    bad file under `runs/`) is printed with `out` naming the record and its error and left
    out, rather than raising before this wake's claim, the way it did before sys-t17."""
    folder = root / "runs"
    if not folder.is_dir():
        return []
    runs = []
    for path in sorted(folder.glob("*.json")):
        try:
            runs.append(load_run(root, path.stem))
        except BoardValidationError as e:
            out(f"run_record_unreadable={e}")
    return runs


def holder(
    root: Path,
    project: Project,
    cards: list[Card],
    *,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> Holder | None:
    """The alive or stalled run holding `project`'s claim, or None (unclaimed, or a claim
    a stopped or crashed run left, which the next wake takes over)."""
    report, run = claim_liveness(root, project, projects_dir=projects_dir, now=now)
    if report is None or report.status not in HELD_STATES:
        return None
    in_progress = [
        c.id
        for c in sorted(cards, key=lambda c: (c.position, c.id))
        if c.project == project.slug and c.stack == Stack.IN_PROGRESS
    ]
    if run is not None:
        return Holder(run.id, run.pid, run.started_at, in_progress)
    # A claimant with no run record: the claim itself is all there is.
    return Holder(project.claim.run_id, project.claim.pid, project.claim.claimed_at, in_progress)


def waiting_run(
    runs: list[Run],
    slug: str,
    *,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> Waiting | None:
    """The alive run already waiting for `slug`: `waiting_for` names it, its record is
    still `alive` (a waiting orchestrator that gives up runs `run end` before it exits),
    and its pid exists (the liveness rule's alive or stalled)."""
    for run in runs:
        if run.waiting_for != slug or run.status != RunStatus.ALIVE:
            continue
        if run_liveness(run, projects_dir=projects_dir, now=now).status in HELD_STATES:
            return Waiting(run.id, run.pid)
    return None


def usage_limit_reset(runs: list[Run], now: datetime) -> str | None:
    """The latest `usage_limit.reset_at` on the run records that is still after `now`, as
    recorded (ISO, UTC); None when no recorded limit is in force."""
    future = []
    for run in runs:
        reset_at = (run.usage_limit or {}).get("reset_at")
        if not reset_at:
            continue
        try:
            at = datetime.fromisoformat(reset_at)
        except ValueError:
            continue
        if at.tzinfo is not None and at > now:
            future.append((at, reset_at))
    return max(future)[1] if future else None


def wake_fire(
    now: datetime, interval_hours: int, *, crontab: str = "crontab"
) -> tuple[datetime | None, str | None]:
    """The wake's next fire and why the crontab could not be read: `(fire, None)` when the
    crontab holds the wake line; `(None, None)` when no timer is installed (no `crontab`
    command, or no wake line); `(None, reason)` when `crontab -l` failed otherwise."""
    # Imported here: timer imports controller, which imports this module.
    from autonomouse.timer import MARKER, TimerError, installed, next_fire

    if shutil.which(crontab) is None:
        return None, None
    try:
        lines = installed(crontab=crontab)
    except TimerError as error:
        return None, str(error)
    if not any(line.rstrip().endswith(f"{MARKER} wake") for line in lines):
        return None, None
    return next_fire(now, interval_hours), None


def build(
    root: Path,
    project: Project,
    *,
    interval_hours: int,
    now: datetime,
    projects_dir: Path | None = None,
    crontab: str = "crontab",
    out: Callable[[str], None] = print,
) -> Situation:
    """Read the board and the user's crontab (`crontab`, the command; a test passes a fake)
    and answer the Situation for `project` at `now` (timezone-aware; the machine's local
    clock, as cron reads it, for the next fire). A run record that fails validation is
    printed with `out` and left out (`_runs`, sys-t17)."""
    if now.tzinfo is None:
        raise ValueError("build needs a timezone-aware `now`")
    fire, timer_error = wake_fire(now, interval_hours, crontab=crontab)
    cards = card_list(root)
    runs = _runs(root, out)
    epoch = now.timestamp()
    return Situation(
        project=project.slug,
        holder=holder(root, project, cards, projects_dir=projects_dir, now=epoch),
        waiting=waiting_run(runs, project.slug, projects_dir=projects_dir, now=epoch),
        interval_hours=interval_hours,
        next_fire=fire,
        timer_error=timer_error,
        usage_limit_reset=usage_limit_reset(runs, now),
        ready=[c.id for c in ready(cards, project.slug)],
    )
