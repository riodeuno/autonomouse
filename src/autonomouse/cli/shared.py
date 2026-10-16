"""Helpers two or more command modules use. It defines no register(), so discovery
adds no command for it."""

import functools
import os
import sys
from pathlib import Path

from autonomouse import controller, inbox, planner
from autonomouse.board import commands as board_commands
from autonomouse.board import runs as board_runs
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.config import secrets_path
from autonomouse.home import locate

# The run a refusal is recorded on and the refused command's name (`card ask`): `main`
# sets it for a `project` or `card` command given a `--run-id`, the only two groups that
# take one, and leaves it None for the hand-run default `manual`.
_refusal_target: tuple[str, str] | None = None

# What writing a refusal onto a run record can raise: no record, a record that fails
# validation or is not JSON (`ValueError` covers a file that is not UTF-8), a disk error.
_RECORD_ERRORS = (board_commands.CommandError, BoardValidationError, OSError, ValueError)


def _refusal_run(args) -> tuple[str, str] | None:
    if args.command == "project":
        sub = args.project_command
    elif args.command == "card":
        sub = args.card_command
    else:
        return None
    run_id = getattr(args, "run_id", None)
    if run_id is None or run_id == "manual":
        return None
    return run_id, f"{args.command} {sub}"


def _fail(message: str) -> None:
    """Print the refusal and exit 1. When the command carried a `--run-id` that names a
    run record, the refusal is also appended to that record's `refusals` before the exit;
    a planning thread's id names no run record of its own, so it falls back to the alive
    planner record working that thread (`board_runs.alive_planner_run`); a run id that
    matches neither records nothing, and a record write that fails prints a second error
    line and still exits 1 (sys-j5, sys-t39)."""
    print(f"error: {message}", file=sys.stderr)
    if _refusal_target is not None:
        run_id, command = _refusal_target
        root = board_root()
        try:
            if board_runs.run_path(root, run_id).is_file():
                board_runs.record_refusal(root, run_id, command, message)
            else:
                alive = board_runs.alive_planner_run(root, run_id)
                if alive is not None:
                    board_runs.record_refusal(root, alive.id, command, message)
        except _RECORD_ERRORS as e:  # the refusal above stands either way
            print(
                f"error: the refusal was not recorded on run {run_id}: {e}",
                file=sys.stderr,
            )
    raise SystemExit(1)


def _plan_step(root: Path, config: dict, *, claude: str = "claude"):
    """`planner.step` with everything but `out` bound: what `plan step` runs, and what
    every wake runs before it picks a project. Its `poll` is `notify poll`
    (`inbox.poll`, with the secrets file in the environment for the bot's token), which
    `planner.step` runs first under its lock, so the human's Telegram replies are on the
    board before the planner reads it."""
    secrets = secrets_path()

    def poll(out) -> None:
        with controller._secrets_in_environ(secrets):
            inbox.poll(root, config=config, out=out)

    return functools.partial(
        planner.step,
        root,
        config=config,
        claude=claude,
        home=locate(),
        secrets=secrets,
        state_dir=controller.default_state_dir() / "plans",
        poll=poll,
    )


def _env_default(value, name: str, flag: str):
    if value is not None:
        return value
    env = os.environ.get(name)
    if not env:
        _fail(f"pass {flag} or run where ${name} is set")
    return env
