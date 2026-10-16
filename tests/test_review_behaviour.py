"""Tests for the behaviour review (autonomouse.review.behaviour), over behaviour logs
written straight into a temp logs folder in the shape `autonomouse.behaviour.log` writes,
cards on a temp board, and the board's git history. The end-to-end tests over a derived
log are the last one here and one in test_behaviour_log.py."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autonomouse.behaviour import log as behaviour_log
from autonomouse.board.commands import (
    card_list,
    card_new,
    card_progress,
    card_show,
    project_new,
)
from autonomouse.board.runs import load_run, run_end, run_start, save_run
from autonomouse.board.schema import Decision, HistoryEntry
from autonomouse.board.store import save_card
from autonomouse.review.behaviour import (
    Log,
    build_review,
    hand_edits,
    load_logs,
    write_review,
)

NOW = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
A = "20260922-070000-aaaa"
B = "20260922-080000-bbbb"
C = "20260922-120000-cccc"
OLD = "20260920-120000-dddd"


def _summary(run_id: str, **fields) -> dict:
    summary = {
        "run_id": run_id,
        "session_id": f"session-{run_id[-4:]}",
        "project": "system",
        "derived_at": "2026-09-22T12:59:00Z",
        "reason": None,
        "agents": [],
        "board_writes": [],
        "transitions": [],
        "phases": [],
        "iterations": [],
        "decisions": [],
        "helpers_spawned_by_workers": [],
        "prs": [],
        "cost": {"total_usd": None, "by_model": {}},
        "jev": [],
        "clock": [],
    }
    summary.update(fields)
    return summary


def _agent(key, role, card, *, model="claude-sonnet-5", cost=None, idle=90.0):
    return {
        "agent": key,
        "role": role,
        "card": card,
        "model": model,
        "effort": "high",
        "cost_usd": cost,
        "longest_idle_s": idle,
        "longest_idle_from": "2026-09-22T07:01:00.000Z",
    }


def _log(folder, summary):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{summary['run_id']}.summary.json").write_text(json.dumps(summary))


def _jev(question, card, source, answer, applied=None):
    return {
        "question": question,
        "card": card,
        "source": source,
        "answer": answer,
        "confidence": 0.9 if source != "orchestrator" else None,
        "applied": applied,
    }


def _window(tmp_path):
    """Three runs in the window and one before it, and a file that is not a run's log."""
    logs = tmp_path / "logs"
    _log(
        logs,
        _summary(
            A,
            agents=[
                _agent("orchestrator", "orchestrator", None, model="claude-opus-5"),
                _agent("w1", "worker", "sys-x1"),
            ],
            prs=[{"card": "sys-x1", "url": "https://github.com/o/r/pull/1", "state": "merged"}],
            cost={"total_usd": 4.0, "by_model": {"claude-opus-5[1m]": 1.0, "claude-sonnet-5": 3.0}},
            jev=[
                _jev("fits", "sys-x1", "jev", "fits"),
                _jev("tier", "sys-x1", "fallback", "sonnet/high"),
            ],
            clock=[
                {"class": "out_of_order", "card": "sys-x3", "at": "2026-09-22T07:09:00Z", "run_id": A, "next_at": "2026-09-22T07:02:00Z", "next_run_id": B},
            ],
        ),
    )
    _log(
        logs,
        _summary(
            B,
            agents=[
                _agent("orchestrator", "orchestrator", None, model="claude-opus-5", cost=2.0),
                _agent("w2", "worker", "sys-x2", cost=1.5),
                _agent("h2", "helper", "sys-x2", model="claude-fable-5", idle=2400.0),
            ],
            cost={"total_usd": 3.5, "by_model": {"claude-opus-5[1m]": 2.0, "claude-sonnet-5": 1.5}},
            jev=[
                _jev("fits", "sys-x1", "orchestrator", "fits", "fits"),
                _jev("tier", "sys-x1", "orchestrator", "opus/high", "opus/high"),
            ],
            clock=[
                {"class": "out_of_order", "card": "sys-x3", "at": "2026-09-22T07:09:00Z", "run_id": A, "next_at": "2026-09-22T07:02:00Z", "next_run_id": B},
            ],
        ),
    )
    _log(
        logs,
        _summary(
            C,
            session_id=None,
            reason="no session started",
            jev=[_jev("tier", "sys-x1", "orchestrator", "sonnet/high", "sonnet/high")],
        ),
    )
    _log(logs, _summary(OLD, jev=[_jev("fits", "sys-x1", "orchestrator", "split")]))
    (logs / "notes.summary.json").write_text("not a run's log")
    return logs


