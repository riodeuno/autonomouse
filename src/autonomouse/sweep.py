"""`autonomouse github sweep <slug>`: one line per open pull request autonomouse opened
on a project's repository, and whether it needs work (`sys-s8`).

The pull requests autonomouse opens have as head branch the `branch` a card of the
project records (a card's PR, named by its kind of change, such as `feat/<slug>`), a
branch starting with `card/` (a card's PR from before that naming, sys-w18) or one
starting with `wake/` (a wake's interim PR), and that branch lives in the project's own
repository — autonomouse never opens a PR from a fork. `gh pr list` on the project's
repository finds the open ones, and every other open PR is left out, including a fork's
pull request whose branch happens to share such a name (`sys-s40`). For each, the sweep
reads:

- its card: the card on the board whose `pr` is the PR's URL, printed `<project>/<id>` when
  it is another project's (two projects can share one repository), else `-`; a wake's
  interim PR (head `wake/<run id>`) has no card, and its project is the one its run record
  `runs/<run id>.json` names, printed `<project>/-` when it is another project's;
- `mergeable`, as GitHub reports it: `MERGEABLE`, `CONFLICTING` or `UNKNOWN`;
- `conflicts`: the files that conflict when its branch merges into the project's default
  branch, from `git merge-tree` in the project's checkout after one `git fetch origin`.
  GitHub's `mergeable` compares a PR only with its base branch, so a PR stacked on another
  card's branch reads `MERGEABLE` while it conflicts with the default branch, and a PR
  just retargeted reads `UNKNOWN` (sys-u7). The fetch moves only the `origin/` branches and
  `merge-tree` writes no branch, index or working file, so the checkout stays as it was;
- the state of every check on its head commit, from GitHub Actions and from any other CI
  (`github.read_checks`, which reads GitHub's overall state of the commit's checks for
  the ones the token cannot read): `fail` when any check failed, else `pending` when any
  is still going, else `pass`, or `none` when no check exists for that commit or every
  one is a GitHub Actions run GitHub never started (its jobs ran no step, `sys-v3`); its
  `note` says plainly which checks the token cannot read when those decide that state,
  and which Actions runs GitHub never started, and is empty otherwise;
- the count of review comments still unanswered (`github.read_unresolved_review` with the
  PR's commits from `github.read_pr`: the rule workers answer by, a reply naming the
  commit that addresses a comment, `docs/worker.md`).

A line is a finding when the PR is `CONFLICTING`, it conflicts with the default branch,
its checks failed, or a comment is unanswered, and the PR is the project's: its card is one
of the project's, its run record names the project, or the board names no project for it
(a `wake/` PR with no run record).
Another project's PR is that project's finding, never this one's (`sys-s27`, `sys-s29`).
The sweep reads and reports only: it reopens, files and answers nothing; the orchestrator
acts on the findings (`docs/orchestrator/conflict-check.md`).

`autonomouse run` treats a project with a finding as having work: `pick`
(`autonomouse.controller`) asks `sweep` for a project with no resumable or ready card, and
a finding picks the project with no cards and hands its findings to Jev's `go` question
(sys-s22), so a wake starts and its orchestrator works the findings.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autonomouse import github
from autonomouse.board.commands import card_list, project_show
from autonomouse.board.runs import load_run, run_path

# The head-branch prefixes of the pull requests autonomouse opens with no card recording
# the branch: a wake's interim PR, and a card's PR from before branches were named by kind.
OWN_BRANCH_PREFIXES = ("card/", "wake/")

_LIST_FIELDS = "number,url,headRefName,headRefOid,mergeable,isCrossRepository"
# `gh pr list` returns 30 rows unless told otherwise; "every open pull request" needs more.
_LIST_LIMIT = "200"
# The first line `git merge-tree --write-tree` prints: the merged tree's id.
_TREE_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


@dataclass
class Line:
    """One open pull request autonomouse opened, as the sweep reports it."""

    number: int
    url: str
    head_branch: str
    head_sha: str
    # the card whose `pr` is `url`, `<project>/<id>` when it is another project's; else "-",
    # or `<project>/-` for another project's wake PR
    card_id: str
    mergeable: str  # MERGEABLE | CONFLICTING | UNKNOWN
    runs: str  # pass | fail | pending | none: the state of every check on the head commit
    unanswered: int
    other_project: bool = False  # the PR is another project's, whose own sweep has it
    # which checks the token cannot read, when GitHub's overall state for them decides
    # `runs`, and which Actions runs GitHub never started
    note: str = ""
    # the files that conflict when the PR's branch merges into the default branch
    conflicts: list[str] = field(default_factory=list)

    @property
    def finding(self) -> bool:
        """Whether the swept project has work on the PR: it conflicts with its base branch
        or the default branch, its checks failed, or a comment is unanswered, and it is not
        another project's PR."""
        if self.other_project:
            return False
        return (
            self.mergeable == "CONFLICTING"
            or bool(self.conflicts)
            or self.runs == "fail"
            or self.unanswered > 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "url": self.url,
            "head_branch": self.head_branch,
            "head_sha": self.head_sha,
            "card_id": self.card_id,
            "mergeable": self.mergeable,
            "runs": self.runs,
            "unanswered": self.unanswered,
            "conflicts": self.conflicts,
            "finding": self.finding,
            "note": self.note,
        }

    def plain(self) -> str:
        conflicts = f"conflicts={','.join(self.conflicts)} " if self.conflicts else ""
        return (
            f"{self.card_id} {self.mergeable} runs={self.runs} unanswered={self.unanswered} "
            f"{conflicts}{'finding' if self.finding else 'ok'} {self.url}"
            + (f" {self.note}" if self.note else "")
        )


