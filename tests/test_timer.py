"""Tests for `autonomouse timer install|status|remove` — the three crontab lines that wake
`autonomouse run`, run the morning review and poll Telegram with `plan step` (sys-s6). A
fake `crontab` stores its input in a temporary file, so no test touches the machine's real
crontab, and HOME and AUTONOMOUSE_CONFIG point into the temporary folder, so the PATH and
the log paths the lines carry, and the config the interval comes from, are the test's own.

The `cron` fixture also fakes `shutil.which` to find every required tool (`claude`, `uv`,
`gh`, `git`) already inside `$HOME/.local/bin`, so every test's PATH is the fixed default
regardless of what is actually installed on the machine running the suite; the tests for
sys-u12 and sys-v9 override it to check what `install` does when a tool is found outside
the fixed folders, or not found at all. The round-trip test below runs the CLI in a real
subprocess, where that monkeypatch cannot reach, so the fixture also puts fake `claude`,
`uv`, `gh` and `git` executables beside the fake crontab, on the PATH it passes to that
subprocess, so the round-trip never depends on the machine actually having them installed."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autonomouse.home import locate
from autonomouse.timer import (
    INTERVAL_KEY,
    MARKER,
    TimerError,
    install,
    next_fire,
    remove,
    status,
)

# `crontab -l` prints the stored crontab, or fails as the real one does when there is none;
# `crontab -` stores its stdin.
FAKE_CRONTAB = """#!/bin/sh
STORE={store}
case "$1" in
  -l) [ -f "$STORE" ] || {{ echo "no crontab for $(id -un)" >&2; exit 1; }}; cat "$STORE" ;;
  -) cat >"$STORE" ;;
  *) echo "fake crontab: unexpected arguments: $*" >&2; exit 2 ;;
esac
"""

FOREIGN = ["MAILTO=me@example.org", "30 1 * * * /usr/local/bin/backup --nightly"]
SCHEDULES = ["0 */4 * * *", "0 6 * * *", "*/5 * * * *"]
INVOCATION = "/opt/autonomouse/bin/autonomouse"
# cron reads the machine's local clock; `next_fire` works on whatever clock `now` carries.
LOCAL = timezone(timedelta(hours=5, minutes=30))


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=LOCAL)


@dataclass
class Cron:
    crontab: str  # the fake `crontab` command
    store: Path  # where it keeps the crontab
    home: Path
    config_dir: Path

    def lines(self) -> list[str]:
        return self.store.read_text().splitlines() if self.store.exists() else []

    def ours(self) -> list[str]:
        return [line for line in self.lines() if MARKER in line]

    def set_interval(self, hours) -> None:
        (self.config_dir / "config.toml").write_text(f"[wake]\ninterval_hours = {hours}\n")


@pytest.fixture
def cron(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Cron:
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    store = fake_dir / "crontab.txt"
    crontab = fake_dir / "crontab"
    crontab.write_text(FAKE_CRONTAB.format(store=store))
    crontab.chmod(0o755)
    for tool in ("claude", "uv", "gh", "git"):
        fake_tool = fake_dir / tool
        fake_tool.write_text("#!/bin/sh\nexit 0\n")
        fake_tool.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.delenv("AUTONOMOUSE_STATE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: str(home / ".local" / "bin" / name))
    return Cron(str(crontab), store, home, config_dir)


def _schedule(line: str) -> str:
    return " ".join(line.split()[:5])


def _command(line: str) -> str:
    return line.split(None, 5)[5]


def _path(line: str) -> str:
    """The value the line's `export PATH=...;` sets."""
    return _command(line).split("; ", 1)[0].removeprefix("export PATH=")


def test_install_keeps_a_foreign_line_and_adds_exactly_three_marked_lines(cron: Cron) -> None:
    cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))

    written = install(crontab=cron.crontab, invocation=INVOCATION)

    assert cron.lines() == FOREIGN + written
    assert len(written) == 3
    assert [_schedule(line) for line in written] == SCHEDULES
    assert [line.rsplit("# ", 1)[1] for line in written] == [
        "autonomouse wake",
        "autonomouse review",
        "autonomouse plan",
    ]


def test_each_line_sets_the_path_runs_the_invocation_and_logs_to_the_state_folder(
    cron: Cron,
) -> None:
    wake, review, plan = install(crontab=cron.crontab, invocation=INVOCATION)

    path = f"export PATH={cron.home}/.local/bin:/usr/local/bin:/usr/bin:/bin;"
    logs = cron.home / ".local" / "state" / "autonomouse"
    assert logs.is_dir()
    assert _command(wake) == (
        f"{path} {INVOCATION} run >>{logs}/wake.log 2>&1 # autonomouse wake"
    )
    assert _command(review) == (
        f"{path} ({INVOCATION} review daily && {INVOCATION} review behaviour --since 1 && "
        f"{INVOCATION} review improve) >>{logs}/review.log 2>&1 # autonomouse review"
    )
    assert _command(plan) == (
        f"{path} {INVOCATION} plan step >>{logs}/plan.log 2>&1 # autonomouse plan"
    )