def _decision(asked_at, question, answered_at=None, number=1):
    return Decision(
        f"d{number}",
        question,
        ["a", "other: tell me"],
        "a",
        asked_at,
        "a" if answered_at else None,
        answered_at,
    )


def _board(root, cards):
    """A board with project `system` and each card with the history rows
    `(at, run id, from, to)` or `(at, run id, from, to, note)` and the decisions `cards`
    gives it; the board's cards."""
    root.mkdir(parents=True, exist_ok=True)
    project_new(
        root,
        slug="system",
        name="System",
        goal="g",
        repo="https://github.com/t/t",
        repo_path=str(root),
        default_branch="main",
        priority=0,
    )
    for card_id, (history, decisions) in cards.items():
        card_new(root, card_id=card_id, project="system", title="t", goal="g", test="t")
        card = card_show(root, card_id)
        card.history = [HistoryEntry(*(*row, "x")[:5]) for row in history]
        card.decisions = list(decisions)
        save_card(root / "projects" / "system" / "cards" / f"{card_id}.json", card)
    return card_list(root)


def _window_board(tmp_path):
    """The board beside `_window`'s logs: sys-x1 done in A (a later row that keeps the
    stack is no move) after an attempt failed in ci, sys-x4 done and reopened in A,
    sys-x5 done before the window, and a decision on sys-x2 asked in A and answered on
    Telegram, and one on sys-x3 asked in B and still open."""
    at = "2026-09-22T{}:00Z".format
    return _board(
        tmp_path / "board",
        {
            "sys-x1": (
                [
                    (at("07:01"), A, "todo", "in_progress"),
                    (at("07:02"), A, "in_progress", "in_progress", "progress: phase=ci"),
                    (at("07:03"), A, "in_progress", "in_progress", "progress: phase=writing, iteration=1"),
                    (at("07:05"), A, "in_progress", "done"),
                    (at("07:08"), A, "done", "done"),
                ],
                [],
            ),
            "sys-x2": (
                [
                    (at("07:02"), A, "todo", "in_progress"),
                    (at("07:06"), A, "in_progress", "decision_needed"),
                    (at("07:30"), "telegram", "decision_needed", "todo"),
                ],
                [_decision(at("07:06"), "Which API token should the bot use?", at("07:30"))],
            ),
            "sys-x3": (
                [
                    (at("08:01"), B, "todo", "in_progress"),
                    (at("08:02"), B, "in_progress", "decision_needed"),
                ],
                [_decision(at("08:02"), "Which wording should the brief use?")],
            ),
            "sys-x4": (
                [
                    (at("07:03"), A, "in_progress", "done"),
                    (at("07:04"), A, "done", "todo"),
                ],
                [],
            ),
            "sys-x5": ([(at("06:00"), OLD, "in_progress", "done")], []),
        },
    )


def _review(tmp_path, folder, cards=(), edits=()):
    return build_review(
        load_logs(folder, NOW - timedelta(days=1)),
        cards=list(cards),
        edits=None if edits is None else list(edits),
        days=1,
        since=NOW - timedelta(days=1),
        now=NOW,
        folder=tmp_path,
    )


def _section(text: str, title: str) -> str:
    return text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


