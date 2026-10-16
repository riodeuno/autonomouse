"""setup/macos.sh sets up autonomouse and a browsermaze pool in a Lima VM on a Mac. These tests
need no Lima and no Mac:

- the options, the CPU and memory defaults and the edit of `~/.ssh/config` run for real, in a
  temporary HOME;
- the whole run happens against stand-ins: `limactl`, `uv`, `brew`, `uname` and `sysctl` are
  small scripts, and a fake `ssh` runs every command in a temporary folder that plays the VM's
  home, so the run shows the order of the steps, how the commands are quoted, what is copied
  into the VM, what is asked of the installers, and the checklist. `setup/install.sh` and
  browsermaze's `setup.sh` are stand-ins too. What none of this shows is Lima, the real
  installers or Ubuntu: the real run on a Mac is the test for those.
"""

import getpass
import os
import pty
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MACOS_SH = ROOT / "setup" / "macos.sh"
USER = getpass.getuser()  # `id -un` in the fake VM: the fake ssh runs commands as this user
INCLUDE = "Include ~/.lima/*/ssh.config"


def _script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _bash(script: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60, check=False
    )


def _run_macos(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(MACOS_SH), *args], env=env, capture_output=True, text=True, timeout=120, check=False
    )


def test_the_script_parses_and_passes_shellcheck():
    subprocess.run(["bash", "-n", str(MACOS_SH)], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", "-s", "bash", str(MACOS_SH)], check=True)


# --- options ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--cpus", "0"],
        ["--cpus", "four"],
        ["--memory", "8.5"],
        ["--disk", "-1"],
        ["--slots", "10"],
        ["--slots", "0"],
        ["--cpus"],
        ["--bogus"],
    ],
)
def test_invalid_options_exit_2_before_anything_runs(args, tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    done = _run_macos(*args, env=env)
    assert done.returncode == 2, done.stderr
    assert "macos.sh" in done.stderr or "usage" in done.stderr


def test_help_lists_every_option_and_exits_0(tmp_path):
    done = _run_macos("--help", env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)})
    assert done.returncode == 0
    for option in ("--cpus", "--memory", "--disk", "--slots", "--browsermaze", "--help"):
        assert option in done.stdout


def test_defaults_follow_the_macs_cpus_and_memory(tmp_path):
    stubs = tmp_path / "bin"
    _script(
        stubs / "sysctl",
        'case "$2" in hw.ncpu) echo "$FAKE_CPUS";; hw.memsize) echo "$FAKE_MEM";; esac\n',
    )
    env = {"PATH": f"{stubs}:{os.environ['PATH']}", "HOME": str(tmp_path)}

    def defaults(cpus: int, gib: int) -> str:
        e = dict(env, FAKE_CPUS=str(cpus), FAKE_MEM=str(gib * 1024**3))
        return _bash(f'source "{MACOS_SH}"; echo "$(default_cpus) $(default_memory)"', e).stdout.strip()

    assert defaults(2, 4) == "2 2"  # small Mac: no more than it has, half the memory
    assert defaults(8, 8) == "4 4"
    assert defaults(8, 16) == "4 8"
    assert defaults(12, 64) == "4 8"  # Lima's CPU default; the memory default stops at 8


# --- ~/.ssh/config ---------------------------------------------------------------------


def _ssh_include(home: Path, extra_env: dict[str, str] | None = None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), **(extra_env or {})}
    return _bash(f'source "{MACOS_SH}"; ensure_ssh_include', env)


def test_ssh_include_creates_the_config_when_there_is_none(tmp_path):
    done = _ssh_include(tmp_path)
    assert done.returncode == 0, done.stderr
    config = tmp_path / ".ssh" / "config"
    assert config.read_text() == INCLUDE + "\n"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".ssh").stat().st_mode) == 0o700
    assert list((tmp_path / ".ssh").glob("config.backup-*")) == []


