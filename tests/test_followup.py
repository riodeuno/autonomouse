"""Tests for `autonomouse.followup`, the wake's PR follow-up (`sys-d8`).

Every `gh` call is faked at `subprocess.run` inside `autonomouse.github`, so the
follow-up runs through the real int-g2 parsing (`read_pr`, `read_checks`,
`read_unresolved_review`, `retarget_to_main`) without the network. `git` calls pass
through to real git, against a bare repository in the test's temporary folder that
stands in for GitHub's."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autonomouse import controller, followup, github
from autonomouse.board.commands import card_new, card_show, project_new
from autonomouse.board.schema import Phase, Stack
from autonomouse.board.store import load_card, save_card

REPO = "https://github.com/o/r"
PR_URL = "https://github.com/o/r/pull/7"
HEAD = "abcdef1234567890abcdef1234567890abcdef12"
FIX = "1234567deadbeef1234567deadbeef1234567dea"
REAL_RUN = subprocess.run


class FakeGh:
    """Replaces `subprocess.run` in `autonomouse.github`: answers each `gh` call by
    its shape and records every call. `runs` are the head commit's Actions runs,
    `statuses` its commit statuses and `check_runs` its check runs; with
    `refuse_check_runs` GitHub refuses the check runs as it refuses this machine's
    fine-grained token, and `rollup` is GitHub's overall state of the commit's checks,
    with its counts of check runs by state `rollup_counts`. Each failed run has one job
    with `steps` steps; 0 steps is a run GitHub never started."""

    def __init__(
        self,
        *,
        pr=None,
        runs=None,
        review=None,
        base_state="MERGED",
        statuses=(),
        check_runs=(),
        refuse_check_runs=False,
        rollup=None,
        rollup_counts=None,
        steps=1,
    ):
        self.pr = {
            "number": 7,
            "url": PR_URL,
            "state": "OPEN",
            "headRefOid": HEAD,
            "baseRefName": "main",
            "commits": [{"oid": FIX}, {"oid": HEAD}],
            **(pr or {}),
        }
        self.runs = runs if runs is not None else []
        self.review = review or {}
        self.base_state = base_state
        self.statuses = list(statuses)
        self.check_runs = list(check_runs)
        self.refuse_check_runs = refuse_check_runs
        self.rollup = rollup
        self.rollup_counts = rollup_counts or {}
        self.steps = steps
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd=None, capture_output=True, text=True, check=False):
        args = list(args)
        if args[0] == "git":
            return REAL_RUN(
                args, cwd=cwd, capture_output=capture_output, text=text, check=check
            )
        self.calls.append(args)
        if args[1:3] == ["pr", "view"] and args[3] == PR_URL:
            out = self.pr
        elif args[1:3] == ["pr", "view"]:
            out = {"state": self.base_state}
        elif args[1:3] == ["pr", "edit"]:
            out = None
        elif args[1] == "api" and "/actions/runs?" in args[2]:
            out = {"workflow_runs": self.runs}
        elif args[1] == "api" and "/jobs?" in args[2]:
            job = {"conclusion": "failure", "steps": [{"name": "s"}] * self.steps}
            out = {"total_count": 1, "jobs": [job]}
        elif args[1] == "api" and "/status?" in args[2]:
            out = {"state": "pending", "statuses": self.statuses, "total_count": 0}
        elif args[1] == "api" and "/check-runs?" in args[2] and self.refuse_check_runs:
            stderr = "gh: Resource not accessible by personal access token (HTTP 403)"
            return subprocess.CompletedProcess(args, 1, stdout="", stderr=stderr)
        elif args[1] == "api" and "/check-runs?" in args[2]:
            out = {"total_count": len(self.check_runs), "check_runs": self.check_runs}
        elif args[1:3] == ["api", "graphql"] and "statusCheckRollup" in args[-1]:
            rollup = {"state": self.rollup} if self.rollup else None
            if rollup and self.rollup_counts:
                rows = [{"state": k, "count": n} for k, n in self.rollup_counts.items()]
                rollup["contexts"] = {"checkRunCountsByState": rows}
            out = {"data": {"repository": {"object": {"statusCheckRollup": rollup}}}}
        elif args[1:3] == ["api", "graphql"]:
            out = {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {"nodes": self.review.get("threads", [])},
                            "reviews": {"nodes": self.review.get("reviews", [])},
                            "comments": {"nodes": self.review.get("comments", [])},
                        }
                    }
                }
            }
        else:
            raise AssertionError(f"unexpected gh call {args}")
        stdout = "" if out is None else json.dumps(out)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")


FAILED_RUN = {
    "id": 1,
    "name": "CI",
    "status": "completed",
    "conclusion": "failure",
    "html_url": "https://github.com/o/r/actions/runs/1",
}
PASSED_RUN = {**FAILED_RUN, "conclusion": "success"}
COMMENT = {
    "author": {"login": "riodeuno"},
    "body": "Please rename   this\nfunction.",
    "createdAt": "2026-09-22T10:00:00Z",
}


@pytest.fixture
def board(tmp_path: Path):
    root = tmp_path / "board"
    project = project_new(
        root, slug="p", name="P", goal="g", repo=REPO, repo_path=str(tmp_path),
        default_branch="main", priority=0,
    )
    card_new(root, card_id="p-a1", project="p", title="t", goal="g", test="t")
    path = root / "projects" / "p" / "cards" / "p-a1.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    card.pr = PR_URL
    card.branch = "card/p-a1"
    card.base_branch = "main"
    card.phase = Phase.REVIEW
    card.iteration = 1
    card.handoff.last_step = "opened the PR; waiting on review"
    save_card(path, card)
    return root, project


def _run(monkeypatch, board, fake) -> followup.FollowUp:
    root, project = board
    monkeypatch.setattr(github.subprocess, "run", fake)
    return followup.follow_up(root, card_show(root, "p-a1"), project, run_id="run-1")


def test_a_failing_check_and_one_comment_make_a_new_iteration_naming_both(monkeypatch, board):
    result = _run(
        monkeypatch, board, FakeGh(runs=[FAILED_RUN], review={"comments": [COMMENT]})
    )

    card = card_show(board[0], "p-a1")
    assert result.handed_back
    assert card.iteration == 2
    assert card.phase == Phase.WRITING
    assert card.handoff.run_id == "run-1"
    assert "check 'CI' failure (https://github.com/o/r/actions/runs/1)" in card.handoff.last_step
    assert 'comment by riodeuno: "Please rename this function."' in card.handoff.last_step
    assert card.handoff.last_step.startswith(f"PR follow-up at head {HEAD[:12]} ({PR_URL}):")
    assert "fix each failed check" in card.handoff.next_step
    assert "reply on the PR naming the commit" in card.handoff.next_step
    assert result.summary == (
        "1 failed check(s), 1 unanswered comment(s); handed back for the next iteration"
    )


def test_a_failing_commit_status_from_another_ci_hands_the_card_back_naming_it(
    monkeypatch, board
):
    """The project's CI posts commit statuses, not GitHub Actions runs: the one failing
    status is a failed check, named with its link, and the card goes back for another
    attempt. The token cannot read check runs, as this machine's cannot."""
    status = {
        "context": "ci/circleci: test",
        "state": "failure",
        "target_url": "https://circleci.com/gh/o/r/12",
    }
    fake = FakeGh(statuses=[status], refuse_check_runs=True, rollup="FAILURE")

    result = _run(monkeypatch, board, fake)

    card = card_show(board[0], "p-a1")
    assert result.handed_back
    assert (card.iteration, card.phase) == (2, Phase.WRITING)
    assert (
        "check 'ci/circleci: test' failure (https://circleci.com/gh/o/r/12)"
        in card.handoff.last_step
    )
    assert "any other check's log is at its link" in card.handoff.next_step


