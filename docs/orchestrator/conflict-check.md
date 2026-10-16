# The conflict check: every open PR mergeable, green and answered

Every open PR autonomouse opened stays mergeable and green, and its reviewers get an answer;
resolving a finding is never a decision for the human. Every wake runs the check at its
start, after each `card finish` and at its end. A wake whose prompt names no card exists
only for the check's findings.

**The sweep.** `autonomouse github sweep <slug> --json` lists every open PR whose head
branch is one a card of the project records, or starts with `card/` (a card branch named
before 2026-09-29) or `wake/`, with its `card_id` (`-` for none), `mergeable`,
`runs`, `unanswered` review comments and `conflicts`. An entry whose `finding` is true
(conflicting with its base branch, conflicting with the default branch in the files
`conflicts` names, failed runs or an unanswered comment) needs work. GitHub's `mergeable`
compares a PR only with its base branch, so a stacked PR reads `MERGEABLE` while it
conflicts with the default branch; the sweep finds that conflict itself with `git
merge-tree`. `UNKNOWN` and `pending` wait for the next check; another project's PR in the
same repository (`<project>/<id>`, `<project>/-`) is never a finding. At the wake's end no next check comes: each PR still `pending` or
`UNKNOWN` goes into the `run end` summary by its number, `run end` follows at once
(`docs/orchestrator.md`, "A wake", step 7), and the next wake's check at its start reads the
result; never wait for it in a background task, because ending your turn ends the session. A
finding that a card already names gets no second card while that card is not `cancelled` and
its PR has not merged. By the finding's card:

- **`in_progress`**: the PR is its worker's.
- **`done`**: reopen the card (below).
- **Any other stack**: not moved. A `cancelled` card's open PR is closed the same way as at
  its cancel, `gh pr close <pr> --comment "<the cancel reason>"` without `--delete-branch`
  (`docs/orchestrator/splitting-and-replanning.md`, "Cancel"). For every other stack, its
  next worker brings the PR up to date, and the check
  after its `card finish` reopens it when the finding remains.
