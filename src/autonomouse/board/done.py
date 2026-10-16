"""When a project is done, and which projects a wake marks done.

A project is done when it says so (`done_at`, set by `project done`, or by a wake that
finds it finished). A wake finds a project finished when it is not done, has at least one
card, every card is `done` or `cancelled`, and every `done` card's pull request has
merged; a `done` card with no pull request (a card whose goal produces no code) counts as
merged. A backlog card, a card waiting on a decision, a card in `todo` or `in_progress`,
and a pull request closed without merging each keep the project from qualifying. The
check costs one `pr_state` call per `done` card, for a project whose cards all sit in
`done` or `cancelled`, until the project is marked.

A project with no cards is not done: its planning has not started.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from autonomouse.board.commands import card_list, project_done, project_list
from autonomouse.board.schema import Card, Project, Stack

_CLOSED = (Stack.DONE, Stack.CANCELLED)


def finished(
    project: Project, cards: list[Card], *, pr_state: Callable[[str], str | None]
) -> bool:
    """Whether a wake marks `project` done: see the module docstring. `cards` are the
    project's own. `pr_state(url)` is `MERGED`, `OPEN`, `CLOSED`, or None when the check
    failed; anything but `MERGED` keeps the project from qualifying, so a failed check
    is tried again by the next wake."""
    if project.done_at is not None or not cards:
        return False
    if any(card.stack not in _CLOSED for card in cards):
        return False
    return all(
        not card.pr or pr_state(card.pr) == "MERGED"
        for card in cards
        if card.stack == Stack.DONE
    )


def mark_finished(
    root: Path,
    *,
    pr_state: Callable[[str], str | None],
    out: Callable[[str], None] = lambda line: None,
) -> list[str]:
    """Mark done every project that `finished` finds finished; print one
    `project_done=<slug>` line each and return the slugs. The board is left uncommitted."""
    cards = card_list(root)
    marked = []
    for project in project_list(root):
        own = [card for card in cards if card.project == project.slug]
        if finished(project, own, pr_state=pr_state):
            project_done(root, project.slug)
            out(
                f"project_done={project.slug}: every card is done or cancelled and "
                "every pull request has merged"
            )
            marked.append(project.slug)
    return marked


def every_project_done(root: Path) -> bool:
    """Whether the board has a project and every project is done. A board with no
    project is not finished, only empty."""
    projects = project_list(root)
    return bool(projects) and all(p.done_at is not None for p in projects)
