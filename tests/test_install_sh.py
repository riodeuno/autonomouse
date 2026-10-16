"""setup/install.sh carries its own copy of every pin in setup/tools.txt (it is served on its
own and cannot read tools.txt); these tests fail when the two disagree. The install itself
is tested by setup/test-install.sh in a clean container."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1] / "setup"
INSTALL_SH = (SETUP / "install.sh").read_text()


def _tools() -> list[dict[str, str]]:
    blocks = [
        b
        for b in (SETUP / "tools.txt").read_text().split("\n\n")
        if b.startswith("tool:")
    ]
    tools = []
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            key, _, value = line.partition(": ")
            fields[key] = value
        tools.append(fields)
    return tools


def test_tools_list_parses_every_tool():
    assert [t["tool"] for t in _tools()] == [
        "uv",
        "git",
        "gh",
        "fzf",
        "ripgrep",
        "fd",
        "jq",
        "tmux",
        "cron",
        "node",
        "Claude Code",
        "Playwright",
        "Chrome DevTools MCP server",
        "Playwright MCP server",
        "gum",
    ]


@pytest.mark.parametrize("tool", _tools(), ids=lambda t: t["tool"])
def test_install_sh_has_the_pinned_version_and_digest(tool):
    if tool["kind"] != "official-installer":  # Claude Code: the latest release, no pin
        version = tool["version"].split()[0]
        assert version in INSTALL_SH or version.removeprefix("v") in INSTALL_SH
        digest = tool.get("sha256") or tool["sha512 (npm integrity)"]
        assert digest in INSTALL_SH
        if tool["kind"] != "npm":  # an npm package is pure JavaScript: one pin for both
            assert re.fullmatch(r"[0-9a-f]{64}", tool["sha256 (arm64)"])
            assert tool["sha256 (arm64)"] in INSTALL_SH
        else:
            assert tool["arm64"].startswith("no second pin")
    url = re.match(r"https://[^\s,]+", tool["source"])
    if url:
        assert url.group(0) in INSTALL_SH
    url_arm64 = re.match(r"https://[^\s,]+", tool.get("source (arm64)", ""))
    if url_arm64:
        assert url_arm64.group(0) in INSTALL_SH


def test_install_sh_parses():
    subprocess.run(["bash", "-n", str(SETUP / "install.sh")], check=True)


# sys-s14: install.sh installs cron like the other apt tools, so the crontab lines
# `autonomouse timer install` writes fire on a fresh machine, and its checklist leads a new
# machine to a working project: `project`, `telegram` and `cron daemon` items, no
# `autonomouse init`.


def _checklist_items() -> dict[str, str]:
    """Each `check "<item>" "<fix>" ...` line of install.sh, item to fix text."""
    return dict(re.findall(r'^ *check "([^"]+)" +"([^"]*)"', INSTALL_SH, re.MULTILINE))


def test_cron_is_pinned_in_tools_txt_and_install_sh_alike():
    (cron,) = [t for t in _tools() if t["tool"] == "cron"]
    assert cron["kind"] == "apt-deb"
    for arch, key in (("X86_64", "sha256"), ("AARCH64", "sha256 (arm64)")):
        apt_pins = re.search(
            rf"APT_PINS_{arch}=\((.*?)\n\)", INSTALL_SH, re.DOTALL
        ).group(1)
        assert f'"cron {cron["version"]} {cron[key]}"' in apt_pins
    assert '[cron]="command -v crontab"' in INSTALL_SH


def test_the_checklist_leads_to_a_project_telegram_and_a_running_cron():
    items = _checklist_items()
    assert "board" not in items
    assert "autonomouse init" not in INSTALL_SH
    assert items["project"].startswith("run: autonomouse project new <name>")
    for words in (
        "asks the GitHub URL",
        "logs gh in when needed",
        "clones",
        "installs the schedule",
    ):
        assert words in items["project"]
    assert 'autonomouse project list --json | jq -e "length > 0"' in INSTALL_SH
    assert items["telegram"] == (
        "put TELEGRAM_BOT_TOKEN=<the token from @BotFather> in $SECRETS with mode 600, then "
        "message the bot once and run: autonomouse notify setup   (to skip Telegram instead: "
        "autonomouse config set notify.telegram false)"
    )
    # Telegram deliberately off is an [ok] line that says so and names the way back on.
    assert (
        'echo "  [ok]   telegram: off, you answer on the board; to turn it on, put '
        "TELEGRAM_BOT_TOKEN=<the token from @BotFather> in $SECRETS with mode 600, message "
        'the bot once and run: autonomouse notify setup"'
    ) in INSTALL_SH
    assert (
        'grep -qE "^(export )?TELEGRAM_BOT_TOKEN=" "$1"' in INSTALL_SH
    )  # the key's name only
    assert 'grep -qE "^notify\\.telegram_chat_id = ."' in INSTALL_SH
    assert items["cron daemon"].startswith("run: sudo systemctl enable --now cron")
    assert "bash -c 'pgrep -x cron || pgrep -x crond'" in INSTALL_SH
    assert list(items)[-3:] == ["project", "telegram", "claude login"]


def test_install_sh_header_names_the_open_items_and_their_steps():
    """The README's install section names the same four."""
    header = INSTALL_SH.split("set -euo pipefail")[0]
    for step in (
        "gh login   run: gh auth login",
        "project    run: autonomouse project new <name>",
        "telegram   put TELEGRAM_BOT_TOKEN=<the token from @BotFather> in ~/.config/autonomouse/.env",
        "run: autonomouse notify setup",
        "autonomouse config set notify.telegram false",
        "claude login   run: claude auth login",
    ):
        assert step in header
    assert "autonomouse init" not in header


