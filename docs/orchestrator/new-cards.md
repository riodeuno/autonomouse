# New cards

Every card you file, a worker's follow-up included, covers every place that states the
behaviour it changes: run the search `docs/planner.md`, "Cards that share a file",
describes, for every doc sentence, code path and test, and put each place's file into the
card's `files` or the place into another card you file with it. A place a card misses
surfaces only in its worker's report, one more card in a chain that runs one card at a time.

`autonomouse card new --id <id> --project <slug> --title … --goal … --test … --size <xs|s|m>
--limit … --depends-on … --files …` creates a card in `todo`, pickable with no `card move`;
`--backlog` is for a card that runs only once the human says so: one the human set aside, or
a steer's card (below). Its id is the next free number in the series of the card that caused
it; `--files`, once per file, names what its PR is expected to change. Before `card new`,
`autonomouse card overlaps --project <slug> --files …` lists the open cards that would
change one of them; resolve each shared file as `docs/planner.md`, "Cards that share a
file", says. Unlike the planner, you may also reshape the other card with `card edit` in any
of those ways while it is in `backlog` or `todo`, never once it is `in_progress` or
`decision_needed`.

Every card you file opens its goal with `Why:` and the goal one level up that the card
serves, in the human's words: the `Why:` text of the card or report that caused the new
card, copied word for word, or the human's own words, quoted. A steer you hear is a rule or
preference the human states in a decision's answer that reaches beyond the question asked.
It becomes a card in `backlog` the first time you hear it: its goal opens with `Why:` and
the steer, quoting the human's words and the time they gave them. A steer an open card's
goal already quotes gets no second card.