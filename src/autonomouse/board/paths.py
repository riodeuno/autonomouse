"""Board path utilities."""

import os
from pathlib import Path


def board_root() -> Path:
    """Return the board root directory.

    Uses AUTONOMOUSE_HOME environment variable if set and non-empty,
    otherwise defaults to ~/.local/share/autonomouse/board.

    Does not create the directory.
    """
    home_env = os.environ.get("AUTONOMOUSE_HOME")
    if home_env and home_env.strip():
        return Path(home_env)
    return Path.home() / ".local" / "share" / "autonomouse" / "board"


def projects_folder() -> Path:
    """Return `~/projects/autonomouse`, the folder that holds `repos` (the clones),
    `worktrees` (the cards' working folders), `wake-reports` and `wake-logs`.

    `setup/install.sh` makes all four. `autonomouse project new` makes `repos` and
    `worktrees` when they are missing, `card start` makes a card's working folder
    with its parents, and `autonomouse run` makes `wake-logs`, so an install made before
    these folders existed keeps working.

    Does not create the directory.
    """
    return Path.home() / "projects" / "autonomouse"


def worktrees_root() -> Path:
    """Return the root every card's working folder lives under
    (`~/projects/autonomouse/worktrees/<project>/<card-id>`, `docs/orchestrator.md`).

    Uses `$AUTONOMOUSE_WORKTREES` when set and non-empty, otherwise defaults to
    `~/projects/autonomouse/worktrees` — overridden separately from the board so a test
    can point both at a temp directory without the two colliding.

    Does not create the directory.
    """
    env = os.environ.get("AUTONOMOUSE_WORKTREES")
    if env and env.strip():
        return Path(env)
    return projects_folder() / "worktrees"


def repos_root() -> Path:
    """Return the root `autonomouse project new` clones each project's repository under
    (`~/projects/autonomouse/repos/<slug>`, the project's `repo_path`).

    Uses `$AUTONOMOUSE_REPOS` when set and non-empty, otherwise defaults to
    `~/projects/autonomouse/repos` — a sibling of the worktrees, overridden separately
    for the same reason `worktrees_root` is.

    Does not create the directory.
    """
    env = os.environ.get("AUTONOMOUSE_REPOS")
    if env and env.strip():
        return Path(env)
    return projects_folder() / "repos"


def wake_logs_root() -> Path:
    """Return the folder `autonomouse run` writes each wake's observation log in
    (`~/projects/autonomouse/wake-logs/<run id>.md`), which the orchestrator appends to as
    the wake goes (`docs/orchestrator.md`, "Your wake log").

    Uses `$AUTONOMOUSE_WAKE_LOGS` when set and non-empty, otherwise defaults to
    `~/projects/autonomouse/wake-logs` — overridden separately so a test's wake never
    writes into the real folder.

    Does not create the directory.
    """
    env = os.environ.get("AUTONOMOUSE_WAKE_LOGS")
    if env and env.strip():
        return Path(env)
    return projects_folder() / "wake-logs"


def errors_log_path() -> Path:
    """Return the file every uncaught error of an autonomouse command or of the board
    viewer, and every board read the viewer could not make, is appended to with its
    traceback (`autonomouse.errors_log`): `~/.local/state/autonomouse/errors.log`.

    Uses `$AUTONOMOUSE_ERRORS_LOG` when set and non-empty, so a test's errors never land
    in the real log.

    Does not create the file or its folder.
    """
    env = os.environ.get("AUTONOMOUSE_ERRORS_LOG")
    if env and env.strip():
        return Path(env)
    return Path.home() / ".local" / "state" / "autonomouse" / "errors.log"
