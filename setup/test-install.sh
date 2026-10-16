#!/usr/bin/env bash
# test-install.sh — the install.sh acceptance test: run install.sh twice in a clean
# ubuntu:24.04 container as a sudo user, then once more logged in to Claude Code, and pass
# only when
#   1. the first two runs' checklists fail on four items only, each naming its fix: the
#      cron daemon (`run: sudo systemctl enable --now cron`; the container has no init to
#      start the cron that install.sh installed), the project (`run: autonomouse project new
#      <name>`; the container has no project yet), telegram (`put TELEGRAM_BOT_TOKEN=<the
#      token from @BotFather> in ~/.config/autonomouse/.env ...`; no token key and no chat
#      id yet) and the Claude Code login (`run: claude auth login`; the container has never
#      logged in),
#   2. the second run reports zero steps that changed something,
#   3. a snapshot of the machine (dpkg packages, every file under ~/.local, ~/.cache,
#      ~/.claude, ~/.profile and ~/projects, with size and mtime, and the MCP servers in
#      ~/.claude.json) is identical before and after the second run — the one expected
#      difference, the second run's own log file (and so the mtime of the folder that holds
#      the logs), is left out, and so is ~/.local/share/claude/versions, where Claude Code's
#      own updater may add a version between the runs,
#   4. ~/projects/autonomouse holds the four folders the install makes: repos, worktrees,
#      wake-reports and wake-logs,
#   5. the installed command, with no repository checkout in the container, writes the three
#      marked crontab lines with `autonomouse timer install` (a stub `crontab` stands in, so
#      the test reads back what was written without touching a real crontab), and
#   6. the installed package resolves the secrets file to ~/.config/autonomouse/.env,
#   7. ~/.claude/agents/worker-high.md and worker-xhigh.md hold the files setup/agents/ ships,
#   8. both registered MCP servers, started as ~/.claude.json says, open a page in the
#      Chromium Playwright installed (setup/mcp-probe.js drives them), and
#   9. after the open items' fixes — `autonomouse project new` answered by flags (with
#      `--repo-path`, so nothing is cloned; `autonomouse init` first makes the board's git
#      repository, which the cloning form of `project new` makes itself and `--repo-path`
#      does not), the key TELEGRAM_BOT_TOKEN with a placeholder value in
#      ~/.config/autonomouse/.env and a placeholder chat id in config.toml, and cron
#      started — and with a login present, a third run's checklist is all green and
#      changes nothing. The login is
#      ANTHROPIC_API_KEY set to a placeholder: `claude auth status` reads the login state
#      locally, and an API key in the environment is one of the logins it accepts. (No
#      real Claude credential enters the container.)
#  10. `autonomouse run`, on that board with one ready card and with a stub `claude` that
#      records its working folder and prompt, finds its charter inside the installed
#      package, loads ~/.config/autonomouse/.env, and starts claude in the package folder
#      on a prompt that names a charter file that exists, and
#  11. that wake's run record gets end_reason `completed` from the SessionEnd hook that
#      `autonomouse run` registers with claude's --settings (the stub claude runs it the
#      way Claude Code does, at a normal exit), and
#  12. the first run's checklist shows the chromium and chromium runs items [ok].
#
#   setup/test-install.sh                 (needs docker; downloads about 1 GB; 5-10 minutes)
#   setup/test-install.sh --broken-ipv6   (the same, on a network whose IPv6 is broken)
#
# With no option the container has no IPv6 at all, as on Docker's default network. With
# --broken-ipv6 the container sits on a Docker network with IPv6, and its IPv6 default route
# points at a second container that forwards nothing, the way a router that advertises IPv6
# with no working upstream does; IPv4 works. The test also proves the break: curl over IPv6
# to Playwright's download host times out while curl over IPv4 answers.
#
# The container gets GH_TOKEN from `gh auth token` on this machine so the checklist's
# "gh login" line can be green; the token lives only in that container's environment,
# and the container is removed at the end. Evidence (both runs' output, both snapshots,
# their diff) lands in the directory printed at the end.
set -euo pipefail

BROKEN_IPV6=0
case "${1:-}" in
  "") ;;
  --broken-ipv6) BROKEN_IPV6=1 ;;
  *) echo "usage: setup/test-install.sh [--broken-ipv6]" >&2; exit 2 ;;
esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mktemp -d /tmp/test-install.XXXXXX)"
NAME="autonomouse-test-install-$$"

uv build --wheel -q -o "$OUT/dist" "$REPO"
WHEEL="$(ls "$OUT"/dist/*.whl)"

cleanup() {
  docker rm -f "$NAME" >/dev/null
  if [ $BROKEN_IPV6 = 1 ]; then
    docker rm -f "$NAME-router" >/dev/null
    docker network rm "$NAME" >/dev/null
  fi
}
trap cleanup EXIT
if [ $BROKEN_IPV6 = 1 ]; then
  # 2001:db8::/32 is the documentation prefix: a global-scope address that routes nowhere.
  # The router container has IPv6 forwarding off, so it drops every packet not addressed to
  # itself and answers none of them.
  docker network create --ipv6 --subnet 2001:db8:5e1f::/64 "$NAME" >/dev/null
  docker run -d --name "$NAME-router" --network "$NAME" ubuntu:24.04 sleep infinity >/dev/null
  docker run -d --name "$NAME" --network "$NAME" ubuntu:24.04 sleep infinity >/dev/null
  router="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.GlobalIPv6Address}}{{end}}' "$NAME-router")"
  docker run --rm --network "container:$NAME" --cap-add NET_ADMIN alpine:3.20 sh -c \
    "ip -6 route del default && ip -6 route add default via $router dev eth0 && ip -6 route show default"
else
  docker run -d --name "$NAME" ubuntu:24.04 sleep infinity >/dev/null
fi

# A bare Ubuntu machine's user can sudo; the docker image has the user but not sudo.
docker exec "$NAME" bash -c 'apt-get update -qq && apt-get install -y -qq sudo >/dev/null \
  && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/ubuntu && rm -rf /var/lib/apt/lists/*'
docker cp "$REPO/setup/install.sh" "$NAME:/tmp/install.sh"
docker cp "$REPO/setup/mcp-probe.js" "$NAME:/tmp/mcp-probe.js"
docker cp "$WHEEL" "$NAME:/tmp/$(basename "$WHEEL")"
docker exec "$NAME" chmod a+r /tmp/install.sh /tmp/mcp-probe.js "/tmp/$(basename "$WHEEL")"

run_install() {  # n [extra docker exec args]
  local n="$1"; shift
  GH_TOKEN="$(gh auth token)" docker exec -e GH_TOKEN "$@" -u ubuntu -w /home/ubuntu "$NAME" \
    bash /tmp/install.sh --wheel "/tmp/$(basename "$WHEEL")" >"$OUT/run$n.txt" 2>&1 || true
  echo "--- run $n ($OUT/run$n.txt)"; cat "$OUT/run$n.txt"
}

open_items_fail() {  # run file: the failed items are the four a new machine leaves open, each with its fix
  [ "$(grep -F '[FAIL]' "$1" | sed 's/^ *\[FAIL\] *//')" = "$(printf 'cron daemon\nproject\ntelegram\nclaude login')" ] \
    && grep -qF 'fix: run: sudo systemctl enable --now cron' "$1" \
    && grep -qF 'fix: run: autonomouse project new <name>' "$1" \
    && grep -qF 'fix: put TELEGRAM_BOT_TOKEN=<the token from @BotFather> in /home/ubuntu/.config/autonomouse/.env with mode 600, then message the bot once and run: autonomouse notify setup' "$1" \
    && grep -qF 'fix: run: claude auth login' "$1"
}

snapshot() {  # file; Claude Code's own updater may add a version to ~/.local/share/claude/versions between the runs, so that folder is left out
  docker exec -u ubuntu "$NAME" bash -c '
    dpkg-query -W -f="\${Package} \${Version} \${Status}\n" | sort
    find ~/.local ~/.cache ~/.claude ~/.profile ~/projects -printf "%p %s %T@ %l\n" 2>/dev/null \
      | grep -v -e "/.local/state/autonomouse-install/install-.*\.log " \
                -e "/.local/state/autonomouse-install [0-9]" \
                -e "/.local/share/claude/versions" | sort
    jq -S .mcpServers ~/.claude.json' >"$1"
}

run_install 1
snapshot "$OUT/before.txt"
run_install 2
snapshot "$OUT/after.txt"