def test_ssh_include_goes_first_after_a_backup_and_a_second_run_changes_nothing(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    original = "Host work\n  User me\n\nHost *\n  ServerAliveInterval 30\n"
    (ssh / "config").write_text(original)
    (ssh / "config").chmod(0o640)

    assert _ssh_include(tmp_path).returncode == 0
    text = (ssh / "config").read_text()
    assert text.splitlines()[0] == INCLUDE
    assert text == INCLUDE + "\n\n" + original
    assert stat.S_IMODE((ssh / "config").stat().st_mode) == 0o640
    (backup,) = ssh.glob("config.backup-*")
    assert backup.read_text() == original

    assert _ssh_include(tmp_path).returncode == 0
    assert (ssh / "config").read_text() == text
    assert len(list(ssh.glob("config.backup-*"))) == 1


def test_ssh_include_edits_a_symlinked_config_through_the_link(tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "ssh_config"
    target.write_text("Host a\n")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "config").symlink_to(target)

    assert _ssh_include(tmp_path).returncode == 0
    assert (tmp_path / ".ssh" / "config").is_symlink()
    assert target.read_text() == INCLUDE + "\n\nHost a\n"


def test_ssh_include_names_a_lima_home_outside_the_home_folder(tmp_path):
    done = _ssh_include(tmp_path, {"LIMA_HOME": "/opt/lima"})
    assert done.returncode == 0
    assert (tmp_path / ".ssh" / "config").read_text() == "Include /opt/lima/*/ssh.config\n"


# --- a whole run against stand-ins -------------------------------------------------------


class FakeMac:
    """A temporary Mac and VM: stand-in commands first on PATH, a checkout of the repository's
    `setup/macos.sh` beside a stand-in installer, and a stand-in browsermaze checkout."""

    def __init__(self, root: Path):
        self.root = root
        self.mac_home = root / "mac-home"
        self.vm_home = root / "vm-home"
        self.state = root / "state"
        self.log = root / "calls.log"
        stubs, vmbin = root / "bin", root / "vm-bin"
        for folder in (self.mac_home, self.vm_home, self.state):
            folder.mkdir()
        self.log.touch()

        # The Mac's commands.
        _script(stubs / "uname", 'if [ "${1:-}" = -s ]; then echo "${FAKE_OS:-Darwin}"; else exec /usr/bin/uname "$@"; fi\n')
        _script(stubs / "sysctl", 'case "$2" in hw.ncpu) echo 8;; hw.memsize) echo 17179869184;; esac\n')
        _script(stubs / "brew", 'echo "brew $*" >>"$FAKE_LOG"; exit 1\n')
        _script(stubs / "chrome", "exit 0\n")
        _script(
            stubs / "limactl",
            """echo "limactl $*" >>"$FAKE_LOG"
status="$FAKE_STATE/vm-status"
case "$1" in
  list)
    case "$*" in
      *--quiet*) if [ -f "$status" ]; then echo autonomouse; fi ;;
      *'{{.Status}}'*) cat "$status" ;;
      *SSHConfigFile*) echo "$FAKE_STATE/ssh.config" ;;
    esac ;;
  start) echo Running >"$status" ;;
  *) exit 1 ;;
esac
exit 0
""",
        )
        _script(
            stubs / "uv",
            """echo "uv $*" >>"$FAKE_LOG"
[ "$1" = build ] || exit 1
while [ $# -gt 0 ]; do if [ "$1" = --out-dir ]; then out="$2"; fi; shift; done
mkdir -p "$out"; echo wheel >"$out/autonomouse-9.9.9-py3-none-any.whl"
""",
        )
        _script(
            stubs / "ssh-keyscan",
            '[ -z "${FAKE_NO_HOSTKEY:-}" ] || exit 1\necho "127.0.0.1 $(cut -d" " -f1,2 "$FAKE_STATE/hostkey.pub")"\n',
        )
        # Every host is the VM: the command runs in the VM's home, as the same user.
        _script(
            stubs / "ssh",
            """echo "ssh $*" >>"$FAKE_LOG"
while [ $# -gt 0 ]; do
  case "$1" in
    -F|-o|-i|-L|-R|-p) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
shift  # the host
cmd="$*"
cmd="${cmd//\\/etc\\/browsermaze\\/config/$FAKE_VM_HOME\\/etc-browsermaze-config}"
cd "$FAKE_VM_HOME"
exec env HOME="$FAKE_VM_HOME" bash -c "$cmd"
""",
        )
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.state / "hostkey")],
            check=True,
        )

        # The VM's commands, found through the login shell's profile, as ~/.local/bin is.
        (self.vm_home / ".profile").write_text(f'export PATH="{vmbin}:{stubs}:$PATH"\n')
        _script(
            vmbin / "autonomouse",
            """cfg="$HOME/.config/autonomouse/config"
case "$1" in
  --version) echo "autonomouse 9.9.9" ;;
  config)
    case "$2" in
      show) for k in browser.pool browser.slots; do echo "$k = $(sed -n "s/^$k=//p" "$cfg" 2>/dev/null)"; done ;;
      set) echo "autonomouse config set $3 $4" >>"$FAKE_LOG"; mkdir -p "$(dirname "$cfg")"
           { grep -v "^$3=" "$cfg" 2>/dev/null || true; echo "$3=$4"; } >"$cfg.new"; mv "$cfg.new" "$cfg" ;;
    esac ;;
esac
""",
        )
        _script(
            vmbin / "browsermaze",
            """case "$1" in
  lease) [ ! -f "$HOME/lease-broken" ] || { echo "no pool" >&2; exit 1; }; echo "1 http://127.0.0.1:9301" ;;
  release) echo "browsermaze release $2" >>"$FAKE_LOG" ;;
esac
""",
        )
        _script(
            vmbin / "claude",
            """case "$1 $2" in
  "--version "*) echo "2.0 (Claude Code)" ;;
  "auth status") [ -f "$HOME/claude-logged-in" ] ;;
  "auth login") echo "claude auth login" >>"$FAKE_LOG"; touch "$HOME/claude-logged-in" ;;
esac
""",
        )

        # The checkout the script lives in, with an installer that needs a Claude login.
        self.checkout = root / "autonomouse"
        (self.checkout / "setup").mkdir(parents=True)
        shutil.copy(MACOS_SH, self.checkout / "setup" / "macos.sh")
        (self.checkout / "pyproject.toml").write_text('[project]\nname = "autonomouse"\nversion = "9.9.9"\n')
        _script(
            self.checkout / "setup" / "install.sh",
            """echo "install.sh $*" >>"$FAKE_LOG"
[ "$1" = --wheel ] && [ -f "$2" ] || { echo "no wheel" >&2; exit 3; }
cat "${XDG_STATE_HOME:-$HOME/.local/state}/autonomouse-install/answered" >"$HOME/answered-seen"
[ -f "$HOME/claude-logged-in" ] || { echo "Claude Code is not logged in" >&2; exit 1; }
""",
        )

        # A browsermaze checkout whose setup.sh records its role and writes what the real one does.
        self.browsermaze = root / "browsermaze"
        (self.browsermaze / ".git").mkdir(parents=True)
        (self.browsermaze / ".git" / "HEAD").write_text("ref\n")
        (self.browsermaze / "VERSION").write_text("0.0.1\n")
        _script(
            self.browsermaze / "setup.sh",
            """echo "setup.sh $*" >>"$FAKE_LOG"
case "$*" in
  *"--role pool"*)
    slots=4
    while [ $# -gt 0 ]; do
      case "$1" in --slots) slots="$2" ;; --authorize-key) echo "$2" >"$HOME/authorized-key" ;; esac
      shift
    done
    echo "POOL_SLOTS=$slots" >"$HOME/etc-browsermaze-config" ;;
  *"--role login"*)
    mkdir -p "$HOME/.local/bin"
    printf '#!/usr/bin/%s\\necho "browsermaze %s"\\n' env\\ bash "$(cat "$(dirname "$0")/VERSION")" >"$HOME/.local/bin/browsermaze"
    chmod +x "$HOME/.local/bin/browsermaze" ;;
esac
""",
        )

        self.env = {
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "HOME": str(self.mac_home),
            "CHROME": str(stubs / "chrome"),
            "FAKE_LOG": str(self.log),
            "FAKE_STATE": str(self.state),
            "FAKE_VM_HOME": str(self.vm_home),
        }
        (self.state / "ssh.config").write_text("Host lima-autonomouse\n")
        (self.mac_home / ".ssh").mkdir()
        (self.mac_home / ".ssh" / "config").write_text("Host work\n  User me\n")

    def run(
        self, *args: str, browsermaze: bool = True, terminal: bool = False, **env: str
    ) -> subprocess.CompletedProcess:
        """Run macos.sh; `terminal` gives it a pseudo-terminal as stdin, as a person's run has."""
        named = ["--browsermaze", str(self.browsermaze)] if browsermaze else []
        primary, secondary = pty.openpty() if terminal else (None, None)
        try:
            return subprocess.run(
                ["bash", str(self.checkout / "setup" / "macos.sh"), *named, *args],
                env={**self.env, **env},
                stdin=secondary if terminal else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        finally:
            if terminal:
                os.close(primary)
                os.close(secondary)

    def calls(self, prefix: str) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def log_in(self) -> None:
        (self.vm_home / "claude-logged-in").touch()

    def set_vm_status(self, status: str) -> None:
        (self.state / "vm-status").write_text(status + "\n")


@pytest.fixture
def mac(tmp_path):
    return FakeMac(tmp_path)


def _ok_items(output: str) -> list[str]:
    return [line.split("]", 1)[1].strip() for line in output.splitlines() if line.startswith("  [ok]")]


def test_a_run_sets_everything_up_and_a_second_run_changes_nothing(mac):
    mac.log_in()
    first = mac.run("--slots", "3")
    assert first.returncode == 0, first.stdout + first.stderr
    assert "[FAIL]" not in first.stdout
    assert len(_ok_items(first.stdout)) == 9
    assert "All green." in first.stdout

    # The VM: one create, with the flags the design names.
    (start,) = mac.calls("limactl start")
    assert start == (
        "limactl start --tty=false --name=autonomouse --vm-type=vz --containerd=none --mount-none "
        "--cpus=4 --memory=8 --disk=60 template:ubuntu-24.04"
    )
    assert mac.calls("brew") == []

    # The pool: installed from a copy of the checkout (no .git), for the VM user, listening on
    # 127.0.0.1, with the key the VM user's ssh uses, and the slot count asked for.
    stage = mac.vm_home / ".local" / "share" / "autonomouse-macos"
    assert (stage / "browsermaze" / "setup.sh").exists()
    assert not (stage / "browsermaze" / ".git").exists()
    pool_pubkey = (mac.vm_home / ".ssh" / "id_ed25519.pub").read_text().strip()
    (pool,) = [c for c in mac.calls("setup.sh") if "--role pool" in c]
    assert f"--bind 127.0.0.1 --caller {USER} --authorize-key {pool_pubkey} --slots 3" in pool
    assert (mac.vm_home / "authorized-key").read_text().strip() == pool_pubkey
    assert "127.0.0.1" in (mac.vm_home / ".ssh" / "known_hosts").read_text()

    # The Mac: Include first, after a backup; the login role points at the VM's ssh alias.
    config = mac.mac_home / ".ssh" / "config"
    assert config.read_text() == INCLUDE + "\n\nHost work\n  User me\n"
    (backup,) = (mac.mac_home / ".ssh").glob("config.backup-*")
    assert backup.read_text() == "Host work\n  User me\n"
    (login,) = [c for c in mac.calls("setup.sh") if "--role login" in c]
    assert login.endswith("--role login --pool lima-autonomouse")

    # autonomouse: built on the Mac, copied with install.sh, the pool question marked answered
    # before the installer runs, then pointed at the VM's own pool.
    assert mac.calls("uv build --wheel --out-dir")
    (install,) = mac.calls("install.sh")
    assert install.endswith("autonomouse-9.9.9-py3-none-any.whl")
    assert (mac.vm_home / "answered-seen").read_text() == "browser-pool\n"
    assert mac.calls("autonomouse config set") == [
        f"autonomouse config set browser.pool {USER}@127.0.0.1",
        "autonomouse config set browser.slots 3",
    ]
    assert mac.calls("browsermaze release 1") == ["browsermaze release 1"]

    before = {p: p.read_text() for p in (config, mac.vm_home / ".ssh" / "known_hosts")}
    second = mac.run("--slots", "3")
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(mac.calls("limactl start")) == 1
    assert {p: p.read_text() for p in before} == before
    assert len(list((mac.mac_home / ".ssh").glob("config.backup-*"))) == 1
    assert len(mac.calls("autonomouse config set")) == 2
    assert (mac.vm_home / "answered-seen").read_text() == "browser-pool\n"


def test_without_a_claude_login_it_logs_in_and_runs_the_installer_again(mac):
    done = mac.run(terminal=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(mac.calls("install.sh")) == 2
    assert mac.calls("claude auth login") == ["claude auth login"]
    assert "--slots" not in next(c for c in mac.calls("setup.sh") if "--role pool" in c)
    assert mac.calls("autonomouse config set")[-1] == "autonomouse config set browser.slots 4"


def test_without_a_claude_login_and_no_terminal_it_names_the_fix_and_does_not_wait(mac):
    done = mac.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert mac.calls("claude auth login") == []
    assert "limactl shell autonomouse claude auth login" in done.stderr + done.stdout
    assert mac.calls("autonomouse config set")[-1] == "autonomouse config set browser.slots 4"


def test_a_stopped_vm_is_started_without_the_size_flags(mac):
    mac.log_in()
    mac.set_vm_status("Stopped")
    done = mac.run("--cpus", "2")
    assert done.returncode == 0, done.stdout + done.stderr
    assert mac.calls("limactl start") == ["limactl start --tty=false autonomouse"]
    assert "--cpus, --memory and --disk are ignored" in done.stdout


def test_a_failed_check_prints_its_fix_and_exits_1(mac):
    mac.log_in()
    (mac.vm_home / "lease-broken").touch()
    done = mac.run()
    assert done.returncode == 1
    assert "[FAIL] browsermaze lease and release work through ssh" in done.stdout
    assert "         fix: in the VM" in done.stdout
    assert "[ok]   VM autonomouse is running" in done.stdout
    assert "1 checklist item(s) failed" in done.stdout


def test_an_installer_that_fails_does_not_stop_the_other_steps(mac):
    mac.log_in()
    _script(mac.checkout / "setup" / "install.sh", 'echo "install.sh $*" >>"$FAKE_LOG"; exit 1\n')
    done = mac.run()
    assert done.returncode == 1
    assert "[FAIL] autonomouse install.sh finished with no failed item" in done.stdout
    assert "[ok]   browsermaze lease and release work through ssh" in done.stdout


def test_a_mac_without_chrome_stops_before_it_creates_anything(mac):
    done = mac.run(CHROME=str(mac.root / "no-chrome"))
    assert done.returncode == 1
    assert "Google Chrome is missing" in done.stderr
    assert "fix:" in done.stderr
    assert mac.calls("limactl") == []


def test_anything_but_macos_stops_at_once(mac):
    done = mac.run(FAKE_OS="Linux")
    assert done.returncode == 1
    assert "runs on macOS" in done.stderr
    assert mac.calls("limactl") == []


def test_it_uses_the_browsermaze_checkout_next_to_its_own(mac):
    mac.log_in()
    done = mac.run(browsermaze=False)  # <root>/autonomouse and <root>/browsermaze are siblings
    assert done.returncode == 0, done.stdout + done.stderr
    assert mac.calls("git") == []
    assert [c for c in mac.calls("setup.sh") if "--role login" in c]


def test_it_clones_browsermaze_next_to_its_own_checkout_when_there_is_none(mac):
    mac.log_in()
    template = mac.root / "browsermaze-template"
    shutil.move(mac.browsermaze, template)
    _script(
        mac.root / "bin" / "git",
        'echo "git $*" >>"$FAKE_LOG"; [ "$1" = clone ] && mkdir -p "$3" && cp -R "$FAKE_TEMPLATE"/. "$3"\n',
    )
    done = mac.run(browsermaze=False, FAKE_TEMPLATE=str(template))
    assert done.returncode == 0, done.stdout + done.stderr
    assert mac.calls("git") == [f"git clone https://github.com/riodeuno/browsermaze {mac.browsermaze}"]


def test_a_folder_that_is_not_browsermaze_stops_the_run(mac):
    (mac.browsermaze / "setup.sh").unlink()
    done = mac.run()
    assert done.returncode == 1
    assert "is not a browsermaze checkout" in done.stderr
    assert mac.calls("limactl") == []


def test_a_vm_whose_sshd_gives_no_host_key_fails_the_pool_step_before_the_installer_runs(mac):
    mac.log_in()
    done = mac.run(FAKE_NO_HOSTKEY="1")
    assert done.returncode == 1
    assert "sshd in the VM gave no host key" in done.stderr
    assert "[FAIL] browsermaze pool setup.sh finished with no failed item" in done.stdout
    assert [c for c in mac.calls("setup.sh") if "--role pool" in c] == []


def test_the_pool_answer_macos_sh_writes_is_the_one_install_sh_reads():
    """macos.sh marks install.sh's browser pool question answered before the installer runs
    (autonomouse is not installed yet, so it cannot set browser.pool first). The marker is a
    line in install.sh's state folder; this fails when install.sh renames it."""
    install = (ROOT / "setup" / "install.sh").read_text()
    macos = MACOS_SH.read_text()
    assert 'STATE="${XDG_STATE_HOME:-$HOME/.local/state}/autonomouse-install"' in install
    assert 'ANSWERED="$STATE/answered"' in install
    assert "|| answered browser-pool" in install
    assert '"${XDG_STATE_HOME:-$HOME/.local/state}/autonomouse-install"' in macos
    assert "echo browser-pool" in macos
