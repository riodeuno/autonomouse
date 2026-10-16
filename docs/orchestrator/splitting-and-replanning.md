# Splitting and replanning

You split, merge, cancel, file and rewrite cards whenever the project needs it, while
workers run too, and the cards that come out start in this wake
(`docs/orchestrator/while-workers-run.md`, point 3). A card is yours to reshape while it is
in `backlog` or `todo`; move an `in_progress` card back first with `card move <id> --to
todo --note "replanned: <cause>"`, and a card replanned in `todo` records its cause with
`card edit --note`.

- **Rewrite** limits, test or notes that set the worker up to fail: `autonomouse card edit
  <id> --limit … --test … --note "<cause>"`.
- **Cancel** a card no longer needed: `autonomouse card cancel <id> --reason "<cause>"`.
  When the card's `pr` is still open (`gh pr view <pr> --json state`), close it with
  `gh pr close <pr> --comment "<the cancel reason>"`, without `--delete-branch`, so the
  human or a later card can reopen it; no worker keeps a cancelled card's PR up to date,
  and nobody should merge it. A split parent's PR takes the comment below.
- **Split** a card that needs more than one worker turn: one child per turn, each with the
  parent's `depends_on`, limits and `--position`, its own `--files`, and a `--depends-on` on
  each earlier child whose work it builds on. A file two children would share is resolved
  as `docs/planner.md`, "Cards that share a file", says, never with a `--depends-on` that
  only makes one child wait for the other's file. Create each child with `card new`
  and `--note "split from <parent id>: <cause>"`, and a child that can reuse work on the
  parent's branch with
  `--note "split from <parent id>: <cause>; reuse branch <branch>: <what it holds>"`; the
  child's first history row carries that note. Point the parent's dependents at the
  children, and `card cancel` the parent with a reason naming the children and the cause.
  An open parent PR is closed with `gh pr close <pr> --comment "split into <child ids>:
  <cause>. Each child opens its own PR."` (rules, item 5), without `--delete-branch`.
- **Split an `m` card in `todo` before it starts** when its parts each carry a test of their
  own, whether `m` is its own size or the size Jev or you gave it: most cards are xs or s
  (`docs/planning-method.md`, "The size rule"). Size its children by
  `docs/orchestrator/tier-rubric.md`. An `m` card whose parts would share one test stays
  whole.
- **Reshape** cards that share a file by one of three moves: re-split, with `card edit <id>
  --files …` on each so that each has files of its own; one owner, with `--files` on both and
  a `--limit` on the other card saying it leaves the shared file alone; or fold, with the
  shared part as one new card (`card new`) and `card edit <id> --depends-on` on each of the
  others. `--files`, `--limit` and `--depends-on` each replace the card's whole list. When to
  reshape, and in which order: `docs/orchestrator/conflict-check.md`, "Cards that share a
  file: reshape first, the shared branch last".
