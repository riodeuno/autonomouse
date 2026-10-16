"""The stuck rule (`autonomouse.board.stuck`, sys-d35), replayed over wake 33.

Wake 33 (run 20260922-135226-5360) is the wake `docs/orchestrator/while-workers-run.md` was
written from: its `sys-i9` worker ran review rounds 3 to 19 without opening a PR, and
its `sys-i3` worker wrote a next step that waited for main's CI to turn green. The fixture,
`tests/fixtures/wake33/`, is built from the board:

- `board/runs/20260922-135226-5360.json` is the run record, copied as it is.
- `board/projects/system/cards/` holds `sys-i9`, `sys-i3` and `sys-i7` as the board's own
  git commit at the end of the wake held them (`run 20260922-135226-5360: ended (exit
  143)`). `sys-i7` is the card whose next step advances: writing, testing, a PR, review,
  done. Each `card progress` row of the wake carries, in its note, the next step the worker
  passed to that call (from the worker's transcript), as `card progress` now writes it.
- `board/projects/system/project.json` is the system project as the same commit held it;
  `card stuck` reads its `default_branch`, `main`.
- `subagents.json` has the times each worker started a sub-agent (from its transcript); the
  board does not record them, so the replay gives their count to the rule.

The replay asks the rule at every moment something happened in the wake: each history row
of the run and each sub-agent start, with the card as the board held it then.
"""

import copy
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from autonomouse.board.commands import card_progress, card_show, project_new
from autonomouse.board.history import progress_note, read_progress_note
from autonomouse.board.schema import Card, HistoryEntry, Stack
from autonomouse.board.store import load_card, save_card
from autonomouse.board.stuck import stuck, subagents_since

WAKE33 = Path(__file__).resolve().parent / "fixtures" / "wake33"
BOARD = WAKE33 / "board"
RUN = json.loads((BOARD / "runs" / "20260922-135226-5360.json").read_text())
SUBAGENTS = json.loads((WAKE33 / "subagents.json").read_text())


def _fixture_card(card_id: str) -> Card:
    return load_card(BOARD / "projects" / "system" / "cards" / f"{card_id}.json")


def _as_of(card: Card, at: str) -> Card:
    """The card as the board held it at `at`: its history rows up to then, and the fields
    those rows set (`stack`, `iteration`, `pr`, `handoff.next_step` and `updated_at`)."""
    past = copy.deepcopy(card)
    past.history = [row for row in card.history if row.at <= at]
    past.stack = Stack(past.history[-1].to)
    past.iteration, past.pr, past.handoff.next_step = 0, None, None
    for row in past.history:
        if row.note == "started":
            past.handoff.updated_at = row.at
        progress = read_progress_note(row.note)
        if progress is None:
            continue
        past.handoff.updated_at = row.at
        if progress.iteration is not None:
            past.iteration = progress.iteration
        if progress.pr is not None:
            past.pr = progress.pr
        if progress.next_step is not None:
            past.handoff.next_step = progress.next_step
    return past


def _replay(card_id: str) -> list[tuple[str, str | None]]:
    """(moment, the signal that fired then or None) for every moment of the wake."""
    card = _fixture_card(card_id)
    spawns = SUBAGENTS[card_id]
    moments = sorted(
        {row.at for row in card.history if row.run_id == RUN["id"]} | set(spawns)
    )
    replay = []
    for at in moments:
        past = _as_of(card, at)
        since_note = sum(past.handoff.updated_at < s <= at for s in spawns)
        signal = stuck(past, default_branch="main", subagents=since_note)
        replay.append((at, str(signal) if signal else None))
    return replay


def _first_fire(card_id: str) -> tuple[str, str]:
    return next((at, signal) for at, signal in _replay(card_id) if signal)


@pytest.mark.parametrize("card_id", ["sys-i9", "sys-i3", "sys-i7"])
def test_the_replay_rebuilds_each_card_as_the_board_held_it_when_the_wake_ended(card_id):
    card = _fixture_card(card_id)
    past = _as_of(card, RUN["ended_at"])
    assert past.stack == card.stack
    assert past.handoff.next_step == card.handoff.next_step
    if card.stack == Stack.IN_PROGRESS:
        assert (past.iteration, past.pr) == (card.iteration, card.pr)
        assert past.handoff.updated_at == card.handoff.updated_at


