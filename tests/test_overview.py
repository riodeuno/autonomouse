"""`autonomouse list` and `autonomouse show <slug>` print the board as plain text.

Each test runs the command as a subprocess with no terminal on any of its streams, on
a temporary board (`AUTONOMOUSE_HOME`). `show` reads the repository's open pull
requests through `gh pr list`; a fake `gh` first on PATH answers it and records every
call it gets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from autonomouse.board import commands
from autonomouse.board.runs import Run, RunKind, RunStatus, save_run
from autonomouse.board.schema import Claim, Decision, Stack
from autonomouse.board.store import load_card, load_project, save_card, save_project

REPO_ROOT = Path(__file__).resolve().parents[1]

ALPHA_GOAL = "Alpha builds the widget factory for the whole team."
QUESTION = "Which widget colour ships first?"
MATCHED_PR = "https://github.com/acme/widgets/pull/7"
OTHER_PR = "https://github.com/acme/widgets/pull/9"
STOPPED_RUN = "20260920-090000-beef"


def _card(
    root: Path,
    card_id: str,
    project: str,
    stack: Stack,
    *,
    pr: str | None = None,
    question: str | None = None,
    position: int = 0,
) -> None:
    commands.card_new(
        root,
        card_id=card_id,
        project=project,
        title=f"title of {card_id}",
        goal="goal",
        test="test",
        position=position,
    )
    path = root / "projects" / project / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = stack
    card.pr = pr
    if question is not None:
        card.decisions.append(
            Decision(
                id="d1",
                question=question,
                options=["red", "blue"],
                recommendation="red",
                asked_at="2026-09-25T10:00:00Z",
                answer=None,
                answered_at=None,
                brief="brief",
            )
        )
    save_card(path, card)


def _run_record(
    root: Path,
    run_id: str,
    project: str,
    summary: str,
    *,
    kind: RunKind = RunKind.WAKE,
) -> None:
    """A run record of `project` that started and ended at the time its id names."""
    started = f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}T{run_id[9:11]}:00:00Z"
    save_run(
        root,
        Run(
            id=run_id,
            project=project,
            session_id=None,
            pid=None,
            model="opus",
            started_at=started,
            ended_at=started,
            cards=[],
            jev=[],
            notify_failures=[],
            result=None,
            end_reason=None,
            status=RunStatus.STOPPED,
            summary=summary,
            kind=kind,
        ),
    )


def _board(root: Path) -> None:
    """Two projects. alpha has one card in every stack but two in in_progress, one of
    them carrying MATCHED_PR, and six wakes; beta has one todo card, one wake, and is
    claimed by a run that recorded its end and whose process is gone (stopped)."""
    commands.project_new(
        root,
        slug="alpha",
        name="Alpha Works",
        goal=ALPHA_GOAL,
        repo="https://github.com/acme/widgets",
        repo_path="/tmp/widgets",
        priority=1,
        default_branch="trunk",
    )
    commands.project_new(
        root,
        slug="beta",
        name="Beta Tools",
        goal="Beta goal.",
        repo="https://github.com/acme/tools",
        repo_path="/tmp/tools",
        default_branch="main",
        priority=2,
    )
    _card(root, "alp-a1", "alpha", Stack.DECISION_NEEDED, question=QUESTION)
    _card(root, "alp-a2", "alpha", Stack.IN_PROGRESS, pr=MATCHED_PR, position=1)
    _card(root, "alp-a3", "alpha", Stack.IN_PROGRESS, position=2)
    _card(root, "alp-a4", "alpha", Stack.TODO)
    _card(root, "alp-a5", "alpha", Stack.BACKLOG)
    _card(
        root, "alp-a6", "alpha", Stack.DONE, pr="https://github.com/acme/widgets/pull/3"
    )
    _card(root, "alp-a7", "alpha", Stack.CANCELLED)
    _card(root, "bet-a1", "beta", Stack.TODO)

    for day in range(1, 7):  # six wakes of alpha, the newest on the 26th
        _run_record(
            root, f"202609{20 + day}-080000-000{day}", "alpha", f"alpha wake {day}"
        )
    _run_record(root, STOPPED_RUN, "beta", "beta wake")

    path = root / "projects" / "beta" / "project.json"
    beta = load_project(path)
    beta.claim = Claim(
        run_id=STOPPED_RUN,
        session_id=None,
        pid=None,
        model="opus",
        claimed_at="2026-09-20T09:00:00Z",
    )
    save_project(path, beta)


def _fake_gh(tmp_path: Path, *, fail: bool) -> tuple[Path, Path]:
    """A `gh` that appends its arguments to a log and prints two open pull requests,
    or, with `fail`, prints a reason on stderr and exits 1. Returns its folder and log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh-calls.log"
    payload = tmp_path / "prs.json"
    payload.write_text(
        json.dumps(
            [
                {"url": MATCHED_PR, "title": "alp-a2: the matched pull request"},
                {"url": OTHER_PR, "title": "a pull request no card owns"},
            ]
        )
    )
    body = (
        'echo "HTTP 401: Bad credentials" >&2\nexit 1\n'
        if fail
        else f'cat "{payload}"\n'
    )
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n{body}')
    gh.chmod(0o755)
    return bin_dir, log


def _autonomouse(*args: str, root: Path, bin_dir: Path | None = None):
    """Run the command the way ssh runs it: no terminal on stdin, stdout or stderr."""
    env = dict(os.environ, AUTONOMOUSE_HOME=str(root))
    if bin_dir is not None:
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


