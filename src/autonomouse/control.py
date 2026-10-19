"""Ask controllers to end their own sessions, without signalling unrelated processes."""

import fcntl
import time
from pathlib import Path

from autonomouse.board.commands import CommandError, project_show
from autonomouse.board.runs import RunKind, load_run, pid_exists
from autonomouse.config import config_dir


def request_path(run_id: str) -> Path:
    return config_dir() / "control" / f"{run_id}.pause"


def pause_requested(run_id: str) -> bool:
    return request_path(run_id).exists()


def acknowledge(run_id: str) -> None:
    request_path(run_id).unlink(missing_ok=True)


def project_pause_path(project: str) -> Path:
    return config_dir() / "control" / "projects" / f"{project}.pause"


def project_paused(project: str) -> bool:
    return project_pause_path(project).exists()


def resume_at_wake(project: str | None = None) -> None:
    folder = config_dir() / "control" / "projects"
    paths = [project_pause_path(project)] if project else folder.glob("*.pause")
    for path in paths:
        path.unlink(missing_ok=True)


def pause(root: Path, project: str | None = None, *, timeout: float = 20) -> list[str]:
    """Pause current managed runs. Future wakes may start fresh work as usual.

    The decision lock prevents a wake from starting a recovery session while the
    command is selecting runs. Each controller ends only its own captured processes.
    """
    from autonomouse.controller import default_state_dir

    if project is not None:
        project_show(root, project)
        marker = project_pause_path(project)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    state = default_state_dir()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        selected = []
        for path in sorted((root / "runs").glob("*.json")):
            run = load_run(root, path.stem)
            if (run.kind in {RunKind.WAKE, RunKind.PLANNER} and run.ended_at is None
                    and (project is None or run.project == project) and pid_exists(run.pid)):
                request = request_path(run.id)
                request.parent.mkdir(parents=True, exist_ok=True)
                request.touch()
                selected.append(run)
    deadline = time.monotonic() + timeout
    while True:
        pending = [r.id for r in selected if pid_exists(r.pid)]
        if not pending:
            for run in selected:
                acknowledge(run.id)
            return [r.id for r in selected]
        if time.monotonic() >= deadline:
            raise CommandError("pause requested but controllers did not finish in time: " +
                               ", ".join(pending) + "; requests remain pending")
        time.sleep(0.1)