# sys-s59: the install makes ~/projects/autonomouse with its four folders, where autonomouse
# keeps each project's clone, each card's worktree, the wake reports and the wake logs.

FOLDER_NAMES = ["repos", "worktrees", "wake-reports", "wake-logs"]


def _bash_function(name: str) -> str:
    """The text of install.sh's function `name`, from its first line to its closing brace."""
    return re.search(
        rf"^{name}\(\) {{.*?^}}\n", INSTALL_SH, re.MULTILINE | re.DOTALL
    ).group(0)


def _assignment(name: str) -> str:
    return re.search(rf"^{name}=.*$", INSTALL_SH, re.MULTILINE).group(0)


def test_the_folders_step_makes_the_four_folders_once(tmp_path):
    """install.sh's folders step, run on its own with HOME at a temporary folder: the first
    run makes the four folders and says it changed something, the second changes nothing."""
    script = "\n".join([
        _assignment("FOLDERS"), _assignment("FOLDER_NAMES"),
        _bash_function("folders_present"), _bash_function("step_folders"),
        "changed() { echo changed; }",
        "step_folders; echo first run done; step_folders; echo second run done",
        "folders_present && echo present",
    ])  # fmt: skip
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script], env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True, text=True, check=True,
    )  # fmt: skip

    assert result.stdout.splitlines() == [
        "changed", "first run done", "second run done", "present",
    ]  # fmt: skip
    folders = tmp_path / "projects" / "autonomouse"
    assert sorted(p.name for p in folders.iterdir() if p.is_dir()) == sorted(
        FOLDER_NAMES
    )


def test_the_install_runs_the_folders_step_and_checks_it():
    assert 'step "~/projects/autonomouse" step_folders' in INSTALL_SH
    items = _checklist_items()
    assert items["~/projects/autonomouse"] == (
        "run: mkdir -p ~/projects/autonomouse/{repos,worktrees,wake-reports,wake-logs}"
    )
    check = r'^check "~/projects/autonomouse" .* folders_present$'
    assert re.search(check, INSTALL_SH, re.MULTILINE)