def list_open_prs(repo: str) -> list[dict[str, Any]]:
    """The open pull requests of `repo` (`<owner>/<name>`) from `gh pr list` (up to 200 —
    its default is 30), each a dict
    with `number`, `url`, `headRefName`, `headRefOid`, `mergeable` and `isCrossRepository`
    (true when the branch lives in a fork rather than `repo` itself). Raises
    `github.GitHubError` when `gh` fails or prints something else."""
    args = [
        "pr", "list", "--repo", repo, "--state", "open", "--limit", _LIST_LIMIT,
        "--json", _LIST_FIELDS,
    ]
    result = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise github.GitHubError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise github.GitHubError(f"gh {' '.join(args)} returned non-JSON: {e}") from e
    if not isinstance(prs, list):
        raise github.GitHubError(f"gh {' '.join(args)} returned no list: {result.stdout!r}")
    return prs


def recorded_branches(root: Path, slug: str) -> set[str]:
    """The `branch` every card of project `slug` records. A card's branch is named by its
    kind of change (`feat/<slug>`, `fix/<slug>`, ...; docs/orchestrator.md, "A wake",
    step 4), a prefix a human's branch can share too, so only a recorded name is a card's."""
    return {c.branch for c in card_list(root, project=slug) if c.branch}


def is_own_branch(branch: str, recorded: set[str]) -> bool:
    """Whether a pull request's head `branch` is autonomouse's own: a branch a card
    records (`recorded_branches`), or one starting with `card/` or `wake/`
    (`OWN_BRANCH_PREFIXES`). The sweep and the wake-end report
    (`autonomouse.controller`) both read heads through it."""
    return branch in recorded or branch.startswith(OWN_BRANCH_PREFIXES)


def _git(repo_path: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_path, *args], capture_output=True, text=True, check=False
    )


def fetch_origin(repo_path: str) -> None:
    """`git fetch origin` in the checkout `repo_path`: it moves only the checkout's
    `origin/` branches, never a local branch, the index or a working file. Raises
    `github.GitHubError` when git fails."""
    result = _git(repo_path, ["fetch", "--quiet", "origin"])
    if result.returncode != 0:
        raise github.GitHubError(
            f"git fetch origin in {repo_path} failed: {result.stderr.strip()}"
        )


