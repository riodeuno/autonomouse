# Running autonomouse

The timer, the board screen, where the files live, and the kill switch.

## The timer

`autonomouse project new` already wrote the timer's crontab lines. Check them:

```
autonomouse timer status
```

Run `autonomouse timer install` yourself after you change `wake.interval_hours` or `wake.start`, after
`autonomouse timer remove`, or when `project new` printed a `timers:` error. Run it from a
login shell, where `claude`, `uv`, `gh` and `git` are all on your PATH.

`autonomouse timer install` puts three lines in your crontab (`crontab -l` shows them), each ending with the
comment `# autonomouse <job>`, so a second `install` replaces only these lines and
`autonomouse timer remove` deletes only them:

- the wake, `autonomouse run`, every four hours: at 00:00, 04:00, 08:00 and so on, local
  time. `wake.interval_hours` in `config.toml` sets the hours between wakes (a whole number
  from 1 to 24, `docs/config.md`); change it and run `install` again. When `wake.start` is
  set, as the install command's last question sets it, the wakes run from that time
  instead: at its minute, on its hour and every `wake.interval_hours` round the clock, and
  none before it. `autonomouse config set wake.start 2026-09-29T09:00+02:00` moves it, and
  `autonomouse config set wake.start ""` returns to 00:00; run `install` again after
  either.
- the morning review at 06:00: `autonomouse review daily`, `autonomouse review behaviour
  --since 1` and then `autonomouse review improve`, each only after the one before
  succeeded. The last one puts a proposed instruction change in the system project's
  backlog for each pattern that recurs in the behaviour review; move a card to todo to have
  it made, or cancel it.
- `autonomouse plan step` every five minutes, so a reply you send on Telegram reaches its
  card or planning thread within minutes.

Each line runs with a PATH of `$HOME/.local/bin`, then, at install time, the folder
`shutil.which` finds each tool a wake calls in — `claude`, `uv`, `gh` and `git`, in that
order — each added once and only when not already in the list, then
`/usr/local/bin:/usr/bin:/bin`, and appends what it prints to
`~/.local/state/autonomouse/wake.log`, `review.log` or `plan.log`. `install` fails, naming
the tool, when `shutil.which` cannot find one of the four on your own PATH. The lines fire
only while a cron daemon runs. The install puts cron on the machine, and its `cron daemon`
checklist item names the command that starts it. A fire missed while the
machine is off is not run later.

`autonomouse timer status` prints the three lines, the next wake and whether a cron daemon
runs. `autonomouse timer remove` deletes the three lines.

## Open the board

```
autonomouse
```

With no arguments, `autonomouse` opens the board screen in the terminal. Run
`autonomouse project new <name>` first, so the board has a project to show. The first screen lists the
projects: each row gives the project's name, what waits on you there (its cards that need
your decision and its planning threads waiting on your reply, or "nothing"), its wake
status, the same word its board shows, and its slug. Enter opens a project's board; escape
goes back.

On the project list and on a project's board, `i` opens the project's details page. It
starts with where the project stands: the count of cards in each stack, in the board's
order, the repository's open pull requests with the card each one belongs to, and the
wakes, with the one that holds the project now and the last five, each with when it
started and ended and its summary. Then come the goal and the settings (slug, repository,
default branch and priority), and last the project's brief when it has one
(`projects/<slug>/brief.md` on the board: why the project exists, its goal sentence, its
intent and its design). `autonomouse show <slug>` prints the same facts above the cards,
with the brief first. The page reads as a document: a column about 88 characters wide, centred when the
terminal is wider, with a heading for each section, and lists and tables for the details.
The open pull requests come from GitHub, so the page says it is
reading them until they arrive, and gives the reason when `gh` cannot list them. Escape goes
back to the screen the page was opened from.

On a project's board, the six stacks sit side by side, decisions first. The arrow keys move
between stacks and cards, and the panel below the stacks shows the focused card as a
document in the same reading column as the details page, in four groups, each under a
heading with its icon: Progress (its phase, pull request, branch and worker, and its last
and next step), Goal (the goal, its limits and its test), Questions, when it has any, and
History. Any Markdown in them (a list in a goal, say) shows as Markdown. A card that waits
on a decision shows the decision instead, under "Your decision": the question first, then
the asking agent's brief, the options, the recommendation and the answer. `d` shows or
hides the rest of that card under it. `a` answers the focused card's open
question: pick one of its options, or pick the last one, "other: tell me", and type the
answer in your own words. After the answer, it asks where the card goes: "more work needed" sends it to the
top of todo, and "the work is already finished" sends it to done. `x` cancels the card, `p` opens the planning threads, where you read a thread as a
document, each message under a heading that says who wrote it and when, and answer the planner with `r`, `t` switches between the
dark and light theme, and `q` quits. The screen follows changes on disk, so a wake's work
shows up without a keypress.