def test_a_failed_check_the_token_cannot_read_hands_the_card_back_and_says_why(
    monkeypatch, board
):
    """Every check the token can read passed, and GitHub's overall state is FAILURE: a
    check run from another CI failed, which the token cannot read. The card goes back, and
    its notes say plainly what the token cannot read and where the check shows."""
    fake = FakeGh(runs=[PASSED_RUN], refuse_check_runs=True, rollup="FAILURE")

    result = _run(monkeypatch, board, fake)

    card = card_show(board[0], "p-a1")
    assert result.handed_back
    assert card.iteration == 2
    last = card.handoff.last_step
    assert (
        f"check 'check runs this token cannot read' failure (https://github.com/o/r/commit/"
        f"{HEAD}): GitHub's overall state of this commit's checks is FAILURE, and no check "
        "this token can read failed, so one of the check runs it cannot read failed "
        "(GitHub gives a fine-grained token no permission to read check runs); its name "
        "and log show on the commit's page" in last
    )


@pytest.mark.parametrize(("steps", "handed_back", "iteration"), [(0, False, 1), (1, True, 2)])
def test_a_run_github_never_started_hands_nothing_back(
    monkeypatch, board, steps, handed_back, iteration
):
    """sys-v3's test: the PR's one Actions run failed with its job running no step
    (GitHub gave the account no runner), the token cannot read check runs, and GitHub's
    overall state is FAILURE from that job alone. The run never started, so no check
    failed: the card is left alone and its iteration stays. A run whose job ran a step and
    failed still hands the card back for the next iteration. The base branch handed both
    back."""
    fake = FakeGh(
        runs=[FAILED_RUN],
        refuse_check_runs=True,
        rollup="FAILURE",
        rollup_counts={"FAILURE": 1},
        steps=steps,
    )

    result = _run(monkeypatch, board, fake)

    card = card_show(board[0], "p-a1")
    assert result.handed_back is handed_back
    assert card.iteration == iteration
    assert [c.name for c in result.failed_checks] == (["CI"] if steps else [])
    assert any(call[2] == "repos/o/r/actions/runs/1/jobs?per_page=100" for call in fake.calls)


