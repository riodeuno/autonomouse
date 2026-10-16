"""An in_progress card shows `stopped` in place of its phase while no wake works its
project (sys-w108): the run holding the project's claim is not alive or stalled, or no
run holds it. `autonomouse show`, `autonomouse card list`, the board screen's card strip
and its card pane all say so; the card file's `phase` never changes, and the phase shows
again once a live wake holds the claim.

`hold_claim` is the fixture the viewer tests share for "a wake holds the project".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import Static

from autonomouse import cli, overview
from autonomouse.board import commands as board_commands
from autonomouse.board.runs import Run, RunStatus, save_run
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem
from autonomouse.tui.card_detail import CardDetail
from test_tui_board import _open_system_board
from tui_wait import focus_lands, pause_until

LIVE_RUN = "20260930-090000-live"
DEAD_RUN = "20260930-080000-dead"


def hold_claim(root: Path, project: str, *, pid: int | None, run_id: str = LIVE_RUN) -> None:
    """Make run `run_id`, with `pid` and no recorded end, hold `project`'s claim, as a
    wake does while it runs. A `pid` that runs makes the run alive or stalled by the
    liveness rule (`autonomouse.board.runs.liveness`); a gone one, or `None`, crashed."""
    save_run(
        root,
        Run(
            id=run_id,
            project=project,
            session_id=None,
            pid=pid,
            model="opus",
            started_at="2026-09-30T09:00:00Z",
            ended_at=None,
            cards=[],
            jev=[],
            notify_failures=[],
            result=None,
            end_reason=None,
            status=RunStatus.ALIVE,
            summary=None,
        ),
    )
    path = root / "projects" / project / "project.json"
    data = json.loads(path.read_text())
    data["claim"] = {
        "run_id": run_id,
        "session_id": None,
        "pid": pid,
        "model": "opus",
        "claimed_at": "2026-09-30T09:00:00Z",
    }
    path.write_text(json.dumps(data))


def _gone_pid() -> int:
    """The pid of a process that has exited and been reaped: a crashed wake's."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _card_file(root: Path) -> Path:
    return root / "projects" / "system" / "cards" / "sys-w1.json"


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Project system with one in_progress card in phase testing, and one todo card."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    board_commands.project_new(
        tmp_path,
        slug="system",
        name="System",
        goal="goal for system",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    for card_id, title in (("sys-w1", "working card"), ("sys-t1", "queued card")):
        board_commands.card_new(
            tmp_path, card_id=card_id, project="system", title=title, goal="g", test="t"
        )
    path = _card_file(tmp_path)
    data = json.loads(path.read_text())
    data.update(stack="in_progress", phase="testing", iteration=1)
    data["assigned"] = {
        "model": "opus",
        "effort": "high",
        "agent_type": "worker-high",
        "session_id": "s1",
        "pid": 1,
        "picked_up_at": "2026-09-30T09:00:00Z",
    }
    path.write_text(json.dumps(data))
    return tmp_path


# ------------------------------------------------------------- the rule --


@pytest.mark.parametrize(
    ("claim", "working"),
    [("none", False), ("crashed", False), ("stopped", False), ("live", True)],
)
def test_a_wake_works_the_project_only_while_its_claims_run_lives(board, claim, working):
    if claim == "live":
        hold_claim(board, "system", pid=os.getpid())
    elif claim == "crashed":
        hold_claim(board, "system", pid=_gone_pid())
    elif claim == "stopped":
        hold_claim(board, "system", pid=_gone_pid())
        run = board / "runs" / f"{LIVE_RUN}.json"
        data = json.loads(run.read_text())
        data["ended_at"] = "2026-09-30T10:00:00Z"
        run.write_text(json.dumps(data))
    project = board_commands.project_show(board, "system")
    assert overview.project_working(board, project) is working
    card = board_commands.card_show(board, "sys-w1")
    assert overview.phase_word(card, working=working) == ("testing" if working else "stopped")
    # a card that is not in_progress has no phase to show either way
    todo = board_commands.card_show(board, "sys-t1")
    assert overview.phase_word(todo, working=working) is None


# -------------------------------------------------------- plain text --


def _cli(monkeypatch, capsys, *argv: str) -> str:
    monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
    cli.main()
    return capsys.readouterr().out


def _line_of(text: str, card_id: str) -> str:
    [line] = [line for line in text.splitlines() if card_id in line]
    return line


