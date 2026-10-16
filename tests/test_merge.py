"""`autonomouse github merge <project> <pr>...` (`autonomouse.merge`, sys-q4, sys-w5,
sys-w22): the super-orchestrator names the pull requests the owner told it to merge, and
the command merges each one that is green and merges cleanly into the default branch, in
stack order, printing why it leaves any one open. It asks nobody and sends nothing on
Telegram; with no pull request named it merges nothing. No wake runs it.

`subprocess.run` is faked the way `_FakeGh` in tests/test_github.py fakes it, a callable
returning `CompletedProcess`; this one answers from the pull requests it holds, as GitHub
would, and a merge, a retarget or marking a draft ready changes them. A `gh` call it does
not know fails the test. Telegram is test_inbox's `FakeTelegram` behind a patched
`urllib.request.urlopen`, so any message the command sent would be recorded."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomouse import cli, merge
from autonomouse import notify as telegram
from autonomouse.board.commands import (
    card_cancel,
    card_new,
    card_show,
    project_new,
)
from autonomouse.board.history import append_history
from autonomouse.board.runs import project_claim, run_start
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card
from test_inbox import FakeTelegram
from test_run_controller import env  # noqa: F401 — the wake fixture

REPO = "riodeuno/widgets"
# A run's conclusion in `_PR.runs` for a run GitHub completed without running a step of its
# job: no runner took it (sys-v3).
NEVER_STARTED = "never-started"
REPO_URL = f"https://github.com/{REPO}"
WAKE = "20260927-075439-2ee2"
CHAT = "555000111"  # the chat test_inbox's FakeTelegram accepts
_SEND, _NOTIFY = telegram.send, telegram.notify  # read before any fixture runs


@dataclass
class _PR:
    """One pull request on the fake GitHub."""

    number: int
    head: str
    base: str = "main"
    # GitHub's answer to each read of its mergeability, in turn; the last one stays.
    mergeable: list[str] = field(default_factory=lambda: ["MERGEABLE"])
    # The conclusion of each workflow run on its head commit; None for one still going,
    # NEVER_STARTED for one GitHub never started.
    runs: list[str | None] = field(default_factory=lambda: ["success"])
    cross: bool = False  # from a fork
    refuse: str | None = None  # GitHub's refusal of the merge
    state: str = "OPEN"
    draft: bool = True  # workers and orchestrators open every pull request as a draft
    pushes: int = 0  # commits pushed after it was opened, each a new head commit

    @property
    def url(self) -> str:
        return f"{REPO_URL}/pull/{self.number}"

    @property
    def sha(self) -> str:
        return f"{self.number:02d}{self.pushes:02d}" + "a" * 36

    @property
    def merge_sha(self) -> str:
        return f"{self.number:02d}" + "c" * 38


class _FakeGitHub:
    """`subprocess.run` for `gh` on the repository `REPO`. `merged_branches` and
    `open_branches` are the branches whose own pull request has merged or is still
    open, for a retarget; every call is recorded in `.calls`."""

    def __init__(self, *prs: _PR, merged_branches=(), open_branches=()):
        self.prs = {pr.number: pr for pr in prs}
        self.merged_branches = set(merged_branches)
        self.open_branches = set(open_branches)
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd=None, capture_output=True, text=True, check=False):
        self.calls.append(list(args))
        assert args[0] == "gh", f"not a gh call: {args}"
        answer = self._answer(list(args[1:]))
        if isinstance(answer, tuple):
            return subprocess.CompletedProcess(args, answer[0], stdout="", stderr=answer[1])
        return subprocess.CompletedProcess(args, 0, stdout=answer, stderr="")

    def _pr(self, url: str) -> _PR:
        return next(pr for pr in self.prs.values() if pr.url == url)

    def _answer(self, a: list[str]) -> str | tuple[int, str]:
        if a == [
            "pr", "list", "--repo", REPO, "--state", "open", "--limit", "200",
            "--json", "number,url,headRefName,headRefOid,mergeable,isCrossRepository",
        ]:  # fmt: skip
            return json.dumps(
                [
                    {
                        "number": pr.number,
                        "url": pr.url,
                        "headRefName": pr.head,
                        "headRefOid": pr.sha,
                        "mergeable": pr.mergeable[0],
                        "isCrossRepository": pr.cross,
                    }
                    for pr in self.prs.values()
                    if pr.state == "OPEN"
                ]
            )
        if a[:2] == ["pr", "view"] and a[3:6] == ["--repo", REPO, "--json"]:
            ref, fields = a[2], a[6]
            if fields == "state":  # a branch's own pull request, which a retarget reads
                merged_here = any(p.head == ref and p.state == "MERGED" for p in self.prs.values())
                if ref in self.merged_branches or merged_here:
                    return json.dumps({"state": "MERGED"})
                if ref in self.open_branches:
                    return json.dumps({"state": "OPEN"})
                return 1, f'no pull requests found for branch "{ref}"'
            pr = self._pr(ref)
            if fields == "number,url,state,headRefOid,baseRefName,commits":
                return json.dumps(
                    {
                        "number": pr.number,
                        "url": pr.url,
                        "state": pr.state,
                        "headRefOid": pr.sha,
                        "baseRefName": pr.base,
                        "commits": [{"oid": pr.sha}],
                    }
                )
            if fields == "mergeable":
                value = pr.mergeable.pop(0) if len(pr.mergeable) > 1 else pr.mergeable[0]
                return json.dumps({"mergeable": value})
            if fields == "mergeCommit":
                commit = {"oid": pr.merge_sha} if pr.state == "MERGED" else None
                return json.dumps({"mergeCommit": commit})
        if a[:2] == ["pr", "edit"] and a[3:] == ["--repo", REPO, "--base", "main"]:
            self.prs[int(a[2])].base = "main"
            return ""
        if a[:2] == ["pr", "ready"] and len(a) == 3:
            self._pr(a[2]).draft = False
            return ""
        if a[:2] == ["pr", "merge"]:
            pr = self._pr(a[2])
            if pr.draft:
                return 1, f"Pull request {REPO}#{pr.number} is still a draft"
            if a[3:] != ["--squash", "--match-head-commit", pr.sha]:
                return 1, "Head branch was modified. Review and try the merge again."
            if pr.refuse:
                return 1, pr.refuse
            pr.state = "MERGED"
            return f"Squashed and merged pull request #{pr.number}\n"
        if a[0] == "api":
            path = a[1]
            if path.startswith(f"repos/{REPO}/actions/runs/") and path.endswith("/jobs?per_page=100"):
                # the jobs of a run that completed and did not pass, which `read_checks` reads
                # to tell a run GitHub never started (sys-v3); a run's id is its PR's number
                # times 100 plus its place in `runs`
                run_id = int(path.split("/")[-2])
                ran = self.prs[run_id // 100].runs[run_id % 100 - 1] != NEVER_STARTED
                steps = [{"number": 1}] if ran else []
                return json.dumps({"jobs": [{"conclusion": "failure", "steps": steps}]})
            pr = next(pr for pr in self.prs.values() if pr.sha in path)
            if path == f"repos/{REPO}/actions/runs?head_sha={pr.sha}":
                return json.dumps(
                    {
                        "workflow_runs": [
                            {
                                "name": "CI",
                                "status": "in_progress" if c is None else "completed",
                                "conclusion": "failure" if c == NEVER_STARTED else c,
                                "id": pr.number * 100 + i,
                                "html_url": f"{REPO_URL}/actions/runs/{i}",
                            }
                            for i, c in enumerate(pr.runs, 1)
                        ]
                    }
                )
            if path == f"repos/{REPO}/commits/{pr.sha}/status?per_page=100":
                return json.dumps({"state": "pending", "statuses": [], "total_count": 0})
            if path == f"repos/{REPO}/commits/{pr.sha}/check-runs?per_page=100":
                return json.dumps({"total_count": 0, "check_runs": []})
        raise AssertionError(f"the fake GitHub has no answer for gh {a}")

    def merged(self) -> list[str]:
        """The URLs of the pull requests `gh pr merge` was called on, in order."""
        return [c[3] for c in self.calls if c[1:3] == ["pr", "merge"]]


@pytest.fixture
def board(tmp_path: Path, monkeypatch) -> Path:
    """Projects `widgets` and `gadgets` on one repository. `AUTONOMOUSE_HOME` points at
    the board for the CLI, `CLAUDE_CONFIG_DIR` at an empty folder where a test writes a
    live session's transcript, and `AUTONOMOUSE_CONFIG` at a folder holding no config and
    no secrets, so no test reads this machine's bot token or sends to its chat."""
    root = tmp_path / "board"
    for slug, priority in (("widgets", 1), ("gadgets", 2)):
        project_new(
            root, slug=slug, name=slug.capitalize(), goal="g", repo=REPO_URL,
            repo_path="/nowhere", default_branch="main", priority=priority,
        )  # fmt: skip
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return root


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    """Every way autonomouse sends on Telegram fails the test: nothing about a merge may
    reach the owner there.

    The stub replaces the channel in `telegram._CHANNELS`, which `send()` looks up at
    each call, and never `send` or `notify` themselves: a module first imported during
    a test here (`cli.main()` imports `autonomouse.cli.notify`) binds those by name, and
    a stub it bound would keep refusing in every later test on the same worker."""

    def refuse(text, *_):
        raise AssertionError(f"a merge sent on Telegram: {text!r}")

    monkeypatch.setitem(telegram._CHANNELS, "telegram", refuse)


