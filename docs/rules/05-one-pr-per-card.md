# Rule 5. One PR per card

**Reader:** the planner, who fixes the card; the worker, who opens its one PR as a draft
and, on the wake's shared branch, merges it there; the orchestrator, who finishes the card; and the
super-orchestrator, the one agent that merges into the default branch, and only the PRs
the owner told it to merge.

## What the reader does

One PR per card — the card, not the issue, is the PR-sized unit, and it is fixed before any
worker sees it. The card carries the PR plan, the tracker issue and the test plan in one
file: goal, limits, test, size, `depends_on`. The card's own test passed and no check on the
PR is red before it is finished; when the repository has no checks yet, the verification
note records that as unverifiable and the card still finishes. A PR merges into the default
branch only when the owner merges it, or tells the super-orchestrator to merge it, which
then runs `autonomouse github merge` in that session (`docs/super-orchestrator.md`).
Nothing asks the owner on Telegram for a merge, and no setting, flag or GitHub auto-merge
merges anything on its own. The one other kind of
PR merge an agent makes is a worker's merge of its own green PR into the wake's shared
branch `wake/<run id>`, which never touches the default branch. A card takes that branch
only after the orchestrator has written on it why none of the reshaping moves (re-split,
one owner, fold) removes its conflict with the cards that share its files
(`docs/orchestrator/conflict-check.md`), and the branch's one final PR into the default
branch merges like any other, only when the owner merges it or tells the super-orchestrator
to. A card whose
goal produces no code and no doc (a spike, a judgment for the human) ends with its findings
in its notes and no PR.

## Why

The owner decides what reaches the default branch. In the owner's words: "I should never be
asked on Telegram for a yes to merge. Only two mechanisms exist to merge any PR - 1. I merge
them. 2. I tell a super-orchestrator to merge them." One card, one PR keeps each change small
enough for one review sitting, with its plan and its test in one place.

## The failure it prevents

An agent merging work the human has not reviewed, such as a worker that merges a throwaway PR
into the default branch.
