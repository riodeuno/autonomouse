"""The error log: an uncaught error in a command or in the board viewer is appended to
`errors.log` with its traceback, and a board read the viewer's poll cannot make is shown
on screen and logged once instead of quitting the viewer."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse import __version__
from autonomouse.board import commands as board_commands
from autonomouse.board.paths import errors_log_path
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.live import POLL_INTERVAL, ReadError

ENTRY = re.compile(r"^=== \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.MULTILINE)


def test_errors_log_path_defaults_to_the_state_folder(monkeypatch):
    monkeypatch.delenv("AUTONOMOUSE_ERRORS_LOG")
    assert errors_log_path() == (
        Path.home() / ".local" / "state" / "autonomouse" / "errors.log"
    )
    monkeypatch.setenv("AUTONOMOUSE_ERRORS_LOG", "/tmp/elsewhere/errors.log")
    assert errors_log_path() == Path("/tmp/elsewhere/errors.log")


def test_an_uncaught_error_in_a_command_is_logged_and_still_fails(tmp_path):
    """A command whose code raises: the log gets the time, the command line, the version
    and the traceback, and the terminal still shows the traceback and a non-zero exit."""
    log = tmp_path / "state" / "errors.log"  # its folder does not exist yet
    script = (
        "import sys\n"
        "from autonomouse import cli\n"
        "import autonomouse.cli.card\n"
        "def boom(args):\n"
        "    raise RuntimeError('boom from the test')\n"
        "autonomouse.cli.card.run = boom\n"
        "sys.argv = ['autonomouse', 'card', 'list']\n"
        "cli.main()\n"
    )
    env = {
        **os.environ,
        "AUTONOMOUSE_ERRORS_LOG": str(log),
        "AUTONOMOUSE_HOME": str(tmp_path / "board"),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 1
    assert "Traceback (most recent call last)" in result.stderr
    assert "RuntimeError: boom from the test" in result.stderr
    text = log.read_text()
    assert len(ENTRY.findall(text)) == 1
    assert "command: autonomouse card list\n" in text
    assert f"version: {__version__}\n" in text
    assert "Traceback (most recent call last)" in text
    assert "in boom" in text  # the frame that raised, not only the message
    assert "RuntimeError: boom from the test" in text


def test_a_command_that_fails_cleanly_logs_nothing(tmp_path, monkeypatch, capsys):
    """An error a command reports itself (`error: ...`, exit 1) is not an uncaught one."""
    log = tmp_path / "errors.log"
    monkeypatch.setenv("AUTONOMOUSE_ERRORS_LOG", str(log))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    monkeypatch.setattr(sys, "argv", ["autonomouse", "card", "show", "sys-zz9"])
    from autonomouse import cli

    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 1
    assert "error:" in capsys.readouterr().err
    assert not log.exists()


async def test_an_uncaught_error_in_the_viewer_is_logged(tmp_path, monkeypatch):
    """An exception in a key's action: Textual still ends the app with its traceback
    and return code 1, and the log holds the same traceback."""
    log = tmp_path / "errors.log"
    monkeypatch.setenv("AUTONOMOUSE_ERRORS_LOG", str(log))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))

    def boom(self) -> None:
        raise RuntimeError("viewer boom")

    monkeypatch.setattr(AutonomouseApp, "action_toggle_theme", boom)
    app = AutonomouseApp()
    with pytest.raises(RuntimeError, match="viewer boom"):
        async with app.run_test() as pilot:
            await pilot.press("t")
            await pilot.pause()

    assert app.return_code == 1
    text = log.read_text()
    assert len(ENTRY.findall(text)) == 1
    assert "in boom" in text
    assert "RuntimeError: viewer boom" in text


async def test_an_invalid_card_file_shows_on_the_board_and_the_viewer_stays(
    tmp_path, monkeypatch
):
    """A card file turns invalid while its board is open: the viewer keeps running and
    the card on screen, shows the error, logs it once per distinct message, and shows
    the card as it is once the file is valid again."""
    root = tmp_path / "board"
    log = tmp_path / "errors.log"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_ERRORS_LOG", str(log))
    board_commands.project_new(
        root,
        slug="system",
        name="System",
        goal="g",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    board_commands.card_new(
        root, card_id="sys-a1", project="system", title="first title", goal="g", test="t"
    )
    card_path = root / "projects" / "system" / "cards" / "sys-a1.json"
    good = json.loads(card_path.read_text())
    polls = 3 * POLL_INTERVAL + 0.1

    def titles(screen: BoardScreen) -> list[str]:
        return [item.card.title for item in screen.query(BoardItem) if item.card]

    app = AutonomouseApp()
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BoardScreen)
        line = screen.query_one(ReadError)
        assert not line.display

        card_path.write_text('{"id": "sys-a1", ')  # as a hand edit cut short
        await pilot.pause(polls)

        assert app.is_running
        assert app.screen is screen
        assert line.display
        shown = str(line.render())
        assert "Cannot read the board" in shown
        assert "not valid JSON" in shown
        assert "sys-a1.json" in shown
        assert titles(screen) == ["first title"]  # the last good board stays
        text = log.read_text()
        assert len(ENTRY.findall(text)) == 1  # three polls failed, one entry
        assert "Traceback (most recent call last)" in text
        assert "BoardValidationError" in text
        assert "not valid JSON" in text

        card_path.write_text("[1, 2")  # a different error: one more entry
        await pilot.pause(polls)
        assert app.is_running
        assert len(ENTRY.findall(log.read_text())) == 2

        card_path.write_text(json.dumps({**good, "title": "fixed title"}))
        await pilot.pause(polls)

        assert app.is_running
        assert not line.display
        assert titles(screen) == ["fixed title"]
        assert len(ENTRY.findall(log.read_text())) == 2