def test_green_checks_and_no_comments_leave_the_card_alone(monkeypatch, board):
    result = _run(monkeypatch, board, FakeGh(runs=[PASSED_RUN]))

    card = card_show(board[0], "p-a1")
    assert not result.handed_back
    assert (card.iteration, card.phase) == (1, Phase.REVIEW)
    assert card.handoff.last_step == "opened the PR; waiting on review"


def test_pending_checks_alone_hand_nothing_back(monkeypatch, board):
    pending = {**FAILED_RUN, "status": "in_progress", "conclusion": None}
    result = _run(monkeypatch, board, FakeGh(runs=[pending]))
    assert not result.handed_back
    assert card_show(board[0], "p-a1").iteration == 1


def test_a_comment_answered_by_a_reply_naming_a_commit_is_handled(monkeypatch, board):
    reply = {
        "author": {"login": "riodeuno"},
        "body": f"Renamed in {FIX[:7]}.",
        "createdAt": "2026-09-22T11:00:00Z",
    }
    thread = {
        "isResolved": False,
        "comments": {
            "nodes": [
                {**COMMENT, "path": "a.py", "line": 3},
                {**reply, "path": "a.py", "line": 3},
            ]
        },
    }
    result = _run(
        monkeypatch,
        board,
        FakeGh(runs=[PASSED_RUN], review={"comments": [COMMENT, reply], "threads": [thread]}),
    )
    assert not result.handed_back
    assert card_show(board[0], "p-a1").iteration == 1


