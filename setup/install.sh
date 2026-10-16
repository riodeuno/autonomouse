#!/usr/bin/env bash
# install.sh — installs autonomouse and every tool in setup/tools.txt on bare Ubuntu 24.04
# (noble, x86_64 or arm64), installs the worker agent types, registers the MCP servers,
# then, on a terminal, asks what is left to set up, and last prints a checklist: one line per
# tool, and for anything that failed, the command or change that fixes it.
#
# The questions (README, "Install"), each asked with gum on the terminal (/dev/tty), never
# on stdin, which under `curl ... | bash` is this script; each is skipped when it is already
# answered, so a second run asks nothing. Claude Code must be logged in first: without a
# login the installer stops here, naming `claude auth login`.
#   1. a GitHub personal access token, handed to `gh auth login` (skipped while gh is logged in)
#   2. the Telegram bot token, written to ~/.config/autonomouse/.env (mode 600); then it waits
#      for your message to the bot and saves the chat id with `autonomouse notify setup`
#   3. the browser pool as user@host, written as browser.pool (blank leaves it unset)
#   4. when the first wake runs, written as wake.start; `autonomouse timer install` then
#      schedules wakes from that time, every wake.interval_hours
#   5. the first project, last, so the install ends in its live session: the installer runs
#      `autonomouse project new` on the terminal, and it asks the project's own questions
#      with gum, the same ones it asks for every later project: the name, the repository
#      URL, the name and email the agents commit with, the branch their pull requests go
#      into, and the Linear project or issue it is linked to (blank for none); it asks no
#      goal, and ends in a live Claude session in the project's repository folder that
#      writes the goal and brief with you (skipped while a project is on the board)
# The installer writes nothing else by itself: the questions end in the same commands a
# human runs by hand. With no terminal (a pipe, cron, CI) or after a failed step, nothing is
# asked, and four checklist items stay open until you act, each naming its step:
#   gh login   run: gh auth login
#   project    run: autonomouse project new <name>   (it asks the GitHub URL, logs gh in when
#              needed, clones the repository and installs the schedule)
#   telegram   put TELEGRAM_BOT_TOKEN=<the token from @BotFather> in ~/.config/autonomouse/.env
#              with mode 600, then message the bot once and run: autonomouse notify setup
#   claude login   run: claude auth login
# The cron package is pinned like the other apt tools, so the crontab lines that
# `autonomouse timer install` writes fire; the `cron daemon` item names the command that
# starts cron when it is not running.
#
#   bash setup/install.sh --wheel dist/autonomouse-<version>-py3-none-any.whl   (from a checkout)
#   curl -fsSL <your host>/autonomouse/install.sh | bash   (a copy setup/publish.sh served)
#
# Run it as the user who will run autonomouse; it calls sudo only for apt. Everything else
# lands under ~/.local: uv, node, the npm tools, the autonomouse command (~/.local/bin),
# and the Python uv brings with it. The agents step writes ~/.claude/agents/worker-high.md and
# worker-xhigh.md when they are missing; the MCP step writes ~/.claude.json.
# The folders step makes ~/projects/autonomouse with its four folders: repos (each project's
# clone), worktrees (each card's working folder), wake-reports and wake-logs.
# Claude Code comes from Anthropic's own installer, at the latest release, and updates itself:
# ~/.local/bin/claude links into ~/.local/share/claude/versions. Its step first removes any
# npm copy of Claude Code (the one an older install.sh put in ~/.local, and the one `claude
# update` makes in the node folder), so a machine ends up with only the official one.
# Running it again changes nothing: each step first checks whether its pin is already in
# place and skips when it is, and each question whether it is already answered. The answers
# that leave no other trace (a blank browser pool) are listed in
# ~/.local/state/autonomouse-install/answered.
#
# The pins below are copies of setup/tools.txt (this file is served on its own, so it cannot
# read that one); tests/test_install_sh.py fails when the two disagree. Bump a pin in
# tools.txt first, then here. Claude Code is the one tool with no pin: it is always the
# latest release, from the official installer.
set -euo pipefail
shopt -s inherit_errexit

# --- pins (setup/tools.txt) -----------------------------------------------------------
# Each downloaded tool has one pin per architecture, _X86_64 and _AARCH64 (what `uname -m`
# prints); the architecture check below copies the machine's pin set into the plain names
# (UV_URL, APT_PINS, ...) that the steps use.
UV_VERSION=0.12.17
UV_URL_X86_64=https://github.com/astral-sh/uv/releases/download/0.12.17/uv-x86_64-unknown-linux-gnu.tar.gz
UV_SHA256_X86_64=fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63
UV_URL_AARCH64=https://github.com/astral-sh/uv/releases/download/0.12.17/uv-aarch64-unknown-linux-gnu.tar.gz
UV_SHA256_AARCH64=d636d1b678e9e7f367ecb22b46bd1cabbed234d6bc3b4d96365d2b507f72f86c

