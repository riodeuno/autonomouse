# Board schema: projects

## project.json

| field | type | meaning | set by |
|---|---|---|---|
| slug | string | directory name and id | `project new` |
| name | string | display name, which the board screen shows in a box above the stacks | `project new` / `project edit --name` |
| goal | string | one paragraph: what this project is for; empty until planning writes it, when `project new` had no `--goal` | `project new --goal` / `project edit --goal`, which the live setup session runs when the human agrees |
| repo | string | GitHub URL PRs are opened against | `project new` |
| repo_path | string | local checkout the worktrees are created from | `project new` |
| default_branch | string | the project's default branch: the branch every card's pull request goes into, unless the card stacks on another card's branch or the wake's shared branch, and the one branch the super-orchestrator merges into, only the pull requests the owner told it to merge | `project new` |
| priority | integer | lower number = picked first | `project new` / `project edit` |
| tracker | object or null | the third-party tracker the project is linked to, `{"kind", "url", "link"}`, or null for none: `kind` is the tracker, read from the URL's host (`linear`, from `linear.app`; the only one known today); `url` is the URL as given; `link` is `project` when the URL is a Linear project (`https://linear.app/<workspace>/project/<name>`), whose cards' issues go in that project, or `issue` when it is a Linear issue (`https://linear.app/<workspace>/issue/<ABC-123>`), whose cards' issues are its sub-issues. A URL on another host is refused with a message naming the trackers known, and a Linear URL that is neither shape is refused with both shapes. `autonomouse.board.trackers` reads the URL, and adding a tracker is one more entry there and its probe charter. Before a link is saved, `project new` and `project edit --tracker` prove it: a small agent (`autonomouse.probe`) follows `docs/probes/<kind>.md` (`docs/probes/linear.md`), which through the Linear MCP reads the link, creates one test issue there and removes it; a missing Linear MCP or a refused write leaves the link unsaved and prints the fix. Each card records its own issue in its `tracker_issue` (`docs/board-schema/card.md`); the mapping rules are the `project-management` skill's `LINEAR-ISSUES.md`. `project show` and `autonomouse show` print `tracker: <kind> <link> <url>`, or `tracker: (none)`. A project written before the link existed has `tracker` null and loads as it is | `project new` (its question, or `--tracker`) / `project edit --tracker <url>`, which replaces the link; `--tracker none` clears it |
| test_temp_env | list of string | the environment variables that every test run of the project points at a fresh temporary folder, so that no test touches this machine's own state, e.g. `["AUTONOMOUSE_HOME"]`; default `[]` | `project edit --test-temp-env <name>`, which repeats, one name each, and replaces the list |
| merge_method | string | `squash`, `merge` or `rebase`: the way a card's green PR merges into the wake's interim branch `wake/<run id>`; a merge into `default_branch` is always a squash (`autonomouse github merge`, `docs/rules/03-pr-leaves-base-branch-working.md`); any other value is refused, and the refusal names the key; default `squash` | `project edit --merge-method` |
| charter_files | list of string | path globs from the repository's root, e.g. `docs/orchestrator/*`; a card that changes a file one of them matches is at least size `s` (`docs/orchestrator/tier-rubric.md`); default `[]` | `project edit --charter-files <glob>`, which repeats, one glob each, and replaces the list |
| claim | object | `{run_id, session_id, pid, model, claimed_at}`, all null when free | `project claim` / `project release` |
| created_at, updated_at | time | | every write |

A project written before `test_temp_env`, `merge_method` and `charter_files` existed loads
their defaults and writes them back in on the next save. `project show` prints all three.