fail=0
if [ $BROKEN_IPV6 = 1 ]; then  # curl is on the machine now: install.sh installs it first
  docker exec "$NAME" bash -c '
    v6=0; curl -6 -sS -m 15 -o /dev/null https://cdn.playwright.dev/ || v6=$?
    v4=0; curl -4 -sS -m 15 -o /dev/null https://cdn.playwright.dev/ || v4=$?
    echo "curl -6 exit $v6, curl -4 exit $v4"' >"$OUT/ipv6-probe.txt" 2>&1 || true
  if grep -qx 'curl -6 exit 28, curl -4 exit 0' "$OUT/ipv6-probe.txt"; then
    echo "PASS the network is broken as meant: IPv6 connections time out, IPv4 ones answer"
  else
    echo "FAIL the network is not broken as meant: $(tr '\n' ' ' <"$OUT/ipv6-probe.txt")"; fail=1
  fi
fi
for n in 1 2; do
  open_items_fail "$OUT/run$n.txt" \
    && echo "PASS run $n: the only failed items are the cron daemon, the project, telegram and the Claude Code login, each naming its fix" \
    || { echo "FAIL run $n: the failed items are not exactly the cron daemon, the project, telegram and the Claude Code login with their fixes (see $OUT/run$n.txt)"; fail=1; }
done
grep -qx 'Steps that changed something this run: 0.' "$OUT/run2.txt" \
  && echo "PASS second run: zero steps changed" \
  || { echo "FAIL second run: some step changed something (see $OUT/run2.txt)"; fail=1; }
if diff "$OUT/before.txt" "$OUT/after.txt" >"$OUT/snapshot.diff"; then
  echo "PASS machine snapshot identical before and after the second run ($(wc -l <"$OUT/after.txt") entries)"
else
  echo "FAIL the second run changed the machine: $OUT/snapshot.diff"; head -n 20 "$OUT/snapshot.diff"; fail=1
fi
docker exec -u ubuntu "$NAME" bash -c \
  'for d in repos worktrees wake-reports wake-logs; do [ -d ~/projects/autonomouse/$d ] && echo "$d"; done' \
  >"$OUT/folders.txt" 2>&1 || true
if [ "$(cat "$OUT/folders.txt")" = "$(printf 'repos\nworktrees\nwake-reports\nwake-logs')" ]; then
  echo "PASS ~/projects/autonomouse holds repos, worktrees, wake-reports and wake-logs"
else
  echo "FAIL ~/projects/autonomouse lacks a folder the install makes; present: $(tr '\n' ' ' <"$OUT/folders.txt")"; fail=1
fi
# A stub crontab(1): `-l` prints the stored table, `-` stores stdin as the table.
docker exec -u ubuntu -w /home/ubuntu "$NAME" bash -c '
  mkdir -p /tmp/stub
  cat >/tmp/stub/crontab <<"STUB"
#!/bin/sh
case "$1" in
  -l) [ -f /tmp/stub/crontab.txt ] && cat /tmp/stub/crontab.txt || { echo "no crontab for $(id -un)" >&2; exit 1; } ;;
  -) cat >/tmp/stub/crontab.txt ;;
  *) echo "stub crontab: unexpected arguments: $*" >&2; exit 2 ;;
esac
STUB
  chmod +x /tmp/stub/crontab
  PATH=/tmp/stub:$HOME/.local/bin:$PATH autonomouse timer install' >"$OUT/timer.txt" 2>&1 || true