def test_the_window_holds_the_runs_that_started_in_it(tmp_path):
    logs = load_logs(_window(tmp_path), NOW - timedelta(days=1))
    assert [log.run_id for log in logs] == [A, B, C]
    text = _review(tmp_path, tmp_path / "logs")
    runs = _section(text, "Runs")
    assert f"| {A} | sys-x1 | system | session-aaaa | 1 orchestrator, 1 worker | - |" in runs
    assert f"| {C} | - | system | - | none | no session started |" in runs
    assert "Window: 3 runs, 2 with a session read; 1 no session started." in runs
    assert OLD not in text


def test_every_section_names_its_run_and_card(tmp_path):
    text = _review(tmp_path, _window(tmp_path), _window_board(tmp_path))

    done = _section(text, "Cards done")
    assert f"| {A} | sys-x1 | 2026-09-22T07:05:00Z |" in done
    assert "sys-x4" not in done  # reopened later in the same run
    assert "sys-x5" not in done  # done before the window
    assert "Window: 1 card done." in done

    decisions = _section(text, "Decisions")
    assert (
        f"| {A} | sys-x2 | d1 | credential | telegram | 2026-09-22T07:06:00Z "
        "| 2026-09-22T07:30:00Z |"
    ) in decisions
    assert f"| {B} | sys-x3 | d1 | judgment | nobody yet | 2026-09-22T08:02:00Z | - |" in decisions
    assert "Window: 2 decisions raised." in decisions
    assert f"| credential | 1 | {A} sys-x2 |" in decisions
    assert f"| telegram | 1 | {A} sys-x2 |" in decisions
    assert "Which API token" not in text  # the question's kind, never its words

    iterations = _section(text, "Iterations")
    assert f"| {A} | sys-x1 | 1 | check | 2026-09-22T07:03:00Z |" in iterations
    assert f"| check | 1 | {A} sys-x1 |" in iterations

    cost = _section(text, "Cost")
    assert f"| {A} | sys-x1 | $4.00 | claude-opus-5[1m] $1.00, claude-sonnet-5 $3.00 | $0.00 | $4.00 |" in cost
    assert f"| {B} | sys-x2 | $3.50 |" in cost and "| $3.50 | $0.00 |" in cost
    assert f"no cost recorded for {C}" in cost
    assert "derived before each agent was priced from its transcript" in cost
    assert f"Window: $7.50 over 2 runs with a cost; no cost recorded for {C}. By" in cost
    assert f"| sys-x1 | {A} | 1 | unknown | 1 |" in cost
    assert f"| sys-x2 | {B} | 2 | $1.50 | 1 |" in cost
    assert f"| opus/high | orchestrator | 2 | $2.00 | 1 | {A} -, {B} - |" in cost
    assert f"| fable/high | helper | 1 | unknown | 1 | {B} sys-x2 |" in cost

    waits = _section(text, "Waits")
    assert f"| {B} | sys-x2 | helper h2 | 40m00s | 2026-09-22T07:01:00.000Z |" in waits
    assert f"| worker | 2 | 1m30s | {A} sys-x1 worker w1 |" in waits
    assert f"| helper | 1 | 40m00s | {B} sys-x2 helper h2 |" in waits

    prs = _section(text, "PR state at finish")
    assert f"| {A} | sys-x1 | merged | https://github.com/o/r/pull/1 |" in prs
    assert f"| merged | 1 | {A} sys-x1 |" in prs


def test_rule_breaches_by_class(tmp_path):
    edits = [
        [A, "sys-x1", "0a1b2c3d", "2026-09-22T07:04:00Z", "history, phase"],
        [B, "sys-x2", "4e5f6a7b", "2026-09-22T08:03:00Z", "title"],
    ]
    text = _review(tmp_path, _window(tmp_path), edits=edits)
    breaches = _section(text, "Rule breaches")
    assert f"| hand edits of cards | 2 | {A} sys-x1, {B} sys-x2 |" in breaches
    # One out-of-order pair on two runs' logs is one time that cannot be true.
    assert (
        f"| times on the board that cannot be true | 1 | {A} sys-x3, {B} sys-x3 |"
        in breaches
    )
    assert (
        f"| {A} | sys-x1 | 0a1b2c3d | 2026-09-22T07:04:00Z | history, phase |"
        in breaches
    )
    assert f"| {B} | sys-x2 | 4e5f6a7b | 2026-09-22T08:03:00Z | title |" in breaches
    assert (
        f"| {A} | sys-x3 | out_of_order | sys-x3: history row 2026-09-22T07:09:00Z "
        f"(run {A}) comes before the earlier row 2026-09-22T07:02:00Z (run {B}) |"
    ) in breaches
    for dropped in (
        "file writes",
        "stack transitions by workers",
        "where a command exists",
    ):
        assert dropped not in text


