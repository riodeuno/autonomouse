# Board schema: planning threads

## plans/<thread-id>.json

A planning thread: the human hands the system a request, and a planner session turns it
into cards through a conversation. The thread record, not the planner's session, is the
memory of record. The orchestrator never reads threads.

| field | type | meaning | set by |
|---|---|---|---|
| id | string | `plan-<YYYYMMDD-HHMMSS>-<4 hex>`, UTC | `plan open` |
| project | string | the project slug the request is about | `plan open` |
| opened_at | time | | `plan open` |
| session_id | string or null | the planner's Claude Code session that holds the conversation now; null until the first `plan step` | `plan step` |
| status | enum | `open` (the request, not answered yet), `waiting_planner` (the human replied), `waiting_human` (the planner's last message waits for the human), `human_joined` (the human is in the planner's session live; `plan step` leaves the thread alone), `closed` | `plan reply` (`waiting_planner`), `plan say` (`waiting_human`), `plan close` (`closed`); `plan reply` and `plan say` leave `human_joined` as it is; `plan join` and `plan new` (`human_joined` while the live session runs, `waiting_planner` after); `project new` on a terminal (`human_joined` while its setup session runs, then `closed` when the brief is written, else `waiting_planner`); `plan step` (`waiting_planner`, releasing a join whose process is gone) |
| joined | object or null | `{pid, at}` while `status` is `human_joined`, else null: the live join's claim, `pid` being the interactive claude's. A thread written before this field existed loads it as null | `plan join`, `plan new`, `project new` on a terminal; cleared when the join ends, by `plan close`, and by `plan step` for a join whose pid is gone |
| messages_given | integer or null | how many of `messages` the last `plan step` session was given in its prompt, recorded before it starts; a human message from that index on is unanswered, even one written while that session ran. Null until the first `plan step`, and after a live join, when the rule below applies. A thread written before this field existed loads it as null | `plan step`; cleared by `plan join`, `plan new` and `project new` on a terminal |
| messages | list | each `{at, from, text}`; `from` is `human` or `planner` | `plan open`, `plan reply` (human); `plan say`, `plan close` with a text (planner) |
| cards_written | list of string | ids of the cards the planner wrote for this thread; each must exist on the board | `plan cards` |
| tiers | list | one entry per planner session `plan step` started or resumed on the thread, oldest first: `{at, source, answer, confidence, probabilities, applied, reason, tier}`. `answer`, `confidence` and `probabilities` are Jev's judgment of the session's kind (null when Jev gave no usable answer), `applied` the kind the session ran as (`complex`, `simple` or `collect-user-intent`), `tier` the `<model>/<effort>` the config's `planner` table maps it to, `source` `jev` or `fallback`, and `reason` why the fallback `complex` was applied (null for Jev's pick). A thread written before this field existed loads it as `[]` | `plan step` |

A closed thread refuses every write; `plan open` starts a new one. Every write holds an
exclusive `flock` on the `plans/` folder, so a human reply and a planner message never
overwrite each other. Nothing commits the board for a thread write; the writes ride in the
next board commit.

A thread has an unanswered human message when it is neither `closed` nor `human_joined` and
a human message sits at index `messages_given` or later; when `messages_given` is null, it
has one when its last message is the human's. So a `plan reply` written in a terminal while
a planner session runs, even before that session's last message, is answered by the next
step. `autonomouse plan step` first runs `autonomouse notify
poll` under its lock, which routes the human's Telegram messages onto the board by the
reference register (`docs/board-schema/telegram-references.md`): a reply to a
decision, or a message starting with its number, answers it with `card answer` (history
`run_id` `telegram`), or is recorded on it as its `reply` when the decision is already
answered, one to a thread message is that thread's `plan reply`, and any other message
answers the one open question when exactly one is open, or else is a `plan open`
(`docs/config.md`, "`autonomouse notify poll`"); a poll that fails is printed and the step goes on, and with `notify.telegram = false` there is no poll. Then it runs the planner on every thread with an unanswered human message, oldest
first, and does nothing when there is none:

- First, the step writes the thread's message count, as it read the thread, to
  `messages_given`: every prompt below is built from that reading.
- Before each thread's session, Jev judges the kind of that session: `complex`,
  `simple` or `collect-user-intent`, a session that only collects the human's intent. The
  config's `planner` table maps the kind to the session's `--model` and `--effort`; when
  `jev.enabled` is off, the request fails or Jev's confidence is below its floor, the
  session runs at the `complex` tier (`docs/config.md`, `planner.complex`). The thread's
  `tiers` gets the entry, and a session that replaces a failed resume runs at the same tier.
- A thread with no `session_id` gets a new session (`claude -p --session-id <new id>`,
  charter `docs/planner.md`), started on the thread's first message. The session id is written on the thread before claude starts.
- A thread with a `session_id` resumes it (`claude -p --resume <session_id>`) with its
  unanswered human messages.
- A resume that exits non-zero without printing a result (claude prints `No conversation
  found with session ID` when the transcript is gone) is replaced by a new session, whose
  prompt carries every message on the thread and the cards already recorded, and whose id
  replaces `session_id`.

Each session starts in the autonomouse repository with the secrets file
(`~/.config/autonomouse/.env`) loaded, as the orchestrator does. Its printed result and stderr go to
`~/.local/state/autonomouse/runs/plans/<thread-id>-<first 8 of session id>-<n>.result.json`
and `.stderr.log` (`$AUTONOMOUSE_STATE/plans/` when that is set). `plan step` exits 1 when a
planner session exited non-zero or printed no result, and 0 otherwise. One `plan step` runs
at a time: a second one finds the lock `plan-step.lock` in that folder held and does nothing.

