# The tier rubric

The human's rule for every tier (2026-09-22): "delegate to capable orchestrators and
workers; do not cheap out on their capabilities, do not underestimate the tasks, do not
overestimate what the agents can do, and do not overspend." A card's size is its tier (the
human's decision, 2026-09-23). Size measures the hardest decision inside the goal, not the
diff: a one-line fix whose cause must first be found is `s` or `m`. The map is the config's
`tiers` (`autonomouse config show`), with the defaults below; a card bigger than `m` is
split.

| the card | size | tier |
|---|---|---|
| the goal names the exact edit, nothing to decide | `xs` | `sonnet/xhigh` |
| choices inside a clear goal | `s` | `opus/xhigh` |
| a goal to interpret, an architecture to choose, a cause anywhere | `m` | `fable/high` |
| a charter card: it changes a file that one of the project's `charter_files` globs matches (`autonomouse project show <slug>`) | `s` at least, `m` when you judge the work beyond Opus | per its size |

`sonnet/xhigh` is the floor under every card, and the charter row is a floor over Jev's
answer and your own override: a card that changes a file one of the project's
`charter_files` globs matches is at least `s`.

**Jev advises; you decide.** Jev asks one question per ready card, its size. The human's
rules (2026-09-23): "Jev's choices are for size", and "What Jev decides is a categorization.
The orchestrator has all rights to override them. The orchestrator is a project manager."
With `jev.enabled` on, your prompt carries each card's size and tier: Jev's size when
usable, else the card's own; Jev's fourth answer, `split`, means the goal needs more than
one worker turn and reaches you as "Split <id>: Jev sized it larger than m". You may set a
different size, split, merge or cancel the card, or run it anyway, whenever the project is
better served, noting why with `card edit --note` (and `--size`). With Jev off, and for
every card you create mid-wake, you answer size yourself. Record each answer of yours with
`autonomouse run note <run-id> --jev '{"question": "size", "card": …, "source":
"orchestrator", "answer": …, "confidence": null, "applied": …}'`. A resumed or reopened card
gets no new answer; its `assigned` tier stands unless you raise it.

A worker that does not follow `docs/worker.md` gets a larger model on its next `card start`,
which records it, or a tester worker that runs the card's test in its worktree and reports
what it saw (`docs/orchestrator/tester-worker.md`); never a deterministic gate, because a
worker keeps every tool its card needs.


While `autonomouse config show` reports a Fable usage-limit fallback, use `opus/xhigh`
for every Fable worker, including in-progress cards whose assigned tier still names Fable.
The controller returns to the configured tier after the recorded reset.
