"""Tests for `autonomouse.github`: every `gh` call is mocked (`subprocess.run`) — no
test here reaches the network or runs the real `gh` binary. The real-repo proof (read a
PR's checks and comments, retarget one) is done by hand against
https://github.com/riodeuno/autonomouse, per the card's test, and recorded in the card's
verification note, not repeated here."""

from __future__ import annotations

import inspect
import json
import subprocess
from dataclasses import dataclass

import pytest

from autonomouse import cli, github, sweep
from autonomouse.board.commands import card_new, project_new
from autonomouse.board.schema import Assigned, Card, Handoff, HistoryEntry, Size, Stack
from autonomouse.board.store import load_card, save_card


@dataclass
class _Call:
    args: list[str]
    cwd: str | None


class _FakeGh:
    """Replaces `subprocess.run`: `responses` is a queue of `(returncode, stdout, stderr)`
    consumed in call order; every call is recorded in `.calls`."""

    def __init__(self, responses: list[tuple[int, str, str]]):
        self.responses = list(responses)
        self.calls: list[_Call] = []

    def __call__(self, args, *, cwd=None, capture_output=True, text=True, check=False):
        self.calls.append(_Call(args=list(args), cwd=cwd))
        if not self.responses:
            raise AssertionError(f"no fake response queued for {args}")
        returncode, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------- Check.state --


@pytest.mark.parametrize(
    ("status", "conclusion", "expected"),
    [
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
        ("pending", None, "pending"),
        ("completed", "success", "pass"),
        ("completed", "neutral", "pass"),
        ("completed", "skipped", "pass"),
        ("completed", "failure", "fail"),
        ("completed", "error", "fail"),
        ("completed", "cancelled", "fail"),
    ],
)
def test_check_state(status, conclusion, expected):
    check = github.Check(name="CI", status=status, conclusion=conclusion, url="https://x")
    assert check.state == expected


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_a_run_whose_jobs_ran_no_step_is_none_whatever_its_conclusion(conclusion):
    check = github.Check(
        name="CI", status="completed", conclusion=conclusion, url="https://x",
        unstarted_jobs=[conclusion],
    )
    assert check.state == "none"


# ------------------------------------------------------------ read_actions_runs --


def test_read_actions_runs_parses_workflow_runs(monkeypatch):
    body = json.dumps(
        {
            "workflow_runs": [
                {
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/riodeuno/autonomouse/actions/runs/1",
                }
            ]
        }
    )
    fake = _FakeGh([(0, body, "")])
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_actions_runs("riodeuno/autonomouse", head_sha="abc123")

    assert checks == [
        github.Check(
            name="CI",
            status="completed",
            conclusion="success",
            url="https://github.com/riodeuno/autonomouse/actions/runs/1",
        )
    ]
    assert fake.calls[0].args == [
        "gh", "api", "repos/riodeuno/autonomouse/actions/runs?head_sha=abc123",
    ]


def test_read_actions_runs_empty_workflow_runs_is_empty_list(monkeypatch):
    monkeypatch.setattr(
        github.subprocess, "run", _FakeGh([(0, json.dumps({"workflow_runs": []}), "")])
    )

    assert github.read_actions_runs("riodeuno/autonomouse", head_sha="abc123") == []


# ------------------------------------------------------------------ read_checks --

_REPO = "o/r"
_SHA = "c0ffee" + "0" * 34
_PAGE = f"https://github.com/{_REPO}/commit/{_SHA}"
_RUNS_PATH = f"repos/{_REPO}/actions/runs?head_sha={_SHA}"
_STATUS_PATH = f"repos/{_REPO}/commits/{_SHA}/status?per_page=100"
_CHECK_RUNS_PATH = f"repos/{_REPO}/commits/{_SHA}/check-runs?per_page=100"
# What this machine's fine-grained token got from GitHub for a check-runs read on
# 2026-09-27 (`gh api repos/riodeuno/autonomouse/commits/e2b9c78.../check-runs`, header
# `X-Accepted-Github-Permissions: checks=read`), and for a statuses read when a token
# lacks Commit statuses read (the same message, the shape sys-s85's tests use).
_REFUSED = (
    1,
    '{"message":"Resource not accessible by personal access token","status":"403"}',
    "gh: Resource not accessible by personal access token (HTTP 403)",
)


_JOBS_PATH = f"repos/{_REPO}/actions/runs/1/jobs?per_page=100"


def _run(name: str, conclusion: str | None) -> dict:
    return {
        "id": 1,
        "name": name,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "html_url": f"https://github.com/{_REPO}/actions/runs/1",
    }


def _job(conclusion: str, *, steps: int) -> dict:
    """One job of Actions run 1 as GitHub lists it. A job no runner took has no step:
    what GitHub gave run 36347628191 of riodeuno/autonomouse on 2026-09-27 (job `check`,
    conclusion failure, `steps: []`, `runner_id: 0`) once the account's Actions minutes
    ran out."""
    return {
        "name": "check",
        "status": "completed",
        "conclusion": conclusion,
        "runner_id": 7 if steps else 0,
        "steps": [
            {"name": f"step {i}", "status": "completed", "conclusion": conclusion}
            for i in range(steps)
        ],
    }


def _status(context: str, state: str, target_url: str | None = "https://ci.example/1") -> dict:
    return {"context": context, "state": state, "target_url": target_url}