echo "--- timer install ($OUT/timer.txt)"; cat "$OUT/timer.txt"
docker exec -u ubuntu "$NAME" cat /tmp/stub/crontab.txt >"$OUT/crontab.txt" 2>&1 || true
echo "--- the crontab written ($OUT/crontab.txt)"; cat "$OUT/crontab.txt"
lines_ok=1
[ "$(wc -l <"$OUT/crontab.txt")" = 3 ] || lines_ok=0
for job in wake review plan; do
  [ "$(grep -c "# autonomouse $job\$" "$OUT/crontab.txt")" = 1 ] || lines_ok=0
done
if [ $lines_ok = 1 ]; then
  echo "PASS autonomouse timer install wrote the three marked crontab lines from the installed package"
else
  echo "FAIL autonomouse timer install did not write exactly the three marked crontab lines (see $OUT/crontab.txt)"; fail=1
fi
docker exec -u ubuntu -w /home/ubuntu "$NAME" bash -c \
  '"$(~/.local/bin/uv tool dir)"/autonomouse/bin/python -c "from autonomouse.config import secrets_path; print(secrets_path())"' \
  >"$OUT/secrets.txt" 2>&1 || true
if grep -qx '/home/ubuntu/.config/autonomouse/.env' "$OUT/secrets.txt"; then
  echo "PASS the installed package reads its secrets file from ~/.config/autonomouse/.env"
else
  echo "FAIL the installed package's secrets file is not ~/.config/autonomouse/.env: $(cat "$OUT/secrets.txt")"; fail=1
fi
docker exec -u ubuntu -w /home/ubuntu "$NAME" bash -c \
  'cat ~/.claude/agents/worker-high.md; echo ====; cat ~/.claude/agents/worker-xhigh.md' >"$OUT/agents.txt" 2>&1 || true
if [ "$(cat "$OUT/agents.txt")" = "$(cat "$REPO/setup/agents/worker-high.md"; echo ====; cat "$REPO/setup/agents/worker-xhigh.md")" ]; then
  echo "PASS ~/.claude/agents holds worker-high.md and worker-xhigh.md as setup/agents/ ships them"
else
  echo "FAIL the worker agents are not in place: $(head -c 300 "$OUT/agents.txt" | tr '\n' ' ')"; fail=1
fi
for probe in "playwright browser_navigate" "chrome-devtools new_page"; do
  read -r server tool <<<"$probe"
  docker exec -u ubuntu -w /tmp "$NAME" /home/ubuntu/.local/bin/node /tmp/mcp-probe.js "$server" "$tool" \
    "{\"url\": \"data:text/html,<title>probe-$server</title>hello\"}" >"$OUT/mcp-$server.txt" 2>&1 \
    && grep -qF "probe-$server" "$OUT/mcp-$server.txt" \
    && echo "PASS the $server MCP server, started as ~/.claude.json registers it, opened a page in Chromium" \
    || { echo "FAIL the $server MCP server did not open a page: $(head -c 400 "$OUT/mcp-$server.txt")"; fail=1; }
done
# The project item's fix, as the checklist names it, answered by flags. `--repo-path` records
# the checkout in the container in place of a clone from GitHub; the cloning form of
# `project new` makes the board's git repository itself and `--repo-path` does not
# (tests/test_project_wizard.py), so `autonomouse init` makes it first. `--default-branch`
# names the branch that the checkout, which has no origin, cannot tell.
docker exec -u ubuntu -w /home/ubuntu "$NAME" bash -c '
  git init -q /tmp/project
  ~/.local/bin/autonomouse init \
    && ~/.local/bin/autonomouse project new Demo --slug demo --goal g \
    --repo https://github.com/t/demo --repo-path /tmp/project --default-branch main --priority 0' >"$OUT/project-new.txt" 2>&1 || true
echo "--- autonomouse project new ($OUT/project-new.txt)"; cat "$OUT/project-new.txt"
# The telegram item's fix with placeholders: the key TELEGRAM_BOT_TOKEN in the secrets file
# (mode 600) and notify.telegram_chat_id in config.toml, written as `autonomouse notify
# setup` writes it; no real token enters the container. Then the cron daemon item's fix: the
# container has no init, so the test starts the daemon itself.
docker exec -u ubuntu -w /home/ubuntu "$NAME" bash -c '
  mkdir -p ~/.config/autonomouse
  install -m 600 /dev/null ~/.config/autonomouse/.env
  echo "TELEGRAM_BOT_TOKEN=placeholder" >>~/.config/autonomouse/.env
  "$(~/.local/bin/uv tool dir)"/autonomouse/bin/python -c "from autonomouse.config import set_config_section, config_path; set_config_section(config_path(), \"notify\", {\"telegram_chat_id\": \"placeholder\"})"
  sudo cron
  pgrep -x cron >/dev/null && echo "cron daemon: running"' >"$OUT/fixes.txt" 2>&1 || true
echo "--- telegram placeholders and the cron daemon ($OUT/fixes.txt)"; cat "$OUT/fixes.txt"
run_install 3 -e ANTHROPIC_API_KEY=placeholder
grep -qx 'All green. Steps that changed something this run: 0.' "$OUT/run3.txt" \
  && echo "PASS logged-in run: checklist all green, zero steps changed" \
  || { echo "FAIL logged-in run: not all green with zero steps changed (see $OUT/run3.txt)"; fail=1; }
docker exec -i -u ubuntu -w /home/ubuntu "$NAME" bash -s >"$OUT/wake.txt" 2>&1 <<'WAKE' || true
set -e
export PATH=/tmp/stub:$HOME/.local/bin:$PATH
git config --global user.name test && git config --global user.email test@example.com
mkdir -p ~/.config/autonomouse /tmp/stub
install -m 600 /dev/null ~/.config/autonomouse/.env
autonomouse card new --id demo-a1 --project demo --title t --goal g --test t >/dev/null
autonomouse board commit -m seed >/dev/null
# The stub claude exits normally, and first does what Claude Code does at a session's end:
# it runs each SessionEnd hook its --settings names, in a shell, with the hook's JSON on
# stdin, over a transcript whose last turn ended cleanly.
cat >/tmp/stub/claude <<'STUB'
#!/bin/sh
pwd >/tmp/stub/cwd
printf "%s" "$2" >/tmp/stub/prompt
settings=
while [ $# -gt 0 ]; do [ "$1" = --settings ] && settings="$2"; shift; done
echo '{"type":"assistant","message":{"stop_reason":"end_turn"}}' >/tmp/stub/transcript.jsonl
echo '{"type":"result","subtype":"success","is_error":false,"result":"stub"}'
printf '%s' "$settings" | jq -r '.hooks.SessionEnd[].hooks[].command' | while read -r hook; do
  echo '{"hook_event_name":"SessionEnd","reason":"other","transcript_path":"/tmp/stub/transcript.jsonl"}' \
    | sh -c "$hook"
done
STUB
chmod +x /tmp/stub/claude
autonomouse run --project demo || true
for run in ~/.local/share/autonomouse/board/runs/*.json; do
  echo "end_reason=$(jq -r .end_reason "$run")"
done
charter="$(sed -n 's/^Read \([^ ]*\) and run one wake\..*/\1/p' /tmp/stub/prompt)"
echo "claude_cwd=$(cat /tmp/stub/cwd)"
echo "package=$("$(uv tool dir)"/autonomouse/bin/python -c 'import autonomouse, os; print(os.path.dirname(autonomouse.__file__))')"
echo "charter=$charter ($(test -f "$charter" && echo present || echo missing))"
WAKE
echo "--- autonomouse run with a stub claude ($OUT/wake.txt)"; cat "$OUT/wake.txt"
package="$(sed -n 's/^package=//p' "$OUT/wake.txt")"
if grep -qx 'secrets=/home/ubuntu/.config/autonomouse/.env (loaded)' "$OUT/wake.txt" \
  && grep -qx "claude_cwd=$package" "$OUT/wake.txt" \
  && grep -qx "charter=$package/docs/orchestrator.md (present)" "$OUT/wake.txt"; then
  echo "PASS autonomouse run started claude in the installed package on its charter, with the secrets file loaded"
else
  echo "FAIL autonomouse run from the installed package did not start claude on its charter with the secrets file loaded (see $OUT/wake.txt)"; fail=1
fi
if [ "$(grep '^end_reason=' "$OUT/wake.txt")" = end_reason=completed ]; then
  echo "PASS the SessionEnd hook autonomouse run registers left end_reason completed on the run record"
else
  echo "FAIL the run record's end_reason is not completed: $(grep '^end_reason=' "$OUT/wake.txt" | tr '\n' ' ')"; fail=1
fi
for item in "chromium" "chromium runs"; do
  grep -qx "  \[ok\]   $item" "$OUT/run1.txt" \
    && echo "PASS the first run's checklist shows $item [ok]" \
    || { echo "FAIL the first run's checklist does not show $item [ok] (see $OUT/run1.txt)"; fail=1; }
done
retried="$(docker exec -u ubuntu "$NAME" bash -c 'cat ~/.local/state/autonomouse-install/install-*.log' \
  | grep -c 'trying again with IPv4 only' || true)"
echo "note: install.sh retried the Chromium download over IPv4 only $retried time(s)"
echo "evidence: $OUT"
exit $fail