The planner writes the thread only through these commands, and the board otherwise only
through `card new`, for the cards its thread agreed, which land in `todo` (in `backlog` only
when the human set one aside to decide later), and through `plan brief`, for the brief of a
new project's brief thread. `plan say` only
writes the record: the human reads a planner message when the planner also sends it with
`autonomouse notify --about <thread-id>` (`docs/planner.md`, the planner's charter), which
numbers the message and registers the number for the thread (`docs/board-schema/telegram-references.md`),
so a Telegram reply to it comes back to the thread.

| command | who | what |
|---|---|---|
| `plan open <project> "<text>"` | human, or `notify poll` for a Telegram message; `project new` opens the project's brief thread the same way | a new thread whose first message is the request |
| `plan reply <thread-id> "<text>"` | human, or `notify poll` for a Telegram reply | appends a human message |
| `plan say <thread-id> "<text>"` | planner | appends a planner message: a question, or what it did |
| `plan cards <thread-id> <card-id>…` | planner | records the cards it wrote |
| `plan brief <thread-id> <file>` | planner | writes the file as the brief of the thread's project, `projects/<slug>/brief.md`, replacing any brief there in one step, and prints the goal sentence and the intent sentence it holds; the thread itself is unchanged. It refuses, and writes nothing, a file that lacks one of the seven headings of "brief.md" (`docs/board-schema/project.md`) or whose goal or intent sentence is empty, naming each problem |
| `plan close <thread-id> ["<text>"]` | planner | appends the closing message when given, and closes the thread |
| `plan show <thread-id>`, `plan list` | anyone | read |
| `plan step` | the machine | the step above |
| `plan join <thread-id>` | human | joins the thread's planner session live in the terminal (below) |
| `plan new <project>` | human | opens a thread and joins it live at once; the request is said in the session |

`plan join` runs `claude --resume <session_id>` interactively in the autonomouse
repository, with its `.env` loaded, at the `complex` tier of the config's `planner` table,
so the human continues the
same conversation with the same context; its first message tells the planner the human is
live, and the planner acts on the thread's `human_joined` status (`docs/planner.md`). A thread no planner has started yet
gets a new session there instead (`--session-id`), as `plan new` always does, whose
thread's first message says the request is in the session. Before claude starts, the
thread is marked `human_joined` with `joined`, a claim like a project's: its pid is
claude's, and it is alive while that pid exists (the liveness rule in `docs/board-schema/run.md`, where only a
crashed claim may be taken over). Claude is started held, for its pid, and runs only once
`joined` names that pid. The mark is taken holding `plan-step.lock`, so it
refuses while a `plan step` runs, and it refuses a thread another live join holds: two
sessions never resume one conversation at once. When claude exits, the thread is marked
`waiting_planner` with a human message saying the human left, and the next `plan step`
resumes the session unattended; a thread the planner closed in the session stays closed.
A join whose process died without that (the terminal closed) is released by the next
`plan step` the same way, with a message saying so.

`autonomouse project new` on a terminal joins the project's brief thread the same way,
with the same mark and lock, and runs a new interactive session in the project's clone in
place of the planner's: `claude --model claude-fable-5-1 --effort high
--dangerously-skip-permissions` with a two-line instruction that names `project edit
<slug> --goal`, `plan brief <thread-id> <file>` and `project edit <slug>
--orchestrator-instruction` and `--worker-instruction` (`docs/board-schema/project.md`). Its
session id is not recorded on the thread, since a session started in the clone cannot be
resumed from the autonomouse repository. When claude exits with the brief written, the
thread is closed, if the session did not close it; with no brief, it is marked
`waiting_planner` with a human message saying the human left before the brief was
written, and the next `plan step` answers it in the planner's own session, as above.

The board screen's `p` key opens the list of the project's planning threads, whose title
and hint say what the list is and what enter, `r`, `n` and `m` do. The open threads come
first, those waiting on the human at the top and then the one changed last first; each row
says whose turn it is in plain words (`waiting on you` for `waiting_human`, `the planner is
working` for `open`, `you answered; the planner is working` for `waiting_planner`, `you are
in it live` for `human_joined`), when the thread last changed and the request's first line.
The closed threads sit below, under their own heading. Under the list, the selected thread
shows in full as a document, in the reading view every screen that shows text to read uses:
each message is its own block under a heading that names who wrote it and when, oldest
first, and the pane opens on the newest message and scrolls back to the older ones. `r` is
`plan reply`: it shows the planner's latest message the same way above a text field, and
appends what the human types to
the thread as the human's message, so the thread waits on the planner and the next `plan
step` answers it; an empty reply keeps the field open, and escape writes nothing. Enter on
an open thread runs `autonomouse plan join <thread-id>`, and `n` runs `autonomouse plan new
<project>`, each in the same terminal with the screen suspended; when the command exits the
screen comes back and reads the threads again. Enter and `r` refuse a closed thread with
"This thread is closed; there is no planner to join." and do nothing else. `m` is `plan
open`: it asks for the request and opens the thread for the next `plan step`. The screen
writes no thread itself except through the calls `plan open` and `plan reply` make. The
board screen's header gives one line to every thread on the project that is not closed: its
request, its status in the same plain words, and that `p` opens it. `plan
step` runs at the start of every wake (`autonomouse run`, after
the `PAUSE` check and before it picks a project) and every five minutes from the crontab line `autonomouse timer install` writes
(`src/autonomouse/timer.py`), so a Telegram reply reaches its card or thread within minutes; its lock
keeps two plan steps from running at once. In the wake a failed plan step is logged and changes nothing
else: the wake keeps its own exit code.
