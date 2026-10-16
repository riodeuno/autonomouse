"""The `board` commands: export the whole board to one dict, and import projects and
cards from a seed folder or from an export file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autonomouse.board.commands.cards import card_list
from autonomouse.board.commands.common import (
    CommandError,
    _board_card_ids,
    _card_path,
    _cards_dir,
    _check_depends_on,
    _now,
    _project_path,
)
from autonomouse.board.commands.projects import project_list
from autonomouse.board.schema import Card, Project, card_from_dict, project_from_dict
from autonomouse.board.store import load_card, load_project, save_card, save_project


@dataclass
class ImportResult:
    """What one `board import` call did: which projects and cards were newly
    written, and which already existed and were left untouched."""

    projects_created: list[str]
    projects_skipped: list[str]
    cards_created: list[str]
    cards_skipped: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "projects_created": self.projects_created,
            "projects_skipped": self.projects_skipped,
            "cards_created": self.cards_created,
            "cards_skipped": self.cards_skipped,
        }


def board_import(root: Path, seed_dir: Path) -> ImportResult:
    """Copy every project and card under `seed_dir` (laid out exactly like the board
    itself: `projects/<slug>/project.json`, `projects/<slug>/cards/<id>.json`) into
    the board at `root`.

    A project or card is copied verbatim — the seed's own `id`/`slug`, `stack`,
    `history` and timestamps ride along unchanged — the moment nothing already sits
    at its destination path; a slug or id already on the board is left untouched and
    counted as skipped, never overwritten. That is what marks a project or card
    imported: its file existing on the board is the mark, so importing the same
    `seed_dir` again finds every destination already occupied and writes nothing —
    the board's own git history (present once this run is committed) is the record
    of when.

    Refused if `seed_dir` has no `projects/` subdirectory. `runs/seed.json` (the
    planning session's own run record) is not copied — the goal is cards and
    projects, and run records are the orchestrator's to write.
    """
    projects_dir = seed_dir / "projects"
    if not projects_dir.is_dir():
        raise CommandError(f"no projects/ under {seed_dir} — nothing to import")

    projects: list[Project] = []
    cards: list[Card] = []
    for project_entry in sorted(projects_dir.iterdir()):
        project_file = project_entry / "project.json"
        if project_file.is_file():
            projects.append(load_project(project_file))
        cards_dir = project_entry / "cards"
        if cards_dir.is_dir():
            cards.extend(load_card(f) for f in sorted(cards_dir.glob("*.json")))
    return _import(root, projects, cards)


def board_import_export(root: Path, export_file: Path) -> ImportResult:
    """Copy every project and card in a `board export` file (`board_export`'s shape:
    `{"projects": [...], "cards": [...]}`) into the board at `root`, by the same rule
    as `board_import`: each is written verbatim where nothing sits at its destination
    yet, and skipped where something does.

    Every entry is validated before anything is written, so a file with one bad
    entry writes nothing. Refused when the file is not JSON or lacks either list.
    """
    source = str(export_file)
    try:
        data = json.loads(export_file.read_text())
    except json.JSONDecodeError as e:
        raise CommandError(f"{source} is not a board export (not JSON: {e})") from e
    if not isinstance(data, dict) or not all(
        isinstance(data.get(key), list) for key in ("projects", "cards")
    ):
        raise CommandError(
            f"{source} is not a board export — it needs a 'projects' list and a "
            "'cards' list (autonomouse board export --out FILE writes one)"
        )
    projects = [project_from_dict(p, source=source) for p in data["projects"]]
    cards = [card_from_dict(c, source=source) for c in data["cards"]]
    return _import(root, projects, cards)


def _import(root: Path, projects: list[Project], cards: list[Card]) -> ImportResult:
    """Write each project and card whose destination is still empty; skip the rest.

    Every card to be written is checked first, so one bad `depends_on` writes
    nothing: each id it names must be a card on the board or in this import.
    """
    known_ids = _board_card_ids(root) | {card.id for card in cards}
    for card in cards:
        if not _card_path(root, card.project, card.id).exists():
            _check_depends_on(card.depends_on, known_ids, card_id=card.id)
    result = ImportResult([], [], [], [])
    for project in projects:
        dest = _project_path(root, project.slug)
        if dest.exists():
            result.projects_skipped.append(project.slug)
        else:
            _cards_dir(root, project.slug).mkdir(parents=True, exist_ok=True)
            save_project(dest, project)
            result.projects_created.append(project.slug)
    for card in cards:
        dest = _card_path(root, card.project, card.id)
        if dest.exists():
            result.cards_skipped.append(card.id)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            save_card(dest, card)
            result.cards_created.append(card.id)
    return result


def board_export(root: Path) -> dict:
    """Export the whole board — every project, every card, in every stack — to
    one JSON-serializable dict, in the same field shapes `docs/board-schema.md`
    describes: `to_dict()` on each, so this round-trips through `project_from_dict`
    / `card_from_dict` unchanged.

    `projects` and `cards` are flat, sorted lists (the same order `project_list`
    and `card_list` already give) rather than cards nested under their project,
    so exporting and later re-importing a card never depends on where its
    project entry landed in the file.
    """
    return {
        "exported_at": _now(),
        "projects": [p.to_dict() for p in project_list(root)],
        "cards": [c.to_dict() for c in card_list(root)],
    }
