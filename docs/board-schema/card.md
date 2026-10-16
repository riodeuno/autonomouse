# Board schema: cards

## cards/<id>.json

| field | type | meaning | set by |
|---|---|---|---|
| id | string | `<project-prefix>-<letter><n>`, e.g. `sys-b3` | `card new` |
| project | string | project slug | `card new` |
| title | string | short, imperative | `card new` / `card edit` (a new title keeps `iteration`, `phase` and `handoff`) |
| stack | enum | `backlog`, `todo`, `in_progress`, `decision_needed`, `done`, `cancelled` | `card new` (`todo`; `backlog` with `--backlog`, a card set aside for the human to decide later; `review improve`'s proposals too); `move`, `start`, `ask`, `answer`, `finish`, `cancel` after that |
| position | integer | order within the stack; lower is picked first | `card new` / `card edit` / `answer` and `move` from done (put it at the top of todo) |
| size | enum | `xs`, `s`, `m`: the card's effort and complexity, measured by the hardest decision inside its goal, not by its diff (a one-line fix whose cause must first be found is `s` or `m`). It fixes the worker's tier through the config's `tiers` (`docs/config.md`); a card bigger than `m` is split | `card new` / `card edit` |
| goal | string | what is true when the card is done | `card new` / `card edit` |
| limits | list of string | what the worker must not do or must keep; `docs/rules.md` always applies | `card new` / `card edit` |
| test | string | the card's test as one string; `AUTOMATED-TESTING.md` in the `project-management` skill says what belongs in it | `card new` / `card edit` |
| depends_on | list of string | card ids, in any project, that must be `done` before this one is picked; each names a card already on the board (for `board import`, on the board or in the import) and holds no comma, or the command that writes it refuses — `--depends-on` repeats, one id each. `card edit --clear-depends-on` sets it to `[]` in one step — for a dependency that is cancelled or otherwise never turns `done`, which would leave the card blocked forever, since `ready()` counts only `done` cards — with a history row naming the flag; refused together with `--depends-on` | `card new` / `card edit` |
| files | list of string | the files the card's PR is expected to change, as paths from the repository's root, written by whoever writes the card; `autonomouse card overlaps --project <slug> --files <path>…` lists the cards not yet `done` or `cancelled` that would change one of the given files, read from each card's `files`, or from the files and modules its `goal` names when the list is empty. A shared file is resolved when the cards are planned, never with `depends_on` (`docs/planner.md`, "Cards that share a file"). Each value is one path with no comma, or the command refuses — `--files` repeats, one path each; `card edit --files` replaces the list. A card written before this field existed loads it as `[]` and writes it back in on the next save | `card new` / `card edit` |
| access | list of object | the logins and tools the card's test needs on this machine, each `{name, check}`: `name` is a tool or service (`gh`, `stripe`, `playwright-mcp`) and `check` a shell command that exits 0 when that login or tool is present (`gh auth status`, `stripe whoami`), so the orchestrator can prove the access before work starts. The commands only record the list and never run a check. Each `--access` value is `<name>=<check>`, the name being the text before the first `=`; a value with no `=`, an empty name or an empty check is refused — `--access` repeats, one entry each; `card edit --access` replaces the list. `card show` prints one `access: <name>: <check>` line per entry, `access: (none)` when the list is empty. A card written before this field existed loads it as `[]` and writes it back in on the next save | `card new` / `card edit` |
| assigned | object | `{model, effort, agent_type, session_id, pid, picked_up_at}`; the worker's model, effort and agent type, and the session id and pid of the orchestrator session that holds it (workers are sub-agents of that session and die with it); null fields when not started | `card start`, and again on resume |
| worktree | string or null | absolute path of the working folder, `~/projects/autonomouse/worktrees/<project>/<card-id>` — `card start` creates it there, from `base_branch` in the project's `repo_path`, the first time, and reuses the recorded path as-is on every later start, so a card started under the earlier root `~/.local/share/autonomouse/worktrees` keeps its folder there; once the card is `done` and its `pr` has merged, or once it is `cancelled` and no alive or stalled run holds its project, a later `autonomouse run` removes the folder (never the field) and records the removal as a history row that keeps the stack (`done`→`done`, `cancelled`→`cancelled`); the card's branch and its commits stay, and uncommitted changes in the folder go with it | `card start` |
| branch | string or null | git branch name, named by the card's kind of change: `feat/<slug>`, `fix/<slug>`, `docs/<slug>`, `test/<slug>` or `chore/<slug>`, such as `feat/board-search` (`docs/orchestrator.md`, "A wake", step 4); a card started before 2026-09-29 keeps the branch it records, in the older form `card/` and its id | `card start` |
| base_branch | string or null | branch the PR targets: the project's `default_branch`, or, when a `depends_on` card is done but its PR is unmerged, that card's branch. With several such dependencies, the most recently finished one's branch, and the others are merged into the card's branch at start. Or the wake's interim branch `wake/<run id>`, for a card that shares a file with another card of its wake, when the orchestrator made that branch with `autonomouse run interim <run id>` only after the three reshaping moves failed (`docs/orchestrator/conflict-check.md`, sys-o1); `autonomouse run` itself makes no branch; a dependency's unmerged branch is then one more branch merged into the card's branch at start | `card start --base-branch`, given by the caller — `card start` takes it as a plain required argument and does not compute it; the rule above is `autonomouse github base-branch <id>`'s job (`int-g2`), which the caller runs first; and the wake's PR follow-up (`autonomouse.followup`, through `card_retarget`; run by `autonomouse run` or `card follow-up <id>`) sets it to the project's `default_branch` when it retargets an in_progress card's PR because its base merged; `autonomouse run` does the same for a done card's PR before it picks a project, then merges the default branch into the card's branch and pushes it, and reopens the card to `todo` with a note naming the conflicting files when that merge conflicts (`followup.follow_up_done`, `sys-t13`) |
| pr | string or null | PR URL | `card progress` / `card finish` |
| phase | enum or null | `writing`, `testing`, `ci`, `review`, set while in_progress by `card progress`; leaving in_progress does not clear it (`move`, `cancel`, `ask`, `finish` all leave it as it was); only `card edit` of `goal`, `limits` or `test` sets it back to null | `card progress`, `card edit` (reset), `move` from done (back to `writing`) |
| iteration | integer | attempts completed and failed: 0 at start, +1 when a test, a check or a change request fails an attempt; the worker stops when it reaches 5. `card edit` of `goal`, `limits` or `test` resets it to 0 (a new plan), and clears `phase` and `handoff.next_step` with it | `card progress`, `card edit` (reset); and the wake's PR follow-up (`autonomouse run` or `card follow-up <id>`), before it resumes an in_progress card whose open PR has a failed check or an unanswered comment (+1, `phase` back to `writing`, both named in `handoff`; not again while the notes are its own for the same head commit) |
| handoff | object | `{last_step, next_step, updated_at, run_id}`; the verification note and a no-code card's findings go in `last_step` | `card progress`, `card start`, `card finish`, `card edit` (clears `next_step` when `goal`, `limits` or `test` changes), `move` from done (sets `next_step`) |
| decisions | list | each `{id, brief, question, options[], recommendation, asked_at, answer, answered_at, message_id, reply, replied_at}`; `brief` is the asking agent's plain-words text for a human who has not read the code (rules, item 8), which `card ask` requires (`--brief`), sends under the question in its message, its paragraphs as written, and the board screen shows first; a decision written before the field existed loads it as null; `card ask` appends `other: tell me` as the last of `options` (once, even when the caller listed it), and `answer` is any text, one of the options or the human's own words; `card ask` refuses a decision without `--recommendation` (rules, item 8); a null `recommendation` stays valid for decisions recorded before that; an unanswered one keeps a finished card out of `done` (it goes to `decision_needed`). `message_id` is the Telegram id of the message that sent the decision's brief, null when that send failed; a decision written before the field existed loads it as null. `reply` and `replied_at` are the human's latest Telegram reply to the decision after it was answered, as written, and when `notify poll` recorded it; the answer stays as it was, a history row `reply to answered decision <id>: <text>` with run id `telegram` records each such reply, and a later one replaces the pair. Both are null until such a reply comes, and a decision written before the fields existed loads them as null. A reply leaves the card's stack as it was, except for a card the human set aside, one in `backlog` or `todo` whose answer is `leave this card for now`: a reply to it that starts with `go ahead`, in any case, moves a `backlog` card to `todo` or leaves a `todo` card in `todo`, either way ready again, and that reply's history row reads from `backlog` to `todo` or from `todo` to `todo`; the Telegram acknowledgement of any other reply to a `backlog` card tells the human to reply `go ahead`. A decision can also stay unanswered forever: `card cancel` of a card whose last decision is still open sends its own follow-up there (below), and never fills in `answer` or `answered_at`. A reply to that follow-up is recorded the same way, on `reply` and `replied_at`, with a history row `reply to cancelled decision <id>: <text>` and run id `telegram`; the card stays `cancelled`, and the Telegram acknowledgement says so and that a message which is not a reply asks for new work | `card ask` / `card answer` / `card cancel` / `notify poll` (`reply`, `replied_at`) |
| history | list | each `{at, run_id, from, to, note}`; from/to are stacks (null on creation). The creation row's `to` is the stack `card new` put the card in, and its note is `created via card new`, or `created via card new; why: <text>` when `card new --note "<text>"` gave the reason (a split's child names its parent and the cause there). `card edit` refuses an edit of `goal`, `limits`, `test` or `size` without `--note`, since each is a judgment on the card; an edit of `title`, `position`, `depends_on`, `files`, `access` or `tracker_issue` needs none. A `card progress` row's note is `progress: ` and the fields the row set, comma-separated (`phase=<phase>`, `iteration=<n>`, `last_step="<the text>"`, `pr=<url>`, `attach=<path>`); the last step's text is a JSON string, so a comma or a `next_step: ` inside it stays text. When the row set `next_step`, `next_step: <the text>` comes last and runs to the end of the note. So the card keeps every note its workers wrote, and `autonomouse card stuck <id>` reads the next steps. Example: `progress: phase=testing, last_step="wrote the parser, chose JSON", next_step: run pytest`. Rows written before sys-m6 hold a bare `last_step`, and rows written before sys-d35 end in a bare `next_step`; both still parse | every write |
| tracker_issue | object or null | the card's issue in its project's tracker (`docs/board-schema/project.md`, `tracker`), `{"url", "identifier"}`, or null when none is recorded: `url` is the issue's URL as given, `identifier` the tracker's id for it, read from the URL (`ABC-123` from `https://linear.app/<workspace>/issue/ABC-123/<title>`). The agent that files the issue on the tracker records it; `card edit --tracker-issue <url>` refuses a URL that is not an issue of a tracker autonomouse knows (a Linear project's URL included), replaces the issue recorded, and leaves `iteration`, `phase` and `handoff` as they are. Nothing in autonomouse reaches the tracker. `card show` prints `tracker_issue: <identifier> <url>`, or `tracker_issue: (none)`, and `autonomouse show` prints the identifier after the card's title. A card written before this field existed loads it as null and writes it back in on the next save | `card edit` |
| attachments | list of string | file paths or URLs, appended in the order given; a card written before this field existed loads it as `[]` and writes it back in on the next save | `card progress --attach` (repeatable) |
| created_at, updated_at | time | | every write |

Allowed stack transitions:

```
null → backlog                       (new; board import copies a card's rows as they are)
null → todo                          (new without --backlog)
backlog → todo                       (move)
todo → backlog                       (move; the human set the card aside, note required)
todo → in_progress                   (start; needs branch, base_branch, session_id, pid, model, effort, project claimed by this run — makes the working folder itself)
in_progress → decision_needed        (ask)
decision_needed → todo               (answer; position 0)
decision_needed → done               (answer --done; the work was already finished, the answer needs no more work)
in_progress → done                   (finish; needs pr and a verification note in handoff.last_step, or, for a
                                      card whose goal produces no code and no doc, its findings there; no open
                                      decision)
in_progress → todo                   (move; a reclaimed or replanned card, note required)
done → todo                          (move; its open PR has a conflict, failed runs or unanswered comments:
                                      reopened at position 0 with phase writing; note and --next-step required)
any → cancelled                      (cancel; reason required)
```

`todo → done` and `done → in_progress` are not
transitions, and no command makes them: a done card is reopened through `done → todo`, and a
`todo` card whose goal is already true is cancelled with a reason naming where it became
true.

A decision that leaves `decision_needed` by any path other than the human's own Telegram
reply — `card answer` with any run id but `telegram`, or `card cancel` of a card whose last
decision is unanswered — sends one follow-up line to the Telegram chat, as a reply to the
decision's message when `message_id` is stored. Like every message about a card, its
first line is a reference number and the project's name (`docs/board-schema/telegram-references.md`),
then `The question "<question>" was answered by <who>:
<answer>. The card is now in …`, or `The question "<question>" is no longer needed: <who>
cancelled the card.` Neither names the card's id; a cancel's reason stays in the card's
history. `<who>` comes from the run id the command carries: `screen` (the board screen's
`a` and `x` keys) is "you, on the board screen", `manual` (the command line's default) is
"someone at the command line", and a planning thread id is "the planner". An
orchestrator-shaped id (`<YYYYMMDD-HHMMSS>-<4 hex>`) is named by its run record's `kind`
when one loads: `wake` is "the orchestrator", `planner` is "the planner" and `session` is
"an agent session acting for you", the same phrase the retired run id `human` used to get;
a record written before the field existed, and an id with no record at all — a wake's own,
the common case — are both "the orchestrator". Any other id is "an agent". A failed send is
logged like any notify and never stops the command. The
human's Telegram reply to that line is recorded on the decision as its `reply`
(`notify poll`) — on the answer already given, for a decision `card answer` closed, or,
for a decision `card cancel` left unanswered, on the decision as it still is, with the
card staying `cancelled` — and opens no planning thread.

Only the orchestrator makes transitions and claims. `card start`, resuming an `in_progress`
card, takes `--branch`/`--base-branch` again as plain arguments and overwrites the card's
`branch`/`base_branch` with whatever it is given; nothing in the command itself remembers
the old values, so the caller must pass the same branch and base branch the card already
has for the worker to continue there from the handoff. The working folder is not one of
its inputs: it lives at the fixed path `~/projects/autonomouse/worktrees/<project>/<card-id>`
(`AUTONOMOUSE_WORKTREES` overrides the root, for a test), which `card start` creates on the
first start and finds already there — untouched — on a resume. A card that already records
a `worktree` keeps that path on every later start, even after the root has moved.

`card delete` removes a card's file outright — no stack transition, no history row in the
file (the board folder's own git history is the record); refused if the card does not exist.

Splitting a card: the children enter `todo` with the parent's `depends_on` and the parent's
position, in the order the orchestrator names them, a child also depending on each earlier
child whose work it builds on. Each child's first row carries the note its `card new` is
given with `--note "split from <parent id>: <cause>"`, so the row reads `created via card
new; why: split from <parent id>: <cause>`; the note also names the parent's `branch`, `;
reuse branch <branch>: <what it holds>`, when that branch holds work the child can reuse.
Every card that depended on the parent now depends on all the children; the parent is
cancelled with a note naming the children and the cause. The parent's open `pr` is closed
with a comment naming the children, since a cancelled card has no worker to finish it
(`docs/orchestrator/splitting-and-replanning.md`). The parent's branch stays, and its
worktree goes the way every cancelled card's does (the `worktree` field above).