# The worker agent types the orchestrator spawns: install.sh carries a copy of each file under
# setup/agents/ and writes it to ~/.claude/agents only when that file is missing, so a global
# Claude Code setup that provides its own wins. The hook in each file refuses the verifying and
# code-review skills (the agent that started a worker verifies its work).

AGENTS = SETUP / "agents"
AGENT_FILES = sorted(AGENTS.glob("worker-*.md"))


def test_the_shipped_agents_are_worker_high_and_worker_xhigh():
    assert [p.name for p in AGENT_FILES] == ["worker-high.md", "worker-xhigh.md"]
    assert _assignment("AGENT_NAMES") == "AGENT_NAMES=(worker-high worker-xhigh)"


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.name)
def test_install_sh_carries_each_agent_file_unchanged(path):
    script = _bash_function("agent_file") + f"agent_file {path.stem}"
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout == path.read_text()


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.name)
def test_each_agent_names_itself_and_refuses_the_review_skills(path):
    text = path.read_text()
    assert f"name: {path.stem}\n" in text
    assert "effort: " + path.stem.removeprefix("worker-") + "\n" in text
    assert "verifying|review|code-review" in text
    assert "exit 2" in text


def test_the_agents_step_writes_missing_files_and_keeps_existing_ones(tmp_path):
    """install.sh's agents step, run on its own with HOME at a temporary folder: the first run
    writes both files and says it changed something; a file that is already there stays as it
    is, whatever it holds; a second run changes nothing."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "worker-xhigh.md").write_text("the owner's own\n")
    script = "\n".join([
        _assignment("AGENT_NAMES"), _bash_function("agent_file"),
        _bash_function("agents_present"), _bash_function("step_agents"),
        "changed() { echo changed; }",
        "agents_present || echo absent",
        "step_agents; step_agents",
        "agents_present && echo present",
    ])  # fmt: skip
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script], env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True, text=True, check=True,
    )  # fmt: skip

    assert result.stdout.splitlines() == ["absent", "changed", "present"]
    assert (agents / "worker-high.md").read_text() == (
        AGENTS / "worker-high.md"
    ).read_text()
    assert (agents / "worker-xhigh.md").read_text() == "the owner's own\n"


def test_the_install_runs_the_agents_step_and_checks_it():
    assert 'step "worker agents" step_agents' in INSTALL_SH
    assert "claude-config" not in INSTALL_SH
    assert re.search(
        r'^check "worker agents" .* agents_present$', INSTALL_SH, re.MULTILINE
    )


# sys-w1: on a network whose IPv6 default route reaches nothing, Playwright's downloader fails
# every attempt (it abandons a request when its IPv6 connection hangs), so install.sh tries
# `playwright install chromium` again with a preload that gives it IPv4 addresses only.


def test_the_chromium_download_retries_over_ipv4_only(tmp_path):
    """install.sh's browsers step, run with a stub playwright whose install fails unless an
    IPv4-only preload is on NODE_OPTIONS, as the real one does on such a network: the step
    installs Chromium on its second try and says it changed something."""
    browser_dir = tmp_path / "ms-playwright" / "chromium-1243"
    stub = tmp_path / "bin" / "playwright"
    stub.parent.mkdir()
    stub.write_text(
        "#!/bin/bash\n"
        'case "$*" in\n'
        f'  "install --dry-run chromium") echo "  Install location:    {browser_dir}" ;;\n'
        '  "install-deps --dry-run chromium") ;;\n'
        '  "install chromium")\n'
        '    preload="${NODE_OPTIONS##*--require=}"\n'
        '    if [ -f "$preload" ] && grep -qF "options.family === 6" "$preload"; then\n'
        f'      mkdir -p "{browser_dir}"\n'
        "    else\n"
        '      echo "Request to https://cdn.playwright.dev/x.zip timed out after 30000ms" >&2\n'
        "      exit 1\n"
        "    fi ;;\n"
        '  *) echo "stub playwright: unexpected arguments: $*" >&2; exit 2 ;;\n'
        "esac\n"
    )  # fmt: skip
    stub.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    script = "\n".join([
        f'WORK="{work}"',
        _bash_function("playwright_browser_dirs"), _bash_function("playwright_missing_deps"),
        _bash_function("write_ipv4_only_preload"), _bash_function("playwright_install_chromium"),
        _bash_function("step_browsers"),
        "changed() { echo changed; }", 'fix() { echo "FIX $*"; }',
        "step_browsers",
    ])  # fmt: skip
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}", "NODE_OPTIONS": ""},
        capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "playwright install chromium failed; trying again with IPv4 only for its downloads",
        "changed",
    ]  # fmt: skip
    assert browser_dir.is_dir()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_ipv4_only_preload_gives_node_no_ipv6_addresses(tmp_path):
    """Node, with the preload install.sh writes, finds no IPv6 address for a lookup that asks
    for IPv6 only, the way Playwright's downloader asks, and still finds IPv4 ones."""
    preload = tmp_path / "ipv4-only.js"
    script = (
        _bash_function("write_ipv4_only_preload")
        + f'write_ipv4_only_preload "{preload}"'
    )
    subprocess.run(["bash", "-c", script], check=True)
    lookups = (
        'const dns = require("dns");'
        "Promise.all([dns.promises.lookup('::1', {all: true, family: 6}),"
        " dns.promises.lookup('127.0.0.1', {all: true, family: 4})])"
        ".then((r) => console.log(JSON.stringify(r)));"
    )

    plain = subprocess.run(
        ["node", "-e", lookups], capture_output=True, text=True, check=True
    )
    preloaded = subprocess.run(
        ["node", f"--require={preload}", "-e", lookups],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(plain.stdout) == [
        [{"address": "::1", "family": 6}], [{"address": "127.0.0.1", "family": 4}],
    ]  # fmt: skip
    assert json.loads(preloaded.stdout) == [[], [{"address": "127.0.0.1", "family": 4}]]


# sys-w7: on a network whose IPv6 route is broken, a plain curl can connect over IPv6 and then
# wait forever for data. install.sh's downloads give up on a stalled try and try once more over
# IPv4 only, for that one command.


def test_fetch_verified_retries_over_ipv4_after_a_stalled_try(tmp_path):
    """fetch_verified, run with a stub curl that stalls (exit 28, as a bounded curl does)
    unless it is told -4: the first try carries a stall bound, the second is IPv4 only, and
    the file lands with its pinned sha256. The notice goes to stderr, since wheel_source's
    caller reads its stdout as a path."""
    calls = tmp_path / "calls"
    stub = tmp_path / "bin" / "curl"
    stub.parent.mkdir()
    stub.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >>"{calls}"\n'
        'out=""; prev=""; v4=0\n'
        'for a in "$@"; do [ "$prev" = -o ] && out="$a"; [ "$a" = -4 ] && v4=1; prev="$a"; done\n'
        'if [ $v4 = 1 ]; then printf payload >"$out"; exit 0; fi\n'
        'echo "curl: (28) Operation too slow. Less than 1024 bytes/sec transferred the last 30 seconds" >&2\n'
        "exit 28\n"
    )  # fmt: skip
    stub.chmod(0o755)
    dest = tmp_path / "tool.tar.gz"
    payload_sha256 = "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
    script = "\n".join([
        _bash_function("sha256_of"), _bash_function("download"), _bash_function("fetch_verified"),
        'fix() { echo "FIX $*"; }',
        f'fetch_verified https://example.com/tool.tar.gz {payload_sha256} "{dest}" tool',
    ])  # fmt: skip
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}"},
        capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert "trying again over IPv4 only" in result.stderr
    first, second = calls.read_text().splitlines()
    assert "-4" not in first.split()
    for flag in ("--connect-timeout 30", "--speed-limit 1024", "--speed-time 30"):
        assert flag in first and flag in second
    assert "-4" in second.split()
    assert dest.read_text() == "payload"


