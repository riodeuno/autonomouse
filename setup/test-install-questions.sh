#!/usr/bin/env bash
# test-install-questions.sh — the acceptance test of install.sh's setup questions (sys-w2,
# sys-w21):
# in a clean ubuntu:24.04 container, as a sudo user, it runs the install command the way the
# README gives it, `curl ... | bash` (here `cat install.sh | bash`), and passes only when
#   1. with no terminal, the install asks nothing and says so;
#   2. on a terminal, with Claude Code logged out, it asks nothing and stops naming
#      `claude auth login`;
#   3. logged in, it asks the machine's questions (GitHub token, Telegram bot token,
#      browser pool, first wake) with the real gum on the terminal while bash reads the
#      script from the pipe, and a refused GitHub token and a Telegram token that Telegram
#      does not know are each asked again; after the first wake, last,
#      `autonomouse project new` asks the project's questions with the real gum on the same
#      terminal (name, repository URL, commit name and email, branch, Linear link; no
#      goal and no planning-now question); afterwards
#      ~/.config/autonomouse/.env has mode 600 and holds the token, config.toml holds the
#      chat id, browser.pool and wake.start, the project is on the board with the answered
#      branch and its clone commits with the answered name and email, the live setup
#      session ran on the terminal in the clone with the model claude-fable-5-1 as the
#      install's last step, and the crontab's wake line starts at the chosen time,
#      tomorrow 09:00;
#   4. the repository, which held branches, a tag and a setting, has every one of them as
#      before;
#   5. a second run asks no question and changes nothing;
#   6. with an empty repository, every default of project new kept with Enter, a blank
#      browser pool and the first wake in 2 hours, the repository gets one first
#      commit on main and nothing else, browser.pool stays unset, the live setup session
#      starts in the new clone and, left before the brief, hands planning to Telegram, and
#      the wake line starts 2 hours on.
#
#   setup/test-install-questions.sh   (needs docker; downloads about 1 GB; 5-10 minutes)
#
# What stands in for the outside world, inside the container only: `gh` and `claude` are
# tests/fixtures/install's fakes, placed ahead of the real ones, which answer only `--version`
# (and `claude mcp`): gh's in ~/.local/bin, claude's in ~/.local/share/claude/versions behind
# the link ~/.local/bin/claude, where install.sh looks for the official install; GitHub is
# bare repositories under /tmp/git that a global git config rewrites https://github.com/ to;
# the Telegram Bot API is tests/install_stubs.py's stub, which AUTONOMOUSE_TELEGRAM_API
# points autonomouse at; the answers are typed on a pseudo-terminal by the same file's driver. No token of any kind
# enters the container. Evidence (every run's transcript and the checks' inputs) lands in
# the directory printed at the end; the container is removed at the end.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$(mktemp -d /tmp/test-install-questions.XXXXXX)"
NAME="autonomouse-test-install-questions-$$"
BOT_TOKEN="123456:fake-bot-token"

