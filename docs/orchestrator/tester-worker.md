# The tester worker

A tester is the standard second opinion on a card: a second agent that checks the card's
work in its worktree and reports what it saw.

## When you spawn one

Spawn a tester for a card when one of these holds:

- its worker reports Finished, and the verification note (`handoff.last_step`) does not
  show both the card's `test` run and what it printed;
- you cannot tell from the verification note whether the card's goal is met;
- you want the state of the card's test before you decide the next step of a card whose
  worker reported Stopped or that you stopped with TaskStop;
- a worker you sent a tester's findings to reports Finished again.

## How you spawn it

Spawn one tester per doubted card with the Agent tool, while the watch goes on
(`docs/orchestrator/while-workers-run.md`). It takes the slot its card's worker held, and it
stays out of the watch's `AGENTS`, because it writes no note. Its tier is `sonnet` with
agent type `worker-xhigh`, the floor. When reading the result of the card's `test` needs
judgment, such as a test step that names a file to read against the goal rather than a
command's exit code, it gets the card's own tier (`model` and `agent_type` in the card's
`assigned`) when that model is larger than `sonnet`, as the model of every tier above the
floor in `docs/orchestrator/tier-rubric.md` is. Its completion notification is its report.
A tester that has gone quiet for long is yours to stop with TaskStop when you judge it
will send no report; write why with `autonomouse card edit <id> --note`.

Its prompt is this, filled in, with the card's goal word for word, `<REPO>` the REPO your
start message names and `<the invocation>` the board invocation it names:

```
The card's goal: <the card's goal>

You are the tester for this card. Your charter is the section "The tester" of
`<REPO>/docs/orchestrator/tester-worker.md`; the card is `<absolute card path>`; its
worktree is `<the card's worktree>`; its base branch is `<the card's base_branch>`; the
board command is `<the invocation>`; the run id is `<run id>`.
```

## The tester

The tester's goal: tell the orchestrator whether the card's work meets the card's goal,
from checks it runs in the card's worktree.

Its charter is this section, `REPO/docs/rules.md` and the card. `REPO` is the part of the
charter's path before `/docs/`, never the worktree. It reads the card with `<board
command> card show <id> --json`, the id being the card file's name without `.json`; the
card's `pr` is its PR, and its `handoff.last_step` is the worker's verification note. The
run id names the wake the tester serves; it writes nothing under it.

It reads and runs, and writes nothing: no commit, no push, no edit to a file in the
worktree, no board write. Every command of a check runs with `AUTONOMOUSE_HOME` set to one
temporary folder that the tester makes outside the worktree before its first check, so no
test reaches the real board. It runs `git status` in the worktree before the checks and
after them, and names any change a check made, which it leaves in place. It runs every
command in the foreground, with a Bash `timeout` of up to 600000 ms on one that may run
past two minutes, because a background job dies when its turn ends.

In the worktree, it runs these checks:

1. **The card's test**: every step of the card's `test`, as written.
2. **The project's own checks**: the ones the testing step of `REPO/docs/worker.md` names
   for the change.
3. **The PR's code and CI**: the worktree's `git rev-parse HEAD` is the PR's head commit
   (`headRefOid` in `gh pr view <pr> --json headRefOid`), and `git status` before the
   checks shows no change to a tracked file. The CI runs on that commit, read and waited
   for as the ci step of `REPO/docs/worker.md` says, each end in success; a commit with no
   run passes this part, as that step's "no checks" does.
4. **The PR's diff**: the PR is open on the card's base branch, and its diff (`gh pr diff
   <pr>`) meets item 11 of `REPO/docs/rules.md`: a code change adds a test in the
   repository's own suite that covers the behaviour the card's goal names, and the
   verification note shows that test failing on the base branch's code. The tester reads
   that failure from the note; outside the card's test, it runs nothing on the base
   branch's code.
5. **The goal**: the diff and what checks 1 and 2 printed make each part of the card's
   goal true.

Its report starts with its verdict: **Verified** when every check passed, else **Not
verified**. Then it lists each check: what it ran or read, what that printed or held (the
lines and exit code that decide it), and whether it passed. A check the tester could not
run, because its environment kept a command from running (a missing tool or login, a
command that outran its timeout), failed, and the report says the tester could not run it,
and why. A command that ran and failed, or a card with no PR, is a failed check of the
work. A Not verified report then names each finding in one sentence.

## What you do with the report

A report counts when it starts with Verified or Not verified and its list agrees with that
verdict. Take the first case that fits:

1. **No report that counts**: you decide whether to respawn the tester with the same
   prompt, and write why with `autonomouse card edit <id> --note`; when you do not, the
   note names the card's next step.
2. **Verified**: `autonomouse card finish <id> --pr <pr> --last-step "<handoff.last_step>
   Tester: <the tester's report>"`.
3. **Not verified, and every failed check is one the tester could not run**: the report
   says nothing about the work. Fix what kept it from running, when you can, and decide
   whether a tester again serves the card better than deciding its next step by step 6 of
   "A wake" in `docs/orchestrator.md`; write that choice and its reason in the card's note,
   with the report.
4. **Not verified, and step 6 of "A wake" already names the card's next step**, such as a
   red base: the card takes that step, with the findings in its note.
5. **Not verified, and you have not stopped the card's worker with TaskStop**: you decide
   whether to resume the worker with the findings, the first time or again, or to send the
   card to `todo` by case 6, and write why with `autonomouse card edit <id> --note`. You
   resume it by `SendMessage`, its `agentId` as the address, with the findings; it takes
   the tester's slot back. A worker that `SendMessage` cannot reach goes to case 6.
6. **Not verified, in every other case**: the card goes back with `autonomouse card move
   <id> --to todo --note "tester: <findings>"` and starts again by steps 4 and 5 of "A
   wake", at a larger model as the last paragraph of `docs/orchestrator/tier-rubric.md`
   says.

A tester's report is never a reason to add a check to the code of the `autonomouse`
commands: a doubted card gets a tester, never a deterministic gate.