def _check_run(name: str, conclusion: str | None, app: str) -> dict:
    return {
        "name": name,
        "status": "completed" if conclusion else "queued",
        "conclusion": conclusion,
        "html_url": f"https://github.com/{_REPO}/runs/7",
        "app": {"slug": app},
    }


def _rollup(state: str | None, **check_runs: int) -> tuple[int, str, str]:
    """GitHub's overall state of the commit's checks, with its counts of check runs by
    state (`FAILURE=2`) when given; GitHub lists every state, most with count 0."""
    rollup = None
    if state:
        rollup = {"state": state}
        if check_runs:
            rollup["contexts"] = {
                "checkRunCountsByState": [
                    {"state": "SUCCESS", "count": 0},
                    *({"state": name, "count": n} for name, n in check_runs.items()),
                ],
                "statusContextCountsByState": [{"state": "FAILURE", "count": 0}],
            }
    commit = {"statusCheckRollup": rollup}
    return 0, json.dumps({"data": {"repository": {"object": commit}}}), ""


class _FakeGhApi:
    """Replaces `subprocess.run` for `read_checks`: answers each `gh api` read by its
    path — the GraphQL overall-state read by `graphql` — with `(returncode, stdout,
    stderr)`, and fails a read it has no answer for. Every call is recorded."""

    def __init__(
        self,
        *,
        runs: list[dict] = (),
        statuses: list[dict] | None = None,
        check_runs: list[dict] | None = None,
        refused: tuple[str, ...] = (),
        rollup: tuple[int, str, str] | None = None,
        jobs: list[dict] | None = None,
    ):
        self.answers = {_RUNS_PATH: (0, json.dumps({"workflow_runs": list(runs)}), "")}
        # Run 1's jobs: by default one job that ran a step, as every run did before sys-v3.
        jobs = [_job("failure", steps=1)] if jobs is None else jobs
        self.answers[_JOBS_PATH] = (0, json.dumps({"total_count": len(jobs), "jobs": jobs}), "")
        if statuses is not None:
            body = {"state": "pending", "statuses": statuses, "total_count": len(statuses)}
            self.answers[_STATUS_PATH] = (0, json.dumps(body), "")
        if check_runs is not None:
            body = {"total_count": len(check_runs), "check_runs": check_runs}
            self.answers[_CHECK_RUNS_PATH] = (0, json.dumps(body), "")
        for path in refused:
            self.answers[path] = _REFUSED
        if rollup is not None:
            self.answers["graphql"] = rollup
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd=None, capture_output=True, text=True, check=False):
        self.calls.append(list(args))
        if args[1] != "api" or args[2] not in self.answers:
            raise AssertionError(f"no fake answer for {args}")
        returncode, stdout, stderr = self.answers[args[2]]
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def test_a_pr_whose_only_check_is_a_failing_commit_status_is_reported_as_failed(monkeypatch):
    """sys-s90's test. A project whose CI posts commit statuses, and no GitHub Actions
    run: the commit's one check is a failing status. The base branch read only the
    Actions runs and saw no check at all, so the sweep said `none` and the follow-up
    handed nothing back. The token cannot read check runs, as this machine's
    fine-grained token cannot; GitHub's overall state is FAILURE, which the failing
    status already explains, so no check is added for what the token cannot read."""
    fake = _FakeGhApi(
        statuses=[_status("ci/circleci: test", "failure")],
        refused=(_CHECK_RUNS_PATH,),
        rollup=_rollup("FAILURE"),
    )
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert sweep.runs_state(checks) == "fail"
    assert [c.state for c in checks] == ["fail"]
    assert checks == [
        github.Check(
            name="ci/circleci: test",
            status="completed",
            conclusion="failure",
            url="https://ci.example/1",
            source="status",
        )
    ]


@pytest.mark.parametrize(
    ("state", "expected"),
    [("pending", "pending"), ("success", "pass"), ("failure", "fail"), ("error", "fail")],
)
def test_each_commit_status_state_reads_as_a_check_state(monkeypatch, state, expected):
    fake = _FakeGhApi(statuses=[_status("ci/jenkins", state, target_url=None)], check_runs=[])
    monkeypatch.setattr(github.subprocess, "run", fake)

    (check,) = github.read_checks(_REPO, head_sha=_SHA)

    assert (check.name, check.state, check.source) == ("ci/jenkins", expected, "status")
    assert check.url == _PAGE  # a status with no link points at the commit's page
    assert not any(call[2] == "graphql" for call in fake.calls)


def test_check_runs_of_another_ci_are_read_and_github_actions_own_are_left_to_its_runs(
    monkeypatch,
):
    """A token that can read check runs (a classic token with `repo`) gets every check run:
    another CI's appear by name, and GitHub Actions' own, one per job, are left out since
    the workflow runs already report them. Nothing was refused, so the overall state is
    never read."""
    fake = _FakeGhApi(
        runs=[_run("CI", "success")],
        statuses=[],
        check_runs=[
            _check_run("build (ubuntu)", "success", "github-actions"),
            _check_run("Buildkite: test", "failure", "buildkite"),
            _check_run("Codecov", "neutral", "codecov"),
        ],
    )
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert [(c.name, c.source, c.state) for c in checks] == [
        ("CI", "actions", "pass"),
        ("Buildkite: test", "check run", "fail"),
        ("Codecov", "check run", "pass"),
    ]
    assert [call[2] for call in fake.calls] == [_RUNS_PATH, _STATUS_PATH, _CHECK_RUNS_PATH]


