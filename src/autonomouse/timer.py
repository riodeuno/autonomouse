"""Install, inspect and remove the crontab lines that wake `autonomouse run`, run the
morning review and poll Telegram.

Scheduling is cron, so the same mechanism works on macOS later. `install` puts three lines
in the user's crontab (read with `crontab -l`, written with `crontab -`), each ending with
the marker comment `# autonomouse <job>` so `install` replaces only its own lines and
`remove` deletes only them:

- `wake`: `0 */N * * *` runs `autonomouse run`, N from the config key `wake.interval_hours`
  (default 4; a whole number from 1 to 24, else `install` fails naming the key). When the
  config key `wake.start` holds a time (ISO-8601, as the installer writes it), the line
  fires at that time's minute, on its hour and every N hours after it round the clock
  (`M H1,H2,... * * *`), and runs `autonomouse run` only from that time on: the command
  opens with a test of the clock against it, so the first scheduled wake is the one at
  `wake.start` (a time that cannot be read fails `install`, naming the key);
- `review`: at 06:00, `review daily`, `review behaviour --since 1` and `review improve`,
  each only after the one before succeeded;
- `plan`: every five minutes, `plan step`, so a Telegram reply reaches its card or thread
  within minutes (`plan step` polls Telegram, routes answers, runs the planner only for
  threads with an unanswered human message, and takes its own lock).

Each line runs the invocation `locate().cli` gives (`autonomouse.home`) with a PATH of
`$HOME/.local/bin`, then, at install time, the folder `shutil.which` finds each tool a wake
calls in — `claude`, `uv`, `gh` and `git`, in that order — each added once and only when it
is not already in the list, then `/usr/local/bin:/usr/bin:/bin`, and appends its output to
one log file per job in the state folder (`~/.local/state/autonomouse/`). `install` fails,
naming the tool, when `shutil.which` cannot find one of these four on the installing user's
own PATH: a wake a cron job starts must find every tool a hand-started wake finds. A fire
missed while the machine is off is not run later. The list of cron lines lives here only.

`crontab` is a parameter, and so is the invocation, so a test never touches the machine's
real crontab. `project new` calls `install()` with no arguments.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from autonomouse.config import load_config
from autonomouse.controller import default_state_dir
from autonomouse.home import locate

MARKER = "# autonomouse"
JOBS = ("wake", "review", "plan")
INTERVAL_KEY = "wake.interval_hours"
START_KEY = "wake.start"


class TimerError(Exception):
    """`crontab` is missing or refused a call, `wake.interval_hours` is not 1 to 24, or
    `wake.start` is not a time."""


def interval_hours(config: dict | None = None) -> int:
    """`wake.interval_hours` from the config: a whole number from 1 to 24, else `TimerError`
    naming the key."""
    config = config if config is not None else load_config()
    value = config.get("wake", {}).get("interval_hours")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 24:
        raise TimerError(f"{INTERVAL_KEY} must be a whole number from 1 to 24; got {value!r}")
    return value


def wake_start(config: dict | None = None) -> datetime | None:
    """`wake.start` from the config as a local time to the minute, or None when it is unset
    (empty). A time with no UTC offset is read as local time. Raises `TimerError` naming
    the key when the value is not an ISO-8601 time."""
    config = config if config is not None else load_config()
    value = config.get("wake", {}).get("start", "")
    if value == "":
        return None
    try:
        start = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        start = None
    if start is None:
        raise TimerError(
            f"{START_KEY} must be an ISO-8601 time such as 2026-09-29T09:00+02:00, or empty; "
            f"got {value!r}"
        )
    return start.astimezone().replace(second=0, microsecond=0)


def wake_hours(start: datetime, interval_hours: int) -> list[int]:
    """The hours the wake line fires on when it is anchored on `start`: `start`'s hour and
    every `interval_hours` after it, round the clock, in order."""
    return sorted({(start.hour + offset) % 24 for offset in range(0, 24, interval_hours)})


def next_fire(now: datetime, interval_hours: int, start: datetime | None = None) -> datetime:
    """When the wake line next fires after `now` and runs a wake, on `now`'s own clock
    (local time, as cron reads it; `status` passes `datetime.now().astimezone()`).

    With no `start`, the line is `0 */N * * *`: the next whole hour that is a multiple of N
    today, else 00:00 tomorrow. With a `start`, the next of `wake_hours` at `start`'s
    minute, on `start`'s clock, that is not before `start`: `start` itself while it is
    still ahead."""
    if start is not None:
        earliest = max(now, start - timedelta(microseconds=1)).astimezone(start.tzinfo)
        day = earliest.replace(hour=0, minute=0, second=0, microsecond=0)
        for days in range(2):
            for hour in wake_hours(start, interval_hours):
                candidate = (day + timedelta(days=days)).replace(hour=hour, minute=start.minute)
                if candidate > earliest:
                    return candidate
    today = now.replace(minute=0, second=0, microsecond=0)
    for hour in range(0, 24, interval_hours):
        candidate = today.replace(hour=hour)
        if candidate > now:
            return candidate
    return today.replace(hour=0) + timedelta(days=1)


def cron_lines(
    interval_hours: int,
    invocation: str,
    *,
    home: Path,
    state_dir: Path,
    extra_path_dirs: tuple[str, ...] = (),
    start: datetime | None = None,
) -> list[str]:
    """The three crontab lines, in the order wake, review, plan. `extra_path_dirs` are
    inserted into PATH after `home/.local/bin` and before the fixed system folders. With a
    `start` (local time, `wake_start`), the wake line fires on `wake_hours` at `start`'s
    minute and runs a wake only once the clock, read to the minute as `YYYYmmddHHMM`, has
    reached `start` (each `%` escaped as `\\%`, since cron reads a bare one as a line
    break)."""
    path = ":".join([f"{home}/.local/bin", *extra_path_dirs, "/usr/local/bin", "/usr/bin", "/bin"])
    wake_schedule, wake_command = f"0 */{interval_hours} * * *", f"{invocation} run"
    if start is not None:
        hours = ",".join(str(hour) for hour in wake_hours(start, interval_hours))
        wake_schedule = f"{start.minute} {hours} * * *"
        clock = r'"$(date +\%Y\%m\%d\%H\%M)"'
        wake_command = f"[ {clock} -ge {start:%Y%m%d%H%M} ] && {wake_command}"

    def line(schedule: str, job: str, command: str) -> str:
        log = shlex.quote(str(state_dir / f"{job}.log"))
        return f"{schedule} export PATH={path}; {command} >>{log} 2>&1 {MARKER} {job}"

    review = (
        f"({invocation} review daily && {invocation} review behaviour --since 1 && "
        f"{invocation} review improve)"
    )
    return [
        line(wake_schedule, "wake", wake_command),
        line("0 6 * * *", "review", review),
        line("*/5 * * * *", "plan", f"{invocation} plan step"),
    ]


def _is_ours(line: str) -> bool:
    return any(line.rstrip().endswith(f"{MARKER} {job}") for job in JOBS)


def _read(crontab: str) -> list[str]:
    try:
        result = subprocess.run([crontab, "-l"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise TimerError(
            f"{crontab} not found: install cron (on Ubuntu, `sudo apt install cron`), then "
            f"run `autonomouse timer install` again"
        ) from None
    if result.returncode != 0:
        if not result.stdout.strip() and "no crontab" in result.stderr.lower():
            return []
        raise TimerError(f"{crontab} -l failed: {result.stderr.strip()}")
    return result.stdout.splitlines()


def _write(crontab: str, lines: list[str]) -> None:
    text = "".join(f"{line}\n" for line in lines)
    result = subprocess.run(
        [crontab, "-"], input=text, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise TimerError(f"{crontab} - failed: {result.stderr.strip()}")


REQUIRED_TOOLS = ("claude", "uv", "gh", "git")


def _extra_path_dirs(base: list[str]) -> list[str]:
    """The folder `shutil.which` finds each of `REQUIRED_TOOLS` in, in that order, each once
    and only when it is not already in `base` (sys-u12: `gh` and `claude`; sys-v9 adds `uv`
    and `git`, the other two tools a wake calls). Raises `TimerError`, naming the tool, for
    the first of them `shutil.which` cannot find: a wake started by cron must find every
    tool a hand-started wake finds."""
    extra: list[str] = []
    for name in REQUIRED_TOOLS:
        found = shutil.which(name)
        if found is None:
            raise TimerError(
                f"{name} not found on PATH: install {name}, then run "
                f"`autonomouse timer install` again"
            )
        folder = str(Path(found).parent)
        if folder not in base and folder not in extra:
            extra.append(folder)
    return extra


def install(
    *,
    crontab: str = "crontab",
    invocation: str | None = None,
) -> list[str]:
    """Write the three marked lines into the user's crontab, replacing any earlier ones and
    keeping every other line. Returns the three lines written.

    Raises `TimerError` when `wake.interval_hours` is not a whole number from 1 to 24, when
    `wake.start` is not a time, when a tool a wake calls (`claude`, `uv`, `gh`, `git`) is not on the installing user's PATH,
    when `crontab` is missing, or when it refuses the read or the write.
    """
    hours = interval_hours()
    start = wake_start()
    invocation = invocation if invocation is not None else locate().cli
    state_dir = default_state_dir().parent
    state_dir.mkdir(parents=True, exist_ok=True)
    home = Path.home()
    base = [f"{home}/.local/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    ours = cron_lines(
        hours,
        invocation,
        home=home,
        state_dir=state_dir,
        extra_path_dirs=tuple(_extra_path_dirs(base)),
        start=start,
    )
    kept = [line for line in _read(crontab) if not _is_ours(line)]
    _write(crontab, kept + ours)
    return ours


def installed(*, crontab: str = "crontab") -> list[str]:
    """The autonomouse lines in the user's crontab, in crontab order."""
    return [line for line in _read(crontab) if _is_ours(line)]


