"""Stop managed agents and remove every Autonomouse cron job."""

from autonomouse import control, timer
from autonomouse.board.commands import CommandError
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail
from autonomouse.config import pause_path


def register(subparsers) -> None:
    subparsers.add_parser(
        "stop", help="end all Autonomouse agents and remove all Autonomouse cron jobs"
    )


def run(args) -> None:
    try:
        marker = pause_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
        # Keep the kill switch set even if cron removal fails, and still end agents.
        cron_error = None
        try:
            removed = timer.remove()
        except timer.TimerError as e:
            removed, cron_error = [], e
        ended = control.pause(board_root())
        if cron_error is not None:
            _fail(f"agents stopped; cron removal failed: {cron_error}; {marker} blocks new work")
        print(f"stopped: {len(ended)} runs ended; {len(removed)} Autonomouse cron jobs removed")
        print(f"new work is blocked by {marker}; remove it and run `autonomouse timer install` to restart")
    except (CommandError, BoardValidationError, OSError) as e:
        _fail(str(e))
