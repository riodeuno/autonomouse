"""sys-i21, sys-o3: the wake's interim branch, and the one command that makes it.

`autonomouse run` makes no branch of its own: a wake whose ready cards share a file hands
the orchestrator every card with no interim branch named, and pushes nothing (sys-o3). The
orchestrator makes the wake's interim branch `wake/<run id>` with `autonomouse run interim
<run id>` only after the three reshaping moves of `docs/orchestrator/conflict-check.md`
failed: the command pushes the branch to the project's `origin` from the default branch and
writes it on the run record. The run record names the branch, and its final PR once the
orchestrator opens one; a card whose PR the sibling's merge into the interim branch broke
is reopened and started again in the same wake.

The tests use the run controller's `env` fixture (a board, two seeded repos with a bare
origin, a fake claude) from `test_run_controller`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.board.commands import (
    CommandError,
    card_edit,
    card_finish,
    card_move,
    card_new,
    card_show,
)
from autonomouse.board.runs import (
    card_start,
    load_run,
    project_claim,
    run_interim,
    run_note,
    run_start,
)
from autonomouse.board.schema import Stack
from autonomouse.board.store import commit_board
from test_run_controller import _args, _git, _prompt, _value
from test_run_controller import env as controller_env  # noqa: F401


@pytest.fixture
def env(controller_env):  # noqa: F811
    """The run controller's `env`. Pytest registers an imported fixture under the name it is
    imported as, and ruff reads a fixture named like its import as a redefinition; the two
    `noqa`s above are that."""
    return controller_env


SHARED = "src/autonomouse/controller.py"
RUN = "20260923-090000-abcd"


def _third_card_sharing_a_file(env) -> None:
    """hi-a1 and hi-a3 both change `SHARED`; hi-a2 changes a file of its own."""
    card_edit(env.root, "hi-a1", files=[SHARED])
    card_edit(env.root, "hi-a2", files=["src/autonomouse/cli.py"])
    card_new(env.root, card_id="hi-a3", project="high", title="t", goal="g", test="t",
             position=2, files=[SHARED, "tests/test_interim_branch.py"])
    card_move(env.root, "hi-a3", to="todo")
    commit_board(env.root, "a third card")


def _origin_branches(env) -> dict[str, str]:
    origin = env.tmp / "high-origin.git"
    out = _git(["for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads"],
               origin)
    return dict(line.split() for line in out.splitlines())


# ------------------------------------------------------------------ the wake --


def test_a_wake_whose_cards_share_a_file_makes_no_interim_branch(env):
    """Two ready cards change the same file: the wake hands the orchestrator every card,
    names no branch for them, pushes nothing to origin and writes `interim_branch` null."""
    _third_card_sharing_a_file(env)
    before = _origin_branches(env)
    assert env.wake(workers_per_wake=3) == 0
    run_id = _value(env.lines, "run_id")

    prompt = _prompt(_args(env, 0))
    assert "Start these ready cards: hi-a1, hi-a2, hi-a3." in prompt
    assert "interim" not in prompt and "wake/" not in prompt
    assert "change the same files" not in prompt
    assert not [line for line in env.lines if line.startswith("interim=")]

    run = load_run(env.root, run_id)
    assert run.cards == ["hi-a1", "hi-a2", "hi-a3"]
    assert run.interim_branch is None and run.interim_pr is None
    assert _origin_branches(env) == before
    assert not [b for b in _origin_branches(env) if b.startswith("wake/")]


# --------------------------------------------------------- run interim <id> --


def _cli(env, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "run", *args],
        capture_output=True, text=True, check=False,
        env={**os.environ, "AUTONOMOUSE_HOME": str(env.root)},
    )


def _claimed_run(env) -> Path:
    """Run RUN's record, claiming project `high`; returns the record's path."""
    run_start(env.root, run_id=RUN, session_id="s", pid=os.getpid(), model="m")
    project_claim(env.root, "high", run_id=RUN)
    return env.root / "runs" / f"{RUN}.json"


def _advance_origin_main(env) -> str:
    """Push one more commit to the fixture's origin `main`; return its SHA."""
    checkout = env.tmp / "high-project"
    (checkout / "later.txt").write_text("later\n")
    _git(["add", "-A"], checkout)
    _git(["commit", "-q", "-m", "later"], checkout)
    _git(["push", "-q", "origin", "main"], checkout)
    return _git(["rev-parse", "HEAD"], checkout).strip()