def _card_sections(stdout: str) -> dict[str, list[str]]:
    """The card lines under each stack heading, and the headings in the order printed."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in stdout.splitlines():
        heading = line.split(" (")[0]
        if heading in {s.value for s in Stack} and not line.startswith(" "):
            current = heading
            sections[current] = []
        elif current is not None and line.startswith("  "):
            sections[current].append(line)
    return sections


def test_list_prints_each_project_with_its_counts_and_claim(tmp_path: Path) -> None:
    root = tmp_path / "board"
    _board(root)

    result = _autonomouse("list", root=root)

    assert result.returncode == 0, result.stderr
    assert "\x1b" not in result.stdout  # plain text: no terminal escape codes
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    alpha, beta = lines  # lowest priority first
    for field in (
        "alpha",
        "Alpha Works",
        "decision_needed=1",
        "in_progress=2",
        "todo=1",
        "backlog=1",
        "done=1",
        "cancelled=1",
        "claim=free",
    ):
        assert field in alpha, (field, alpha)
    for field in (
        "beta",
        "Beta Tools",
        "decision_needed=0",
        "in_progress=0",
        "todo=1",
        "backlog=0",
        "done=0",
        "cancelled=0",
        f"claim={STOPPED_RUN} (stopped)",
    ):
        assert field in beta, (field, beta)


def test_show_prints_the_details_and_the_cards_by_stack(tmp_path: Path) -> None:
    root = tmp_path / "board"
    _board(root)
    bin_dir, log = _fake_gh(tmp_path, fail=False)
    before = _snapshot(root)

    result = _autonomouse("show", "alpha", root=root, bin_dir=bin_dir)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "\x1b" not in out
    details = out.split("\n\n")[0]
    for line in (
        f"goal: {ALPHA_GOAL}",
        "repo: https://github.com/acme/widgets",
        "default_branch: trunk",
        "priority: 1",
        "claim: free",
        "cards: decision_needed=1 in_progress=2 todo=1 backlog=1 done=1 cancelled=1",
        "open pull requests: 2",
    ):
        assert line in details.splitlines(), (line, details)
    pr_lines = [line for line in details.splitlines() if "/pull/" in line]
    assert any(MATCHED_PR in line and "alp-a2" in line for line in pr_lines), pr_lines
    assert any(OTHER_PR in line and "no card" in line for line in pr_lines), pr_lines

    # The last five wakes, newest first: the oldest of alpha's six and beta's are left out.
    wakes = [line for line in details.splitlines() if "alpha wake" in line]
    assert [w.split("alpha wake ")[1] for w in wakes] == ["6", "5", "4", "3", "2"]
    assert "beta wake" not in out

    sections = _card_sections(out)
    assert list(sections) == [
        "decision_needed",
        "in_progress",
        "todo",
        "backlog",
        "done",
        "cancelled",
    ]
    assert len(sections["decision_needed"]) == 1
    assert "alp-a1" in sections["decision_needed"][0]
    assert f"question: {QUESTION}" in sections["decision_needed"][0]
    assert [line.split()[0] for line in sections["in_progress"]] == ["alp-a2", "alp-a3"]
    assert sections["in_progress"][0].split()[1] == "s"  # the size
    assert "title of alp-a2" in sections["in_progress"][0]
    assert sections["in_progress"][0].endswith(MATCHED_PR)
    assert sections["done"][0].endswith("https://github.com/acme/widgets/pull/3")
    assert "bet-a1" not in out

    # Read-only: the board is byte for byte the same, and gh was called once, to list
    # the repository's open pull requests.
    assert _snapshot(root) == before
    calls = log.read_text().splitlines()
    assert len(calls) == 1
    assert calls[0].startswith("pr list --repo acme/widgets --state open"), calls


def test_show_lists_only_wake_records_among_last_wakes(tmp_path: Path) -> None:
    """A planner session's run record (sys-b17) carries the same project as a wake's
    run record, but it is not a wake: it must not appear among the last wakes, and must
    not push a real wake out of the five `show` prints."""
    root = tmp_path / "board"
    commands.project_new(
        root,
        slug="gamma",
        name="Gamma Co",
        goal="Gamma goal.",
        repo="https://github.com/acme/gamma",
        repo_path="/tmp/gamma",
        default_branch="main",
        priority=1,
    )
    _run_record(root, "20260926-080000-0001", "gamma", "gamma wake")
    _run_record(
        root, "20260926-090000-0002", "gamma", "gamma planner", kind=RunKind.PLANNER
    )
    bin_dir, _ = _fake_gh(tmp_path, fail=False)

    result = _autonomouse("show", "gamma", root=root, bin_dir=bin_dir)

    assert result.returncode == 0, result.stderr
    wake_lines = [line for line in result.stdout.splitlines() if "started" in line]
    assert len(wake_lines) == 1, wake_lines
    assert "gamma wake" in wake_lines[0]
    assert "gamma planner" not in result.stdout


def test_show_when_gh_fails_says_the_pull_requests_are_unknown(tmp_path: Path) -> None:
    root = tmp_path / "board"
    _board(root)
    bin_dir, _ = _fake_gh(tmp_path, fail=True)

    result = _autonomouse("show", "alpha", root=root, bin_dir=bin_dir)

    assert result.returncode == 0, result.stderr
    pr_line = [
        line for line in result.stdout.splitlines() if "open pull requests" in line
    ]
    assert len(pr_line) == 1
    assert pr_line[0].startswith("open pull requests: unknown")
    assert "Bad credentials" in pr_line[0]
    sections = _card_sections(result.stdout)
    assert [line.split()[0] for line in sections["in_progress"]] == ["alp-a2", "alp-a3"]
    assert f"question: {QUESTION}" in sections["decision_needed"][0]


def test_show_an_unknown_slug_exits_non_zero_naming_it(tmp_path: Path) -> None:
    root = tmp_path / "board"
    _board(root)

    result = _autonomouse("show", "gamma", root=root)

    assert result.returncode != 0
    assert "gamma" in result.stderr
