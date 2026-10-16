#!/usr/bin/env bash
# macos.sh — runs autonomouse and a browsermaze browser pool in a Linux VM on this Mac.
#
#   bash setup/macos.sh            (from an autonomouse checkout, in a terminal)
#
# What it does, in order:
#   1. checks the Mac: macOS, Homebrew, Google Chrome (browsermaze logs your sites in with
#      it); installs Lima (the VM manager) and uv (builds the autonomouse wheel) with
#      Homebrew when they are missing
#   2. finds the browsermaze checkout: ../browsermaze next to this checkout, cloned there
#      from GitHub when it is missing (or the one --browsermaze names)
#   3. creates and starts one Lima VM named autonomouse: Ubuntu 24.04 for this Mac's own
#      processor (arm64 on Apple silicon) under Apple's virtualization framework (vz), with no
#      folder of this Mac mounted in it, and no containerd
#   4. in the VM, installs the browser pool (browsermaze's setup.sh --role pool) listening on
#      127.0.0.1, with the VM user as the login that leases slots and a key that user uses
#      for ssh to itself
#   5. on this Mac, adds "Include ~/.lima/*/ssh.config" as the first line of ~/.ssh/config
#      (after a backup) so that `ssh lima-autonomouse` works, then installs the `browsermaze`
#      command (setup.sh --role login --pool lima-autonomouse)
#   6. in the VM, builds autonomouse from this checkout and runs setup/install.sh. Its
#      questions (the GitHub token, the Telegram bot token, which a blank answer skips, and
#      the first project) are asked in this terminal. When Claude Code in the VM is not
#      logged in, the installer stops; this script runs `claude auth login` there, then runs
#      the installer again
#   7. in the VM, sets browser.pool to <vm user>@127.0.0.1 and browser.slots to the pool's
#      slot count
#   8. prints a checklist, one [ok] or [FAIL] line per item and a fix: line for each failure.
#      The exit status is 0 when everything is in place, 1 when anything failed, 2 for
#      invalid options.
#
# Running it again changes nothing that is already in place: a VM that exists is kept (and
# started when stopped; the size options apply only when the VM is created), every installer
# skips what is installed, and the installer's questions already answered are not asked again.
# What it never touches: the Mac's files other than ~/.ssh/config (and its backup), the
# browsermaze command and its config, and the Homebrew packages above.
#
# Why uv runs on the Mac: setup/install.sh needs the wheel before it can install uv in the VM,
# and the wheel is pure Python, so the Mac builds it and copies it into the VM with install.sh.
# Why the VM mounts nothing: agents run in it with the VM user's rights, and a mounted
# home folder would show them this Mac's files. Files go in over ssh instead.
#
# This file runs under the bash 3.2 that macOS ships. Sourcing it (the tests do) defines the
# functions and runs nothing.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"  # the autonomouse checkout
VM_NAME=autonomouse
VM_HOST=lima-$VM_NAME            # the Host name in Lima's ssh.config
BROWSERMAZE_URL=https://github.com/riodeuno/browsermaze
STAGE=.local/share/autonomouse-macos  # in the VM's home: the copies this script puts there
POOL_CONFIG=/etc/browsermaze/config   # in the VM: where browsermaze's setup.sh writes the pool's settings
LIMA_DIR="${LIMA_HOME:-$HOME/.lima}"
SSH_DIR="$HOME/.ssh"
SSH_USER_CONFIG="$SSH_DIR/config"
MAC_BIN="$HOME/.local/bin"       # where browsermaze's setup.sh links the browsermaze command