def test_a_comment_after_the_reply_still_counts(monkeypatch, board):
    reply = {**COMMENT, "body": f"Fixed in {FIX[:10]}", "createdAt": "2026-09-22T11:00:00Z"}
    later = {**COMMENT, "body": "One more thing", "createdAt": "2026-09-22T12:00:00Z"}
    change = {
        "state": "CHANGES_REQUESTED",
        "body": "old",
        "author": {"login": "x"},
        "submittedAt": "2026-09-22T09:00:00Z",
    }
    result = _run(
        monkeypatch,
        board,
        FakeGh(runs=[], review={"comments": [COMMENT, reply, later], "reviews": [change]}),
    )
    assert result.handed_back
    last = card_show(board[0], "p-a1").handoff.last_step
    assert "One more thing" in last
    assert "rename" not in last and "change request" not in last


def test_a_repeat_follow_up_at_the_same_head_does_not_count_twice(monkeypatch, board):
    fake = FakeGh(runs=[FAILED_RUN], review={"comments": [COMMENT]})
    _run(monkeypatch, board, fake)
    _run(monkeypatch, board, fake)
    assert card_show(board[0], "p-a1").iteration == 2

    # A new comment at the same head refreshes the notes, still without a new attempt.
    fake.review["comments"].append({**COMMENT, "body": "and this"})
    _run(monkeypatch, board, fake)
    card = card_show(board[0], "p-a1")
    assert card.iteration == 2
    assert "and this" in card.handoff.last_step


def test_a_pr_whose_base_merged_is_retargeted_and_the_card_records_it(monkeypatch, board):
    root, _ = board
    path = root / "projects" / "p" / "cards" / "p-a1.json"
    card = load_card(path)
    card.base_branch = "card/p-a0"
    save_card(path, card)
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, runs=[PASSED_RUN], base_state="MERGED")

    result = _run(monkeypatch, board, fake)

    assert result.retargeted_to == "main"
    assert ["gh", "pr", "edit", "7", "--repo", "o/r", "--base", "main"] in fake.calls
    card = card_show(root, "p-a1")
    assert card.base_branch == "main"
    assert "retargeted: base_branch card/p-a0 -> main" in card.history[-1].note


def test_a_pr_whose_base_is_unmerged_keeps_its_base(monkeypatch, board):
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, runs=[PASSED_RUN], base_state="OPEN")
    result = _run(monkeypatch, board, fake)
    assert result.retargeted_to is None
    assert not any(c[1:3] == ["pr", "edit"] for c in fake.calls)


def test_a_merged_pr_is_left_alone(monkeypatch, board):
    fake = FakeGh(pr={"state": "MERGED"})
    result = _run(monkeypatch, board, fake)
    assert result.summary == "PR is MERGED; left alone"
    assert len(fake.calls) == 1


# ------------------------------------------------------------ done cards --
#
# sys-t13: a done card's PR stacked on another card's branch. `origin` is a bare
# repository standing in for GitHub; the card's worktree is a clone of it on the card's
# branch, which holds its base card/p-a0's original commits under its own.