def _done(
    root: Path,
    card_id: str,
    number: int,
    *,
    finished: str = "2026-09-27T10:00:00Z",
    base_branch: str | None = None,
):
    """A `done` card of `widgets` whose PR is #`number`, finished at `finished`."""
    card_new(root, card_id=card_id, project="widgets", title="t", goal="g", test="t")
    path = root / "projects" / "widgets" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.DONE
    card.pr = f"{REPO_URL}/pull/{number}"
    card.base_branch = base_branch
    append_history(
        card, run_id="20260927-000000-0000", from_="in_progress", to="done", note="finished",
        at=finished,
    )  # fmt: skip
    save_card(path, card)


def _run(board: Path, fake: _FakeGitHub, monkeypatch, *prs: str) -> list[str]:
    monkeypatch.setattr(subprocess, "run", fake)
    lines: list[str] = []
    merge.merge_project(board, "widgets", list(prs), delay=0, out=lines.append)
    return lines


def _merge_rows(board: Path, card_id: str) -> list[str]:
    return [h.note for h in card_show(board, card_id).history if h.run_id == "merge"]


def _cli(monkeypatch, capsys, *argv: str) -> str:
    monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code in (None, 0), capsys.readouterr().err
    return capsys.readouterr().out


def _secrets(tmp_path: Path) -> None:
    """A config with the owner's chat and a secrets file with a bot token, so a send
    would go out if any code path made one."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text(f'[notify]\ntelegram_chat_id = "{CHAT}"\n')
    (config / ".env").write_text("TELEGRAM_BOT_TOKEN=123456789:AAtest-token\n")


# ------------------------------------------------------- only what the owner named --


def test_with_no_pr_named_nothing_merges_and_nothing_is_sent(
    board, tmp_path, monkeypatch, capsys
):
    _done(board, "wid-a1", 1)
    fake = _FakeGitHub(_PR(1, "card/wid-a1"))
    monkeypatch.setattr(subprocess, "run", fake)
    _secrets(tmp_path)
    sent = FakeTelegram([])

    with patch("urllib.request.urlopen", side_effect=sent):
        out = _cli(monkeypatch, capsys, "github", "merge", "widgets")

    assert out == "merge=widgets: nothing merged: no pull request was named\n"
    assert fake.calls == []
    assert sent.sent == []
    assert _merge_rows(board, "wid-a1") == []


def test_only_the_named_green_pr_merges_and_nothing_is_sent(
    board, tmp_path, monkeypatch, capsys
):
    """No code path of the merge calls Telegram: with the bot token and chat configured,
    the command merges the one pull request named and sends nothing."""
    _done(board, "wid-a1", 1)
    _done(board, "wid-a2", 2)
    first, second = _PR(1, "card/wid-a1"), _PR(2, "card/wid-a2")
    fake = _FakeGitHub(first, second)
    monkeypatch.setattr(subprocess, "run", fake)
    _secrets(tmp_path)
    sent = FakeTelegram([])

    with patch("urllib.request.urlopen", side_effect=sent):
        out = _cli(monkeypatch, capsys, "github", "merge", "widgets", "1")

    assert out == f"merge=widgets: merged {first.url} into main as {first.merge_sha} (wid-a1)\n"
    assert fake.merged() == [first.url]
    assert second.state == "OPEN"
    ready = fake.calls.index(["gh", "pr", "ready", first.url])
    assert fake.calls.index(
        ["gh", "pr", "merge", first.url, "--squash", "--match-head-commit", first.sha]
    ) > ready
    assert sent.sent == []
    assert "TELEGRAM_BOT_TOKEN" not in os.environ
    (row,) = [h for h in card_show(board, "wid-a1").history if h.run_id == "merge"]
    assert (row.from_, row.to) == ("done", "done")
    assert row.note == f"merged {first.url} into main as {first.merge_sha}"
    assert _merge_rows(board, "wid-a2") == []


def test_a_pr_is_named_by_its_url_or_its_number(board, monkeypatch):
    first, second = _PR(1, "card/wid-a1"), _PR(2, "card/wid-a2")
    fake = _FakeGitHub(first, second)

    lines = _run(board, fake, monkeypatch, first.url, "#2")

    assert fake.merged() == [first.url, second.url]
    assert lines == [
        f"merge=widgets: merged {first.url} into main as {first.merge_sha}",
        f"merge=widgets: merged {second.url} into main as {second.merge_sha}",
    ]


def test_a_name_that_is_no_open_pr_of_the_project_is_left_open_with_its_reason(
    board, monkeypatch
):
    fake = _FakeGitHub(_PR(1, "card/wid-a1"), _PR(3, "card/wid-a3", state="MERGED"))
    other = "https://github.com/someone/else/pull/1"

    lines = _run(board, fake, monkeypatch, "3", "7", other, "main")

    assert fake.merged() == []
    assert lines == [
        f"merge=widgets: left open {ref}: it is not an open pull request of {REPO}"
        for ref in ("3", "7", other, "main")
    ]


# --------------------------------------------------------------------- stack order --


def test_two_named_stacked_prs_merge_base_first_and_the_second_is_retargeted(
    board, monkeypatch
):
    _done(board, "wid-a1", 1)
    _done(board, "wid-a2", 2, base_branch="card/wid-a1")
    below = _PR(1, "card/wid-a1")
    above = _PR(2, "card/wid-a2", base="card/wid-a1", mergeable=["UNKNOWN", "MERGEABLE"])
    fake = _FakeGitHub(below, above)

    lines = _run(board, fake, monkeypatch, "2", "1")  # named top first

    assert fake.merged() == [below.url, above.url]
    retarget = fake.calls.index(["gh", "pr", "edit", "2", "--repo", REPO, "--base", "main"])
    assert fake.calls.index(
        ["gh", "pr", "merge", below.url, "--squash", "--match-head-commit", below.sha]
    ) < retarget
    assert lines == [
        f"merge=widgets: merged {below.url} into main as {below.merge_sha} (wid-a1)",
        (
            f"merge=widgets: retargeted {above.url} from card/wid-a1 to main, since that "
            "branch merged"
        ),
        f"merge=widgets: merged {above.url} into main as {above.merge_sha} (wid-a2)",
    ]


def test_a_pr_on_a_named_pr_left_open_stays_open(board, monkeypatch):
    below = _PR(1, "card/wid-a1", runs=["failure"])
    above = _PR(2, "card/wid-a2", base="card/wid-a1")
    fake = _FakeGitHub(below, above, open_branches={"card/wid-a1"})

    lines = _run(board, fake, monkeypatch, "2", "1")

    assert fake.merged() == []
    assert not [c for c in fake.calls if c[1:3] == ["pr", "edit"]]
    assert lines == [
        f"merge=widgets: left open {below.url}: a check failed: CI {REPO_URL}/actions/runs/1",
        f"merge=widgets: left open {above.url}: its base card/wid-a1 has not merged",
    ]


def test_a_named_pr_on_a_branch_merged_earlier_is_retargeted_then_merged(board, monkeypatch):
    on_merged = _PR(1, "card/wid-a1", base="card/wid-a0")
    fake = _FakeGitHub(on_merged, merged_branches={"card/wid-a0"})

    lines = _run(board, fake, monkeypatch, "1")

    assert fake.merged() == [on_merged.url]
    assert lines == [
        (
            f"merge=widgets: retargeted {on_merged.url} from card/wid-a0 to main, since "
            "that branch merged"
        ),
        f"merge=widgets: merged {on_merged.url} into main as {on_merged.merge_sha}",
    ]


def test_a_named_wake_final_pr_merges_like_any_other(board, monkeypatch):
    run_start(board, run_id=WAKE, session_id="s", pid=1, model="m", kind="wake",
              project="widgets")  # fmt: skip
    final = _PR(7, f"wake/{WAKE}")
    fake = _FakeGitHub(final, _PR(8, "card/wid-a8"))

    lines = _run(board, fake, monkeypatch, final.url)

    assert fake.merged() == [final.url]
    assert lines == [f"merge=widgets: merged {final.url} into main as {final.merge_sha}"]


# ------------------------------------------------------ what is left open, and why --


@pytest.mark.parametrize(
    ("pr", "reason"),
    [
        (_PR(1, "card/wid-a1", mergeable=["CONFLICTING"]), "GitHub reports it CONFLICTING"),
        (
            _PR(1, "card/wid-a1", runs=["success", None]),
            f"checks still going: CI {REPO_URL}/actions/runs/2",
        ),
        (_PR(1, "card/wid-a1", runs=[]), "no check has run on its head commit yet"),
        (
            _PR(1, "card/wid-a1", runs=["success", "failure"]),
            f"a check failed: CI {REPO_URL}/actions/runs/2",
        ),
        (
            _PR(1, "card/wid-a1", runs=[NEVER_STARTED]),
            f"GitHub never started: CI {REPO_URL}/actions/runs/1",
        ),
    ],
    ids=["conflicting", "run-in-progress", "no-run-yet", "run-failed", "run-never-started"],
)
def test_a_named_pr_that_is_red_or_conflicting_is_left_open_with_its_reason(
    board, monkeypatch, pr, reason
):
    _done(board, "wid-a1", 1)
    fake = _FakeGitHub(pr)

    lines = _run(board, fake, monkeypatch, "1")

    assert fake.merged() == []
    assert pr.state == "OPEN"
    assert lines == [f"merge=widgets: left open {pr.url}: {reason} (wid-a1)"]
    assert _merge_rows(board, "wid-a1") == []


def test_an_unknown_mergeability_is_read_again_until_github_knows(board, monkeypatch):
    pr = _PR(1, "card/wid-a1", mergeable=["UNKNOWN", "UNKNOWN", "MERGEABLE"])
    fake = _FakeGitHub(pr)

    lines = _run(board, fake, monkeypatch, "1")

    reads = [c for c in fake.calls if c[-2:] == ["--json", "mergeable"]]
    assert len(reads) == 3
    assert lines == [f"merge=widgets: merged {pr.url} into main as {pr.merge_sha}"]


def test_an_unknown_mergeability_is_read_eight_more_times_then_left_open(board, monkeypatch):
    pr = _PR(1, "card/wid-a1", mergeable=["UNKNOWN"])
    fake = _FakeGitHub(pr)
    sleeps: list[float] = []
    monkeypatch.setattr(merge.time, "sleep", sleeps.append)

    lines = _run(board, fake, monkeypatch, "1")

    assert len([c for c in fake.calls if c[-2:] == ["--json", "mergeable"]]) == 9
    assert sleeps == [0] * 8  # the delay the caller passed, between reads
    assert fake.merged() == []
    assert lines == [f"merge=widgets: left open {pr.url}: GitHub reports it UNKNOWN"]


def test_a_merge_github_refuses_is_a_row_on_the_card(board, monkeypatch):
    _done(board, "wid-a1", 1)
    refusal = "Pull request is not mergeable: the base branch policy prohibits the merge"
    pr = _PR(1, "card/wid-a1", refuse=refusal)
    fake = _FakeGitHub(pr)

    lines = _run(board, fake, monkeypatch, "1")

    (row,) = _merge_rows(board, "wid-a1")
    assert row.startswith(f"merge of {pr.url} refused: gh pr merge {pr.url} --squash")
    assert row.endswith(refusal)
    assert lines == [f"merge=widgets: {row} (wid-a1)"]


def test_a_live_claim_on_the_same_repository_merges_nothing(board, tmp_path, monkeypatch):
    run_id = "20260927-120000-abcd"
    run_start(board, run_id=run_id, session_id="s-live", pid=os.getpid(), model="m")
    project_claim(board, "gadgets", run_id=run_id)
    folder = tmp_path / "claude-config" / "projects" / "-elsewhere"
    folder.mkdir(parents=True)
    (folder / "s-live.jsonl").write_text('{"type":"assistant"}\n')
    fake = _FakeGitHub(_PR(1, "card/wid-a1"))

    lines = _run(board, fake, monkeypatch, "1")

    assert fake.calls == []
    (line,) = lines
    assert line.startswith(
        "merge=widgets: nothing merged: a live wake holds its repository, project "
        f"'gadgets' held by run '{run_id}' (alive: pid exists; transcript written"
    )


# --------------------------------------------------- the old Telegram merge question --


def test_a_send_imported_by_name_during_a_test_is_the_real_one_and_still_refuses():
    """A module first imported while `_no_telegram` is active, as `autonomouse.cli.notify`
    is on the first `cli.main()`, binds the real `send` and `notify`, so no stub of this
    file outlives its test (sys-w121); a send through those names still fails the test."""
    from autonomouse.notify import notify, send

    assert (send, notify) == (_SEND, _NOTIFY)
    with pytest.raises(AssertionError, match="a merge sent on Telegram: 'hi'"):
        send("hi", config={})
    with pytest.raises(AssertionError, match="a merge sent on Telegram: 'hi'"):
        notify("hi", config={})


# ------------------------------------------------------------------------ the wake --


def test_a_wake_merges_nothing_and_asks_nothing(
    env, monkeypatch  # noqa: F811 — the fixture imported above
):
    """A wake merges no pull request, even a green one of a done card, and asks nobody
    whether to: only the super-orchestrator merges, on the owner's word."""
    for card_id in ("hi-a1", "hi-a2", "low-a1"):
        card_cancel(env.root, card_id, reason="test")
    path = env.root / "projects" / "high" / "project.json"
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "repo": REPO_URL}, indent=2))
    card_new(env.root, card_id="hi-a3", project="high", title="t", goal="g", test="t")
    card_path = env.root / "projects" / "high" / "cards" / "hi-a3.json"
    card = load_card(card_path)
    card.stack = Stack.DONE
    card.pr = f"{REPO_URL}/pull/1"
    save_card(card_path, card)
    fake = _FakeGitHub(_PR(1, "card/hi-a3", draft=False))
    real_run = subprocess.run

    def run(args, *rest, **kwargs):
        if args and args[0] == "gh":
            return fake(args)
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)

    assert env.wake() == 0
    assert fake.merged() == []
    assert not [line for line in env.lines if line.startswith("merge=")]
    assert "nothing to do: no project has ready or reclaimable cards" in env.lines
