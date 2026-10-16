# Set up a new machine

This runbook takes a fresh Ubuntu 24.04 machine to a working autonomouse. Follow the
sections in this order:

1. [What the machine needs](#what-the-machine-needs)
2. [Install](#install)
3. [Log in to Claude and GitHub](#log-in-to-claude-and-github)
4. [Telegram](#telegram)
5. [First board](first-project.md#first-board): create a project from its GitHub URL
6. [The timer](running.md#the-timer)
7. [Open the board](running.md#open-the-board), on the machine or over ssh
8. [Check that it works](#check-that-it-works)
9. [Where files live](running.md#where-files-live): the logs and the state
10. [Pause and resume](running.md#pause-and-resume)

[How the board works](how-it-works.md) explains the board, and [Commands](commands.md) lists every command.

## What the machine needs

- Ubuntu 24.04 on x86_64 or arm64 (aarch64). `install.sh` picks the pins for the machine's
  processor from `setup/tools.txt`, which holds both builds of every download, and stops on
  any other processor.
- A user who can run `sudo`. Install and run autonomouse as that user.
- `curl`, to download the tools. When `curl --version` fails, run
  `sudo apt-get install -y curl` first.
- A checkout of this repository, and [uv](https://docs.astral.sh/uv/) to build the autonomouse
  wheel from it (the installer installs uv too, but the wheel comes first).
- HTTPS access to `github.com` (uv and gh), `nodejs.org`, `registry.npmjs.org`, the Ubuntu
  archive, Playwright's browser download, and `claude.ai` and `downloads.claude.ai` (the
  Claude Code installer and its releases).
- The accounts autonomouse works through: a Claude account, a GitHub account that can push
  to the repository autonomouse will work on, and a Telegram account. A Typesafe API key is
  needed only to turn on Jev (see [Configuration and secrets](config.md)).

## Install

Run these commands in the checkout, as the user who will run autonomouse. `uv build --wheel`
writes the wheel into `dist/`:

```
uv build --wheel
bash setup/install.sh --wheel dist/autonomouse-<version>-py3-none-any.whl
```

The script calls `sudo` only for `apt`, so it may ask for your password. Every tool it
downloads is pinned to one version and one checksum in `setup/tools.txt`, and the script
checks the checksum before it installs anything. Claude Code is the one exception: it comes
from Anthropic's official installer, at the latest version, and then updates itself. It
installs, in this order:

| step | what it installs |
|---|---|
| base packages | `ca-certificates`, `curl` and `xz-utils`, when they are missing |
| apt tools | git, fzf, ripgrep, fd, jq, tmux and cron (which fires the schedule, see [The timer](running.md#the-timer)) |
| gh, uv, gum, node | gh from its GitHub release; uv, which brings Python 3.13; gum, which asks the setup questions below; node, under `~/.local` |
| Claude Code | Claude Code from Anthropic's official installer, at the latest version. It links `~/.local/bin/claude` into `~/.local/share/claude/versions` and updates itself. A machine that has the old npm copy loses it first, so only the official one is left |
| a browser | Playwright with Chromium and the system libraries Chromium needs, and the Playwright and Chrome DevTools MCP servers |
| worker agents | `~/.claude/agents/worker-high.md` and `worker-xhigh.md`, the agent types the orchestrator starts its workers as. The installer writes a file from `setup/agents/` only when it is missing, so a global Claude Code setup that has its own keeps it |
| MCP servers | registers the two MCP servers for every Claude Code session, in `~/.claude.json` |
| autonomouse | the `autonomouse` command, in `~/.local/bin`, from the wheel you pass with `--wheel` (or the one a copy of the script served by `setup/publish.sh` pins) |
| folders | `~/projects/autonomouse` with its four folders, `repos`, `worktrees`, `wake-reports` and `wake-logs` (see [Where files live](running.md#where-files-live)) |
| PATH | a line in `~/.profile`, so a new login shell finds `~/.local/bin` |

Running it again changes nothing that is already in place, except that it installs autonomouse
again when the wheel changed. `autonomouse --version` prints the version you have.

After the steps, the script asks its setup questions on the terminal, one at a time, each
with gum. It asks the questions that belong to the machine itself; the questions about your
project come from `autonomouse project new`, which the script runs on the same terminal.
First it checks that Claude Code is logged in; when it is not, it stops and names `claude
auth login`: run that, then run the install command again. Then, in order:

1. the GitHub token: a personal access token it hands to `gh auth login`, asked again when
   gh refuses it. Skipped when gh is already logged in;
2. the Telegram bot token, from @BotFather. It writes the token to
   `~/.config/autonomouse/.env` (mode 600), then waits until you send the bot a message and
   saves your chat id, as `autonomouse notify setup` does (see [Telegram](#telegram));
3. optional: the browser pool, as `user@host` (`browser.pool`, `docs/config.md`). A pool is a
   machine that keeps logged-in Chromes, which the wakes borrow one per card in place of a
   local browser. [browsermaze](https://github.com/riodeuno/browsermaze) builds one; set it
   up there first. This machine reaches the pool with `ssh user@host browsermaze ...`, so
   that command must log in without a password prompt, and `browser.slots` (default 4)
   must match the pool's slot count. A blank answer leaves `browser.pool` unset, and each
   wake uses a local browser;
4. when the first wake runs: `now`, `in 2 hours`, `tomorrow 09:00`, or any time `date -d`
   reads. From then on a wake runs every `wake.interval_hours` from that time (see
   [The timer](running.md#the-timer));
5. last, your first project. The script runs `autonomouse project new`, which asks the
   same questions it asks for every project you add later (see [First board](first-project.md#first-board)):
   the project's name; the repository URL, `https://github.com/<owner>/<name>`, of a
   repository that may be empty or already hold code; the name and email the agents commit
   with; the branch their pull requests go into; and the Linear project or issue the project
   is linked to, blank for none. While it checks GitHub access, it prints each step it
   takes. It asks no goal: it ends in a live Claude session in the project's repository
   folder, where you tell Claude what the project is for, and it writes the project's goal
   and brief when you agree. That session is the install's last step. Skipped when a
   project is already on the board.

The questions read the terminal and never the pipe, so they work under `curl ... | bash`;
`autonomouse project new` also asks its questions on the terminal, with gum. Each question
is skipped when its answer is already in place, so a second run asks nothing already
answered.

First open a new login shell, or run `export PATH="$HOME/.local/bin:$PATH"`, so the shell
finds the new commands.

The script ends with a checklist: one line per item, `[ok]` or `[FAIL]`. Each failed item
names the command that fixes it. The full log is in
`~/.local/state/autonomouse-install/install-<UTC time>.log`. With the questions answered,
every item is `[ok]`. With no terminal to ask on, such as a run from a script, the script
asks nothing and says so, and four items stay open until you act. The next sections close
them by hand, in this order:

1. `claude login`: run `claude auth login` (see
   [Log in to Claude and GitHub](#log-in-to-claude-and-github)).
2. `gh login`: run `gh auth login` (same section).
3. `telegram`: put `TELEGRAM_BOT_TOKEN=<the token from @BotFather>` in
   `~/.config/autonomouse/.env` with mode 600, then message the bot once and run
   `autonomouse notify setup` (see [Telegram](#telegram)).
4. `project`: run `autonomouse project new <name>` (see [First board](first-project.md#first-board)). It
   asks the GitHub URL, logs gh in when needed, clones the repository and installs the
   schedule.

Then run the install command again; every item is `[ok]`. The `cron daemon` item is open
only when cron is not running; it names the command that starts it. When a step fails, the
script asks no question until a run where every step succeeds.

### Host your own install command

`setup/publish.sh` serves your own install command. It builds the wheel from `HEAD`, pins it
in a copy of `install.sh`, and copies the pair to a host you name, so other machines can
install with one `curl` line. It names no host of its own: you pass the URL the folder is
served at, and for a remote host the ssh login and the folder.

```
setup/publish.sh --url https://setup.example.com/autonomouse        # dry run
setup/publish.sh --url https://setup.example.com/autonomouse --apply \
  --host deploy@setup.example.com --remote-dir /srv/autonomouse [--key ~/.ssh/deploy_key]
setup/publish.sh --apply --dest ./served                            # a local folder
```

`--url`, `--host`, `--remote-dir` and `--key` also read `PUBLISH_URL`, `PUBLISH_HOST`,
`PUBLISH_REMOTE_DIR` and `PUBLISH_SSH_KEY`. It is a dry run unless you pass `--apply`. It
first reads the version the host already serves from the wheel name in its `install.sh`, and
refuses to publish when that equals the version in `pyproject.toml`: two commits would then
answer to one wheel name, and an install caught mid-swap would fail its checksum. Cut a
release first (`autonomouse release major|minor|patch`), which bumps the version, then
publish. After the copy it reads both files back from the URL and prints `PUBLISH: PASS`
only when the served `install.sh` pins the served wheel.

## Your Claude Code setup

autonomouse installs Claude Code and the two worker agent types, and nothing else of your
Claude Code configuration. The charters under `docs/` assume a global setup that you install
separately, in `~/.claude`:

- `~/.claude/CLAUDE.md` with the principles the charters cite: principle 0 (a charter or a
  question opens with the goal sentence), principle 6 (the kinds of decision only the human
  makes) and the rule that a secret value is never printed.
- Skills the charters name: `operations`, `project-management` (with its `PLANNING-PRS.md`,
  `LINEAR-ISSUES.md`, `USER-INVOLVEMENT.md` and `TEST-FIX-LOOP.md`), `communication`,
  `verifying`, `rca-discipline`, `github-posts`, `goal-discovery`, `prompting` and
  `software-design`. The orchestrator, the planner and the super-orchestrator load `verifying`;
  a worker loads neither it nor code review (its agent type refuses both).

Without them every session still starts and follows the charter in `docs/`; it lacks the craft
the skills carry.

## Log in to Claude and GitHub

Every session autonomouse starts runs `claude` and `gh` as your user, so both need a login
on this machine. The install command installs Claude Code, then stops before its questions
until Claude Code is logged in, since no script can log it in for you: run
`claude auth login` when the install command names it, then run the install command again.
Its first question then logs gh in with a token. To log gh in by hand instead, or on a
machine where the installer had no terminal, run both, as the user who runs autonomouse,
and follow their prompts:

```
claude auth login
gh auth login
```

- `claude auth login` signs in with your Claude subscription. `claude auth login --console`
  signs in with an Anthropic Console account instead, which bills API usage.
  `claude auth status` shows the login.
- `gh auth login` logs gh in to github.com. `gh auth status` shows the login. When you skip
  it, `autonomouse project new` asks for a token or an SSH key instead. Either way,
  `project new` then proves that the login can do on the repository what a worker does,
  and names any permission that is missing (see [First board](first-project.md#first-board)).

Two more kinds of access come later, and only when you need them:

- **Jev**, which is optional: put `TYPESAFE_API_KEY=<key>` in `~/.config/autonomouse/.env`,
  set `enabled = true` under `[jev]` in `~/.config/autonomouse/config.toml`, and check it
  with `(set -a; . ~/.config/autonomouse/.env; autonomouse jev probe)`, which prints
  `matches: yes`.
- **A card's own access.** A card can name a login its test needs on this machine, such as
  `stripe login`, or a key in `~/.config/autonomouse/.env`. The orchestrator checks each
  one before the card starts. When one is missing, you get a question on Telegram that
  names the command to run on this machine or the key to add. Do that, then answer `done`.

## Telegram

autonomouse sends you its questions on Telegram and reads your answers there. Set it up
before you create a project, because the new project's planner asks its questions there.
The install command's second question does steps 2 to 4 below for you; you still create
the bot (step 1) and send it a message when the installer asks. The steps below do the same
by hand.

1. On Telegram, message @BotFather, create a new bot, and copy the token it gives you.
2. Make the secrets file, readable only by you:

   ```
   mkdir -p ~/.config/autonomouse
   touch ~/.config/autonomouse/.env
   chmod 600 ~/.config/autonomouse/.env
   ```

   Open `~/.config/autonomouse/.env` in an editor and add this line. An editor keeps the
   token out of your shell history.

   ```
   TELEGRAM_BOT_TOKEN=<the token from @BotFather>
   ```

3. Send your new bot any message from your Telegram account. Telegram gives the bot your
   chat id only after you have messaged it.
4. Run `autonomouse notify setup`. It reads the chat id from your message and writes it to
   `notify.telegram_chat_id` in `~/.config/autonomouse/config.toml`. To run it again later,
   send the bot a new message first.
5. Run `autonomouse notify "autonomouse is set up"`. The message arrives in your chat with
   the bot.

Each wake that started work ends with one short message. It names the project and says how
many cards the wake finished and how many of autonomouse's pull requests wait for you to
merge. A wake that failed still sends it, after the message about the failure. A wake that
found nothing to do sends nothing.

`docs/config.md`, "Telegram notifications", says how your replies reach the board.

## Check that it works

1. Run the install command again. Every checklist item is `[ok]`, and the last line starts
   `All green.`
2. Run `autonomouse timer status`. It prints three crontab lines, ending
   `# autonomouse wake`, `# autonomouse review` and `# autonomouse plan`, then the next wake
   and `cron daemon: running`.
3. Run `autonomouse notify "test"`. The message arrives on Telegram.
4. Run `autonomouse list`. It prints one line for your project. `autonomouse show <slug>`
   prints the project's details and cards.
5. Answer the planner's question about the brief on Telegram. `plan step` runs every five
   minutes, so the planner's next message comes within minutes. When it does not, read
   `~/.local/state/autonomouse/plan.log`.
6. After the next wake time that `timer status` printed, read
   `~/.local/state/autonomouse/wake.log`. A wake that found work is listed under the last
   five wakes in `autonomouse show <slug>`. A wake starts a session only when the project
   has a ready card, and a new project gets its first cards once its brief is written.

To run one wake now instead of waiting for the timer, run
`autonomouse run --project <slug>`.