usage() {
  cat <<'EOF'
usage: bash setup/macos.sh [options]

Creates the Linux VM "autonomouse" on this Mac, and runs autonomouse and a browsermaze
browser pool in it. The size options apply when the VM is created.

  --cpus N           virtual CPUs (default: 4, or the Mac's CPU count when lower)
  --memory GIB       memory in GiB (default: 8, or half the Mac's memory when lower)
  --disk GIB         disk size in GiB; the disk file grows only as the VM fills it (default: 60)
  --slots N          browser slots in the pool, 1 to 9 (default: the pool's own, 4)
  --browsermaze DIR  a browsermaze checkout (default: ../browsermaze, cloned when missing)
  -h, --help         this text
EOF
}

say() { printf '==> %s\n' "$*"; }
die() { echo "macos.sh: $*" >&2; exit 1; }
usage_error() { echo "macos.sh: $*" >&2; echo "run setup/macos.sh --help for the options" >&2; exit 2; }

# --- options ---------------------------------------------------------------------------
CPUS=""
MEMORY=""
DISK=""
SLOTS=""
BROWSERMAZE_DIR=""
SIZE_GIVEN=0

valid_count() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }
valid_slots() { [[ "$1" =~ ^[1-9]$ ]]; }  # the pool's ports are 930N, so N is one digit
need_value() { [ "$1" -ge 2 ] || usage_error "$2 needs a value"; }

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --cpus)   need_value $# "$1"; CPUS="$2"; SIZE_GIVEN=1; shift 2 ;;
      --memory) need_value $# "$1"; MEMORY="$2"; SIZE_GIVEN=1; shift 2 ;;
      --disk)   need_value $# "$1"; DISK="$2"; SIZE_GIVEN=1; shift 2 ;;
      --slots)  need_value $# "$1"; SLOTS="$2"; shift 2 ;;
      --browsermaze) need_value $# "$1"; BROWSERMAZE_DIR="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; exit 2 ;;
    esac
  done
  [ -z "$CPUS" ] || valid_count "$CPUS" || usage_error "--cpus needs a whole number of 1 or more, not '$CPUS'"
  [ -z "$MEMORY" ] || valid_count "$MEMORY" || usage_error "--memory needs a whole number of GiB, 1 or more, not '$MEMORY'"
  [ -z "$DISK" ] || valid_count "$DISK" || usage_error "--disk needs a whole number of GiB, 1 or more, not '$DISK'"
  [ -z "$SLOTS" ] || valid_slots "$SLOTS" || usage_error "--slots needs a number from 1 to 9, not '$SLOTS'"
  return 0
}

# Estimates, not measurements. A slot is a Chrome with its own display (about 1 GiB), and the
# VM also runs Claude Code sessions (node, several at once during a wake) and the OS, so four
# slots need about 8 GiB. Lima's own default of 4 GiB would swap. The CPU default is Lima's
# (4, and never more than the Mac has); the memory default never takes more than half the Mac.
default_cpus() {
  local n; n="$(sysctl -n hw.ncpu)"
  if [ "$n" -lt 4 ]; then echo "$n"; else echo 4; fi
}
default_memory() {
  local gib; gib=$(( $(sysctl -n hw.memsize) / 1073741824 / 2 ))
  if [ "$gib" -lt 8 ]; then echo "$gib"; else echo 8; fi
}
DEFAULT_DISK=60  # estimate: Ubuntu and the tools take about 10 GiB; the rest is profiles, clones and worktrees

# --- the Mac ---------------------------------------------------------------------------
chrome_path() { echo "${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"; }

brew_install_if_missing() {  # command formula
  command -v "$1" >/dev/null && return 0
  say "Installing $2 with Homebrew"
  brew install "$2" || die "brew could not install $2. fix: run brew install $2 and read its error, then run setup/macos.sh again"
}

check_mac() {
  [ "$(uname -s)" = Darwin ] || die "this is $(uname -s); setup/macos.sh runs on macOS. On Ubuntu, follow docs/setup.md"
  [ -f "$DIR/pyproject.toml" ] || die "$DIR is not an autonomouse checkout (no pyproject.toml)"
  command -v brew >/dev/null || die "Homebrew is missing. fix: install it from https://brew.sh, then run setup/macos.sh again"
  [ -x "$(chrome_path)" ] || die "Google Chrome is missing; browsermaze logs your sites in with it. fix: run brew install --cask google-chrome (or set CHROME to a Chrome executable), then run setup/macos.sh again"
  brew_install_if_missing limactl lima
  brew_install_if_missing uv uv
}

