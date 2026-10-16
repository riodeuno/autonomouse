"""Tests for board.history (history rows) and board.store.commit_board/commit_paths
(save points)."""

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from autonomouse.board.history import append_history, now_iso
from autonomouse.board.schema import (
    Assigned,
    Card,
    Handoff,
    Size,
    Stack,
)
from autonomouse.board.store import (
    GitCommitError,
    commit_board,
    commit_paths,
    load_card,
    save_card,
)

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _card(**overrides) -> Card:
    """A minimal valid card, fields overridable."""
    defaults = {
        "id": "sys-b2",
        "project": "system",
        "title": "history line + save point",
        "stack": Stack.IN_PROGRESS,
        "position": 1,
        "size": Size.XS,
        "goal": "test",
        "limits": [],
        "test": "test",
        "depends_on": [],
        "assigned": Assigned(None, None, None, None, None, None),
        "worktree": None,
        "branch": None,
        "base_branch": None,
        "pr": None,
        "phase": None,
        "iteration": 0,
        "handoff": Handoff(None, None, None, None),
        "decisions": [],
        "history": [],
        "created_at": "2026-09-22T06:00:00Z",
        "updated_at": "2026-09-22T06:00:00Z",
    }
    defaults.update(overrides)
    return Card(**defaults)


def _init_repo(root: Path) -> None:
    """A bare git repo with a local identity, isolated from the machine's own config."""
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


class TestNowIso:
    def test_format(self):
        """now_iso matches the board's ISO-8601-UTC-with-Z format."""
        assert ISO_UTC.match(now_iso())


class TestAppendHistory:
    def test_appends_row_and_bumps_updated_at(self):
        card = _card()
        entry = append_history(
            card, run_id="run-1", from_="todo", to="in_progress", note="started"
        )

        assert card.history == [entry]
        assert entry.run_id == "run-1"
        assert entry.from_ == "todo"
        assert entry.to == "in_progress"
        assert entry.note == "started"
        assert ISO_UTC.match(entry.at)
        assert card.updated_at == entry.at

    def test_explicit_at_is_used_verbatim(self):
        card = _card()
        entry = append_history(
            card,
            run_id="run-1",
            from_=None,
            to="todo",
            note="seeded",
            at="2026-09-22T06:00:00Z",
        )
        assert entry.at == "2026-09-22T06:00:00Z"
        assert card.updated_at == "2026-09-22T06:00:00Z"

    def test_second_call_appends_not_replaces(self):
        card = _card()
        append_history(card, run_id="run-1", from_=None, to="todo", note="seeded")
        append_history(
            card, run_id="run-2", from_="todo", to="in_progress", note="started"
        )
        assert len(card.history) == 2
        assert card.history[0].to == "todo"
        assert card.history[1].to == "in_progress"