# apt package, pinned version, sha256 of the .deb
APT_PINS_X86_64=(
  "git 1:2.43.0-1ubuntu7.3 099bb129f543adc4c14203334b0fa0a909f8bf038c4d56bc9cc7c774ebf78f87"
  "fzf 0.44.1-1ubuntu0.3 345ab0b82c0a2dfc364d1da4404b61c438413fc6881daf8acecd9e3821578258"
  "ripgrep 14.1.0-1 c5ae63c7bee915b1cfc9f0bd07c55b4ce7f2bcd1133cba4da56719aac26101a4"
  "fd-find 9.0.0-1 2f4f1bf881e3e967f7ef3da0b757638f4b0db5227f21b2ac0aaa89ae76e6672d"
  "jq 1.7.1-3ubuntu0.24.04.2 f59183392f84fe67b407b494d8ba6185e9b0e4a106ffcb369490bd20a83fd40e"
  "tmux 3.4-1ubuntu0.1 2913e17aa61879d1f905aab6f374f153f1ece63aefba1b996e387325d1d5338d"
  "cron 3.0pl1-184ubuntu2 5a257b6b3b05d290964f68868db8127cca7eeef64c30dc9586f46cd4f68f1b90"
)
APT_PINS_AARCH64=(
  "git 1:2.43.0-1ubuntu7.3 db7f9a9a06a5dd6d6d5bd4bc3b14a2a0f0aaf0cc48f6e096e7ba3f4db63503ee"
  "fzf 0.44.1-1ubuntu0.3 d38ac854e08c2ceabffec271779771b398609f07691a9689e30596268994923f"
  "ripgrep 14.1.0-1 71fe8a5667bc87d2bca175a3426c6ec518e0679cc821d23ae7056de7096f04de"
  "fd-find 9.0.0-1 5c739d4fb19ca7625ee518d83f764ad6720b96b5e3a7a5c4f5b9f603228195d4"
  "jq 1.7.1-3ubuntu0.24.04.2 d26709d728bf14016eac6ec5e56f88027b1751dffe1471e8cda3f6f8a0c8c1dc"
  "tmux 3.4-1ubuntu0.1 8c44cae9ab4e220fa7a8951344af49a54e2e930d7418da1291d1eac027634335"
  "cron 3.0pl1-184ubuntu2 f29126ccb78db3e21b54bb1e351a830bc90e1a0c8eff0a1eb77233384c9cbcf8"
)

GUM_VERSION=2.0.2
GUM_URL_X86_64=https://github.com/charmbracelet/gum/releases/download/v2.0.2/gum_2.0.2_Linux_x86_64.tar.gz
GUM_SHA256_X86_64=d842e06d93dbed90af48cb8dd10698db6f22e331fc40346bb37bbc753109edc2
GUM_URL_AARCH64=https://github.com/charmbracelet/gum/releases/download/v2.0.2/gum_2.0.2_Linux_arm64.tar.gz
GUM_SHA256_AARCH64=8ebf8b54ec1e8c81f2bb58b59ff9b70998186a4d11375f0cf357b80e0ccfa1d5

GH_VERSION=2.101.0
GH_URL_X86_64=https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_linux_amd64.deb
GH_SHA256_X86_64=f876a3b87bf67c94f773d17becca4dc7340b056dab901473a9260ee2a73e237b
GH_URL_AARCH64=https://github.com/cli/cli/releases/download/v2.101.0/gh_2.101.0_linux_arm64.deb
GH_SHA256_AARCH64=9aec87f9a011b1521556b06cb003776e7e214144c8efd2144924a28d90c23057

NODE_VERSION=v24.21.0
NODE_URL_X86_64=https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz
NODE_SHA256_X86_64=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
NODE_URL_AARCH64=https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-arm64.tar.xz
NODE_SHA256_AARCH64=6ad1325edbdb5649c379b75a237147a666c95d4f9ae8d340fef2d1575d289ad2

# npm package, pinned version, tarball URL, npm integrity (sha512)
NPM_PINS=(
  "playwright 1.63.0 https://registry.npmjs.org/playwright/-/playwright-1.63.0.tgz sha512-+7ziBLidS4NaNCdt57SUDT+wYmmd5fmiQejUic/kb+YsYSCPyOOE9sebzMjNmQrsnNpDJqd4WHvV/8lfKfUDUg=="
  "chrome-devtools-mcp 1.9.0 https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-1.9.0.tgz sha512-RnzXoJiUQ44hpOihWk90uOhLD/CnwDkDy0ldHMZONJ2nYQ+dWN1fq1luHHqyd+7FuYnyIlCY6uTThbN5ut9kSQ=="
  "@playwright/mcp 0.0.82 https://registry.npmjs.org/@playwright/mcp/-/mcp-0.0.82.tgz sha512-OCqftfb8H4dnqm/njbTBRk3seUvUPttOlJUxCtEzXGETYOlRH5Qt3bbXIjmZIuWAxD9RF+yg1ASrPeXvm0y5cA=="
)

PYTHON_VERSION=3.13

# Claude Code: Anthropic's official installer, not a pin. It puts the latest release in
# CLAUDE_VERSIONS (below), links ~/.local/bin/claude to it, and updates it from then on.
CLAUDE_INSTALL_URL=https://claude.ai/install.sh

# The autonomouse wheel served beside this script. setup/publish.sh fills these two lines in
# the copy it publishes; the repository's copy keeps them empty, so run from a checkout, pass
# --wheel.
WHEEL_URL=""
WHEEL_SHA256=""

# --- layout ---------------------------------------------------------------------------
BIN="$HOME/.local/bin"
LIB="$HOME/.local/lib"
CLAUDE_VERSIONS="$HOME/.local/share/claude/versions"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/autonomouse-install"
FOLDERS="$HOME/projects/autonomouse"
FOLDER_NAMES=(repos worktrees wake-reports wake-logs)
WHEEL_RECORD="$STATE/wheel.sha256"
ANSWERED="$STATE/answered"
SECRETS="${AUTONOMOUSE_CONFIG:-$HOME/.config/autonomouse}/.env"
PROFILE_LINE='[ -d "$HOME/.local/bin" ] && PATH="$HOME/.local/bin:$PATH"  # autonomouse install.sh'
export PATH="$BIN:$PATH"
export DEBIAN_FRONTEND=noninteractive

WHEEL_ARG=""
usage() {
  echo "usage: install.sh [--wheel PATH]   (a local autonomouse .whl; default: the pinned one)"
}
while [ $# -gt 0 ]; do
  case "$1" in
    --wheel) WHEEL_ARG="${2:?--wheel needs a path}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
if [ -n "$WHEEL_ARG" ]; then
  [ -f "$WHEEL_ARG" ] || { echo "install.sh: --wheel $WHEEL_ARG: no such file" >&2; exit 2; }
  WHEEL_ARG="$(cd "$(dirname "$WHEEL_ARG")" && pwd)/$(basename "$WHEEL_ARG")"
