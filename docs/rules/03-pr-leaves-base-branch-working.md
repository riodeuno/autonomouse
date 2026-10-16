# Rule 3. A PR targets its card's `base_branch`, and its change is safe to merge the instant it lands

**Reader:** the worker, who opens the PR, and the planner, who splits a feature into several
PRs.

## What the reader does

A PR targets its card's `base_branch`, and its change is safe to merge the instant it lands,
whatever order other cards' PRs land in: where the change plugs into another card's work, it
sits behind a feature flag or stays inert until something else calls it. A feature that
needs several PRs goes behind a flag; the PR that turns the flag on goes last, on a card that
depends on the cards whose work it turns on. A branch is brought up to date with its base by
fetch-and-merge only, never rebase, and every PR is squash-merged.

## Why

The project's default branch is always releasable: on its own, and together with everything
that merged since a branch was cut (`docs/design/DESIGN.md`, decision "Every PR must leave
main working").

## The failure it prevents

PRs that each pass alone and break their target together.
