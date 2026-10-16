# Rules on every job

Your craft rules are the global Claude Code config (`~/.claude/CLAUDE.md`, loaded
automatically) and these skills: `operations`, `project-management` (its `PLANNING-PRS.md`,
`LINEAR-ISSUES.md`, `USER-INVOLVEMENT.md` and `TEST-FIX-LOOP.md`). This file holds only what
is specific to autonomouse.

The orchestrator, planner and super-orchestrator also load `verifying` and may run code review.
A worker loads neither that skill nor code review: the orchestrator checks the evidence a
worker returns, and the `worker-*` agent types (`setup/agents/`) refuse both skills.

The rules below apply to every card on the board. A card's own limits add to them, never
replace them.

Each rule is its own file under `docs/rules/`, which says whom the rule binds, what that
reader does, why, and the failure the rule prevents. Read the file of every rule whose
reader you are; "rules, item N" in the other charters means rule N below.

1. **Write to the board only through `autonomouse` commands** — every agent that writes the
   board: [rules/01-board-writes-through-commands.md](rules/01-board-writes-through-commands.md)
2. **A missing capability is a new card, not a workaround.** — worker, orchestrator:
   [rules/02-missing-capability-is-a-new-card.md](rules/02-missing-capability-is-a-new-card.md)
3. **A PR targets its card's `base_branch`, and its change is safe to merge the instant it
   lands** — worker, planner:
   [rules/03-pr-leaves-base-branch-working.md](rules/03-pr-leaves-base-branch-working.md)
4. **Write your notes after every step, not at the end:** — worker:
   [rules/04-notes-after-every-step.md](rules/04-notes-after-every-step.md)
5. **One PR per card** — planner, worker, orchestrator, super-orchestrator:
   [rules/05-one-pr-per-card.md](rules/05-one-pr-per-card.md)
6. **A card whose fifth attempt fails goes back to the orchestrator** — worker, orchestrator:
   [rules/06-fifth-failed-attempt.md](rules/06-fifth-failed-attempt.md)
7. **Secrets** — every agent: [rules/07-secrets.md](rules/07-secrets.md)
8. **A question for the human is written for someone who has not read the code.** It also
   holds **Decide on evidence; ask the human only what is truly theirs.** — orchestrator,
   and every agent whose text reaches the human:
   [rules/08-questions-for-the-human.md](rules/08-questions-for-the-human.md)
9. **A card's test ends on its own.** It also holds **An agent runs every step of a card's
   test; no step is the human's.** — worker, planner, orchestrator, super-orchestrator:
   [rules/09-a-test-ends-on-its-own.md](rules/09-a-test-ends-on-its-own.md)
10. **Limits serve the goal; they are never hard blocks.** — orchestrator:
    [rules/10-limits-serve-the-goal.md](rules/10-limits-serve-the-goal.md)
11. **Every code change ships with a test in the repository's own suite.** — planner, worker,
    orchestrator: [rules/11-every-code-change-has-a-test.md](rules/11-every-code-change-has-a-test.md)