fi

case "$(uname -m)" in
  x86_64)
    UV_URL=$UV_URL_X86_64 UV_SHA256=$UV_SHA256_X86_64
    GUM_URL=$GUM_URL_X86_64 GUM_SHA256=$GUM_SHA256_X86_64
    GH_URL=$GH_URL_X86_64 GH_SHA256=$GH_SHA256_X86_64
    NODE_URL=$NODE_URL_X86_64 NODE_SHA256=$NODE_SHA256_X86_64
    APT_PINS=("${APT_PINS_X86_64[@]}")
    NODE_DIR="$LIB/node-$NODE_VERSION-linux-x64" ;;
  aarch64)
    UV_URL=$UV_URL_AARCH64 UV_SHA256=$UV_SHA256_AARCH64
    GUM_URL=$GUM_URL_AARCH64 GUM_SHA256=$GUM_SHA256_AARCH64
    GH_URL=$GH_URL_AARCH64 GH_SHA256=$GH_SHA256_AARCH64
    NODE_URL=$NODE_URL_AARCH64 NODE_SHA256=$NODE_SHA256_AARCH64
    APT_PINS=("${APT_PINS_AARCH64[@]}")
    NODE_DIR="$LIB/node-$NODE_VERSION-linux-arm64" ;;
  *)
    echo "install.sh: this machine is $(uname -m); setup/tools.txt has pins for x86_64 and aarch64 (arm64) only." >&2
    exit 1 ;;
esac
if [ "$(id -u)" = 0 ]; then SUDO=(); else SUDO=(sudo); fi

mkdir -p "$BIN" "$LIB" "$STATE"
LOG="$STATE/install-$(date -u +%Y%m%dT%H%M%SZ).log"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

CHANGED=0
STEPS_FAILED=0
declare -A FIX=()

# Each step prints "unchanged" when its pin was already in place (return 0 before touching
# anything), "installed" when it changed something, or "FAILED" plus the log's tail. A step
# that fails calls `fix <checklist item> <text>` first; the checklist shows that text.
# A step's stdin is /dev/null: under `curl ... | bash`, stdin is the rest of this script, and
# a program that reads it (a package's install script does, when apt installs cron) ends
# the install there, with exit 0 and no word.
step() {
  local name="$1"; shift
  printf '%-28s ' "$name"
  echo "=== $name" >>"$LOG"
  local rc=0 start; start=$(wc -l <"$LOG")
  set +e
  ( set -e; "$@" ) </dev/null >>"$LOG" 2>&1 3>"$WORK/changed"
  rc=$?
  set -e
  if [ $rc -ne 0 ]; then
    echo "FAILED (log: $LOG)"; STEPS_FAILED=$((STEPS_FAILED + 1))
    tail -n +"$start" "$LOG" | tail -n 5 | sed 's/^/    /'
  elif [ -s "$WORK/changed" ]; then
    echo "installed"; CHANGED=$((CHANGED + 1))
  else
    echo "unchanged"
  fi
  : >"$WORK/changed"
}
changed() { echo x >&3; }
fix() { printf 'FIX %s\t%s\n' "$1" "$2" >&2; }  # lands in the log; collect_fixes reads it back

sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

# On a network whose IPv6 route is broken, a plain curl can connect over IPv6 and then get no
# data, and it waits for that data forever. So each try gives up after 30 s below 1 KB/s, and
# a failed first try is followed by one over IPv4 only. `curl -4` affects that one command:
# nothing about the machine's network changes.
download() {  # url dest
  local bound=(--connect-timeout 30 --speed-limit 1024 --speed-time 30)
  curl -fsSL "${bound[@]}" -o "$2" "$1" && return 0
  echo "download of $1 failed; trying again over IPv4 only" >&2  # stderr: wheel_source prints a path
  curl -4 -fsSL --retry 3 "${bound[@]}" -o "$2" "$1"
}

fetch_verified() {  # url sha256 dest item
  download "$1" "$3" || { fix "$4" "download failed: $1 — check the network, then re-run install.sh"; return 1; }
  local got; got="$(sha256_of "$3")"
  if [ "$got" != "$2" ]; then
    fix "$4" "the download from $1 has sha256 $got, the pin says $2 — do not install it; re-verify the pin in setup/tools.txt"
    return 1
  fi
}

apt_version() { dpkg-query -W -f='${Status} ${Version}' "$1" 2>/dev/null | sed -n 's/^install ok installed //p'; }

# --- steps ----------------------------------------------------------------------------
apt_update_once() {  # steps run in subshells, so the marker is a file
  [ -e "$WORK/apt-updated" ] && return 0
  "${SUDO[@]}" apt-get update -q
  touch "$WORK/apt-updated"
}

