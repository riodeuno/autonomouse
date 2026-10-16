"""An open board viewer that nobody is using takes almost no CPU (sys-w90).

The owner's viewer, left open on a board of about 400 cards, averaged a quarter of a CPU
over a day. Two things kept it busy: every half-second poll read and validated every
card file (`autonomouse.tui.live`), and every spinner frame of an in_progress card laid
out the whole screen again (`autonomouse.tui.board.BoardItem.animate_strip`).

The test opens a board of that size, with cards in progress, lets it sit for 30
seconds and measures the CPU time this process spent meanwhile. Then it moves a card
from another process and checks the board shows it within two seconds, the live
refresh's own goal.

A busy machine makes the same work cost more CPU seconds, in stretches of several
seconds (sys-w97): with two full test suites running beside it, the idle viewer did the
same polls, strip redraws and screen renders in every run, and was charged from 0.60 s
to 1.02 s for them. So the test measures the 30 seconds as six stretches of five and
judges the quietest one, scaled to 30 seconds. Work the viewer does at least every five
seconds lies in every stretch, so a viewer that polls or redraws too often still fails.
"""

import asyncio
import json
import sys
import time

import pytest
from textual.widgets import DataTable

from autonomouse.board import commands as board_commands
from autonomouse.board.schema import Stack
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import STACK_ORDER, BoardScreen

CARDS = 400
IN_PROGRESS = 5
IDLE_SECONDS = 30
STRETCHES = 6  # of five seconds each; the quietest one is judged
CPU_BUDGET_SECONDS = 1.0  # over IDLE_SECONDS
UPDATE_BUDGET_SECONDS = 2.0
MOVED = "sys-t399"  # the last card, in backlog; another process moves it to todo


def _stack_of(index: int) -> str:
    if index < IN_PROGRESS:
        return "in_progress"
    if index >= CARDS - 20:
        return "backlog"
    return "done"


@pytest.fixture
def big_board(tmp_path, monkeypatch):
    """A project with 400 cards: five in progress, twenty in backlog, the rest done,
    each with a goal of a few sentences, as a board that has run for a while has."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    board_commands.project_new(
        root,
        slug="system",
        name="System",
        goal="goal for system",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    board_commands.card_new(
        root,
        card_id="sys-t0",
        project="system",
        title="card 0",
        goal="The viewer shows this card. " * 20,
        test="A test checks it.",
        position=0,
    )
    cards = root / "projects" / "system" / "cards"
    template = json.loads((cards / "sys-t0.json").read_text())
    for index in range(CARDS):
        card = dict(
            template,
            id=f"sys-t{index}",
            title=f"card {index}",
            position=index,
            stack=_stack_of(index),
            phase="writing" if index < IN_PROGRESS else None,
        )
        (cards / f"sys-t{index}.json").write_text(json.dumps(card, indent=2))
    return root


def _ids_in(screen: BoardScreen, stack: Stack) -> set[str]:
    column = screen.grid[STACK_ORDER.index(stack)]
    return {item.card.id for item in column if item.card is not None}


async def _wait_until(pilot, condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        await pilot.pause(0.05)
    return condition()


async def _settle() -> None:
    """Wait, at most a minute, for the first draw of 400 cards to finish: until a second
    goes by in which the viewer spent less than a tenth of a second of CPU. A machine
    busy with other work can stretch that first draw well past a fixed wait."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        before = time.process_time()
        await asyncio.sleep(1)
        if time.process_time() - before < 0.1:
            return


async def test_idle_board_takes_almost_no_cpu_and_still_shows_a_move(big_board):
    app = AutonomouseApp()
    async with app.run_test(size=(200, 50)) as pilot:
        table = app.query_one(DataTable)
        table.move_cursor(row=table.get_row_index("system"))
        await pilot.press("enter")
        screen = app.screen
        assert isinstance(screen, BoardScreen)
        drawn = await _wait_until(
            pilot, lambda: len(_ids_in(screen, Stack.DONE)) == CARDS - 25, 60
        )
        assert drawn, "the board never showed its cards"
        await _settle()

        spent = []
        for _ in range(STRETCHES):
            before = time.process_time()
            await asyncio.sleep(IDLE_SECONDS / STRETCHES)
            spent.append(time.process_time() - before)
        used = min(spent) * STRETCHES
        stretches = " ".join(f"{seconds:.2f}" for seconds in spent)
        print(
            f"idle CPU per {IDLE_SECONDS / STRETCHES:g} s: {stretches}; "
            f"the quietest, scaled: {used:.2f} s over {IDLE_SECONDS} s"
        )
        assert used < CPU_BUDGET_SECONDS, (
            f"the idle viewer used {used:.2f} s of CPU over {IDLE_SECONDS} s "
            f"(its quietest {IDLE_SECONDS / STRETCHES:g} s, scaled; each: {stretches})"
        )

        # another process moves the card while the viewer runs; the two seconds
        # count from the moment that process has written the move and exited
        mover = await asyncio.create_subprocess_exec(
            *(sys.executable, "-m", "autonomouse.cli", "card", "move", MOVED, "--to", "todo"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, error = await mover.communicate()
        assert mover.returncode == 0, error.decode()
        started = time.monotonic()
        shown = await _wait_until(
            pilot, lambda: MOVED in _ids_in(screen, Stack.TODO), UPDATE_BUDGET_SECONDS
        )
        waited = time.monotonic() - started
        print(f"the move showed after {waited:.2f} s")
        assert shown, f"the moved card was not in todo {waited:.1f} s after the move"
