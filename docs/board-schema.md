# Board schema

The board is a directory, by default `~/.local/share/autonomouse/board/` (override with
`AUTONOMOUSE_HOME`). It is its own git repository. The orchestrator commits it after each of
its own writes and at the end of the wake, with `autonomouse board commit -m <message>`; a
worker's edits to its own card ride in the next orchestrator commit. Runs on different
projects share the board, so every commit and push of it (`board commit`, the run
controller) takes the board commit lock, an `flock` on
`.git/autonomouse-commit.lock`; `board commit` prints `nothing to commit` when another
run's commit already carried the changes.

```
board/
  projects/<slug>/project.json
  projects/<slug>/brief.md
  projects/<slug>/cards/<card-id>.json
  runs/<run-id>.json
  plans/<thread-id>.json
  telegram-references.json
  reviews/<date>.md
  reviews/behaviour-<date>.md
```

Times are ISO-8601 UTC with a `Z`. Ids are lowercase, `[a-z0-9-]`.

A project or card file may carry fields the tables in the record files do not name. Such a
field can sit at the top level or inside one of the file's objects: `claim`, `assigned`,
`handoff`, and each entry of `decisions`, `access` and `history`. It is loaded, kept and
written back as it is, never refused, as in a run record (`docs/board-schema/run.md`). A
board viewer or command started before a merge that added a field keeps the old code in
memory while it reads the files the new code writes. On 2026-09-26 the viewer refused a
card that carried the new `access` field and stopped (sys-t1). A command that replaces a
whole object (a new claim, a new assignment, a new `access` list) writes the new one
without the old one's unknown fields. A missing field, or one of the wrong type, is still
refused, and the refusal names the file and the field.

## The records

- [`docs/board-schema/project.md`](board-schema/project.md): a project's record, `projects/<slug>/project.json`, its brief, `projects/<slug>/brief.md`, and `autonomouse project new`, the wizard that creates it.
- [`docs/board-schema/card.md`](board-schema/card.md): a card's record, `projects/<slug>/cards/<card-id>.json`: its fields, its stack transitions, and how a card is deleted or split.
- [`docs/board-schema/run.md`](board-schema/run.md): a run's record, `runs/<run-id>.json`, and a run's life and what it leaves behind: the liveness of a claim, the run commands and the run controller, the behaviour log and the two review commands.
- [`docs/board-schema/plan.md`](board-schema/plan.md): a planning thread's record, `plans/<thread-id>.json`, the `plan` commands that write it, and the board screen's list of threads.
- [`docs/board-schema/telegram-references.md`](board-schema/telegram-references.md): the reference register, `telegram-references.json`, which card or planning thread each Telegram message is about, and how `notify` numbers and routes them.

A charter that names `docs/board-schema.md` means these five files too.

A pull request merges only when the owner merges it or tells the super-orchestrator to
merge it (`docs/super-orchestrator.md`); the board holds no merge setting or merge question.

## The whole board

`board export` writes every project and every card, in the same field shapes these tables
describe, into one JSON file — not a per-card write, so it has no "set by" row in
`docs/board-schema/project.md` or `docs/board-schema/card.md`.
`board import <dir>` copies projects and cards from a seed directory laid out like the board
itself (`projects/<slug>/project.json`, `projects/<slug>/cards/<id>.json`) verbatim, skipping
any slug or id that already exists on the board; a file existing at the destination is what
marks it imported, so re-running the same import writes nothing. It checks every card it
would write first: one `depends_on` id that names no card on the board or in the import
refuses the whole import, and nothing is written.

`autonomouse init`, the first-board wizard, makes the board on a fresh install, without a
project. It creates the board folder, makes it a git repository on branch `main` (with its
own git author, `autonomouse <autonomouse@localhost>`, when git resolves none, so board
commits work on a machine with no global identity), and with `--import FILE` fills the
board from a `board export` file by the same rule as `board import` (nothing already on the
board is overwritten). It asks nothing. It ends with a board commit, `autonomouse init`. Running it again keeps everything already in place; on a board with no project it
prints `create one with: autonomouse project new <name>`. The installer's checklist names
it until the board folder is a git repository.
