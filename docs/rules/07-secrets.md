# Rule 7. Secrets

**Reader:** every agent: the worker, the orchestrator, the planner and the human proxy (the
super-orchestrator).

## What the reader does

Secrets live in `~/.config/autonomouse/.env` (mode 600; `$AUTONOMOUSE_CONFIG/.env` when that
is set), outside every repository, and reach a session only through its environment. No
agent reads a secrets file's values; key names only, and only when needed.

## Why

A secret kept outside every repository cannot be committed with the work. The reason behind
the rule is wider than its text: no secret value is ever printed, from a file or from the
environment.

## The failure it prevents

A secret leaking into a transcript or a log, from a secrets file or from the environment.
