"""The error log (`docs/config.md`, "The error log").

Every uncaught exception in an autonomouse command (`cli.main`) or in the board viewer
(`AutonomouseApp._handle_exception`), and every board read the viewer's poll could not
make (`tui/live.py`), is appended to one file, `board.paths.errors_log_path()`, so a crash
can still be read after the terminal that showed it has closed. The terminal shows the
error as it did before; the log is a copy.
"""

import shlex
import sys
import traceback
from datetime import UTC, datetime

from autonomouse import __version__
from autonomouse.board.paths import errors_log_path


def record(error: BaseException) -> None:
    """Append one entry for `error`: a `===` line with the UTC time, the command line,
    the installed version, then the full traceback. Makes the log's folder when it is
    missing."""
    path = errors_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = (
        f"=== {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"command: {shlex.join(sys.argv)}\n"
        f"version: {__version__}\n"
        + "".join(traceback.format_exception(error))
        + "\n"
    )
    with path.open("a", encoding="utf-8") as log:
        log.write(entry)