def test_show_and_card_list_print_stopped_for_a_crashed_claim_and_the_phase_for_a_live_one(
    board, monkeypatch, capsys, tmp_path
):
    # a `gh` that lists no pull requests, so `show` asks no network
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text("#!/bin/sh\necho '[]'\n")
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    before = _card_file(board).read_bytes()

    hold_claim(board, "system", pid=_gone_pid())
    show = _cli(monkeypatch, capsys, "show", "system")
    assert "claim: 20260930-090000-live (crashed)" in show.splitlines()
    assert _line_of(show, "sys-w1").split() == ["sys-w1", "s", "stopped", "working", "card"]
    listed = _cli(monkeypatch, capsys, "card", "list", "--project", "system")
    assert _line_of(listed, "sys-w1").split()[-1] == "stopped"
    assert "stopped" not in _line_of(listed, "sys-t1")

    hold_claim(board, "system", pid=os.getpid())
    show = _cli(monkeypatch, capsys, "show", "system")
    assert _line_of(show, "sys-w1").split() == ["sys-w1", "s", "testing", "working", "card"]
    listed = _cli(monkeypatch, capsys, "card", "list", "--project", "system")
    assert _line_of(listed, "sys-w1").split()[-1] == "testing"

    # the card file is never written: its phase is what the next worker continues from
    assert _card_file(board).read_bytes() == before
    shown = json.loads(_cli(monkeypatch, capsys, "card", "show", "sys-w1", "--json"))
    assert shown["phase"] == "testing"


# ------------------------------------------------------- the board screen --


def _item(screen, card_id: str) -> BoardItem:
    return next(i for i in screen.query(BoardItem) if i.card and i.card.id == card_id)


def _strip(screen, card_id: str) -> str:
    return str(_item(screen, card_id).query_one("#card-strip", Static).content)


def _pane(screen) -> str:
    return screen.query_one(CardDetail).source


async def _click_title(pilot, screen, card_id: str) -> None:
    """Click `card_id`'s title once the layout has placed it: a click before that
    lands wherever its empty region points, on a machine loaded past its cores."""
    title = await pause_until(
        pilot,
        lambda: _item(screen, card_id).query_one("#card-title"),
        lambda widget: widget.region.width > 0,
    )
    assert await pilot.click(title), f"the click missed {card_id}'s title"


@pytest.fixture
def wake():
    """A process that stands in for a running wake; the test kills it to crash the wake.
    Killed by its own pid at teardown if the test did not."""
    proc = subprocess.Popen(["sleep", "120"])
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait()


async def test_the_board_shows_stopped_while_no_wake_works_it_and_the_phase_once_one_does(
    board, wake
):
    """A live wake holds the claim, then its process dies with no board file changed,
    then a new live wake takes the claim: the strip and the card pane read testing,
    stopped, testing. A click on the card's title shows it in the pane."""
    before = _card_file(board).read_bytes()
    hold_claim(board, "system", pid=wake.pid)
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await _click_title(pilot, screen, "sys-w1")
        await focus_lands(pilot, "sys-w1")
        assert "testing" in _strip(screen, "sys-w1")
        assert "stopped" not in _strip(screen, "sys-w1")
        assert "testing" in _pane(screen).split("**Phase:**")[1].splitlines()[0]

        wake.kill()  # the wake crashes; no board file changes
        wake.wait()
        await pause_until(pilot, lambda: _strip(screen, "sys-w1"), lambda s: "stopped" in s)
        assert "testing" not in _strip(screen, "sys-w1")
        await pause_until(pilot, lambda: _pane(screen), lambda t: "**Phase:** stopped" in t)

        hold_claim(board, "system", pid=os.getpid(), run_id="20260930-100000-next")
        await pause_until(pilot, lambda: _strip(screen, "sys-w1"), lambda s: "testing" in s)
        await pause_until(
            pilot, lambda: _pane(screen), lambda t: "**Phase:** stopped" not in t
        )
    assert _card_file(board).read_bytes() == before


async def test_a_stopped_card_is_clicked_open_and_its_strip_never_moves(board):
    hold_claim(board, "system", pid=_gone_pid())
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await _click_title(pilot, screen, "sys-w1")
        await focus_lands(pilot, "sys-w1")
        assert "**Phase:** stopped" in _pane(screen)
        first = _strip(screen, "sys-w1")
        assert first.startswith("stopped")
        await pilot.pause(1.1)  # past two animation ticks (`ANIMATION_INTERVAL`)
        assert _strip(screen, "sys-w1") == first
