"""The wake's PR follow-up (`sys-d8`): before the orchestrator resumes an
in_progress card that has a PR, read what GitHub says about that PR and put it
in the card's notes, so the worker that resumes the card works on it.

For one card, in order:

1. Read the PR (`github.read_pr`). Anything but `OPEN` is left alone: a merged
   or closed PR on an in_progress card is the orchestrator's to judge.
2. When the branch the PR targets is not the project's default branch and has
   itself merged, retarget the PR to the default branch
   (`github.retarget_to_main`) and record the new `base_branch` on the card
   (`card_retarget`).
3. Read every check on the PR's head commit, from GitHub Actions and from any
   other CI (`github.read_checks`; when the token cannot read some of them, the
   check that stands for those says so), and the comments nobody has answered yet
   (`github.read_unresolved_review` with the PR's commits: a comment answered by a
   reply naming a commit is handled, `docs/worker.md`).
   A GitHub Actions run GitHub never started (its jobs ran no step, `sys-v3`) is
   not run, never a failed check.
4. When a check failed or a comment is unanswered, hand the card back: the
   attempt failed, so `iteration` goes up by one, `phase` goes to `writing`,
   and the notes name every failed check and every unanswered comment. When
   the card's notes are already this follow-up's notes for the same head
   commit — no worker has worked on it since — the notes are refreshed and
   `iteration` stays as it is, so a wake that repeats the follow-up does not
   count the same failure twice.

Pending checks alone hand nothing back: the resumed worker waits for them in
its ci phase.

A `done` card has no worker to hand anything back to, so its follow-up
(`follow_up_done`, `sys-t13`) only keeps its stacked PR mergeable once the PR's
base has merged. Every base merges as a squash, so the stacked branch still
holds its base's original commits, and GitHub shows the conflict only after the
retarget. For one
done card whose PR targets a branch other than the default branch:

1. Read the PR; anything but `OPEN`, or a base that has not merged, is left
   alone.
2. Retarget the PR to the default branch and record the old and new base on the
   card (`card_retarget`). A PR that already targets the default branch while
   the card still records the stacked base (GitHub retargets it when the merged
   base branch is deleted) only gets the record.
3. In the card's worktree, merge `origin/<default branch>` into the card's
   branch (a merge, never a rebase) and push it.
4. When that merge conflicts, abort it and reopen the card (`card_move` to
   `todo`, at position 0) with a note naming the conflicting files, so a worker
   resolves them.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from autonomouse import github
from autonomouse.board.commands import card_move, card_progress, card_retarget
from autonomouse.board.schema import Card, Phase, Project, Stack

_BODY_CHARS = 200


@dataclass
class FollowUp:
    """What the follow-up saw and did for one card; `summary` is one line for the
    wake's output."""

    card_id: str
    pr_state: str
    retargeted_to: str | None = None
    failed_checks: list[github.Check] = field(default_factory=list)
    review: github.UnresolvedReview | None = None
    handed_back: bool = False

    @property
    def summary(self) -> str:
        if self.pr_state != "OPEN":
            return f"PR is {self.pr_state}; left alone"
        parts = []
        if self.retargeted_to:
            parts.append(f"retargeted to {self.retargeted_to}")
        comments = _count(self.review)
        parts.append(f"{len(self.failed_checks)} failed check(s), {comments} unanswered comment(s)")
        parts.append("handed back for the next iteration" if self.handed_back else "nothing to hand back")
        return "; ".join(parts)


def _count(review: github.UnresolvedReview | None) -> int:
    if review is None:
        return 0
    return len(review.thread_comments) + len(review.change_requests) + len(review.comments)


def _quote(body: str) -> str:
    text = " ".join(body.split())
    if len(text) > _BODY_CHARS:
        text = text[: _BODY_CHARS - 1] + "…"
    return f'"{text}"'


def _prefix(pr: github.PullRequest) -> str:
    return f"PR follow-up at head {pr.head_sha[:12]} ({pr.url}):"


def _notes(
    pr: github.PullRequest, failed: list[github.Check], review: github.UnresolvedReview
) -> tuple[str, str]:
    items = [
        f"check {c.name!r} {c.conclusion or 'failed'} ({c.url})" + (f": {c.note}" if c.note else "")
        for c in failed
    ]
    items += [
        f"review comment by {c.author} on {c.path}:{c.line}: {_quote(c.body)}"
        for c in review.thread_comments
    ]
    items += [
        f"change request by {r.author}: {_quote(r.body)}" for r in review.change_requests
    ]
    items += [f"comment by {c.author}: {_quote(c.body)}" for c in review.comments]
    last_step = f"{_prefix(pr)} " + "; ".join(items) + "."
    next_step = (
        "Address the PR follow-up in last_step: "
        + (
            "fix each failed check (an Actions run's log: gh run view <run id> --log-failed; "
            "any other check's log is at its link); "
            if failed
            else ""
        )
        + (
            "answer each comment with a change and a reply on the PR naming the commit "
            "that addresses it; "
            if _count(review)
            else ""
        )
        + "then testing, ci and review again."
    )
    return last_step, next_step