def test_install_adds_ghs_folder_to_the_path_once_on_all_three_lines_when_not_already_there(
    cron: Cron, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sys-u12: a wake's own sessions run `gh`; when it lives outside the fixed folders, the
    installed PATH must still find it, on all three lines. `claude`, `uv` and `git` here
    already live in `$HOME/.local/bin`, so none of them is added a second time."""
    local_bin = str(cron.home / ".local" / "bin")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/x/shims/gh" if name == "gh" else f"{local_bin}/{name}",
    )

    written = install(crontab=cron.crontab, invocation=INVOCATION)

    for line in written:
        assert _path(line) == f"{local_bin}:/x/shims:/usr/local/bin:/usr/bin:/bin"
        assert _path(line).count("/x/shims") == 1
        assert _path(line).count(local_bin) == 1


def test_install_adds_uvs_and_gits_folders_when_not_already_on_the_path(
    cron: Cron, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sys-v9: a wake also calls `uv` (to run `autonomouse` itself) and `git` (to merge
    branches); when either lives outside the fixed folders, the installed PATH must find it
    too, ahead of the fixed folders — each in its own folder here, so this fails on
    sys-u12's code, which looked up only `gh` and `claude`."""
    local_bin = str(cron.home / ".local" / "bin")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: {
            "claude": f"{local_bin}/claude",
            "uv": "/opt/uv-shim/uv",
            "gh": f"{local_bin}/gh",
            "git": "/opt/git-shim/git",
        }[name],
    )

    wake, _, _ = install(crontab=cron.crontab, invocation=INVOCATION)

    assert _path(wake) == f"{local_bin}:/opt/uv-shim:/opt/git-shim:/usr/local/bin:/usr/bin:/bin"


def test_install_adds_a_folder_shared_by_two_tools_only_once(
    cron: Cron, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sys-v9: `uv` and `git` — the two tools sys-v9 adds — can live in the same shim folder
    (as two tools managed by the same version manager do); the no-duplicates rule must hold
    across two different required tools, not just between one tool and the fixed folders.
    `claude` and `gh` resolve to already-fixed folders here, so a version of `install` that
    (like sys-u12's) never looks at `uv` or `git` at all would miss the shared folder
    entirely rather than merely double it — either way, this fails on that code."""
    local_bin = str(cron.home / ".local" / "bin")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: {
            "claude": f"{local_bin}/claude",
            "uv": "/opt/shims/uv",
            "gh": "/usr/local/bin/gh",
            "git": "/opt/shims/git",
        }[name],
    )

    wake, _, _ = install(crontab=cron.crontab, invocation=INVOCATION)

    assert _path(wake) == f"{local_bin}:/opt/shims:/usr/local/bin:/usr/bin:/bin"
    assert _path(wake).count("/opt/shims") == 1


def test_install_leaves_the_path_unchanged_when_every_tool_is_already_on_it(
    cron: Cron, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gh` and `git` resolving to a fixed folder (`/usr/bin`), not just `$HOME/.local/bin`,
    adds nothing either."""
    local_bin = str(cron.home / ".local" / "bin")
    found = {
        "claude": f"{local_bin}/claude",
        "uv": f"{local_bin}/uv",
        "gh": "/usr/bin/gh",
        "git": "/usr/bin/git",
    }
    monkeypatch.setattr(shutil, "which", lambda name: found[name])

    wake, _, _ = install(crontab=cron.crontab, invocation=INVOCATION)

    assert _path(wake) == f"{cron.home}/.local/bin:/usr/local/bin:/usr/bin:/bin"


@pytest.mark.parametrize("missing", ["claude", "uv", "gh", "git"])
def test_install_fails_naming_a_tool_it_cannot_find(
    cron: Cron, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    """sys-v9: a wake started by cron must find every tool a hand-started wake finds; when
    `shutil.which` cannot find one, `install` fails at once, naming it, instead of writing a
    crontab that will fail later with `[Errno 2] No such file or directory`."""
    cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))
    local_bin = str(cron.home / ".local" / "bin")
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == missing else f"{local_bin}/{name}"
    )

    with pytest.raises(TimerError, match=missing):
        install(crontab=cron.crontab, invocation=INVOCATION)

    assert cron.lines() == FOREIGN


