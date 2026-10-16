"""`autonomouse github merge <project> <pr>...`: merge the pull requests the owner told
the super-orchestrator to merge into the project's default branch (sys-w22). The owner's
rule of 2026-09-29: a pull request is merged only by the owner, or by a super-orchestrator
the owner told to merge it. The super-orchestrator runs this command in the session where
the owner named the pull requests (`docs/super-orchestrator.md`); no wake runs it, it asks
nobody anything, and it sends nothing on Telegram. With no pull request named it merges
nothing.

Each named pull request is its number or its URL on the project's repository. One that is
not an open pull request of that repository is left open, with the reason printed.

The named pull requests merge in stack order: one whose base branch is another named pull
request's head branch merges after that one, and otherwise they merge in the order named.
A pull request whose base is not the default branch is first pointed at the default branch
(`github.retarget_to_main`) once its base branch's own pull request has merged, and is
otherwise left open. So a stacked pull request merges right after the one it sits on, and
stays open when that one stays open.

A pull request merges when every check on its head commit (`github.read_checks`) has
completed with success and GitHub reports it `MERGEABLE`. A mergeability of `UNKNOWN`,
which GitHub reports for a while after a pull request or its base changed, is read again
up to `REREADS` times, `delay` seconds apart. A pull request that is `CONFLICTING`, has a
failed check, has checks still going or none yet, or has a GitHub Actions run GitHub never
started (`github.Check.state` `none`, whose local stand-in in the worker's note this
command cannot read) is left open, and the reason is printed. A draft is marked ready for
review (`gh pr ready`), then it merges with `gh pr merge <url> --squash`, naming the head
commit whose checks were read (`--match-head-commit`), so GitHub refuses it when the
branch moved.

Each merge of a card's pull request is a history row on the card, run id `merge`, that
names the pull request and the commit it became on the default branch; a merge GitHub
refuses is a row the same way, with GitHub's reason. Nothing here commits the board: the
rows ride in the next board commit.

Nothing merges in a repository that an alive or stalled wake holds (`claim_liveness` on
every project with the same `repo`): the project is skipped with the holder printed, so a
merge never lands under a running wake. Nothing here pulls, fetches or tests: the default
branch's own CI runs on every push to it, and a red default branch is the next wake's
(`docs/orchestrator/red-main.md`).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autonomouse import github
from autonomouse.board.commands import (
    _find_card_path,
    card_list,
    project_list,
    project_show,
)
from autonomouse.board.history import append_history
from autonomouse.board.runs import Liveness, claim_liveness
from autonomouse.board.schema import Card, Project
from autonomouse.board.store import load_card, save_card
from autonomouse.sweep import list_open_prs

# The run id of the history rows a merge writes on a card.
RUN_ID = "merge"
# How many times an UNKNOWN mergeability is read again, and how long apart by default.
REREADS = 8
DELAY_S = 5.0

_HOLDING = (Liveness.ALIVE, Liveness.STALLED)


@dataclass
class _Named:
    """One named pull request that is open on the project's repository."""

    url: str
    head: str  # its head branch
    base: str  # its base branch when the command started


def merge_project(
    root: Path,
    slug: str,
    prs: list[str],
    *,
    delay: float = DELAY_S,
    projects_dir: Path | None = None,
    now: float | None = None,
    out: Callable[[str], None] = print,
) -> None:
    """Merge each pull request in `prs` (numbers or URLs on project `slug`'s repository)
    that is green and merges cleanly, in stack order (the module's docstring has the
    rules), printing one `merge=<slug>: ...` line per pull request: what merged, or why it
    was left open. Raises `CommandError` for an unknown project, and `github.GitHubError`
    when the list of open pull requests cannot be read; a read that fails for one pull
    request leaves that one open, printed, and the rest go on."""
    project = project_show(root, slug)

    def say(text: str) -> None:
        out(f"merge={slug}: {text}")

    if not prs:
        say("nothing merged: no pull request was named")
        return
    holder = _live_holder(root, project, projects_dir=projects_dir, now=now)
    if holder is not None:
        say(f"nothing merged: a live wake holds its repository, {holder}")
        return

    repo = github.repo_slug(project.repo)
    open_prs = {pr["number"]: pr for pr in list_open_prs(repo)}
    named: list[_Named] = []
    for ref in dict.fromkeys(prs):
        number = _number(repo, ref)
        pr = open_prs.get(number) if number is not None else None
        if pr is None:
            say(f"left open {ref}: it is not an open pull request of {repo}")
            continue
        if any(n.url == pr["url"] for n in named):
            continue
        try:
            base = github.read_pr(repo, pr["url"]).base_branch
        except github.GitHubError as e:
            say(f"left open {pr['url']}: {e}")
            continue
        named.append(_Named(pr["url"], pr["headRefName"], base))

    cards = card_list(root, project=slug)
    for pr in _stack_order(named):
        merged, text = _merge(project, repo, pr.url, delay=delay, say=say)
        card = next((c for c in cards if c.pr == pr.url), None)
        say(f"{text} ({card.id})" if card else text)
        if card is not None and merged is not None:
            _record(root, card, text)