find_browsermaze() {
  if [ -z "$BROWSERMAZE_DIR" ]; then
    BROWSERMAZE_DIR="$(dirname "$DIR")/browsermaze"
    if [ ! -e "$BROWSERMAZE_DIR" ]; then
      say "Cloning browsermaze into $BROWSERMAZE_DIR"
      git clone "$BROWSERMAZE_URL" "$BROWSERMAZE_DIR" \
        || die "git could not clone $BROWSERMAZE_URL. fix: clone it to $BROWSERMAZE_DIR yourself (or pass --browsermaze DIR), then run setup/macos.sh again"
    fi
  fi
  { [ -f "$BROWSERMAZE_DIR/setup.sh" ] && [ -f "$BROWSERMAZE_DIR/VERSION" ]; } \
    || die "$BROWSERMAZE_DIR is not a browsermaze checkout (it has no setup.sh and VERSION). fix: pass --browsermaze DIR"
  BROWSERMAZE_DIR="$(cd "$BROWSERMAZE_DIR" && pwd -P)"
}

# The first line of ~/.ssh/config, because an Include below a Host line applies only to that
# host. The line is Lima's own (https://lima-vm.io/docs/usage/ssh/).
lima_include_line() {
  local lima_home="$LIMA_DIR"
  case "$lima_home" in "$HOME"/*) lima_home="~${lima_home#"$HOME"}" ;; esac
  printf 'Include %s/*/ssh.config\n' "$lima_home"
}

ensure_ssh_include() {
  local line backup
  line="$(lima_include_line)"
  if [ -f "$SSH_USER_CONFIG" ] && grep -qxF -- "$line" "$SSH_USER_CONFIG"; then
    echo "$SSH_USER_CONFIG already has: $line"; return 0
  fi
  [ -d "$SSH_DIR" ] || mkdir -m 700 "$SSH_DIR"
  if [ -f "$SSH_USER_CONFIG" ]; then
    backup="$SSH_USER_CONFIG.backup-$(date +%Y%m%d-%H%M%S)"
    cp -p "$SSH_USER_CONFIG" "$backup"
    # Written in place from the backup, so a symlinked config and its mode stay as they are.
    { printf '%s\n\n' "$line"; cat "$backup"; } >"$SSH_USER_CONFIG"
    echo "$SSH_USER_CONFIG: added as its first line: $line (backup: $backup)"
  else
    ( umask 077; printf '%s\n' "$line" >"$SSH_USER_CONFIG" )
    echo "$SSH_USER_CONFIG: created with: $line"
  fi
}

# --- the VM ----------------------------------------------------------------------------
VM_SSH_CONFIG=""
VM_USER=""
VM_HOME=""

vm_status() { limactl list --format '{{.Status}}' "$VM_NAME" 2>/dev/null || true; }
vm_exists() { limactl list --quiet 2>/dev/null | grep -qx "$VM_NAME"; }

ensure_vm() {
  local status
  if ! vm_exists; then
    say "Creating the VM $VM_NAME: $CPUS CPUs, $MEMORY GiB memory, $DISK GiB disk. The first start downloads Ubuntu 24.04 and takes several minutes"
    limactl start --tty=false --name="$VM_NAME" --vm-type=vz --containerd=none --mount-none \
      --cpus="$CPUS" --memory="$MEMORY" --disk="$DISK" template:ubuntu-24.04 \
      || die "limactl could not create the VM; its log is $LIMA_DIR/$VM_NAME/ha.stderr.log. fix: read that log; to start over, run limactl delete --force $VM_NAME, then run setup/macos.sh again"
    return 0
  fi
  [ "$SIZE_GIVEN" = 0 ] || echo "the VM $VM_NAME exists, so --cpus, --memory and --disk are ignored (to resize it: limactl stop $VM_NAME, then limactl edit $VM_NAME --memory GIB)"
  status="$(vm_status)"
  case "$status" in
    Running) ;;
    Stopped) say "Starting the VM $VM_NAME"
      limactl start --tty=false "$VM_NAME" \
        || die "limactl could not start the VM; its log is $LIMA_DIR/$VM_NAME/ha.stderr.log. fix: read that log, then run setup/macos.sh again" ;;
    *) die "the VM $VM_NAME is '${status:-unknown}', not Running or Stopped. fix: run limactl stop --force $VM_NAME, then run setup/macos.sh again" ;;
  esac
}