def _git(cwd: Path, *args: str) -> str:
    return REAL_RUN(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _commit(cwd: Path, name: str, text: str, message: str) -> None:
    (cwd / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", message)


def _stack(tmp_path: Path, *, conflicting: bool) -> Path:
    """Build `origin` with main, card/p-a0 and card/p-a1 stacked on it, then merge
    card/p-a0 into main as one squash commit, as GitHub does; return the worktree of
    card/p-a1. With `conflicting`, card/p-a1 rewrites the line card/p-a0 added."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    for cwd in (seed,):
        _git(cwd, "config", "user.name", "t")
        _git(cwd, "config", "user.email", "t@t")
    _git(seed, "checkout", "-q", "-b", "main")
    _commit(seed, "a.txt", "one\n", "start")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", "card/p-a0")
    _commit(seed, "a.txt", "one\ntwo\n", "p-a0: add two")
    _git(seed, "push", "-q", "origin", "card/p-a0")
    _git(seed, "checkout", "-q", "-b", "card/p-a1")
    if conflicting:
        _commit(seed, "a.txt", "one\nTWO\n", "p-a1: shout two")
    else:
        _commit(seed, "b.txt", "b\n", "p-a1: add b")
    _git(seed, "push", "-q", "origin", "card/p-a1")
    _git(seed, "checkout", "-q", "main")
    _git(seed, "merge", "-q", "--squash", "card/p-a0")
    _git(seed, "commit", "-q", "-m", "p-a0: add two (#6)")
    _git(seed, "push", "-q", "origin", "main")

    worktree = tmp_path / "worktree"
    _git(tmp_path, "clone", "-q", "-b", "card/p-a1", str(origin), str(worktree))
    _git(worktree, "config", "user.name", "t")
    _git(worktree, "config", "user.email", "t@t")
    return worktree


def _done(root: Path, worktree: Path) -> None:
    path = root / "projects" / "p" / "cards" / "p-a1.json"
    card = load_card(path)
    card.stack = Stack.DONE
    card.base_branch = "card/p-a0"
    card.worktree = str(worktree)
    card.handoff.last_step = "verified; PR green"
    save_card(path, card)


def _run_done(monkeypatch, board, fake) -> followup.DoneFollowUp:
    root, project = board
    monkeypatch.setattr(github.subprocess, "run", fake)
    return followup.follow_up_done(root, card_show(root, "p-a1"), project, run_id="run-1")


def test_a_done_cards_pr_whose_base_merged_is_retargeted_and_main_merged_into_it(
    monkeypatch, board, tmp_path
):
    worktree = _stack(tmp_path, conflicting=False)
    _done(board[0], worktree)
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, base_state="MERGED")

    result = _run_done(monkeypatch, board, fake)

    assert ["gh", "pr", "edit", "7", "--repo", "o/r", "--base", "main"] in fake.calls
    origin = tmp_path / "origin.git"
    # main is merged into the PR's branch on origin, as a merge commit (never a rebase):
    # the branch keeps its own commit and gains main's squash as a parent.
    _git(origin, "merge-base", "--is-ancestor", "main", "card/p-a1")
    head = _git(origin, "log", "-1", "--format=%P%n%s", "card/p-a1").split("\n")
    assert len(head[0].split()) == 2
    assert head[1] == "p-a1: merge main after its base card/p-a0 merged"
    assert _git(origin, "log", "--format=%s", "card/p-a1").count("p-a1: add b\n") == 1
    card = card_show(board[0], "p-a1")
    assert card.stack == Stack.DONE
    assert card.base_branch == "main"
    assert card.history[-1].note == "retargeted: base_branch card/p-a0 -> main (its base merged)"
    assert (card.history[-1].from_, card.history[-1].to) == ("done", "done")
    assert result.summary == (
        "retargeted from card/p-a0 to main; merged main into the branch and pushed"
    )


def test_a_done_cards_pr_that_conflicts_with_main_is_reopened_naming_the_file(
    monkeypatch, board, tmp_path
):
    worktree = _stack(tmp_path, conflicting=True)
    _done(board[0], worktree)
    before = _git(tmp_path / "origin.git", "rev-parse", "card/p-a1")
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, base_state="MERGED")

    result = _run_done(monkeypatch, board, fake)

    assert ["gh", "pr", "edit", "7", "--repo", "o/r", "--base", "main"] in fake.calls
    card = card_show(board[0], "p-a1")
    assert (card.stack, card.position, card.phase) == (Stack.TODO, 0, Phase.WRITING)
    assert card.base_branch == "main"
    retarget, reopen = card.history[-2:]
    assert retarget.note == "retargeted: base_branch card/p-a0 -> main (its base merged)"
    assert (reopen.from_, reopen.to) == ("done", "todo")
    assert reopen.note == (
        f"reopened: its base card/p-a0 merged, its PR {PR_URL} now targets main, "
        "and merging main into card/p-a1 conflicts in a.txt"
    )
    assert card.handoff.next_step.startswith("reopened: merge origin/main into card/p-a1")
    assert "a.txt" in card.handoff.next_step
    # Nothing was pushed, and the worktree is left clean for the worker.
    assert _git(tmp_path / "origin.git", "rev-parse", "card/p-a1") == before
    assert _git(worktree, "status", "--porcelain") == ""
    assert result.conflicts == ["a.txt"]


def test_a_done_cards_pr_whose_base_is_unmerged_is_left_alone(monkeypatch, board, tmp_path):
    worktree = _stack(tmp_path, conflicting=False)
    _done(board[0], worktree)
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, base_state="OPEN")

    result = _run_done(monkeypatch, board, fake)

    assert not any(c[1:3] == ["pr", "edit"] for c in fake.calls)
    card = card_show(board[0], "p-a1")
    assert (card.stack, card.base_branch) == (Stack.DONE, "card/p-a0")
    assert result.summary == "its base card/p-a0 has not merged; left alone"


def test_a_done_cards_pr_github_already_retargeted_still_gets_main_merged_in(
    monkeypatch, board, tmp_path
):
    """GitHub retargets a PR itself when its merged base branch is deleted: the PR
    already targets main, the card still records card/p-a0, and main is not in the
    branch yet."""
    worktree = _stack(tmp_path, conflicting=False)
    _done(board[0], worktree)
    fake = FakeGh(pr={"baseRefName": "main"})

    result = _run_done(monkeypatch, board, fake)

    assert not any(c[1:3] == ["pr", "edit"] for c in fake.calls)
    _git(tmp_path / "origin.git", "merge-base", "--is-ancestor", "main", "card/p-a1")
    card = card_show(board[0], "p-a1")
    assert (card.stack, card.base_branch) == (Stack.DONE, "main")
    assert card.history[-1].note == "retargeted: base_branch card/p-a0 -> main (its base merged)"
    assert result.retargeted_from == "card/p-a0"


def test_the_wake_follows_up_only_stacked_done_cards_of_projects_no_run_holds(
    board, tmp_path
):
    """The wake's pass (`controller._follow_up_done_prs`) hands the done-card follow-up
    every done card with a PR whose base is not the default branch, skips held projects
    and leaves in_progress cards to the follow-up of resumed cards."""
    root, _ = board
    project_new(
        root, slug="q", name="Q", goal="g", repo=REPO, repo_path=str(tmp_path),
        default_branch="main", priority=1,
    )

    def card(card_id, project, stack, base, pr=PR_URL):
        card_new(root, card_id=card_id, project=project, title="t", goal="g", test="t")
        path = root / "projects" / project / "cards" / f"{card_id}.json"
        c = load_card(path)
        c.stack, c.base_branch, c.pr = stack, base, pr
        save_card(path, c)

    card("p-a2", "p", Stack.DONE, "card/p-a0")  # followed up
    card("p-a3", "p", Stack.DONE, "main")  # already on the default branch
    card("p-a4", "p", Stack.DONE, "card/p-a0", pr=None)  # no PR
    card("p-a5", "p", Stack.IN_PROGRESS, "card/p-a0")  # the resumed cards' follow-up
    card("q-a1", "q", Stack.DONE, "card/q-a0")  # its project is held
    seen, lines = [], []

    def follow_up_done(root, card, project, *, run_id):
        seen.append(card.id)
        return followup.DoneFollowUp(
            card_id=card.id,
            pr_state="OPEN",
            stacked_on="card/p-a0",
            retargeted_from="card/p-a0",
            retargeted_to="main",
        )

    changed = controller._follow_up_done_prs(
        root, skip={"q"}, only=None, follow_up_done=follow_up_done, out=lines.append
    )

    assert seen == changed == ["p-a2"]
    assert lines == [
        (
            "follow_up=p-a2: retargeted from card/p-a0 to main; merged main into the "
            "branch and pushed"
        )
    ]
