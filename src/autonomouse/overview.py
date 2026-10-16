"""The board as plain text: `autonomouse list` prints one line per project, and
`autonomouse show <slug>` prints one project's brief (`projects/<slug>/brief.md`,
`autonomouse.board.brief`) when it has one, then its details and its cards by stack.
Both work over ssh with no terminal screen.

`project_details` computes a project's details as data, so the board screen's
project page (sys-s13) shows the same facts as `show`. Its one outside call, the
repository's open pull requests, goes through its `read_prs` parameter
(`gh_open_prs`, `gh pr list`, by default). Nothing here writes the board.

An in_progress card's phase is shown as `phase_word` says, wherever the board shows it
(`show`, `card list`, the board screen's card strip and card pane): `stopped` when no
wake is working the card's project (`wake_working`), so a card a stopped or crashed
wake left behind does not read as a worker still on it (sys-w108). The card's recorded
`phase` stays as it is, since the next worker continues from it, and the phase shows
again once a live wake holds the claim.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autonomouse.board import trackers
from autonomouse.board.brief import Brief, load_brief
from autonomouse.board.commands import card_list, project_list, project_show
from autonomouse.board.runs import (
    Liveness,
    LivenessReport,
    RunKind,
    claim_liveness,
    load_run,
)
from autonomouse.board.schema import Card, Project, Stack
from autonomouse.github import GitHubError, repo_slug

# The order both commands print the stacks in.
STACK_ORDER: tuple[Stack, ...] = (
    Stack.DECISION_NEEDED,
    Stack.IN_PROGRESS,
    Stack.TODO,
    Stack.BACKLOG,
    Stack.DONE,
    Stack.CANCELLED,
)

LAST_WAKES = 5

# What an in_progress card shows in place of its phase while no wake works its project.
STOPPED = "stopped"
# What it shows before its worker has written a phase.
STARTING = "starting"


@dataclass
class ProjectSummary:
    """One line of `autonomouse list`."""

    project: Project
    counts: dict[Stack, int]
    claim: str  # `free`, or the holding run id and its liveness: `<run id> (alive)`


@dataclass
class OpenPull:
    url: str
    title: str
    card: (
        str | None
    )  # the id of the card whose `pr` is this URL; None when no card's is


@dataclass
class Wake:
    run_id: str
    started_at: str | None
    ended_at: str | None
    summary: str | None


@dataclass
class ProjectDetails(ProjectSummary):
    """What `autonomouse show <slug>` prints."""

    cards: list[Card]  # the project's cards, by position then id
    pulls: list[OpenPull] | None  # None when the list could not be read
    pulls_error: str | None  # why the list could not be read
    wakes: list[Wake]  # the project's last run records of kind wake, newest first
    brief: Brief | None  # None when the project has no brief.md
    working: bool  # a wake works the project now (`wake_working`)


def gh_open_prs(repo_url: str) -> list[dict[str, str]]:
    """The open pull requests of the GitHub repository at `repo_url`, each as
    `{"url", "title"}`, from `gh pr list --repo <owner>/<name> --state open`.
    Raises `GitHubError` with the reason when gh cannot give the list."""
    # gh lists 30 by default; a repository can have more open, and a short list
    # would read as the whole one.
    argv = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo_slug(repo_url),
        "--state",
        "open",
        "--limit",
        "1000",
        "--json",
        "url,title",
    ]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as e:
        raise GitHubError(f"cannot run gh: {e}") from e
    if result.returncode != 0:
        why = result.stderr.strip() or f"exit status {result.returncode}"
        raise GitHubError(f"gh pr list failed: {why}")
    try:
        return [
            {"url": r["url"], "title": r["title"]} for r in json.loads(result.stdout)
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise GitHubError(f"gh pr list printed no usable list: {e}") from e


def _counts(cards: list[Card]) -> dict[Stack, int]:
    counts = dict.fromkeys(STACK_ORDER, 0)
    for card in cards:
        counts[card.stack] += 1
    return counts


def _claim_text(project: Project, report: LivenessReport | None) -> str:
    if report is None:
        return "free"
    return f"{project.claim.run_id} ({report.status.value})"


def _claim(root: Path, project: Project) -> str:
    report, _ = claim_liveness(root, project)
    return _claim_text(project, report)


def wake_working(report: LivenessReport | None) -> bool:
    """Whether a wake is working the project whose claim `report` judges: the run
    holding the claim is alive or stalled, the liveness `autonomouse list` prints
    after the run id. A stalled run counts: its process still runs, and no other wake
    may take its claim over. An unclaimed project (`None`), and a claim a stopped,
    crashed or taken-over run left, have no wake working them."""
    return report is not None and report.status in (Liveness.ALIVE, Liveness.STALLED)


def project_working(root: Path, project: Project) -> bool:
    """`wake_working` for `project`'s claim as it stands now."""
    report, _ = claim_liveness(root, project)
    return wake_working(report)


def phase_word(card: Card, *, working: bool) -> str | None:
    """What the board shows for `card`'s phase: `stopped` for an in_progress card when
    no wake works its project (`working` false), else its phase, or `starting` before
    its worker has written one; `None` for a card that is not in_progress."""
    if card.stack != Stack.IN_PROGRESS:
        return None
    if not working:
        return STOPPED
    return card.phase.value if card.phase is not None else STARTING