# Everything that runs in the VM goes over ssh with Lima's own ssh config, so it needs nothing
# from ~/.ssh/config. A command runs in a login shell, which puts ~/.local/bin on PATH the way
# the VM user's terminal has it.
remote_command() {  # words...: one line for the VM's shell that runs the words
  local words
  printf -v words '%q ' "$@"
  printf 'bash -l -c %q' "$words"
}
vm()        { ssh -F "$VM_SSH_CONFIG" -n "$VM_HOST" "$(remote_command "$@")"; }   # stdin: none
vm_in()     { ssh -F "$VM_SSH_CONFIG" "$VM_HOST" "$(remote_command "$@")"; }      # stdin: ours
vm_tty()    { ssh -F "$VM_SSH_CONFIG" -t "$VM_HOST" "$(remote_command "$@")"; }   # a terminal: ours
vm_script() { vm_in bash -s; }  # runs the script on stdin

vm_put() {  # local-dir vm-dir [tar option...]: vm-dir becomes a copy of local-dir
  local src="$1" dest="$2"; shift 2
  # shellcheck disable=SC2016  # the VM's shell expands $1, not this one
  COPYFILE_DISABLE=1 tar -C "$src" --no-xattrs "$@" -cf - . \
    | vm_in bash -c 'rm -rf "$1" && mkdir -p "$1" && tar -xf - -C "$1"' _ "$dest"
}

vm_facts() {
  VM_SSH_CONFIG="$(limactl list --format '{{.SSHConfigFile}}' "$VM_NAME")"
  [ -f "$VM_SSH_CONFIG" ] || die "Lima has no ssh config for $VM_NAME (looked for '$VM_SSH_CONFIG'). fix: run limactl stop $VM_NAME, then run setup/macos.sh again"
  VM_USER="$(vm id -un)" || die "cannot ssh into the VM with: ssh -F $VM_SSH_CONFIG $VM_HOST. fix: run limactl shell $VM_NAME and read its error"
  VM_HOME="$(vm printenv HOME)"
}

# --- the steps -------------------------------------------------------------------------
# Each step runs in a subshell with errexit on, so its first failing command stops it and
# the step reports failure. A failed step does not stop the others: the checklist at the end
# looks at the result of each.
run_step() {  # result-variable title function [args...]
  local var="$1" title="$2" rc=0; shift 2
  say "$title"
  set +e
  ( set -e; "$@" )
  rc=$?
  set -e
  printf -v "$var" '%s' "$rc"
  if [ "$rc" -ne 0 ]; then echo "    this step failed (exit status $rc); the checklist below names its fix"; fi
}

# Runs in the VM as the VM user. The key is the one the user's ssh offers by itself
# (autonomouse runs `ssh user@127.0.0.1 browsermaze lease`). The host key goes into
# known_hosts because ssh asks to trust 127.0.0.1 once, and a wake cannot answer.
vm_ssh_to_self_script() {
  cat <<'EOF'
set -e
mkdir -p -m 700 "$HOME/.ssh"
[ -f "$HOME/.ssh/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C "autonomouse@$(hostname)" -f "$HOME/.ssh/id_ed25519"
ssh-keygen -F 127.0.0.1 -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1 || ssh-keyscan -H 127.0.0.1 >>"$HOME/.ssh/known_hosts" 2>/dev/null || true
ssh-keygen -F 127.0.0.1 -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1 || { echo "sshd in the VM gave no host key on 127.0.0.1" >&2; exit 1; }
ssh-keygen -y -f "$HOME/.ssh/id_ed25519"
EOF
}

step_pool() {
  local key stage="$VM_HOME/$STAGE"
  key="$(vm_ssh_to_self_script | vm_script | tail -n 1)"
  case "$key" in "ssh-ed25519 "*) ;; *) echo "the VM user's ssh key did not come back as a public key" >&2; return 1 ;; esac
  vm_put "$BROWSERMAZE_DIR" "$stage/browsermaze" --exclude=.git
  vm bash "$stage/browsermaze/setup.sh" --role pool --bind 127.0.0.1 --caller "$VM_USER" \
    --authorize-key "$key" ${SLOTS:+--slots "$SLOTS"}
}

