"""A `card progress` history row keeps the text of the last step it wrote (sys-m6).

A worker writes each fork it takes, and why, in its next `--last-step` note (sys-m3).
`handoff.last_step` keeps only the newest note, so the card's history is where every
earlier note stays: the row carries the text as `last_step="…"`, at its place among the
fields, and the next step's text stays last in the row, where `card stuck` reads it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from autonomouse.board.commands import project_new
from autonomouse.board.history import read_progress_note
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures" / "wake33" / "board" / "projects" / "system" / "cards" / "sys-i7.json"
)  # fmt: skip
STARTED = "2026-09-22T13:53:19Z"  # sys-i7's worker started then, and wrote nothing yet

# A comma, a quote and the words `next_step: ` inside the text are the text, not fields.
FORK = 'took the JSON form, since a comma, next_step: or a "quote" in the text stays text'
FORK_2 = "kept the old rows as they are: the limits say rows on the board stay"
NEXT = "run pytest, then open the PR"


def _cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


def _board(tmp_path: Path) -> dict[str, str]:
    """A temporary board with sys-i7 in_progress, as it stood when its worker started."""
    board = tmp_path / "board"
    project_new(
        board, slug="system", name="System", goal="g", repo="https://github.com/o/r",
        repo_path="/nowhere", default_branch="main", priority=1,
    )  # fmt: skip
    card = load_card(FIXTURE)
    card.history = [row for row in card.history if row.at <= STARTED]
    card.stack = Stack.IN_PROGRESS
    card.pr = card.handoff.next_step = None
    save_card(board / "projects" / "system" / "cards" / "sys-i7.json", card)
    return dict(os.environ, AUTONOMOUSE_HOME=str(board))


def _progress(env: dict[str, str], *args: str) -> str:
    """Run `card progress sys-i7 <args>` and return the note of the row it wrote."""
    result = _cli("card", "progress", "sys-i7", "--run-id", "r1", *args, env=env)
    assert result.returncode == 0, result.stderr
    shown = _cli("card", "show", "sys-i7", "--json", env=env)
    return json.loads(shown.stdout)["history"][-1]["note"]


def test_a_progress_row_keeps_the_last_step_text_and_card_stuck_reads_the_next_step(tmp_path):
    env = _board(tmp_path)

    first = _progress(env, "--phase", "writing", "--last-step", FORK, "--next-step", NEXT)
    second = _progress(env, "--last-step", FORK_2, "--pr", "https://x/pull/1", "--next-step", NEXT)

    # Both texts are in the row, the last step at its place and the next step last.
    assert first == (
        "progress: phase=writing, "
        'last_step="took the JSON form, since a comma, next_step: or a \\"quote\\" in the '
        'text stays text", '
        "next_step: run pytest, then open the PR"
    )
    assert first.endswith(", next_step: " + NEXT)
    read = read_progress_note(first)
    assert (read.phase, read.last_step, read.next_step) == ("writing", FORK, NEXT)
    read = read_progress_note(second)
    assert (read.last_step, read.pr, read.next_step) == (FORK_2, "https://x/pull/1", NEXT)

    # card stuck reads the next step from the rows as before: the same step twice repeats.
    stuck = _cli("card", "stuck", "sys-i7", env=env)
    assert stuck.returncode == 0, stuck.stderr
    assert stuck.stdout.startswith(f'repeat: the next step "{NEXT}" is the one already written at ')


def test_rows_written_before_the_last_step_text_still_parse():
    since_sys_d35 = read_progress_note("progress: phase=testing, last_step, next_step: " + NEXT)
    assert (since_sys_d35.phase, since_sys_d35.last_step, since_sys_d35.next_step) == (
        "testing", None, NEXT,
    )  # fmt: skip
    before = read_progress_note("progress: phase=writing, iteration=0, last_step, next_step")
    assert (before.iteration, before.last_step, before.sets_next_step, before.next_step) == (
        0, None, True, None,
    )  # fmt: skip
    bare = read_progress_note("progress: phase=ci, last_step, pr=https://x/pull/1")
    assert (bare.phase, bare.pr, bare.sets_next_step) == ("ci", "https://x/pull/1", False)