uv build --wheel -q -o "$OUT/dist" "$REPO"
WHEEL="/tmp/t/$(basename "$(ls "$OUT"/dist/*.whl)")"

trap 'docker rm -f "$NAME" >/dev/null' EXIT
docker run -d --name "$NAME" ubuntu:24.04 sleep infinity >/dev/null
docker exec "$NAME" bash -c 'apt-get update -qq && apt-get install -y -qq sudo >/dev/null \
  && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/ubuntu && rm -rf /var/lib/apt/lists/*'
docker exec "$NAME" mkdir -p /tmp/t
docker cp "$REPO/setup/install.sh" "$NAME:/tmp/t/install.sh"
docker cp "$REPO/tests/install_stubs.py" "$NAME:/tmp/t/install_stubs.py"
docker cp "$REPO/tests/fixtures/install/gh" "$NAME:/tmp/t/gh"
docker cp "$REPO/tests/fixtures/install/claude" "$NAME:/tmp/t/claude"
docker cp "$OUT/dist/$(basename "$WHEEL")" "$NAME:$WHEEL"
docker exec "$NAME" chown -R ubuntu:ubuntu /tmp/t

as_ubuntu() {  # [-e VAR=value]... command...: in the container as ubuntu, in its home
  local env=(-e FAKE_DIR=/tmp/t/fake -e FAKE_GH_REAL=/usr/bin/gh
    -e FAKE_CLAUDE_REAL=/home/ubuntu/.local/bin/claude.real)
  while [ "${1:-}" = -e ]; do env+=(-e "$2"); shift 2; done
  docker exec -u ubuntu -w /home/ubuntu "${env[@]}" "$NAME" "$@"
}
INSTALL='cat /tmp/t/install.sh | bash -s -- --wheel '"$WHEEL"

drive() {  # name steps-json [-e VAR=value]...: the install on a terminal, answered
  local name="$1" steps="$2"; shift 2
  local rc=0
  as_ubuntu "$@" /tmp/t/python /tmp/t/install_stubs.py drive "$steps" "/tmp/t/$name.txt" \
    -- bash -c "$INSTALL" >"$OUT/$name.driver.txt" 2>&1 || rc=$?
  docker cp "$NAME:/tmp/t/$name.txt" "$OUT/$name.txt" 2>/dev/null || true
  sed 's/\x1b\[[0-9;?<>=$]*[A-Za-z]//g; s/\x1b\][^\x1b]*\x1b\\//g; s/\r$//' "$OUT/$name.txt" >"$OUT/$name.plain.txt" 2>/dev/null || true
  echo "--- $name: exit $rc ($OUT/$name.plain.txt)"; tail -n 40 "$OUT/$name.plain.txt" 2>/dev/null || true
  cat "$OUT/$name.driver.txt"
  return $rc
}

fail=0
pass() { echo "PASS $*"; }
flunk() { echo "FAIL $*"; fail=1; }

# 1. The first run installs every tool; with no terminal it asks nothing.
as_ubuntu bash -c "$INSTALL" >"$OUT/no-terminal.txt" 2>&1 || true
echo "--- no terminal ($OUT/no-terminal.txt)"; tail -n 30 "$OUT/no-terminal.txt"
grep -qx 'Setup questions: not asked, with no terminal to ask on; the checklist names each open item'"'"'s fix.' "$OUT/no-terminal.txt" \
  && pass "with no terminal the install asks nothing and says so" \
  || flunk "the install with no terminal did not say it asks nothing (see $OUT/no-terminal.txt)"

# The stand-ins: the fakes ahead of the real gh (/usr/bin/gh) and claude; the tool's python
# for the driver and the stub; GitHub's two repositories; the Telegram stub.
as_ubuntu bash -c '
  set -e
  mkdir -p /tmp/t/fake
  mv ~/.local/bin/claude ~/.local/bin/claude.real
  install -m 755 /tmp/t/claude ~/.local/share/claude/versions/fake
  ln -s "$HOME/.local/share/claude/versions/fake" ~/.local/bin/claude
  install -m 755 /tmp/t/gh ~/.local/bin/gh
  ln -s "$(~/.local/bin/uv tool dir)/autonomouse/bin/python" /tmp/t/python
  id=(-c user.name=t -c user.email=t@example.com)
  git init -q -b main /tmp/t/source
  echo widgets >/tmp/t/source/README.md
  git -C /tmp/t/source add README.md
  git -C /tmp/t/source "${id[@]}" commit -q -m first
  git -C /tmp/t/source branch feature
  git -C /tmp/t/source "${id[@]}" tag -a v1 -m v1
  git init -q --bare -b main /tmp/git/owner/full
  git -C /tmp/git/owner/full config receive.denyDeletes false
  git -C /tmp/t/source push -q /tmp/git/owner/full main feature v1
  git init -q --bare -b main /tmp/git/owner/empty
  git config --global url.file:///tmp/git/.insteadOf https://github.com/
' >"$OUT/stand-ins.txt" 2>&1 || { cat "$OUT/stand-ins.txt"; flunk "the stand-ins could not be set up"; exit 1; }
docker exec -d -u ubuntu "$NAME" /tmp/t/python /tmp/t/install_stubs.py telegram "$BOT_TOKEN" 1 \
  /tmp/t/telegram.port /tmp/t/telegram.log
for _ in $(seq 50); do docker exec "$NAME" test -s /tmp/t/telegram.port && break; sleep 0.2; done
TELEGRAM=(-e "AUTONOMOUSE_TELEGRAM_API=http://127.0.0.1:$(docker exec "$NAME" cat /tmp/t/telegram.port)")

snapshot() {  # bare repository: every ref and what it points at, HEAD, the config
  as_ubuntu bash -c 'git -C "$1" for-each-ref --format="%(refname) %(objectname)"; cat "$1/HEAD" "$1/config"' _ "$1"
}
snapshot /tmp/git/owner/full >"$OUT/full-before.txt"

# 2. Logged out of Claude Code: nothing is asked.
drive logged-out '[]' "${TELEGRAM[@]}" && rc=0 || rc=$?
if [ "$rc" = 1 ] && grep -qx '  claude auth login' "$OUT/logged-out.plain.txt" \
  && ! grep -q 'GitHub personal access token' "$OUT/logged-out.plain.txt"; then
  pass "logged out of Claude Code, the install asks nothing and stops naming claude auth login"
else
  flunk "logged out of Claude Code, the install did not stop before the questions naming claude auth login (exit $rc)"
fi

# 3. Logged in: the machine's questions from the installer, the project's from project new.
as_ubuntu touch /tmp/t/fake/claude-logged-in
tomorrow="$(as_ubuntu date -d tomorrow +%Y-%m-%d)"
drive questions '[
  ["GitHub personal access token for gh", "ghp_refused\r"],
  ["GitHub personal access token for gh", "ghp_fake_token\r"],
  ["Telegram bot token, from @BotFather", "999:not-this-bot\r"],
  ["Telegram bot token, from @BotFather", "'"$BOT_TOKEN"'\r"],
  ["The browser pool as user@host", "deploy@192.0.2.14\r"],
  ["When does the first wake run", "\u0015tomorrow 09:00\r"],
  ["Project name, as the board shows it", "Widget Works\r"],
  ["GitHub repository URL, https", "https://github.com/owner/full\r"],
  ["Name the agents commit with", "Widget Bot\r"],
  ["Email the agents commit with", "bot@widgets.example\r"],
  ["Branch the agents. pull requests go into", "feature\r"],
  ["Linear project or issue URL", "https://linear.app/acme/project/widgets-5b1a2c3d4e5f\r"]
]' "${TELEGRAM[@]}" && rc=0 || rc=$?
as_ubuntu bash -c '
  echo "exit: '"$rc"'"
  echo "env mode: $(stat -c %a ~/.config/autonomouse/.env)"
  echo "env keys: $(sed -n "s/=.*//p" ~/.config/autonomouse/.env | tr "\n" " ")"
  grep -qx "TELEGRAM_BOT_TOKEN='"$BOT_TOKEN"'" ~/.config/autonomouse/.env && echo "env token: the one Telegram knows"
  echo "gh token: $(cat /tmp/t/fake/gh-token)"
  ~/.local/bin/autonomouse config show | grep -E "^(notify.telegram_chat_id|browser.pool|wake.start) = "
  ~/.local/bin/autonomouse project list --json | jq -r ".[] | \"project: \(.slug) \(.repo) \(.default_branch)\""
  ~/.local/bin/autonomouse project list --json | jq -r ".[] | \"tracker: \(.tracker.kind) \(.tracker.link) \(.tracker.url)\""
  clone="$(~/.local/bin/autonomouse project list --json | jq -r ".[0].repo_path")"
  echo "commits as: $(git -C "$clone" config --local user.name) <$(git -C "$clone" config --local user.email)>"
  cat /tmp/t/fake/claude-live
  [ "$(cat /tmp/t/fake/claude-live.cwd)" = "$clone" ] && echo "live session: in the clone"
  tr "\0" "\n" </tmp/t/fake/claude-live.argv | grep -qx claude-fable-5-1 && echo "live model: claude-fable-5-1"
  echo "telegram: $(tr "\n" "," </tmp/t/telegram.log)"
  crontab -l | grep "# autonomouse wake$"
' >"$OUT/after-questions.txt" 2>&1 || true
echo "--- after the questions ($OUT/after-questions.txt)"; cat "$OUT/after-questions.txt"
questions_ok=1
for line in "env mode: 600" "env keys: TELEGRAM_BOT_TOKEN " "env token: the one Telegram knows" \
  "gh token: ghp_fake_token" "notify.telegram_chat_id = 424242" "browser.pool = deploy@192.0.2.14" \
  "wake.start = ${tomorrow}T09:00+00:00" "project: widget-works https://github.com/owner/full feature" \
  "tracker: linear project https://linear.app/acme/project/widgets-5b1a2c3d4e5f" \
  "commits as: Widget Bot <bot@widgets.example>" \
  "stdin: terminal" "live session: in the clone" "live model: claude-fable-5-1" \
  "telegram: getUpdates read token=wrong,getUpdates read token=ok,getUpdates read token=ok,getUpdates confirm token=ok,"; do
  grep -qxF -- "$line" "$OUT/after-questions.txt" || { echo "missing: $line"; questions_ok=0; }
done
grep -q "^0 1,5,9,13,17,21 \* \* \* .*-ge ${tomorrow//-/}0900 \] && " "$OUT/after-questions.txt" \
  || { echo "missing: a wake line at 0 1,5,9,13,17,21 guarded from ${tomorrow//-/}0900"; questions_ok=0; }
grep -q '^argv: --session-id ' "$OUT/after-questions.txt" || { echo "missing: the live setup session, on a new session"; questions_ok=0; }
# project new asked its questions in order, after the browser pool's and the first
# wake's, and ended in the live session, the install's last step; the installer asked none
# of the two it asked before sys-w21.
at=0
for q in "The browser pool as user@host" "When does the first wake run" \
  "Project name, as the board shows it" "GitHub repository URL, https" \
  "Name the agents commit with [Owner]" "Email the agents commit with [7+owner@users.noreply.github.com]" \
  "Branch the agents' pull requests go into [main]" \
  "Linear project or issue URL for this project (blank for none)" \
  "planning live with claude-fable-5-1"; do
  line="$(grep -nF -- "$q" "$OUT/questions.plain.txt" | head -n 1 | cut -d: -f1)"
  if [ -z "$line" ] || [ "$line" -lt "$at" ]; then echo "missing or out of order: $q"; questions_ok=0; else at="$line"; fi
done
grep -qE "The GitHub repository autonomouse works on|The first project.s name" "$OUT/questions.plain.txt" \
  && { echo "the installer asked a project question itself"; questions_ok=0; }
grep -qE "Project goal|Start planning now" "$OUT/questions.plain.txt" \
  && { echo "project new asked the goal or whether to plan now"; questions_ok=0; }
[ "$(grep -F '[FAIL]' "$OUT/questions.plain.txt" | sed 's/^ *\[FAIL\] *//')" = "cron daemon" ] \
  || { echo "the checklist's failed items are not only the cron daemon, which no init starts here"; questions_ok=0; }
[ "$questions_ok" = 1 ] \
  && pass "the machine's questions and project new's, answered on the terminal, left the token, the chat id, browser.pool, the project with its branch and commit identity, the live session and the wake line from tomorrow 09:00" \
  || flunk "the questions did not leave everything in place (see $OUT/after-questions.txt and $OUT/questions.plain.txt)"

# 4. The repository that held code.
snapshot /tmp/git/owner/full >"$OUT/full-after.txt"
diff "$OUT/full-before.txt" "$OUT/full-after.txt" >"$OUT/full.diff" \
  && pass "the repository's branches, tag, HEAD and settings are as they were ($(wc -l <"$OUT/full-before.txt") lines)" \
  || flunk "the install changed the repository: $OUT/full.diff"

# 5. A second run asks nothing and changes nothing.
as_ubuntu bash -c 'set -e; crontab -l; ~/.local/bin/autonomouse config show; sed "s/=.*//" ~/.config/autonomouse/.env' \
  >"$OUT/state-before-rerun.txt" 2>&1 || flunk "could not read the crontab, the config or the secrets' keys before the second run"
drive rerun '[]' "${TELEGRAM[@]}" && rc=0 || rc=$?
as_ubuntu bash -c 'set -e; crontab -l; ~/.local/bin/autonomouse config show; sed "s/=.*//" ~/.config/autonomouse/.env' \
  >"$OUT/state-after-rerun.txt" 2>&1 || flunk "could not read the crontab, the config or the secrets' keys after the second run"
rerun_ok=1
for line in "1. GitHub token: gh is already logged in" "   Telegram chat: already saved" \
  "3. browser pool: already answered" "5. project: a project is already on the board" \
  "Steps that changed something this run: 0."; do
  grep -qxF -- "$line" "$OUT/rerun.plain.txt" || { echo "missing: $line"; rerun_ok=0; }
done
grep -q '^4\. first wake: already set' "$OUT/rerun.plain.txt" || { echo "missing: 4. first wake: already set"; rerun_ok=0; }
grep -qE 'GitHub personal access token for gh|Telegram bot token, from|The browser pool as user@host|Project name, as the board|GitHub repository URL, https|Name the agents commit with|Email the agents commit with|Branch the agents. pull requests|Linear project or issue URL|When does the first wake run' "$OUT/rerun.plain.txt" \
  && { echo "a question was asked"; rerun_ok=0; }
diff "$OUT/state-before-rerun.txt" "$OUT/state-after-rerun.txt" >"$OUT/rerun.diff" || { echo "the crontab, the config or the secrets' keys changed: $OUT/rerun.diff"; rerun_ok=0; }
[ "$rerun_ok" = 1 ] \
  && pass "a second run asks no question and changes nothing (exit $rc; the cron daemon is the one open item)" \
  || flunk "the second run asked a question or changed something (see $OUT/rerun.plain.txt)"

# 6. An empty repository, on a board, config and state of its own; gh stays logged in.
as_ubuntu rm -f /home/ubuntu/.local/state/autonomouse-install/answered
EMPTY=(-e AUTONOMOUSE_HOME=/tmp/b/board -e AUTONOMOUSE_CONFIG=/tmp/b/config -e AUTONOMOUSE_REPOS=/tmp/b/repos
  -e AUTONOMOUSE_WORKTREES=/tmp/b/worktrees -e AUTONOMOUSE_STATE=/tmp/b/state)
as_ubuntu mv /tmp/t/fake/claude-live /tmp/t/claude-live.questions
before="$(as_ubuntu date +%s)"
drive empty '[
  ["Telegram bot token, from @BotFather", "'"$BOT_TOKEN"'\r"],
  ["The browser pool as user@host", "\r"],
  ["When does the first wake run", "\u0015in 2 hours\r"],
  ["Project name, as the board shows it", "Empty One\r"],
  ["GitHub repository URL, https", "https://github.com/owner/empty\r"],
  ["Name the agents commit with", "\r"],
  ["Email the agents commit with", "\r"],
  ["Branch the agents. pull requests go into", "\r"],
  ["Linear project or issue URL", "\r"]
]' "${TELEGRAM[@]}" "${EMPTY[@]}" && rc=0 || rc=$?
as_ubuntu "${EMPTY[@]}" bash -c '
  git -C /tmp/git/owner/empty for-each-ref --format="ref: %(refname)"
  echo "log: $(git -C /tmp/git/owner/empty log --format=%s main | tr "\n" ",")"
  echo "files: $(git -C /tmp/git/owner/empty ls-tree main | wc -l)"
  ~/.local/bin/autonomouse project list --json | jq -r ".[] | \"project: \(.slug) \(.repo) \(.default_branch)\""
  echo "browser.pool: [$(~/.local/bin/autonomouse config show | sed -n "s/^browser.pool = //p")]"
  echo "start: $(date -d "$(~/.local/bin/autonomouse config show | sed -n "s/^wake.start = //p")" +%s)"
  echo "start fields: $(date -d "$(~/.local/bin/autonomouse config show | sed -n "s/^wake.start = //p")" "+%-M %Y%m%d%H%M")"
  echo "answered: $(tr "\n" " " </home/ubuntu/.local/state/autonomouse-install/answered)"
  [ "$(cat /tmp/t/fake/claude-live.cwd)" = "/tmp/b/repos/empty-one" ] && echo "live session: in the clone" || echo "live session: none"
  crontab -l | grep "# autonomouse wake$"