- **No card** (a wake's final PR): at most two cards fix it ("Cards that share a file:
  reshape first, the shared branch last", below).

**A run GitHub never started.** A GitHub Actions run whose jobs ran no step never started:
GitHub gave it no runner, as when the account's Actions minutes run out. The sweep reads it
as not run, never as failed, and its `note` says GitHub never started it. For each run
GitHub never started on a PR's head, and for such a run only, the worker's verification
note stands in for its green check when it names the PR's head SHA and records the
commands of that run's workflow, from the project's own `.github/workflows/`, run locally
on that head, with their output, every one passing (`docs/worker.md`, **ci**). Every run
that ran a step needs its green check as before.

## Reopening a done card

`autonomouse card move <id> --to todo --note "reopened: <finding> on PR <url>" --next-step
"reopened: <what the worker does>"` puts the card back in `todo` at position 0 with its PR.
A PR with several findings reopens its card once, one `reopened:` before all their parts.
By finding:

- **A conflict with its base branch** (`CONFLICTING`, or `conflicts` on a PR whose base is
  the default branch): the note names `#<n>`, the merged PR that touches its files; the next
  step is to merge `origin/<baseRefName>` (`gh pr view <url> --json baseRefName`) into the
  card's branch, resolve, rerun the card's test and push to the PR.
- **A conflict with the default branch** (`conflicts` names files) on a stacked PR, one whose
  `baseRefName` is another branch: merging `origin/<baseRefName>` alone leaves this conflict,
  and every later sweep would reopen the card again. This step replaces the one above when
  the PR is also `CONFLICTING`. The note names the files `conflicts` lists. `autonomouse
  github base-branch <id> --json` answers the card's base from now on, `<base_branch>`: the
  default branch once every card it stacks on has reached it. The next step has the worker
  merge into the card's branch, fetch-and-merge with a merge commit each and never rebase,
  first `origin/<base_branch>` when it is not the default branch, then
  `origin/<default_branch>`, resolving the files `conflicts` names; then point the PR at
  `<base_branch>` when it targets another (`gh pr edit <url> --base <base_branch>`), rerun
  the card's test and push. For example: `--next-step "reopened: merge
  origin/feat/board-search, then origin/<default_branch>, into feat/board-filters; resolve
  src/x.py; rerun the test; push"`. The base comes in before the default branch, the
  base-to-tip order in which a stack merges. So start the
  reopened cards of one stack from base to tip: a card whose `<base_branch>` is another
  reopened card's branch starts after that card's `card finish`, and merges a base that
  already holds the default branch.
- **Failed runs**: the next step names each failed run's link (`autonomouse github checks
  --repo <owner>/<name> --head-sha <head_sha> --json`): fix, rerun the card's test, push.
- **Unanswered review comments**: read them with `autonomouse github comments --repo
  <owner>/<name> --pr <number> --json`, skipping any that a later reply naming one of the
  PR's commits answers. Judge each yourself, or have a probing worker read the code and
  report its judgment, changing nothing. A comment asking for a change the card's goal or
  the rules warrant becomes a fix card in `todo` (`docs/orchestrator/new-cards.md`) whose
  goal, limits and test name the comment's link, with `--depends-on <id>` so it builds on
  the card's branch. Every other comment gets the reopen, its next step naming those
  comments' links (each one's `url` in the output): reply to each with the reason, change
  nothing else.

A reopened card starts as a card you file mid-wake, with its PR's `baseRefName` as
`--base-branch`, or the `<base_branch>` above for a conflict with the default branch. When
PRs can no longer merge because their conflicts resolve only against each other, file cards
that make them mergeable.

## Cards that share a file: reshape first, the shared branch last

The human's intent: "The intention is to have easy to review and small changes. [...] The
orchestrator has the authority to decide on this as the project manager." You use that
authority in the order below: a card with no real dependency on another gets its own small
pull request, squash-merged straight into the default branch on its own.

**Reshape first.** Before you start a card, find the cards it shares a file with:
`autonomouse card overlaps --project <slug> --files …` over its `files` prints the cards not
yet `done` or `cancelled`, and it prints no `done` card, so also compare the `files` of each
`done` card that has an entry in the sweep above. When the card shares a file with a card
that is ready (in `todo`, every dependency `done`), `in_progress`, or `done` with its PR open,
take the first of these moves that both removes the overlap and leaves every card one worker
turn (`docs/orchestrator/splitting-and-replanning.md`, "Reshape"). A move edits only cards in
`backlog` or `todo`, the card you are about to start among them; a card `in_progress` or
`done` stays as it is. A card in `backlog`, or in `todo` with a dependency not yet `done`, is
checked when it starts.

1. **Re-split**: each card gets files of its own.
2. **One owner**: one card carries every change to the shared file, and the other card's
   limits say it leaves the file alone.
3. **Fold**: the shared part becomes one card that the others depend on.

**The default branch, for every other overlap.** Cards that still change the same file after
the moves, and do not depend on each other, each start on the project's default branch with a
PR of their own, squash-merged into it on its own (rules, item 3). This holds whether the
other card is ready, `in_progress` or `done`, everywhere outside the one case below. When the
first to merge leaves another's PR `CONFLICTING`, the conflict check above acts on it by the
card's stack, as on any conflict: a `done` card is reopened, and its next worker merges
`origin/<default_branch>` into its branch (fetch-and-merge, never rebase), re-runs the card's
test and pushes. This is the normal path: bounded, one-time friction, and never a reason for
the shared branch or for one card to wait for the other.

**The shared branch, for one case.** The one case is the human's "short, scoped high-conflict
refactor phase where many cards must touch the same seam at once": several ready cards must
each change the same file, as when each moves one part of a large file into a new file and so
still changes the large file, and no move above applies to them. For those cards only:

1. Write on each of them, with `autonomouse card edit <id> --note "…"`, why re-split, one
   owner and fold each did not apply.
2. Run `autonomouse run interim <run id>`. It creates `wake/<run id>` on `origin` from the
   default branch and records it as the run's `interim_branch`; run again, it changes
   nothing and prints the same branch, so a second group of such cards in this wake joins
   the same branch.
3. For each of them, first run `autonomouse github base-branch <id> --json`: a `base_branch`
   it answers other than the default branch joins the card's `also_merge`, with the
   branches that answer's `also_merge` already names; these are the branches its worker
   merges into its branch first (`docs/orchestrator.md`, "A wake", step 4). Then start the
   card with `--base-branch wake/<run id>`.

A resumed card keeps the base branch it has.

**The workers merge into the shared branch.** Each worker on `wake/<run id>` merges its own
green PR into it (a `CONFLICTING` final PR's fix card is the one exception, under "The final
PR"), after it has merged `origin/wake/<run id>` into its own branch and resolved the
conflicts there, and a worker still in progress merges `origin/wake/<run id>` into its own
branch at its ci step (`docs/worker.md`, "Before the loop" and "The loop"). You merge
nothing. `gh pr list --base wake/<run id> --state merged` lists the PRs merged into the
branch, and each worker's verification note names the commit its merge made there. Once a
PR has merged into the branch, each conflict check reads the branch's newest finished run,
`gh api "repos/<owner>/<repo>/actions/runs?branch=wake/<run id>&status=completed&per_page=1"`;
a failed one is a red base (`docs/orchestrator/red-main.md`), whose fix card takes
`--base-branch wake/<run id>`, unless GitHub never started it (`gh api
"repos/<owner>/<repo>/actions/runs/<id>/jobs" --jq '[.jobs[].steps | length]'` prints only
zeros), which shows nothing about the branch.

**The final PR.** At the wake's end, open the final PR of each shared branch that a PR merged
into and that has no open final PR yet, as a draft, `gh pr create --draft --base
<default_branch> --head wake/<run id>`, titled in the worker's PR title form (`docs/worker.md`, **ci**),
`type(scope): description`, with one type for the whole branch: the merged PRs' type when
they share one, else `feat` when any of them is `feat`, else `fix` when any is `fix`, else
`chore`; the description names what the merged cards do together. The merged
PRs go in its body, and record it with `autonomouse run note <run id> --interim-pr <url>`,
where `<run id>` is the one the branch's name carries. That one PR merges only when the
owner merges it or tells the super-orchestrator to merge it (`docs/super-orchestrator.md`). Later sweeps show it
as a `wake/` entry with card `-`. Its findings get at most two cards. Each is filed with `card new` as a card you file
mid-wake and started with `card start … --base-branch wake/<run id>`, since `card new` takes
no base branch. A `CONFLICTING` or red final PR is a red base for the cards on the branch
(`docs/orchestrator/red-main.md`): it gets one card, whose goal names the branch and is that
the final PR is mergeable into the default branch with every check green. When the final PR
is `CONFLICTING`, that goal has the card's worker merge `origin/<default_branch>` into
`wake/<run id>` itself (fetch-and-merge, a merge commit, never rebase), resolve the conflicts
on that branch, re-run the tests of the cards on it and push the branch, with no PR of its
own to squash. Squashing a PR into the shared branch keeps the branch's old common ancestor
with the default branch, so the final PR would stay `CONFLICTING`. When the final PR is only
red, the card's worker merges its own PR into the branch. Unanswered comments, judged as a
card's are, get the other card: its goal makes each change a comment warrants and replies on
the final PR to every other comment with the reason, and its worker merges its own PR into
the branch. A wake that ends with a red shared branch says so in its summary.

**A card whose dependency has not reached the default branch.** A card that depends on a
`done` card whose work has not reached the default branch starts on the base `autonomouse
github base-branch <id> --json` answers: that card's branch, or the shared branch its PR
merged into, while that branch has not merged into the default branch; with several such
dependencies, the answer's `also_merge` names the other branches. Its PR stacks on that base.
The dependency decides this base, and a dependency alone never calls for making the shared
branch.