def test_hand_edits_that_could_not_be_read_are_not_checked(tmp_path):
    breaches = _section(
        _review(tmp_path, _window(tmp_path), edits=None), "Rule breaches"
    )
    assert "| hand edits of cards | not checked | - |" in breaches
    assert "Not checked: the board's git history could not be read." in breaches


def _git(root, *args, at=None):
    env = {**os.environ, "GIT_COMMITTER_DATE": at or "", "GIT_AUTHOR_DATE": at or ""}
    if at is None:
        del env["GIT_COMMITTER_DATE"], env["GIT_AUTHOR_DATE"]
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(root, message, at):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message, at=f"2026-09-22T{at}:00+00:00")
    return _git(root, "rev-parse", "HEAD")[:8]


def _by_hand(root, card_id, change):
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    card = json.loads(path.read_text())
    change(card)
    path.write_text(json.dumps(card, indent=2) + "\n")


def test_hand_edits_are_card_changes_in_board_commits_that_no_command_recorded(
    tmp_path,
):
    """A command appends a history row to every card it writes and never rewrites one;
    a change to a card file in a board commit that does neither was made by hand. The
    run is the one the commit names, else the run of the card's project whose time holds
    the commit."""
    root = tmp_path / "board"
    _board(root, {"sys-x1": ([], []), "sys-x2": ([], [])})
    _by_hand(root, "sys-x1", lambda c: c.update(stack="in_progress"))  # before the seed
    _git(root, "init", "-q")
    _commit(root, "seed", "06:00")
    for run, start, end in ((A, "07:00", "07:59"), (B, "08:00", "08:59")):
        run_start(root, run_id=run, session_id="s", pid=1, model="opus")
        record = load_run(root, run)
        record.project = "system"
        record.started_at, record.ended_at = (
            f"2026-09-22T{start}:00Z",
            f"2026-09-22T{end}:00Z",
        )
        save_run(root, record)
    _commit(root, "runs", "06:30")  # no card changed

    card_progress(root, "sys-x1", phase="ci", run_id=A)
    _commit(root, f"run {A}: sys-x1 in ci", "07:10")  # a command's write: no row
    _by_hand(root, "sys-x1", lambda c: c.update(phase="review"))
    named = _commit(root, f"run {A}: the phase", "07:20")

    def rewrite(card):
        card["history"][0]["note"] = "created by hand"
        card["history"].append(dict(card["history"][-1], note="progress: phase=ci"))

    _by_hand(root, "sys-x2", lambda c: c.update(title="a better title"))
    by_time = _commit(root, "tidy", "08:10")  # names no run: B's time on system
    _by_hand(root, "sys-x1", rewrite)
    rewritten = _commit(root, "tidy", "08:20")
    _by_hand(root, "sys-x2", lambda c: c.update(goal="g2"))
    _commit(root, "late", "10:00")  # no run of the window holds it

    logs = [Log(run, NOW, {}) for run in (A, B)]
    assert hand_edits(root, logs, NOW - timedelta(days=1)) == [
        [A, "sys-x1", named, "2026-09-22T07:20:00Z", "phase"],
        [B, "sys-x2", by_time, "2026-09-22T08:10:00Z", "title"],
        [B, "sys-x1", rewritten, "2026-09-22T08:20:00Z", "history"],
    ]
    (tmp_path / "not-a-repo").mkdir()
    assert hand_edits(tmp_path / "not-a-repo", logs, NOW) is None


