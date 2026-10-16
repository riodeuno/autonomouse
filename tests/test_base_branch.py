"""sys-w10: a project whose pull requests go into `develop`, not `main`, works end to end.

The owner's goal of 2026-09-29: a project names the branch its pull requests go into, agents
open and merge pull requests only into that branch, and a merge into it still needs the
owner's word. Every step that treats the project's branch as the target reads the project's
`default_branch`, so each test here builds a project whose `default_branch` is `develop`,
on a local bare repository that holds both `main` and `develop`, and fails if the step used
`main` instead:

- a card's base (`github base-branch`, `github.compute_base_branch`);
- the worktree's start point (`card_start`, `board.worktrees.create`);
- a retarget once a stacked base merged (`github.retarget_to_main`), and
  the done card's follow-up that merges `origin/<branch>` into the card's branch
  (`followup.follow_up_done`);
- the conflict check (`sweep.sweep`, `git merge-tree` against `origin/<branch>`);
- the wake's shared branch `wake/<run id>` (`run_interim`);
- the stuck signal's wait (`card stuck`, `board.stuck`);
- the merge (`merge.merge_project`), which merges the pull requests the owner named into
  `develop`, a wake's final pull request among them.

`develop` and `main` differ on `shared.txt`, so a check against the wrong branch gives a
different answer. `gh` is faked; `git` runs for real against the local repositories.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse import cli, followup, merge, sweep
from autonomouse import github as gh
from autonomouse.board.commands import (
    card_move,
    card_new,
    card_progress,
    card_show,
    project_new,
    project_show,
)
from autonomouse.board.history import append_history
from autonomouse.board.runs import card_start, project_claim, run_interim, run_start
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card

REPO = "riodeuno/widgets"
REPO_URL = f"https://github.com/{REPO}"
RUN = "20260929-090000-abcd"
_REAL_RUN = subprocess.run


def _git(cwd: Path, *args: str) -> str:
    return _REAL_RUN(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


class Env:
    """The board root, the project's checkout (`repo_path`), its bare `origin`, the
    worktree root, and the SHAs of `origin/main` and `origin/develop`."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.root = tmp / "board"
        self.origin = tmp / "origin.git"
        self.checkout = tmp / "repo"
        self.worktrees = tmp / "worktrees"
        self.main = ""
        self.develop = ""

    def branch_from_main(self, branch: str, path: str, text: str) -> str:
        """Push `branch`, one commit on `main` that writes `path`; return its SHA."""
        seed = self.tmp / "seed"
        _git(seed, "checkout", "-q", "-B", branch, "origin/main")
        (seed / path).write_text(text)
        _git(seed, "add", path)
        _git(seed, "commit", "-q", "-m", f"{branch}: {path}")
        _git(seed, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return _git(seed, "rev-parse", "HEAD")

    def started(self, card_id: str, *, base_branch: str) -> Path:
        """Move `card_id` to `todo` and start it for run RUN on `base_branch`; return its
        worktree."""
        card_move(self.root, card_id, "todo")
        card = card_start(
            self.root, card_id, branch=f"card/{card_id}", base_branch=base_branch,
            session_id="s", pid=os.getpid(), model="opus", effort="high",
            agent_type="worker-high", run_id=RUN, worktrees_root=self.worktrees,
        )  # fmt: skip
        return Path(card.worktree)

    def claimed(self) -> None:
        run_start(self.root, run_id=RUN, session_id="s", pid=os.getpid(), model="m")
        project_claim(self.root, "widgets", run_id=RUN)


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Env:
    """Origin holds `main` (shared.txt `base`) and `develop` (shared.txt `develop`, one more
    commit); the board holds project `widgets` whose `default_branch` is `develop`."""
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "test@example.com")
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    e = Env(tmp_path)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(e.origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(e.origin), str(seed))
    _git(seed, "checkout", "-q", "-b", "main")
    (seed / "shared.txt").write_text("base\n")
    _git(seed, "add", "shared.txt")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", "develop")
    (seed / "shared.txt").write_text("develop\n")
    _git(seed, "commit", "-q", "-am", "develop")
    _git(seed, "push", "-q", "origin", "develop")
    _git(tmp_path, "clone", "-q", str(e.origin), str(e.checkout))
    e.main = _git(e.checkout, "rev-parse", "origin/main")
    e.develop = _git(e.checkout, "rev-parse", "origin/develop")
    assert e.main != e.develop
    project_new(
        e.root, slug="widgets", name="Widgets", goal="g", repo=REPO_URL,
        repo_path=str(e.checkout), default_branch="develop", priority=1,
    )  # fmt: skip
    return e


