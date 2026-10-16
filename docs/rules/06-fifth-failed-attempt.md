# Rule 6. A card whose fifth attempt fails goes back to the orchestrator

**Reader:** the worker, who reassesses and reports, and the orchestrator, who decides what
follows.

## What the reader does

A fifth failed attempt (`iteration` 5), or the same cause failing two attempts, is the
worker's cue to reassess. The worker stops spending attempts on that route, writes on the
card what it sees (the last failure, its cause, and what it thinks the card needs), and
reports. What follows is the orchestrator's decision, written on the card: a larger model, a
tester worker, a replan that rewrites the card's limits, test or notes, or a split. The goal
never softens. The count marks the moment to reassess; it never stops the card on its own.
It is not a decision for the human unless the orchestrator cannot see how to go on:
non-convergence is a planning failure the board absorbs.

## Why

The card's goal is what the work is for, and a route that keeps failing says the plan is
wrong, not the goal. A number such as five attempts is a default the orchestrator reads, not
a wall the card hits: a worker that does not converge is judged on evidence by the
orchestrator, who writes what it saw and decides. The count exists to stop spending time and
tokens on a route that does not converge; the review cap of rule 9 works the same way.

## The failure it prevents

Two failures, one on each side of the count. A worker spends its remaining attempts on a
route that already failed for the same cause. Or a card stops at a count with its goal unmet,
or has its goal softened to make it pass.