def test_jev_against_the_orchestrator(tmp_path):
    text = _review(tmp_path, _window(tmp_path))
    jev = _section(text, "Jev and the orchestrator")
    assert f"| {A} | sys-x1 | fits | jev | fits | 0.9 | - |" in jev
    assert f"| {A} | sys-x1 | fits | fits (jev) | fits | {B} | yes |" in jev
    # B's opus/high is nearer to A than C's sonnet/high.
    assert f"| {A} | sys-x1 | tier | sonnet/high (fallback) | opus/high | {B} | no |" in jev
    assert (
        "Window: 5 answers (fallback 1, jev 1, orchestrator 3); 2 by Jev, 2 compared with "
        "the orchestrator, 1 agreeing: agreement rate 50%."
    ) in jev
    assert "| fits | 1 | 1 | 1 | 100% |" in jev and "| tier | 1 | 1 | 0 | 0% |" in jev


def test_a_log_derived_before_jev_answers_and_board_times_says_so(tmp_path):
    logs = tmp_path / "logs"
    old = _summary(A)
    del old["jev"], old["clock"]
    _log(logs, old)
    text = _review(tmp_path, logs)
    note = "Not in the logs of 1 run, derived before the log carried Jev answers"
    assert note in _section(text, "Jev and the orchestrator")
    assert f"derives one again): {A}." in _section(text, "Rule breaches")


def test_a_log_whose_board_could_not_be_read_says_its_times_are_unchecked(tmp_path):
    logs = tmp_path / "logs"
    _log(logs, _summary(A, clock=None))
    text = _review(tmp_path, logs)
    assert (
        f"Not checked for 1 run, whose board could not be read when the log was "
        f"derived: {A}."
    ) in _section(text, "Rule breaches")


def test_cli_writes_the_review_from_the_logs_beside_the_board(tmp_path):
    board = tmp_path / "board"
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=2)).strftime("%Y%m%d-%H%M%S") + "-abcd"
    _log(tmp_path / "logs", _summary(recent))
    _board(board, {"sys-x1": ([("2026-09-22T07:05:00Z", recent, "in_progress", "done")], [])})
    env = {**os.environ, "AUTONOMOUSE_HOME": str(board)}

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", "review", "behaviour", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    done = cli("--since", "1")
    assert done.returncode == 0, done.stderr
    path = board / "reviews" / f"behaviour-{datetime.now(UTC).date().isoformat()}.md"
    assert done.stdout.strip() == str(path)
    assert f"| {recent} | sys-x1 | 2026-09-22T07:05:00Z |" in path.read_text()

    assert cli("--since", "0").returncode == 2
    assert cli().returncode == 2
    (board / "projects" / "system" / "cards" / "sys-x2.json").write_text("{not json\n")
    broken = cli("--since", "1")
    assert broken.returncode == 1 and "the board's cards cannot be read" in broken.stderr
    (tmp_path / "logs" / f"{recent}.summary.json").write_text("{not json\n")
    (board / "projects" / "system" / "cards" / "sys-x2.json").unlink()
    broken = cli("--since", "1")
    assert broken.returncode == 1 and f"behaviour log of run {recent}" in broken.stderr


def test_write_review_names_the_file_by_the_day_it_is_written(tmp_path):
    _window(tmp_path)
    path = write_review(tmp_path / "board", 1, now=NOW)
    assert path == tmp_path / "board" / "reviews" / "behaviour-2026-09-22.md"
    assert path.read_text().startswith("# Behaviour review — 2026-09-22\n")


