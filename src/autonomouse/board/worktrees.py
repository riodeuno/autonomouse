"""Git worktrees for a card's working folder.

Every card gets one, at `<worktrees_root>/<project>/<card-id>` (`docs/board-schema.md`,
`docs/orchestrator.md`), in the *project's own* git repository (`repo_path`) — never the
board's, which `board.store` already owns. `create` makes one from the card's
base branch; `remove` takes one back out once its card's PR has merged, or once the card
is cancelled and no alive or stalled run holds its project
(`autonomouse.board.runs.cleanup_worktrees` decides when). `registered` lists every
worktree of a repository and `remove_registered` takes one out, so that the run controller
can remove, at the end of a wake, a worktree made during the wake that is no card's
(`autonomouse.controller`, sys-u5). All of them shell out to `git`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class WorktreeError(Exception):
    """A `git worktree` operation failed; the message carries git's own stderr."""


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} in {cwd} failed: {result.stderr.strip()}"
        )
    return result.stdout


def _branch_exists(repo_path: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def create(repo_path: Path, worktree_path: Path, *, branch: str, base_branch: str) -> None:
    """Create `worktree_path` as a git worktree of `repo_path`, on `branch`.

    A no-op when `worktree_path` already exists — the ordinary resume, where a
    previous `card start` already made it and it is still there. Otherwise fetches
    `repo_path`'s `origin` and creates `branch` from `origin/<base_branch>`, unless
    `branch` already exists locally (a resume after the worktree itself was removed
    but the branch survived), in which case the worktree is added on that branch as
    it stands.

    Raises `WorktreeError` when `git` refuses — `repo_path` is not a git repository,
    `origin/<base_branch>` does not exist, or `branch` is already checked out
    somewhere else.
    """
    if worktree_path.is_dir():
        return
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["fetch", "-q", "origin"], cwd=repo_path)
    if _branch_exists(repo_path, branch):
        _run_git(["worktree", "add", str(worktree_path), branch], cwd=repo_path)
    else:
        _run_git(
            ["worktree", "add", str(worktree_path), "-b", branch, f"origin/{base_branch}"],
            cwd=repo_path,
        )


def remove(repo_path: Path, worktree_path: Path) -> None:
    """Remove `worktree_path` from `repo_path`'s worktrees.

    A no-op when `worktree_path` is already gone. Raises `WorktreeError` when `git`
    refuses (uncommitted changes in it, for one) — `--force` still discards those, so
    this only happens when git itself cannot proceed (for example a lock file left by
    a crashed process).
    """
    if not worktree_path.exists():
        return
    _run_git(["worktree", "remove", str(worktree_path), "--force"], cwd=repo_path)


def registered(repo_path: Path) -> dict[Path, str | None]:
    """Every worktree git has registered in `repo_path` (`git worktree list --porcelain`),
    the main checkout included, each mapped to its branch's short name, or `None` for a
    detached one. A worktree whose folder is gone is still registered until it is removed.

    Raises `WorktreeError` when `git` refuses (`repo_path` is not a git repository)."""
    found: dict[Path, str | None] = {}
    path = None
    for line in _run_git(["worktree", "list", "--porcelain"], cwd=repo_path).splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
            found[path] = None
        elif line.startswith("branch ") and path is not None:
            found[path] = line.removeprefix("branch ").removeprefix("refs/heads/")
    return found


def remove_registered(repo_path: Path, worktree_path: Path) -> None:
    """Remove the registered worktree `worktree_path` from `repo_path` with `git worktree
    remove --force`: its folder, uncommitted changes and all, and git's record of it, also
    when the folder is already gone. Its branch stays. Raises `WorktreeError` when `git`
    refuses (a locked worktree, for one)."""
    _run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=repo_path)
