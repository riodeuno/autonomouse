"""What a project setup that has not finished has been told and has done, so that
`autonomouse project new` continues it from the step it stopped at (sys-w102).

The wizard (`autonomouse.project_wizard`) writes one record per setup once it knows the
project's name, and writes it again after each answer it accepts and each step it ends:
`<state>/setup/<slug>.json`, where `<state>` is the state folder (`$AUTONOMOUSE_STATE`, else
`~/.local/state/autonomouse/runs`). The record belongs to this machine, as the clone it
names does, so it is not on the board. A field that is None, or False, is a question not
answered yet or a step not done yet. A setup is finished once the project's brief is
written (`docs/board-schema/project.md`, "brief.md"): the wizard then removes the record,
and `unfinished` removes a record whose brief was written since, as by the planner on
Telegram. A project recorded with `--repo-path` has no record.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from autonomouse.board.brief import brief_path
from autonomouse.controller import default_state_dir


@dataclass
class Setup:
    """One project setup: `name` and `slug` as the wizard made them; `repo`, the URL as
    given; `clone`, the clone's path; `git_name` and `git_email`, the name and email the
    agents commit with; `branch`, the branch their pull requests go into, once it is on
    origin; `access`, the GitHub access check passed; `goal`, the goal `--goal` gave, which
    no question asks; `tracker`, the Linear link proven, "" for none; `project`,
    `project.json` written; `timers`, the crontab lines written; `thread`, the brief
    thread opened."""

    slug: str
    name: str
    repo: str | None = None
    clone: str | None = None
    git_name: str | None = None
    git_email: str | None = None
    branch: str | None = None
    access: bool = False
    goal: str | None = None
    tracker: str | None = None
    project: bool = False
    timers: bool = False
    thread: str | None = None
    updated_at: str = ""

    def stopped_at(self) -> str:
        """The step this setup stopped at, in plain words."""
        if self.repo is None:
            return "stopped at the repository URL"
        if self.clone is None:
            return "stopped at the GitHub login and the clone"
        if self.git_name is None or self.git_email is None or self.branch is None:
            return "stopped at the name, email and branch the agents use"
        if not self.access:
            return "stopped at the GitHub access check"
        if self.tracker is None:
            return "stopped at the Linear link"
        if not self.project:
            return "stopped before the project was written"
        if not self.timers:
            return "stopped at the timers"
        if self.thread is None:
            return "stopped before planning"
        return "stopped at planning: its brief is not written yet"

    def save(self) -> None:
        """Write the record, whole, in place of the one before."""
        self.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        path = _path(self.slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f".{path.name}.{os.getpid()}")
        part.write_text(json.dumps(asdict(self), indent=2) + "\n")
        part.replace(path)

    def remove(self) -> None:
        _path(self.slug).unlink(missing_ok=True)


def load(slug: str) -> Setup | None:
    """The record of the setup `slug`, or None when it has none."""
    path = _path(slug)
    return _read(path) if path.is_file() else None


def unfinished(root: Path) -> list[Setup]:
    """Every setup that has not finished, the one most recently changed first. A record
    whose project's brief is on the board at `root` is finished: it is removed here."""
    folder = _folder()
    setups = [_read(path) for path in folder.glob("*.json")] if folder.is_dir() else []
    open_ = []
    for setup in setups:
        if setup.project and brief_path(root, setup.slug).is_file():
            setup.remove()
        else:
            open_.append(setup)
    return sorted(open_, key=lambda s: s.updated_at, reverse=True)


def _read(path: Path) -> Setup:
    data = json.loads(path.read_text())
    known = {f.name for f in fields(Setup)}
    return Setup(**{k: v for k, v in data.items() if k in known})


def _folder() -> Path:
    return default_state_dir() / "setup"


def _path(slug: str) -> Path:
    return _folder() / f"{slug}.json"