def test_a_cost_the_review_cannot_price_names_the_model_without_a_rate(tmp_path):
    logs = tmp_path / "logs"
    _log(
        logs,
        _summary(
            A,
            agents=[
                _agent("orchestrator", "orchestrator", None, model="claude-opus-5", cost=1.0),
                _agent("w1", "worker", "sys-x1", model="claude-new-9"),
                # its own model has a rate: a message on the log's unpriced model is why
                _agent("h1", "helper", "sys-x1", model="claude-sonnet-5"),
            ],
            cost={"total_usd": 2.0, "by_model": {}, "unpriced_models": ["claude-new-9"]},
        ),
    )
    _log(
        logs,
        _summary(
            B,
            agents=[_agent("w2", "worker", "sys-x2", model="claude-opus-5-5")],
            cost={"total_usd": 1.0, "by_model": {}, "unpriced_models": ["claude-opus-5-5"]},
        ),
    )
    cost = _section(_review(tmp_path, logs), "Cost")
    assert f"| sys-x1 | {A} | 2 | unknown | 2, no rate for claude-new-9 |" in cost
    assert f"| sys-x2 | {B} | 1 | unknown | 1, no rate for claude-opus-5-5 |" in cost
    assert f"| new/high | worker | 1 | unknown | 1, no rate for claude-new-9 | {A} sys-x1 |" in cost
    assert f"| opus/high | orchestrator | 1 | $1.00 | 0 | {A} - |" in cost
    # claude-opus-5-5 has a rate now; claude-new-9 still has none.
    assert (
        "until the log is derived again (`autonomouse log derive <run>`): "
        f"{B} (claude-opus-5-5)."
    ) in cost


A3AD = json.loads(
    (
        Path(__file__).parent / "fixtures" / "behaviour" / "run-20260926-073030-a3ad-board.json"
    ).read_text()
)
A3AD_SESSION = "d74c36b5-0703-4a16-830a-4a0f469dc358"
A3AD_WORKER = "a96f51af6c66cb04b"


def _message(at, model, content, usage, *, sidechain, effort="high"):
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {"id": f"msg_{at}", "model": model, "usage": usage, "content": content},
        "effort": effort,
        "timestamp": f"2026-09-26T{at}.000Z",
        "cwd": "/home/t/repo",
    }


def _said(at, content, *, sidechain, meta=None):
    return {
        "type": "user",
        "isSidechain": sidechain,
        "message": {"role": "user", "content": content},
        "toolUseResult": meta,
        "timestamp": f"2026-09-26T{at}.000Z",
        "cwd": "/home/t/repo",
    }


def _result(tool_id, text):
    return [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]


