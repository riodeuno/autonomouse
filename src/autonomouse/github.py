"""Talk to GitHub for a card: read its pull request's checks
and unresolved review comments, retarget it to ``main`` once the branch it
targets has merged, compute which branch a new card's pull request should
target, and merge a green pull request (``autonomouse.merge``).

Every function here shells out to ``gh`` (logged in already, by the install
checklist's ``gh login`` step or ``autonomouse project new``'s own check,
``README.md``); nothing else reaches the network. ``docs/worker.md``
documents the same ``gh`` calls by hand — ``gh pr merge``, ``gh pr checks``,
``gh api`` — for a worker session that has no board and no Python import to
reach; this module is for callers that do: ``docs/board-schema.md``'s
base-branch rule, and the wake's PR follow-up (``autonomouse.followup``,
``sys-d8``).
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from autonomouse.board.schema import Card, Stack


class GitHubError(Exception):
    """A ``gh`` call failed, or returned something this module cannot use;
    the message is plain-language and safe to print."""


def _gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def _run_gh(args: list[str]) -> str:
    result = _gh(args)
    if result.returncode != 0:
        raise GitHubError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _parse(out: str, what: str) -> Any:
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise GitHubError(f"{what} returned non-JSON: {e}") from e


_GITHUB_URL = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:)([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def repo_slug(url: str) -> str:
    """``<owner>/<name>`` from a GitHub URL in either form,
    ``https://github.com/<owner>/<name>`` (``project.repo`` in
    ``docs/board-schema.md``) or ``git@github.com:<owner>/<name>.git`` (what
    ``autonomouse project new`` accepts and clones from). Raises ``GitHubError`` when
    ``url`` is not a ``github.com`` URL with an owner and a name.
    """
    match = _GITHUB_URL.match(url.strip())
    if not match:
        raise GitHubError(f"not a github.com repository URL: {url!r}")
    return f"{match.group(1)}/{match.group(2)}"


# ------------------------------------------------------------------ checks --

# The conclusions GitHub counts as passing, for a workflow run and a check run alike.
_PASSING = ("success", "neutral", "skipped")


@dataclass
class Check:
    """One check on a commit, from one of the three places GitHub keeps a commit's
    checks, which ``source`` names: ``actions`` is a GitHub Actions workflow run,
    ``status`` a commit status that a CI posted, ``check run`` a check run that an app
    other than GitHub Actions made. ``source`` ``overall`` is the one check
    ``read_checks`` adds for the checks the token cannot read; its ``note`` says so in
    plain words."""

    name: str
    status: str  # queued | in_progress | completed; pending for a commit status still going
    conclusion: str | None  # success | failure | error | cancelled | ...; None until completed
    url: str
    source: str = "actions"
    note: str = ""
    # The conclusions of the jobs of a GitHub Actions run that GitHub completed without
    # running one step of any of them (``read_actions_runs``); empty for every other check.
    unstarted_jobs: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        """``pending``, ``pass``, ``fail`` or ``none``. A neutral or skipped check passes,
        as GitHub counts it. A GitHub Actions run whose jobs ran no step never started, so
        it is ``none``, never ``fail``, whatever conclusion GitHub gave it (sys-v3)."""
        if self.status != "completed":
            return "pending"
        if self.unstarted_jobs:
            return "none"
        return "pass" if self.conclusion in _PASSING else "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "url": self.url,
            "state": self.state,
            "source": self.source,
            "note": self.note,
        }


def read_actions_runs(repo: str, *, head_sha: str) -> list[Check]:
    """The GitHub Actions workflow runs for ``head_sha`` in ``repo``
    (``<owner>/<name>``), from ``gh api repos/<repo>/actions/runs?head_sha=<sha>``.
    The query string must be part of the URL, not a `-f`/`-F` flag: those switch
    `gh api` to a `POST` request, which 404s on this read-only endpoint. An empty list
    means no workflow has run for this commit yet, or the repository has none — not
    that a check failed.

    For each run that completed and did not pass, its jobs are read too
    (``_unstarted_jobs``): when every job ran no step, GitHub never started the run — no
    runner took it, as when the account's Actions minutes ran out and every job failed
    in two seconds with no step (sys-v3) — so the run is not run (``Check.state`` ``none``)
    and its ``note`` says so."""
    out = _run_gh(["api", f"repos/{repo}/actions/runs?head_sha={head_sha}"])
    data = _parse(out, f"gh api actions/runs for {repo}@{head_sha}")
    checks = []
    for r in data.get("workflow_runs", []):
        check = Check(
            name=r["name"], status=r["status"], conclusion=r.get("conclusion"), url=r["html_url"]
        )
        if check.status == "completed" and check.conclusion not in _PASSING:
            check.unstarted_jobs = _unstarted_jobs(repo, r["id"])
            if check.unstarted_jobs:
                check.note = (
                    f"GitHub never started the {check.name} run: its "
                    f"{len(check.unstarted_jobs)} job(s) ran no step, so it counts as not run"
                )
        checks.append(check)
    return checks


def _unstarted_jobs(repo: str, run_id: int) -> list[str]:
    """The conclusions of the jobs of Actions run ``run_id`` when it has at least one job
    and none of them ran a step, else ``[]``. A run with no job at all, such as one whose
    workflow file GitHub could not read, is a real failure and gives ``[]``."""
    path = f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    jobs = _parse(_run_gh(["api", path]), f"gh api {path}").get("jobs", [])
    if jobs and not any(job.get("steps") for job in jobs):
        return [job.get("conclusion") or "" for job in jobs]
    return []


# Why the token cannot read each kind of check, for the `overall` check's note.
_REFUSED_WHY = {
    "commit statuses": "the token lacks the Commit statuses read permission",
    "check runs": "GitHub gives a fine-grained token no permission to read check runs",
}

_ROLLUP_QUERY = (
    "query($o:String!,$r:String!,$sha:GitObjectID!){repository(owner:$o,name:$r){"
    "object(oid:$sha){...on Commit{statusCheckRollup{state contexts{"
    "checkRunCountsByState{state count} statusContextCountsByState{state count}}}}}}}"
)
# GitHub's overall state of a commit's checks, as the state of one check.
_ROLLUP_STATES = {
    "SUCCESS": "pass",
    "PENDING": "pending",
    "EXPECTED": "pending",
    "FAILURE": "fail",
    "ERROR": "fail",
}
_RANK = {"none": -1, "pass": 0, "pending": 1, "fail": 2}
# The states GitHub counts a commit's check runs and commit statuses by, as the state of
# one check, the way `Check.state` reads them; every other counted state is a failure.
_COUNTED_PASSING = ("SUCCESS", "NEUTRAL", "SKIPPED")
_COUNTED_PENDING = ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "EXPECTED")
# For each state of the `overall` check: what the checks read show, what that makes the
# checks not read, and where to see them.
_UNREAD_NOTES = {
    "fail": (
        "no check this token can read failed",
        "one of the {} it cannot read failed",
        "its name and log show",
    ),
    "pending": (
        "no check this token can read failed or is still going",
        "one of the {} it cannot read is still going",
        "its name and log show",
    ),
    "pass": (
        "this token can read none of them",
        "the {} it cannot read passed",
        "their names and logs show",
    ),
}


def read_checks(repo: str, *, head_sha: str) -> list[Check]:
    """Every check on ``head_sha`` in ``repo`` (``<owner>/<name>``), from GitHub Actions
    and from any other CI: its Actions workflow runs (``read_actions_runs``), the latest
    commit status of each context a CI posted (``commits/<sha>/status``), and its check
    runs (``commits/<sha>/check-runs``) but GitHub Actions' own, which the workflow runs
    already report, one per workflow instead of one per job. An empty list means no
    check exists for the commit yet, or the repository has none — not that a check
    failed.

    GitHub answers a read the token may not make with HTTP 403: the check runs, for a
    fine-grained token on a repository it is granted, since GitHub gives a fine-grained
    token no Checks permission (sys-s85), and the commit statuses, for a token without
    Commit statuses read. When it refuses either, the overall state GitHub keeps for
    every check on the commit (GraphQL ``statusCheckRollup``, which such a token can
    read) stands in for what was not read: when that state is worse than every check
    read — ``FAILURE`` or ``ERROR`` while none failed, ``PENDING`` or ``EXPECTED`` while
    none failed or is still going, ``SUCCESS`` while none was read — one more check,
    source ``overall``, carries it, and its ``note`` names the checks the token cannot
    read and why. GitHub's overall state counts the jobs of an Actions run it never
    started as failed, so when the commit has such a run, the state stands on GitHub's
    counts of the commit's checks by state instead, without those jobs
    (``_state_without_unstarted``). Raises ``GitHubError`` when a read fails any other
    way."""
    page = f"https://github.com/{repo}/commit/{head_sha}"
    checks = read_actions_runs(repo, head_sha=head_sha)
    refused = []
    statuses = _read_unless_refused(f"repos/{repo}/commits/{head_sha}/status?per_page=100")
    if statuses is None:
        refused.append("commit statuses")
    else:
        checks += [
            Check(
                name=s["context"],
                status="pending" if s["state"] == "pending" else "completed",
                conclusion=None if s["state"] == "pending" else s["state"],
                url=s.get("target_url") or page,
                source="status",
            )
            for s in statuses.get("statuses", [])
        ]
    runs = _read_unless_refused(f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100")
    if runs is None:
        refused.append("check runs")
    else:
        checks += [
            Check(
                name=r["name"],
                status=r["status"],
                conclusion=r.get("conclusion"),
                url=r.get("html_url") or page,
                source="check run",
            )
            for r in runs.get("check_runs", [])
            if (r.get("app") or {}).get("slug") != "github-actions"
        ]
    if refused:
        unread = _unread_check(repo, head_sha, checks, refused, page)
        if unread is not None:
            checks.append(unread)
    return checks


def _read_unless_refused(path: str) -> Any | None:
    """``gh api <path>`` as JSON, or ``None`` when GitHub refuses the token the read
    (HTTP 403, "Resource not accessible by ..."). Any other 403, such as a rate limit,
    raises."""
    result = _gh(["api", path])
    if result.returncode != 0:
        if "HTTP 403" in result.stderr and "Resource not accessible by" in result.stderr:
            return None
        raise GitHubError(f"gh api {path} failed: {result.stderr.strip()}")
    return _parse(result.stdout, f"gh api {path}")


def _unread_check(
    repo: str, head_sha: str, checks: list[Check], refused: list[str], page: str
) -> Check | None:
    """The ``overall`` check (``read_checks``): GitHub's overall state of the commit's
    checks when it is worse than every check in ``checks``, else ``None``."""
    owner, name = repo.split("/", 1)
    # `-f`, not `-F`: `-F` would turn an all-digit owner, name or SHA into a number.
    out = _run_gh(
        [
            "api", "graphql",
            "-f", f"o={owner}",
            "-f", f"r={name}",
            "-f", f"sha={head_sha}",
            "-f", f"query={_ROLLUP_QUERY}",
        ]
    )
    data = _parse(out, f"gh api graphql statusCheckRollup for {repo}@{head_sha}")
    commit = ((data.get("data") or {}).get("repository") or {}).get("object") or {}
    rollup_data = commit.get("statusCheckRollup") or {}
    rollup = rollup_data.get("state")
    if rollup is None:
        return None
    if rollup not in _ROLLUP_STATES:
        raise GitHubError(f"GitHub's overall check state for {repo}@{head_sha} is {rollup!r}")
    overall = f"GitHub's overall state of this commit's checks is {rollup}"
    state = _ROLLUP_STATES[rollup]
    unstarted = [job for c in checks for job in c.unstarted_jobs]
    if unstarted:
        if not rollup_data.get("contexts"):
            raise GitHubError(f"GitHub gave no counts of the checks on {repo}@{head_sha}")
        state = _state_without_unstarted(rollup_data["contexts"], unstarted)
        overall += f" ({state} without the jobs of the GitHub Actions runs it never started)"
    if _RANK[state] <= max((_RANK[c.state] for c in checks), default=-1):
        return None
    unread = " and ".join(refused)
    why = "; ".join(_REFUSED_WHY[kind] for kind in refused)
    read, so, where = _UNREAD_NOTES[state]
    note = f"{overall}, and {read}, so {so.format(unread)} ({why}); {where} on the commit's page"
    return Check(
        name=f"{unread} this token cannot read",
        status="pending" if state == "pending" else "completed",
        conclusion={"fail": rollup.lower(), "pass": "success"}.get(state),
        url=page,
        source="overall",
        note=note,
    )


def _state_without_unstarted(contexts: dict[str, Any], unstarted: list[str]) -> str:
    """The state of a commit's checks from GitHub's counts of its check runs and commit
    statuses by state (``contexts``, the overall state's ``checkRunCountsByState`` and
    ``statusContextCountsByState``), less one check run for each job of an Actions run
    GitHub never started, by the job's conclusion in ``unstarted``: ``fail`` when any
    other counted check failed, else ``pending`` when any is still going, else ``pass``
    when any passed, else ``none``."""
    counts: Counter[str] = Counter()
    for kind in ("checkRunCountsByState", "statusContextCountsByState"):
        for row in contexts.get(kind) or []:
            counts[row["state"]] += row["count"]
    counts.subtract(conclusion.upper() for conclusion in unstarted)
    left = [name for name, count in counts.items() if count > 0]
    if any(name not in _COUNTED_PASSING + _COUNTED_PENDING for name in left):
        return "fail"
    if any(name in _COUNTED_PENDING for name in left):
        return "pending"
    return "pass" if left else "none"


# ---------------------------------------------------------- review comments --

_COMMENTS_QUERY = (
    "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){"
    "reviewThreads(first:100){nodes{isResolved comments(first:50){"
    "nodes{author{login} body path line createdAt url}}}} "
    "reviews(first:100){nodes{state body author{login} submittedAt url}} "
    "comments(first:100){nodes{author{login} body createdAt url}}"
    "}}}"
)


@dataclass
class ThreadComment:
    author: str
    body: str
    path: str | None
    line: int | None
    at: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "body": self.body,
            "path": self.path,
            "line": self.line,
            "url": self.url,
        }


@dataclass
class Review:
    author: str
    state: str
    body: str
    at: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"author": self.author, "state": self.state, "body": self.body, "url": self.url}


@dataclass
class Comment:
    author: str
    body: str
    at: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"author": self.author, "body": self.body, "url": self.url}


@dataclass
class UnresolvedReview:
    """What ``docs/worker.md``'s review phase reads before finishing a card:
    comments in review threads still unresolved, reviews requesting changes,
    and top-level conversation comments — each already narrowed to what needs
    an answer."""

    thread_comments: list[ThreadComment]
    change_requests: list[Review]
    comments: list[Comment]

    def is_empty(self) -> bool:
        return not (self.thread_comments or self.change_requests or self.comments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_comments": [c.to_dict() for c in self.thread_comments],
            "change_requests": [r.to_dict() for r in self.change_requests],
            "comments": [c.to_dict() for c in self.comments],
        }


def _login(author: dict[str, Any] | None) -> str:
    return author["login"] if author else "ghost"


_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")


def names_commit(body: str, commits: Iterable[str]) -> bool:
    """Whether ``body`` names one of ``commits`` (full SHAs) by a 7-to-40
    character hex prefix — the reply ``docs/worker.md`` has a worker post on
    each comment it addressed."""
    words = _SHA_RE.findall(body.lower())
    return any(sha.lower().startswith(word) for word in words for sha in commits)


def read_unresolved_review(
    repo: str,
    *,
    pr_number: int,
    exclude_login: str | None = None,
    commits: Iterable[str] | None = None,
) -> UnresolvedReview:
    """Read ``pr_number`` in ``repo`` (``<owner>/<name>``) for what a worker
    must answer before finishing: comments inside unresolved review threads,
    reviews with state ``CHANGES_REQUESTED``, and every top-level
    conversation comment, each with its ``url`` (the link a fix card or a reopened
    card names, ``docs/orchestrator/conflict-check.md``). ``exclude_login`` drops
    rows by that GitHub login — the worker's own account, so a reply it already
    posted does not count as new.

    ``commits`` (the pull request's commit SHAs) applies ``docs/worker.md``'s
    rule instead of a login — a comment answered by a reply naming the commit
    that addresses it is handled — for when the workers and the human share
    one GitHub account, so no login tells them apart. In an unresolved thread,
    every comment up to the last one naming a commit is handled; in the
    conversation, every comment and change request posted before the last
    conversation comment naming a commit is handled. A comment that names a
    commit is itself a reply and never needs an answer.
    """
    owner, name = repo.split("/", 1)
    out = _run_gh(
        [
            "api", "graphql",
            "-F", f"o={owner}",
            "-F", f"r={name}",
            "-F", f"n={pr_number}",
            "-f", f"query={_COMMENTS_QUERY}",
        ]
    )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise GitHubError(f"gh api graphql for {repo}#{pr_number} returned non-JSON: {e}") from e
    pr = (data.get("data") or {}).get("repository", {}).get("pullRequest")
    if pr is None:
        raise GitHubError(f"gh api graphql for {repo}#{pr_number} found no pull request: {data}")

    shas = list(commits) if commits is not None else None

    def _replied(body: str) -> bool:
        return shas is not None and names_commit(body, shas)

    thread_comments = []
    for t in pr["reviewThreads"]["nodes"]:
        if t["isResolved"]:
            continue
        nodes = t["comments"]["nodes"]
        replies = [i for i, c in enumerate(nodes) if _replied(c["body"])]
        for c in nodes[replies[-1] + 1 if replies else 0 :]:
            thread_comments.append(
                ThreadComment(
                    author=_login(c["author"]),
                    body=c["body"],
                    path=c.get("path"),
                    line=c.get("line"),
                    at=c.get("createdAt"),
                    url=c.get("url"),
                )
            )
    change_requests = [
        Review(
            author=_login(r["author"]),
            state=r["state"],
            body=r["body"],
            at=r.get("submittedAt"),
            url=r.get("url"),
        )
        for r in pr["reviews"]["nodes"]
        if r["state"] == "CHANGES_REQUESTED"
    ]
    comments = [
        Comment(
            author=_login(c["author"]), body=c["body"], at=c.get("createdAt"), url=c.get("url")
        )
        for c in pr["comments"]["nodes"]
    ]

    if shas is not None:
        last_reply = max((c.at or "" for c in comments if _replied(c.body)), default="")
        change_requests = [r for r in change_requests if (r.at or "") > last_reply]
        comments = [c for c in comments if (c.at or "") > last_reply and not _replied(c.body)]

    if exclude_login:
        thread_comments = [c for c in thread_comments if c.author != exclude_login]
        change_requests = [r for r in change_requests if r.author != exclude_login]
        comments = [c for c in comments if c.author != exclude_login]

    return UnresolvedReview(
        thread_comments=thread_comments, change_requests=change_requests, comments=comments
    )


# ------------------------------------------------------------ pull request --


@dataclass
class PullRequest:
    """What the wake's PR follow-up needs to know about one pull request."""

    number: int
    url: str
    state: str  # OPEN | CLOSED | MERGED
    head_sha: str
    base_branch: str
    commits: list[str]


def read_pr(repo: str, ref: str) -> PullRequest:
    """Read the pull request ``ref`` (a number, URL or branch) in ``repo``
    (``<owner>/<name>``): its number, URL, state, head commit, base branch and
    every commit SHA on it."""
    out = _run_gh(
        [
            "pr", "view", ref, "--repo", repo,
            "--json", "number,url,state,headRefOid,baseRefName,commits",
        ]
    )
    try:
        data = json.loads(out)
        return PullRequest(
            number=data["number"],
            url=data["url"],
            state=data["state"],
            head_sha=data["headRefOid"],
            base_branch=data["baseRefName"],
            commits=[c["oid"] for c in data["commits"]],
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise GitHubError(f"gh pr view {ref} --repo {repo} returned no usable PR: {out!r}") from e


# ------------------------------------------------------------- retargeting --


def pr_state(repo: str, ref: str) -> str:
    """The state (``OPEN``, ``CLOSED``, ``MERGED``) of the pull request for
    ``ref`` (a branch name, PR number, or PR URL) in ``repo``
    (``<owner>/<name>``)."""
    out = _run_gh(["pr", "view", ref, "--repo", repo, "--json", "state"])
    try:
        return json.loads(out)["state"]
    except (json.JSONDecodeError, KeyError) as e:
        raise GitHubError(f"gh pr view {ref} --repo {repo} returned no state: {out!r}") from e


def retarget_to_main(repo: str, *, pr_number: int, base_branch: str, main: str) -> bool:
    """Point ``pr_number`` in ``repo`` at ``main``, the project's default branch
    (``project.default_branch``; no default here), once ``base_branch`` — the
    branch it currently targets — has itself merged; return whether it
    retargeted.

    Checks ``base_branch``'s own pull request with ``pr_state``; if that
    state is not ``MERGED``, does nothing and returns ``False`` — the PR
    still needs to diff against ``base_branch`` because ``main`` does not
    have those commits yet. Once merged, runs
    ``gh pr edit <pr_number> --base <main>`` and returns ``True``. A no-op,
    returning ``False``, when ``base_branch`` already is ``main``.
    """
    if base_branch == main:
        return False
    if pr_state(repo, base_branch) != "MERGED":
        return False
    _run_gh(["pr", "edit", str(pr_number), "--repo", repo, "--base", main])
    return True


# ------------------------------------------------------------------- merging --


def read_mergeable(repo: str, ref: str) -> str:
    """Whether the pull request ``ref`` (a number, URL or branch) in ``repo``
    (``<owner>/<name>``) merges cleanly into its base, as GitHub reports it:
    ``MERGEABLE``, ``CONFLICTING``, or ``UNKNOWN`` while GitHub is still working it out
    (for a while after the pull request or its base changed)."""
    out = _run_gh(["pr", "view", ref, "--repo", repo, "--json", "mergeable"])
    try:
        return json.loads(out)["mergeable"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise GitHubError(f"gh pr view {ref} --repo {repo} returned no mergeable: {out!r}") from e


def merge_pr(url: str, *, head_sha: str) -> None:
    """Squash-merge the pull request at ``url`` into its base: ``gh pr merge <url>
    --squash``, with ``--match-head-commit <head_sha>`` so that GitHub merges only the
    commit whose checks the caller read, and refuses when the branch has moved since.
    Raises ``GitHubError`` carrying GitHub's reason when it refuses the merge."""
    _run_gh(["pr", "merge", url, "--squash", "--match-head-commit", head_sha])


def merge_commit(repo: str, ref: str) -> str:
    """The SHA of the commit a merged pull request ``ref`` in ``repo`` became on its
    base branch (its ``mergeCommit``)."""
    out = _run_gh(["pr", "view", ref, "--repo", repo, "--json", "mergeCommit"])
    try:
        return json.loads(out)["mergeCommit"]["oid"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise GitHubError(
            f"gh pr view {ref} --repo {repo} returned no merge commit: {out!r}"
        ) from e


# --------------------------------------------------------- base branch rule --


@dataclass
class BaseBranchPlan:
    """What a new card's pull request should target, and what else must be
    merged in first (``docs/orchestrator.md``, step 4: the `also_merge`
    branches the worker merges into its branch before it starts writing)."""

    base_branch: str
    also_merge: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"base_branch": self.base_branch, "also_merge": self.also_merge}


def compute_base_branch(
    dependencies: Iterable[Card],
    *,
    default_branch: str,
    merged_into: Callable[[Card], str | None],
    branch_merged: Callable[[Card, str], bool],
) -> BaseBranchPlan:
    """The base branch for a new card, from its dependency cards, already
    loaded (``docs/board-schema/card.md``'s ``base_branch`` row).

    ``dependencies`` holds every card the new card ``depends_on``, loaded
    from the board — not just ids, since the rule reads each one's
    ``stack``, ``pr``, ``branch`` and ``history``. The caller supplies two
    reads, so this function stays a pure, unit-testable decision with no
    network call of its own. ``merged_into(card)`` is the branch that
    dependency's PR (``card.pr``) merged into, its ``baseRefName``, or
    ``None`` while the PR has not merged (normally from ``read_pr``).
    ``branch_merged(card, branch)`` answers whether ``branch``'s own PR, in
    that dependency's repository, has merged; it is ``False`` while that PR
    is open or closed unmerged, and when none has been opened yet (normally
    ``pr_state(repo, branch) == "MERGED"``). It is asked only about a branch
    other than ``default_branch``.

    A dependency still waits to reach ``default_branch`` when it is ``done``
    with a ``pr`` that has not merged; its own ``branch`` carries its work.
    It also waits when its PR merged into another branch whose own PR has
    not merged — a wake's shared branch ``wake/<run id>``
    (``docs/orchestrator/conflict-check.md``) before the human merges that
    branch's PR; that branch carries its work and stands in for its
    ``branch``. A PR merged into ``default_branch``, or into a branch whose
    own PR has merged, has reached it. A no-code dependency (``pr`` is
    ``None``) and one not ``done`` never set the base.

    With no dependency waiting, the base is ``default_branch``. Otherwise it
    is the branch carrying the work of the one moved to ``done`` most
    recently (its ``history``); the branches carrying the rest go in
    ``also_merge``, oldest done first, for the caller to merge into the new
    branch after creating it. A branch is named once: two dependencies
    merged into the same branch give that branch once, and the base never
    repeats in ``also_merge``. Raises ``GitHubError`` when a dependency
    with an unmerged PR has no ``branch`` (should not happen: a card with a
    ``pr`` was started, and starting sets ``branch``), or when a waiting
    dependency has no history row moving it to ``done``.
    """
    waiting: list[tuple[Card, str]] = []  # (dependency, the branch carrying its work)
    for card in dependencies:
        if card.stack != Stack.DONE or not card.pr:
            continue
        into = merged_into(card)
        if into is None:
            if not card.branch:
                raise GitHubError(f"{card.id} is done with an unmerged pr but has no branch")
            waiting.append((card, card.branch))
        elif into != default_branch and not branch_merged(card, into):
            waiting.append((card, into))
    if not waiting:
        return BaseBranchPlan(base_branch=default_branch, also_merge=[])

    def _done_at(entry: tuple[Card, str]) -> str:
        """When the entry's card last moved into `done` from another stack; a history row
        that stays in `done`, such as a worktree removal or a `merge` row, is not a finish."""
        card = entry[0]
        moves = [h.at for h in card.history if h.to == "done" and h.from_ != "done"]
        if not moves:
            raise GitHubError(f"{card.id} is done but has no history row moving it to done")
        return max(moves)

    ordered = sorted(waiting, key=_done_at, reverse=True)
    base = ordered[0][1]
    also_merge: list[str] = []
    for _, branch in sorted(ordered[1:], key=_done_at):
        if branch != base and branch not in also_merge:
            also_merge.append(branch)
    return BaseBranchPlan(base_branch=base, also_merge=also_merge)
