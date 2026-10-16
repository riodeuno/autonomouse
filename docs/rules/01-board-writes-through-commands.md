# Rule 1. Write to the board only through `autonomouse` commands

**Reader:** every agent that writes the board: the worker, the orchestrator, the planner and
the human proxy (the super-orchestrator).

## What the reader does

Write to the board only through `autonomouse` commands, and only your own part of it. The
orchestrator owns every stack move, every project claim, the run record, every new card
outside a planning thread, and every commit of the board folder. A super-orchestrator never
writes a card that exists; the one card it may write is a new one, which `card new` puts in
`todo`, where the running wake may pick it up, and which it then leaves alone
(`docs/super-orchestrator.md`). A worker writes only the progress fields of its own card:
`phase`, `iteration`, `handoff`, `pr`, `attachments`. A planner writes only its own planning
thread and the new cards that thread agreed with the human, which `card new` puts in `todo`,
or in `backlog` with `--backlog` when the human set the card aside to decide later, the
tracker issue it filed for each of those cards, which `card edit --tracker-issue` records
(`docs/trackers/`), and on a new project's brief thread that project's brief, which `plan
brief` writes (`docs/planner.md`, "The brief"). A write that has no command is a missing capability (rule 2), and no agent
makes it by editing a board file.

## Why

The board's guarantees live in code: every command validates the schema, writes the file
atomically and appends a history row (`docs/design/DESIGN.md`, decision "Board writes only
through the CLI"). One write path keeps those guarantees true for every write, and the
ownership split keeps three kinds of session from colliding on one repository.

## The failure it prevents

A board file edited by hand, which skips the guarantees. An agent that sees no command for a
write reports the missing capability (rule 2) and does not edit the file.