step_prereqs() {  # base-system plumbing the pinned downloads need; not pinned
  local missing=()
  for p in ca-certificates curl xz-utils; do [ -n "$(apt_version "$p")" ] || missing+=("$p"); done
  [ ${#missing[@]} -eq 0 ] && return 0
  apt_update_once
  "${SUDO[@]}" apt-get install -y -q "${missing[@]}" || { fix prereqs "run: sudo apt-get install ${missing[*]}"; return 1; }
  changed
}

step_apt() {  # package version sha256
  local pkg="$1" ver="$2" sum="$3"
  [ "$(apt_version "$pkg")" = "$ver" ] && return 0
  apt_update_once
  local dir="$WORK/apt-$pkg"; mkdir -p "$dir"; chmod 755 "$dir"
  (cd "$dir" && apt-get download "$pkg=$ver") || {
    fix "$pkg" "the Ubuntu archive no longer offers $pkg $ver — re-pin $pkg in setup/tools.txt and install.sh (see the tools.txt header)"
    return 1
  }
  local deb; deb="$(ls "$dir"/*.deb)"
  local got; got="$(sha256_of "$deb")"
  [ "$got" = "$sum" ] || { fix "$pkg" "the archive's $pkg $ver has sha256 $got, the pin says $sum — do not install it; re-verify the pin"; return 1; }
  "${SUDO[@]}" apt-get install -y -q --allow-downgrades "$deb" || { fix "$pkg" "apt-get could not install $deb; read $LOG"; return 1; }
  changed
}

step_fd_link() {  # Ubuntu ships fd as `fdfind`; tools.txt names it `fd`
  local target; target="$(command -v fdfind)" || { fix fd "install fd-find first (its line above names the fix)"; return 1; }
  [ "$(readlink "$BIN/fd" 2>/dev/null)" = "$target" ] && return 0
  ln -sfn "$target" "$BIN/fd"
  changed
}

step_gh() {
  [ "$(apt_version gh)" = "$GH_VERSION" ] && return 0
  fetch_verified "$GH_URL" "$GH_SHA256" "$WORK/gh.deb" gh
  apt_update_once
  "${SUDO[@]}" apt-get install -y -q --allow-downgrades "$WORK/gh.deb" || { fix gh "apt-get could not install gh; read $LOG"; return 1; }
  changed
}

step_uv() {
  [ -x "$BIN/uv" ] && [ "$("$BIN/uv" --version | cut -d' ' -f2)" = "$UV_VERSION" ] && return 0
  fetch_verified "$UV_URL" "$UV_SHA256" "$WORK/uv.tar.gz" uv
  tar -xzf "$WORK/uv.tar.gz" -C "$WORK"
  local dir; dir="$WORK/$(basename "$UV_URL" .tar.gz)"  # the tarball's one folder, named like it
  install -m 755 "$dir/uv" "$dir/uvx" "$BIN/"
  changed
}

step_gum() {  # the prompt tool every question below is asked with
  [ -x "$BIN/gum" ] && [ "$("$BIN/gum" --version | cut -d' ' -f3)" = "v$GUM_VERSION" ] && return 0
  fetch_verified "$GUM_URL" "$GUM_SHA256" "$WORK/gum.tar.gz" gum
  tar -xzf "$WORK/gum.tar.gz" -C "$WORK"
  install -m 755 "$WORK/$(basename "$GUM_URL" .tar.gz)/gum" "$BIN/gum"  # the tarball's one folder, named like it
  changed
}

step_python() {
  uv python find --managed-python "$PYTHON_VERSION" >/dev/null 2>&1 && return 0
  uv python install "$PYTHON_VERSION" || { fix python "run: uv python install $PYTHON_VERSION   (uv downloads its own Python)"; return 1; }
  changed
}

step_node() {
  [ -x "$BIN/node" ] && [ "$("$BIN/node" --version)" = "$NODE_VERSION" ] && return 0
  if [ ! -x "$NODE_DIR/bin/node" ]; then
    fetch_verified "$NODE_URL" "$NODE_SHA256" "$WORK/node.tar.xz" node
    tar -xJf "$WORK/node.tar.xz" -C "$LIB"
  fi
  for b in node npm npx; do ln -sfn "$NODE_DIR/bin/$b" "$BIN/$b"; done
  changed
}

npm_installed_version() {
  local manifest="$HOME/.local/lib/node_modules/$1/package.json"
  [ -f "$manifest" ] && node -p "require(process.argv[1]).version" "$manifest"
  true
}

step_npm() {  # package version url integrity
  local pkg="$1" ver="$2" url="$3" integrity="$4"
  [ "$(npm_installed_version "$pkg")" = "$ver" ] && return 0
  local tgz="$WORK/$(basename "$url")"
  download "$url" "$tgz" || { fix "$pkg" "download failed: $url — check the network, then re-run install.sh"; return 1; }
  local want got
  want="$(printf '%s' "${integrity#sha512-}" | base64 -d | od -An -v -tx1 | tr -d ' \n')"
  got="$(sha512sum "$tgz" | cut -d' ' -f1)"
  [ "$got" = "$want" ] || { fix "$pkg" "the download from $url does not match its pinned sha512 — do not install it; re-verify the pin"; return 1; }
  npm install -g --prefix "$HOME/.local" --no-fund --no-audit "$tgz" || { fix "$pkg" "npm could not install $pkg $ver; read $LOG"; return 1; }
  changed
}

claude_is_official() {  # ~/.local/bin/claude links into the folder the official installer fills
  case "$(readlink "$BIN/claude" 2>/dev/null)" in "$CLAUDE_VERSIONS"/*) ;; *) return 1 ;; esac
}

# Both npm copies go first: `npm uninstall` deletes ~/.local/bin/claude even once that link
# points elsewhere, so removing a copy after the official install would break it. One copy is
# what this installer used to install; the other is what `claude update` makes in the node
# folder when it runs from an npm copy.
step_claude() {
  local pkg=@anthropic-ai/claude-code
  if [ -d "$HOME/.local/lib/node_modules/$pkg" ]; then
    npm uninstall -g --prefix "$HOME/.local" "$pkg" || { fix claude "npm could not remove $pkg from ~/.local; read $LOG"; return 1; }
    changed
  fi
  if [ -d "$NODE_DIR/lib/node_modules/$pkg" ]; then
    "$NODE_DIR/bin/npm" uninstall -g "$pkg" || { fix claude "npm could not remove $pkg from $NODE_DIR; read $LOG"; return 1; }
    changed
  fi
  claude_is_official && return 0
  local script="$WORK/claude-install.sh"
  download "$CLAUDE_INSTALL_URL" "$script" \
    || { fix claude "download failed: $CLAUDE_INSTALL_URL — check the network, then re-run install.sh"; return 1; }
  bash "$script" || { fix claude "the official installer failed (read $LOG); to run it alone: curl -fsSL $CLAUDE_INSTALL_URL | bash"; return 1; }
  changed
}

claude_works() { claude_is_official && claude --version; }

playwright_browser_dirs() {  # the install locations `playwright install` would use for chromium
  playwright install --dry-run chromium | sed -n 's/^ *Install location: *//p'
}

# The apt packages chromium needs that dpkg lacks. Playwright 1.63's dry run lists them one
# per indented line under "Missing system dependencies (N):".
playwright_missing_deps() {
  local pkgs p
  pkgs="$(playwright install-deps --dry-run chromium 2>/dev/null \
    | sed -n '/^Missing system dependencies/,$ s/^ \{2,\}\([a-z0-9][a-z0-9.+-]*\)$/\1/p' || true)"
  for p in $pkgs; do [ -n "$(apt_version "$p")" ] || echo "$p"; done
}

# Playwright's downloader (playwright 1.63 on node 24) tries IPv6 first and abandons the whole
# request when that connection hangs for 5 s. So on a network whose IPv6 default route reaches
# nothing, every one of its attempts fails, while curl and apt get through over IPv4. The
# second try preloads this file into playwright's node processes: it answers each IPv6
# address lookup with no addresses, so the download goes over IPv4. The file lives in $WORK,
# and nothing on the machine changes.
write_ipv4_only_preload() {  # path
  cat >"$1" <<'JS'
const dns = require("dns");
const lookup = dns.promises.lookup;
dns.promises.lookup = (host, options) =>
  options && options.family === 6 && options.all ? Promise.resolve([]) : lookup(host, options);
JS
}

playwright_install_chromium() {
  playwright install chromium && return 0
  echo "playwright install chromium failed; trying again with IPv4 only for its downloads"
  write_ipv4_only_preload "$WORK/ipv4-only.js"
  NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--require=$WORK/ipv4-only.js" playwright install chromium
}

chromium_path() {  # the Chromium executable the pinned playwright package installs and drives
  NODE_PATH="$LIB/node_modules" node -e 'process.stdout.write(require("playwright").chromium.executablePath())'
}

step_browsers() {
  local dirs d missing=0
  dirs="$(playwright_browser_dirs)"
  [ -n "$dirs" ] || { fix chromium "playwright printed no install location; is playwright installed? read $LOG"; return 1; }
  for d in $dirs; do [ -d "$d" ] || missing=1; done
  if [ -n "$(playwright_missing_deps)" ]; then
    apt_update_once
    "${SUDO[@]}" env "PATH=$PATH" playwright install-deps chromium || { fix chromium "run: sudo env PATH=\$PATH playwright install-deps chromium"; return 1; }
    changed
  fi
  if [ $missing = 1 ]; then
    playwright_install_chromium || { fix chromium "check the network, then re-run install.sh; read $LOG"; return 1; }
    changed
  fi
}

# The worker agent types the orchestrator spawns (docs/orchestrator.md). The text is a copy of
# setup/agents/<name>.md (this file is served on its own and cannot read that folder);
# tests/test_install_sh.py fails when the two disagree. A file already in ~/.claude/agents is
# left as it is, so a global Claude Code setup that provides its own wins.
AGENT_NAMES=(worker-high worker-xhigh)
agent_file() {  # name — the agent's file
  case "$1" in
    worker-high) cat <<'EOF'
---
name: worker-high
description: General worker at high reasoning effort. Use for a delegated piece with a clear goal and a few decisions inside it — a contained code change, a probe that must interpret what it finds.
effort: high
disallowedTools: Artifact, ArtifactCheck, ArtifactComments, ArtifactData
hooks:
  PreToolUse:
    - matcher: Skill
      hooks:
        - type: command
          command: >-
            jq -e '.tool_input.skill // "" | test("^(verifying|review|code-review)$|:code-review$")' >/dev/null
            && { echo 'A worker does not run the verifying or code-review skill: the agent that started you verifies your work. Finish your charter and return your evidence.' >&2; exit 2; }; exit 0
---

Carry out the charter in your first message: it states the goal, the scope, and what to return.
Return the evidence for what you did; the agent that started you verifies it.
EOF
    ;;
    worker-xhigh) cat <<'EOF'
---
name: worker-xhigh
description: General worker at xhigh reasoning effort. Use for a delegated piece that carries many decisions inside its own scope — a change across several files, an investigation that must weigh several causes.
effort: xhigh
disallowedTools: Artifact, ArtifactCheck, ArtifactComments, ArtifactData
hooks:
  PreToolUse:
    - matcher: Skill
      hooks:
        - type: command
          command: >-
            jq -e '.tool_input.skill // "" | test("^(verifying|review|code-review)$|:code-review$")' >/dev/null
            && { echo 'A worker does not run the verifying or code-review skill: the agent that started you verifies your work. Finish your charter and return your evidence.' >&2; exit 2; }; exit 0
---

Carry out the charter in your first message: it states the goal, the scope, and what to return.
Return the evidence for what you did; the agent that started you verifies it.
EOF
    ;;
    *) echo "agent_file: unknown agent $1" >&2; return 1 ;;
  esac
}