def test_run_interim_makes_the_branch_from_the_default_branch_and_records_it(env):
    record = _claimed_run(env)
    main = _origin_branches(env)["main"]
    board_head = _git(["rev-parse", "HEAD"], env.root)
    done = _cli(env, "interim", RUN)
    assert done.returncode == 0, done.stderr
    branch = f"wake/{RUN}"
    assert done.stdout == f"{branch}\n"
    assert _origin_branches(env)[branch] == main
    assert load_run(env.root, RUN).interim_branch == branch
    # it commits nothing itself: the orchestrator commits the board
    assert _git(["rev-parse", "HEAD"], env.root) == board_head
    written = record.read_bytes()

    # run again after origin's main moved: the same name, no second push, no new write
    later = _advance_origin_main(env)
    again = _cli(env, "interim", RUN)
    assert again.returncode == 0, again.stderr
    assert again.stdout == f"{branch}\n"
    branches = _origin_branches(env)
    assert branches["main"] == later and branches[branch] == main
    assert record.read_bytes() == written


def test_run_interim_refuses_a_run_that_names_no_project(env):
    run_start(env.root, run_id=RUN, session_id="s", pid=os.getpid(), model="m")
    record = env.root / "runs" / f"{RUN}.json"
    written = record.read_bytes()
    done = _cli(env, "interim", RUN)
    assert done.returncode != 0
    assert "names no project" in done.stderr
    assert record.read_bytes() == written
    assert not [b for b in _origin_branches(env) if b.startswith("wake/")]


def test_run_interim_refuses_a_run_with_no_record(env):
    done = _cli(env, "interim", RUN)
    assert done.returncode != 0
    assert f"no run record {RUN!r}" in done.stderr
    assert not (env.root / "runs" / f"{RUN}.json").exists()
    assert not [b for b in _origin_branches(env) if b.startswith("wake/")]


def test_run_interim_refuses_when_the_push_fails(env):
    record = _claimed_run(env)
    written = record.read_bytes()
    hook = env.tmp / "high-origin.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'pushes are closed' >&2\nexit 1\n")
    hook.chmod(0o755)
    done = _cli(env, "interim", RUN)
    assert done.returncode != 0
    assert f"could not create the interim branch wake/{RUN}" in done.stderr
    assert "pushes are closed" in done.stderr
    assert record.read_bytes() == written
    assert not [b for b in _origin_branches(env) if b.startswith("wake/")]


def test_run_interim_help_prints_its_usage(env):
    done = _cli(env, "interim", "--help")
    assert done.returncode == 0, done.stderr
    assert "usage:" in done.stdout and "interim" in done.stdout
    assert "reshaping moves" in done.stdout


# -------------------------------------------------------------- the record --


def test_run_note_records_the_final_pr(tmp_path):
    run_start(tmp_path, run_id="20260923-090000-abcd", session_id="s", pid=1, model="m")
    url = "https://github.com/t/t/pull/200"
    run = run_note(tmp_path, "20260923-090000-abcd", interim_pr=url)
    assert run.interim_pr == url
    assert load_run(tmp_path, "20260923-090000-abcd").interim_pr == url
    with pytest.raises(CommandError, match="needs --card, --jev or --interim-pr"):
        run_note(tmp_path, "20260923-090000-abcd")


def test_the_cli_writes_the_final_pr(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    run_start(tmp_path, run_id="20260923-090000-abcd", session_id="s", pid=1, model="m")
    done = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "run", "note", "20260923-090000-abcd",
         "--interim-pr", "https://github.com/t/t/pull/201"],
        capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert load_run(tmp_path, "20260923-090000-abcd").interim_pr == (
        "https://github.com/t/t/pull/201"
    )


def test_a_record_written_before_the_fields_existed_loads_as_null(tmp_path):
    run_start(tmp_path, run_id="20260923-090000-abcd", session_id="s", pid=1, model="m")
    path = tmp_path / "runs" / "20260923-090000-abcd.json"
    data = json.loads(path.read_text())
    assert data["interim_branch"] is None and data["interim_pr"] is None
    del data["interim_branch"], data["interim_pr"]
    path.write_text(json.dumps(data))
    run = load_run(tmp_path, "20260923-090000-abcd")
    assert run.interim_branch is None and run.interim_pr is None


# ---------------------------------------------- reopened in the same wake --


def _commit(worktree: Path, text: str, message: str) -> None:
    (worktree / "x.py").write_text(text)
    _git(["config", "user.email", "t@t"], worktree)
    _git(["config", "user.name", "t"], worktree)
    _git(["add", "-A"], worktree)
    _git(["commit", "-q", "-m", message], worktree)
    _git(["push", "-q", "origin", "HEAD"], worktree)


