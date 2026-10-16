"""Tests for board.worktrees: the real `git worktree` mechanics `card_start` and
`cleanup_worktrees` (board.runs) call through their `create_worktree`/`remove_worktree`
parameters."""

import subprocess
from pathlib import Path

import pytest

from autonomouse.board.worktrees import WorktreeError, create, remove


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repo with `main` pushed to a bare `origin` — the same shape a
    project's `repo_path` has: a checkout with a fetchable `origin/main`."""
    origin = tmp_path / "origin.git"
    _git(["init", "-q", "--bare", "-b", "main", str(origin)], tmp_path)
    checkout = tmp_path / "repo"
    _git(["clone", "-q", str(origin), str(checkout)], tmp_path)
    _git(["config", "user.email", "t@t"], checkout)
    _git(["config", "user.name", "t"], checkout)
    (checkout / "README.md").write_text("hello\n")
    _git(["add", "-A"], checkout)
    _git(["commit", "-q", "-m", "seed"], checkout)
    _git(["push", "-q", "origin", "main"], checkout)
    return checkout


def test_create_checks_out_the_base_branch(tmp_path: Path, repo: Path) -> None:
    worktree = tmp_path / "wt" / "card-1"

    create(repo, worktree, branch="card/card-1", base_branch="main")

    assert (worktree / "README.md").is_file()
    branch = _git(["branch", "--show-current"], worktree).strip()
    assert branch == "card/card-1"


def test_create_is_a_no_op_when_the_worktree_already_exists(
    tmp_path: Path, repo: Path
) -> None:
    worktree = tmp_path / "wt" / "card-1"
    create(repo, worktree, branch="card/card-1", base_branch="main")
    (worktree / "local-note.txt").write_text("keep me\n")  # a worker's own progress

    create(repo, worktree, branch="card/card-1", base_branch="main")  # a resume

    assert (worktree / "local-note.txt").is_file()  # untouched, not recreated


def test_create_reuses_an_existing_branch_on_a_fresh_worktree(
    tmp_path: Path, repo: Path
) -> None:
    """The worktree can go missing (removed by hand, or a previous attempt that
    got this far and no further) while its branch survives; `create` still finds
    the branch and does not try (and fail) to create it again with `-b`."""
    first = tmp_path / "wt" / "card-1"
    create(repo, first, branch="card/card-1", base_branch="main")
    remove(repo, first)

    second = tmp_path / "wt" / "card-1-again"
    create(repo, second, branch="card/card-1", base_branch="main")

    assert (second / "README.md").is_file()
    assert _git(["branch", "--show-current"], second).strip() == "card/card-1"


def test_create_raises_on_an_unknown_base_branch(tmp_path: Path, repo: Path) -> None:
    with pytest.raises(WorktreeError, match="origin/no-such-branch"):
        create(
            repo,
            tmp_path / "wt" / "card-1",
            branch="card/card-1",
            base_branch="no-such-branch",
        )


def test_create_raises_when_repo_path_is_not_a_git_repository(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "nothing-here"
    not_a_repo.mkdir()
    with pytest.raises(WorktreeError):
        create(
            not_a_repo,
            tmp_path / "wt" / "card-1",
            branch="card/card-1",
            base_branch="main",
        )


def test_remove_deletes_the_worktree(tmp_path: Path, repo: Path) -> None:
    worktree = tmp_path / "wt" / "card-1"
    create(repo, worktree, branch="card/card-1", base_branch="main")

    remove(repo, worktree)

    assert not worktree.exists()
    listed = _git(["worktree", "list", "--porcelain"], repo)
    assert str(worktree) not in listed


def test_remove_forces_past_uncommitted_changes(tmp_path: Path, repo: Path) -> None:
    worktree = tmp_path / "wt" / "card-1"
    create(repo, worktree, branch="card/card-1", base_branch="main")
    (worktree / "README.md").write_text("dirty, never committed\n")

    remove(repo, worktree)  # --force: does not raise despite the dirty working tree

    assert not worktree.exists()


def test_remove_is_a_no_op_when_already_gone(tmp_path: Path, repo: Path) -> None:
    remove(repo, tmp_path / "wt" / "never-existed")  # does not raise