def test_install_with_no_arguments_runs_the_command_locate_gives(cron: Cron) -> None:
    """`project new` calls `install()` bare (sys-s2); the invocation is `locate().cli`."""
    wake, _, _ = install(crontab=cron.crontab)

    assert f" {locate().cli} run >>" in wake


def test_install_on_a_machine_with_no_crontab_yet_writes_the_three_lines(cron: Cron) -> None:
    assert not cron.store.exists()

    written = install(crontab=cron.crontab, invocation=INVOCATION)

    assert cron.lines() == written


def test_a_second_install_still_leaves_three_lines(cron: Cron) -> None:
    cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))
    install(crontab=cron.crontab, invocation=INVOCATION)

    cron.set_interval(6)
    written = install(crontab=cron.crontab, invocation=INVOCATION)

    assert cron.lines() == FOREIGN + written
    assert len(cron.ours()) == 3
    assert _schedule(written[0]) == "0 */6 * * *"


@pytest.mark.parametrize("hours", [0, 25, "4", True])
def test_an_interval_outside_1_to_24_fails_naming_the_key_and_writes_nothing(
    cron: Cron, hours
) -> None:
    cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))
    cron.set_interval(hours if not isinstance(hours, str) else f'"{hours}"')
    if hours is True:
        (cron.config_dir / "config.toml").write_text("[wake]\ninterval_hours = true\n")

    with pytest.raises(TimerError, match=INTERVAL_KEY):
        install(crontab=cron.crontab, invocation=INVOCATION)

    assert cron.lines() == FOREIGN


def test_remove_leaves_only_the_foreign_lines(cron: Cron) -> None:
    cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))
    written = install(crontab=cron.crontab, invocation=INVOCATION)

    removed = remove(crontab=cron.crontab)

    assert removed == written
    assert cron.lines() == FOREIGN
    assert remove(crontab=cron.crontab) == []


def test_install_names_the_fix_when_crontab_is_not_installed(cron: Cron) -> None:
    with pytest.raises(TimerError, match="install cron"):
        install(crontab=str(cron.home / "no-such-crontab"), invocation=INVOCATION)


@pytest.mark.parametrize(
    ("now", "hours", "expected"),
    [
        (_at(26, 5, 10), 4, _at(26, 8)),
        (_at(26, 21, 30), 5, _at(27, 0)),
        (_at(26, 8), 4, _at(26, 12)),
        (_at(26, 23, 59), 24, _at(27, 0)),
    ],
)
def test_next_fire_is_the_next_multiple_of_the_interval_as_cron_reads_it(
    now: datetime, hours: int, expected: datetime
) -> None:
    assert next_fire(now, hours) == expected


def test_status_prints_the_three_lines_the_next_wake_and_the_daemon(cron: Cron) -> None:
    written = install(crontab=cron.crontab, invocation=INVOCATION)

    text = status(crontab=cron.crontab, now=_at(26, 5, 10))

    lines = text.splitlines()
    assert lines[:3] == written
    assert lines[3] == "next wake: 2026-09-26 08:00 local time"
    assert lines[4].startswith("cron daemon: ")


def test_status_before_install_says_so(cron: Cron) -> None:
    text = status(crontab=cron.crontab, now=_at(26, 5, 10))

    assert text.splitlines()[0] == (
        "no autonomouse lines in the crontab; run: autonomouse timer install"
    )


def test_cli_timer_install_status_remove_round_trip(cron: Cron) -> None:
    env = dict(
        os.environ,
        HOME=str(cron.home),
        AUTONOMOUSE_CONFIG=str(cron.config_dir),
        PATH=f"{Path(cron.crontab).parent}:{os.environ['PATH']}",
    )
    env.pop("AUTONOMOUSE_STATE", None)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", *args],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    installed = run("timer", "install").stdout.splitlines()
    assert installed[0] == "installed in the crontab:"
    assert [line.strip() for line in installed[1:]] == cron.ours()
    assert " autonomouse run >>" in cron.ours()[0]

    shown = run("timer", "status").stdout.splitlines()
    assert shown[:3] == cron.ours()
    assert shown[3].startswith("next wake: ")

    removed = run("timer", "remove").stdout.splitlines()
    assert removed[0] == "removed from the crontab:"
    assert cron.ours() == []
    assert run("timer", "remove").stdout.strip() == "nothing installed"


def test_cli_timer_install_fails_naming_the_key_on_a_bad_interval(cron: Cron) -> None:
    cron.set_interval(0)
    env = dict(
        os.environ,
        HOME=str(cron.home),
        AUTONOMOUSE_CONFIG=str(cron.config_dir),
        PATH=f"{Path(cron.crontab).parent}:{os.environ['PATH']}",
    )

    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "timer", "install"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert INTERVAL_KEY in result.stderr
    assert cron.lines() == []