def conflicts_with(repo_path: str, *, default_branch: str, head_branch: str) -> list[str]:
    """The files that conflict when `origin/<head_branch>` merges into
    `origin/<default_branch>`, empty when they merge cleanly, from `git merge-tree
    --write-tree` in the checkout `repo_path`, which adds objects to its store and writes no
    branch, index or working file. Raises `github.GitHubError` when git cannot merge the two
    at all, such as when either branch is missing from `origin/`."""
    args = [
        "merge-tree", "--write-tree", "--name-only", "--no-messages",
        f"origin/{default_branch}", f"origin/{head_branch}",
    ]
    result = _git(repo_path, args)
    lines = result.stdout.splitlines()
    # Exit 1 means conflicts only when the merged tree's id comes first; git also exits 1
    # for a branch it cannot find, printing nothing.
    if result.returncode not in (0, 1) or not lines or not _TREE_ID.fullmatch(lines[0]):
        raise github.GitHubError(
            f"git {' '.join(args)} in {repo_path} failed: {result.stderr.strip()}"
        )
    return [line for line in lines[1:] if line]


def runs_state(checks: list[github.Check]) -> str:
    """`fail`, `pending`, `pass` or `none` for every check on one commit. A GitHub Actions
    run GitHub never started (`github.Check.state` `none`) counts as no check at all."""
    states = {c.state for c in checks} - {"none"}
    if "fail" in states:
        return "fail"
    if "pending" in states:
        return "pending"
    return "pass" if states else "none"


def sweep(root: Path, slug: str) -> list[Line]:
    """Every open pull request autonomouse opened on project `slug`'s repository, in
    `gh pr list` order. Raises `CommandError` for an unknown project,
    `github.GitHubError` when a `gh` or `git` call fails, and `BoardValidationError` when a
    `wake/` PR's run record is not a valid one.

    A PR's card is looked up among every card on the board: a PR's URL names its
    repository, so only the cards of the projects that share this one can match. A `wake/`
    PR's project is the one its run record names (`_wake_project`).

    A PR is autonomouse's own when its head branch is the `branch` a card of project
    `slug` records, or starts with `card/` or `wake/`. A pull request whose branch lives in
    a fork (`isCrossRepository`) is left out even then: autonomouse only ever opens a
    branch in the project's own repository, so a fork's PR was never one of its own,
    whatever it is named (`sys-s40`).

    Each PR's branch is also merged with the default branch by `git merge-tree` in the
    project's checkout (`project.repo_path`), after one `git fetch origin` there
    (`conflicts_with`), whatever GitHub reports as `mergeable` (sys-u7)."""
    project = project_show(root, slug)
    repo = github.repo_slug(project.repo)
    by_pr = {c.pr: c for c in card_list(root) if c.pr}
    recorded = recorded_branches(root, slug)
    own = [
        pr
        for pr in list_open_prs(repo)
        if is_own_branch(pr["headRefName"], recorded) and not pr["isCrossRepository"]
    ]
    if own:
        fetch_origin(project.repo_path)
    lines = []
    for pr in own:
        conflicts = conflicts_with(
            project.repo_path,
            default_branch=project.default_branch,
            head_branch=pr["headRefName"],
        )
        checks = github.read_checks(repo, head_sha=pr["headRefOid"])
        details = github.read_pr(repo, str(pr["number"]))
        review = github.read_unresolved_review(
            repo, pr_number=pr["number"], commits=details.commits
        )
        card = by_pr.get(pr["url"])
        if card is not None:
            owner, card_id = card.project, card.id
        else:
            owner, card_id = _wake_project(root, pr["headRefName"]), "-"
        other = owner is not None and owner != slug
        if other:
            card_id = f"{owner}/{card_id}"
        lines.append(
            Line(
                number=pr["number"],
                url=pr["url"],
                head_branch=pr["headRefName"],
                head_sha=pr["headRefOid"],
                card_id=card_id,
                mergeable=pr["mergeable"],
                runs=runs_state(checks),
                unanswered=len(review.thread_comments)
                + len(review.change_requests)
                + len(review.comments),
                other_project=other,
                # two runs of one workflow (a push and a pull request) say the same once
                note="; ".join(dict.fromkeys(c.note for c in checks if c.note)),
                conflicts=conflicts,
            )
        )
    return lines


def _wake_project(root: Path, head_branch: str) -> str | None:
    """The project named by the run record `runs/<run id>.json` of a `wake/<run id>` head;
    None for any other head, for a run id the board has no record of, and for a record that
    names no project."""
    if not head_branch.startswith("wake/"):
        return None
    run_id = head_branch.removeprefix("wake/")
    if not run_path(root, run_id).is_file():
        return None
    return load_run(root, run_id).project