_FAILED = "no check this token can read failed, so one of the check runs it cannot read failed"
_GOING = (
    "no check this token can read failed or is still going, so one of the check runs it "
    "cannot read is still going"
)
_PASSED = "this token can read none of them, so the check runs it cannot read passed"


@pytest.mark.parametrize(
    ("runs", "rollup", "state", "says"),
    [
        ([_run("CI", "success")], "FAILURE", "fail", _FAILED),
        ([_run("CI", None)], "ERROR", "fail", _FAILED),
        ([_run("CI", "success")], "PENDING", "pending", _GOING),
        ([], "SUCCESS", "pass", _PASSED),
    ],
)
def test_a_check_the_token_cannot_read_reads_as_github_overall_state_and_says_so(
    monkeypatch, runs, rollup, state, says
):
    """Another CI's check run failed, still runs or passed; the token cannot read check
    runs. GitHub's overall state is worse than every check read — a check read passed or
    still runs, or none was read — so one more check carries it and says plainly what the
    token cannot read and why."""
    fake = _FakeGhApi(runs=runs, statuses=[], refused=(_CHECK_RUNS_PATH,), rollup=_rollup(rollup))
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert [c.source for c in checks] == ["actions"] * len(runs) + ["overall"]
    unread = checks[-1]
    assert unread.name == "check runs this token cannot read"
    assert unread.url == _PAGE
    assert unread.note.startswith(f"GitHub's overall state of this commit's checks is {rollup}")
    assert says in unread.note
    assert "GitHub gives a fine-grained token no permission to read check runs" in unread.note
    assert unread.state == state
    assert sweep.runs_state(checks) == state
    graphql = fake.calls[-1]
    assert graphql[:3] == ["gh", "api", "graphql"]
    assert f"sha={_SHA}" in graphql and "o=o" in graphql and "r=r" in graphql


@pytest.mark.parametrize(
    ("runs", "rollup"),
    [
        ([], None),  # the commit has no check at all
        ([_run("CI", "success")], "SUCCESS"),
        ([_run("CI", "failure")], "FAILURE"),  # the failed Actions run explains it
        ([_run("CI", None)], "PENDING"),  # the running Actions run explains it
    ],
)
def test_an_overall_state_the_checks_read_explain_adds_no_check(monkeypatch, runs, rollup):
    fake = _FakeGhApi(
        runs=runs, statuses=[], refused=(_CHECK_RUNS_PATH,), rollup=_rollup(rollup)
    )
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert [c.source for c in checks] == ["actions"] * len(runs)


@pytest.mark.parametrize(
    ("steps", "state", "unstarted"), [(0, "none", ["failure"]), (1, "fail", [])]
)
def test_a_failed_run_whose_jobs_ran_no_step_is_not_run_and_one_with_steps_fails(
    monkeypatch, steps, state, unstarted
):
    """sys-v3's test. GitHub Actions gives this private repository no runner: every job of
    every run fails in about two seconds with no step. The commit's one run completed with
    conclusion failure; the token cannot read check runs, and GitHub's overall state is
    FAILURE, from that run's one job. A run whose job ran no step never started: it is
    `none`, its note says so, and the overall state adds no failed check, because
    without that job no check is left. A run whose job ran a step still fails. The base
    branch read both as `fail`."""
    fake = _FakeGhApi(
        runs=[_run("CI", "failure")],
        statuses=[],
        refused=(_CHECK_RUNS_PATH,),
        rollup=_rollup("FAILURE", FAILURE=1),
        jobs=[_job("failure", steps=steps)],
    )
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert [(c.name, c.source, c.state) for c in checks] == [("CI", "actions", state)]
    assert checks[0].unstarted_jobs == unstarted
    assert sweep.runs_state(checks) == state
    assert ("GitHub never started the CI run" in checks[0].note) == (steps == 0)
    assert [call[2] for call in fake.calls[:2]] == [_RUNS_PATH, _JOBS_PATH]


def test_a_run_that_ran_steps_still_fails_beside_one_github_never_started(monkeypatch):
    """Two workflows on one commit: CI's run never started, Lint's ran a step and failed.
    The commit's checks fail, from Lint alone."""
    lint = {**_run("Lint", "failure"), "id": 2}
    fake = _FakeGhApi(
        runs=[_run("CI", "failure"), lint],
        statuses=[],
        check_runs=[],
        jobs=[_job("failure", steps=0)],
    )
    lint_jobs = json.dumps({"total_count": 1, "jobs": [_job("failure", steps=3)]})
    fake.answers[f"repos/{_REPO}/actions/runs/2/jobs?per_page=100"] = (0, lint_jobs, "")
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert [(c.name, c.state) for c in checks] == [("CI", "none"), ("Lint", "fail")]
    assert sweep.runs_state(checks) == "fail"


def test_a_failed_run_with_no_job_at_all_still_fails(monkeypatch):
    """A run GitHub fails before it makes any job, such as one whose workflow file it
    cannot read, is a real failure for the PR to fix, never a run GitHub never started."""
    fake = _FakeGhApi(runs=[_run("CI", "failure")], statuses=[], check_runs=[], jobs=[])
    monkeypatch.setattr(github.subprocess, "run", fake)

    (check,) = github.read_checks(_REPO, head_sha=_SHA)

    assert (check.state, check.unstarted_jobs, check.note) == ("fail", [], "")


