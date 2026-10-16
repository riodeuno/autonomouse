"""Live refresh: poll the board folder and redraw when it changes.

Each screen that shows the board (the project list, a project's board, its planning
threads) builds its whole view from one read of the board (`_board_fingerprint`), so
noticing a change and redrawing is the entire mechanism — no incremental diffing, no
filesystem-event watcher, no new dependency. A short poll comfortably inside the
two-second goal is simpler than inotify (also more portable — a networked or
container-mounted board folder does not always deliver filesystem events); the
board's own atomic writes (`board/store.py`'s temp-file-then-`rename`) mean a poll
never observes a half-written file.

A poll reads the board only when it may have changed. Each poll first takes a stamp
(`board_stamp`): the path, modification time, size and inode of every file under the
board folder, and whether the PAUSE file exists. That costs a few milliseconds; reading
and validating every card costs a quarter of a second on a board of 400 cards, which
twice a second kept an idle viewer at a third of a CPU (sys-w90). The stamp is taken
before the read, so a write during the read changes the next poll's stamp and is read
then. A host whose view also depends on something outside the board folder adds it
to its stamp (`_board_stamp`), as the project details page does with its claim's
liveness. A screen that is not showing, such as the project list under a board, does
not poll at all.

A poll whose read fails (`READ_ERRORS`: a board file that is not valid, or one that
cannot be read) is an event, not a crash: the screen keeps the last board it read,
shows the error in its status line (`ReadError`), and reads again on the next poll; the
app appends the error to the error log once per distinct message
(`AutonomouseApp.log_board_read_error`). A hand edit that leaves a card file invalid
for a moment, or for good, never quits the viewer.

A screen's first read is its first poll, so a file that is already bad when the viewer
starts, or when a screen opens, is handled the same way: the screen shows no board
until a read succeeds, and its status line says so (sys-s99).
"""

from __future__ import annotations

import os
from typing import Any

from textual.content import Content
from textual.screen import Screen
from textual.widgets import Static

from autonomouse.board.paths import board_root, errors_log_path
from autonomouse.board.schema import BoardValidationError
from autonomouse.config import pause_path

POLL_INTERVAL = 0.5  # seconds; well under the two-second budget the goal sets

READ_ERRORS = (BoardValidationError, OSError)


class ReadError(Static):
    """The status line of a failed board read, after the failed icon
    (`autonomouse.tui.icons`); hidden while the board reads fine."""

    DEFAULT_CSS = """
    ReadError {
        display: none;
        height: auto;
        padding: 0 2;
        color: $text-error;
        background: $error-muted;
    }
    """

    def set_error(self, message: str | None, *, drawn: bool = True) -> None:
        """Show `message`, or hide the line when it is `None`. `drawn` says whether the
        screen shows an earlier read, which it keeps, or no board yet."""
        self.display = message is not None
        if message is not None:
            shown = (
                "Showing the last board read"
                if drawn
                else "Nothing is shown until a read succeeds"
            )
            # plain `Content`, never markup: an error message can hold `[`
            self.update(
                Content(
                    f"{self.app.icons.failed} Cannot read the board: {message}\n"
                    f"{shown}; reading again every "
                    f"{POLL_INTERVAL:g} seconds. The traceback is in {errors_log_path()}."
                )
            )


class LiveRefresh:
    """Mixin for a `Widget`/`App`: poll a fingerprint of the board, redraw on change.

    A host class implements `_board_fingerprint()` (a cheap, comparable snapshot of
    the board state it shows — equality, not identity, is what matters) and
    `_board_changed()` (how it draws `_live_fingerprint`), composes one `ReadError`
    and no board, then calls `self._start_live_refresh()` from its own `on_mount`.
    That call's first poll reads the board and draws it through `_board_changed`, so a
    host reads the board nowhere else. The host also needs `set_interval`, which every
    `Widget` and `App` already has, and runs inside `AutonomouseApp`, which keeps the
    error log.
    """

    # `None` until a read succeeds; a fingerprint is never `None`
    _live_fingerprint: Any = None
    # the stamp (`_board_stamp`) taken before the last read that succeeded
    _live_stamp: Any = None

    def _start_live_refresh(self) -> None:
        # looked up once, at mount: the app's own query would later also reach the
        # line of a screen pushed on top of it
        self._read_error_line = self.query_one(ReadError)  # type: ignore[attr-defined]
        # the screen this view is on, looked up once too: the line has none while the
        # app shuts down, and a poll can still fire then
        self._live_screen = self._read_error_line.screen
        self._read_board()  # the first read and draw
        self.set_interval(POLL_INTERVAL, self._poll_board)  # type: ignore[attr-defined]

    def _poll_board(self) -> None:
        """One tick of the poll: a view on a screen that is not showing, such as the
        project list under a board, skips it and reads on the first tick after its
        screen shows again."""
        if _showing(self._live_screen):
            self._read_board()

    def _read_board(self) -> None:
        try:
            stamp = self._board_stamp()
            if stamp == self._live_stamp:
                return  # nothing the screen shows can have changed
            fingerprint = self._board_fingerprint()
        except READ_ERRORS as error:
            self._read_error_line.set_error(
                str(error), drawn=self._live_fingerprint is not None
            )
            self.app.log_board_read_error(error)  # type: ignore[attr-defined]
            return
        self._read_error_line.set_error(None)
        self._live_stamp = stamp
        if fingerprint != self._live_fingerprint:
            self._live_fingerprint = fingerprint
            self._board_changed()

    def _board_stamp(self) -> Any:
        """A cheap value that changes whenever `_board_fingerprint` may have changed."""
        return board_stamp()

    def _board_fingerprint(self) -> Any:
        raise NotImplementedError

    def _board_changed(self) -> None:
        raise NotImplementedError


def _showing(screen: Screen) -> bool:
    """Whether `screen` is on the human's terminal: it is the top screen, or every
    screen above it lets it show through, as a dialog does. (Textual's own
    `Screen.is_current` also counts the screen right under an opaque one.)"""
    stack = screen.app.screen_stack
    if screen not in stack:
        return False
    above = stack[stack.index(screen) + 1 :]
    return all(other.styles.background.a < 1 for other in above)


def board_stamp() -> tuple[tuple[tuple[str, int, int, int], ...], bool]:
    """`(path, modification time, size, inode)` of every file under the board folder,
    hidden folders such as a `.git` left out, and whether the PAUSE file exists.

    A file or folder that disappears between listing and `stat` (the temp file of an
    atomic write, `board/store.py`) is left out: the rename that removed it changes a
    file the stamp does hold. A board folder that does not exist yet has no files."""
    files = []
    folders = [str(board_root())]
    while folders:
        try:
            with os.scandir(folders.pop()) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        folders.append(entry.path)
                        continue
                    try:
                        stat = entry.stat()
                    except FileNotFoundError:
                        continue
                    files.append(
                        (entry.path, stat.st_mtime_ns, stat.st_size, stat.st_ino)
                    )
        except FileNotFoundError:
            continue
    return tuple(sorted(files)), pause_path().exists()
