# Cards in flight

Your goal here: no card waits on a dependency its goal does not need, every running worker
works from its card's newest goal, limits and test, and a worker's question that the board
or the code already answers gets its answer from you.

## A dependency the goal does not need

At wake start, before you fill the slots (`docs/orchestrator.md`, "A wake", step 3), read
each `todo` card of the project whose `depends_on` are not all `done`, and each of those
dependencies. A dependency stays when the later card needs something the earlier card
creates: a function, a file, a field or a fact, such as the green base a red base's fix
makes (`docs/orchestrator/red-main.md`). Otherwise it guards only a shared file, or nothing,
and you remove it with one `card edit` on the later card:

```
autonomouse card edit <id> --depends-on <kept> --note "dropped <dep>: <why the goal does not need it>"
```

with one `--depends-on <kept>` for each dependency that stays (the flag replaces the list),
or `--clear-depends-on` in their place when none stays. The one note names every dropped
dependency with its reason, `dropped <dep>: <why>; dropped <dep>: <why>`. The later card's
need alone decides: a free slot is never a reason to drop one.

The later card then starts in this wake the way a card you file mid-wake does
(`docs/orchestrator/while-workers-run.md`, point 3). When `autonomouse card overlaps` shows
it shares a file with another open card, it starts as `docs/orchestrator/conflict-check.md`
says. Its worker's prompt (`docs/orchestrator.md`, "A wake", step 5) also names each dropped
dependency whose work is not merged, and says that a placeholder or a flag stands where that
work would plug in (rules, item 2).

## An edit to a running card

On each Monitor event and at each expiry (`docs/orchestrator/while-workers-run.md`, point
1), run `autonomouse card show <id> --json` for each `in_progress` card with a worker of
yours that is running or ended on a question you are answering. For each history row that
starts `edited:`, names `goal`, `limits` or `test`, and is dated after the worker's start
(`assigned.picked_up_at`), send that worker the card's goal, limits and test as the card now
holds them, all three, by SendMessage, once per row: a row you have relayed is never sent
again. SendMessage also resumes a worker whose turn has ended, such as one that reported a
question. The worker's own re-read of its card at the start of each phase (`docs/worker.md`)
is the second path for the same text.

## A worker that does not know what its card asks

A worker's report can show that it does not know what its card asks: it puts a genuine
question about the goal. The stuck signals of `docs/orchestrator/while-workers-run.md`
(`repeat`, `climb`, `wait`) are a different case. A confused worker's question is yours to
answer first, as a project lead answers before escalating. This is step 6's decision gate
(`docs/orchestrator/decision-gate.md`) for a question about the card, and the answer goes
into the card itself, so every later worker reads it too.

Read the card, the worker's report and the codebase. When the answer is already there — in
the card's goal and the `Why:` it opens with, a sibling card, an existing file or test —
write it into the card's goal, limits or test:

```
autonomouse card edit <id> --limit … --note "answered: <the worker's question>; gate: (1) … (2) <where the answer is> (3) …"
```

with `--goal …` or `--test …` beside `--limit` or in its place, as the answer needs
(`--limit` replaces the list: give every limit that stays). The answer adds to what the card
asks and changes none of it, so the card stays `in_progress` with its worker, and no
replan's `card move` goes with it (`docs/orchestrator/splitting-and-replanning.md`). On the
next Monitor event the relay above sees the edit's `edited:` row and sends the worker the
new text, which continues it.

Only a question that nothing on the board or in the code answers goes to the human, through
the decision gate: a fork in the goal, a spend, a credential, a judgment of taste, or another
kind rules, item 8, names. Never guess at one of those. A question you could have answered
gets its answer in the card, never a restart or respawn of the worker on the card as it was.
