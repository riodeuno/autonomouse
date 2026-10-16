"""The wake's crontab line finds `claude`, `uv`, `gh` and `git` under cron's own environment
(sys-s6; `uv` added sys-v9). cron starts a command with almost nothing set — HOME and a PATH
of `/usr/bin:/bin` — so the line must set the PATH itself, with `$HOME/.local/bin` first,
where `setup/install.sh` puts `claude`. The tests install the lines with a fake `crontab` and
the invocation pointed at a probe script, then run the command part of the generated wake
line the way cron would: `env -i HOME=<tmp> /bin/sh -c '<command>'`. The probe prints
`command -v` of `claude`, `uv`, `gh` and `git`, one per line, into the line's own log; the
stub `claude` beside the probe must resolve from `<tmp>/.local/bin`, `uv` from wherever it is
on this machine (any test below needs it findable at all — `install` fails otherwise, sys-v9),
and `gh` and `git` from the system folders.

`install()` now raises when it cannot find one of the four tools at all (sys-v9), so every
test in this file needs them all on the PATH of the process running the suite, not just
`claude`'s stub; the module-level `skipif` below covers that precondition, and the narrower
one on the second test still covers its own, additional claim about `gh` and `git`."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from autonomouse.timer import REQUIRED_TOOLS, install
from test_timer import FAKE_CRONTAB

SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"

MISSING = [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(
    bool(MISSING), reason=f"install() needs {REQUIRED_TOOLS} on PATH; missing: {MISSING}"
)


def _launch_the_wake_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Install with the probe as the invocation, run the wake line's command as cron does,
    and return what the probe resolved `claude`, `uv`, `gh` and `git` to, from the wake's
    log."""
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    probe = bin_dir / "autonomouse-probe"
    # one `command -v` per name: dash's `command -v a b c` stops at the first name
    probe.write_text(
        "#!/bin/sh\nfor tool in claude uv gh git; do command -v \"$tool\"; done\n"
    )
    probe.chmod(0o755)
    stub_claude = bin_dir / "claude"
    stub_claude.write_text("#!/bin/sh\nexit 0\n")
    stub_claude.chmod(0o755)
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    crontab = fake_dir / "crontab"
    crontab.write_text(FAKE_CRONTAB.format(store=fake_dir / "crontab.txt"))
    crontab.chmod(0o755)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.delenv("AUTONOMOUSE_STATE", raising=False)

    wake, _, _ = install(crontab=str(crontab), invocation=str(probe))
    command = wake.split(None, 5)[5]

    run = subprocess.run(
        ["env", "-i", f"HOME={home}", "/bin/sh", "-c", command],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.stdout == "" and run.stderr == "", "the line sends everything to its log"
    log = home / ".local" / "state" / "autonomouse" / "wake.log"
    return log.read_text().splitlines()


def test_the_wake_line_finds_claude_in_the_homes_local_bin_and_uv_under_crons_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uv` is not stubbed: the module-level `skipif` above already guarantees it resolves
    somewhere on this machine, and `install()` adds its folder to the cron PATH like any
    other tool not already in the fixed folders (sys-v9), so it must resolve to the same
    place `shutil.which` finds it outside the test."""
    resolved = _launch_the_wake_line(tmp_path, monkeypatch)

    assert resolved[0] == str(tmp_path / "home" / ".local" / "bin" / "claude")
    assert resolved[1] == shutil.which("uv")


@pytest.mark.skipif(
    shutil.which("gh", path=SYSTEM_PATH) is None or shutil.which("git", path=SYSTEM_PATH) is None,
    reason=f"gh and git are not both on {SYSTEM_PATH} on this machine",
)
def test_the_wake_line_finds_gh_and_git_in_the_system_folders_under_crons_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolved = _launch_the_wake_line(tmp_path, monkeypatch)

    assert resolved == [
        str(tmp_path / "home" / ".local" / "bin" / "claude"),
        shutil.which("uv"),
        shutil.which("gh", path=SYSTEM_PATH),
        shutil.which("git", path=SYSTEM_PATH),
    ]