agents_present() {
  local n
  for n in "${AGENT_NAMES[@]}"; do [ -f "$HOME/.claude/agents/$n.md" ] || return 1; done
}

step_agents() {
  local n
  for n in "${AGENT_NAMES[@]}"; do
    [ -e "$HOME/.claude/agents/$n.md" ] && continue
    mkdir -p "$HOME/.claude/agents"
    agent_file "$n" >"$HOME/.claude/agents/$n.md"
    changed
  done
}

# The MCP servers Claude Code starts for every session (user scope, ~/.claude.json). Both run
# the pinned npm packages through the pinned node by absolute path, so they start whatever
# PATH claude runs with, and both drive the Chromium that Playwright installed. No sandbox:
# Ubuntu 24.04 blocks the unprivileged user namespaces Chromium's sandbox needs for a
# browser outside /opt.
mcp_server_json() {  # name
  local chrome; chrome="$(chromium_path)"
  case "$1" in
    playwright) jq -cS -n --arg node "$BIN/node" --arg cli "$(readlink -f "$BIN/playwright-mcp")" --arg chrome "$chrome" \
      '{type: "stdio", command: $node, args: [$cli, "--browser", "chromium", "--executable-path", $chrome, "--headless", "--no-sandbox", "--caps", "vision"], env: {}}' ;;
    chrome-devtools) jq -cS -n --arg node "$BIN/node" --arg cli "$(readlink -f "$BIN/chrome-devtools-mcp")" --arg chrome "$chrome" \
      '{type: "stdio", command: $node, args: [$cli, "--executablePath", $chrome, "--headless", "--chromeArg=--no-sandbox", "--chromeArg=--disable-setuid-sandbox"], env: {}}' ;;
    *) echo "mcp_server_json: unknown server $1" >&2; return 1 ;;
  esac
}