def _merges_cleanly(repo: Path, base: str, branch: str) -> bool:
    """The conflict check's verdict, stood in for `gh pr view --json mergeable`."""
    _git(["fetch", "-q", "origin"], repo)
    done = subprocess.run(
        ["git", "merge-tree", "--write-tree", f"origin/{base}", f"origin/{branch}"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    return done.returncode == 0


def test_a_merge_into_the_interim_branch_reopens_the_sibling_in_the_same_wake(env):
    """The orchestrator's path in `docs/orchestrator/conflict-check.md`, with the board
    commands and git as it runs them, and the two `gh` calls stood in for: hi-a1's PR
    merges into the interim branch (a fast-forward push, for `gh pr merge --squash`), the
    conflict check finds hi-a3's PR conflicting (`git merge-tree`, for `gh pr view`),
    reopens it, and starts it again in the same run; its worker resolves and pushes, and
    the orchestrator merges that too."""
    _third_card_sharing_a_file(env)
    repo = env.tmp / "high-project"
    run_id = RUN
    run_start(env.root, run_id=run_id, session_id="s", pid=os.getpid(), model="m")
    project_claim(env.root, "high", run_id=run_id)
    branch = run_interim(env.root, run_id).interim_branch
    assert branch == f"wake/{run_id}"
    start = {"session_id": "s", "pid": os.getpid(), "model": "sonnet", "effort": "high",
             "agent_type": "worker-high", "run_id": run_id}
    for card_id in ("hi-a1", "hi-a3"):
        card_start(env.root, card_id, branch=f"card/{card_id}", base_branch=branch, **start)
    one = Path(card_show(env.root, "hi-a1").worktree)
    three = Path(card_show(env.root, "hi-a3").worktree)

    # both workers change the same file; hi-a1 finishes first and the orchestrator merges it
    _commit(one, "one\n", "hi-a1: one")
    card_finish(env.root, "hi-a1", pr="https://github.com/t/t/pull/1", last_step="verified",
                run_id=run_id)
    assert _merges_cleanly(repo, branch, "card/hi-a1")
    _git(["push", "-q", "origin", f"card/hi-a1:{branch}"], one)

    _commit(three, "three\n", "hi-a3: three")
    card_finish(env.root, "hi-a3", pr="https://github.com/t/t/pull/3", last_step="verified",
                run_id=run_id)
    assert not _merges_cleanly(repo, branch, "card/hi-a3")

    # the conflict check reopens hi-a3 and starts it again, still under this run
    card_move(
        env.root, "hi-a3", to="todo", run_id=run_id,
        note=f"reopened: PR https://github.com/t/t/pull/3 conflicts with {branch} after #1 merged",
        next_step=f"reopened: merge origin/{branch} into card/hi-a3, resolve, rerun the "
        "card's test, push to PR https://github.com/t/t/pull/3",
    )
    card = card_start(env.root, "hi-a3", branch="card/hi-a3", base_branch=branch, **start)
    assert card.stack == Stack.IN_PROGRESS and Path(card.worktree) == three
    assert (three / "x.py").read_text() == "three\n"  # the worktree is reused as it was
    moves = [(h.from_, h.to, h.note) for h in card.history if h.run_id == run_id]
    assert (Stack.DONE.value, Stack.TODO.value) in [(f, t) for f, t, _ in moves]
    reopened = next(note for f, t, note in moves if f == "done" and t == "todo")
    assert reopened.startswith("reopened: PR https://github.com/t/t/pull/3 conflicts with wake/")
    assert moves[-1][:2] == (Stack.TODO.value, Stack.IN_PROGRESS.value)

    # its worker merges the interim branch, resolves, pushes; the orchestrator merges it
    _git(["fetch", "-q", "origin"], three)
    merge = subprocess.run(["git", "merge", f"origin/{branch}"], cwd=three,
                           capture_output=True, text=True, check=False)
    assert merge.returncode != 0 and "CONFLICT" in merge.stdout
    _commit(three, "one\nthree\n", "hi-a3: merge the interim branch")
    assert _merges_cleanly(repo, branch, "card/hi-a3")
    _git(["push", "-q", "origin", f"card/hi-a3:{branch}"], three)
    _git(["fetch", "-q", "origin"], repo)
    assert _git(["show", f"origin/{branch}:x.py"], repo) == "one\nthree\n"
