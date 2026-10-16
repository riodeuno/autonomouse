"""`autonomouse github sweep <slug>` (`autonomouse.sweep`, sys-s8): one line per open pull
request autonomouse opened, findings marked; and `pick` treating a project with a finding
and no ready card as having work.

`subprocess.run` is faked the way `_FakeGh` in tests/test_github.py fakes it — a callable
returning `CompletedProcess` — but keyed on the `gh` arguments instead of a queue, so each
test reads as the repository it describes. `git` calls run for real, on a fixture
repository: a bare `origin` and the project's checkout cloned from it (`_fixture_repo`)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autonomouse import cli, controller, github, sweep
from autonomouse.board.commands import card_move, card_new, project_done, project_new
from autonomouse.board.runs import run_start, save_run
from autonomouse.board.schema import Stack
from autonomouse.board.store import commit_board, load_card, save_card
from test_run_controller import _args, _prompt, env  # noqa: F401 — the wake fixture

REPO = "riodeuno/widgets"
REPO_URL = f"https://github.com/{REPO}"
# The real `subprocess.run`, for `git`: the tests replace `subprocess.run` with a fake `gh`.
_REAL_RUN = subprocess.run


def _pr(number: int, branch: str, mergeable: str = "MERGEABLE", *, cross: bool = False) -> dict:
    return {
        "number": number,
        "url": f"{REPO_URL}/pull/{number}",
        "headRefName": branch,
        "headRefOid": f"sha{number:02d}" + "0" * 34,
        "mergeable": mergeable,
        "isCrossRepository": cross,
    }


def _runs(*conclusions: str | None) -> str:
    return json.dumps(
        {
            "workflow_runs": [
                {
                    "id": i,
                    "name": "CI",
                    "status": "completed" if c is not None else "in_progress",
                    "conclusion": c,
                    "html_url": f"{REPO_URL}/actions/runs/{i}",
                }
                for i, c in enumerate(conclusions, 1)
            ]
        }
    )


def _pr_view(number: int, commits: list[str]) -> str:
    return json.dumps(
        {
            "number": number,
            "url": f"{REPO_URL}/pull/{number}",
            "state": "OPEN",
            "headRefOid": commits[-1],
            "baseRefName": "main",
            "commits": [{"oid": c} for c in commits],
        }
    )


def _review(thread_bodies: list[str] = (), resolved: bool = False) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "isResolved": resolved,
                                    "comments": {
                                        "nodes": [
                                            {
                                                "author": {"login": "reviewer"},
                                                "body": body,
                                                "path": "src/x.py",
                                                "line": 3,
                                                "createdAt": "2026-09-26T07:00:00Z",
                                            }
                                            for body in thread_bodies
                                        ]
                                    },
                                }
                            ]
                            if thread_bodies
                            else []
                        },
                        "reviews": {"nodes": []},
                        "comments": {"nodes": []},
                    }
                }
            }
        }
    )


class _FakeGhByArgs:
    """`subprocess.run` for `gh`: `answers` maps a tuple of `gh` arguments to stdout, or
    to a failed call's `(returncode, stderr)`, and a missing entry fails the call the way
    `gh` does (returncode 1); every call is recorded in `.calls`."""

    def __init__(self, answers: dict[tuple[str, ...], str | tuple[int, str]]):
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd=None, capture_output=True, text=True, check=False):
        self.calls.append(list(args))
        if args[0] == "git":
            return _REAL_RUN(args, cwd=cwd, capture_output=capture_output, text=text, check=check)
        key = tuple(args[1:])
        if key not in self.answers:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr=f"no fake for {key}")
        answer = self.answers[key]
        if isinstance(answer, tuple):
            return subprocess.CompletedProcess(args, answer[0], stdout="", stderr=answer[1])
        return subprocess.CompletedProcess(args, 0, stdout=answer, stderr="")


def _list_args() -> tuple[str, ...]:
    return (
        "pr", "list", "--repo", REPO, "--state", "open", "--limit", "200",
        "--json", "number,url,headRefName,headRefOid,mergeable,isCrossRepository",
    )


def _checks_args(sha: str) -> tuple[str, ...]:
    return ("api", f"repos/{REPO}/actions/runs?head_sha={sha}")


def _checks(
    sha: str, runs: str, statuses: list[dict] = (), *, steps: int = 1
) -> dict[tuple[str, ...], str]:
    """The reads of a commit's checks (`github.read_checks`): its Actions runs `runs`, the
    jobs of each that failed, each job with `steps` steps (0: GitHub never started it), its
    commit statuses `statuses` and no check run."""
    answers = {
        _checks_args(sha): runs,
        ("api", f"repos/{REPO}/commits/{sha}/status?per_page=100"): json.dumps(
            {"state": "pending", "statuses": list(statuses), "total_count": len(statuses)}
        ),
        ("api", f"repos/{REPO}/commits/{sha}/check-runs?per_page=100"): json.dumps(
            {"total_count": 0, "check_runs": []}
        ),
    }
    for run in json.loads(runs)["workflow_runs"]:
        if run["conclusion"] not in (None, "success"):
            job = {"conclusion": run["conclusion"], "steps": [{"name": "s"}] * steps}
            answers[("api", f"repos/{REPO}/actions/runs/{run['id']}/jobs?per_page=100")] = (
                json.dumps({"total_count": 1, "jobs": [job]})
            )
    return answers


def _view_args(number: int) -> tuple[str, ...]:
    return (
        "pr", "view", str(number), "--repo", REPO,
        "--json", "number,url,state,headRefOid,baseRefName,commits",
    )


def _graphql_args(number: int) -> tuple[str, ...]:
    owner, name = REPO.split("/")
    return (
        "api", "graphql",
        "-F", f"o={owner}", "-F", f"r={name}", "-F", f"n={number}",
        "-f", f"query={github._COMMENTS_QUERY}",
    )


# Every head branch the tests' PRs name. The fixture repository has each at main's commit,
# so none conflicts with main.
_BRANCHES = (
    "card/wid-a1", "card/wid-a2", "card/wid-a3", "card/wid-a4", "card/gad-a1",
    "feat/widget-colors",
    "wake/20260926-000000-abcd", "wake/20260926-010203-a1b2", "wake/20260926-111111-beef",
)


def _git(cwd: Path, *args: str) -> str:
    return _REAL_RUN(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


def _commit(cwd: Path, name: str, text: str) -> None:
    (cwd / name).write_text(text)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", f"{name}: {text.strip()}")


def _fixture_repo(tmp_path: Path) -> Path:
    """A bare repository `origin` whose `main` has one commit and every branch in
    `_BRANCHES` at that commit, and the project's checkout `clone`, cloned from it before
    any test pushes more; returns `origin`."""
    origin, clone = tmp_path / "origin", tmp_path / "clone"
    _REAL_RUN(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _REAL_RUN(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)
    _git(clone, "checkout", "-q", "-b", "main")
    _commit(clone, "f.txt", "one\n")
    _git(clone, "push", "-q", "origin", "main", *(f"main:refs/heads/{b}" for b in _BRANCHES))
    return origin


@pytest.fixture
def board(tmp_path: Path, monkeypatch) -> Path:
    """A board with project `widgets` whose done card wid-a1 has PR #1; the rest of the
    board is empty. The project's checkout is `tmp_path/clone` (`_fixture_repo`).
    `AUTONOMOUSE_HOME` points at the board for the CLI."""
    _fixture_repo(tmp_path)
    root = tmp_path / "board"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    project_new(
        root, slug="widgets", name="Widgets", goal="g", repo=REPO_URL,
        repo_path=str(tmp_path / "clone"),
        default_branch="main", priority=1,
    )
    card_new(root, card_id="wid-a1", project="widgets", title="t", goal="g", test="t")
    path = root / "projects" / "widgets" / "cards" / "wid-a1.json"
    card = load_card(path)
    card.stack = Stack.DONE
    card.pr = f"{REPO_URL}/pull/1"
    save_card(path, card)
    commit_board(root, "seed")
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    return root


def _four_prs_and_a_feature() -> _FakeGhByArgs:
    """Four open PRs from `card/` branches — a clean one of done card wid-a1 (#1), a
    CONFLICTING one (#2), one whose run failed (#3), one with an unanswered review-thread
    comment (#4) — and one from `feature/x` (#5)."""
    prs = [
        _pr(1, "card/wid-a1"),
        _pr(2, "card/wid-a2", mergeable="CONFLICTING"),
        _pr(3, "card/wid-a3"),
        _pr(4, "card/wid-a4"),
        _pr(5, "feature/x", mergeable="CONFLICTING"),
    ]
    sha = {p["number"]: p["headRefOid"] for p in prs}
    answers = {_list_args(): json.dumps(prs)}
    for n in (1, 2, 3, 4):
        answers[_view_args(n)] = _pr_view(n, ["a" * 40, sha[n]])
    answers.update(_checks(sha[1], _runs("success")))
    answers.update(_checks(sha[2], _runs("success")))
    answers.update(_checks(sha[3], _runs("success", "failure")))
    answers.update(_checks(sha[4], _runs("success")))
    answers[_graphql_args(1)] = _review()
    answers[_graphql_args(2)] = _review()
    answers[_graphql_args(3)] = _review()
    # #4: two comments, the first answered by a reply naming a commit, the last not.
    answers[_graphql_args(4)] = _review(
        ["please rename", f"done in {'a' * 12}", "and this one?"]
    )
    return _FakeGhByArgs(answers)


# ------------------------------------------------------------------------- sweep --


def test_sweep_reports_every_own_open_pr_and_marks_the_findings(board, monkeypatch):
    fake = _four_prs_and_a_feature()
    monkeypatch.setattr(subprocess, "run", fake)

    lines = sweep.sweep(board, "widgets")

    assert [(line.number, line.card_id) for line in lines] == [
        (1, "wid-a1"), (2, "-"), (3, "-"), (4, "-")
    ]
    assert [line.mergeable for line in lines] == [
        "MERGEABLE", "CONFLICTING", "MERGEABLE", "MERGEABLE"
    ]
    assert [line.runs for line in lines] == ["pass", "pass", "fail", "pass"]
    assert [line.unanswered for line in lines] == [0, 0, 0, 1]
    assert [line.finding for line in lines] == [False, True, True, True]
    assert all(line.head_branch.startswith("card/") for line in lines)
    # The feature branch's PR is never read beyond the list.
    assert not any("5" in call for call in fake.calls[1:])
    assert fake.calls[0][1:] == list(_list_args())


def test_sweep_leaves_out_a_forks_pr_even_when_its_branch_is_named_like_a_cards(
    board, monkeypatch
):
    """A pull request opened from a fork is never one autonomouse opened, even when its
    branch happens to be named `card/sys-s1`: the sweep leaves it out entirely, the way it
    leaves out a `feature/x` branch, so it yields no finding for it."""
    own = _pr(1, "card/wid-a1")
    fork = _pr(9, "card/sys-s1", mergeable="CONFLICTING", cross=True)
    answers = {_list_args(): json.dumps([own, fork])}
    answers[_view_args(1)] = _pr_view(1, ["a" * 40, own["headRefOid"]])
    answers.update(_checks(own["headRefOid"], _runs("success")))
    answers[_graphql_args(1)] = _review()
    fake = _FakeGhByArgs(answers)
    monkeypatch.setattr(subprocess, "run", fake)

    lines = sweep.sweep(board, "widgets")

    assert [line.number for line in lines] == [1]
    assert not any(line.finding for line in lines)
    # The fork PR is never read beyond the list.
    assert not any("9" in call for call in fake.calls[1:])


def test_sweep_finds_a_cards_pr_by_the_branch_the_card_records_whatever_its_prefix(
    board, monkeypatch
):
    """A card's branch is named by its kind of change (sys-w18): card wid-a1 records
    `feat/widget-colors`, and its PR #1 comes from that branch. The sweep reports it as
    wid-a1's, a conflict with its base making it a finding, and leaves out #5, a human's
    `fix/typo` PR that no card records, the way it leaves out `feature/x`."""
    path = board / "projects" / "widgets" / "cards" / "wid-a1.json"
    card = load_card(path)
    card.branch = "feat/widget-colors"
    save_card(path, card)
    own = _pr(1, "feat/widget-colors", mergeable="CONFLICTING")
    human = _pr(5, "fix/typo", mergeable="CONFLICTING")
    answers = {_list_args(): json.dumps([own, human])}
    answers[_view_args(1)] = _pr_view(1, [own["headRefOid"]])
    answers.update(_checks(own["headRefOid"], _runs("success")))
    answers[_graphql_args(1)] = _review()
    fake = _FakeGhByArgs(answers)
    monkeypatch.setattr(subprocess, "run", fake)

    lines = sweep.sweep(board, "widgets")

    assert [(line.number, line.card_id, line.finding) for line in lines] == [
        (1, "wid-a1", True)
    ]
    # The human's PR is never read beyond the list.
    assert not any("5" in call for call in fake.calls[1:])


def test_a_pr_whose_only_check_is_a_failing_commit_status_is_a_finding(board, monkeypatch):
    """A project whose CI posts commit statuses and runs no GitHub Actions workflow: the
    PR's one check, a failing status, makes its runs `fail` and the PR a finding."""
    own = _pr(1, "card/wid-a1")
    status = {"context": "ci/jenkins", "state": "failure", "target_url": "https://ci/1"}
    answers = {_list_args(): json.dumps([own])}
    answers[_view_args(1)] = _pr_view(1, [own["headRefOid"]])
    answers.update(_checks(own["headRefOid"], _runs(), [status]))
    answers[_graphql_args(1)] = _review()
    monkeypatch.setattr(subprocess, "run", _FakeGhByArgs(answers))

    (line,) = sweep.sweep(board, "widgets")

    assert (line.runs, line.finding) == ("fail", True)


def test_a_failed_check_the_token_cannot_read_is_a_finding_whose_line_says_so(
    board, monkeypatch, capsys
):
    """The PR's Actions run passed, GitHub refuses the token the check runs (as it refuses
    this machine's fine-grained token), and GitHub's overall state is FAILURE: another
    CI's check run failed. The line is a finding, and it says plainly which checks the
    token cannot read, in the JSON and the plain line alike."""
    own = _pr(1, "card/wid-a1")
    sha = own["headRefOid"]
    answers = {_list_args(): json.dumps([own])}
    answers[_view_args(1)] = _pr_view(1, [sha])
    answers.update(_checks(sha, _runs("success")))
    answers[("api", f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")] = (
        1,
        "gh: Resource not accessible by personal access token (HTTP 403)",
    )
    owner, name = REPO.split("/")
    rollup = {"data": {"repository": {"object": {"statusCheckRollup": {"state": "FAILURE"}}}}}
    answers[
        (
            "api", "graphql", "-f", f"o={owner}", "-f", f"r={name}", "-f", f"sha={sha}",
            "-f", f"query={github._ROLLUP_QUERY}",
        )
    ] = json.dumps(rollup)  # fmt: skip
    answers[_graphql_args(1)] = _review()
    monkeypatch.setattr(subprocess, "run", _FakeGhByArgs(answers))

    (line,) = sweep.sweep(board, "widgets")

    assert (line.runs, line.finding) == ("fail", True)
    says = "so one of the check runs it cannot read failed"
    assert says in line.note
    assert line.to_dict()["note"] == line.note
    assert line.plain().endswith(f"{line.url} {line.note}")


@pytest.mark.parametrize(("steps", "runs", "finding"), [(0, "none", False), (1, "fail", True)])
def test_a_pr_whose_only_run_github_never_started_is_no_finding(
    board, monkeypatch, steps, runs, finding
):
    """sys-v3's test, #338's and #284's case: the PR's two Actions runs of one workflow (a
    push and a pull request) failed in two seconds, each one's job ran no step (GitHub gave
    the account no runner), GitHub refuses the token the check runs, and GitHub's overall
    state is FAILURE from those jobs alone. The runs never started: `runs` is `none`, the
    PR is no finding, and the line says why, once. Runs whose jobs ran a step and failed
    are still `fail` and a finding. The base branch read both as `fail`."""
    own = _pr(1, "card/wid-a1")
    sha = own["headRefOid"]
    answers = {_list_args(): json.dumps([own])}
    answers[_view_args(1)] = _pr_view(1, [sha])
    answers.update(_checks(sha, _runs("failure", "failure"), steps=steps))
    answers[("api", f"repos/{REPO}/commits/{sha}/check-runs?per_page=100")] = (
        1,
        "gh: Resource not accessible by personal access token (HTTP 403)",
    )
    owner, name = REPO.split("/")
    counts = {"checkRunCountsByState": [{"state": "FAILURE", "count": 2}]}
    rollup = {"state": "FAILURE", "contexts": counts}
    answers[
        (
            "api", "graphql", "-f", f"o={owner}", "-f", f"r={name}", "-f", f"sha={sha}",
            "-f", f"query={github._ROLLUP_QUERY}",
        )
    ] = json.dumps({"data": {"repository": {"object": {"statusCheckRollup": rollup}}}})  # fmt: skip
    answers[_graphql_args(1)] = _review()
    monkeypatch.setattr(subprocess, "run", _FakeGhByArgs(answers))

    (line,) = sweep.sweep(board, "widgets")

    assert (line.runs, line.finding) == (runs, finding)
    assert line.note.count("GitHub never started the CI run") == (1 if steps == 0 else 0)
    assert line.plain().startswith(f"wid-a1 MERGEABLE runs={runs} ")


def test_sweep_reads_checks_pr_and_review_with_the_existing_github_reads(board, monkeypatch):
    fake = _four_prs_and_a_feature()
    monkeypatch.setattr(subprocess, "run", fake)

    sweep.sweep(board, "widgets")

    per_pr = [tuple(call[1:]) for call in fake.calls[1:]]
    assert _checks_args(_pr(3, "x")["headRefOid"]) in per_pr
    assert _view_args(3) in per_pr
    assert _graphql_args(3) in per_pr


def test_a_stacked_pr_that_conflicts_with_main_is_a_finding_naming_the_file(
    board, monkeypatch
):
    """PR #2 (card/wid-a2) is stacked on card/wid-a1 (PR #1) and changes f.txt; main then
    changes f.txt too, after the project's checkout was cloned. GitHub compares #2 only
    with its base card/wid-a1 and reports it MERGEABLE, its runs passed and no comment
    waits. The sweep fetches origin, merges each branch with main by `git merge-tree`, and
    marks #2 a finding naming f.txt; #1, which leaves f.txt alone, stays clean (sys-u7).
    The checkout's own branches and files stay as they were."""
    origin, clone = board.parent / "origin", board.parent / "clone"
    author = board.parent / "author"
    _REAL_RUN(["git", "clone", "-q", str(origin), str(author)], check=True, capture_output=True)
    _git(author, "checkout", "-q", "card/wid-a1")
    _commit(author, "g.txt", "a\n")
    _git(author, "push", "-q", "origin", "card/wid-a1")
    _git(author, "checkout", "-q", "-b", "card/wid-a2")
    _commit(author, "f.txt", "b\n")
    _git(author, "push", "-q", "-f", "origin", "card/wid-a2")
    _git(author, "checkout", "-q", "main")
    _commit(author, "f.txt", "main\n")
    _git(author, "push", "-q", "origin", "main")
    local_main = _git(clone, "rev-parse", "main")
    prs = [_pr(1, "card/wid-a1"), _pr(2, "card/wid-a2")]
    monkeypatch.setattr(subprocess, "run", _clean_gh(prs))

    a, b = sweep.sweep(board, "widgets")

    assert (b.mergeable, b.runs, b.unanswered, b.finding) == ("MERGEABLE", "pass", 0, True)
    assert b.conflicts == ["f.txt"]
    assert (a.mergeable, a.conflicts, a.finding) == ("MERGEABLE", [], False)
    assert b.to_dict()["conflicts"] == ["f.txt"]
    assert b.plain() == (
        f"- MERGEABLE runs=pass unanswered=0 conflicts=f.txt finding {REPO_URL}/pull/2"
    )
    assert _git(clone, "rev-parse", "main") == local_main
    assert _git(clone, "status", "--porcelain") == ""


def test_the_sweep_fails_plainly_when_a_prs_branch_is_not_on_origin(board, monkeypatch):
    """git cannot merge a branch it cannot find: the sweep raises and names the branch,
    rather than counting the PR as clean."""
    monkeypatch.setattr(subprocess, "run", _clean_gh([_pr(9, "card/wid-gone")]))

    with pytest.raises(github.GitHubError, match="origin/card/wid-gone"):
        sweep.sweep(board, "widgets")


@pytest.mark.parametrize(
    ("conclusions", "expected"),
    [
        ((), "none"),
        (("success",), "pass"),
        (("success", None), "pending"),
        (("success", None, "failure"), "fail"),
        (("cancelled",), "fail"),
    ],
)
def test_runs_state(conclusions, expected):
    checks = [
        github.Check(
            name="CI",
            status="completed" if c is not None else "queued",
            conclusion=c,
            url="https://x",
        )
        for c in conclusions
    ]
    assert sweep.runs_state(checks) == expected


def test_runs_state_counts_a_run_github_never_started_as_no_check():
    unstarted = github.Check(
        name="CI", status="completed", conclusion="failure", url="https://x",
        unstarted_jobs=["failure"],
    )
    passed = github.Check(name="Lint", status="completed", conclusion="success", url="https://y")
    assert sweep.runs_state([unstarted]) == "none"
    assert sweep.runs_state([unstarted, passed]) == "pass"


def test_sweep_raises_when_gh_pr_list_fails(board, monkeypatch):
    monkeypatch.setattr(subprocess, "run", _FakeGhByArgs({}))

    with pytest.raises(github.GitHubError, match="gh pr list --repo riodeuno/widgets"):
        sweep.sweep(board, "widgets")


# --------------------------------------------------------------------------- CLI --


def _cli(monkeypatch, capsys, *argv: str) -> str:
    monkeypatch.setattr("sys.argv", ["autonomouse", *argv])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (None, 0), capsys.readouterr().err
    return capsys.readouterr().out


def test_cli_sweep_prints_one_line_per_own_pr(board, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _four_prs_and_a_feature())

    out = _cli(monkeypatch, capsys, "github", "sweep", "widgets").splitlines()

    assert out == [
        f"wid-a1 MERGEABLE runs=pass unanswered=0 ok {REPO_URL}/pull/1",
        f"- CONFLICTING runs=pass unanswered=0 finding {REPO_URL}/pull/2",
        f"- MERGEABLE runs=fail unanswered=0 finding {REPO_URL}/pull/3",
        f"- MERGEABLE runs=pass unanswered=1 finding {REPO_URL}/pull/4",
    ]


def test_cli_sweep_json_holds_the_same(board, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _four_prs_and_a_feature())

    data = json.loads(_cli(monkeypatch, capsys, "github", "sweep", "widgets", "--json"))

    assert [d["number"] for d in data] == [1, 2, 3, 4]
    assert [d["card_id"] for d in data] == ["wid-a1", "-", "-", "-"]
    assert [d["finding"] for d in data] == [False, True, True, True]
    assert data[3] == {
        "number": 4,
        "url": f"{REPO_URL}/pull/4",
        "head_branch": "card/wid-a4",
        "head_sha": _pr(4, "x")["headRefOid"],
        "card_id": "-",
        "mergeable": "MERGEABLE",
        "runs": "pass",
        "unanswered": 1,
        "conflicts": [],
        "finding": True,
        "note": "",
    }


def test_cli_sweep_fails_plainly_when_gh_fails(board, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", _FakeGhByArgs({}))
    monkeypatch.setattr("sys.argv", ["autonomouse", "github", "sweep", "widgets"])

    with pytest.raises(SystemExit) as e:
        cli.main()

    assert e.value.code != 0
    assert "gh pr list --repo riodeuno/widgets" in capsys.readouterr().err


# -------------------------------------------------------------------------- pick --


def _line(number: int, *, runs: str = "pass") -> sweep.Line:
    """One line of the sweep: a finding when its runs failed, else a PR that needs nothing."""
    return sweep.Line(
        number=number, url=f"{REPO_URL}/pull/{number}", head_branch=f"card/wid-a{number}",
        head_sha=_pr(number, "x")["headRefOid"], card_id=f"wid-a{number}",
        mergeable="MERGEABLE", runs=runs, unanswered=0,
    )


def test_pick_takes_a_project_with_a_finding_and_no_ready_card(board):
    """wid-a1 is done; nothing is ready. The injected sweep decides, and the pick keeps the
    lines that are findings, which Jev's `go` reads as the wake's work (sys-s22)."""
    lines = []
    asked = []
    ok, failed = _line(1), _line(3, runs="fail")

    def finding(root, slug):
        asked.append((root, slug))
        return [ok, failed]

    chosen = controller.pick(board, 2, sweep=finding, out=lines.append)

    assert asked == [(board, "widgets")]
    assert chosen is not None and chosen.project.slug == "widgets"
    assert chosen.resume == [] and chosen.ready == [] and chosen.batch == []
    assert chosen.findings == [failed]
    assert lines == ["sweep=widgets: finding"]


def test_pick_returns_none_when_the_sweep_finds_nothing(board):
    lines = []
    assert controller.pick(board, 2, sweep=lambda root, slug: [_line(1)], out=lines.append) is None
    assert lines == ["sweep=widgets: no finding"]


def test_pick_returns_none_and_prints_when_the_sweep_raises(board):
    lines = []

    def broken(root, slug):
        raise github.GitHubError("gh pr list failed: not logged in")

    assert controller.pick(board, 2, sweep=broken, out=lines.append) is None
    assert lines == ["sweep=widgets: failed, no finding counted: gh pr list failed: not logged in"]


def test_pick_without_a_sweep_asks_nothing(board):
    assert controller.pick(board, 2) is None


def test_pick_asks_the_sweep_only_for_a_project_with_no_work(board):
    card_new(board, card_id="wid-a2", project="widgets", title="t", goal="g", test="t")
    card_move(board, "wid-a2", to="todo")
    asked = []

    def finding(root, slug):
        asked.append(slug)
        return [_line(3, runs="fail")]

    chosen = controller.pick(board, 2, sweep=finding, out=lambda line: None)

    assert [c.id for c in chosen.ready] == ["wid-a2"]
    assert asked == [] and chosen.findings == []


# ------------------------------------------------- two projects, one repository --


@pytest.fixture
def shared(board: Path) -> Path:
    """The board with a second project, `gadgets` (priority 2), on the same repository as
    `widgets`; its done card gad-a1 has PR #6. Nothing is ready in either project."""
    project_new(
        board, slug="gadgets", name="Gadgets", goal="g", repo=REPO_URL,
        repo_path=str(board.parent / "clone"),
        default_branch="main", priority=2,
    )
    card_new(board, card_id="gad-a1", project="gadgets", title="t", goal="g", test="t")
    path = board / "projects" / "gadgets" / "cards" / "gad-a1.json"
    card = load_card(path)
    card.stack = Stack.DONE
    card.pr = f"{REPO_URL}/pull/6"
    save_card(path, card)
    commit_board(board, "gadgets")
    return board


def _clean_gh(prs: list[dict]) -> _FakeGhByArgs:
    """`gh` for the open PRs `prs`: each one's runs passed and no comment waits."""
    answers = {_list_args(): json.dumps(prs)}
    for pr in prs:
        n, sha = pr["number"], pr["headRefOid"]
        answers[_view_args(n)] = _pr_view(n, [sha])
        answers.update(_checks(sha, _runs("success")))
        answers[_graphql_args(n)] = _review()
    return _FakeGhByArgs(answers)


def test_sweep_prints_another_projects_card_with_its_project_and_no_finding(
    shared, monkeypatch, capsys
):
    """gad-a1's PR conflicts. The widgets sweep prints it as gadgets/gad-a1 and ok; the
    gadgets sweep prints it as gad-a1 and a finding."""
    monkeypatch.setattr(subprocess, "run", _clean_gh([_pr(6, "card/gad-a1", "CONFLICTING")]))

    widgets = _cli(monkeypatch, capsys, "github", "sweep", "widgets").splitlines()
    as_json = json.loads(_cli(monkeypatch, capsys, "github", "sweep", "widgets", "--json"))
    gadgets = _cli(monkeypatch, capsys, "github", "sweep", "gadgets").splitlines()

    assert widgets == [
        f"gadgets/gad-a1 CONFLICTING runs=pass unanswered=0 ok {REPO_URL}/pull/6"
    ]
    assert [(d["card_id"], d["finding"]) for d in as_json] == [("gadgets/gad-a1", False)]
    assert gadgets == [
        f"gad-a1 CONFLICTING runs=pass unanswered=0 finding {REPO_URL}/pull/6"
    ]


def test_a_pr_with_no_card_in_any_project_is_still_a_finding(shared, monkeypatch):
    """A wake's interim PR (#7) has no card in either project: it is a finding for both,
    while gad-a1's conflicting PR (#6) is gadgets' alone."""
    monkeypatch.setattr(
        subprocess,
        "run",
        _clean_gh(
            [
                _pr(6, "card/gad-a1", "CONFLICTING"),
                _pr(7, "wake/20260926-000000-abcd", "CONFLICTING"),
            ]
        ),
    )

    widgets = sweep.sweep(shared, "widgets")
    gadgets = sweep.sweep(shared, "gadgets")

    assert [(line.card_id, line.finding) for line in widgets] == [
        ("gadgets/gad-a1", False), ("-", True)
    ]
    assert [(line.card_id, line.finding) for line in gadgets] == [("gad-a1", True), ("-", True)]


def test_pick_leaves_another_projects_finding_to_that_project(shared, monkeypatch):
    """Only gad-a1's PR conflicts. widgets (priority 1) is asked first and has no finding;
    the pick is gadgets, with that PR as its finding."""
    monkeypatch.setattr(subprocess, "run", _clean_gh([_pr(6, "card/gad-a1", "CONFLICTING")]))
    lines = []

    chosen = controller.pick(shared, 2, sweep=sweep.sweep, out=lines.append)

    assert lines == ["sweep=widgets: no finding", "sweep=gadgets: finding"]
    assert chosen is not None and chosen.project.slug == "gadgets"
    assert [line.card_id for line in chosen.findings] == ["gad-a1"]
    assert controller.pick(shared, 2, only="widgets", sweep=sweep.sweep, out=print) is None


WAKE_RUN = "20260926-010203-a1b2"


def _wake_run(board: Path, project: str) -> None:
    """Write the run record of wake WAKE_RUN, whose project is `project` and whose interim
    branch is `wake/WAKE_RUN`."""
    run = run_start(board, run_id=WAKE_RUN, session_id="sess", pid=4242, model="opus")
    run.project = project
    run.interim_branch = f"wake/{WAKE_RUN}"
    save_run(board, run)


def test_a_wake_pr_is_a_finding_only_for_the_project_its_run_record_names(
    shared, monkeypatch, capsys
):
    """Wake WAKE_RUN's run record names gadgets, and its interim PR (#7) conflicts: the
    widgets sweep prints it as gadgets/- and ok, the gadgets sweep as - and a finding. The
    interim PR of a wake the board has no run record of (#8) is a finding for both."""
    _wake_run(shared, "gadgets")
    monkeypatch.setattr(
        subprocess,
        "run",
        _clean_gh(
            [
                _pr(7, f"wake/{WAKE_RUN}", "CONFLICTING"),
                _pr(8, "wake/20260926-111111-beef", "CONFLICTING"),
            ]
        ),
    )

    widgets = _cli(monkeypatch, capsys, "github", "sweep", "widgets").splitlines()
    as_json = json.loads(_cli(monkeypatch, capsys, "github", "sweep", "widgets", "--json"))
    gadgets = _cli(monkeypatch, capsys, "github", "sweep", "gadgets").splitlines()

    assert widgets == [
        f"gadgets/- CONFLICTING runs=pass unanswered=0 ok {REPO_URL}/pull/7",
        f"- CONFLICTING runs=pass unanswered=0 finding {REPO_URL}/pull/8",
    ]
    assert [(d["card_id"], d["finding"]) for d in as_json] == [
        ("gadgets/-", False), ("-", True)
    ]
    assert gadgets == [
        f"- CONFLICTING runs=pass unanswered=0 finding {REPO_URL}/pull/7",
        f"- CONFLICTING runs=pass unanswered=0 finding {REPO_URL}/pull/8",
    ]
    assert any(line.finding for line in sweep.sweep(shared, "gadgets"))


def test_pick_leaves_another_projects_wake_pr_to_that_project(shared, monkeypatch):
    """Only gadgets' interim PR (#7) conflicts. widgets (priority 1) is asked first and
    has no finding; the pick is gadgets, with that PR as its finding."""
    _wake_run(shared, "gadgets")
    monkeypatch.setattr(
        subprocess, "run", _clean_gh([_pr(7, f"wake/{WAKE_RUN}", "CONFLICTING")])
    )
    lines = []

    chosen = controller.pick(shared, 2, sweep=sweep.sweep, out=lines.append)

    assert lines == ["sweep=widgets: no finding", "sweep=gadgets: finding"]
    assert chosen is not None and chosen.project.slug == "gadgets"
    assert [(line.number, line.card_id) for line in chosen.findings] == [(7, "-")]
    assert not any(line.finding for line in sweep.sweep(shared, "widgets"))


# -------------------------------------------------------------------------- wake --


def test_a_wake_starts_on_a_project_with_a_finding_and_no_cards(env):  # noqa: F811
    """No project has a ready card; the sweep finds a PR of `high` that needs work, so the
    wake starts an orchestrator session there with no cards (the charter tells it to work
    the findings, sys-s17). `low` is asked first (priority 1) and has none."""
    for card_id in ("hi-a1", "hi-a2"):
        card_move(env.root, card_id, to="backlog", note="set aside")
    asked = []

    def finding(root, slug):
        asked.append(slug)
        return [_line(3, runs="fail")] if slug == "high" else []

    assert env.wake(sweep=finding) == 0

    assert asked == ["low", "high"]
    assert "sweep=low: no finding" in env.lines and "sweep=high: finding" in env.lines
    assert next(line for line in env.lines if line.startswith("project=")) == "project=high"
    assert "ready=" in env.lines and "resume=" in env.lines
    prompt = _prompt(_args(env, 0))
    assert "Resume these in_progress cards: none. Start these ready cards: none." in prompt
    run_id = next(line for line in env.lines if line.startswith("run_id=")).split("=", 1)[1]
    run = json.loads((env.root / "runs" / f"{run_id}.json").read_text())
    assert run["cards"] == [] and run["project"] == "high"


def test_a_failing_sweep_never_stops_the_wake(env):  # noqa: F811
    for card_id in ("hi-a1", "hi-a2"):
        card_move(env.root, card_id, to="backlog", note="set aside")

    def broken(root, slug):
        raise github.GitHubError("gh is offline")

    assert env.wake(sweep=broken) == 0

    assert "sweep=low: failed, no finding counted: gh is offline" in env.lines
    assert "sweep=high: failed, no finding counted: gh is offline" in env.lines
    assert env.lines[-1] == "nothing to do: no project has ready or reclaimable cards"
    assert not (env.fake_dir / "args").exists()


def test_a_wake_without_a_sweep_calls_none(env):  # noqa: F811
    for card_id in ("hi-a1", "hi-a2"):
        card_move(env.root, card_id, to="backlog", note="set aside")

    assert env.wake() == 0

    assert not any(line.startswith("sweep=") for line in env.lines)


def test_a_done_project_is_never_swept(env):  # noqa: F811
    """`high` is marked done by hand while a PR of it needs work. The wake does not ask the
    sweep about it, and `low`, which is not done and has no card to start, is still asked."""
    for card_id in ("hi-a1", "hi-a2"):
        card_move(env.root, card_id, to="backlog", note="set aside")
    project_done(env.root, "high")
    asked = []

    def no_finding(root, slug):
        asked.append(slug)
        return []

    assert env.wake(sweep=no_finding) == 0

    assert asked == ["low"]
    assert env.lines[-1] == "nothing to do: no project has ready or reclaimable cards"