def test_the_npm_downloads_use_the_same_helper():
    """The npm tarballs download before or beside Chromium too."""
    assert 'download "$url" "$tgz"' in _bash_function("step_npm")
    assert 'download "$1" "$3"' in _bash_function("fetch_verified")


# The machine's architecture picks the pin set: x86_64 and arm64 (`uname -m` aarch64) are
# supported, and anything else is refused naming both.


def _apt_pins(arch: str) -> list[list[str]]:
    body = re.search(rf"APT_PINS_{arch}=\((.*?)\n\)", INSTALL_SH, re.DOTALL).group(1)
    return [pin.split() for pin in re.findall(r'"([^"]+)"', body)]


def test_both_architectures_pin_the_same_apt_packages_and_versions():
    x86, arm = _apt_pins("X86_64"), _apt_pins("AARCH64")
    assert [p[:2] for p in x86] == [p[:2] for p in arm]
    assert not {p[2] for p in x86} & {
        p[2] for p in arm
    }  # a .deb is built per architecture


def _arch_selection() -> str:
    """The `case "$(uname -m)"` block, which copies one architecture's pins to the plain names."""
    return re.search(
        r'^case "\$\(uname -m\)" in\n.*?^esac\n', INSTALL_SH, re.MULTILINE | re.DOTALL
    ).group(0)