class FakeGh:
    """`subprocess.run`: `gh` calls answered as GitHub would for the pull requests in
    `prs` (number -> dict with head, base, sha, state, draft); every other command, such
    as git, runs for real. `merged_branches` are the branches whose own PR merged."""

    def __init__(self, prs: dict[int, dict] | None = None, merged_branches=()):
        self.prs = prs or {}
        self.merged_branches = set(merged_branches)
        self.calls: list[list[str]] = []

    def url(self, number: int) -> str:
        return f"{REPO_URL}/pull/{number}"

    def _pr(self, ref: str) -> tuple[int, dict]:
        number = int(ref.rsplit("/", 1)[-1])
        return number, self.prs[number]

    def __call__(self, args, *pos, **kw):
        if args[0] != "gh":
            return _REAL_RUN(args, *pos, **kw)
        self.calls.append(list(args))
        answer = self._answer(list(args[1:]))
        if isinstance(answer, tuple):
            return subprocess.CompletedProcess(args, answer[0], stdout="", stderr=answer[1])
        return subprocess.CompletedProcess(args, 0, stdout=answer, stderr="")

    def _answer(self, a: list[str]) -> str | tuple[int, str]:
        if a[:3] == ["pr", "list", "--repo"]:
            return json.dumps(
                [
                    {
                        "number": n, "url": self.url(n), "headRefName": p["head"],
                        "headRefOid": p["sha"], "mergeable": "MERGEABLE",
                        "isCrossRepository": False,
                    }
                    for n, p in self.prs.items()
                    if p["state"] == "OPEN"
                ]
            )
        if a[:2] == ["pr", "view"] and a[3:5] == ["--repo", REPO]:
            ref, fields = a[2], a[6]
            if fields == "state" and not ref.startswith(REPO_URL):
                if ref in self.merged_branches:
                    return json.dumps({"state": "MERGED"})
                return 1, f'no pull requests found for branch "{ref}"'
            number, p = self._pr(ref)
            if fields == "number,url,state,headRefOid,baseRefName,commits":
                return json.dumps(
                    {
                        "number": number, "url": self.url(number), "state": p["state"],
                        "headRefOid": p["sha"], "baseRefName": p["base"],
                        "commits": [{"oid": p["sha"]}],
                    }
                )
            if fields == "mergeable":
                return json.dumps({"mergeable": "MERGEABLE"})
            if fields == "mergeCommit":
                return json.dumps({"mergeCommit": {"oid": "c" * 40}})
        if a[:2] == ["pr", "edit"] and a[3:6] == ["--repo", REPO, "--base"]:
            self.prs.setdefault(int(a[2]), {})["base"] = a[6]
            return ""
        if a[:2] == ["pr", "ready"]:
            self._pr(a[2])[1]["draft"] = False
            return ""
        if a[:2] == ["pr", "merge"]:
            _, p = self._pr(a[2])
            if p["draft"] or a[3:] != ["--squash", "--match-head-commit", p["sha"]]:
                return 1, "refused"
            p["state"] = "MERGED"
            return "merged\n"
        if a[0] == "api":
            path = a[1]
            if "/actions/runs?head_sha=" in path:
                return json.dumps(
                    {
                        "workflow_runs": [
                            {
                                "name": "CI", "status": "completed", "conclusion": "success",
                                "id": 1, "html_url": f"{REPO_URL}/actions/runs/1",
                            }
                        ]
                    }
                )
            if path.endswith("/status?per_page=100"):
                return json.dumps({"state": "pending", "statuses": [], "total_count": 0})
            if path.endswith("/check-runs?per_page=100"):
                return json.dumps({"total_count": 0, "check_runs": []})
        raise AssertionError(f"the fake GitHub has no answer for gh {a}")

    def with_verb(self, *verb: str) -> list[list[str]]:
        return [c for c in self.calls if c[1 : 1 + len(verb)] == list(verb)]