def test_a_passing_run_reads_no_jobs(monkeypatch):
    fake = _FakeGhApi(runs=[_run("CI", "success")], statuses=[], check_runs=[])
    monkeypatch.setattr(github.subprocess, "run", fake)

    github.read_checks(_REPO, head_sha=_SHA)

    assert _JOBS_PATH not in [call[2] for call in fake.calls]


@pytest.mark.parametrize(
    ("counts", "state"),
    [
        ({"FAILURE": 2}, "fail"),  # another CI's check run failed too
        ({"FAILURE": 1, "TIMED_OUT": 1}, "fail"),
        ({"FAILURE": 1, "IN_PROGRESS": 1}, "pending"),  # another CI's still runs
        ({"FAILURE": 1, "SUCCESS": 1}, "pass"),  # another CI's passed
    ],
)
def test_the_overall_state_without_the_jobs_github_never_started_decides_the_unread_check(
    monkeypatch, counts, state
):
    """The commit's one Actions run never started, and the token cannot read check runs.
    GitHub's overall state is FAILURE from that run's job alone or from another CI's check
    run too; GitHub's counts of the check runs by state, less that job, tell which: the
    check that stands for the check runs the token cannot read carries what is left, and
    says the state came without the jobs GitHub never started."""
    fake = _FakeGhApi(
        runs=[_run("CI", "failure")],
        statuses=[],
        refused=(_CHECK_RUNS_PATH,),
        rollup=_rollup("FAILURE", **counts),
        jobs=[_job("failure", steps=0)],
    )
    monkeypatch.setattr(github.subprocess, "run", fake)

    checks = github.read_checks(_REPO, head_sha=_SHA)

    assert [(c.source, c.state) for c in checks] == [("actions", "none"), ("overall", state)]
    assert checks[-1].note.startswith(
        f"GitHub's overall state of this commit's checks is FAILURE ({state} without the jobs "
        "of the GitHub Actions runs it never started), and "
    )
    assert sweep.runs_state(checks) == state


def test_refused_statuses_and_check_runs_are_both_named(monkeypatch):
    fake = _FakeGhApi(refused=(_STATUS_PATH, _CHECK_RUNS_PATH), rollup=_rollup("FAILURE"))
    monkeypatch.setattr(github.subprocess, "run", fake)

    (unread,) = github.read_checks(_REPO, head_sha=_SHA)

    assert unread.name == "commit statuses and check runs this token cannot read"
    assert "the token lacks the Commit statuses read permission" in unread.note
    assert unread.state == "fail"


@pytest.mark.parametrize(
    "stderr",
    [
        "gh: Server Error (HTTP 502)",
        "gh: You have exceeded a secondary rate limit. (HTTP 403)",
    ],
)
def test_a_read_that_fails_other_than_by_refusal_raises(monkeypatch, stderr):
    """A server error, or a 403 that is a rate limit rather than a refusal of the token,
    fails the read loudly instead of reading as checks the token cannot read."""
    fake = _FakeGhApi(check_runs=[])
    fake.answers[_STATUS_PATH] = (1, "", stderr)
    monkeypatch.setattr(github.subprocess, "run", fake)

    with pytest.raises(github.GitHubError) as raised:
        github.read_checks(_REPO, head_sha=_SHA)
    assert stderr in str(raised.value)


# ------------------------------------------------------------ read_unresolved_review --


def _graphql_body() -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": True,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "me"},
                                                "body": "resolved thread comment",
                                                "path": "a.py",
                                                "line": 1,
                                            }
                                        ]
                                    },
                                },
                                {
                                    "isResolved": False,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "reviewer"},
                                                "body": "still needs work",
                                                "path": "b.py",
                                                "line": 12,
                                            },
                                            {
                                                "author": {"login": "me"},
                                                "body": "my own reply in the open thread",
                                                "path": "b.py",
                                                "line": 12,
                                            },
                                        ]
                                    },
                                },
                            ]
                        },
                        "reviews": {
                            "nodes": [
                                {"state": "CHANGES_REQUESTED", "body": "fix this", "author": {"login": "reviewer"}},
                                {"state": "APPROVED", "body": "lgtm", "author": {"login": "other"}},
                            ]
                        },
                        "comments": {
                            "nodes": [
                                {"author": {"login": "me"}, "body": "my own conversation comment"},
                                {"author": {"login": "reviewer"}, "body": "a conversation comment"},
                            ]
                        },
                    }
                }
            }
        }
    )


def test_read_unresolved_review_unfiltered(monkeypatch):
    fake = _FakeGh([(0, _graphql_body(), "")])
    monkeypatch.setattr(github.subprocess, "run", fake)

    review = github.read_unresolved_review("riodeuno/autonomouse", pr_number=42)

    assert [c.body for c in review.thread_comments] == [
        "still needs work",
        "my own reply in the open thread",
    ]
    assert [r.body for r in review.change_requests] == ["fix this"]
    assert [c.body for c in review.comments] == [
        "my own conversation comment",
        "a conversation comment",
    ]
    assert fake.calls[0].args[:3] == ["gh", "api", "graphql"]
    assert "-F" in fake.calls[0].args and "o=riodeuno" in fake.calls[0].args


