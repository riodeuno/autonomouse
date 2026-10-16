"""Board file I/O with atomic writes."""

import fcntl
import json
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from autonomouse.board.schema import (
    BoardValidationError,
    Card,
    Project,
    card_from_dict,
    project_from_dict,
)


class GitCommitError(Exception):
    """Committing the board's git repository failed; the message carries git's own stderr."""


def load_project(path: Path) -> Project:
    """Load a project from a JSON file.

    Args:
        path: Path to the project.json file

    Returns:
        Parsed Project object

    Raises:
        BoardValidationError: If the file is not valid JSON or the data is invalid
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise BoardValidationError(f"{path}: failed to read file — {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BoardValidationError(f"{path}: not valid JSON — {e}") from e

    return project_from_dict(data, source=str(path))


def load_card(path: Path) -> Card:
    """Load a card from a JSON file.

    Args:
        path: Path to the card.json file

    Returns:
        Parsed Card object

    Raises:
        BoardValidationError: If the file is not valid JSON or the data is invalid
    """
    try:
        text = path.read_text()
    except OSError as e:
        raise BoardValidationError(f"{path}: failed to read file — {e}") from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise BoardValidationError(f"{path}: not valid JSON — {e}") from e

    return card_from_dict(data, source=str(path))


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write `data` as JSON to `path` atomically: temp file in the same directory, then rename.

    `path` itself is touched only by the final `os.replace` call, so a crash at any
    point before it leaves `path` exactly as it was; a crash after it leaves the new
    content, since `os.replace` is atomic on POSIX. On any failure the temp file is
    removed and the original error re-raised.
    """
    json_text = json.dumps(data, indent=2) + "\n"

    tmp_fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp_path = Path(tmp_path_str)
    try:
        with open(tmp_fd, "w") as f:
            f.write(json_text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _validated_dict(obj, from_dict_fn, path: Path) -> dict:
    """Serialize `obj` and round-trip it through `from_dict_fn` before anything is written.

    Catches a hand-built object that skipped validation (e.g. an enum field holding
    a plain string) and turns it into a plain BoardValidationError instead of an
    AttributeError from deep inside `to_dict()`.
    """
    try:
        data = obj.to_dict()
    except AttributeError as e:
        raise BoardValidationError(f"{path}: object is corrupted — {e}") from e
    from_dict_fn(data, source=str(path))
    return data


def save_project(path: Path, project: Project) -> None:
    """Save a project to a JSON file atomically.

    Args:
        path: Path to save to

    Raises:
        BoardValidationError: If the project is invalid
    """
    data = _validated_dict(project, project_from_dict, path)
    _atomic_write_json(path, data)


def save_card(path: Path, card: Card) -> None:
    """Save a card to a JSON file atomically.

    Args:
        path: Path to save to

    Raises:
        BoardValidationError: If the card is invalid
    """
    data = _validated_dict(card, card_from_dict, path)
    _atomic_write_json(path, data)


def _run_git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GitCommitError(
            f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}"
        )
    return result.stdout


@contextmanager
def board_commit_lock(root: Path) -> Iterator[None]:
    """Hold the board's commit lock: an exclusive `flock` on `autonomouse-commit.lock`
    in the board repository's git directory (never staged, one per board).

    Two runs on different projects share one board; every commit and every push of it
    happens under this lock, so their git operations never overlap. Blocks until the
    lock is free; the kernel releases it if the holder dies.
    """
    git_dir = Path(_run_git(["rev-parse", "--absolute-git-dir"], cwd=root).strip())
    with open(git_dir / "autonomouse-commit.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


# Every `git commit` ends by running `git maintenance run --auto`, which by default
# detaches and keeps running in the background after the commit returns. In git
# 2.55 (CI's) it repacks once about 256 loose objects exist, and the repack's
# `prune-packed` removes each emptied `.git/objects/<xx>/` directory. The next
# `git add` or `git commit`, run under the lock while that maintenance still runs,
# can recreate such a directory and lose it again before writing its object
# there, failing with "unable to create
# temporary file: No such file or directory" (CI run 36301290217). Running the
# maintenance in the foreground keeps it inside the commit, so inside the lock.
_COMMIT = [
    "-c",
    "maintenance.autoDetach=false",
    "-c",
    "gc.autoDetach=false",
    "commit",
]


# `_atomic_write_json`'s own temp files, excluded from every board repo so `git add`
# never has to look at one (see `_ensure_tmp_gitignored`).
_TMP_GITIGNORE_LINE = "*.tmp"


def _ensure_tmp_gitignored(root: Path) -> None:
    """Make sure `root/.gitignore` excludes `_atomic_write_json`'s temp files.

    `git add -A` walks the whole tree, so without this it can lose a race against a
    concurrent write elsewhere under `root`: `_atomic_write_json`'s temp file, live
    for the few instructions between its `mkstemp` and its `os.replace`, can be
    staged while it exists and then vanish before the commit, permanently marking
    that path deleted, or vanish between `git add`'s scan and its read, failing the
    whole call with "No such file or directory" — neither is a real problem with
    the write that triggered it. Idempotent: only adds the line if it is missing,
    never touches the rest of an existing `.gitignore`.
    """
    path = root / ".gitignore"
    lines = path.read_text().splitlines() if path.exists() else []
    if _TMP_GITIGNORE_LINE in lines:
        return
    path.write_text("\n".join([*lines, _TMP_GITIGNORE_LINE]) + "\n")


def commit_board(root: Path, message: str) -> str | None:
    """Stage every change under `root` and commit it as a save point, under the board's
    commit lock; return the sha, or None when nothing had changed.

    `root` is the board directory, already its own git repository (this function
    does not initialize one). Every file under `root` is staged, so a write must
    land inside `root` before this is called, and another run's uncommitted writes
    ride in this commit; for the same reason a caller's writes may already be in
    another run's commit, which is why nothing to commit is not an error. Raises
    `GitCommitError`, with git's own stderr, when git refuses.

    A caller committing one write it just made, and nothing else, should use
    `commit_paths` instead — naming its own path, rather than staging everything
    pending, also means its own commit's message is never swallowed into (or
    swallows) an unrelated one.
    """
    with board_commit_lock(root):
        _ensure_tmp_gitignored(root)
        _run_git(["add", "-A"], cwd=root)
        if not _run_git(["status", "--porcelain"], cwd=root).strip():
            return None
        _run_git([*_COMMIT, "-m", message], cwd=root)
        return _run_git(["rev-parse", "HEAD"], cwd=root).strip()


def commit_paths(root: Path, paths: list[str], message: str) -> str | None:
    """Stage and commit exactly `paths` (relative to `root`) as a save point, under
    the board's commit lock; return the sha, or None when nothing had changed.

    Unlike `commit_board`, this names its paths instead of scanning the tree, so it
    is safe to call while an unrelated write is in flight elsewhere under `root` —
    the caller's own write (already complete before this is called) is the only
    thing it touches, staged and committed on its own even when something else is
    already staged. Raises `GitCommitError`, with git's own stderr, when git refuses.
    """
    with board_commit_lock(root):
        _run_git(["add", "--", *paths], cwd=root)
        if not _run_git(["status", "--porcelain", "--", *paths], cwd=root).strip():
            return None
        _run_git([*_COMMIT, "-m", message, "--", *paths], cwd=root)
        return _run_git(["rev-parse", "HEAD"], cwd=root).strip()
