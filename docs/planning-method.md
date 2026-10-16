# How planning is done

Your craft rules are the global Claude Code config (`~/.claude/CLAUDE.md`, loaded automatically) and these skills: `communication`, `goal-discovery`, `prompting`, `rca-discipline`, `software-design` (its `DESIGN-DOC.md`), `project-management` (its `PLANNING-PRS.md`, `AUTOMATED-TESTING.md` and `USER-INVOLVEMENT.md`). This file holds only what is specific to autonomouse.

## The shape the human wanted

The human first gets the whole thing in plain English, its context built from zero as
`USER-INVOLVEMENT.md` in the `project-management` skill says.

1. **Then every task, each in two to three plain sentences.** Present the whole list once so the human can see the shape.
2. **Then, per task, in this order:**
   - goal and size together ("is this the right thing, and is it small enough?")
   - limits ("what must the helper not do; what must it keep?")
   - test — the four lists and their per-step evidence compress into the card's one `test` string, settled at planning time.

   Goal, limits and test are agreed once, in this order, with the planning session that writes the card; the worker that runs the test never renegotiates them.
3. **Every answer refines `docs/design/DESIGN.md`'s Decisions table immediately**, one row per steer.

## Corrections the agent received

- Do not decide from what is on this machine; the system installs on a bare server.
- The planner never plays orchestrator: it writes instructions, cards and plans; a separate
  session executes.
- The whole project is built the way the system builds everything: cards on the board, an
  orchestrator session, workers per card.
- Ask "is every aspect planned, can it be broken down further, are there fringe items?"
  before calling a breakdown done.
- Plan ahead for conflicts: no card waits on another for a file both would change.
  `docs/planner.md`, "Cards that share a file", holds the rule.
- A missing capability becomes a new card, with no workaround; do not block on it. Use a
  placeholder and flag it.
- Every PR leaves the project's default branch working; the wiring PR goes last.

## The size rule

Jobs are xs / s / m. "m" is the ceiling. Bigger is split during planning, never on the board.
The whole point of clear goals + limits + tests is that a helper can loop (write → test → CI →
review, iteration by iteration) without a human; that only works when the job is small.

The planner does the thinking, in the thread with the human, so that every card it writes is a
simple task an Opus worker finishes in one turn. A card that needs more thought than that is
not a card yet: it goes back to planning.

So xs and s are the normal sizes, and m is rare. A card the planner would still size m after
that thinking is first tried as two or more s cards, each with its own goal and test; it stays
one m card only when no split leaves each part with a test of its own. A card that would be
larger than m is always split.