def project_summaries(root: Path) -> list[ProjectSummary]:
    """Every project on the board, lowest `priority` first, with its count per stack
    and its claim."""
    cards = card_list(root)
    return [
        ProjectSummary(
            project=project,
            counts=_counts([c for c in cards if c.project == project.slug]),
            claim=_claim(root, project),
        )
        for project in project_list(root)
    ]


def _wakes(root: Path, slug: str) -> list[Wake]:
    folder = root / "runs"
    runs = [load_run(root, p.stem) for p in sorted(folder.glob("*.json"))]
    mine = [r for r in runs if r.project == slug and r.kind is RunKind.WAKE]
    mine.sort(key=lambda r: (r.started_at or "", r.id), reverse=True)
    return [
        Wake(
            run_id=r.id, started_at=r.started_at, ended_at=r.ended_at, summary=r.summary
        )
        for r in mine[:LAST_WAKES]
    ]


def project_details(
    root: Path,
    slug: str,
    *,
    read_prs: Callable[[str], list[dict[str, str]]] = gh_open_prs,
) -> ProjectDetails:
    """The details of project `slug`: its brief, its fields, claim, count per stack and
    cards, its repository's open pull requests each with the id of the card whose `pr`
    it is, and its last five wakes. `read_prs` takes the project's `repo` URL and
    returns `{"url", "title"}` per open pull request, or raises `GitHubError`; the
    details then carry no list and the reason. Refused (`CommandError`) when no
    project has the slug."""
    project = project_show(root, slug)
    report, _ = claim_liveness(root, project)
    every_card = card_list(root)
    cards = [c for c in every_card if c.project == slug]
    # Projects can share a repository, so a pull request's card is looked up board-wide.
    card_of_pr = {c.pr: c.id for c in every_card if c.pr}
    try:
        pulls = [
            OpenPull(url=p["url"], title=p["title"], card=card_of_pr.get(p["url"]))
            for p in read_prs(project.repo)
        ]
        pulls_error = None
    except GitHubError as e:
        pulls, pulls_error = None, str(e)
    return ProjectDetails(
        project=project,
        counts=_counts(cards),
        claim=_claim_text(project, report),
        cards=cards,
        pulls=pulls,
        pulls_error=pulls_error,
        wakes=_wakes(root, slug),
        brief=load_brief(root, slug),
        working=wake_working(report),
    )


# -------------------------------------------------------------- plain text --


def _one_line(text: str) -> str:
    return " ".join(text.split())


def format_counts(counts: dict[Stack, int]) -> str:
    return " ".join(f"{stack.value}={counts[stack]}" for stack in STACK_ORDER)


def format_list(summaries: list[ProjectSummary]) -> str:
    if not summaries:
        return "no projects"
    slug_w = max(len(s.project.slug) for s in summaries)
    name_w = max(len(s.project.name) for s in summaries)
    return "\n".join(
        f"{s.project.slug:<{slug_w}}  {s.project.name:<{name_w}}  "
        f"{format_counts(s.counts)}  claim={s.claim}"
        for s in summaries
    )


def _open_question(card: Card) -> str | None:
    last = card.decisions[-1] if card.decisions else None
    return last.question if last is not None and last.answer is None else None


def _card_line(card: Card, id_w: int, *, working: bool) -> str:
    """One card under its stack: id, size, for an in_progress card its phase
    (`phase_word`), title, tracker issue, then an open question or its PR."""
    phase = phase_word(card, working=working)
    shown = f"{phase:<8}  " if phase is not None else ""
    line = f"  {card.id:<{id_w}}  {card.size.value:<2}  {shown}{_one_line(card.title)}"
    if card.tracker_issue is not None:
        line += f"  {card.tracker_issue['identifier']}"
    question = _open_question(card) if card.stack == Stack.DECISION_NEEDED else None
    if question is not None:
        return f"{line}  question: {_one_line(question)}"
    return f"{line}  {card.pr}" if card.pr else line


def format_show(details: ProjectDetails) -> str:
    project = details.project
    brief = details.brief.text.strip() if details.brief else ""
    lines = [brief, ""] if brief else []
    lines += [
        f"slug: {project.slug}",
        f"name: {project.name}",
        f"goal: {_one_line(project.goal)}",
        f"repo: {project.repo}",
        f"default_branch: {project.default_branch}",
        f"priority: {project.priority}",
        f"tracker: {trackers.describe(project.tracker)}",
        f"claim: {details.claim}",
        f"cards: {format_counts(details.counts)}",
    ]
    if details.pulls is None:
        lines.append(f"open pull requests: unknown ({details.pulls_error})")
    elif not details.pulls:
        lines.append("open pull requests: none")
    else:
        lines.append(f"open pull requests: {len(details.pulls)}")
        url_w = max(len(p.url) for p in details.pulls)
        for p in details.pulls:
            card = p.card or "no card"
            lines.append(f"  {p.url:<{url_w}}  {card}  {_one_line(p.title)}")
    if not details.wakes:
        lines.append("last wakes: none")
    else:
        lines.append(f"last wakes ({len(details.wakes)}, newest first):")
        for w in details.wakes:
            lines.append(
                f"  {w.run_id}  started {w.started_at or '-'}  "
                f"ended {w.ended_at or '-'}  {_one_line(w.summary or 'no summary')}"
            )
    id_w = max((len(c.id) for c in details.cards), default=0)
    for stack in STACK_ORDER:
        in_stack = [c for c in details.cards if c.stack == stack]
        if in_stack:
            lines += ["", f"{stack.value} ({len(in_stack)})"]
            lines += [_card_line(c, id_w, working=details.working) for c in in_stack]
    return "\n".join(lines)
