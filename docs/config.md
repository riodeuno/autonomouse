# Config file

`~/.config/autonomouse/config.toml` holds the settings a human tunes: where the board
lives, the spend ceilings, whether Jev is consulted, and whether the board viewer draws
Nerd Font icons. `autonomouse notify` sends to
Telegram only, and Telegram is optional (`notify.telegram`). The file is optional: any key it does not set keeps its default, and a
missing file yields every default below.
`autonomouse config show` prints the effective config — file over defaults — with any
value that looks like a credential masked (`***`). `autonomouse config set KEY VALUE` writes
one key, named as `config show` prints it, into the file as the type its default has (a
whole number, `true` or `false`, or text), keeps every other line, and refuses a key the
table below does not hold or a value of the wrong type, naming it. `install.sh` writes
`browser.pool` and `wake.start` with it.

| key | default | meaning |
|---|---|---|
| `wake.interval_hours` | `4` | the hours between scheduled wakes. With `wake.start` unset, `autonomouse timer install` writes the wake's crontab line as `0 */N * * *` from it: a wake at 00:00 and every N hours after, local time (`autonomouse timer status` prints the next one). A whole number from 1 to 24, else `install` fails naming this key. Change it, then run `autonomouse timer install` again; the crontab line holds the old value until then |
| `wake.start` | `""` (unset) | the time the first wake runs, ISO-8601, such as `2026-09-29T09:00+02:00` (a time with no offset is local time); `install.sh`'s fourth question, asked before the first project, writes it. Set, `autonomouse timer install` writes the wake's crontab line at the start's minute, on the start's hour and every `wake.interval_hours` round the clock from it (`30 1,5,9,13,17,21 * * *` for 09:30 every 4 hours), and the line runs no wake before the start; `autonomouse timer status` names the start as the next wake while it is ahead. Unset, the line is `wake.interval_hours`'s. A value that is not a time fails `install` naming this key. Change it with `autonomouse config set wake.start <time>` (`""` unsets it), then run `autonomouse timer install` again |
| `ceilings.workers_per_wake` | `8` | the number of workers alive at once, at most. `autonomouse run` hands the orchestrator every ready card of the project: it starts the first ones, up to this ceiling, at once (the run record's `cards`), and names the rest as waiting for a free slot, in pick order; at every event of its watch loop, the orchestrator starts every ready card a free slot can take, a slot no worker has filled yet included: first the waiting cards in pick order, then the cards that became ready during the wake (`docs/orchestrator.md`, "A wake", step 3; sys-i30) |
| `ceilings.resume_tokens` | `140000` | `autonomouse run` resumes a crashed run's session only when it holds fewer tokens than this (its last API call's input, cached or not, plus output, from its transcript; unknown counts as over). A bigger session, or a run that ended cleanly, gets a new session that continues from the board's notes |
| `ceilings.retries_per_wake` | `2` | with `jev.enabled`, how many new sessions one wake starts after a failed session whose `recover` answer is `retry` (Jev's, or the fallback's). A `retry` past it ends the wake as `next_wake`, with the reason on the failed run's `recover` entry, and notifies nobody. `autonomouse run` reads it; it is not sent to Jev |
| `jev.enabled` | `false` | whether `autonomouse run` asks Jev `go` and one `size` per ready card, started or waiting (one request, before the orchestrator starts), and `recover` after a failed session; off means the orchestrator answers `size` itself. Jev's `size` is `xs`, `s`, `m`, or `split`, which means larger than `m`: the goal needs more than one worker turn (several independent deliverables, subsystems or pull requests), so the orchestrator splits the card and sizes its children; a `split` is applied as `split`, with no tier, whatever its confidence. On, `autonomouse run` applies Jev's size whenever it names one of the four sizes, whatever its confidence (no floor), and the card's own size only when the answer is missing or malformed or the request fails, and passes the tier that size maps to in `tiers` to the orchestrator's prompt; the run record's `size` entry holds the size applied and its tier. The width is the same either way and is never a Jev question: `autonomouse run` counts it (`width_rule` in `src/autonomouse/jev/wake.py`) from the cards it picked -- the resumed cards, then the ready ones in pick order -- at most `ceilings.workers_per_wake` of them; the wake starts that many at once and the orchestrator starts a waiting ready card whenever a slot is free. Two ready cards whose goals name the same file are both handed over; while the workers run, the orchestrator's conflict check reopens a done card whose pull request its sibling's merge broke (`docs/orchestrator/conflict-check.md`). On, `autonomouse run` needs `TYPESAFE_API_KEY` in its own environment (`autonomouse run` loads `~/.config/autonomouse/.env` for that request itself); without it the request fails and the wake runs as if Jev were off. A project picked for a sweep finding alone (an open pull request autonomouse opened that conflicts, whose runs failed or whose review comment is unanswered, with no card ready) has no card to size: Jev's `go` reads each finding, its card, pull request and what is wrong, as the wake's work (sys-s22). Jev's `go` is advice, with no floor and no fallback: the run record keeps the answer and the wake starts whatever it says. `autonomouse run` starts a wake whenever a card is ready (or a sweep finding is), no PAUSE file exists and no usage limit is in force, with Jev on or off; the usage limit is its own check before any session starts (a run record's `usage_limit.reset_at` still ahead), and a wake it holds back writes a run record with no session whose summary names the reset. On, a session that fails (exits non-zero, prints no result, or its result is an error) is followed by Jev's `recover` question (retry now / next wake / raise to the human), with the error, how many of the project's sessions failed in a row, and a usage limit's reset time. An answer below `recover`'s 0.6 floor, or a request that fails, is `retry`, a new session from the notes; past `ceilings.retries_per_wake` retries the wake ends as `next_wake`, and only a usable `raise` from Jev notifies the human. What Jev answers is a categorization the orchestrator takes as advice: it may set a different size (and so tier), split, merge or cancel the card, or run it anyway, noting why in the card's history, never below the `sonnet/xhigh` floor (`docs/orchestrator/tier-rubric.md`) |
| `notify.telegram` | `true` | whether autonomouse uses Telegram at all. `false` is a choice (`install.sh` writes it when its Telegram question is answered blank, and `autonomouse config set notify.telegram false` writes it): nothing is sent, nothing is polled, nothing about Telegram is logged or recorded as a failure, and you answer on the board screen. An explicit `autonomouse notify "<text>"` or `notify poll` then fails and says Telegram is off and how to turn it on. `autonomouse notify setup` writes `true` along with the chat id. With `true` and no token or chat id, Telegram is set up wrong, and every command that needs it fails loudly with the fix |
| `notify.telegram_chat_id` | `""` (unset) | the Telegram chat `autonomouse notify` sends to and `autonomouse notify poll` reads from; written by `autonomouse notify setup`, never by hand |
| `browser.pool` | `""` (unset; `install.sh`'s third question writes it, and a blank answer leaves it unset) | optional: the login, `user@host`, of a browser pool (<https://github.com/riodeuno/browsermaze>; its `browsermaze` command runs on that host, and autonomouse reaches it over ssh with the user's own ssh config) whose logged-in Chromes a wake's sessions borrow instead of a local browser. Unset: every session of every wake keeps today's local Playwright and Chrome DevTools MCP tools, unchanged. Set: `autonomouse run` starts the orchestrator session, and every worker it spawns, with `--mcp-config` naming one Playwright MCP server per slot (`browser.slots`), `pool-1` up, each pointed at one of the pool's slots over CDP (port 9300 plus the slot number on the pool's host), and `--strict-mcp-config`, so those servers are the session's only browser tools for the whole wake — no local Playwright or Chrome DevTools MCP tools, and no runtime fallback to them either way (the human's rule, 2026-09-28). A card leases a slot with `autonomouse browser lease` before it starts, which waits for a slot to free when every one is leased (exit 75 after under two minutes with the pool still full: run it again; any other failure exits 1), and releases it with `autonomouse browser release <n>` after its report; a lease left over a crashed or ended session is released as the crash-safety backstop for `_leftovers` already is. `autonomouse browser logins` and `autonomouse browser has-login <domain>` read what the pool is logged in to, with no slot needed (`docs/orchestrator/access-checks.md`) |
| `browser.slots` | `4` | how many slots the pool in `browser.pool` runs, which is how many `pool-N` servers a session gets. Set it to the pool's own slot count; a whole number from 1 up. Read only when `browser.pool` is set |
| `orchestrator.model` | `"fable"` | the model of the orchestrator session `autonomouse run` starts (`claude -p --model`); `autonomouse run --model` overrides it for one wake. A resumed session keeps the model of the run it resumes |
| `orchestrator.effort` | `"medium"` | the reasoning effort of that session (`claude -p --effort`), one of `medium`, `high`, `xhigh`, `max`; any other value fails the wake before it starts anything. `autonomouse run --effort` overrides it for one wake. The human's rule (2026-09-28) is Fable at `medium` effort for every wake, replacing the human's 2026-09-22 rule of Fable at `high` or Opus at `xhigh`, never lower. A resumed session keeps the effort of the run it resumes. The workers' tiers are chosen apart from this (`docs/orchestrator/tier-rubric.md`) |
| `tiers.xs` | `"sonnet/xhigh"` | the worker tier, `<model>/<effort>`, of a card sized `xs`. The `[tiers]` table maps each card size to its worker's tier (the human's decision, 2026-09-23): a card's size, `xs`, `s` or `m`, is the one marker of its effort and complexity, and a card bigger than `m` is split, never started (`docs/orchestrator/tier-rubric.md`). A model is `sonnet`, `opus` or `fable` and an effort `high` or `xhigh`, never below the `sonnet/xhigh` floor (Sonnet runs only at `xhigh`); a value off those, or a key that is not a size, fails the wake before it starts anything. Change a size's tier here, for example `s = "fable/high"` under `[tiers]` |
| `tiers.s` | `"opus/xhigh"` | the worker tier of a card sized `s` |
| `tiers.m` | `"fable/high"` | the worker tier of a card sized `m` |
| `improve.min_runs` | `2` | the runs a pattern of the behaviour review must show in before `autonomouse review improve` proposes an instruction change for it (a whole number, 1 or more; `docs/board-schema.md`, after the behaviour review) |
| `improve.project` | `"system"` | the board project whose `backlog` `autonomouse review improve` writes its proposed instruction changes to. Those cards change autonomouse's own charters, so it names the project whose repository is autonomouse's; its cards' id prefix (or the slug's first three letters when it has no cards yet) starts each new card's id. On a board with no project by this name — a fresh install, before `autonomouse project new` makes one — `autonomouse review improve` prints one line naming this setting and this doc, files no card, and exits 0 |
| `planner.complex` | `"fable/high"` | the tier, `<model>/<effort>`, of a planner session Jev judges `complex`: one that makes a hard decision. The `[planner]` table maps each kind of planner session to its tier (the human's decision, 2026-09-28): before `autonomouse plan step` starts or resumes a planner session, Jev judges the session `complex`, `simple` or `collect-user-intent`, and the session runs with that kind's model and effort (`claude -p --model --effort`). Jev only judges the kind (`planner_session` in `src/autonomouse/jev/questions.py`). The session runs at the `complex` tier when `jev.enabled` is off, when the request fails and when Jev's confidence is below the question's floor, and so do the live sessions of `plan join` and `plan new`. The thread's `tiers` records each answer and tier (`docs/board-schema/plan.md`). A model is `sonnet`, `opus` or `fable` and an effort `medium`, `high`, `xhigh` or `max`; a value off those fails `plan step`, `plan join` and `plan new` before they run anything. `planner.model`, the planner's one model before this table, is no longer read |
| `planner.simple` | `"opus/xhigh"` | the tier of a planner session Jev judges `simple`: one that makes a decision inside a clear goal |
| `planner.collect-user-intent` | `"opus/medium"` | the tier of a planner session Jev judges `collect-user-intent`: one that only collects the human's intent |
| `viewer.nerd_font` | `false` | whether the board viewer (`autonomouse` with no arguments) shows Nerd Font icons. The icons mark state (a card's stack and phase, a question open or answered or a read that failed, what waits on you, whose turn it is on a planning thread, a project's wakes active, paused or stopped), kind (a project, a card, a pull request, a planning thread, a wake, a worker, a model, an attachment), facts (the time, time gone by, a card's size and its iteration) and the headings of the card details, the project page and the decision prompts (goal, progress, history, settings, limits, test, step, brief, recommendation), each beside the word it stands for, on every screen of the viewer. States and sizes also show as pills: the icon and the word in capitals on a filled background in the state's colour, such as a project's `ACTIVE`, a card's stack, its size, an `OPEN` or `ANSWERED` question and whose turn a planning thread is (`WAITING ON YOU`, `PLANNER WORKING`, `LIVE`, `CLOSED`); on, a pill has rounded ends, and off, it is a plain filled block. The viewer runs on this machine, but the terminal you view it from, often over ssh, draws the icons, so turn this on when that terminal's font is a Nerd Font, such as JetBrainsMono Nerd Font: `autonomouse config set viewer.nerd_font true`. Off, the viewer shows plain Unicode symbols (such as `●`, `✓`, `⚠`) in the same places, which a terminal font without Nerd Font glyphs draws. The viewer reads it when it starts, so start it again after a change. A value other than `true` or `false` stops the viewer as it starts, naming this key |

The folder is `~/.config/autonomouse`, or `$AUTONOMOUSE_CONFIG` when that is set (for a
test run that must not touch the real settings or the real PAUSE file).

Example, running the orchestrator on Opus at extra-high effort:

```toml
[orchestrator]
model = "opus"
effort = "xhigh"
```

## The secrets file

`.env` in the same folder (`~/.config/autonomouse/.env`) holds the credentials as
`KEY=VALUE` lines, mode 600: `TYPESAFE_API_KEY` for Jev, `TELEGRAM_BOT_TOKEN` for
notifications. It sits outside every repository, so an install from the wheel finds it
without a checkout. `autonomouse run` and `autonomouse plan step` load it into the
environment of each session they start. Each wake prints `secrets=<path> (loaded)`, or
`(absent)` when the file is missing or unreadable.

## Telegram notifications

Telegram is optional. With `notify.telegram = false` (see the table above) `card_ask`,
the wake report and every other send does nothing and logs nothing, `plan step` runs no
poll, and the human answers on the board screen. The rest of this section is about
Telegram on.

`autonomouse notify "<text>"` sends to Telegram. Telegram needs `TELEGRAM_BOT_TOKEN` in
the secrets file `~/.config/autonomouse/.env` (mode 600). `autonomouse notify "<text>"`,
`notify setup` and `notify poll` each load that file into their own environment, so each
runs from a plain shell with nothing exported first; none prints a value from it
(`docs/rules.md`, item 7). A missing credential or chat id fails loudly and names the
exact fix; if Telegram rejects the message it fails loudly with its HTTP status and body.
A failed send from `card_ask` (the brief a `decision_needed` card sends) never stops the
wake — it is logged on the run record's `notify_failures` instead
(`docs/board-schema.md`).

### `autonomouse notify setup`

Telegram's bot API never gives a chat id until the human has messaged the bot at
least once. `autonomouse notify setup`:

1. loads the secrets file into its own environment for `TELEGRAM_BOT_TOKEN` (the same
   credential `notify` itself uses);
2. calls the bot's `getUpdates`, which carries every message sent to it since the
   last time something confirmed its updates;
3. when no chat id is configured yet, confirms every update it read, so `notify poll`
   never turns the message you sent to find the chat into a new planning thread; with a
   chat id already configured it confirms nothing, because those updates can be replies
   `notify poll` has not routed yet;
4. takes the chat id of the most recent message and writes it to
   `notify.telegram_chat_id` in `config.toml`, with `notify.telegram = true`, leaving every
   other key untouched. This is how Telegram is turned on after it was left off.

Message the bot on Telegram once, by hand, before running it; a bot with no
messages yet fails loudly and says so. `install.sh` runs it after its Telegram question,
again every few seconds while the bot has no message yet, and asks for the token again when
Telegram refuses it (HTTP 401 or 404). `AUTONOMOUSE_TELEGRAM_API`, when set, replaces
`https://api.telegram.org` as the Bot API's address for every Telegram request; the
installer's tests point it at a stub. Once setup or `notify poll` below has confirmed the
bot's messages, `getUpdates` no longer carries them: send the bot a new message before
running setup again.

### `autonomouse notify --about <id> [--bold "<part>"] "<text>"`

Sends `<text>` about a card or planning thread. The human cannot read a board id, so line
one of the message is a two-digit reference number and the project's name, `07 Widgets`,
in bold, and `<text>` follows on the next line; the id itself appears nowhere in the
message. `notify` records the number, the message's Telegram id and the card or thread on
the board's reference register (`docs/board-schema/telegram-references.md`), so a
reply to the message, or a message starting with `07`, reaches that card or thread.
Numbers go up by one per message and wrap after 99, reusing the oldest, except that the
number of a question still waiting on the human is never reused while it waits. An id the board
does not hold is refused, and nothing is sent.

The message goes as Telegram rich text (HTML): `<text>` shows exactly as written, `&`, `<`
and `>` included, and its line breaks and blank lines are kept. `--bold "<part>"` shows the
first place `<text>` holds `<part>`, word for word, in bold; a `<part>` that `<text>` does
not hold is refused, and nothing is sent, as is `--bold` without `--about`. `card ask`
sends every decision numbered the same way, its question in bold as the heading, then the
brief, then its options as a numbered list, and a follow-up when it is answered or
cancelled off Telegram, a bold heading line then one short line per fact; the planner
sends every message on its thread this way, its question in bold (`docs/planner.md`).

### `autonomouse notify poll`

Reads the human's new messages to the bot and routes each onto the board
(`autonomouse.inbox`). `autonomouse plan step` runs it first, under its own lock, so it runs
at the start of every wake and in the morning review. Each loads the secrets file
`~/.config/autonomouse/.env` into its environment for the token.

- Only messages from `notify.telegram_chat_id` are read; any other chat's messages are
  ignored and counted in the last line it prints.
- **What a message is about** is decided by the reference register as the poll
  found it, in this order, first match wins:
  1. **A reply to a bot message** is about the card or thread the register holds for the
     replied message. A reply to an older message the register no longer holds, whose
     text still carries a `[<id>]` tag (on its first line, or under its number line, as
     decision briefs sent before 2026-09-27 carry it), is about that id.
  2. **A message whose first word is a two-digit number** as shown, with or without a
     leading `#` (`07 2`, `#07 2`), is about the card or thread the register holds for
     that number; the rest of the message is the answer.
  3. **A message that starts with a card id or thread id** (`sys-b3 2`) is about that id.
  4. **Any other message that is not a reply** answers the one open question — a card's
     unanswered decision, or a planning thread waiting on the human — when exactly one is
     open across the board. When none or several are open, or its first word is a
     project's slug, it opens a new planning thread (below). A reply to a message the
     register cannot place and that carries no tag opens a new planning thread too: it
     points at one message, never at whichever question happens to be open.
- **About a card:** the text answers the card's open decision with `card answer`: an
  option number picks that option (the number of the last option, "other: tell me", is
  refused and asks for the answer in the human's own words); a reply equal to an option's
  name, in any case and with surrounding spaces, picks that option the same way; `done`
  finishes the card (`--done`) only when no option is named `done`; any other text is
  recorded as written and the card goes to the top of `todo`. When the card's last
  decision is already answered — the human replies to the line that says it was answered
  some other way, or answers the question late — the text is recorded on that decision as
  its `reply`, as written, and acknowledged as a reply to the answered question; the answer
  and the card stay as they were, and no planning thread opens.
- **About a planning thread:** the text is appended to that thread as the human's message
  (`plan reply`). A reply to a closed thread opens a new thread on the same project.
- **A new planning thread** (`plan open`) opens on the project whose slug is the message's
  first word (`widgets: a dark mode`), or else on the project of the most recent thread
  message or decision on the board. Board times are whole seconds, so the messages one poll
  writes usually share a second; the thread message the poll wrote last counts as the most
  recent.

Each message routed gets an acknowledgement back on Telegram. One about a card or thread
is numbered like any message about it, so its first line names the project, and it says
in plain words what was recorded and where: the answer and the question it answers, the
reply and the planning conversation it joined, or the new conversation and its project.
One that could not be routed (a number no option has, a message with no text) starts
`Nothing was recorded:` with the reason, and nothing is recorded. When the message named
no open question — a number the register does not hold, a card with no question waiting,
a card or thread no longer on the board — the acknowledgement also lists the questions
waiting for the human by number and project. Every message read is then confirmed to
Telegram, so the next poll never reads it again: a message that was not routed is sent
again, corrected. The history rows `card answer` writes carry the run id `telegram`, so it
sends no follow-up of its own: a decision answered or cancelled any other way does
(`docs/board-schema.md`, after the stack transitions), as a reply to the decision's
message, so no message in the chat points at a decision that is no longer there.

## The PAUSE file

`~/.config/autonomouse/PAUSE` is the kill switch: when it exists, `autonomouse run` exits
immediately with code 3 without picking up any card, and the crontab's next few fires do the same.
Each such fire writes a run record with no session whose `refusals` names the file, with the
summary `refused: PAUSE - <path> exists` (`docs/board-schema/run.md`). It
holds no content the tool reads — its presence is the whole signal. Remove it to let runs
resume. It is not a config key: create and delete it directly (`touch` / `rm`), in the same
directory as `config.toml`.

## The error log

`~/.local/state/autonomouse/errors.log` keeps every error autonomouse did not handle, so
you can read what broke after the terminal that showed it has closed. Each entry starts
with a `===` line holding the UTC time, then the command line, the installed version and
the full traceback. An entry is added:

- when an `autonomouse` command raises an exception it does not catch. The command still
  prints the traceback and exits non-zero, as before. An error the command reports itself
  (a line starting `error:`) is not an uncaught one and is not logged.
- when the board viewer (`autonomouse` with no arguments) raises one. The viewer still
  exits and prints the traceback, as before, and `autonomouse` exits with code 1.
- when the viewer cannot read the board: a card, project or planning-thread file that is
  not valid, or that cannot be read. The read can be the first one, when the viewer
  starts or a board or its planning threads open, or a later one: the viewer checks the
  board folder every half second and reads it again when a file in it changed. The
  viewer does not quit. It keeps the last board it read on screen, or shows
  none when no read has succeeded yet, shows the error in a line under the header, and
  reads again every half second; the line goes away and the board shows once a read
  succeeds. Each distinct error message is logged once while the viewer runs.

The file and its folder are made with the first entry. `AUTONOMOUSE_ERRORS_LOG` moves the
file, for a test.

## The folders under ~/projects/autonomouse

`~/projects/autonomouse` holds what autonomouse keeps for you to look into. The install
(`setup/install.sh`) makes it with its four folders:

| folder | what it holds | override, for a test |
|---|---|---|
| `repos/<slug>` | the clone of each project's repository, the project's `repo_path`, made by `autonomouse project new` | `AUTONOMOUSE_REPOS` |
| `worktrees/<project>/<card-id>` | each started card's git worktree, made by `card start` from the clone | `AUTONOMOUSE_WORKTREES` |
| `wake-reports` | the super-orchestrator's wake reports, `decisions.md` and `concerns.md`; git-ignored, never committed | |
| `wake-logs/<run id>.md` | each wake's observation log: `autonomouse run` makes it with a header, and the orchestrator appends to it as the wake goes (`docs/orchestrator.md`, "Your wake log") | `AUTONOMOUSE_WAKE_LOGS` |

`autonomouse project new` makes `repos` and `worktrees` when they are missing, so an
install made before these folders existed keeps working. Both roots were under
`~/.local/share/autonomouse` before: a project recorded then keeps its `repo_path`, and a
card started then keeps its `worktree`. The board stays at `~/.local/share/autonomouse/board`,
or wherever `AUTONOMOUSE_HOME` points.


### Model usage limits

A structured usage-limit 429 ends the current wake and its workers, even when Claude Code
keeps retrying. The controller polls the session and worker transcripts; `StopFailure`
records rate-limit details too. Retry waiting is bounded with
`CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS=60000` (Claude Code v2.1.295 or later), and
`CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS=1000` on versions supporting it.

While a recorded Fable limit is in force, orchestrators, planners (including project
setup), and Fable worker tiers use `opus/xhigh`. This is the Opus alias, so the user's
Claude Code model pin still applies. Reclaimed workers also use the temporary tier.
`autonomouse config show` prints these effective tiers. The saved config stays untouched:
after `reset_at`, new sessions use the saved Fable preferences again. Without a readable
reset, a Fable fallback lasts seven days from the hit. Explicitly saved Opus preferences
remain Opus; restore the preferred Fable settings to enable automatic return to Fable.

Usage limits end the wake without Jev recovery or immediate retries. Shared limits still
hold later wakes until their recorded reset; model-scoped Fable limits allow Opus wakes.
Transcript `api_error` retry entries do not refresh a claim's liveness.


### Pause one project until its next wake

Run `autonomouse project pause <slug>` to end that project's current orchestrator,
workers, and planner sessions. The command waits for their controllers to stop the
captured processes; it leaves cron installed and holds planner sessions until the next wake.
The next scheduled or manual wake clears the temporary pause and may start the project
again from its cards and handoff notes.
Other projects keep running. Repeating the command when no agents are running succeeds.
A controller that does not acknowledge within 20 seconds produces an error and leaves
the pause request pending; upgrade already-running controllers to use this command.
