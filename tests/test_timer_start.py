"""sys-w2: the installer asks when the first wake runs and writes it as `wake.start`; from
then on `autonomouse timer install` anchors the wake's crontab line on that time: it fires
at its minute, on its hour and every `wake.interval_hours` after it, and runs no wake before
it. The fake crontab and the `cron` fixture are `tests/test_timer.py`'s."""

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autonomouse.timer import (
    TimerError,
    cron_lines,
    install,
    next_fire,
    status,
    wake_hours,
    wake_start,
)
from test_timer import INVOCATION, LOCAL, Cron, cron  # noqa: F401 — `cron` is a fixture

START = datetime(2026, 9, 29, 9, 30, tzinfo=LOCAL)


def _wake_line(lines: list[str]) -> str:
    [wake] = [line for line in lines if line.endswith("# autonomouse wake")]
    return wake


def _as_sh_runs_it(line: str) -> str:
    """The command cron hands to sh for a crontab line: its sixth field on, where `\\%`
    becomes `%` (a bare `%` would end the command there, so the line must hold none)."""
    command = line.split(None, 5)[5]
    assert "%" not in command.replace("\\%", ""), f"a bare % in: {command}"
    return command.replace("\\%", "%")


def test_the_wake_line_fires_at_the_start_minute_on_its_hour_and_every_interval_after(
    tmp_path: Path,
) -> None:
    lines = cron_lines(4, INVOCATION, home=tmp_path, state_dir=tmp_path, start=START)
    wake = _wake_line(lines)

    assert wake.split()[:5] == ["30", "1,5,9,13,17,21", "*", "*", "*"]
    assert (
        '; [ "$(date +\\%Y\\%m\\%d\\%H\\%M)" -ge 202609290930 ] && '
        f"{INVOCATION} run >>" in wake
    )
    unanchored = cron_lines(4, INVOCATION, home=tmp_path, state_dir=tmp_path)
    assert lines[1:] == unanchored[1:]  # the review and plan lines do not move
    assert _wake_line(unanchored).split()[:5] == ["0", "*/4", "*", "*", "*"]


@pytest.mark.parametrize(
    ("clock", "runs"),
    [("202609290929", False), ("202609290930", True), ("202610011330", True)],
)
def test_the_wake_line_runs_a_wake_only_from_the_start_on(
    tmp_path: Path, clock: str, runs: bool
) -> None:
    """The wake line's command, as sh runs it, with a `date` that prints `clock` and an
    invocation that says it ran."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "date").write_text(f'#!/bin/sh\n[ "$1" = "+%Y%m%d%H%M" ] && echo {clock}\n')
    (fake / "date").chmod(0o755)
    (fake / "autonomouse").write_text('#!/bin/sh\necho "ran: $*"\n')
    (fake / "autonomouse").chmod(0o755)
    lines = cron_lines(
        4, "autonomouse", home=tmp_path, state_dir=tmp_path, extra_path_dirs=(str(fake),),
        start=START,
    )  # fmt: skip

    subprocess.run(["sh", "-c", _as_sh_runs_it(_wake_line(lines))], check=False)

    log = tmp_path / "wake.log"
    assert (log.read_text() if log.exists() else "") == ("ran: run\n" if runs else "")


def test_install_anchors_the_wake_line_on_wake_start(cron: Cron) -> None:  # noqa: F811
    (cron.config_dir / "config.toml").write_text('[wake]\nstart = "2026-09-29T09:30"\n')

    install(crontab=cron.crontab, invocation=INVOCATION)

    wake = _wake_line(cron.ours())
    assert wake.split()[:5] == ["30", "1,5,9,13,17,21", "*", "*", "*"]
    assert "-ge 202609290930 ] && " in wake


def test_status_names_the_start_as_the_next_wake_while_it_is_ahead(cron: Cron) -> None:  # noqa: F811
    (cron.config_dir / "config.toml").write_text('[wake]\nstart = "2026-09-29T09:30"\n')
    install(crontab=cron.crontab, invocation=INVOCATION)

    printed = status(crontab=cron.crontab, now=datetime(2026, 9, 28, 14, 10).astimezone())

    assert "next wake: 2026-09-29 09:30 local time" in printed


@pytest.mark.parametrize(
    ("now", "interval", "expected"),
    [
        (datetime(2026, 9, 28, 14, 10, tzinfo=LOCAL), 4, datetime(2026, 9, 29, 9, 30, tzinfo=LOCAL)),
        (datetime(2026, 9, 29, 9, 30, tzinfo=LOCAL), 4, datetime(2026, 9, 29, 13, 30, tzinfo=LOCAL)),
        (datetime(2026, 9, 29, 22, 0, tzinfo=LOCAL), 4, datetime(2026, 9, 30, 1, 30, tzinfo=LOCAL)),
        (datetime(2026, 9, 29, 10, 0, tzinfo=LOCAL), 24, datetime(2026, 9, 30, 9, 30, tzinfo=LOCAL)),
        (datetime(2026, 9, 29, 19, 31, tzinfo=LOCAL), 5, datetime(2026, 9, 30, 0, 30, tzinfo=LOCAL)),
    ],
)
def test_next_fire_from_a_start_is_the_start_then_every_interval_after(
    now: datetime, interval: int, expected: datetime
) -> None:
    assert next_fire(now, interval, START) == expected


def test_the_hours_run_round_the_clock_from_the_start_hour() -> None:
    assert wake_hours(START, 4) == [1, 5, 9, 13, 17, 21]
    assert wake_hours(START, 5) == [0, 5, 9, 14, 19]
    assert wake_hours(START, 24) == [9]


def test_wake_start_reads_a_local_time_to_the_minute() -> None:
    assert wake_start({"wake": {"start": ""}}) is None
    naive = wake_start({"wake": {"start": "2026-09-29T09:30:45"}})
    assert naive == datetime(2026, 9, 29, 9, 30).astimezone()
    utc = wake_start({"wake": {"start": "2026-09-29T09:30+00:00"}})
    assert utc == datetime(2026, 9, 29, 9, 30, tzinfo=UTC)
    assert utc.utcoffset() == datetime(2026, 9, 29, 9, 30).astimezone().utcoffset()


@pytest.mark.parametrize("value", ["tomorrow 09:00", "2026-13-01T09:00", 900])
def test_a_start_that_is_not_a_time_fails_naming_the_key(value) -> None:
    with pytest.raises(TimerError, match="wake.start must be an ISO-8601 time"):
        wake_start({"wake": {"start": value}})


def test_install_with_a_bad_start_fails_and_writes_nothing(cron: Cron) -> None:  # noqa: F811
    (cron.config_dir / "config.toml").write_text('[wake]\nstart = "tomorrow"\n')

    with pytest.raises(TimerError, match="wake.start"):
        install(crontab=cron.crontab, invocation=INVOCATION)
    assert cron.lines() == []


def test_a_start_one_interval_ahead_of_now_is_still_the_first_wake() -> None:
    now = datetime(2026, 9, 29, 9, 30, tzinfo=LOCAL) - timedelta(hours=4)
    assert next_fire(now, 4, START) == START