class TestCommitBoard:
    def test_commits_staged_changes(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "card.json").write_text("{}")

        sha = commit_board(tmp_path, "first save point")

        log = subprocess.run(
            ["git", "log", "-1", "--format=%H %s"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert log == f"{sha} first save point"

    def test_returns_none_when_nothing_to_commit(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "card.json").write_text("{}")
        sha = commit_board(tmp_path, "first save point")

        assert commit_board(tmp_path, "nothing changed") is None
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == sha

    def test_concurrent_commits_all_land(self, tmp_path):
        """Twelve processes each write a file and run `autonomouse board commit` at
        once; the commit lock serializes git, so none fails and every file is
        committed (a file may ride in another process's commit)."""
        _init_repo(tmp_path)
        (tmp_path / "seed").write_text("x")
        commit_board(tmp_path, "seed")
        env = {**os.environ, "AUTONOMOUSE_HOME": str(tmp_path)}
        procs = []
        for n in range(12):
            (tmp_path / f"f{n}").write_text(str(n))
            procs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "autonomouse.cli",
                        "board",
                        "commit",
                        "-m",
                        f"write {n}",
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        results = [p.communicate() + (p.returncode,) for p in procs]
        assert [code for _, _, code in results] == [0] * 12, results
        tracked = subprocess.run(
            ["git", "ls-files"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        assert {f"f{n}" for n in range(12)} <= set(tracked)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert status == ""

    def test_raises_when_not_a_repo(self, tmp_path):
        (tmp_path / "card.json").write_text("{}")
        with pytest.raises(GitCommitError):
            commit_board(tmp_path, "not a repo")


class TestCommitPaths:
    """`commit_paths` (`board/store.py`): like `commit_board`, but names the exact
    paths to stage instead of scanning the tree — used for a write a caller knows
    is its own and wants committed on its own, without either the risk or the
    swallowing behavior of `git add -A` (see `commit_board`'s own docstring)."""

    def test_commits_only_the_named_path(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "mine.json").write_text("{}")
        (tmp_path / "other.json").write_text("{}")  # untracked, not passed in

        sha = commit_paths(tmp_path, ["mine.json"], "mine only")

        tracked = subprocess.run(
            ["git", "show", "--stat", "--format=", sha],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "mine.json" in tracked and "other.json" not in tracked
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert status.strip() == "?? other.json"

    def test_returns_none_when_the_named_path_has_nothing_to_commit(self, tmp_path):
        _init_repo(tmp_path)
        (tmp_path / "mine.json").write_text("{}")
        sha = commit_paths(tmp_path, ["mine.json"], "first")

        (tmp_path / "other.json").write_text("{}")  # a change, but not to mine.json
        assert commit_paths(tmp_path, ["mine.json"], "nothing changed") is None
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head == sha

    def test_raises_when_not_a_repo(self, tmp_path):
        (tmp_path / "mine.json").write_text("{}")
        with pytest.raises(GitCommitError):
            commit_paths(tmp_path, ["mine.json"], "not a repo")

    def test_survives_a_concurrent_atomic_write_elsewhere_under_root(self, tmp_path):
        """The failure this exists to avoid: `commit_board`'s `git add -A` walks the
        whole tree, so a concurrent `_atomic_write_json` elsewhere under `root` — a
        temp file created, then renamed over its target — can vanish between the
        scan seeing it and `git add` reading it, failing the call outright with
        "No such file or directory" even though nothing the caller touched is
        wrong (hit in CI on `sys-d12`'s own PR: two `autonomouse run` wakes racing
        on the same board). `commit_paths` names its path instead of scanning, so
        it never looks at the file being churned here."""
        _init_repo(tmp_path)
        (tmp_path / "runs").mkdir()
        (tmp_path / "runs" / "mine.json").write_text("{}")
        commit_board(tmp_path, "seed")

        stop = threading.Event()
        failure: list[Exception] = []

        def churn() -> None:
            n = 0
            try:
                while not stop.is_set():
                    tmp = tmp_path / "runs" / f".other.json.{n}.tmp"
                    tmp.write_text("x")
                    tmp.replace(tmp_path / "runs" / "other.json")
                    n += 1
            except OSError as e:  # pragma: no cover - would show up as a test failure
                failure.append(e)

        writer = threading.Thread(target=churn)
        writer.start()
        try:
            for i in range(200):
                (tmp_path / "runs" / "mine.json").write_text(json.dumps({"i": i}))
                commit_paths(tmp_path, ["runs/mine.json"], f"mine {i}")
        finally:
            stop.set()
            writer.join()
        assert failure == []

    def test_git_maintenance_has_finished_when_the_commit_returns(self, tmp_path):
        """The race behind CI run 36301290217: `git commit` starts `git maintenance
        run --auto`, detached by default, whose repack removes emptied
        `.git/objects/<xx>/` directories while the next commit writes objects
        there ("unable to create temporary file: No such file or directory").
        Two packs make that maintenance repack on the next commit (git 2.43's gc
        through `gc.autoPackLimit`, 2.55's geometric repack by default); when it
        runs inside the commit, those two packs are gone by the time
        `commit_paths` returns."""
        _init_repo(tmp_path)
        subprocess.run(
            ["git", "config", "gc.autoPackLimit", "1"], cwd=tmp_path, check=True
        )
        pack_dir = tmp_path / ".git" / "objects" / "pack"
        for i in range(2):
            (tmp_path / "mine.json").write_text(json.dumps({"seed": i}))
            subprocess.run(["git", "add", "mine.json"], cwd=tmp_path, check=True)
            subprocess.run(
                ["git", "-c", "maintenance.auto=false", "commit", "-qm", f"seed {i}"],
                cwd=tmp_path,
                check=True,
            )
            subprocess.run(["git", "repack", "-q"], cwd=tmp_path, check=True)
        seed_packs = set(pack_dir.glob("*.pack"))
        assert len(seed_packs) == 2

        (tmp_path / "mine.json").write_text(json.dumps({"i": 0}))
        commit_paths(tmp_path, ["mine.json"], "mine 0")

        assert seed_packs.isdisjoint(pack_dir.glob("*.pack"))


class TestTwoWrites:
    """The card's own test: two writes → two history rows, two commits with readable messages."""

    def test_two_writes_two_history_rows_two_commits(self, tmp_path):
        board_root = tmp_path
        _init_repo(board_root)
        card_path = board_root / "sys-b2.json"

        card = _card(stack=Stack.TODO)
        save_card(card_path, card)
        commit_board(board_root, "sys-b2: seeded")

        # Write 1: todo -> in_progress
        card.stack = Stack.IN_PROGRESS
        append_history(
            card, run_id="run-1", from_="todo", to="in_progress", note="started"
        )
        save_card(card_path, card)
        sha1 = commit_board(board_root, "run-1: sys-b2 todo -> in_progress, started")

        # Write 2: in_progress -> done
        card.stack = Stack.DONE
        append_history(
            card, run_id="run-2", from_="in_progress", to="done", note="finished"
        )
        save_card(card_path, card)
        sha2 = commit_board(board_root, "run-2: sys-b2 in_progress -> done, finished")

        # Two history rows on the card, on disk and in memory.
        reloaded = load_card(card_path)
        assert len(reloaded.history) == 2
        assert reloaded.history[0].from_ == "todo"
        assert reloaded.history[0].to == "in_progress"
        assert reloaded.history[0].run_id == "run-1"
        assert reloaded.history[1].from_ == "in_progress"
        assert reloaded.history[1].to == "done"
        assert reloaded.history[1].run_id == "run-2"

        # Two distinct, readable commits (plus the seed commit) in the board repo.
        result = subprocess.run(
            ["git", "log", "--format=%H %s"],
            cwd=board_root,
            check=True,
            capture_output=True,
            text=True,
        )
        log = result.stdout.strip().splitlines()
        assert log[0] == f"{sha2} run-2: sys-b2 in_progress -> done, finished"
        assert log[1] == f"{sha1} run-1: sys-b2 todo -> in_progress, started"
        assert sha1 != sha2
