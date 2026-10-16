# Rule 2. A missing capability is a new card, not a workaround

**Reader:** the worker, who finds and names the missing capability, and the orchestrator,
who files the card.

## What the reader does

The worker names the missing capability in its report (goal, limits, test); the orchestrator
creates the card — the finder never files. Nobody waits for it: continue with a placeholder
or behind a flag and finish your own PR. Stop only when continuing is truly impossible, and
say so in your notes.

A finding that names a way a program the human runs (the board viewer, an `autonomouse`
command, the installer, a wake) can crash, stop or show a wrong state is a new card too,
filed at once with its evidence: the worker proposes it in its report with goal, limits and
test, the orchestrator files it on that report, and it is never a concerns entry (a line in
a `concerns.md` notes file); a finding that is comments-only or style goes in the worker's
report as an unrelated problem.

The orchestrator files a card a worker proposes only when the worker's reason for it is in
the goal and intent of the project, which the Goal and Intent sections of the project's brief
state (`projects/<slug>/brief.md` on the board, printed by `autonomouse show <slug>`), or the
project's `goal` when it has no brief (`autonomouse project show <slug>`); the filed card's
goal says how the card serves the project, quoting the part of that goal or intent it serves.
A proposal whose reason is not in the project's goal and intent gets no card: the
orchestrator lists it under reported problems in its final message, for the human.

## Why

Each job stays small and independent, and every change to the board goes through the one
write path (`docs/design/DESIGN.md`, decisions "A missing capability is never worked around"
and "Jobs stay independent"). A card exists to serve its project, so a proposed card that
serves no part of the project's goal and intent is work the human never asked for.

## The failure it prevents

A card filed that serves the finder's task and no goal of the project; a board write made
by hand because its command did not exist; a job that stops and waits for another job's
card; a crash that a finding named and left in a notes file, with no card to fix it.