def follow_up(root: Path, card: Card, project: Project, *, run_id: str) -> FollowUp:
    """Follow up on `card`'s PR, as the module docstring describes. `card.pr` must
    be set. Raises `github.GitHubError` when a `gh` call fails."""
    repo = github.repo_slug(project.repo)
    pr = github.read_pr(repo, card.pr)
    result = FollowUp(card_id=card.id, pr_state=pr.state)
    if pr.state != "OPEN":
        return result

    main = project.default_branch
    if pr.base_branch != main and github.retarget_to_main(
        repo, pr_number=pr.number, base_branch=pr.base_branch, main=main
    ):
        card_retarget(root, card.id, base_branch=main, project=card.project, run_id=run_id)
        result.retargeted_to = main

    checks = github.read_checks(repo, head_sha=pr.head_sha)
    result.failed_checks = [c for c in checks if c.state == "fail"]
    result.review = github.read_unresolved_review(
        repo, pr_number=pr.number, commits=pr.commits
    )
    if not result.failed_checks and result.review.is_empty():
        return result

    last_step, next_step = _notes(pr, result.failed_checks, result.review)
    previous = card.handoff.last_step or ""
    repeat = card.phase == Phase.WRITING and previous.startswith(_prefix(pr))
    if repeat and previous == last_step:
        result.handed_back = True
        return result
    card_progress(
        root,
        card.id,
        project=card.project,
        phase=Phase.WRITING.value,
        iteration=card.iteration if repeat else card.iteration + 1,
        last_step=last_step,
        next_step=next_step,
        run_id=run_id,
    )
    result.handed_back = True
    return result


class MergeError(Exception):
    """A `git` step of a done card's follow-up failed for a reason other than a
    merge conflict; the message carries git's own stderr."""


@dataclass
class DoneFollowUp:
    """What the follow-up saw and did for one done card; `summary` is one line
    for the wake's output."""

    card_id: str
    pr_state: str
    stacked_on: str | None = None
    retargeted_from: str | None = None
    retargeted_to: str | None = None
    conflicts: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.pr_state != "OPEN":
            return f"PR is {self.pr_state}; left alone"
        if self.stacked_on is None:
            return "not stacked on another branch; left alone"
        if self.retargeted_to is None:
            return f"its base {self.stacked_on} has not merged; left alone"
        moved = f"retargeted from {self.retargeted_from} to {self.retargeted_to}"
        if self.conflicts:
            return (
                f"{moved}; merging {self.retargeted_to} conflicts in "
                f"{', '.join(self.conflicts)}; reopened to todo"
            )
        return f"{moved}; merged {self.retargeted_to} into the branch and pushed"


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, check=False
    )


def _git_ok(worktree: Path, *args: str) -> str:
    result = _git(worktree, *args)
    if result.returncode != 0:
        raise MergeError(f"git {' '.join(args)} in {worktree} failed: {result.stderr.strip()}")
    return result.stdout


def _merge_main(worktree: Path, *, card: Card, main: str, old_base: str) -> list[str]:
    """Merge `origin/<main>` into the card's branch in `worktree` and push it;
    return the conflicting files, after aborting the merge, when it conflicts
    (nothing is pushed then)."""
    _git_ok(worktree, "fetch", "-q", "origin", main)
    merged = _git(
        worktree,
        "merge",
        "--no-edit",
        "-m",
        f"{card.id}: merge {main} after its base {old_base} merged",
        f"origin/{main}",
    )
    if merged.returncode != 0:
        conflicts = _git_ok(worktree, "diff", "--name-only", "--diff-filter=U").splitlines()
        if not conflicts:
            raise MergeError(
                f"git merge origin/{main} in {worktree} failed: "
                f"{(merged.stderr or merged.stdout).strip()}"
            )
        _git_ok(worktree, "merge", "--abort")
        return conflicts
    _git_ok(worktree, "push", "-q", "origin", f"HEAD:refs/heads/{card.branch}")
    return []


def follow_up_done(root: Path, card: Card, project: Project, *, run_id: str) -> DoneFollowUp:
    """Follow up on done `card`'s stacked PR, as the module docstring describes.
    `card.pr` must be set. Raises `github.GitHubError` when a `gh` call fails and
    `MergeError` when a `git` step fails for any reason but a conflict; a card
    with no worktree on disk raises `MergeError` before anything changes."""
    if card.stack != Stack.DONE:
        raise ValueError(f"{card.id} is {card.stack.value}, not done")
    repo = github.repo_slug(project.repo)
    pr = github.read_pr(repo, card.pr)
    result = DoneFollowUp(card_id=card.id, pr_state=pr.state)
    main = project.default_branch
    if pr.state != "OPEN":
        return result
    # A PR that already targets `main` while the card records a stacked base was
    # retargeted by GitHub when its merged base branch was deleted, or by hand; it
    # still needs `main` merged in.
    old = pr.base_branch if pr.base_branch != main else card.base_branch
    if old in (None, main):
        return result
    result.stacked_on = old
    worktree = Path(card.worktree) if card.worktree else None
    if worktree is None or not worktree.is_dir():
        raise MergeError(f"{card.id} has no worktree on disk ({card.worktree}) to merge {main} in")
    if pr.base_branch != main and not github.retarget_to_main(
        repo, pr_number=pr.number, base_branch=old, main=main
    ):
        return result
    card_retarget(root, card.id, base_branch=main, project=card.project, run_id=run_id)
    result.retargeted_from, result.retargeted_to = old, main

    result.conflicts = _merge_main(worktree, card=card, main=main, old_base=old)
    if result.conflicts:
        files = ", ".join(result.conflicts)
        card_move(
            root,
            card.id,
            Stack.TODO.value,
            project=card.project,
            note=(
                f"reopened: its base {old} merged, its PR {pr.url} now targets {main}, "
                f"and merging {main} into {card.branch} conflicts in {files}"
            ),
            next_step=(
                f"reopened: merge origin/{main} into {card.branch}, resolving the "
                f"conflicts in {files} and keeping both sides' changes; rerun the "
                f"card's test and push to PR {pr.url}"
            ),
            run_id=run_id,
        )
    return result