def _cli(monkeypatch, capsys, *argv: str) -> str:
    monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (None, 0), capsys.readouterr().err
    return capsys.readouterr().out


def _done(env: Env, card_id: str, *, pr: int, base_branch: str | None) -> None:
    """Mark `card_id` done with PR #`pr`, as `card finish` leaves it."""
    path = env.root / "projects" / "widgets" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.DONE
    card.pr = f"{REPO_URL}/pull/{pr}"
    card.base_branch = base_branch
    append_history(card, run_id=RUN, from_="in_progress", to="done", note="finished")
    save_card(path, card)


# ------------------------------------------------------------ base and worktree --


def test_a_card_with_no_dependency_starts_from_develop(env, monkeypatch, capsys):
    """`github base-branch` answers `develop`; `card start` on that answer makes a
    worktree at `origin/develop`."""
    card_new(env.root, card_id="wid-a1", project="widgets", title="t", goal="g", test="t")
    plan = json.loads(_cli(monkeypatch, capsys, "github", "base-branch", "wid-a1", "--json"))
    assert plan == {"base_branch": "develop", "also_merge": []}

    env.claimed()
    worktree = env.started("wid-a1", base_branch=plan["base_branch"])
    assert _git(worktree, "rev-parse", "HEAD") == env.develop
    assert (worktree / "shared.txt").read_text() == "develop\n"
    card = card_show(env.root, "wid-a1")
    assert card.base_branch == "develop"


# ------------------------------------------------------------------- retarget --


def test_a_done_cards_follow_up_retargets_to_develop_and_merges_origin_develop(
    env, monkeypatch
):
    """A done card stacked on `card/wid-a0`, whose PR merged: its PR is pointed at
    `develop`, the card records `develop`, and `origin/develop` is merged into its branch
    and pushed."""
    env.branch_from_main("card/wid-a0", "a0.txt", "a0\n")
    card_new(env.root, card_id="wid-a2", project="widgets", title="t", goal="g", test="t")
    env.claimed()
    worktree = env.started("wid-a2", base_branch="card/wid-a0")
    (worktree / "a2.txt").write_text("a2\n")
    _git(worktree, "add", "a2.txt")
    _git(worktree, "commit", "-q", "-m", "wid-a2")
    _git(worktree, "push", "-q", "origin", "HEAD:refs/heads/card/wid-a2")
    _done(env, "wid-a2", pr=2, base_branch="card/wid-a0")
    head = _git(worktree, "rev-parse", "HEAD")

    fake = FakeGh(
        {2: {"head": "card/wid-a2", "base": "card/wid-a0", "sha": head, "state": "OPEN",
             "draft": True}},
        merged_branches={"card/wid-a0"},
    )  # fmt: skip
    monkeypatch.setattr(subprocess, "run", fake)
    card = card_show(env.root, "wid-a2")
    result = followup.follow_up_done(
        env.root, card, project_show(env.root, "widgets"), run_id=RUN
    )

    assert (result.retargeted_from, result.retargeted_to, result.conflicts) == (
        "card/wid-a0", "develop", [],
    )  # fmt: skip
    assert fake.prs[2]["base"] == "develop"
    assert card_show(env.root, "wid-a2").base_branch == "develop"
    pushed = _git(env.origin, "rev-parse", "refs/heads/card/wid-a2")
    assert _REAL_RUN(
        ["git", "merge-base", "--is-ancestor", env.develop, pushed], cwd=env.origin
    ).returncode == 0
    assert _git(env.origin, "log", "-1", "--format=%s", pushed) == (
        "wid-a2: merge develop after its base card/wid-a0 merged"
    )


# -------------------------------------------------------------- conflict check --