When a board file cannot be read, such as a card file that a hand edit left as invalid
JSON, the screen does not quit. It keeps the last board it read, shows the error in a line
under the header, and reads again every half second until the file is fixed. A file that is
already bad when the viewer starts, or when a board or its planning threads open, is
handled the same way: that screen shows the error line and no board until the file is
fixed. That error, and any error that makes the screen or a command quit, goes into
`~/.local/state/autonomouse/errors.log` with its traceback (`docs/config.md`, "The error
log"). When the viewer quits on an error, `autonomouse` exits with code 1.

### Over ssh

The board lives on the machine, so from your own computer you view it over ssh. A command
that ssh runs does not read `~/.profile`, where the install put `~/.local/bin` on PATH, so
give the command's full path. `-t` gives the board screen a terminal:

```
ssh -t <user>@<machine> '~/.local/bin/autonomouse'
```

For plain text with no screen, use `list` and `show`:

```
ssh <user>@<machine> '~/.local/bin/autonomouse list'
ssh <user>@<machine> '~/.local/bin/autonomouse show <slug>'
```

To keep the board screen open when the connection drops, log in with ssh and run
`autonomouse` inside `tmux`, which the install puts on the machine.

## Where files live

| path | what it holds | created by |
|---|---|---|
| `~/.local/bin/autonomouse` | the command | `install.sh` |
| `~/.local/share/autonomouse/board/` | the board, a git repository: `projects/<slug>/project.json`, `projects/<slug>/cards/<id>.json`, `runs/<run-id>.json`, `plans/<thread-id>.json`, `reviews/<date>.md` | `autonomouse project new`, or `autonomouse init` |
| `~/projects/autonomouse/repos/<slug>/` | the clone of each project's repository, its `repo_path`; the card worktrees are made from it | `install.sh` makes `repos`; `autonomouse project new` the clone |
| `~/projects/autonomouse/worktrees/<project>/<card-id>/` | one git worktree per started card; removed once its card is done and its PR has merged, or once the card is cancelled and no alive or stalled run holds its project; any other worktree made during a wake is removed at the wake's end, and one there before the wake is never touched | `install.sh` makes `worktrees`; the first wake that starts the card, the worktree |
| `~/projects/autonomouse/wake-reports/` | the super-orchestrator's wake reports, `decisions.md` and `concerns.md`; git-ignored, never committed | `install.sh` |
| `~/projects/autonomouse/wake-logs/` | each wake's observation log, `<run-id>.md`, which the orchestrator appends to as the wake goes | `install.sh` makes the folder; `autonomouse run`, each log |
| `~/.local/share/autonomouse/logs/` | each wake's behaviour log, `<run-id>.jsonl` and `<run-id>.summary.json` | the first wake |
| `~/.local/state/autonomouse/runs/` | each wake's printed result and stderr log; `setup/<slug>.json`, what a project setup that has not finished was told, until the project's brief is written | the first wake; `autonomouse project new` |
| `~/.local/state/autonomouse/wake.log`, `review.log`, `plan.log` | what each cron job printed | the first fire of each |
| `~/.local/state/autonomouse/errors.log` | every error a command or the board screen did not handle, with its traceback, and every board file the screen could not read | the first such error |
| `~/.config/autonomouse/config.toml` | the settings | `autonomouse notify setup`, or you |
| `~/.config/autonomouse/.env` | the credentials | you |
| `~/.config/autonomouse/PAUSE` | the kill switch; its presence is the whole signal | you |
| your crontab (`crontab -l`) | the three scheduled jobs, one line each, marked `# autonomouse <job>` | `autonomouse timer install` |
| `~/.local/state/autonomouse-install/` | the install logs, and `answered`, the setup questions answered with nothing else to show it (a blank browser pool, planning) | `install.sh` |
| `~/.claude/agents/worker-high.md`, `worker-xhigh.md` | the worker agent types the orchestrator starts; a file already there is kept | `install.sh`, from `setup/agents/` |
| `~/.claude.json` | the Claude Code settings that register the two MCP servers | `install.sh` |

Seven environment variables move these folders, mostly for tests: `AUTONOMOUSE_HOME`
(the board), `AUTONOMOUSE_REPOS` (the clones), `AUTONOMOUSE_WORKTREES` (the worktrees),
`AUTONOMOUSE_WAKE_LOGS` (the wake logs), `AUTONOMOUSE_CONFIG` (the config folder, with
`config.toml`, `.env` and `PAUSE`), `AUTONOMOUSE_STATE` (the run results) and
`AUTONOMOUSE_ERRORS_LOG` (the error log file). `autonomouse project new` makes `repos` and
`worktrees` when they are missing. Both were under `~/.local/share/autonomouse` before:
a project recorded then keeps its `repo_path`, and a card started then keeps its
`worktree`.

In this repository:

| path | what it holds |
|---|---|
| `src/autonomouse/` | the package: `cli/` is the command, one module per top-level command, `controller.py` the wake, `tui/` the board screen, `board/` the code that reads and writes the board |
| `docs/` | the charters the agents follow, the board schema and the config reference |
| `setup/` | `install.sh`, `tools.txt` (the pinned tools), `publish.sh` |
| `scripts/` | the session-end hook and the screenshot script |
| `tests/` | the test suite |

## Pause and resume

To stop new wakes, create the kill switch:

```
touch ~/.config/autonomouse/PAUSE
```

While the file exists, every wake exits at once with code 3 and starts nothing. Each one
writes a run record whose summary is `refused: PAUSE - <path> exists`, and the board screen
shows the wake status as paused. A wake that is already running goes on to its end.

The kill switch holds back wakes only. `plan step` still runs every five minutes, so the
planner still answers your Telegram messages, and the morning review still runs at 06:00.

To resume, delete the file. The next wake on the timer starts work again:

```
rm ~/.config/autonomouse/PAUSE
```

To stop everything the timer runs, remove its crontab lines with
`autonomouse timer remove`. `autonomouse timer install` puts them back.