def test_sys_i9_fires_by_review_round_3():
    at, signal = _first_fire("sys-i9")
    # 14:09:49 is the note "Round-3 review passes ... running", next step "If round 3
    # clean: push, open PR ..."; rounds 1 and 2, which rule 9 allows, fire nothing.
    assert at == "2026-09-22T14:09:49Z"
    assert signal.startswith("climb: no PR, and a next step of this attempt names review round 3")
    assert '"If round 3 clean: push, open PR' in signal
    # and it still fires when the worker was killed, 12 rounds in
    assert _replay("sys-i9")[-1][1].startswith("climb:")


def test_sys_i9_started_more_than_ten_sub_agents_between_two_notes():
    # The sub-agent half of the same signal: rounds 8 to 11 ran between the notes of
    # 14:33:53 and 14:53:44, five review passes each.
    card = _fixture_card("sys-i9")
    at = "2026-09-22T14:43:24Z"  # round 10's first pass
    past = _as_of(card, at)
    count = sum(past.handoff.updated_at < s <= at for s in SUBAGENTS["sys-i9"])
    assert (past.handoff.updated_at, count) == ("2026-09-22T14:33:53Z", 11)
    past.history = [row for row in past.history if not (row.note or "").startswith("progress:")]
    past.handoff.next_step = None  # leave only the count to fire on
    assert str(stuck(past, default_branch="main", subagents=count)).startswith(
        "climb: no PR, and 11 sub-agents started since the last note at 2026-09-22T14:33:53Z"
    )
    assert stuck(past, default_branch="main", subagents=10) is None


def test_sys_i3_fires_on_its_next_step_that_waits_for_main():
    at, signal = _first_fire("sys-i3")
    assert at == "2026-09-22T14:27:54Z"
    assert signal.startswith(
        "wait: the next step waits on main or the base branch (\"main's CI is green\")"
    )
    assert "When main's CI is green again:" in signal


def test_sys_i7_whose_next_step_advances_never_fires():
    replay = _replay("sys-i7")
    assert len(replay) == 6  # four notes, the finish, and nothing else happened
    assert [signal for _, signal in replay] == [None] * 6


def test_a_card_handed_back_behind_its_wait_has_no_running_worker_to_be_stuck():
    card = _fixture_card("sys-i3")
    assert stuck(card, default_branch="main").name == "wait"
    card.stack = Stack.TODO  # `card move --to todo`, as "While workers run" hands it back
    card.history.append(
        HistoryEntry(
            at="2026-09-22T14:28:19Z",
            run_id=RUN["id"],
            from_="in_progress",
            to="todo",
            note="stopped: wait on main; behind sys-g5, the fix for the red main",
        )
    )
    assert stuck(card, default_branch="main") is None


# The command, against the fixture board.


def _cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_card_stuck_against_the_fixture_board_prints_each_signal():
    env = dict(os.environ, AUTONOMOUSE_HOME=str(BOARD))

    i9 = _cli("card", "stuck", "sys-i9", env=env)
    assert i9.returncode == 0, i9.stderr
    assert i9.stdout.startswith(
        "climb: no PR, and a next step of this attempt names review round 12"
    )

    i3 = _cli("card", "stuck", "sys-i3", env=env)
    assert i3.returncode == 0, i3.stderr
    assert i3.stdout.startswith("wait: the next step waits on main or the base branch")

    i7 = _cli("card", "stuck", "sys-i7", env=env)  # done: no worker runs on it
    assert (i7.returncode, i7.stdout) == (0, "")

    missing = _cli("card", "stuck", "sys-z1", env=env)
    assert missing.returncode != 0
    assert "no card 'sys-z1'" in missing.stderr