def _select(machine: str, tmp_path) -> subprocess.CompletedProcess:
    """Run the pins and the architecture block with `uname -m` answering `machine`."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "uname").write_text(f"#!/bin/sh\necho {machine}\n")
    (fake_bin / "uname").chmod(0o755)
    pins = re.search(
        r"^UV_VERSION=.*?(?=^# --- layout)", INSTALL_SH, re.MULTILINE | re.DOTALL
    ).group(0)
    show = (
        'echo "$UV_URL $UV_SHA256"; echo "$GUM_URL $GUM_SHA256"; echo "$GH_URL $GH_SHA256"\n'
        'echo "$NODE_URL $NODE_SHA256 $NODE_DIR"; printf "%s\\n" "${APT_PINS[@]}"'
    )
    script = f"LIB=/lib\n{pins}\n{_arch_selection()}\n{show}"
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("machine", "arch_words"),
    [("x86_64", ("x86_64-unknown-linux-gnu", "Linux_x86_64", "linux_amd64", "linux-x64")),
     ("aarch64", ("aarch64-unknown-linux-gnu", "Linux_arm64", "linux_arm64", "linux-arm64"))],
)  # fmt: skip
def test_the_machine_architecture_picks_its_own_pins(machine, arch_words, tmp_path):
    result = _select(machine, tmp_path)
    assert result.returncode == 0, result.stderr
    uv, gum, gh, node, *apt = result.stdout.splitlines()
    for line, word in zip((uv, gum, gh, node), arch_words):
        assert word in line
    assert node.endswith(f"/lib/node-v24.21.0-{arch_words[3]}")
    key = "sha256" if machine == "x86_64" else "sha256 (arm64)"
    tools = {t["tool"]: t for t in _tools()}
    for line, name in zip((uv, gum, gh, node), ("uv", "gum", "gh", "node")):
        assert line.split()[1] == tools[name][key]
    assert [pin.split()[2] for pin in apt] == [
        t[key] for t in _tools() if t["kind"] == "apt-deb"
    ]
    assert len(apt) == 7


def test_an_unsupported_architecture_is_refused_naming_both_supported_ones(tmp_path):
    result = _select("riscv64", tmp_path)
    assert result.returncode == 1
    assert "riscv64" in result.stderr
    assert (
        "x86_64" in result.stderr
        and "aarch64" in result.stderr
        and "arm64" in result.stderr
    )