`autonomouse project new [<name>]`, the create-project wizard (`autonomouse.project_wizard`),
creates a project from its GitHub URL, and the board first when there is none, asking on
stdin each question its flag does not answer, so a second project is set up the same way
as the first: the project's name (`<name>` or `--name`), then the URL (`--repo`;
`https://github.com/<owner>/<name>` or
`git@github.com:<owner>/<name>.git`; `repo` is recorded in the https form), then, once the
repository is cloned into `~/projects/autonomouse/repos/<slug>` (`$AUTONOMOUSE_REPOS`;
the project's `repo_path`; the clone root and the worktree root
`~/projects/autonomouse/worktrees` are made first when missing), three questions, each
with a default that Enter keeps: the name the agents commit with (`--git-name`; default
the GitHub account's name, else its login), their email (`--git-email`; default
`<id>+<login>@users.noreply.github.com`, the account's no-reply address) and the branch
their pull requests go into (`--default-branch`; default the clone's `origin/HEAD`, or
`main` on an empty repository). A flag skips its question, and when stdin has ended each
question takes its default. The name and email are set with `git config` on the clone
only, which its worktrees share, so every worktree commits with them and no other
repository changes. A branch that is not on origin is refused and the question is asked
again; given with `--default-branch`, it stops the command with a plain message. An empty
repository, one with no branch yet as GitHub makes a new one, has no `origin/HEAD`, and
its branch need not exist on origin: the wizard pushes one commit with no files to the
chosen branch, which GitHub then makes the default branch, and checks the clone out on it,
since a worker's pull request needs a base branch. That commit and branch are all it adds,
and it changes nothing that was there. The chosen branch is recorded as `default_branch`,
and the access proof (`autonomouse.github_access`) starts from `origin/<default_branch>`
and opens its draft pull request against it. After that it asks the Linear
project or issue URL the project is linked to (`tracker` above), which a blank answer, and
ended stdin, leave unlinked; a URL no tracker reads is refused and asked again, and a bad
`--tracker` stops the wizard before it makes anything. A link the access check (`tracker`
above) cannot prove is refused with its fix and asked again; given with `--tracker`, it
stops the wizard before `project.json`. No question asks the goal: `--goal` sets it, for
a script, and otherwise it is empty until planning writes it. A project recorded
before the clone root moved keeps its `repo_path`. `slug` comes from the name, `priority`
is one more than the highest on the board (1 on an empty one), and the board is committed
as `autonomouse project new <slug>`. Then it
installs the timers and checks by key name that `TELEGRAM_BOT_TOKEN` is in the secrets
file and `notify.telegram_chat_id` is set, printing the three setup steps when either is
missing. Then it opens the project's brief thread, whose request (`BRIEF_TEXT` in
`autonomouse.planner`) names the brief's path. With no terminal it runs the planner on the
thread once under the `plan step` lock, at the tier a `plan step` gives the session
(`docs/board-schema/plan.md`), prints the planner's first question and commits the board
as `autonomouse project new <slug>: brief thread <thread-id>`; a planner that fails leaves
the thread to the next `plan step` and exits non-zero with that fix; the planner asks the
rest on Telegram. On a terminal it runs no headless turn: it joins the brief thread under
the `plan step` lock, as `autonomouse plan join` does, and runs an interactive `claude` in
the project's clone (`repo_path`) with `--model claude-fable-5-1 --effort high
--dangerously-skip-permissions`, a new `--session-id`, and a two-line instruction
(`setup_prompt` in `autonomouse.planner`) that says it is planning the project with the
human and names the commands that write the goal (`project edit <slug> --goal`) and the
brief (`plan brief <thread-id> <file>`) when the human agrees. The session ends when the
human leaves it. With the brief written, the wizard closes the thread, if the session did
not, and removes the setup's record; with no brief, it leaves the thread to the planner
with a message saying so (`waiting_planner`), as `plan leave` does. Either way it commits
the board as `autonomouse project new <slug>: brief thread <thread-id>`. When stdin has
ended, a question with no default (the name, the URL) stops the command and names the
question. Each project gets its own clone, identity and branch; the crontab
holds one set of autonomouse lines for every project. A step before
`project.json` that fails prints its fix and exits non-zero before the file is written.
With `--repo-path` it records that checkout, its `default_branch` read from the checkout's
`origin/HEAD` unless `--default-branch` names one, sets `--git-name` and `--git-email`,
when given, on that checkout with `git config`, links the tracker `--tracker` names once
the access check proves it, and runs nothing else.

A setup has not finished until the project's `brief.md` is written. On a terminal, with
no name, `--slug` or `--repo-path` given, the wizard's first question is a choice of every
setup that has not finished, each with the step it stopped at, plus start a new project;
choosing one continues it from that step, using each answer already given where no flag
gives one and skipping each step already done; one stopped at planning goes straight to
the live setup session on its brief thread. With no unfinished setup, nothing is offered. The wizard keeps each
setup's answers and ended steps off the board, on this machine, in
`<state>/setup/<slug>.json` (`autonomouse.setup_record`; `<state>` is `$AUTONOMOUSE_STATE`,
else `~/.local/state/autonomouse/runs`), and removes the file once the brief is written. A
project recorded with `--repo-path` has no such file.

## brief.md

The project's brief, its own page: why the project exists and what it must achieve, in
markdown. It sits beside `project.json`, which does not change for it. A project may have
none. An optional `# <title>` line comes first; then these seven `## ` headings, in this
order, each followed by its text:

| heading | what goes under it |
|---|---|
| `## Why this project exists` | why the project exists |
| `## Goal` | the goal sentence the human confirmed, as its first paragraph |
| `## Intent` | the intent sentence as its first paragraph: what the creator wants from the project; then, after a blank line, as much as needed |
| `## Who uses it and where` | who uses what the project makes, and where |
| `## How it works` | the system design approach |
| `## How we work` | how the cards get there: wakes, decisions, principles |
| `## Out of scope` | what the project does not do |

A deeper heading (`### `) stays inside its section. The goal sentence and the intent
sentence each end at the first blank line under their heading; either may wrap over
several lines, which are read as one.

`load_brief` (`autonomouse.board.brief`) reads it: the text as written, each section by
its heading, the goal sentence and the intent sentence. A project with no `brief.md`
gives None, and the reader raises nothing. `autonomouse show <slug>` prints the brief
first, and the board screen's project details page (`i`) shows it last, below where the
project stands (its cards, pull requests and wakes), its goal and its settings.
The planner writes it with the human on the project's brief thread, which `autonomouse
project new` opens (`docs/planner.md`, "The brief"), through `autonomouse plan brief
<thread-id> <file>`. That command refuses, and writes nothing, a file that lacks one of
the seven headings above or whose goal or intent sentence is empty, so that a mistyped
heading never loses the two sentences the workers get (`docs/board-schema/plan.md`).