def test_card_stuck_counts_the_workers_sub_agents_since_its_last_note(tmp_path):
    # sys-i7 during its testing phase: no PR yet, and nothing else fires.
    board = tmp_path / "board"
    _system_project(board, default_branch="main")
    card = _as_of(_fixture_card("sys-i7"), "2026-09-22T14:05:34Z")
    save_card(board / "projects" / "system" / "cards" / "sys-i7.json", card)
    assert stuck(card, default_branch="main") is None

    subagents = (
        tmp_path / "claude" / "projects" / "-home-x" / card.assigned.session_id / "subagents"
    )
    subagents.mkdir(parents=True)
    note = card.handoff.updated_at

    def meta(name: str, parent: str, seconds_after_note: int) -> None:
        path = subagents / f"agent-{name}.meta.json"
        path.write_text(json.dumps({"agentType": "worker-high", "parentAgentId": parent}))
        when = datetime.fromisoformat(note).timestamp() + seconds_after_note
        os.utime(path, (when, when))

    for i in range(10):
        meta(f"w{i}", "aworker", 60)
    meta("before", "aworker", -60)  # started before the last note
    meta("other", "aother", 60)  # another worker's
    env = dict(
        os.environ, AUTONOMOUSE_HOME=str(board), CLAUDE_CONFIG_DIR=str(tmp_path / "claude")
    )
    assert subagents_since(card, "aworker", tmp_path / "claude" / "projects") == 10
    ten = _cli("card", "stuck", "sys-i7", "--agent-id", "aworker", env=env)
    assert (ten.returncode, ten.stdout) == (0, ""), ten.stderr

    meta("w10", "aworker", 90)
    eleven = _cli("card", "stuck", "sys-i7", "--agent-id", "aworker", env=env)
    assert eleven.returncode == 0, eleven.stderr
    assert eleven.stdout.startswith(
        f"climb: no PR, and 11 sub-agents started since the last note at {note}"
    )
    # without the worker's id, the command does not count
    assert _cli("card", "stuck", "sys-i7", env=env).stdout == ""


def _system_project(board: Path, *, default_branch: str) -> None:
    project_new(
        board, slug="system", name="System", goal="g", repo="https://github.com/o/r",
        repo_path="/nowhere", default_branch=default_branch, priority=1,
    )  # fmt: skip


