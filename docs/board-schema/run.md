# Board schema: runs

## runs/<run-id>.json

A run record may carry fields this table does not name: they are loaded, kept and written
back as they are, never refused. Processes running different versions of autonomouse write
the same record — a merge that lands while a wake runs gives the orchestrator's
`autonomouse` commands the new code while the wake's `autonomouse run` keeps the old one —
and a record the older version refused lost its result that way (run
`20260922-180917-e38d`, sys-d31). A record that fails validation for another reason (a field
missing, or of the wrong type) still gets the session's end from `autonomouse run`, written
straight into its JSON: `ended_at`, `result`, `usage_limit`, `exit_code`, `leftover_processes`,
`status` `stopped` from `alive`, and `summary` when it has none.

The same tolerance applies before a wake's claim, wherever it reads every run record to
answer a question of its own: the usage-limit check (`usage_limit_in_force`), the Jev
request (`consult_jev`'s `all_runs`), and the Situation block every prompt ends with
(`autonomouse.situation.build`). A record that fails validation there is printed
(`run_record_unreadable=`, naming the record and its error) and left out of that one read,
rather than stopping the wake before it claims the project or starts a session, the way one
bad file used to (sys-t17); the remaining, valid records still answer the question.

| field | meaning |
|---|---|
| id | `<YYYYMMDD-HHMMSS>-<4 hex>`, UTC; `seed` for the planning session. `autonomouse run` chooses it and passes it to the orchestrator; `run start` makes one with `new_run_id` when given none, and prints the record as it does for a given id |
| kind | who started the session: `wake` (`autonomouse run`'s own orchestrator session), `planner` (a planning thread's session, `autonomouse.planner`, which also writes `thread`) or `session` (any other agent session, the human's proxy included, that registered itself with the shell's `run start`, which always writes this kind). A record written before the field existed loads as `wake` |
| project | slug, or null until a project is claimed |
| thread | the planning thread id on a `planner` record, written by `autonomouse.planner`; null on every other kind |
| session_id, pid, model | the orchestrator session; null until started. `session_id` and `pid` stay null for good in a wake that started no session, which `autonomouse run` writes `stopped` at once, with no cards and no claim: a wake a usage limit held back (it names the project, `model` is null, and `summary` names the reset), and a wake the PAUSE file or a config value refused (no project; `model` is the one the wake would have run, and `refusals` says why). Records written before sys-j6 also hold wakes Jev answered `skip`, with the answers |
| effort | the orchestrator session's reasoning effort (`claude -p --effort`), written by `autonomouse run` beside `model` (`orchestrator.model` and `orchestrator.effort` in `docs/config.md`, or its `--model` and `--effort`); null when the session was started by hand with `run start`, in a wake a usage limit held back (and, on records written before sys-j6, one Jev did not start), and on records written before the field existed |
| started_at, ended_at | times |
| cards | the batch: the ids the wake started at once, the resumed cards then the first ready ones up to `ceilings.workers_per_wake`, written by `autonomouse run`; every other card the orchestrator starts in the wake, into a slot a finished worker freed or one no worker has filled yet, is added with `run note --card` |
| jev | list of `{question, card, source, answer, confidence, probabilities, applied, reason}`, and `tier` on a `size` entry; `question` is `go`, `width`, `size` or `recover` (`fits` on records written before sys-i29, when Jev answered whether a card fit one worker, and `tier` before sys-i18, when it answered a model and an effort); `card` is null for wake-level questions and for `recover`; `source` is `jev`, `orchestrator` (Jev not consulted) or `fallback` (Jev's answer was unusable: for `size` the card's own size, taken only when the answer is missing or names none of the four sizes, or the request failed; for `recover` `retry`, a new session from the notes, taken when the answer is below the 0.6 floor or unusable, or the request failed). `go` has no floor and no fallback, since its answer is advice the wake never applies: a wake starts whenever it has work, no PAUSE file exists and no usage limit is in force. A `go` entry's `source` is always `jev`, its `answer` Jev's pick (null when it gave none), its `applied` null, and its `reason` `advice, never applied: the wake starts whenever it has work`, after why the answer was missing or unusable when it was. Records written before sys-j6 show `go` applied as `go` or `skip`, through a 0.6 floor and a fallback that read the run records. `answer` is Jev's pick, null when it gave none; `confidence` is null when Jev gave none; `probabilities` is Jev's probability per option, kept when the pick fell below the `recover` floor (or, on records written before sys-j6, the `go` or `size` floor) so a low-confidence answer can be read afterwards, null when Jev gave no choice answer, and absent on `orchestrator` entries and on records written before the field existed; `width` is counted by `autonomouse run`, not asked: its `answer`, `confidence` and `probabilities` are null, `applied` is the width (the rule is in `docs/config.md`, `jev.enabled`: every card the wake picked, at most `ceilings.workers_per_wake`), `source` is `jev`, or `fallback` when the Jev request itself failed, and `reason` gives the count in words; `applied` is what was done after ceilings, null when nothing was done with the answer (always, on a `go` entry); `reason` says why a fallback was taken, why a ceiling changed `applied`, why `applied` is null, or, on a `size` entry, that the card `waits for a free slot` (the width left it out of the run record's `cards`; the orchestrator starts it as soon as a free slot can take it) or is `larger than m: the orchestrator splits it`, and is null or absent otherwise. For `size`, `applied` is the size applied (Jev's whenever it names `xs`, `s`, `m` or `split`, whatever its confidence, or the card's own on a fallback) and `tier` is the `model/effort` that size maps to in the config's `tiers`, null when `applied` is null; a `size` answer of `split` (larger than `m`) is applied as `split` with `tier` null, whatever its confidence. The orchestrator may override any size (`docs/orchestrator/tier-rubric.md`). A `recover` entry sits on the record of the session that failed; its `applied` is `retry` (a new session starts from the notes, as a new run record), `next_wake` (the wake ends) or `raise` (the wake ends and `autonomouse notify` tells the human; only a usable `raise` from Jev does this); a `retry` past `ceilings.retries_per_wake` (`docs/config.md`, 2 by default) is applied as `next_wake`, its `reason` naming the count (`2 retries this wake already (ceilings.retries_per_wake is 2)`), and nobody is notified. With `jev.enabled`, `autonomouse run` writes every Jev entry; the orchestrator writes the `orchestrator` entries |
| notify_failures | list of `{at, text, error}`; one entry per send `autonomouse notify` (`card_ask`'s call, or any other caller of `autonomouse.notify.notify`) could not deliver — the channel, credential, chat id or network problem named in `error`. Never stops the call that tried to send; a run record written before this field existed loads as if it were `[]` |
| refusals | list of `{at, command, reason}`: each refusal of a command that carried this run's id (`--run-id`, sys-j5), and each one `autonomouse run` meets itself. The PAUSE file gets a record of its own, with no session, `command` `PAUSE` and `reason` `<path> exists`; so does a config value `autonomouse run` refuses (`command` `run`). A `project claim` refused as the session starts is written on the wake's record (`command` `project claim`); the wake fails and claude never runs. A session that ended on `--max-budget-usd` (claude's result `subtype` `error_max_budget_usd`, or `terminal_reason` `budget_exhausted`) gets `command` `claude --max-budget-usd` and a `reason` that quotes each error in claude's result JSON that names the budget (`the session ended on --max-budget-usd: Reached maximum budget ($5)`), or, when none does, says what the result holds (`... its result names no budget: subtype 'error_max_budget_usd', errors [...]`). A run record written before the field existed loads as if it were `[]` |
| result | the JSON result `claude -p` printed (`is_error`, `num_turns`, `duration_ms`, `total_cost_usd`, `stop_reason`, `session_id`…), copied in by `autonomouse run` after the session exits; null while it runs |
| exit_code | the orchestrator session's own process exit code (`proc.wait()`), written by `autonomouse run` beside `result`, once the process has actually exited; null while it runs, on a record written before this field existed, and on the orchestrator's own clean-end call (`run end`), which runs before the process exits and so never knows it |
| leftover_processes | how many processes the orchestrator session left alive that `autonomouse run` ended once the session exited (sys-u4): every process still in the session's own process group, and every process whose environment carries the run's `AUTONOMOUSE_RUN_ID`, which catches what Claude Code's Bash tool starts in a process group of its own. Each got `TERM`, then `KILL` after five seconds when still there, and one line naming its pid and command in the wake log. `0` when none were left; null while the session runs, on a wake that started no session, and on records written before this field existed |
| usage_limit | null or an object with required `hit_at`, `reset_at`, `text`, and optional string `model` and `rateLimitType`. Structured controller or worker 429 errors supply the error timestamp, model, limit type and `error.rateLimits.resetsAt` (epoch seconds converted to UTC ISO text). Those transcript fields are undocumented and pinned by regression tests. Printed legacy usage-limit messages supply the hit time, parsed reset and message line. An unreadable reset is null. Older records retain their three original fields and records predating this field load it as null. |
| end_reason | `completed` or `interrupted`, from the `SessionEnd` hook (`src/autonomouse/session_end_hook.py`, which `autonomouse run` registers on every orchestrator session it starts with claude's `--settings`, from a checkout and from the installed wheel alike): a headless session's own hook payload reports the same generic reason for a clean exit and one cut off by a signal, so the hook instead reads the session's transcript for whether its last turn ended cleanly; `clear`, `logout` or `prompt_input_exit` when Claude Code's payload already names an interactive ending. Or `crashed`, set by the next run's `project claim` when it takes over a crashed claim that had none (the hook never got to run) — a takeover never writes the literal `taken_over` here, only onto `status`. Whichever writes first wins; nothing overwrites it after |
| status | `alive` (written at start with `session_id`, `pid`, `model`, `started_at`, `version`), `stopped` (written by the orchestrator at its clean end with `ended_at`), `crashed`, `taken_over` (written by the run that finds it) |
| summary | one line written at the end: what moved, or `blocked: <reason>` when nothing could. A wake that started no session says why: `not started: a usage limit is in force until <reset_at>`, or `refused: <command> - <reason>` for a refusal that got a record of its own, such as `refused: PAUSE - <path> exists`. Records written before sys-j6 also hold `jev: skip (<why>)`, where the why named Jev's confidence and the floor |
| version | the installed autonomouse version (`autonomouse --version`, read from the installed package's metadata, so a machine installed from a wheel reports the wheel's version) that wrote the record; set by `run start` and by `autonomouse run` when it records a wake it did not start. A board shows from it which release each wake ran. A run record written before this field existed loads as if it were null |
| interim_branch | `wake/<run id>`, the wake's interim branch, which the orchestrator makes with `autonomouse run interim <run id>` only after the three reshaping moves failed (`docs/orchestrator/conflict-check.md`, sys-o1): the command pushes the branch to the project's `origin` from `origin/<default_branch>` and writes this field, and run again it changes nothing. `autonomouse run` itself never makes the branch, and ready cards that share a file get none from it. The cards the orchestrator puts on the branch target it with their pull requests, each card's worker merges its own into it when green, and the branch's one final pull request merges into the default branch only when the owner merges it or tells the super-orchestrator to merge it (`autonomouse github merge`). Null until the orchestrator makes the branch, in a wake that never needed one, and on records written before the field existed |
| interim_pr | the URL of the final pull request from `interim_branch` to the project's `default_branch`, which the orchestrator opens as a draft at the wake's end and records with `autonomouse run note <run id> --interim-pr <url>`; null until then, and null for good when `interim_branch` is null |
| waiting_for | the slug of the project a waiting wake waited for. `autonomouse run` before one wake per machine wrote it when it started the orchestrator on a project an alive or stalled run held; nothing writes it now (a wake that starts beside a live one starts nothing), and records that carry it still load. Null on every other run, and on records written before the field existed |

## Liveness of a claim

A claim (on a project or a card) is alive when the pid exists **and** the session transcript
`~/.claude/projects/<cwd-slug>/<session_id>.jsonl` was written to in the last ten minutes. It is
stopped when the pid is gone and the run record has `ended_at` or `result`. It is crashed when
the pid is gone and it has neither. It is stalled when the pid exists but the transcript is
older than thirty minutes. A stopped or crashed claim may be taken over; an alive claim is left
alone; a stalled one is left alone and reported in the run summary for the human.

`<cwd-slug>` is the session's working directory with every `/` replaced by `-`, for example
`/home/user/projects/autonomouse` → `-home-user-projects-autonomouse`. Orchestrator
sessions always start in the autonomouse repository, so that is their slug.

How `autonomouse run status <id>` applies this rule:

- The transcript's write time is the newest of `<session_id>.jsonl` and every file in
  `<session_id>/subagents/`. While the orchestrator waits on its workers only their
  transcripts grow, and the orchestrator is not stalled.
- Every `<cwd-slug>` folder is searched for the session id, so the caller need not know
  the session's working directory. The folder is `$CLAUDE_CONFIG_DIR/projects` when that is
  set, else `~/.claude/projects`.
- A live pid whose transcript is 10 to 30 minutes old is reported `alive`; the rule above
  names no state for it, and it is left alone either way. A live pid with no transcript at
  all is `stalled`.
- A record whose `status` is `taken_over` is reported `taken_over`.

The commands that write these files: `run start [<id>] --model M` (session id and pid
default to `$CLAUDE_CODE_SESSION_ID` and `$CLAUDE_PID`) writes the record `alive`, of kind
`session`, since a record started by hand this way is an agent session's; its id is
optional — given none, it makes one with `new_run_id`, the same as `autonomouse run` does
for a wake; `run end <id> [--result FILE] [--summary S]` sets `ended_at` once, copies in
the result, and turns `alive` into `stopped`; `project claim <slug> --run-id <id>` writes
the claim from the run record and
sets the record's `project`, taking over a stopped, crashed or taken-over claim and marking
that run `taken_over` (with `end_reason: crashed` when it crashed and had none), and refusing
an alive or stalled one; `project release <slug> --run-id <id>` clears the claim, only for
the run that holds it.

`autonomouse run` (no subcommand) is the run controller and writes the record for every
wake it starts. One wake per machine: when a run the liveness rule calls alive or stalled
holds any project's claim, another wake is working, and this one starts nothing (exit 3, no
record). Otherwise it marks finished projects done (`done_at`, `docs/board-schema/project.md`);
when every project is done it removes the three crontab lines and exits 0 with no record.
Then it picks, among the projects not done, the smallest `priority`; among projects of
equal priority, one whose claim is held by a stopped or crashed run (per `run status`)
comes first. A project with no ready card, nothing to resume and no finding passes the
pick to the next. `--project <slug>` considers only that project. Every prompt
`autonomouse run` gives the orchestrator ends with a Situation block
(`src/autonomouse/situation.py`): the project; `wake.interval_hours` and the next fire,
named only when the wake's line is in the user's crontab (else "no timer is installed", or
"next fire unknown" with the reason when `crontab -l` fails); the latest recorded
usage-limit reset still in the future, or none; and the ready cards. The code reports
these facts and decides nothing. When the picked project's
claim is held by a crashed run (its session never reached a clean end) whose session holds
fewer than `ceilings.resume_tokens` tokens, it resumes that run's session: `claude -p
--resume <its session id>` with its model and effort (the configured effort when its
record has none), and a message that gives the new run id and
says to continue the cards from their notes. The count is the session's last API call,
input (uncached, cache written and cache read) plus output, read from the `usage` of the
last assistant line of its transcript; no transcript or no usage counts as over. Otherwise
(no dead run, a run that ended cleanly, or a big session) it starts `claude -p` with a
session id of its own choosing (`--session-id`), and the board's handoff notes and the
worktrees carry the cards. The printed `resumed=` line says which case applied and the
token count it saw. Then it writes a new run record `alive` with that session id
and claude's pid, puts the cards it picked in `cards`, and claims the project for the run,
which marks the dead run `taken_over`. Claude is started before these writes, because the
record needs its pid, and runs nothing until they are committed: the session never acts
before its project is claimed. A resumed session keeps its session id, so two run
records can carry the same one. A resume that exits non-zero without printing a result
has failed (claude prints `No conversation found with session ID` when the transcript is
gone): the controller records that run's end with the summary `resume of session <id>
failed …` and starts a new session from the notes in the same wake, which takes over the
claim. After the session exits it does what `run end` does with the printed result, adds
the summary `orchestrator exited <code> without writing a summary` when the orchestrator
wrote none, derives the run's behaviour log, and releases the claim when the run still
holds it.

The behaviour log says how each agent of a run behaved, never what it built. It is
derived from the session's transcripts and the board's card histories, which are only read,
and no row reads a shell command. It goes into `logs/<run-id>.jsonl`
(one event per line) and `logs/<run-id>.summary.json` in the folder beside the board
(`~/.local/share/autonomouse/logs/`), outside the board's git repository.
`autonomouse log show <run-id>` prints the summary; `autonomouse log derive <run-id>` derives
it again. A wake a usage limit kept from starting gets one too, with the reason `no session started`;
a transcript that is missing or unreadable gives the reason `transcript unavailable`; a
derivation that fails is printed by the controller and never fails the wake. The summary
also carries the run record's Jev answers, without their prose `reason`, and the times on
the board the run wrote that cannot be true (history rows out of order or later than the
run's last transcript message, decisions answered before they were asked), both read at
the time of the derivation. `docs/behaviour-log.md` lists every row of the log and of the
review and where each comes from.

`autonomouse review behaviour --since <days>` reads these logs, those of the runs whose id
falls in the last `<days>` days, and the board's cards: the cards done and the decisions
come from the board's card histories and decisions; everything else comes from the logs.
It writes `reviews/behaviour-<date>.md` (the UTC day it is written): per run and over the
window, the cards done, decisions, iterations, cost, rule breaches by class, waits, PR
states and Jev's answers against the orchestrator's; every row names its run and card. The
iterations also come from the card histories, and the hand edits of cards from the board's
git history. What each section counts is in `src/autonomouse/review/behaviour.py`.

`autonomouse review improve` reads the latest behaviour review (`--review <path>` names
another) and proposes instruction changes; it never edits a doc. For each pattern the
review shows in `improve.min_runs` runs or more (`docs/config.md`, default 2) — hand edits
of cards; times on the board that cannot be true; workers or their helpers idle 30 minutes
or more; PRs conflicting at finish — it creates one card
in the `system` project's `backlog` with `card new` (run id `improve`, id the next free
number of the project's `d` series, title `improve: <pattern>`). The card's goal names the
doc, its section, the sentence to add or reword, the runs, and quotes the review's rows as
evidence. A pattern whose `improve: <pattern>` card is still open (any stack but `done`
and `cancelled`) gets no second card. It prints one line per pattern found: `created`,
`open already` or `below threshold`. The morning review's service runs `review behaviour
--since 1` and then `review improve` after `review daily`. The patterns and their proposed
sentences are in `src/autonomouse/review/improve.py`.