step_mac() {
  ensure_ssh_include
  bash "$BROWSERMAZE_DIR/setup.sh" --role login --pool "$VM_HOST"
}

vm_mark_pool_answered_script() {
  cat <<'EOF'
state="${XDG_STATE_HOME:-$HOME/.local/state}/autonomouse-install"
mkdir -p "$state"
grep -qx browser-pool "$state/answered" 2>/dev/null || echo browser-pool >>"$state/answered"
EOF
}

step_autonomouse() {
  local stage="$VM_HOME/$STAGE/autonomouse" wheel="" f
  uv build --wheel --out-dir "$WORK/wheel" "$DIR"
  for f in "$WORK/wheel"/*.whl; do wheel="$f"; done
  [ -f "$wheel" ] || { echo "uv build made no wheel in $WORK/wheel" >&2; return 1; }
  mkdir -p "$WORK/stage"
  cp "$DIR/setup/install.sh" "$wheel" "$WORK/stage/"
  vm_put "$WORK/stage" "$stage"
  # install.sh's third question (the browser pool) is skipped once it is marked answered. The
  # pool is this VM's own; step_config sets it, because only the installer can install the
  # autonomouse command that sets it.
  vm_mark_pool_answered_script | vm_script
  run_installer() { vm_tty bash "$stage/install.sh" --wheel "$stage/$(basename "$wheel")"; }
  run_installer && return 0
  vm claude --version >/dev/null 2>&1 || return 1       # no claude yet: the installer failed earlier
  vm claude auth status >/dev/null 2>&1 && return 1     # logged in: the installer failed for another reason
  if [ ! -t 0 ]; then  # the login waits for a pasted code; with no terminal it would wait forever
    echo "Claude Code in the VM needs a login, and this run has no terminal to log in from. fix: run setup/macos.sh again in a terminal, or run: limactl shell $VM_NAME claude auth login" >&2
    return 1
  fi
  say "Claude Code in the VM needs a login (the installer said so); logging in, then running the installer again"
  vm_tty claude auth login || return 1
  run_installer
}

vm_config_get() { vm autonomouse config show | sed -n "s/^$1 = //p"; }
pool_slots() { vm sed -n 's/^POOL_SLOTS=//p' "$POOL_CONFIG"; }

step_config() {
  local want_slots want_pool="$VM_USER@127.0.0.1"
  want_slots="$(pool_slots)"
  valid_slots "$want_slots" || { echo "$POOL_CONFIG in the VM has no valid POOL_SLOTS (got '$want_slots'); did the pool step finish?" >&2; return 1; }
  [ "$(vm_config_get browser.pool)" = "$want_pool" ] || vm autonomouse config set browser.pool "$want_pool"
  [ "$(vm_config_get browser.slots)" = "$want_slots" ] || vm autonomouse config set browser.slots "$want_slots"
  echo "browser.pool = $want_pool, browser.slots = $want_slots"
}

# --- the checklist ---------------------------------------------------------------------
FAILED=0
check() {  # item fix command...
  local item="$1" fix="$2"; shift 2
  if ( "$@" ) </dev/null >/dev/null 2>&1; then
    printf '  [ok]   %s\n' "$item"
  else
    printf '  [FAIL] %s\n         fix: %s\n' "$item" "$fix"
    FAILED=$((FAILED + 1))
  fi
}

step_ok() { [ "$1" = 0 ]; }  # the exit status run_step stored for a step
vm_running() { [ "$(vm_status)" = Running ]; }
autonomouse_version_is() { [ "$(vm autonomouse --version)" = "autonomouse $1" ]; }
config_matches_pool() {
  [ "$(vm_config_get browser.pool)" = "$VM_USER@127.0.0.1" ] && [ "$(vm_config_get browser.slots)" = "$(pool_slots)" ]
}
lease_and_release() {  # what autonomouse does: ssh to the pool user at 127.0.0.1
  local out slot pool=(ssh -o BatchMode=yes -o ConnectTimeout=10 "$VM_USER@127.0.0.1")
  out="$(vm "${pool[@]}" browsermaze lease)" || return 1
  slot="${out%% *}"
  valid_slots "$slot" || return 1
  vm "${pool[@]}" browsermaze release "$slot"
}
mac_ssh_works() { ssh -n -o BatchMode=yes -o ConnectTimeout=10 "$VM_HOST" true; }
browsermaze_version_is() { [ "$("$MAC_BIN/browsermaze" version)" = "browsermaze $(cat "$BROWSERMAZE_DIR/VERSION")" ]; }

# --- run -------------------------------------------------------------------------------
main() {
  local version POOL_RC MAC_RC INSTALL_RC
  parse_args "$@"
  check_mac
  find_browsermaze
  CPUS="${CPUS:-$(default_cpus)}"
  MEMORY="${MEMORY:-$(default_memory)}"
  DISK="${DISK:-$DEFAULT_DISK}"
  version="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$DIR/pyproject.toml" | head -n 1)"
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT

  ensure_vm
  vm_facts
  run_step POOL_RC "Installing the browser pool in the VM" step_pool
  run_step MAC_RC "Setting up ssh and the browsermaze command on this Mac" step_mac
  run_step INSTALL_RC "Installing autonomouse in the VM (its questions are asked here)" step_autonomouse
  run_step _CONFIG_RC "Pointing autonomouse at the pool" step_config  # config_matches_pool checks the result

  echo
  echo "Checklist"
  check "VM $VM_NAME is running" "run: limactl start $VM_NAME   (its log: $LIMA_DIR/$VM_NAME/ha.stderr.log)" vm_running
  check "autonomouse $version runs in the VM" \
    "read the installer's output above and fix what it names; or run: limactl shell $VM_NAME ~/.local/bin/autonomouse --version; then run setup/macos.sh again" \
    autonomouse_version_is "$version"
  check "autonomouse install.sh finished with no failed item" \
    "the installer printed its own checklist above: fix the items it names, then run setup/macos.sh again" step_ok "$INSTALL_RC"
  check "browser.pool and browser.slots in the VM point at its own pool" \
    "run setup/macos.sh again; or in the VM: autonomouse config set browser.pool $VM_USER@127.0.0.1, and autonomouse config set browser.slots <the pool's slot count>" \
    config_matches_pool
  check "browsermaze pool setup.sh finished with no failed item" \
    "the pool's own checklist is above: fix the items it names, then run setup/macos.sh again" step_ok "$POOL_RC"
  check "browsermaze lease and release work through ssh $VM_USER@127.0.0.1 inside the VM" \
    "in the VM (limactl shell $VM_NAME) run: ssh $VM_USER@127.0.0.1 browsermaze lease   and read its error; the pool's setup log is ~/.local/state/browsermaze-setup/ there" \
    lease_and_release
  check "ssh $VM_HOST true works from this Mac" \
    "check that $SSH_USER_CONFIG starts with: $(lima_include_line)   then run: ssh -F $LIMA_DIR/$VM_NAME/ssh.config $VM_HOST true" \
    mac_ssh_works
  check "browsermaze login setup.sh finished with no failed item" \
    "its own checklist is above (a missing ~/.local/bin on PATH is the usual cause): fix the items it names, then run setup/macos.sh again" step_ok "$MAC_RC"
  check "browsermaze $(cat "$BROWSERMAZE_DIR/VERSION") on this Mac" \
    "run: bash $BROWSERMAZE_DIR/setup.sh --role login --pool $VM_HOST" browsermaze_version_is

  echo
  if [ "$FAILED" -ne 0 ]; then
    echo "$FAILED checklist item(s) failed; each names its fix. Running setup/macos.sh again changes nothing that is already in place."
    exit 1
  fi
  echo "All green."
  echo "  A shell in the VM:  limactl shell $VM_NAME      (or: ssh $VM_HOST)"
  echo "  The board:          ssh -t $VM_HOST '~/.local/bin/autonomouse'"
  echo "  The logins:         browsermaze login"
  echo "  More:               docs/macos.md"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