mcp_registered() {  # name — its entry in ~/.claude.json, or nothing
  jq -cS --arg n "$1" '.mcpServers[$n] // empty' "$HOME/.claude.json" 2>/dev/null || true
}

mcp_matches() { [ "$(mcp_registered "$1")" = "$(mcp_server_json "$1")" ]; }

step_mcp() {  # name
  local want; want="$(mcp_server_json "$1")"
  [ "$(mcp_registered "$1")" = "$want" ] && return 0
  if [ -n "$(mcp_registered "$1")" ]; then claude mcp remove --scope user "$1"; fi
  claude mcp add-json --scope user "$1" "$want" || { fix "MCP $1" "claude could not register the $1 MCP server; read $LOG"; return 1; }
  changed
}

wheel_source() {  # prints the wheel path to install, downloading the pinned one if needed
  if [ -n "$WHEEL_ARG" ]; then echo "$WHEEL_ARG"; return 0; fi
  if [ -z "$WHEEL_URL" ]; then
    fix autonomouse "this copy of install.sh pins no autonomouse wheel (only the copy setup/publish.sh serves does) — build one with \`uv build --wheel\` in the repository and re-run: bash install.sh --wheel dist/autonomouse-<version>-py3-none-any.whl"
    return 1
  fi
  local dest; dest="$WORK/$(basename "$WHEEL_URL")"
  fetch_verified "$WHEEL_URL" "$WHEEL_SHA256" "$dest" autonomouse
  echo "$dest"
}

step_wheel() {
  local whl sum
  whl="$(wheel_source)"
  sum="$(sha256_of "$whl")"
  if [ -x "$BIN/autonomouse" ] && [ "$(cat "$WHEEL_RECORD" 2>/dev/null)" = "$sum" ]; then return 0; fi
  uv tool install --force --managed-python --python "$PYTHON_VERSION" "$whl" \
    || { fix autonomouse "uv could not install $whl; read $LOG"; return 1; }
  echo "$sum" >"$WHEEL_RECORD"
  changed
}

folders_present() {  # every folder FOLDER_NAMES names is in FOLDERS
  local d
  for d in "${FOLDER_NAMES[@]}"; do [ -d "$FOLDERS/$d" ] || return 1; done
}

step_folders() {  # ~/projects/autonomouse and its four folders (FOLDER_NAMES)
  folders_present && return 0
  local d; for d in "${FOLDER_NAMES[@]}"; do mkdir -p "$FOLDERS/$d"; done
  changed
}

login_path_has_bin() { env -i HOME="$HOME" bash -lc 'case ":$PATH:" in *":$HOME/.local/bin:"*) exit 0;; *) exit 1;; esac'; }

step_path() {  # a new login shell finds ~/.local/bin
  login_path_has_bin && return 0
  printf '\n%s\n' "$PROFILE_LINE" >>"$HOME/.profile"
  changed
}

# --- the questions --------------------------------------------------------------------
# Asked after the steps, on a terminal only, in the header's order. Each reads the terminal
# (/dev/tty) and never stdin, which under `curl ... | bash` is the rest of this script, and
# each is skipped when its answer is already in place. They end in the commands a human runs
# by hand: gh auth login, autonomouse notify setup, autonomouse config set, autonomouse
# project new and autonomouse timer install. The installer asks the questions that belong to
# the machine; `autonomouse project new` asks the project's own, on the same terminal.

# What a token needs for `autonomouse project new` to prove it can do a worker's job
# (autonomouse.github_access, TOKEN_NEEDS).
TOKEN_NEEDS="Contents, Pull requests and Actions read and write, and Commit statuses read, on the repository; a classic token needs the repo and read:org scopes"

has_terminal() { ( : </dev/tty ) 2>/dev/null; }

ask() {  # header [gum input flags...]: prints the answer; ctrl-c stops the installer
  local header="$1"; shift
  gum input --header "$header" --width 0 "$@" </dev/tty
}

ask_nonempty() {  # header [gum input flags...]: asks again until the answer is not blank
  local answer=""
  while [ -z "$answer" ]; do answer="$(ask "$@")"; done
  printf '%s\n' "$answer"
}

answered() { grep -qx "$1" "$ANSWERED" 2>/dev/null; }
mark_answered() { answered "$1" || echo "$1" >>"$ANSWERED"; }

config_value() { autonomouse config show | sed -n "s/^$1 = //p"; }

secret_keys() {  # the key names in the secrets file; values are never printed
  [ -f "$SECRETS" ] || return 0
  sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' "$SECRETS"
}

write_secret() {  # key value: sets one key in the secrets file, keeps every other line, mode 600
  local tmp
  mkdir -p "$(dirname "$SECRETS")"
  tmp="$(mktemp "$SECRETS.XXXXXX")"  # mktemp makes the file with mode 600
  if [ -f "$SECRETS" ]; then grep -vE "^[[:space:]]*(export[[:space:]]+)?$1=" "$SECRETS" >"$tmp" || true; fi
  printf '%s=%s\n' "$1" "$2" >>"$tmp"
  mv "$tmp" "$SECRETS"
}