def test_read_unresolved_review_excludes_login(monkeypatch):
    monkeypatch.setattr(github.subprocess, "run", _FakeGh([(0, _graphql_body(), "")]))

    review = github.read_unresolved_review(
        "riodeuno/autonomouse", pr_number=42, exclude_login="me"
    )

    assert [c.body for c in review.thread_comments] == ["still needs work"]
    assert [r.body for r in review.change_requests] == ["fix this"]
    assert [c.body for c in review.comments] == ["a conversation comment"]
    assert not review.is_empty()


def test_read_unresolved_review_empty_when_nothing_left(monkeypatch):
    body = json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": []},
                        "reviews": {"nodes": []},
                        "comments": {"nodes": []},
                    }
                }
            }
        }
    )
    monkeypatch.setattr(github.subprocess, "run", _FakeGh([(0, body, "")]))

    review = github.read_unresolved_review("riodeuno/autonomouse", pr_number=1)
    assert review.is_empty()


# ------------------------------------------------------------ `github comments` CLI --
# sys-s26: the command the conflict check reads (docs/orchestrator/conflict-check.md) prints
# each unanswered comment's link and lists only what the sweep counts: the PR's commits come
# from `read_pr`, and a reply naming one of them answers a comment.

PR_URL = "https://github.com/riodeuno/autonomouse/pull/42"
FIX_SHA = "abc1234" + "0" * 33


def _pr_view_body() -> str:
    return json.dumps(
        {
            "number": 42,
            "url": PR_URL,
            "state": "OPEN",
            "headRefOid": FIX_SHA,
            "baseRefName": "main",
            "commits": [{"oid": "f00d" + "0" * 36}, {"oid": FIX_SHA}],
        }
    )


def _node(login: str, body: str, at: str, url: str, **extra) -> dict:
    return {"author": {"login": login}, "body": body, "createdAt": at, "url": url, **extra}


def _comments_body(threads: list[list[dict]], reviews: list[dict], comments: list[dict]) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {"isResolved": False, "comments": {"nodes": nodes}}
                                for nodes in threads
                            ]
                        },
                        "reviews": {"nodes": reviews},
                        "comments": {"nodes": comments},
                    }
                }
            }
        }
    )


def _review_node(body: str, at: str, url: str) -> dict:
    return {
        "state": "CHANGES_REQUESTED",
        "body": body,
        "author": {"login": "reviewer"},
        "submittedAt": at,
        "url": url,
    }


def _one_of_each() -> str:
    """One unanswered thread comment, change request and conversation comment."""
    return _comments_body(
        threads=[
            [
                _node(
                    "reviewer", "still needs\nwork", "2026-09-26T01:00:00Z",
                    f"{PR_URL}#discussion_r1", path="b.py", line=12,
                )
            ]
        ],
        reviews=[_review_node("fix this", "2026-09-26T01:01:00Z", f"{PR_URL}#pullrequestreview-2")],
        comments=[
            _node("reviewer", "a conversation comment", "2026-09-26T01:02:00Z", f"{PR_URL}#issuecomment-3")
        ],
    )


def _comments_cli(monkeypatch, capsys, fake: _FakeGh, *extra: str) -> str:
    monkeypatch.setattr(github.subprocess, "run", fake)
    monkeypatch.setattr(
        "sys.argv",
        ["autonomouse", "github", "comments", "--repo", "riodeuno/autonomouse", "--pr", "42", *extra],
    )
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (None, 0), capsys.readouterr().err
    return capsys.readouterr().out


def test_cli_comments_prints_each_comments_url(monkeypatch, capsys):
    fake = _FakeGh([(0, _pr_view_body(), ""), (0, _one_of_each(), "")])

    out = _comments_cli(monkeypatch, capsys, fake).splitlines()

    assert out == [
        "1 thread comment(s), 1 change request(s), 1 comment(s)",
        f"{PR_URL}#discussion_r1 thread comment by reviewer on b.py:12: still needs work",
        f"{PR_URL}#pullrequestreview-2 change request by reviewer: fix this",
        f"{PR_URL}#issuecomment-3 comment by reviewer: a conversation comment",
    ]
    assert fake.calls[0].args[:4] == ["gh", "pr", "view", "42"]
    assert fake.calls[1].args[:3] == ["gh", "api", "graphql"]
    query = next(a for a in fake.calls[1].args if a.startswith("query="))
    assert query.count(" url}") == 3


def test_cli_comments_json_holds_each_url(monkeypatch, capsys):
    fake = _FakeGh([(0, _pr_view_body(), ""), (0, _one_of_each(), "")])

    data = json.loads(_comments_cli(monkeypatch, capsys, fake, "--json"))

    assert [c["url"] for c in data["thread_comments"]] == [f"{PR_URL}#discussion_r1"]
    assert [r["url"] for r in data["change_requests"]] == [f"{PR_URL}#pullrequestreview-2"]
    assert [c["url"] for c in data["comments"]] == [f"{PR_URL}#issuecomment-3"]


