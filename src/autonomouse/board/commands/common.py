"""What the board commands share: the `CommandError` they raise, the run ids and
patterns they check against, the paths of a board's project and card files, and the
checks more than one record type's commands run."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

RULES_LIMIT = "docs/rules.md applies"

# The run id `autonomouse notify poll` (`autonomouse.inbox`) answers with: the human's own
# reply on Telegram, which needs no follow-up message there.
TELEGRAM_RUN_ID = "telegram"
# The run id the board screen (`autonomouse.tui.board`) answers and cancels with.
SCREEN_RUN_ID = "screen"
_ORCHESTRATOR_RUN_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_CARD_ID_RE = re.compile(r"^[a-z0-9]+-[a-z][0-9]+$")


class CommandError(Exception):
    """A command's precondition failed; the message is plain-language and safe to print."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _project_path(root: Path, slug: str) -> Path:
    return root / "projects" / slug / "project.json"


def _cards_dir(root: Path, slug: str) -> Path:
    return root / "projects" / slug / "cards"


def _card_path(root: Path, slug: str, card_id: str) -> Path:
    return _cards_dir(root, slug) / f"{card_id}.json"


def _board_card_ids(root: Path) -> set[str]:
    """The id of every card on the board, in any project."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return set()
    return {path.stem for path in projects_dir.glob("*/cards/*.json")}


def _check_depends_on(depends_on: list[str], known_ids: set[str], *, card_id: str) -> None:
    """Refuse a `depends_on` list that holds a value with a comma (the flag repeats,
    one id per `--depends-on`), the card's own id, or an id that is not in
    `known_ids`."""
    joined = [dep for dep in depends_on if "," in dep]
    if joined:
        raise CommandError(
            f"{card_id}: depends_on value {joined[0]!r} holds a comma — --depends-on "
            "takes one card id and repeats: --depends-on <id> --depends-on <id>"
        )
    if card_id in depends_on:
        raise CommandError(f"{card_id}: depends_on names its own id: {card_id}")
    unknown = [dep for dep in depends_on if dep not in known_ids]
    if unknown:
        raise CommandError(
            f"{card_id}: depends_on names no card on the board: {', '.join(unknown)}"
        )


def _check_files(files: list[str], *, card_id: str) -> None:
    """Refuse a `files` value that is blank or holds a comma: the flag repeats, one path
    per `--files`."""
    for path in files:
        if not path.strip() or "," in path:
            raise CommandError(
                f"{card_id}: files value {path!r} is blank or holds a comma — --files "
                "takes one path and repeats: --files <path> --files <path>"
            )


def _require_project(root: Path, slug: str) -> Path:
    path = _project_path(root, slug)
    if not path.is_file():
        raise CommandError(f"no project {slug!r} — run `project new` first")
    return path


def _find_card_path(root: Path, card_id: str, project: str | None) -> Path:
    if project is not None:
        path = _card_path(root, project, card_id)
        if not path.is_file():
            raise CommandError(f"no card {card_id!r} in project {project!r}")
        return path

    projects_dir = root / "projects"
    matches = (
        sorted(projects_dir.glob(f"*/cards/{card_id}.json"))
        if projects_dir.is_dir()
        else []
    )
    if not matches:
        raise CommandError(f"no card {card_id!r} in any project")
    if len(matches) > 1:
        found = ", ".join(str(m) for m in matches)
        raise CommandError(
            f"card {card_id!r} found in more than one project ({found}) — pass --project"
        )
    return matches[0]
