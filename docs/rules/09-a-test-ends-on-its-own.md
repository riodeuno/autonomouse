# Rule 9. A card's test ends on its own

**Reader:** the worker, who runs the test; the planner, who writes the test; the
orchestrator, who rewrites a test before it starts the card; and the planner, the
orchestrator and the super-orchestrator as reviewers of their own work.

## What the reader does

A card's test ends on its own. It names checks that finish by themselves: a command that
exits with a result, or a file that holds a named thing. A reviewer's silence is never a
check: "the review passes return no findings" and "review until clean" have no end, because
an adversarial reviewer always finds something. A worker runs no review of its own work:
the orchestrator checks the evidence the worker returns. A
review the orchestrator, the planner or the super-orchestrator runs on its own work (review
agents, a skill's review passes) is at most two rounds. A finding the base branch already
has, about text the work did not change, is out of scope: it goes in the report, never in a
fix. After the second round, the reviewing agent fixes what it can, and every finding still
open goes in its report, never into notes as accepted. The cap is a count of rounds, not a
timer or a token budget.

**An agent runs every step of a card's test; no step is the human's.** What an agent on this
machine can do, the worker does: it sends a message through the bot, runs a command, drives
a screen from a test (as a Textual pilot drives the board screen), reads a file. What only
the human can produce, such as a reply from the human's own Telegram account, is a fixture:
the worker writes it in the shape the real input has, and the test feeds it in. The human's
own use of the feature after it merges is not a step of the test, and no decision asks the
human for it. The planner writes every test this way (`docs/planner.md`), and the
orchestrator rewrites a test that gives the human a step before it starts the card
(`docs/orchestrator/bounded-tests.md`).

## Why

A test that ends on its own lets the work finish and be judged. The review cap stops the
spend on review rounds that do not converge, and the goal stays whole: what the rounds could
not settle is reported, so the plan can change. Code reviews run by workers cost a large share of the tokens, so
the orchestrator checks a worker's evidence in their place. Every feature can be reached
from this machine, so a test step handed to the human only adds a wait; what the machine
cannot reach is mocked.

## The failure it prevents

A review loop with no end, and a test that waits on the human: a test that asks for review
passes with no findings can run for dozens of rounds and never open a PR, and a test that
asks the human to watch a screen, send replies or run a live command only adds a wait.
