# The Situation

The human: "Every wake should do as much as possible (not just 8 cards)"; "the rule of one
orchestrator holding the project at any time is correct"; "There can only be
one waiting wake at anytime (at max)"; (2026-09-26) "When any super orchestrator
is running, the cron cadence (set in the config) can be overriden, and the
super-orchestrator can decide to run wakes back to back if necessary." Only one orchestrator
ever works a project at a time.

The Situation is the block that ends your start message. When it says another run holds the
project, you are the waiting wake, and a waiting wake touches no card. If the Situation
already names a waiting wake, end at once: `run end <id> --summary "not started: wake <id>
already waits for <slug>"`. Otherwise wait for the holder's pid to exit (a Monitor,
re-armed), then run `project claim <slug> --run-id <your run id>`; a refusal means another
session took the claim, and you wait for its pid (`claim.pid` in `project show <slug>
--json`) the same way. Holding the claim, run the wake from `docs/orchestrator.md`, "A
wake", step 1, on the board's current in_progress and ready cards, not the Situation's.

A wake holding the claim keeps every slot filled while a ready card remains. When the
Situation names a next fire, it does so until about 30 minutes before that fire, by your
judgment; then it starts no new card, lets the running ones finish and ends. A wake still
waiting then runs `run end`, saying so. When the Situation says no timer is installed, or
that the next fire is unknown, no fire is coming that you know of: the 30-minute stop does
not apply, and the wake keeps its slots filled until no ready card remains.
