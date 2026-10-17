"""When a project is done, and which projects a wake marks done.

A project is done when it says so (`done_at`, set by `project done`, or by a wake that
finds it finished). A wake finds a project finished when it is not done, has at least one
card, every card is `done` or `cancelled`, and every `done` card's work has reached the
project's default branch. That is `github.waiting_branch`'s rule, the one
`github base-branch` uses: the card's pull request has merged into the default branch, or
into a branch whose own pull request has merged. A pull request merged into a wake's
shared branch `wake/<run id>` while that branch's own pull request is open has not
reached it, so the project stays open and the sweep keeps reading that pull request. A
`done` card with no pull request (a card whose goal produces no code) has nothing to
reach. A backlog card, a card waiting on a decision, a card in `todo` or `in_progress`,
and a pull request closed without merging each keep the project from qualifying.

The check reads, for a project whose cards all sit in `done` or `cancelled`, each `done`
card's pull request (`merged_into`, one `gh pr view` each) and, for a pull request merged
into another branch, that branch's own pull request (`branch_merged`, one `gh pr view`
per distinct branch per wake), until the project is marked. A read that fails keeps the
project open and prints a line; the next wake reads again.

A project with no cards is not done: its planning has not started.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from autonomouse import github
from autonomouse.board.commands import card_list, project_done, project_list
from autonomouse.board.schema import Card, Project, Stack

_CLOSED = (Stack.DONE, Stack.CANCELLED)

# The two reads of `github.waiting_branch`, by repository (`<owner>/<name>`): the branch a
# pull request (number or URL) merged into, None while it has not merged; whether a
# branch's own pull request has merged. `github.pr_merged_into` and
# `github.branch_pr_merged` are the real ones.
MergedInto = Callable[[str, str], str | None]
BranchMerged = Callable[[str, str], bool]


def finished(
    project: Project, cards: list[Card], *, merged_into: MergedInto, branch_merged: BranchMerged
) -> bool:
    """Whether a wake marks `project` done: see the module docstring. `cards` are the
    project's own. Raises `github.GitHubError` or `OSError` when a read fails."""
    if project.done_at is not None or not cards:
        return False
    if any(card.stack not in _CLOSED for card in cards):
        return False
    return all(
        github.waiting_branch(
            card,
            default_branch=project.default_branch,
            merged_into=lambda c: merged_into(github.repo_slug(project.repo), c.pr),
            branch_merged=lambda c, branch: branch_merged(
                github.repo_slug(project.repo), branch
            ),
        )
        is None
        for card in cards
    )


def mark_finished(
    root: Path,
    *,
    merged_into: MergedInto,
    branch_merged: BranchMerged,
    out: Callable[[str], None] = lambda line: None,
) -> list[str]:
    """Mark done every project that `finished` finds finished; print one
    `project_done=<slug>` line each and return the slugs. A project whose pull requests
    cannot be read stays open, with one `project_done=<slug>: left open, …` line. A
    branch's own pull request is read once per wake, however many cards merged into it.
    The board is left uncommitted."""
    reads: dict[tuple[str, str], bool] = {}

    def branch_once(repo: str, branch: str) -> bool:
        if (repo, branch) not in reads:
            reads[(repo, branch)] = branch_merged(repo, branch)
        return reads[(repo, branch)]

    cards = card_list(root)
    marked = []
    for project in project_list(root):
        own = [card for card in cards if card.project == project.slug]
        try:
            done = finished(project, own, merged_into=merged_into, branch_merged=branch_once)
        except (github.GitHubError, OSError) as e:
            out(f"project_done={project.slug}: left open, {' '.join(str(e).split())}")
            continue
        if done:
            project_done(root, project.slug)
            out(
                f"project_done={project.slug}: every card is done or cancelled and every "
                "done card's work has reached the default branch"
            )
            marked.append(project.slug)
    return marked


def every_project_done(root: Path) -> bool:
    """Whether the board has a project and every project is done. A board with no
    project is not finished, only empty."""
    projects = project_list(root)
    return bool(projects) and all(p.done_at is not None for p in projects)
