# The Situation

The human: "Every wake should do as much as possible (not just 8 cards)"; "only one project
can be worked on, on a machine at a time. So, one wake on one project at anytime";
(2026-09-26) "When any super orchestrator is running, the cron cadence (set in the config)
can be overriden, and the super-orchestrator can decide to run wakes back to back if
necessary." Only one orchestrator ever works a project at a time, and on a machine only one
works at all: `autonomouse run` starts nothing while another wake is alive or stalled, so
you never share the machine with another wake and never wait for one.

The Situation is the block that ends your start message: the project, the wake interval
and next fire, the usage limit in force and the ready cards. `autonomouse run` has claimed
the project for you, and you run the wake from `docs/orchestrator.md`, "A wake", step 1, on
the board's current in_progress and ready cards.

A wake keeps every slot filled while a ready card remains. When the Situation names a next
fire, it does so until about 30 minutes before that fire, by your judgment; then it starts
no new card, lets the running ones finish and ends. When the Situation says no timer is
installed, or that the next fire is unknown, no fire is coming that you know of: the
30-minute stop does not apply, and the wake keeps its slots filled until no ready card
remains.