def test_cli_comments_leaves_out_what_a_reply_naming_a_commit_answers(monkeypatch, capsys):
    """The sweep's rule: in a thread, a reply naming one of the PR's commits answers every
    comment before it; in the conversation, it answers every comment and change request
    posted before it. A reply naming a commit not on the PR answers nothing."""
    body = _comments_body(
        threads=[
            [
                _node("reviewer", "rename this", "2026-09-26T01:00:00Z", f"{PR_URL}#discussion_r1",
                      path="a.py", line=3),
                _node("reviewer", "done in abc1234", "2026-09-26T01:05:00Z",
                      f"{PR_URL}#discussion_r2", path="a.py", line=3),
            ],
            [
                _node("reviewer", "and this", "2026-09-26T01:00:00Z", f"{PR_URL}#discussion_r3",
                      path="b.py", line=9),
                _node("reviewer", "done in 9999999", "2026-09-26T01:05:00Z",
                      f"{PR_URL}#discussion_r4", path="b.py", line=9),
            ],
        ],
        reviews=[
            _review_node("answered change request", "2026-09-26T01:01:00Z", f"{PR_URL}#pullrequestreview-5"),
            _review_node("new change request", "2026-09-26T01:20:00Z", f"{PR_URL}#pullrequestreview-6"),
        ],
        comments=[
            _node("reviewer", "answered comment", "2026-09-26T01:02:00Z", f"{PR_URL}#issuecomment-7"),
            _node("reviewer", "addressed in abc1234", "2026-09-26T01:10:00Z", f"{PR_URL}#issuecomment-8"),
            _node("reviewer", "a new comment", "2026-09-26T01:30:00Z", f"{PR_URL}#issuecomment-9"),
        ],
    )
    fake = _FakeGh([(0, _pr_view_body(), ""), (0, body, "")])

    out = _comments_cli(monkeypatch, capsys, fake)

    listed = [line.split()[0] for line in out.splitlines()[1:]]
    assert listed == [
        f"{PR_URL}#discussion_r3",
        f"{PR_URL}#discussion_r4",
        f"{PR_URL}#pullrequestreview-6",
        f"{PR_URL}#issuecomment-9",
    ]
    assert out.splitlines()[0] == "2 thread comment(s), 1 change request(s), 1 comment(s)"


# --------------------------------------------------------- pr_state / retarget_to_main --


def test_retarget_to_main_when_base_merged(monkeypatch):
    fake = _FakeGh(
        [
            (0, json.dumps({"state": "MERGED"}), ""),
            (0, "", ""),
        ]
    )
    monkeypatch.setattr(github.subprocess, "run", fake)

    retargeted = github.retarget_to_main(
        "riodeuno/autonomouse", pr_number=7, base_branch="card/int-g1", main="main"
    )

    assert retargeted is True
    assert len(fake.calls) == 2
    assert fake.calls[0].args == [
        "gh", "pr", "view", "card/int-g1", "--repo", "riodeuno/autonomouse", "--json", "state",
    ]
    assert fake.calls[1].args == [
        "gh", "pr", "edit", "7", "--repo", "riodeuno/autonomouse", "--base", "main",
    ]


def test_retarget_to_main_when_base_not_merged(monkeypatch):
    fake = _FakeGh([(0, json.dumps({"state": "OPEN"}), "")])
    monkeypatch.setattr(github.subprocess, "run", fake)

    retargeted = github.retarget_to_main(
        "riodeuno/autonomouse", pr_number=7, base_branch="card/int-g1", main="main"
    )

    assert retargeted is False
    assert len(fake.calls) == 1  # no `pr edit` call


def test_retarget_to_main_is_a_noop_when_base_already_main(monkeypatch):
    fake = _FakeGh([])
    monkeypatch.setattr(github.subprocess, "run", fake)

    retargeted = github.retarget_to_main(
        "riodeuno/autonomouse", pr_number=7, base_branch="main", main="main"
    )

    assert retargeted is False
    assert fake.calls == []


def test_retarget_to_main_names_no_branch_of_its_own():
    """The branch a PR goes back to is the project's default branch, which every caller
    passes (`followup.follow_up` passes `project.default_branch`); `main` is not assumed."""
    main = inspect.signature(github.retarget_to_main).parameters["main"]
    assert main.default is inspect.Parameter.empty


# ------------------------------------------------------------------------ repo_slug --


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/riodeuno/autonomouse", "riodeuno/autonomouse"),
        ("https://github.com/riodeuno/autonomouse.git", "riodeuno/autonomouse"),
        ("https://github.com/riodeuno/autonomouse/", "riodeuno/autonomouse"),
    ],
)
def test_repo_slug(url, expected):
    assert github.repo_slug(url) == expected


def test_repo_slug_rejects_non_github_url():
    with pytest.raises(github.GitHubError, match="not a github.com repository URL"):
        github.repo_slug("https://gitlab.com/riodeuno/autonomouse")


# ------------------------------------------------------------------ compute_base_branch --


def _assigned() -> Assigned:
    return Assigned(model=None, effort=None, agent_type=None, session_id=None, pid=None, picked_up_at=None)


def _handoff() -> Handoff:
    return Handoff(last_step=None, next_step=None, updated_at=None, run_id=None)


def _card(
    card_id: str,
    *,
    stack: Stack = Stack.DONE,
    pr: str | None = "https://github.com/riodeuno/autonomouse/pull/1",
    branch: str | None = "card/x",
    done_at: str | None = "2026-09-22T10:00:00Z",
    history: list[HistoryEntry] | None = None,
) -> Card:
    if history is None:
        history = (
            [HistoryEntry(at=done_at, run_id="r", from_="in_progress", to="done", note=None)]
            if done_at
            else []
        )
    return Card(
        id=card_id,
        project="integrations",
        title=card_id,
        stack=stack,
        position=0,
        size=Size.S,
        goal="g",
        limits=[],
        test="t",
        depends_on=[],
        assigned=_assigned(),
        worktree=None,
        branch=branch,
        base_branch=None,
        pr=pr,
        phase=None,
        iteration=0,
        handoff=_handoff(),
        decisions=[],
        history=history,
        created_at="2026-09-22T06:00:00Z",
        updated_at="2026-09-22T10:00:00Z",
    )