' >"$OUT/after-empty.txt" 2>&1 || true
echo "--- after the empty repository's questions ($OUT/after-empty.txt)"; cat "$OUT/after-empty.txt"
empty_ok=1
for line in "ref: refs/heads/main" "log: Initial commit," "files: 0" \
  "project: empty-one https://github.com/owner/empty main" "browser.pool: []" \
  "answered: browser-pool " "live session: in the clone"; do
  grep -qxF -- "$line" "$OUT/after-empty.txt" || { echo "missing: $line"; empty_ok=0; }
done
[ "$(grep -c '^ref: ' "$OUT/after-empty.txt")" = 1 ] || { echo "the empty repository has more than one ref"; empty_ok=0; }
start="$(sed -n 's/^start: //p' "$OUT/after-empty.txt")"
[ -n "$start" ] && [ "$start" -ge $((before + 7200 - 60)) ] && [ "$start" -le $((before + 7200 + 180)) ] \
  || { echo "wake.start is not 2 hours on: $start, from $before"; empty_ok=0; }
read -r minute stamp <<<"$(sed -n 's/^start fields: //p' "$OUT/after-empty.txt")"
grep -q "^$minute [0-9,]* \* \* \* .*-ge $stamp \] && " "$OUT/after-empty.txt" \
  || { echo "the wake line does not start at wake.start"; empty_ok=0; }
grep -qF "before the brief was written; the planner asks the rest on Telegram (planning thread $(as_ubuntu "${EMPTY[@]}" /home/ubuntu/.local/bin/autonomouse plan list --json | jq -r '.[0].id'))" "$OUT/empty.plain.txt" \
  || { echo "missing: the session left before the brief, and the planner asks the rest on Telegram"; empty_ok=0; }
[ "$empty_ok" = 1 ] \
  && pass "an empty repository, with project new's defaults, got one first commit on main and nothing else; a blank browser pool left browser.pool unset; the live setup session started in the new clone; the wake line starts 2 hours on" \
  || flunk "the empty repository's run did not end as it should (see $OUT/after-empty.txt and $OUT/empty.plain.txt)"

echo "evidence: $OUT"
exit $fail
