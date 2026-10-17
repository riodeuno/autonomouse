"""The Situation block every orchestrator prompt ends with: the facts of the fire, for the
orchestrator to act on (`docs/orchestrator/fire.md`, sys-s16). This module reports and
decides nothing: when to stop starting cards is the orchestrator's.

For one project, at one moment:

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
from autonomouse.board.runs import Run, load_run
from autonomouse.board.schema import BoardValidationError, Card, Project, Stack


@dataclass(frozen=True)
class Situation:
    project: str
    interval_hours: int
    next_fire: datetime | None  # None when no timer fires the wake, or the crontab is unreadable
    timer_error: str | None  # why the crontab could not be read, when it could not
    usage_limit_reset: str | None  # ISO, from a run record's `usage_limit.reset_at`
    ready: list[str]  # card ids

    def text(self) -> str:
        """The block the orchestrator's prompt ends with."""
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
        return f"Situation:\nProject: {self.project}.\n{fire}\n{limit}\n{ready}"


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
    return Situation(
        project=project.slug,
        interval_hours=interval_hours,
        next_fire=fire,
        timer_error=timer_error,
        usage_limit_reset=usage_limit_reset(runs, now),
        ready=[c.id for c in ready(cards, project.slug)],
    )