def test_the_conflict_check_compares_a_pr_with_origin_develop(env, monkeypatch):
    """`card/wid-a1` changes shared.txt from main's text: it merges cleanly into `main` and
    conflicts with `develop`, so the sweep's conflicts name shared.txt."""
    sha = env.branch_from_main("card/wid-a1", "shared.txt", "card\n")
    fake = FakeGh({1: {"head": "card/wid-a1", "base": "develop", "sha": sha,
                       "state": "OPEN", "draft": True}})  # fmt: skip
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        gh, "read_unresolved_review",
        lambda *a, **k: gh.UnresolvedReview(thread_comments=[], change_requests=[],
                                             comments=[]),
    )  # fmt: skip
    (line,) = sweep.sweep(env.root, "widgets")
    assert line.conflicts == ["shared.txt"]
    assert sweep.conflicts_with(
        str(env.checkout), default_branch="main", head_branch="card/wid-a1"
    ) == []


# --------------------------------------------------------- the wake's shared branch --


def test_the_wakes_shared_branch_starts_from_origin_develop(env):
    env.claimed()
    run = run_interim(env.root, RUN)
    assert run.interim_branch == f"wake/{RUN}"
    assert _git(env.origin, "rev-parse", f"refs/heads/wake/{RUN}") == env.develop


# ------------------------------------------------------------------ stuck signal --


def test_the_stuck_signal_waits_on_develop_and_not_on_main(env, monkeypatch, capsys):
    """On a project whose branch is `develop`, a worker waiting for develop to turn green
    fires the wait signal, naming develop; `main` is not the project's branch, so a note
    about main's state is no wait on it."""
    card_new(env.root, card_id="wid-a3", project="widgets", title="t", goal="g", test="t")
    env.claimed()
    env.started("wid-a3", base_branch="develop")

    card_progress(env.root, "wid-a3", next_step="wait for develop to turn green", run_id=RUN)
    out = _cli(monkeypatch, capsys, "card", "stuck", "wid-a3")
    assert out.strip() == (
        'wait: the next step waits on develop or the base branch ("develop to turn green"): '
        '"wait for develop to turn green"'
    )

    card_progress(
        env.root, "wid-a3", next_step="main is red on tests/x.py; fix shared.txt", run_id=RUN
    )
    assert _cli(monkeypatch, capsys, "card", "stuck", "wid-a3").strip() == ""


# ------------------------------------------------------------------- merge gate --


def test_the_merge_merges_the_named_prs_into_develop(env, monkeypatch):
    """The owner names a done card's green PR into `develop`, a card's PR into `main` and
    a wake's final PR into `develop`: the two into `develop` merge, naming develop, and the
    PR into `main` is never merged."""
    for card_id, pr in (("wid-b1", 1), ("wid-b2", 2)):
        card_new(env.root, card_id=card_id, project="widgets", title=f"card {card_id}",
                 goal="g", test="t")  # fmt: skip
        _done(env, card_id, pr=pr, base_branch="develop" if pr == 1 else None)
    fake = FakeGh(
        {
            1: {"head": "card/wid-b1", "base": "develop", "sha": "1" * 40, "state": "OPEN",
                "draft": True},
            2: {"head": "card/wid-b2", "base": "main", "sha": "2" * 40, "state": "OPEN",
                "draft": True},
            3: {"head": f"wake/{RUN}", "base": "develop", "sha": "3" * 40, "state": "OPEN",
                "draft": True},
        }
    )  # fmt: skip
    monkeypatch.setattr(subprocess, "run", fake)

    lines: list[str] = []
    merge.merge_project(env.root, "widgets", ["1", "2", "3"], delay=0, out=lines.append)
    assert [c[3] for c in fake.with_verb("pr", "merge")] == [fake.url(1), fake.url(3)]
    assert fake.prs[2]["state"] == "OPEN" and fake.prs[2]["base"] == "main"
    assert f"merge=widgets: merged {fake.url(1)} into develop as {'c' * 40} (wid-b1)" in lines
    assert f"merge=widgets: merged {fake.url(3)} into develop as {'c' * 40}" in lines
    assert [line for line in lines if fake.url(2) in line] == [
        (
            f"merge=widgets: left open {fake.url(2)}: gh pr view main --repo {REPO} --json "
            f'state failed: no pull requests found for branch "main" (wid-b2)'
        )
    ]