def _a3ad_transcripts(projects, root, run):
    """A sample of the wake's session: the orchestrator on claude-fable-5-1 finishes
    sys-s4 through the shell function the wake used (which the log does not read as a
    command) and spawns sys-s9's worker, whose one message is on claude-opus-5-5 and
    costs $6.00 at its rates, $1.00 for each token class but output's $2.00."""
    folder = projects / "-home-t-repo"
    card_file = f"{root}/projects/system/cards/sys-s9.json"
    finish = (
        'A() { uv run -q --project /home/t/repo autonomouse "$@"; }; '
        f"A card finish sys-s4 --pr https://github.com/t/t/pull/140 --last-step x --run-id {run}"
    )
    spawn = {"description": "Worker for sys-s9", "subagent_type": "worker-xhigh", "prompt": f"Your card is `{card_file}`."}
    orchestrator = [
        _said("07:30:40", f"Read docs/orchestrator.md and run one wake. Your run id is {run}.", sidechain=False),
        _message("07:31:00", "claude-fable-5-1", [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": finish}}], {"output_tokens": 20_000}, sidechain=False),
        _said("07:31:05", _result("toolu_1", '{"id":"sys-s4","stack":"done"}'), sidechain=False),
        _message("07:32:00", "claude-fable-5-1", [{"type": "tool_use", "id": "toolu_2", "name": "Agent", "input": spawn}], {"output_tokens": 20_000}, sidechain=False),
        _said("07:32:01", _result("toolu_2", "Async agent launched successfully."), sidechain=False, meta={"agentId": A3AD_WORKER}),
    ]
    worker = [
        _said("07:32:02", f"Your card is `{card_file}`.", sidechain=True),
        _message(
            "07:33:00",
            "claude-opus-5-5",
            [{"type": "text", "text": "done"}],
            {
                "input_tokens": 250_000,  # $1.00 at $4/MTok
                "output_tokens": 100_000,  # $2.00 at $20/MTok
                "cache_read_input_tokens": 5_000_000,  # $1.00 at $0.20/MTok
                "cache_creation_input_tokens": 325_000,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 200_000,  # $1.00 at $5/MTok
                    "ephemeral_1h_input_tokens": 125_000,  # $1.00 at $8/MTok
                },
            },
            sidechain=True,
            effort="xhigh",
        ),
    ]
    subagents = folder / A3AD_SESSION / "subagents"
    subagents.mkdir(parents=True)
    for path, records in (
        (folder / f"{A3AD_SESSION}.jsonl", orchestrator),
        (subagents / f"agent-{A3AD_WORKER}.jsonl", worker),
    ):
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
    (subagents / f"agent-{A3AD_WORKER}.meta.json").write_text(
        json.dumps({"agentType": "worker-xhigh", "toolUseId": "toolu_2"})
    )


def test_the_a3ad_wake_counts_its_done_cards_and_decision_and_prices_opus_5_5(tmp_path):
    """sys-s41. The review run at 2026-09-26T11:28:16Z printed 'Cards done: None' and
    'Decisions: None' for run 20260926-073030-a3ad, which finished 28 cards and raised
    one decision (sys-s23), and 'unknown' for the cards of its claude-opus-5-5 workers.
    The board here is that run's card histories and decisions as the board held them
    then (tests/fixtures/behaviour/run-20260926-073030-a3ad-board.json); the transcripts
    are a sample, derived into the run's behaviour log before the review reads it."""
    run = A3AD["run_id"]
    root = tmp_path / "board"
    _board(
        root,
        {
            c["id"]: (
                c["history"],
                [
                    _decision(d["asked_at"], d["question"], d["answered_at"])
                    for d in c["decisions"]
                ],
            )
            for c in A3AD["cards"]
        },
    )
    run_start(root, run_id=run, session_id=A3AD_SESSION, pid=1, model="fable")
    record = load_run(root, run)
    record.project, record.cards = "system", ["sys-s4", "sys-s9"]
    save_run(root, record)
    run_end(root, run, result={"total_cost_usd": 8.0}, summary="s")
    projects = tmp_path / "claude" / "projects"
    _a3ad_transcripts(projects, root, run)
    behaviour_log.write(
        root,
        run,
        projects_dir=projects,
        pr_state=lambda url: "merged",
    )

    text = write_review(root, 1, now=datetime(2026, 9, 26, 11, 28, 16, tzinfo=UTC)).read_text()

    done = _section(text, "Cards done")
    assert "Window: 28 cards done." in done
    assert f"| {run} | sys-s23 | 2026-09-26T08:45:52Z |" in done

    decisions = _section(text, "Decisions")
    assert "Window: 1 decision raised." in decisions
    assert (
        f"| {run} | sys-s23 | d1 | other | manual | 2026-09-26T08:08:15Z "
        "| 2026-09-26T08:11:58Z |"
    ) in decisions

    cost = _section(text, "Cost")
    assert f"| sys-s9 | {run} | 1 | $6.00 | 0 |" in cost
    assert f"| opus/xhigh | worker | 1 | $6.00 | 0 | {run} sys-s9 |" in cost
    assert f"| fable/high | orchestrator | 1 | $2.00 | 0 | {run} - |" in cost
    assert f"| {run} | sys-s9 | $8.00 | claude-fable-5-1 $2.00, claude-opus-5-5 $6.00 | $8.00 | $0.00 |" in cost
    assert "| unknown |" not in cost and ", no rate for" not in cost
    assert pytest.approx(6.0) == next(
        a["cost_usd"]
        for a in behaviour_log.read_summary(root, run)["agents"]
        if a["model"] == "claude-opus-5-5"
    )