def _unmerged(card: Card) -> str | None:
    """A `merged_into` read for a dependency whose PR has not merged."""
    return None


def _no_branch_read(card: Card, branch: str) -> bool:
    """A `branch_merged` read the rule must not need."""
    raise AssertionError(f"asked whether {branch}'s own PR merged, for {card.id}")


def _plan(
    dependencies: list[Card],
    *,
    merged_into=_unmerged,
    branch_merged=_no_branch_read,
) -> github.BaseBranchPlan:
    return github.compute_base_branch(
        dependencies,
        default_branch="main",
        merged_into=merged_into,
        branch_merged=branch_merged,
    )


def test_no_dependencies_uses_default_branch():
    assert _plan([]) == github.BaseBranchPlan(base_branch="main", also_merge=[])


def test_one_done_dependency_with_unmerged_pr_sets_the_base():
    dep = _card("int-g1", branch="card/int-g1")
    assert _plan([dep]) == github.BaseBranchPlan(base_branch="card/int-g1", also_merge=[])


def test_a_dependency_merged_into_the_default_branch_gives_the_default_branch():
    """As before sys-o4, and with no read of any branch's own PR."""
    dep = _card("int-g1", branch="card/int-g1")
    plan = _plan([dep], merged_into=lambda c: "main")
    assert plan == github.BaseBranchPlan(base_branch="main", also_merge=[])


def test_no_code_dependency_never_sets_the_base():
    dep = _card("int-x1", pr=None, branch=None)
    assert _plan([dep]) == github.BaseBranchPlan(base_branch="main", also_merge=[])


def test_dependency_not_done_never_sets_the_base():
    dep = _card("int-g1", stack=Stack.IN_PROGRESS)
    assert _plan([dep]) == github.BaseBranchPlan(base_branch="main", also_merge=[])


def test_several_pending_dependencies_pick_the_most_recently_done():
    older = _card("int-a1", branch="card/int-a1", done_at="2026-09-20T10:00:00Z")
    newer = _card("int-a2", branch="card/int-a2", done_at="2026-09-21T10:00:00Z")
    plan = _plan([older, newer])
    assert plan.base_branch == "card/int-a2"
    assert plan.also_merge == ["card/int-a1"]


def test_also_merge_is_ordered_oldest_done_first():
    a = _card("int-a1", branch="card/int-a1", done_at="2026-09-20T10:00:00Z")
    b = _card("int-a2", branch="card/int-a2", done_at="2026-09-22T10:00:00Z")
    c = _card("int-a3", branch="card/int-a3", done_at="2026-09-21T10:00:00Z")
    plan = _plan([a, b, c])
    assert plan.base_branch == "card/int-a2"
    assert plan.also_merge == ["card/int-a1", "card/int-a3"]


def test_pending_dependency_with_no_branch_raises():
    dep = _card("int-g1", branch=None)
    with pytest.raises(github.GitHubError, match="has no branch"):
        _plan([dep])


def test_pending_dependency_with_no_done_history_row_raises():
    dep = _card("int-g1", branch="card/int-g1", done_at=None, history=[])
    with pytest.raises(github.GitHubError, match="no history row moving it to done"):
        _plan([dep])


# A dependency's PR squash-merged into a wake's shared branch `wake/<run id>`
# (docs/orchestrator/conflict-check.md) reads MERGED while the default branch still lacks
# its work, until the shared branch's own PR merges (sys-o4).


def test_a_dependency_merged_into_a_branch_whose_pr_is_open_puts_the_card_on_that_branch():
    dep = _card("int-g1", branch="card/int-g1")
    asked: list[tuple[str, str]] = []

    def branch_merged(card: Card, branch: str) -> bool:
        asked.append((card.id, branch))
        return False  # wake/x's own PR is open

    plan = _plan([dep], merged_into=lambda c: "wake/x", branch_merged=branch_merged)

    assert plan == github.BaseBranchPlan(base_branch="wake/x", also_merge=[])
    assert asked == [("int-g1", "wake/x")]


def test_a_dependency_merged_into_a_branch_whose_pr_merged_gives_the_default_branch():
    dep = _card("int-g1", branch="card/int-g1")
    plan = _plan([dep], merged_into=lambda c: "wake/x", branch_merged=lambda c, b: True)
    assert plan == github.BaseBranchPlan(base_branch="main", also_merge=[])


def test_two_dependencies_merged_into_the_same_open_branch_name_it_once():
    a = _card("int-a1", branch="card/int-a1", done_at="2026-09-20T10:00:00Z")
    b = _card("int-a2", branch="card/int-a2", done_at="2026-09-21T10:00:00Z")
    plan = _plan([a, b], merged_into=lambda c: "wake/x", branch_merged=lambda c, br: False)
    assert plan == github.BaseBranchPlan(base_branch="wake/x", also_merge=[])