def test_card_stuck_on_a_master_project_sees_a_wait_for_master(tmp_path):
    """sys-s89: a project's default branch is not always main. On a project whose default
    branch is master, a stacked card's worker that waits for master waits on something
    outside its card, as a worker on a main project that waits for main does."""
    board = tmp_path / "board"
    _system_project(board, default_branch="master")
    card = _as_of(_fixture_card("sys-i7"), "2026-09-22T13:53:19Z")
    card.base_branch = "card/sys-d34"
    save_card(board / "projects" / "system" / "cards" / "sys-i7.json", card)
    card_progress(board, "sys-i7", last_step="pushed", next_step="wait for master to go green")
    env = dict(os.environ, AUTONOMOUSE_HOME=str(board))

    result = _cli("card", "stuck", "sys-i7", env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(
        'wait: the next step waits on master or the base branch ("master to go green")'
    )


# Each signal's edges, on a card whose notes go through `card progress` itself.


@pytest.fixture
def board(tmp_path) -> Path:
    """sys-i7 as it stood when its worker started, and nothing written since."""
    card = _as_of(_fixture_card("sys-i7"), "2026-09-22T13:53:19Z")
    path = tmp_path / "projects" / "system" / "cards" / "sys-i7.json"
    path.parent.mkdir(parents=True)
    save_card(path, card)
    return tmp_path


def _note(board: Path, next_step: str, **fields) -> Card:
    card_progress(board, "sys-i7", last_step="did a step", next_step=next_step, **fields)
    return card_show(board, "sys-i7")


def test_a_progress_row_keeps_the_next_step_it_wrote(board):
    card = _note(board, "open the PR, then wait for checks", phase="ci", pr="https://x/pull/1")
    note = card.history[-1].note
    assert note == (
        'progress: phase=ci, last_step="did a step", pr=https://x/pull/1, '
        "next_step: open the PR, then wait for checks"
    )
    read = read_progress_note(note)
    assert (read.phase, read.pr, read.next_step) == ("ci", "https://x/pull/1", "open the PR, then wait for checks")
    # a row written before rows carried the text still says it set a next step
    old = read_progress_note("progress: phase=writing, iteration=0, last_step, next_step")
    assert (old.iteration, old.sets_next_step, old.next_step) == (0, True, None)
    assert read_progress_note("started") is None
    assert progress_note([], "") == "progress: next_step: "


def test_a_next_step_written_again_with_other_numbers_repeats(board):
    _note(board, "If round 1 clean: push, open PR.")
    assert stuck(_note(board, "Fix the findings, rerun the tests."), default_branch="main") is None
    signal = stuck(_note(board, "if round 2 clean - push, open PR"), default_branch="main")
    assert str(signal).startswith('repeat: the next step "if round 2 clean - push, open PR"')


def test_a_next_step_of_another_attempt_or_another_worker_is_not_a_repeat(board):
    _note(board, "push, open PR, wait for checks")
    assert stuck(_note(board, "push, open PR, wait for checks", iteration=1), default_branch="main") is None

    card = card_show(board, "sys-i7")
    card.history.append(
        # as `board.runs.card_start` writes a resume
        HistoryEntry(
            at="2026-09-22T16:00:00Z",
            run_id="20260922-160000-abcd",
            from_="in_progress",
            to="in_progress",
            note="resumed; previous claim was crashed (pid gone; the run recorded no end)",
        )
    )
    save_card(board / "projects" / "system" / "cards" / "sys-i7.json", card)
    assert stuck(_note(board, "push, open PR, wait for checks"), default_branch="main") is None


def test_the_next_step_the_card_carried_before_its_worker_wrote_one_does_not_fire(board):
    # `card move` from done, or a card handed back for a red main, carries a next step
    # the worker has not written; only the worker's own notes count.
    card = card_show(board, "sys-i7")
    card.handoff.next_step = "blocked: main red on tests/x.py::t; once main is green, merge"
    assert stuck(card, default_branch="main") is None
    assert str(stuck(_note(board, card.handoff.next_step), default_branch="main")).startswith(
        'wait: the next step waits on main or the base branch ("main red")'
    )


@pytest.mark.parametrize(
    ("next_step", "what"),
    [
        ("once main is green, merge origin/main and push", "main or the base branch"),
        ("wait for main, then push", "main or the base branch"),
        ("rebase when card/sys-d34 turns green", "main or the base branch"),
        ("merge after sys-g5 merges, then push", "another card, sys-g5"),
        ("ask the human which option", "the human"),
        ("the human still to confirm the live join", "the human"),
        ("leave the rest for the next wake", "a later wake"),
    ],
)
def test_a_next_step_waiting_outside_the_card_fires(board, next_step, what):
    card = card_show(board, "sys-i7")
    card.base_branch = "card/sys-d34"
    save_card(board / "projects" / "system" / "cards" / "sys-i7.json", card)
    signal = stuck(_note(board, next_step), default_branch="main")
    assert str(signal).startswith(f"wait: the next step waits on {what} ")


@pytest.mark.parametrize(
    "next_step",
    [
        "merge origin/main, push, open the PR against main, wait for checks",
        "if main is red too, report blocked; else fix and push",
        "write the test for the human's reply, then run pytest",
        "test that the next wake picks the card up",
        "once sys-i7's checks pass, write the verification note",  # its own id
        "two review rounds done; push and open the PR",
    ],
)
def test_a_next_step_that_advances_does_not_fire(board, next_step):
    assert stuck(_note(board, next_step), default_branch="main") is None


def test_a_third_review_round_without_a_pr_climbs_and_a_pr_stops_it(board):
    assert stuck(_note(board, "second review round running"), default_branch="main") is None
    signal = stuck(_note(board, "Review rounds 3-4 running; if clean push"), default_branch="main")
    assert str(signal).startswith("climb: no PR, and a next step of this attempt names review round 4")
    assert stuck(_note(board, "wait for checks", pr="https://x/pull/1"), default_branch="main") is None