setup_claude_login() {
  claude auth status >/dev/null 2>&1 && return 0
  echo "Claude Code is not logged in on this machine, and every session autonomouse starts needs it."
  echo "Log in first, then run the install command again:"
  echo "  claude auth login"
  exit 1
}

setup_github() {  # 1
  if gh auth status --hostname github.com >/dev/null 2>&1; then
    echo "1. GitHub token: gh is already logged in"; return 0
  fi
  local token
  while :; do
    token="$(ask_nonempty "GitHub personal access token for gh ($TOKEN_NEEDS)" --password)"
    printf '%s\n' "$token" | gh auth login --hostname github.com --with-token && break
    echo "gh did not take that token (its error is above); give another"
  done
  gh auth setup-git --hostname github.com
  echo "1. GitHub token: gh is logged in, and git over https uses its login"
}

ask_telegram_token() {
  local token  # assigned apart from `local`, so a ctrl-c in the question stops the installer
  token="$(ask_nonempty "Telegram bot token, from @BotFather" --password)"
  write_secret TELEGRAM_BOT_TOKEN "$token"
}

setup_telegram() {  # 2
  if secret_keys | grep -qx TELEGRAM_BOT_TOKEN; then
    echo "2. Telegram bot token: already in $SECRETS"
  else
    ask_telegram_token
    echo "2. Telegram bot token: written to $SECRETS (mode 600)"
  fi
  if [ -n "$(config_value notify.telegram_chat_id)" ]; then
    echo "   Telegram chat: already saved"; return 0
  fi
  echo "   Now send your bot any message on Telegram; the installer waits for it."
  local out
  while ! out="$(autonomouse notify setup 2>&1)"; do
    case "$out" in
      *"has no messages yet"*)
        gum spin --title "Waiting for your message to the bot on Telegram" -- sleep 3 </dev/tty ;;
      *"rejected getUpdates: HTTP 401"*|*"rejected getUpdates: HTTP 404"*)
        echo "   Telegram does not know that bot token; give it again"
        ask_telegram_token ;;
      *) echo "$out"; echo "Fix the cause above, then run the install command again."; exit 1 ;;
    esac
  done
  echo "   Telegram chat: $out"
}

project_exists() { autonomouse project list --json | jq -e 'length > 0' >/dev/null; }