def _number(repo: str, ref: str) -> int | None:
    """The pull request number `ref` names: a number, with or without `#`, or the URL of a
    pull request of `repo`; None for anything else."""
    ref = ref.strip()
    found = re.fullmatch(r"#?(\d+)", ref) or re.fullmatch(
        rf"https://github\.com/{re.escape(repo)}/pull/(\d+)/?", ref, flags=re.IGNORECASE
    )
    return int(found.group(1)) if found else None


def _stack_order(named: list[_Named]) -> list[_Named]:
    """`named` in the order they merge: each after the named pull request whose head
    branch is its base, and otherwise in the order named."""
    left, ordered = list(named), []
    while left:
        heads = {pr.head for pr in left}
        # A cycle of bases cannot merge at all; taking the first keeps the loop finite.
        nxt = next((pr for pr in left if pr.base not in heads), left[0])
        ordered.append(nxt)
        left.remove(nxt)
    return ordered


def _merge(
    project: Project, repo: str, url: str, *, delay: float, say: Callable[[str], None]
) -> tuple[bool | None, str]:
    """Merge one pull request when it is green and merges cleanly. Returns whether it
    merged (None when it did not try, False when GitHub refused the merge) and the
    sentence that says what happened: `merged <url> into <main> as <sha>`, `merge of
    <url> refused: <reason>`, or `left open <url>: <reason>`."""
    main = project.default_branch
    try:
        pr = github.read_pr(repo, url)
        if pr.state != "OPEN":
            return None, f"left open {url}: it is {pr.state}"
        if pr.base_branch != main:
            if not github.retarget_to_main(
                repo, pr_number=pr.number, base_branch=pr.base_branch, main=main
            ):
                return None, f"left open {url}: its base {pr.base_branch} has not merged"
            say(f"retargeted {url} from {pr.base_branch} to {main}, since that branch merged")
        why = _not_green(github.read_checks(repo, head_sha=pr.head_sha))
        if why is not None:
            return None, f"left open {url}: {why}"
        mergeable = _mergeable(repo, url, delay=delay)
        if mergeable != "MERGEABLE":
            return None, f"left open {url}: GitHub reports it {mergeable}"
    except github.GitHubError as e:
        return None, f"left open {url}: {e}"
    try:
        # A draft cannot merge.
        github._run_gh(["pr", "ready", url])
        github.merge_pr(url, head_sha=pr.head_sha)
    except github.GitHubError as e:
        return False, f"merge of {url} refused: {e}"
    try:
        sha = github.merge_commit(repo, url)
    except github.GitHubError as e:
        return True, f"merged {url} into {main}; the commit it became could not be read: {e}"
    return True, f"merged {url} into {main} as {sha}"


def _not_green(checks: list[github.Check]) -> str | None:
    """Why the checks on a head commit keep its pull request from merging, or None when
    every one completed with success."""
    failed = [c for c in checks if c.state == "fail"]
    if failed:
        return "a check failed: " + "; ".join(f"{c.name} {c.url}" for c in failed)
    going = [c for c in checks if c.state == "pending"]
    if going:
        return "checks still going: " + "; ".join(f"{c.name} {c.url}" for c in going)
    unstarted = [c for c in checks if c.state == "none"]
    if unstarted:
        return "GitHub never started: " + "; ".join(f"{c.name} {c.url}" for c in unstarted)
    if not checks:
        return "no check has run on its head commit yet"
    return None


def _mergeable(repo: str, url: str, *, delay: float) -> str:
    """GitHub's mergeability of `url`, read again up to `REREADS` times, `delay` seconds
    apart, while it is `UNKNOWN`."""
    state = github.read_mergeable(repo, url)
    for _ in range(REREADS):
        if state != "UNKNOWN":
            break
        time.sleep(delay)
        state = github.read_mergeable(repo, url)
    return state


def _record(root: Path, card: Card, note: str) -> None:
    """One `merge` history row on `card`, which stays in its stack."""
    path = _find_card_path(root, card.id, card.project)
    fresh = load_card(path)
    append_history(fresh, run_id=RUN_ID, from_=fresh.stack.value, to=fresh.stack.value, note=note)
    save_card(path, fresh)


def _live_holder(
    root: Path, project: Project, *, projects_dir: Path | None, now: float | None
) -> str | None:
    """A line naming the alive or stalled run that holds a project on `project`'s
    repository, or None when none does."""
    for other in project_list(root):
        if other.repo != project.repo:
            continue
        report, _ = claim_liveness(root, other, projects_dir=projects_dir, now=now)
        if report is not None and report.status in _HOLDING:
            return (
                f"project {other.slug!r} held by run {other.claim.run_id!r} "
                f"({report.status.value}: {report.reason})"
            )
    return None