def cron_daemon_runs() -> bool | None:
    """Whether a cron daemon (`cron` or `crond`) is running; `None` when `pgrep` is missing."""
    for name in ("cron", "crond"):
        try:
            found = subprocess.run(["pgrep", "-x", name], capture_output=True, check=False)
        except FileNotFoundError:
            return None
        if found.returncode == 0:
            return True
    return False


def status(*, crontab: str = "crontab", now: datetime | None = None) -> str:
    """The three lines, the next wake (local time, as cron reads it) and whether a cron
    daemon runs."""
    lines = installed(crontab=crontab)
    if not lines:
        lines = ["no autonomouse lines in the crontab; run: autonomouse timer install"]
    fire = next_fire(now or datetime.now().astimezone(), interval_hours(), wake_start())
    daemon = cron_daemon_runs()
    daemon_text = {
        True: "running",
        False: "not running: the lines will not fire until cron is installed and started",
        None: "unknown (no pgrep)",
    }[daemon]
    return "\n".join(
        [*lines, f"next wake: {fire:%Y-%m-%d %H:%M} local time", f"cron daemon: {daemon_text}"]
    )


def remove(*, crontab: str = "crontab") -> list[str]:
    """Delete the marked lines from the user's crontab, keeping every other line. Returns the
    lines removed; safe when none was installed (nothing is written then)."""
    current = _read(crontab)
    removed = [line for line in current if _is_ours(line)]
    if removed:
        _write(crontab, [line for line in current if not _is_ours(line)])
    return removed