setup_browsers() {  # 3
  if [ -n "$(config_value browser.pool)" ] || answered browser-pool; then
    echo "3. browser pool: already answered"; return 0
  fi
  local pool
  pool="$(ask "The browser pool as user@host, whose logged-in Chromes the wakes borrow (optional, see https://github.com/riodeuno/browsermaze; blank: none, each wake uses a local browser)")"
  [ -z "$pool" ] || autonomouse config set browser.pool "$pool"
  mark_answered browser-pool
}

# 5: `project new` asks each of its questions with gum when its stdin is a terminal, so its
# stdin is the terminal, never the pipe that holds the rest of this script.
setup_project() {  # 5
  if project_exists; then
    echo "5. project: a project is already on the board"; return 0
  fi
  echo "5. project: autonomouse project new asks the project's questions"
  if ! autonomouse project new </dev/tty; then
    project_exists || { echo "autonomouse project new stopped; fix the cause above, then run the install command again."; exit 1; }
    echo "autonomouse project new created the project and then stopped; its fix is above."
  fi
}

first_wake_at() {  # when: the next whole minute at or after it, as seconds; fails on no time
  local epoch now
  epoch="$(date -d "${1#in }" +%s 2>/dev/null)" || return 1
  now="$(date +%s)"
  [ "$epoch" -ge $((now - 60)) ] || return 1  # a time that has passed
  if [ "$epoch" -le "$now" ]; then echo $(((now / 60 + 1) * 60)); else echo $(((epoch + 59) / 60 * 60)); fi
}

setup_first_wake() {  # 4
  if [ -n "$(config_value wake.start)" ]; then
    echo "4. first wake: already set, wakes run from $(config_value wake.start)"; return 0
  fi
  local hours when start
  hours="$(config_value wake.interval_hours)"
  while :; do
    when="$(ask "When does the first wake run? For example: now, in 2 hours, tomorrow 09:00. Then a wake runs every $hours hours from that time." --value now)"
    start="$(first_wake_at "$when")" && break
    echo "   '$when' is not a time to come; give another"
  done
  autonomouse config set wake.start "$(date -d "@$start" +%Y-%m-%dT%H:%M%:z)"
  autonomouse timer install
  echo "4. first wake: $(date -d "@$start" '+%Y-%m-%d %H:%M'), then every $hours hours"
}

ask_the_questions() {
  if [ "$STEPS_FAILED" -ne 0 ]; then
    echo "Setup questions: not asked until every step above succeeds."; return 0
  fi
  if ! has_terminal; then
    echo "Setup questions: not asked, with no terminal to ask on; the checklist names each open item's fix."
    return 0
  fi
  echo "Setup questions (each is asked once; a second run skips what is answered)"
  setup_claude_login
  setup_github
  setup_telegram
  setup_browsers
  setup_first_wake
  setup_project  # last: it ends in the project's live Claude session
}

# --- checklist ------------------------------------------------------------------------
collect_fixes() {  # the FIX lines steps wrote to the log, last one per item wins
  local line item text
  while IFS= read -r line; do
    item="${line%%	*}"; text="${line#*	}"
    FIX["${item#FIX }"]="$text"
  done < <(grep '^FIX ' "$LOG" || true)
}

FAILED=0
check() {  # item fix-if-no-step-said command...
  local item="$1" default_fix="$2"; shift 2
  if ( "$@" ) </dev/null >/dev/null 2>&1; then  # stdin: as in step()
    printf '  [ok]   %s\n' "$item"
  else
    printf '  [FAIL] %s\n         fix: %s\n' "$item" "${FIX[$item]:-$default_fix}"
    FAILED=$((FAILED + 1))
  fi
}
has_version() { local want="$1"; shift; "$@" 2>&1 | grep -qF -- "$want"; }

# --- run ------------------------------------------------------------------------------
echo "autonomouse install.sh — log: $LOG"
step prereqs step_prereqs
for pin in "${APT_PINS[@]}"; do
  read -r pkg ver sum <<<"$pin"
  step "$pkg $ver" step_apt "$pkg" "$ver" "$sum"
done
step "fd -> fdfind" step_fd_link
step "gh $GH_VERSION" step_gh
step "uv $UV_VERSION" step_uv
step "gum $GUM_VERSION" step_gum
step "python $PYTHON_VERSION (uv)" step_python
step "node $NODE_VERSION" step_node
step "claude (official installer)" step_claude
for pin in "${NPM_PINS[@]}"; do
  read -r pkg ver url integrity <<<"$pin"
  step "$pkg $ver" step_npm "$pkg" "$ver" "$url" "$integrity"
done
step "chromium (playwright)" step_browsers
step "worker agents" step_agents
step "MCP playwright" step_mcp playwright
step "MCP chrome-devtools" step_mcp chrome-devtools
step "autonomouse wheel" step_wheel
step "~/projects/autonomouse" step_folders
step "~/.local/bin on login PATH" step_path

echo
ask_the_questions

collect_fixes
echo
echo "Checklist"
check prereqs "run: sudo apt-get install ca-certificates curl xz-utils" \
  bash -c 'command -v curl && command -v xz && [ -s /etc/ssl/certs/ca-certificates.crt ]'
declare -A APT_VERIFY=([git]="git --version" [fzf]="fzf --version" [ripgrep]="rg --version"
  [fd-find]="fdfind --version" [jq]="jq --version" [tmux]="tmux -V" [cron]="command -v crontab")
for pin in "${APT_PINS[@]}"; do
  read -r pkg ver sum <<<"$pin"
  check "$pkg" "re-run install.sh and read $LOG" \
    bash -c '[ "$1" = "$2" ] && $3' _ "$(apt_version "$pkg")" "$ver" "${APT_VERIFY[$pkg]}"
done
check "cron daemon" "run: sudo systemctl enable --now cron   (the crontab lines autonomouse timer install writes fire only while cron runs)" \
  bash -c 'pgrep -x cron || pgrep -x crond'
check "fd" "run: ln -sf \"\$(command -v fdfind)\" ~/.local/bin/fd" fd --version
check "gh"       "re-run install.sh and read $LOG" has_version "gh version $GH_VERSION" gh --version
check "gh login" "run: gh auth login   (or export GH_TOKEN in the environment autonomouse runs in)" gh auth status
check "uv"       "re-run install.sh and read $LOG" has_version "uv $UV_VERSION" uv --version
check "gum"      "re-run install.sh and read $LOG" has_version "gum version v$GUM_VERSION" gum --version
check "python"   "run: uv python install $PYTHON_VERSION" uv python find --managed-python "$PYTHON_VERSION"
check "node"     "re-run install.sh and read $LOG" has_version "$NODE_VERSION" node --version
check "npx"      "re-run install.sh and read $LOG" npx --version
check "claude" "re-run install.sh and read $LOG" claude_works
check "playwright" "re-run install.sh and read $LOG" has_version "1.63.0" playwright --version
check "chromium" "run: playwright install --with-deps chromium" \
  bash -c 'd="$(playwright install --dry-run chromium | sed -n "s/^ *Install location: *//p")"; [ -n "$d" ] && for x in $d; do [ -d "$x" ] || exit 1; done'
check "chromium runs" "run: sudo env PATH=\$PATH playwright install-deps chromium" bash -c '"$1" --version' _ "$(chromium_path 2>/dev/null)"
check "chrome-devtools-mcp" "re-run install.sh and read $LOG" has_version "1.9.0" chrome-devtools-mcp --version
check "@playwright/mcp" "re-run install.sh and read $LOG" has_version "0.0.82" playwright-mcp --version
check "worker agents" "copy setup/agents/worker-high.md and worker-xhigh.md to ~/.claude/agents" agents_present
check "MCP playwright" "re-run install.sh and read $LOG" mcp_matches playwright
check "MCP chrome-devtools" "re-run install.sh and read $LOG" mcp_matches chrome-devtools
check "autonomouse" "re-run install.sh and read $LOG" autonomouse --help
check "~/projects/autonomouse" "run: mkdir -p ~/projects/autonomouse/{repos,worktrees,wake-reports,wake-logs}" folders_present
check "~/.local/bin on login PATH" "add this line to ~/.profile, then log in again: $PROFILE_LINE" login_path_has_bin
check "project" "run: autonomouse project new <name>   (it asks the GitHub URL, logs gh in when needed, clones the repository and installs the schedule)" \
  bash -c 'autonomouse project list --json | jq -e "length > 0"'
# The token is checked by key name only; nothing reads or prints its value.
check "telegram" "put TELEGRAM_BOT_TOKEN=<the token from @BotFather> in $SECRETS with mode 600, then message the bot once and run: autonomouse notify setup" \
  bash -c 'grep -qE "^(export )?TELEGRAM_BOT_TOKEN=" "$1" && autonomouse config show | grep -qE "^notify\.telegram_chat_id = ."' _ "$SECRETS"
check "claude login" "run: claude auth login   (then re-run install.sh)" claude auth status

echo
if [ "$FAILED" -eq 0 ] && [ "$STEPS_FAILED" -eq 0 ]; then
  echo "All green. Steps that changed something this run: $CHANGED."
else
  echo "$FAILED checklist item(s) and $STEPS_FAILED step(s) failed; each failed item above names its fix, and the log names each failed step's cause: $LOG"
  echo "Steps that changed something this run: $CHANGED."
  exit 1
fi
