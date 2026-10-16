# A red base

A worker reports `blocked: <base_branch> red on <test>` when a test that fails on its PR
also fails on its base's latest commit; the base is the card's `base_branch`: the project's
default branch, another card's branch or the wake's interim branch. A red base is never a
failed attempt of the card, and each red test gets one fix card.

Confirm it from the base's own runs, `gh api
"repos/<owner>/<repo>/actions/runs?branch=<base>&per_page=5"`. A run GitHub never started
(its jobs ran no step, `docs/orchestrator/conflict-check.md`) shows nothing about the base:
when the base's runs never started, and for such a base only, the worker's report confirms
it in their place when it records the commands of the project's own CI workflow run
locally on the base's latest commit, with the test failing in their output
(`docs/worker.md`, **ci**). When the base is not red, tell the worker to treat the red check
as a failed attempt. When it is red, the fix card is one that already fixes the test and is
neither `done` nor `cancelled`, or a new one at `--position 0` whose goal is that the test
passes on the base. Send the blocked card back with `autonomouse card move <id> --to todo
--note "blocked: <base_branch> red on <test>; waits for <fix id>"`, then `card edit
--depends-on` with its dependencies and `<fix id>` (the flag replaces the list). The fix,
existing or new, starts as a card you file mid-wake, and the blocked card starts again in
this wake once the fix is `done` (`docs/orchestrator.md`, "A wake", step 3).
