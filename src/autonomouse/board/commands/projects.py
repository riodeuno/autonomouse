"""The `project` commands: create, show, list and edit a board's projects."""

from __future__ import annotations

from pathlib import Path

from autonomouse.board import trackers
from autonomouse.board.commands.common import (
    _SLUG_RE,
    CommandError,
    _cards_dir,
    _now,
    _project_path,
)
from autonomouse.board.schema import Claim, MergeMethod, Project
from autonomouse.board.store import load_project, save_project

# The `project edit --tracker` value that clears the project's tracker link.
NO_TRACKER = "none"


def project_new(
    root: Path,
    *,
    slug: str,
    name: str,
    goal: str,
    repo: str,
    repo_path: str,
    default_branch: str,
    priority: int,
    tracker: str | None = None,
) -> Project:
    """Create a new project directory with an empty `cards/` folder.

    `tracker` is a tracker's project or issue URL to link the project to
    (`board.trackers`), or None for no link. Refused if `slug` is not lowercase
    `[a-z0-9-]`, a project already exists there, or `tracker` is no link a known tracker
    reads.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise CommandError(
            f"slug must be lowercase letters, digits and hyphens — got {slug!r}"
        )
    path = _project_path(root, slug)
    if path.exists():
        raise CommandError(f"project {slug!r} already exists at {path}")
    link = _tracker_link(tracker) if tracker is not None else None

    ts = _now()
    project = Project(
        slug=slug,
        name=name,
        goal=goal,
        repo=repo,
        repo_path=repo_path,
        default_branch=default_branch,
        priority=priority,
        tracker=link,
        claim=Claim(
            run_id=None, session_id=None, pid=None, model=None, claimed_at=None
        ),
        created_at=ts,
        updated_at=ts,
    )
    _cards_dir(root, slug).mkdir(parents=True, exist_ok=True)
    save_project(path, project)
    return project


def project_show(root: Path, slug: str) -> Project:
    """Load one project by slug."""
    path = _project_path(root, slug)
    if not path.is_file():
        raise CommandError(f"no project {slug!r} at {path}")
    return load_project(path)


def project_list(root: Path) -> list[Project]:
    """Every project on the board: the ones not done first, lowest `priority` first, then
    slug; the done ones (`done_at` set) after them in the same order."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []
    projects = []
    for entry in sorted(projects_dir.iterdir()):
        path = entry / "project.json"
        if path.is_file():
            projects.append(load_project(path))
    projects.sort(key=lambda p: (p.done_at is not None, p.priority, p.slug))
    return projects


def project_done(root: Path, slug: str) -> Project:
    """Mark the project done: record `done_at`. A project already done keeps the time it
    was marked. A wake already running on the project goes on to its end; a done project
    gets no new wake, sweep or follow-up (`docs/board-schema/project.md`, `done_at`)."""
    path = _project_path(root, slug)
    if not path.is_file():
        raise CommandError(f"no project {slug!r} at {path}")
    project = load_project(path)
    if project.done_at is None:
        project.done_at = project.updated_at = _now()
        save_project(path, project)
    return project


def project_reopen(root: Path, slug: str) -> bool:
    """Clear the project's `done_at` because work came back to it: a card entered `todo`.
    True when the project was done and is not any more; False, with nothing written, when
    it was not done."""
    path = _project_path(root, slug)
    project = load_project(path)
    if project.done_at is None:
        return False
    project.done_at = None
    project.updated_at = _now()
    save_project(path, project)
    return True


def project_edit(
    root: Path,
    slug: str,
    *,
    name: str | None = None,
    goal: str | None = None,
    priority: int | None = None,
    test_temp_env: list[str] | None = None,
    merge_method: str | None = None,
    charter_files: list[str] | None = None,
    orchestrator_instructions: list[str] | None = None,
    worker_instructions: list[str] | None = None,
    tracker: str | None = None,
) -> Project:
    """Edit the fields `docs/board-schema.md` lists as set by `project edit`: `name`,
    `goal`, `priority`, `test_temp_env`, `merge_method`, `charter_files`,
    `orchestrator_instructions`, `worker_instructions` and `tracker`. At least one must be
    given; a list given replaces the one on the project, and a blank instruction is
    dropped, so a list of one blank instruction clears it. `tracker`
    is a tracker's project or issue URL, which links the project to it, or `none`, which
    clears the link. The slug never changes."""
    edits = (
        name,
        goal,
        priority,
        test_temp_env,
        merge_method,
        charter_files,
        orchestrator_instructions,
        worker_instructions,
        tracker,
    )
    if all(value is None for value in edits):
        raise CommandError(
            "nothing to edit — pass --name, --goal, --priority, --test-temp-env, "
            "--merge-method, --charter-files, --orchestrator-instruction, "
            "--worker-instruction or --tracker"
        )
    if name is not None and not name.strip():
        raise CommandError("name must not be empty")
    merge_method_enum = None
    if merge_method is not None:
        try:
            merge_method_enum = MergeMethod(merge_method)
        except ValueError:
            raise CommandError(
                f"merge_method must be one of {', '.join(m.value for m in MergeMethod)} "
                f"— got {merge_method!r}"
            )
    link = None
    if tracker is not None and tracker.strip().lower() != NO_TRACKER:
        link = _tracker_link(tracker)
    path = _project_path(root, slug)
    if not path.is_file():
        raise CommandError(f"no project {slug!r} at {path}")
    project = load_project(path)
    if name is not None:
        project.name = name
    if goal is not None:
        project.goal = goal
    if priority is not None:
        project.priority = priority
    if test_temp_env is not None:
        project.test_temp_env = test_temp_env
    if merge_method_enum is not None:
        project.merge_method = merge_method_enum
    if charter_files is not None:
        project.charter_files = charter_files
    if orchestrator_instructions is not None:
        project.orchestrator_instructions = [i for i in orchestrator_instructions if i.strip()]
    if worker_instructions is not None:
        project.worker_instructions = [i for i in worker_instructions if i.strip()]
    if tracker is not None:
        project.tracker = link
    project.updated_at = _now()
    save_project(path, project)
    return project


def _tracker_link(url: str) -> dict:
    try:
        return trackers.project_link(url)
    except trackers.TrackerError as e:
        raise CommandError(f"tracker: {e}") from None
