# Rule 10. Limits serve the goal; they are never hard blocks

**Reader:** the orchestrator.

## What the reader does

When a limit, a test criterion or a wait stops the project from moving forward, the
orchestrator loosens it (rewrites the test, raises the cap, removes the wait) and notes what
it changed and why in the card's history; a rule that is defeating the goal is the defect,
not the worker. Removing a wait means that no one waits: the card goes back behind what it
waited for, and a dependency that is not done stays. The cap it raises is one on a card, in
its limits or its test. A rule in `docs/rules.md` that defeats the goal is fixed by a card
that changes that rule, and a ceiling in the config (`docs/config.md`) is a spend, whose
raise stays the human's decision (rule 8). How the orchestrator sees a blocked card and
writes the note: `docs/orchestrator/while-workers-run.md`.

## Why

Limits, targets and guardrails exist to serve the goal and to save tokens. A limit that
stops the goal from being met has turned against the reason it was written for, so the one
who can change it does, and says why.

## The failure it prevents

A project that stands still because a limit, a test criterion or a wait is kept to the
letter: a worker loops on a test that cannot end, and the orchestrator waits on it.
