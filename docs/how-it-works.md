# How the board works

A **project** is one repository that the system works on. Each project has a goal, a
priority (a lower number is picked first) and the local checkout that worktrees are made
from. A project is **done** once it is marked so: you mark it with `autonomouse project
done <slug>`, or a wake marks it by itself (wake step 3). A done project gets no wake, no
pull-request sweep and no follow-up, and the board lists it last.

A **card** is one job in a project, sized to one pull request. It has a goal (what is true
when the card is done), limits (what the worker must keep), a test (the checks that prove
the goal), and the cards it depends on. Each card sits in one stack:

| stack | meaning |
|---|---|
| `backlog` | set aside: the human decides later whether it is worked; `card new --backlog` puts a card here, and so does `review improve` for each change it proposes |
| `todo` | where `card new` puts a card; ready once every card it depends on is `done` |
| `in_progress` | a worker holds it |
| `decision_needed` | it waits on a question only the human can answer |
| `done` | finished: its test passed and its pull request is open with green checks; it merges when the human merges it or tells a super-orchestrator to merge it (`autonomouse github merge`). A card whose goal produces no code ends here with its findings and no pull request |
| `cancelled` | dropped, with a reason |

A **wake** is one run of `autonomouse run`:

1. First it checks the kill switch. While the file `~/.config/autonomouse/PAUSE` exists, it
   starts nothing.
2. It runs the planner on every planning thread that has an unanswered message from the
   human.
3. One machine works on one project at a time. If another wake is still running, this
   wake starts nothing and exits. Otherwise it marks every finished project done: a
   project with at least one card, every card `done` or `cancelled`, and every `done`
   card's pull request merged (a card with no pull request counts as merged; it asks
   GitHub once per `done` card, until the project is marked). When every project is done,
   the wake removes the three crontab lines and says so: there is nothing left to wake. A
   new `todo` card on a done project makes it a working project again, and
   `autonomouse card new` puts the three crontab lines back if they are missing.
4. It picks the project with work that has the lowest priority number: ready `todo`
   cards, or `in_progress` cards whose session died. If you give project B priority 0 while
   project A is half done, the next wake works B, not A; once B has no work left for the
   agents, the wake goes back to A. Among projects of equal priority, one that a stopped
   or crashed wake left claimed comes first.
5. It starts one Claude Code session, the orchestrator, with `claude -p`. The
   orchestrator follows `docs/orchestrator.md`: it starts one worker per card, each in its
   own worktree on a branch named by the kind of change the card makes, such as
   `feat/board-search`, `fix/viewer-crash-on-access`, `docs/install-guide`,
   `test/sweep-fork-prs` or `chore/drop-auto-merge`. Each worker follows `docs/worker.md`:
   it writes the code, runs the card's test, opens the pull request, waits for its checks
   and answers review comments. It writes its notes on the card after every step, so a
   later session can continue from them.
6. A question only the human can decide moves the card to `decision_needed` and sends a
   Telegram message (with Telegram off, none: you answer on the board screen). A reply to that
   message answers it, and the card goes back to `todo`.
   The kinds only the human decides are the ones `docs/rules.md`, item 8, lists: a
   credential, a spend, a fork in the goal, a judgment of taste, a goal sentence the human
   has not confirmed, and every other kind principle 6 of your global `CLAUDE.md`
   names, such as deleting something or a step that cannot be undone.
7. When the session ends, the wake records its result, releases the project and commits
   the board.

Every agent follows `docs/rules.md`. Agents change the board only through `autonomouse`
commands. A card gets five attempts; after the fifth failure the orchestrator splits or
rewrites it.

A **planning thread** turns a request from the human into new cards. The human opens one
from the board screen (`p`), with `autonomouse plan open`, or by sending the Telegram bot a
message. A planner session (`docs/planner.md`) talks the request through with the human and
writes the cards it agreed into `todo`, where the next wake picks them up; a card the human
sets aside to decide later goes to `backlog`. A new project's first thread, which
`autonomouse project new` opens, writes the project's brief instead, and writes no card.

The JSON shapes of projects, cards, run records and planning threads are in
`docs/board-schema.md`. The design decisions and their reasons are in
`docs/design/DESIGN.md`.

### Where sessions run

The wheel ships the agents' charters (`docs/`) inside the package. Every Claude Code
session autonomouse starts, the orchestrator of `autonomouse run` and the planner of
`plan step`, `plan join` and `plan new`, runs in the folder that holds them. From a
checkout, that folder is the repository. From the wheel, it is the installed package. The
session's prompt names its charter's absolute path and the command line that runs the board
commands.

Each orchestrator session also gets a `SessionEnd` hook, which writes how the session
ended (`end_reason` on its run record). The hook ships inside the package
(`autonomouse.session_end_hook`), and `autonomouse run` passes it to claude with
`--settings`, so it runs from a checkout and from the wheel alike.