@pytest.mark.parametrize(
    ("on_wake_done_at", "on_own_done_at", "base_branch", "also_merge"),
    [
        ("2026-09-21T10:00:00Z", "2026-09-20T10:00:00Z", "wake/x", ["card/b"]),
        ("2026-09-20T10:00:00Z", "2026-09-21T10:00:00Z", "card/b", ["wake/x"]),
    ],
    ids=["shared-branch-done-last", "own-branch-done-last"],
)
def test_a_dependency_on_an_open_shared_branch_and_one_unmerged_on_its_own_branch(
    on_wake_done_at, on_own_done_at, base_branch, also_merge
):
    on_wake = _card("int-a1", branch="card/a", done_at=on_wake_done_at)
    on_own = _card("int-b1", branch="card/b", done_at=on_own_done_at)
    plan = _plan(
        [on_wake, on_own],
        merged_into=lambda c: "wake/x" if c.id == "int-a1" else None,
        branch_merged=lambda c, br: False,
    )
    assert plan == github.BaseBranchPlan(base_branch=base_branch, also_merge=also_merge)


# ------------------------------------------------------------ github base-branch --

BOARD_REPO = "riodeuno/widgets"
DEP_PR = f"https://github.com/{BOARD_REPO}/pull/1"


class _FakeGhByArgs:
    """`subprocess.run` for `gh`: `answers` maps a tuple of `gh` arguments to stdout, or
    to a failed call's `(returncode, stderr)`; a missing entry fails the test."""

    def __init__(self, answers: dict[tuple[str, ...], str | tuple[int, str]]):
        self.answers = answers

    def __call__(self, args, *, cwd=None, capture_output=True, text=True, check=False):
        key = tuple(args[1:])
        if key not in self.answers:
            raise AssertionError(f"no fake for gh {key}")
        answer = self.answers[key]
        if isinstance(answer, tuple):
            return subprocess.CompletedProcess(args, answer[0], stdout="", stderr=answer[1])
        return subprocess.CompletedProcess(args, 0, stdout=answer, stderr="")


@pytest.fixture
def dependency_board(tmp_path, monkeypatch):
    """A board whose card wid-a2 depends on wid-a1, done on branch `card/wid-a1` with PR
    #1; `AUTONOMOUSE_HOME` points at it for the CLI."""
    root = tmp_path / "board"
    root.mkdir()
    project_new(
        root, slug="widgets", name="Widgets", goal="g", repo=f"https://github.com/{BOARD_REPO}",
        repo_path="/nowhere", default_branch="main", priority=1,
    )
    card_new(root, card_id="wid-a1", project="widgets", title="t", goal="g", test="t")
    path = root / "projects" / "widgets" / "cards" / "wid-a1.json"
    card = load_card(path)
    card.stack = Stack.DONE
    card.pr = DEP_PR
    card.branch = "card/wid-a1"
    card.history.append(
        HistoryEntry(at="2026-09-27T08:00:00Z", run_id="r", from_="in_progress", to="done", note=None)
    )
    save_card(path, card)
    card_new(
        root, card_id="wid-a2", project="widgets", title="t", goal="g", test="t",
        depends_on=["wid-a1"],
    )
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    return root


def _dependency_merged_into(branch: str) -> dict[tuple[str, ...], str]:
    """gh's answers in a repository where wid-a1's PR #1 has merged into `branch`: to a
    full read of the PR (`read_pr`) and to a read of its state alone (`pr_state`)."""
    view = {
        "number": 1, "url": DEP_PR, "state": "MERGED", "headRefOid": "a" * 40,
        "baseRefName": branch, "commits": [{"oid": "a" * 40}],
    }
    return {
        ("pr", "view", DEP_PR, "--repo", BOARD_REPO, "--json",
         "number,url,state,headRefOid,baseRefName,commits"): json.dumps(view),
        ("pr", "view", DEP_PR, "--repo", BOARD_REPO, "--json", "state"): json.dumps(
            {"state": "MERGED"}
        ),
    }


def _branch_pr_state_args(branch: str) -> tuple[str, ...]:
    return ("pr", "view", branch, "--repo", BOARD_REPO, "--json", "state")


def _base_branch_cli(monkeypatch, capsys, fake: _FakeGhByArgs) -> tuple[int, str, str]:
    monkeypatch.setattr(github.subprocess, "run", fake)
    monkeypatch.setattr("sys.argv", ["autonomouse", "github", "base-branch", "wid-a2", "--json"])
    try:
        cli.main()
        code = 0
    except SystemExit as e:
        code = e.code or 0
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_cli_base_branch_names_the_shared_branch_while_it_has_no_pr_yet(
    dependency_board, monkeypatch, capsys
):
    """During a wake the shared branch has no PR until the wake ends; gh then answers
    `no pull requests found`, and main still lacks the dependency's work."""
    answers = _dependency_merged_into("wake/x")
    answers[_branch_pr_state_args("wake/x")] = (1, 'no pull requests found for branch "wake/x"')

    code, out, err = _base_branch_cli(monkeypatch, capsys, _FakeGhByArgs(answers))

    assert code == 0, err
    assert json.loads(out) == {"base_branch": "wake/x", "also_merge": []}


def test_cli_base_branch_fails_when_reading_the_shared_branchs_pr_fails(
    dependency_board, monkeypatch, capsys
):
    """Any other failed read stops the command; it never passes for a branch with no PR."""
    answers = _dependency_merged_into("wake/x")
    answers[_branch_pr_state_args("wake/x")] = (1, "HTTP 401: Bad credentials")

    code, out, err = _base_branch_cli(monkeypatch, capsys, _FakeGhByArgs(answers))

    assert code != 0
    assert out == ""
    assert "Bad credentials" in err
