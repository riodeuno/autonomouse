"""Tests for `autonomouse run` (autonomouse.controller) with a fake claude and a temp board.

The held-project tests (sys-s7): a wake on a project a live run holds waits for it — a run
record with `waiting_for`, no claim, the Situation at the end of its prompt — unless the
project has no ready card, when it still exits 3."""

import contextlib
import json
import os
import pickle
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autonomouse import browser, controller, github, session_end_hook
from autonomouse.board.brief import load_brief
from autonomouse.board.commands import (
    CommandError,
    card_cancel,
    card_finish,
    card_move,
    card_new,
    card_show,
    project_edit,
    project_new,
    project_show,
)
from autonomouse.board.runs import (
    card_start,
    load_run,
    pid_exists,
    project_claim,
    project_release,
    run_end,
    run_start,
    run_status,
    save_run,
)
from autonomouse.board.schema import Assigned, Stack
from autonomouse.board.store import GitCommitError, commit_board, load_card, save_card
from autonomouse.home import Home

REPO_ROOT = Path(__file__).resolve().parents[1]

FAKE_CLAUDE = """#!/usr/bin/env bash
# First adds its own pid to $FAKE_DIR/pids, which the `env` fixture checks at the end of
# every test: no stub claude outlives the test that started it (sys-w12).
echo "$$" >> "$FAKE_DIR/pids"
# Records its arguments (in args, and in args.<n> per call), one secret's value, the
# background-wait-ceiling env var and the retry watchdog the controller sets. Like claude
# when it finds no such session, `--resume <id>` fails when $FAKE_RESUME_FAILS is set.
# Appends an assistant line using 1000 tokens to its session's transcript, so the liveness
# rule sees it alive and a later wake counts its tokens, runs $FAKE_ON_NEW or $FAKE_ON_RESUME
# (as the orchestrator would; $sid and $run_id are set) -- a trailing `&` there stands for a
# worker the Agent tool ran in the background, still going after the fake orchestrator's own
# turn -- then waits for every such background job (real claude, given the ceiling env var
# below, waits for its background workers the same way instead of giving up on them), waits
# for $FAKE_WAIT when set, and prints a claude -p result: $FAKE_DIR/result.<n> for call n
# when that file exists, and exits with $FAKE_DIR/exit.<n> when that exists.
printf '%s\\n' "$@" > "$FAKE_DIR/args"
n=$(ls "$FAKE_DIR" | grep -c '^args\\.'); cp "$FAKE_DIR/args" "$FAKE_DIR/args.$n"
printf '%s\\n' "${TEST_SECRET_KEY:-unset}" > "$FAKE_DIR/env"
printf '%s\\n' "${AUTONOMOUSE_RUN_ID:-unset}" > "$FAKE_DIR/run_id_env"
printf '%s\\n' "${CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS:-unset}" > "$FAKE_DIR/env-ceiling"
printf '%s\\n' "${CLAUDE_CODE_RETRY_WATCHDOG:-unset}" > "$FAKE_DIR/watchdog"
prev=""; resume=""
for a in "$@"; do
  [ "$prev" = "-p" ] && prompt="$a"
  [ "$prev" = "--session-id" ] && sid="$a"
  [ "$prev" = "--resume" ] && sid="$a" && resume=1
  prev="$a"
done
if [ -n "$resume" ] && [ -n "${FAKE_RESUME_FAILS:-}" ]; then
  echo "No conversation found with session ID: $sid" >&2
  exit 1
fi
run_id=$(printf '%s' "$prompt" | grep -o 'Your run id is [a-z ]*[0-9]\\{8\\}-[0-9]\\{6\\}-[0-9a-f]\\{4\\}' | awk '{print $NF}')
mkdir -p "$CLAUDE_CONFIG_DIR/projects/-fake"
echo '{"type":"assistant","message":{"usage":{"input_tokens":900,"output_tokens":100}}}' \\
  >> "$CLAUDE_CONFIG_DIR/projects/-fake/$sid.jsonl"
if [ -n "$resume" ]; then eval "${FAKE_ON_RESUME:-}"; else eval "${FAKE_ON_NEW:-}"; fi
wait
while [ -n "${FAKE_WAIT:-}" ] && [ ! -e "$FAKE_WAIT" ]; do sleep 0.05; done
if [ -e "$FAKE_DIR/result.$n" ]; then cat "$FAKE_DIR/result.$n"
else [ -n "${FAKE_NO_JSON:-}" ] || echo '{"type":"result","is_error":false,"total_cost_usd":0.01}'
fi
if [ -e "$FAKE_DIR/exit.$n" ]; then exit "$(cat "$FAKE_DIR/exit.$n")"; fi
exit "${FAKE_EXIT:-0}"
"""


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def _seed_repo(tmp_path: Path, name: str) -> Path:
    """A real repo with `main` pushed to a bare `origin` — a project's `repo_path`,
    the shape `card start`'s real worktree creation needs (`git fetch origin`, then
    `git worktree add ... origin/main`)."""
    origin = tmp_path / f"{name}-origin.git"
    _git(["init", "-q", "--bare", "-b", "main", str(origin)], tmp_path)
    checkout = tmp_path / f"{name}-project"
    _git(["clone", "-q", str(origin), str(checkout)], tmp_path)
    _git(["config", "user.email", "t@t"], checkout)
    _git(["config", "user.name", "t"], checkout)
    (checkout / "README.md").write_text(f"{name}\n")
    _git(["add", "-A"], checkout)
    _git(["commit", "-q", "-m", "seed"], checkout)
    _git(["push", "-q", "origin", "main"], checkout)
    return checkout


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A board (git repo) with project `low` (priority 1, nothing ready) and `high`
    (priority 2, ready cards hi-a1 and hi-a2), a fake claude,
    a secrets file, and a repo folder holding docs/orchestrator.md."""
    root = tmp_path / "board"
    root.mkdir()
    _git(["init", "-q", "-b", "main"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    for slug, priority in (("low", 1), ("high", 2)):
        project_new(
            root,
            slug=slug,
            name=slug,
            goal="g",
            repo="https://github.com/t/t",
            repo_path=str(_seed_repo(tmp_path, slug)),
            default_branch="main",
            priority=priority,
        )
    # hi-a2 first: `card new` refuses a dependency that names no card yet.
    for card_id, project, position, deps in (
        ("hi-a1", "high", 0, []),
        ("hi-a2", "high", 1, []),
        ("low-a1", "low", 0, ["hi-a2"]),
    ):
        card_new(
            root,
            card_id=card_id,
            project=project,
            title="t",
            goal="g",
            test="t",
            position=position,
            depends_on=deps,
        )
        card_move(root, card_id, to="todo")
    commit_board(root, "seed")

    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    claude = fake_dir / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    secrets = tmp_path / ".env"
    secrets.write_text("TEST_SECRET_KEY=sk-test-123\n")
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "orchestrator.md").write_text("# orchestrator\n")

    monkeypatch.setenv("FAKE_DIR", str(fake_dir))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    # A failed send is recorded, but a send that works would, without these two
    # lines, read this machine's own ~/.config/autonomouse/config.toml and
    # ambient TELEGRAM_BOT_TOKEN/SLACK_WEBHOOK_URL — sending a real message to a real
    # chat from a test run. Point config at an empty, non-existent directory (loads as
    # defaults: no chat id) and drop any real credential from this process's environment,
    # so notify() always fails closed here, the same way `card_ask`'s tests never send.
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "no-such-config-dir"))
    # The wake's report (sys-w23) counts the open pull requests on the project's repository
    # with `gh pr list`; no test reaches GitHub, so the fixture's repository has none.
    monkeypatch.setattr(controller, "_open_prs", lambda repo: [])
    # so a `card start` run through the fake claude's embedded CLI calls creates its
    # working folder under here, never near the real ~/.local/share/autonomouse.
    monkeypatch.setenv("AUTONOMOUSE_WORKTREES", str(tmp_path / "worktrees"))
    # so every wake's log (sys-s60) goes here, never into the real
    # ~/projects/autonomouse/wake-logs.
    monkeypatch.setenv("AUTONOMOUSE_WAKE_LOGS", str(tmp_path / "wake-logs"))
    # The Situation reads the crontab for the wake's line (sys-u9): a fake `crontab` whose
    # `-l` prints one, first on PATH, so no test reads this machine's real crontab.
    cron_bin = tmp_path / "cron-bin"
    cron_bin.mkdir()
    (cron_bin / "crontab").write_text(
        "#!/bin/sh\necho '0 */4 * * * autonomouse run # autonomouse wake'\n"
    )
    (cron_bin / "crontab").chmod(0o755)
    monkeypatch.setenv("PATH", f"{cron_bin}{os.pathsep}{os.environ['PATH']}")
    for name in (
        "FAKE_WAIT",
        "FAKE_NO_JSON",
        "FAKE_EXIT",
        "FAKE_ON_NEW",
        "FAKE_ON_RESUME",
        "FAKE_RESUME_FAILS",
        "TEST_SECRET_KEY",
        "TELEGRAM_BOT_TOKEN",
        "SLACK_WEBHOOK_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    class Env:
        pass

    e = Env()
    e.root, e.fake_dir, e.tmp = root, fake_dir, tmp_path
    e.lines = []

    def kwargs_for(**overrides):
        kwargs = {
            "model": "testmodel",
            "effort": "xhigh",
            "workers_per_wake": 2,
            "pause": tmp_path / "config" / "PAUSE",
            "state_dir": tmp_path / "state",
            "secrets": secrets,
            "claude": str(claude),
            "home": Home(repo, "autonomouse-cli"),
            "out": e.lines.append,
        }
        kwargs.update(overrides)
        return kwargs

    e.kwargs_for = kwargs_for
    e.wake = lambda **overrides: controller.wake(root, **kwargs_for(**overrides))
    yield e

    # sys-w12: every stub claude this test started has ended. A stub the test itself
    # killed may take a moment to be reaped by its new parent, so wait a little; one still
    # alive after that is a leak: kill its process group (its own, from `_start`) by the
    # pid the stub recorded, so it does not outlive the test either, then fail.
    pids_file = fake_dir / "pids"
    pids = [int(p) for p in pids_file.read_text().split()] if pids_file.exists() else []
    deadline = time.time() + 10
    while (alive := [p for p in pids if pid_exists(p)]) and time.time() < deadline:
        time.sleep(0.05)
    for pid in alive:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
    assert not alive, f"stub claude still running after the test: pids {alive}"


def _value(lines: list[str], key: str) -> str:
    return next(line.split("=", 1)[1] for line in lines if line.startswith(f"{key}="))


def test_pause_starts_nothing(env):
    pause = env.tmp / "config" / "PAUSE"
    pause.parent.mkdir()
    pause.touch()
    assert env.wake() == controller.EXIT_NOT_STARTED
    assert env.lines[0].startswith("paused:")
    assert not (env.fake_dir / "args").exists()
    # sys-j6: the kill switch's refusal is a run record with no session whose `refusals`
    # names the PAUSE file, committed to the board.
    (path,) = (env.root / "runs").glob("*.json")
    run = load_run(env.root, path.stem)
    assert run.session_id is None and run.project is None and run.cards == []
    assert run.status.value == "stopped"
    assert run.summary == f"refused: PAUSE - {pause} exists"
    assert [(r["command"], r["reason"]) for r in run.refusals] == [
        ("PAUSE", f"{pause} exists")
    ]
    assert (run.model, run.effort) == ("testmodel", "xhigh")
    assert env.lines[1] == f"refusal_record={path} (refused: PAUSE - {pause} exists)"
    assert not _git(["status", "--porcelain"], env.root)  # committed
    assert _git(["log", "-1", "--format=%s"], env.root).strip() == (
        f"run {run.id}: refused: PAUSE - {pause} exists"
    )


def test_a_pause_whose_record_cannot_be_written_still_starts_nothing(env, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("read-only board")

    monkeypatch.setattr(controller, "run_refused", broken)
    pause = env.tmp / "config" / "PAUSE"
    pause.parent.mkdir()
    pause.touch()
    assert env.wake() == controller.EXIT_NOT_STARTED
    assert env.lines[1] == "refusal_record=failed: OSError: read-only board"
    assert not (env.fake_dir / "args").exists()


def test_nothing_ready_starts_nothing(env):
    for card_id in ("hi-a1", "hi-a2", "low-a1"):
        card_cancel(env.root, card_id, reason="test")
    assert env.wake() == 0
    assert env.lines == ["nothing to do: no project has ready or reclaimable cards"]
    assert not (env.fake_dir / "args").exists()


def test_a_wake_end_to_end(env):
    assert env.wake() == 0
    run_id = _value(env.lines, "run_id")
    args = (env.fake_dir / "args").read_text().splitlines()

    # The prompt: charter, run id, the higher-priority project, both its ready cards
    # (width is ceilings.workers_per_wake, 2 here, until Jev is consulted).
    prompt = _prompt(args)
    assert "docs/orchestrator.md" in prompt and f"run id is {run_id}" in prompt
    assert "project high" in prompt
    assert "Start these ready cards: hi-a1, hi-a2." in prompt
    assert "Resume these in_progress cards: none." in prompt
    # It ends with the Situation (sys-s7): built before the claim, so a free project reads
    # as not held, and the ready cards are the pick's.
    block = prompt[prompt.index("\n\nSituation:\n") + 2:]
    assert prompt.endswith(block)
    assert block.startswith("Situation:\nProject high is not held.\nWaiting wake: none.\n")
    assert "\nUsage limit: none in force.\nReady cards: hi-a1, hi-a2." in block
    assert args[args.index("--model") + 1] == "testmodel"
    assert "--dangerously-skip-permissions" in args
    session_id = args[args.index("--session-id") + 1]

    # The secret reached the session's environment and never its command line.
    assert (env.fake_dir / "env").read_text().strip() == "sk-test-123"
    assert not any("sk-test" in a for a in args)

    # AUTONOMOUSE_RUN_ID reached the session's environment too — the SessionEnd hook
    # (sys-d5) reads it to know which run record its end reason belongs to.
    assert (env.fake_dir / "run_id_env").read_text().strip() == run_id
    # ... and the hook itself is registered on the session with --settings (sys-f11), so
    # it runs from a wheel install as well as from a checkout.
    assert args[args.index("--settings") + 1] == session_end_hook.settings()

    # The card's test: the orchestrator session never gets the 600s background-task
    # ceiling that killed a worker mid-test (sys-d2, run 20260922-101216-c18a) -- the
    # run controller starts it with no ceiling at all.
    assert (env.fake_dir / "env-ceiling").read_text().strip() == "0"

    # The run record is complete.
    run = load_run(env.root, run_id)
    assert run.status.value == "stopped"
    assert run.session_id == session_id and run.pid == int(_value(env.lines, "pid"))
    assert run.project == "high" and run.cards == ["hi-a1", "hi-a2"]
    assert run.result == {"type": "result", "is_error": False, "total_cost_usd": 0.01}
    assert run.ended_at is not None and run.summary is not None

    # The claim is released and the board committed.
    assert project_show(env.root, "high").claim.run_id is None
    log = _git(["log", "--format=%s"], env.root)
    assert f"run {run_id}: started; claimed high; cards hi-a1, hi-a2" in log
    assert f"run {run_id}: ended (exit 0); result recorded; claim released" in log
    assert _git(["status", "--porcelain"], env.root) == ""


def test_a_launch_writes_the_wake_log_and_names_it_in_the_prompt(env, monkeypatch):
    """The card's test (sys-s60): with HOME at a temp folder and AUTONOMOUSE_WAKE_LOGS
    unset, the launch creates HOME/projects/autonomouse/wake-logs/<run id>.md before the
    orchestrator runs, with a header naming the run id, the project, the ready cards and
    the launch time, and the orchestrator's prompt names that path."""
    home = env.tmp / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AUTONOMOUSE_WAKE_LOGS")
    # The fake orchestrator copies the log as it finds it when it starts.
    monkeypatch.setenv(
        "FAKE_ON_NEW",
        'cp "$HOME/projects/autonomouse/wake-logs/$run_id.md" "$FAKE_DIR/log-at-start"',
    )
    before = datetime.now(UTC).replace(microsecond=0)

    assert env.wake() == 0

    after = datetime.now(UTC)
    run_id = _value(env.lines, "run_id")
    wake_log = home / "projects" / "autonomouse" / "wake-logs" / f"{run_id}.md"
    header = wake_log.read_text()
    assert (env.fake_dir / "log-at-start").read_text() == header
    head, launched = header.rsplit("- Launched: ", 1)
    assert head == (
        f"# Wake log of run {run_id}\n\n"
        f"- Run id: {run_id}\n"
        "- Project: high\n"
        "- Ready cards: hi-a1, hi-a2\n"
    )
    assert launched.endswith("Z\n")
    at = datetime.strptime(launched.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert before <= at <= after

    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert f" Your wake log is {wake_log}: append to it as the wake goes." in prompt
    assert _value(env.lines, "wake_log") == str(wake_log)


# A brief in the format of docs/board-schema.md, "brief.md", its goal sentence in quotes as
# the system project's real brief writes it.
BRIEF = """# high — project brief

## Why this project exists

High exists so that a wake's prompt has a brief to carry.

## Goal

"Every wake on high carries this goal sentence to its workers."

## Intent

The creator wants every agent on high to know why it exists.

This second paragraph of the intent stays in the full brief only.

## Who uses it and where

The tests, on this machine.

## How it works

A fake claude stands in for the orchestrator.

## How we work

One card at a time.

## Out of scope

Everything else.
"""


def test_a_launch_puts_the_projects_brief_in_the_orchestrator_prompt(env):
    """The card's test (sys-t6): a launch on a project with a brief puts the whole brief,
    its Why section among it, in the orchestrator's prompt under the heading "The
    project's brief", before the Situation that still ends the prompt."""
    (env.root / "projects" / "high" / "brief.md").write_text(BRIEF)
    commit_board(env.root, "high's brief")

    assert env.wake() == 0

    prompt = _prompt(_args(env, 0))
    start = prompt.index("Start these ready cards: hi-a1, hi-a2.\n\n# The project's brief\n\n")
    situation = prompt.index("\n\nSituation:\n")
    block = prompt[start:situation]
    assert (
        "## Why this project exists\n\n"
        "High exists so that a wake's prompt has a brief to carry.\n"
    ) in block
    assert block.endswith(f"\n\n<brief>\n{BRIEF.strip()}\n</brief>")
    assert prompt.endswith("\nReady cards: hi-a1, hi-a2.")


def test_a_workers_spawn_template_carries_the_goal_and_intent_sentences(env):
    """The card's test (sys-t6): the spawn template (docs/orchestrator.md, "A wake", step
    5) follows the card's goal with the goal line and intent line that the orchestrator's
    prompt gives under "The project's brief"; rendered for a card of high, they carry
    high's goal sentence as the brief writes it, quotes and all, and its intent sentence,
    and no more of the brief. A project with no brief gets no brief block."""
    template = (REPO_ROOT / "docs" / "orchestrator.md").read_text()
    step5 = template[
        template.index("5. **Spawn one worker per card**") : template.index(
            "6. **On each report**"
        )
    ]
    assert (
        "followed by the project's goal line and intent line as your start message gives "
        "them under \"The project's brief\""
    ) in " ".join(step5.split())

    (env.root / "projects" / "high" / "brief.md").write_text(BRIEF)
    card = card_show(env.root, "hi-a1")
    rendered = controller.brief_prompt(load_brief(env.root, card.project))
    lines = rendered.split('as written (your charter, "A wake", step 5):\n', 1)[1]
    assert lines.split("\n\n<brief>\n", 1)[0] == (
        "The project's goal: "
        '"Every wake on high carries this goal sentence to its workers."\n'
        "The project's intent: The creator wants every agent on high to know why it exists."
    )
    assert controller.brief_prompt(load_brief(env.root, "low")) == ""


def test_the_session_runs_only_once_its_start_is_committed(env, monkeypatch):
    """claude is started first, for its pid, and held until its run record and claim are
    committed; a session that ran at once could `card start` before its project was
    claimed and be refused (sys-i10). The pause before the commit gives a session that
    was not held the time to run, so this fails every time the hold is missing."""
    ran_before_commit = []
    real_commit = controller.commit_board

    def slow_commit(root, message):
        if ": started;" in message:
            time.sleep(0.3)
            ran_before_commit.append((env.fake_dir / "args").exists())
        return real_commit(root, message)

    monkeypatch.setattr(controller, "commit_board", slow_commit)
    monkeypatch.setenv(
        "FAKE_ON_NEW", f'git -C "{env.root}" log -1 --format=%s > "$FAKE_DIR/first-seen"'
    )

    assert env.wake() == 0

    run_id = _value(env.lines, "run_id")
    assert ran_before_commit == [False]
    assert (env.fake_dir / "first-seen").read_text().strip() == (
        f"run {run_id}: started; claimed high; cards hi-a1, hi-a2"
    )


def test_a_start_that_cannot_claim_never_runs_claude(env, monkeypatch):
    def refuse(*args, **kwargs):
        time.sleep(0.3)  # time for a session that was not held to run
        raise CommandError("refused")

    monkeypatch.setattr(controller, "project_claim", refuse)

    with pytest.raises(CommandError, match="refused"):
        env.wake()

    assert not (env.fake_dir / "args").exists()
    # sys-j6: the refused claim is on the wake's run record.
    (path,) = (env.root / "runs").glob("*.json")
    run = load_run(env.root, path.stem)
    assert [(r["command"], r["reason"]) for r in run.refusals] == [("project claim", "refused")]
    assert "refusal=project claim: refused" in env.lines


def _budget_result(env, **fields) -> None:
    """Make fake claude's first session end the way `--max-budget-usd` ends one."""
    (env.fake_dir / "result.0").write_text(json.dumps({
        "type": "result", "subtype": "error_max_budget_usd", "is_error": True,
        "total_cost_usd": 5.02, **fields,
    }))


def test_a_session_that_ends_on_its_budget_records_a_refusal_naming_it(env):
    _budget_result(
        env, terminal_reason="budget_exhausted", errors=["Reached maximum budget ($5)"]
    )
    env.wake(max_budget_usd=5.0)
    run = load_run(env.root, _value(env.lines, "run_id"))
    reason = "the session ended on --max-budget-usd: Reached maximum budget ($5)"
    assert [(r["command"], r["reason"]) for r in run.refusals] == [
        ("claude --max-budget-usd", reason)
    ]
    assert f"refusal=claude --max-budget-usd: {reason}" in env.lines


def test_a_budget_stop_whose_result_names_no_budget_says_what_it_holds(env):
    _budget_result(env, errors=[])
    env.wake(max_budget_usd=5.0)
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert [r["reason"] for r in run.refusals] == [(
        "the session ended on --max-budget-usd; its result names no budget: "
        "subtype 'error_max_budget_usd', errors [], total_cost_usd 5.02"
    )]


def test_a_session_that_ends_otherwise_records_no_refusal(env):
    assert env.wake() == 0
    assert load_run(env.root, _value(env.lines, "run_id")).refusals == []


def _wait_for_waiting_run(root: Path, slug: str) -> str:
    """The id of the run record that says `waiting_for` `slug`, once it is written."""
    deadline = time.time() + 10
    while True:
        for path in sorted((root / "runs").glob("*.json")) if (root / "runs").is_dir() else []:
            if json.loads(path.read_text()).get("waiting_for") == slug:
                return path.stem
        assert time.time() < deadline, f"no run waits for {slug}"
        time.sleep(0.05)


def test_a_wake_on_a_held_project_with_ready_cards_waits_for_it(env, monkeypatch):
    """sys-s7: `high` is held by a live run and its cards hi-a1, hi-a2 are ready (the
    fake orchestrator starts none). A second wake no longer exits 3: it starts a session
    whose prompt ends with the Situation, writes a run record with `waiting_for` and takes
    no claim, and exits with the session's own code. That run's `project claim` is refused
    while the holder is alive; once the holder released, it sets the claim and clears
    `waiting_for`."""
    go_first = env.tmp / "go-first"
    monkeypatch.setenv("FAKE_WAIT", str(go_first))
    first_codes, second_codes, second_lines = [], [], []
    first = threading.Thread(target=lambda: first_codes.append(env.wake()))
    first.start()
    try:
        holder = _wait_for_claim(env.root, "high")
        go_second = env.tmp / "go-second"
        monkeypatch.setenv("FAKE_WAIT", str(go_second))
        (env.fake_dir / "exit.1").write_text("7")  # the waiting session's own exit code
        second = threading.Thread(
            target=lambda: second_codes.append(env.wake(out=second_lines.append))
        )
        second.start()
        try:
            waiting = _wait_for_waiting_run(env.root, "high")
            record = load_run(env.root, waiting)
            assert record.waiting_for == "high"
            assert record.project is None and record.cards == []
            assert record.status.value == "alive"
            assert project_show(env.root, "high").claim.run_id == holder

            holding = load_run(env.root, holder)
            deadline = time.time() + 10  # the held session runs once its record is committed
            while not (env.fake_dir / "args.1").exists():
                assert time.time() < deadline, "the waiting session never ran"
                time.sleep(0.05)
            prompt = _prompt(_args(env, 1))
            assert f"Your run id is {waiting}" in prompt
            assert "waiting_for high" in prompt
            assert f"Your wake log is {env.tmp / 'wake-logs' / waiting}.md" in prompt
            block = prompt[prompt.index("Situation:"):]
            assert (
                f"Project high is held by run {holder} (pid {holding.pid}, started "
                f"{holding.started_at}); its in_progress cards: none."
            ) in block
            assert "Waiting wake: none." in block
            assert "Wake interval: every 4 hours; next fire: " in block
            assert "Usage limit: none in force." in block
            assert "Ready cards: hi-a1, hi-a2." in block
            assert prompt.endswith(block)

            with pytest.raises(CommandError, match="alive and stalled claims are left alone"):
                project_claim(env.root, "high", run_id=waiting)
        finally:
            go_first.touch()
            first.join(timeout=10)
        assert first_codes == [0]
        assert project_show(env.root, "high").claim.run_id is None

        outcome = project_claim(env.root, "high", run_id=waiting)
        assert outcome.took_over is None
        assert project_show(env.root, "high").claim.run_id == waiting
        record = load_run(env.root, waiting)
        assert record.project == "high" and record.waiting_for is None
    finally:
        go_second.touch()
        second.join(timeout=10)
    assert second_codes == [7]
    assert _value(second_lines, "waiting_for").startswith("high: project 'high' held by live run")
    assert _value(second_lines, "ready") == ""
    # Its end is recorded as any run's: the claim it took is released, the result copied.
    ended = load_run(env.root, waiting)
    assert ended.status.value == "stopped" and ended.result is not None
    assert project_show(env.root, "high").claim.run_id is None


def test_a_held_project_with_no_ready_card_is_not_started(env, monkeypatch):
    """A live run holds `high`, whose cards it works; nothing waits for it → exit 3."""
    go = env.tmp / "go"
    monkeypatch.setenv("FAKE_WAIT", str(go))
    codes = []
    first = threading.Thread(target=lambda: codes.append(env.wake()))
    first.start()
    try:
        _wait_for_claim(env.root, "high")
        for card_id in ("hi-a1", "hi-a2"):
            card_cancel(env.root, card_id, reason="test")
        second_lines = []
        assert env.wake(out=second_lines.append) == controller.EXIT_NOT_STARTED
        assert second_lines[0].startswith("not started: no free project has ready")
        assert "'high' held by live run" in second_lines[0]
        assert len(list((env.root / "runs").glob("*.json"))) == 1
    finally:
        go.touch()
        first.join(timeout=10)
    assert codes == [0]


def _wait_for_claim(root: Path, slug: str) -> str:
    deadline = time.time() + 10
    while (run_id := project_show(root, slug).claim.run_id) is None:
        assert time.time() < deadline, f"{slug} never claimed"
        time.sleep(0.05)
    return run_id


def test_runs_on_different_projects_run_at_the_same_time(env, monkeypatch):
    """The card's test: two projects with a ready card each; a second run while the
    first is alive claims the other project, a third exits 3, and no board commit is
    lost — the orchestrators commit through `autonomouse board commit` meanwhile."""
    card_new(env.root, card_id="low-a2", project="low", title="t", goal="g", test="t")
    card_move(env.root, "low-a2", to="todo")
    commit_board(env.root, "low-a2 ready")
    go = env.tmp / "go"
    monkeypatch.setenv("FAKE_WAIT", str(go))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setenv(
        "FAKE_ON_NEW",
        f'echo "$run_id" > "{env.root}/note-$run_id" && '
        f'{sys.executable} -m autonomouse.cli board commit -m "run $run_id: note" >&2',
    )
    first_lines, second_lines, codes = [], [], {}

    def wake(name, lines):
        codes[name] = env.wake(out=lines.append)

    first = threading.Thread(target=wake, args=("first", first_lines))
    second = threading.Thread(target=wake, args=("second", second_lines))
    first.start()
    try:
        first_run = _wait_for_claim(env.root, "low")  # priority 1 comes first
        second.start()
        second_run = _wait_for_claim(env.root, "high")
        while not all(
            (env.root / f"note-{r}").exists() for r in (first_run, second_run)
        ):
            time.sleep(0.05)

        # A third wake finds both projects held; `low` (priority 1) still has low-a1
        # ready, so it waits for `low` (sys-s7): its own session runs at once (FAKE_WAIT
        # names a file that exists) and takes no claim.
        monkeypatch.setenv("FAKE_WAIT", str(env.tmp))
        third_lines = []
        assert env.wake(out=third_lines.append) == 0
        assert _value(third_lines, "waiting_for").startswith("low: project 'low' held")
        third_run = load_run(env.root, _value(third_lines, "run_id"))
        assert third_run.waiting_for == "low" and third_run.project is None
        assert project_show(env.root, "low").claim.run_id == first_run
        assert project_show(env.root, "high").claim.run_id == second_run
    finally:
        go.touch()
        first.join(timeout=20)
        second.join(timeout=20)

    assert codes == {"first": 0, "second": 0}
    assert _value(first_lines, "project") == "low"
    assert _value(second_lines, "project") == "high"
    assert _value(second_lines, "run_id") == second_run
    for run_id, slug, card in (
        (first_run, "low", "low-a2"),
        (second_run, "high", "hi-a1"),
    ):
        # `commit_board` stages and commits *everything* pending under `root`, so one
        # thread's commit can legitimately carry a second thread's just-written bytes
        # under the first thread's own message: a session runs only after its own
        # run's "started" commit (`controller._start` holds it until then), and the
        # run-controller-wide start lock serializes the two `_start` calls, but
        # nothing holds back the first run's session, whose own board commits (like
        # the "note" commit below, made through the fake orchestrator's `board
        # commit` unlocked) can race the second run's "started" commit the same way
        # the end-of-wake commits race each other. So no
        # commit message is guaranteed to survive as its own commit; nothing is lost
        # either way (checked below via each run's own record and a clean working
        # tree) — the claim itself is checked on the run record, not the commit log.
        run = load_run(env.root, run_id)
        assert run.status.value == "stopped" and run.result is not None
        assert run.project == slug and card in run.cards
        assert _git(["log", "--format=%s", "--", f"note-{run_id}"], env.root)
    assert _git(["status", "--porcelain"], env.root) == ""
    assert project_show(env.root, "high").claim.run_id is None
    assert project_show(env.root, "low").claim.run_id is None


def test_project_forces_the_wake_onto_that_project(env):
    card_new(env.root, card_id="low-a2", project="low", title="t", goal="g", test="t")
    card_move(env.root, "low-a2", to="todo")
    assert env.wake(project="high") == 0
    assert _value(env.lines, "project") == "high"
    env.lines.clear()
    for card_id in ("hi-a1", "hi-a2"):
        card_cancel(env.root, card_id, reason="test")
    assert env.wake(project="high") == 0
    assert env.lines == [
        "nothing to do: project 'high' has no ready or reclaimable cards"
    ]


def test_project_on_a_held_project_waits_for_it_or_exits_3_without_ready_cards(
    env, monkeypatch
):
    """`--project high` while a live run holds `high` (sys-s7): with ready cards the wake
    waits for it; once they are gone, exit 3 as before."""
    go = env.tmp / "go"
    monkeypatch.setenv("FAKE_WAIT", str(go))
    first = threading.Thread(target=env.wake)
    first.start()
    try:
        holder = _wait_for_claim(env.root, "high")
        monkeypatch.setenv("FAKE_WAIT", str(env.tmp))  # exists: the waiting session ends at once
        lines = []
        assert env.wake(project="high", out=lines.append) == 0
        assert _value(lines, "waiting_for").startswith("high: project 'high' held by live run")
        assert load_run(env.root, _value(lines, "run_id")).waiting_for == "high"
        assert project_show(env.root, "high").claim.run_id == holder

        for card_id in ("hi-a1", "hi-a2"):
            card_cancel(env.root, card_id, reason="test")
        lines = []
        assert env.wake(project="high", out=lines.append) == controller.EXIT_NOT_STARTED
        assert lines[0].startswith("not started: project 'high' held by live run")
    finally:
        go.touch()
        first.join(timeout=10)


def _die_holding(
    env, card_id: str, *, session_id: str, tokens: int | None = None
) -> tuple[str, int]:
    """A run that claimed `high` and started `card_id`, then died without an end. With
    `tokens`, its session's transcript records a last API call of that many tokens
    (input plus output); without, the session has no transcript."""
    if tokens is not None:
        folder = env.tmp / "claude-config" / "projects" / "-elsewhere"
        folder.mkdir(parents=True, exist_ok=True)
        usage = {
            "input_tokens": 2,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": tokens - 1502,
            "output_tokens": 500,
        }
        lines = [
            {"type": "user", "message": {"content": "go"}},
            {"type": "assistant", "message": {"usage": {"input_tokens": 1}}},
            {"type": "assistant", "message": {"usage": usage}},
            {"type": "user", "message": {"content": "tool result"}},
        ]
        (folder / f"{session_id}.jsonl").write_text(
            "".join(json.dumps(line) + "\n" for line in lines)
        )
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    old = "20260922-000000-dead"
    run_start(env.root, run_id=old, session_id=session_id, pid=dead.pid, model="m")
    project_claim(env.root, "high", run_id=old)
    path = env.root / "projects" / "high" / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    card.assigned = Assigned(
        model="m",
        effort="high",
        agent_type="worker-high",
        session_id=session_id,
        pid=dead.pid,
        picked_up_at="2026-09-22T00:00:00Z",
    )
    save_card(path, card)
    commit_board(env.root, "a run that died mid-card")
    return old, dead.pid


def _args(env, n: int) -> list[str]:
    return (env.fake_dir / f"args.{n}").read_text().splitlines()


def _prompt(args: list[str]) -> str:
    """The whole `-p` text: the fake writes one argument per line, and a prompt spans
    several since it ends with the Situation block (sys-s7)."""
    start = args.index("-p") + 1
    end = next(i for i in range(start, len(args)) if args[i] in ("--session-id", "--resume"))
    return "\n".join(args[start:end])


def test_a_failed_resume_falls_back_to_a_new_session_from_the_notes(env, monkeypatch):
    # A small crashed session is resumed, and `claude --resume` finds nothing.
    old, _ = _die_holding(env, "hi-a2", session_id="old-session", tokens=50_000)
    monkeypatch.setenv("FAKE_RESUME_FAILS", "1")

    assert env.wake() == 0

    first, second = _args(env, 0), _args(env, 1)
    assert not (env.fake_dir / "args.2").exists()
    assert first[first.index("--resume") + 1] == "old-session"
    assert first[first.index("--model") + 1] == "m"
    assert "--session-id" not in first
    assert "Continue your cards from their notes" in _prompt(first)

    assert "--resume" not in second
    assert second[second.index("--model") + 1] == "testmodel"
    new_session = second[second.index("--session-id") + 1]
    assert new_session != "old-session"
    assert _prompt(second).startswith(
        f"Read {env.tmp / 'repo' / 'docs' / 'orchestrator.md'} and run one wake."
    )
    assert "Resume these in_progress cards: hi-a2." in _prompt(second)

    failed_line = _value(env.lines, "resume_failed")
    assert failed_line.startswith("session old-session exited 1 without a result")
    assert "No conversation found with session ID: old-session" in failed_line

    run_ids = [line.split("=", 1)[1] for line in env.lines if line.startswith("run_id=")]
    assert len(run_ids) == 2
    failed, final = (load_run(env.root, r) for r in run_ids)
    assert failed.session_id == "old-session" and failed.model == "m"
    assert failed.status.value == "taken_over" and failed.result is None
    assert failed.summary.startswith("resume of session old-session failed (exit 1")
    assert final.session_id == new_session and final.status.value == "stopped"
    # width 2: hi-a2 resumed plus hi-a1, now also ready, filling the rest of the width.
    assert final.cards == ["hi-a2", "hi-a1"] and final.result is not None
    assert load_run(env.root, old).status.value == "taken_over"
    assert load_run(env.root, old).end_reason == "crashed"
    assert project_show(env.root, "high").claim.run_id is None
    assert card_show(env.root, "hi-a2").stack == Stack.IN_PROGRESS


@contextlib.contextmanager
def _killed_run(env, monkeypatch, on_new):
    """Starts the run controller as its own process group -- the run program and the
    stub claude under it, the way a real wake and its claude behave -- and kills the
    whole group on every way out of the `with` block this wraps: the block finishing,
    an assertion in it failing, or the test being interrupted. SIGKILL gives the
    controller's own leftover-process cleanup (`_end_leftovers`, sys-u4) no chance to
    run, and that cleanup would miss the stub claude anyway: `_start` gives it a process
    group of its own (sys-u4), separate from the controller's, so this also kills the
    group of every run record's pid. The controller writes a run's record before it lets
    that run's stub start (`_EXEC_WHEN_RECORDED`), so the records name every stub that
    ever ran. hi-a1's handoff is no substitute: it names the run only once the stub's own
    `card start` has finished, which under load comes after a timed-out wait (sys-w12). A
    `ProcessLookupError` means a group is already gone; the controller is waited either
    way, so it is reaped and never lingers."""
    monkeypatch.setenv("FAKE_ON_NEW", on_new)
    monkeypatch.setenv("FAKE_WAIT", str(env.tmp / "never"))
    killed = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, pickle; from autonomouse import controller; "
                "kwargs = pickle.loads(sys.stdin.buffer.read()); "
                "controller.wake(kwargs.pop('root'), **kwargs)"
            ),
        ],
        stdin=subprocess.PIPE,
        start_new_session=True,  # its own process group: the controller
    )
    kwargs = env.kwargs_for(out=print)
    killed.stdin.write(pickle.dumps({"root": env.root, **kwargs}))
    killed.stdin.close()
    try:
        yield killed
    finally:
        try:
            os.killpg(killed.pid, signal.SIGKILL)  # the whole run, the way a timer stop kills it
        except ProcessLookupError:
            pass  # the group is already gone
        killed.wait()
        for claude_pid in _run_pids(env):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(claude_pid, signal.SIGKILL)  # the stub claude, its own group too


def _run_pids(env) -> list[int]:
    """The pid of every run record on `env`'s board: each stub claude a wake started."""
    runs = (load_run(env.root, path.stem) for path in (env.root / "runs").glob("*.json"))
    return [run.pid for run in runs if run.pid is not None]


def test_a_run_killed_mid_card_is_resumed_in_the_same_session_and_the_card_finishes(
    env, monkeypatch
):
    cli = f"{sys.executable} -m autonomouse.cli"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    start = (
        f"{cli} card start hi-a1 --branch card/hi-a1 "
        '--base-branch main --session-id "$sid" --pid $$ --model sonnet --effort high '
        '--agent-type worker-high --run-id "$run_id" >/dev/null'
    )
    # The first run starts its card, then hangs mid-card until it is killed.
    deadline = time.time() + 20
    with _killed_run(
        env, monkeypatch, on_new=f'{start} && touch "$FAKE_DIR/mid-card"'
    ) as killed:
        while not (env.fake_dir / "mid-card").exists():
            assert killed.poll() is None, "the first run exited before its card started"
            assert time.time() < deadline, "the first run never reached its card"
            time.sleep(0.05)
        first_run = card_show(env.root, "hi-a1").handoff.run_id
        first = load_run(env.root, first_run)

    while pid_exists(first.pid):
        assert time.time() < deadline, "the killed claude is still running"
        time.sleep(0.05)
    assert run_status(env.root, first_run).status.value == "crashed"
    assert project_show(env.root, "high").claim.run_id == first_run

    # The next run resumes that session; the orchestrator in it finishes the card.
    monkeypatch.delenv("FAKE_WAIT")
    monkeypatch.setenv("FAKE_ON_NEW", "exit 9")  # a new session is the wrong path
    monkeypatch.setenv(
        "FAKE_ON_RESUME",
        f"{start} && {cli} card finish hi-a1 --pr https://github.com/t/t/pull/1 "
        '--last-step "verified" --run-id "$run_id" >/dev/null && '
        f'{cli} run end "$run_id" --summary "hi-a1 done" >/dev/null',
    )
    assert env.wake(model="othermodel", effort="max") == 0

    args = _args(env, 1)
    assert not (env.fake_dir / "args.2").exists()
    assert args[args.index("--resume") + 1] == first.session_id
    assert args[args.index("--model") + 1] == "testmodel"  # the first run's, not the new one
    assert args[args.index("--effort") + 1] == "xhigh"  # likewise
    assert "--session-id" not in args
    second_run = _value(env.lines, "run_id")
    prompt = _prompt(args)
    assert f"Your run id is now {second_run}" in prompt
    assert f"(run {first_run})" in prompt
    assert "Continue your cards from their notes" in prompt
    assert "Resume these in_progress cards: hi-a1." in prompt

    second = load_run(env.root, second_run)
    assert second.session_id == first.session_id and second.model == "testmodel"
    assert second.status.value == "stopped" and second.summary == "hi-a1 done"
    assert load_run(env.root, first_run).status.value == "taken_over"
    assert load_run(env.root, first_run).end_reason == "crashed"
    card = card_show(env.root, "hi-a1")
    assert card.stack == Stack.DONE and card.pr == "https://github.com/t/t/pull/1"
    assert card.assigned.session_id == first.session_id
    assert project_show(env.root, "high").claim.run_id is None
    assert _value(env.lines, "resumed") == (
        f"session {first.session_id} of run {first_run} (crashed; its session holds "
        f"1000 tokens, under the 140000 limit)"
    )


@pytest.mark.parametrize("card_started", [True, False], ids=["card-started", "card-not-started"])
def test_the_mid_card_kill_reaps_the_run_and_its_stub_claude_when_the_wait_times_out(
    env, monkeypatch, card_started
):
    # sys-p4: the card's mid-card mark never appears here, so the wait below times out
    # instead of finding it -- the failing path `_killed_run`'s `finally` must also
    # cover. Both the run program and the stub claude it started must still die.
    # sys-w12: whether or not the stub's `card start` has finished when the wait times
    # out. Under load it had not, and the stub outlived the test.
    cli = f"{sys.executable} -m autonomouse.cli"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    start = (
        f"{cli} card start hi-a1 --branch card/hi-a1 "
        '--base-branch main --session-id "$sid" --pid $$ --model sonnet --effort high '
        '--agent-type worker-high --run-id "$run_id" >/dev/null'
    )
    ready = 'touch "$FAKE_DIR/stub-ready"'  # never touches mid-card
    on_new = f"{start} && {ready}" if card_started else ready
    ready_deadline = time.time() + 60  # the stub's start, however loaded the machine
    with (
        pytest.raises(AssertionError, match="never reached its card"),
        _killed_run(env, monkeypatch, on_new=on_new) as killed,
    ):
        while not (env.fake_dir / "stub-ready").exists():
            assert killed.poll() is None, "the first run exited before its stub claude was ready"
            assert time.time() < ready_deadline, "the stub claude was never ready"
            time.sleep(0.05)
        deadline = time.time() + 1
        while not (env.fake_dir / "mid-card").exists():
            assert killed.poll() is None, "the first run exited before its card started"
            assert time.time() < deadline, "the first run never reached its card"
            time.sleep(0.05)

    assert not pid_exists(killed.pid), "the run program is still alive after the failing wait"
    (first_run,) = [path.stem for path in (env.root / "runs").glob("*.json")]
    assert (card_show(env.root, "hi-a1").handoff.run_id == first_run) is card_started
    first = load_run(env.root, first_run)
    reap_deadline = time.time() + 5
    while pid_exists(first.pid):
        assert time.time() < reap_deadline, "the stub claude is still alive after the failing wait"
        time.sleep(0.05)


def test_a_run_that_ended_cleanly_is_not_resumed(env):
    # sys-d15: its orchestrator wrote `run end`, then the wake died before releasing the
    # claim; one card is still in_progress. The next run starts a new session for it.
    old, _ = _die_holding(env, "hi-a2", session_id="old-session", tokens=50_000)
    run_end(env.root, old, summary="hi-a2 left in_progress")
    commit_board(env.root, "the run ended cleanly")

    assert env.wake() == 0

    args = _args(env, 0)
    assert "--resume" not in args and "--session-id" in args
    assert "Resume these in_progress cards: hi-a2." in _prompt(args)
    assert _value(env.lines, "resumed") == (
        f"no (new session; run {old} is stopped: pid gone; the run recorded its end)"
    )
    assert load_run(env.root, old).status.value == "taken_over"


def test_a_crashed_small_session_is_resumed(env):
    old, _ = _die_holding(env, "hi-a2", session_id="old-session", tokens=50_000)

    assert env.wake() == 0

    args = _args(env, 0)
    assert args[args.index("--resume") + 1] == "old-session"
    assert "--session-id" not in args
    # The resumed session gets its new run's own wake log.
    run_id = _value(env.lines, "run_id")
    assert f"Your wake log is {env.tmp / 'wake-logs' / run_id}.md" in _prompt(args)
    assert _value(env.lines, "resumed") == (
        f"session old-session of run {old} (crashed; its session holds 50000 tokens, "
        f"under the 140000 limit)"
    )


@pytest.mark.parametrize(
    ("tokens", "why"),
    [
        (200_000, "its session holds 200000 tokens, at or over the 140000 limit"),
        (140_000, "its session holds 140000 tokens, at or over the 140000 limit"),
        (None, "its session's token count is unknown, which counts as over the 140000 limit"),
    ],
)
def test_a_crashed_big_or_unknown_session_starts_fresh(env, tokens, why):
    old, _ = _die_holding(env, "hi-a2", session_id="old-session", tokens=tokens)

    assert env.wake() == 0

    args = _args(env, 0)
    assert "--resume" not in args and "--session-id" in args
    assert args[args.index("--session-id") + 1] != "old-session"
    assert "Resume these in_progress cards: hi-a2." in _prompt(args)
    assert _value(env.lines, "resumed") == f"no (new session; run {old} crashed; {why})"
    assert load_run(env.root, old).status.value == "taken_over"


def test_the_resume_limit_is_ceilings_resume_tokens(env):
    _die_holding(env, "hi-a2", session_id="old-session", tokens=50_000)

    assert env.wake(resume_tokens=40_000) == 0

    assert "--resume" not in _args(env, 0)
    assert "at or over the 40000 limit" in _value(env.lines, "resumed")


def test_wake_removes_a_done_cards_worktree_once_its_pr_has_merged(env):
    """`sys-e6`'s other half: `card start` made the working folder (the test
    above); this is the next run taking it back out once the card's PR has merged —
    here, a wake with nothing left to pick, so cleanup is the only thing it does."""
    card_cancel(env.root, "hi-a2", reason="test")
    card_cancel(env.root, "low-a1", reason="test")
    run_start(
        env.root, run_id="20260922-070000-0001", session_id="s1", pid=os.getpid(), model="m"
    )
    project_claim(env.root, "high", run_id="20260922-070000-0001")
    started = card_start(
        env.root,
        "hi-a1",
        branch="card/hi-a1",
        base_branch="main",
        session_id="s1",
        pid=os.getpid(),
        model="sonnet",
        effort="high",
        agent_type="worker-high",
        run_id="20260922-070000-0001",
        worktrees_root=env.tmp / "worktrees",
    )
    worktree = Path(started.worktree)
    card_finish(
        env.root,
        "hi-a1",
        last_step="verified",
        pr="https://github.com/t/t/pull/9",
        run_id="20260922-070000-0001",
    )
    project_release(env.root, "high", run_id="20260922-070000-0001")
    assert worktree.is_dir()

    checked = []
    code = env.wake(pr_state=lambda pr: checked.append(pr) or "MERGED")

    assert code == 0
    assert "nothing to do: no project has ready or reclaimable cards" in env.lines
    assert checked == ["https://github.com/t/t/pull/9"]
    assert not worktree.exists()
    assert any(line.startswith("worktree_removed card=hi-a1") for line in env.lines)
    note = card_show(env.root, "hi-a1").history[-1]
    assert note.from_ == "done" and note.to == "done"
    assert "worktree removed" in note.note


def test_a_wake_ends_with_no_worktree_it_did_not_mean_to_keep(env, monkeypatch):
    """sys-u5: a worktree registered during the wake that is no card's, as the review fork
    of sys-s62 left /tmp/pr201-worktree on branch pr-201, is removed at the wake's end with
    its branch kept and a line in the wake log. A worktree registered before the wake, which
    may be the human's, and a worktree that is a card's stay. Every worktree lives in the
    test's own repository under tmp_path."""
    repo = Path(project_show(env.root, "high").repo_path)
    human = env.tmp / "human-worktree"
    _git(["worktree", "add", "-q", str(human), "-b", "human-branch"], repo)
    orphan = env.tmp / "pr201-worktree"
    owned = env.tmp / "card-worktree"
    path = env.root / "projects" / "high" / "cards" / "hi-a1.json"
    card = load_card(path)
    card.worktree = str(owned)
    save_card(path, card)
    commit_board(env.root, "hi-a1's worktree")
    monkeypatch.setenv(
        "FAKE_ON_NEW",
        f'git -C "{repo}" worktree add -q "{orphan}" -b pr-201 && '
        f'git -C "{repo}" worktree add -q "{owned}" -b card/hi-a1',
    )

    assert env.wake() == 0

    listed = _git(["worktree", "list", "--porcelain"], repo)
    assert f"worktree {orphan}\n" not in listed and not orphan.exists()
    assert _git(["branch", "--list", "pr-201"], repo).strip() == "pr-201"
    assert f"worktree {human}\n" in listed and human.is_dir()
    assert f"worktree {owned}\n" in listed and owned.is_dir()
    assert f"worktree_removed path={orphan} branch=pr-201" in env.lines
    wake_log = Path(_value(env.lines, "wake_log")).read_text()
    assert f"removed worktree {orphan}" in wake_log
    assert "its branch pr-201 is kept" in wake_log
    assert str(human) not in wake_log and str(owned) not in wake_log
    assert _git(["status", "--porcelain"], env.root) == ""


def test_a_wake_keeps_new_worktrees_while_a_live_run_shares_its_repository(
    env, monkeypatch
):
    """sys-u5: two projects can share one repository (system and integrations do). While a
    live run holds the other one, a worktree made during this wake may be that run's
    worker's, so the wake's end removes none and says so."""
    repo = Path(project_show(env.root, "high").repo_path)
    project_new(
        env.root,
        slug="twin",
        name="twin",
        goal="g",
        repo="https://github.com/t/t",
        repo_path=str(repo),
        default_branch="main",
        priority=3,
    )
    run_start(
        env.root, run_id="20260927-070000-0001", session_id="s1", pid=os.getpid(), model="m"
    )
    project_claim(env.root, "twin", run_id="20260927-070000-0001")
    commit_board(env.root, "twin held by a live run")
    orphan = env.tmp / "twin-worker-worktree"
    monkeypatch.setenv(
        "FAKE_ON_NEW", f'git -C "{repo}" worktree add -q "{orphan}" -b twin-branch'
    )

    assert env.wake() == 0

    assert f"worktree {orphan}\n" in _git(["worktree", "list", "--porcelain"], repo)
    assert any(line.startswith("worktree_orphans=kept: ") for line in env.lines)
    assert not any(line.startswith("worktree_removed") for line in env.lines)


def test_a_wake_still_ends_with_a_slow_workers_report_recorded(env, monkeypatch):
    """The card's test: a fake worker still running when the fake orchestrator's own turn
    would otherwise be over -- as the sys-d2 worker was, still running 700s in, when run
    20260922-101216-c18a's ceiling cut it off mid-test -- does not get its report dropped.
    Real claude's own wait for a background worker happens inside its binary, out of this
    repository's reach; here the fake orchestrator's `&`-launched background job (FAKE_CLAUDE)
    stands for it, and the fake always waits for it (as claude does once the run controller
    sets CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0), so the wake only returns once the worker
    has reported. The 700s from the incident is compressed to a fraction of a second so the
    suite stays fast -- what matters is that `wake()` waits for the background job to finish
    at all, for however long that takes, not the exact duration.
    """
    cli = f"{sys.executable} -m autonomouse.cli"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    start = (
        f"{cli} card start hi-a1 --branch card/hi-a1 "
        '--base-branch main --session-id "$sid" --pid $$ --model sonnet --effort high '
        '--agent-type worker-high --run-id "$run_id" >/dev/null'
    )
    finish = (
        f'{cli} card finish hi-a1 --pr https://github.com/t/t/pull/9 '
        '--last-step "worker report" --run-id "$run_id" >/dev/null'
    )
    # The `&` backgrounds the worker's whole lifecycle, past the point the fake
    # orchestrator's own scripted turn (the rest of FAKE_ON_NEW) has finished.
    monkeypatch.setenv("FAKE_ON_NEW", f"({start} && sleep 0.3 && {finish}) &")

    assert env.wake() == 0

    assert (env.fake_dir / "env-ceiling").read_text().strip() == "0"
    card = card_show(env.root, "hi-a1")
    assert card.stack == Stack.DONE
    assert card.pr == "https://github.com/t/t/pull/9"
    assert card.handoff.last_step == "worker report"


def test_a_stopped_claim_is_picked_before_a_higher_priority_project(env):
    card_new(env.root, card_id="low-a2", project="low", title="t", goal="g", test="t")
    card_move(env.root, "low-a2", to="todo")
    old, _ = _die_holding(env, "hi-a2", session_id="old-session")

    chosen = controller.pick(env.root, 1)
    assert chosen.project.slug == "high"
    assert [c.id for c in chosen.resume] == ["hi-a2"]
    assert chosen.stopped_run.id == old

    project_release(env.root, "high", run_id=old)
    chosen = controller.pick(env.root, 1)
    assert chosen.project.slug == "low" and chosen.stopped_run is None


def test_pick_orders_ready_cards_by_position_not_id(env):
    for card_id in ("hi-a1", "hi-a2", "low-a1"):
        card_cancel(env.root, card_id, reason="test")
    for card_id, position in (("sys-x2", 22), ("sys-x11", 20), ("sys-x3", 30)):
        card_new(
            env.root, card_id=card_id, project="high", title="t", goal="g", test="t",
            position=position,
        )
        card_move(env.root, card_id, to="todo")

    chosen = controller.pick(env.root, 2)
    assert chosen.project.slug == "high"
    # sys-x11 (position 20) before sys-x2 (position 22): position order, not id order.
    # sys-x3 (position 30) is picked too (sys-i29: every ready card is handed over); the
    # ceiling of 2 leaves it out of the batch, waiting for a free slot.
    assert [c.id for c in chosen.ready] == ["sys-x11", "sys-x2", "sys-x3"]
    assert [c.id for c in chosen.batch] == ["sys-x11", "sys-x2"]
    assert [c.id for c in chosen.started] == ["sys-x11", "sys-x2"]
    assert [c.id for c in chosen.waiting] == ["sys-x3"]


def test_resumed_cards_fill_the_batch_before_ready_ones(env):
    _die_holding(env, "hi-a2", session_id="old-session")
    chosen = controller.pick(env.root, 1)
    assert [c.id for c in chosen.resume] == ["hi-a2"]
    assert [c.id for c in chosen.ready] == ["hi-a1"]
    assert [c.id for c in chosen.batch] == ["hi-a2"]
    assert chosen.started == [] and [c.id for c in chosen.waiting] == ["hi-a1"]


def test_resumed_cards_past_the_ceiling_stay_for_a_later_wake(env):
    """The ceiling cuts the resumed cards in the pick itself, as before sys-i29, so the
    prompt's resume list and the run record's `cards` name the same cards; only the ready
    list is left uncut, its tail waiting for a free slot."""
    _die_holding(env, "hi-a2", session_id="old-session")
    dead = card_show(env.root, "hi-a2").assigned
    card_new(env.root, card_id="hi-a3", project="high", title="t", goal="g", test="t",
             position=2)
    card_move(env.root, "hi-a3", to="todo")
    for card_id in ("hi-a1", "hi-a3"):
        path = env.root / "projects" / "high" / "cards" / f"{card_id}.json"
        card = load_card(path)
        card.stack, card.assigned = Stack.IN_PROGRESS, dead
        save_card(path, card)
    commit_board(env.root, "three cards under one dead claim")
    chosen = controller.pick(env.root, 2)
    assert [c.id for c in chosen.resume] == ["hi-a1", "hi-a2"]
    assert chosen.ready == [] and [c.id for c in chosen.batch] == ["hi-a1", "hi-a2"]
    assert chosen.started == [] and chosen.waiting == []
    assert controller.pick(env.root, 8).resume[2].id == "hi-a3"


def test_the_width_is_ceilings_workers_per_wake(env):
    """The ceiling cuts the run record's `cards` (the workers alive at once), never the
    ready cards the prompt hands the orchestrator: the rest wait for a free slot
    (sys-i29). With Jev off the prompt carries no Jev sentence either way."""
    one = []
    assert env.wake(workers_per_wake=1, out=one.append) == 0
    assert load_run(env.root, _value(one, "run_id")).cards == ["hi-a1"]
    prompt = _prompt(_args(env, 0))
    assert "Start these ready cards: hi-a1." in prompt
    assert "These ready cards wait for a free slot, in pick order: hi-a2." in prompt
    assert _value(one, "ready") == "hi-a1" and _value(one, "waiting") == "hi-a2"
    assert "Jev" not in prompt

    two = []
    assert env.wake(workers_per_wake=2, out=two.append) == 0
    assert load_run(env.root, _value(two, "run_id")).cards == ["hi-a1", "hi-a2"]
    prompt = _prompt(_args(env, 1))
    # Nothing follows the start sentence but the Situation block (sys-s7).
    assert "Start these ready cards: hi-a1, hi-a2.\n\nSituation:\n" in prompt
    assert "wait for a free slot" not in prompt
    assert _value(two, "waiting") == ""


def test_a_wake_writes_its_behaviour_log(env):
    assert env.wake() == 0
    run_id = _value(env.lines, "run_id")
    logs = env.tmp / "logs"
    assert _value(env.lines, "behaviour_log") == str(logs / f"{run_id}.summary.json")
    summary = json.loads((logs / f"{run_id}.summary.json").read_text())
    assert summary["run_id"] == run_id and summary["reason"] is None
    assert [p["card"] for p in summary["prs"]] == ["hi-a1", "hi-a2"]
    assert all(p["state"] == "none" for p in summary["prs"])
    assert (logs / f"{run_id}.jsonl").is_file()


def test_a_failing_behaviour_log_never_fails_the_wake(env, monkeypatch):
    def broken(root, run_id, **kwargs):
        raise RuntimeError("derivation bug")

    monkeypatch.setattr(controller.behaviour_log, "write", broken)
    assert env.wake() == 0
    assert _value(env.lines, "behaviour_log") == "failed: RuntimeError: derivation bug"


def test_a_session_that_prints_no_result_fails_the_run(env, monkeypatch):
    monkeypatch.setenv("FAKE_NO_JSON", "1")
    assert env.wake() == 1
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.result is None
    assert run.summary == "orchestrator exited 0 without writing a summary"
    assert "result none" in _value(env.lines, "record")


def test_the_session_exit_code_is_the_run_exit_code(env, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "7")
    assert env.wake() == 7
    assert project_show(env.root, "high").claim.run_id is None


def test_the_run_record_keeps_the_sessions_exit_code(env, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "3")
    assert env.wake() == 3
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.exit_code == 3


def _alive(pid: int) -> bool:
    """The process runs: it exists and is not a zombie waiting for its parent to reap it."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.fixture
def spawned():
    """The pids a test's fake processes wrote when they started. Any still alive when the
    test ends is killed by that pid alone, never by name: other sessions share this
    machine."""
    pids: list[int] = []
    yield pids
    for pid in pids:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize(
    "spawn",
    [
        # in the session's own process group
        "sleep 300",
        # in a session and process group of its own, as Claude Code's Bash tool starts
        # every command
        "setsid sleep 300",
    ],
)
def test_a_process_the_session_left_alive_is_ended_and_logged(
    env, monkeypatch, spawned, spawn
):
    """The card's test (sys-u4): the fake orchestrator starts a background sleep and exits
    without stopping it, as a worker that forgot its background job would. After the wake
    returns, the sleep is gone, the wake log names it and the run record counts it. The
    subshell exits at once, so the fake's own `wait` does not wait for the sleep; the loop
    waits for the sleep's exec, so its command line is `sleep 300` when the wake finds it."""
    pidfile = env.fake_dir / "leftover.pid"
    monkeypatch.setenv(
        "FAKE_ON_NEW",
        f'({spawn} >/dev/null 2>&1 & echo $! > "{pidfile}"); '
        f'until [ "$(cat /proc/$(cat "{pidfile}")/comm)" = sleep ]; do sleep 0.05; done',
    )
    try:
        assert env.wake() == 0
    finally:
        if pidfile.exists():
            spawned.append(int(pidfile.read_text()))
    (pid,) = spawned
    assert not _alive(pid)
    run_id = _value(env.lines, "run_id")
    log = (env.tmp / "wake-logs" / f"{run_id}.md").read_text()
    assert f"autonomouse run ended process {pid}, which the session left alive: sleep 300\n" in log
    assert _value(env.lines, "leftover_processes") == "1"
    assert load_run(env.root, run_id).leftover_processes == 1


@pytest.mark.parametrize(
    "sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGKILL], ids=lambda s: s.name
)
def test_a_signal_to_the_run_ends_every_process_of_its_run(env, monkeypatch, spawned, sig):
    """The card's test (sys-w106): the fake orchestrator starts a sleep in its own process
    group and one in a session of its own, as Claude Code's Bash tool does, then waits
    forever. The test sends the signal to `autonomouse run` alone, by its pid. Within a few
    seconds no process of the run is left: neither the stub claude nor either sleep, and
    nothing in the session's process group or with the run's AUTONOMOUSE_RUN_ID. SIGTERM
    and SIGHUP are handled, so the run record gets its end. SIGKILL is not: the session's
    watcher ends the processes, and the record is left for the next wake to find crashed.
    The watcher writes its wake-log lines only after it finds the processes gone, which can
    take one more 0.2 s poll (`_end_processes`) after the test finds them gone, so the test
    waits for the lines too."""
    children = env.fake_dir / "children"
    monkeypatch.setenv(
        "FAKE_ON_NEW",
        f'sleep 300 & echo $! >> "{children}"; setsid sleep 300 & echo $! >> "{children}"',
    )
    monkeypatch.setenv("FAKE_WAIT", str(env.tmp / "never"))
    run = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, pickle; from autonomouse import controller; "
                "kwargs = pickle.loads(sys.stdin.buffer.read()); "
                "controller.wake(kwargs.pop('root'), **kwargs)"
            ),
        ],
        stdin=subprocess.PIPE,
        start_new_session=True,  # its own process group, as a wake cron starts
    )
    try:
        run.stdin.write(pickle.dumps({"root": env.root, **env.kwargs_for(out=print)}))
        run.stdin.close()
        deadline = time.time() + 60  # the stub's start, however loaded the machine
        pids: list[int] = []
        while len(pids) < 2 or not all(
            Path(f"/proc/{pid}/comm").read_text().strip() == "sleep" for pid in pids
        ):
            assert run.poll() is None, "the run exited before its session started both sleeps"
            assert time.time() < deadline, "the session never started both sleeps"
            time.sleep(0.05)
            pids = [int(p) for p in children.read_text().split()] if children.exists() else []
        spawned.extend(pids)
        (run_id,) = [path.stem for path in (env.root / "runs").glob("*.json")]
        claude_pid = load_run(env.root, run_id).pid
        spawned.append(claude_pid)
        assert controller._leftovers(run_id, claude_pid).keys() >= {claude_pid, *pids}

        os.kill(run.pid, sig)
        run.wait(timeout=30)
        deadline = time.time() + 15
        while (left := controller._leftovers(run_id, claude_pid)) or any(
            _alive(pid) for pid in spawned
        ):
            assert time.time() < deadline, f"processes of the run still alive: {left}"
            time.sleep(0.1)
    finally:
        if run.poll() is None:
            run.kill()  # the exact run this test started
            run.wait()

    record = load_run(env.root, run_id)
    if sig == signal.SIGKILL:
        assert record.ended_at is None
        assert run_status(env.root, run_id).status.value == "crashed"
        wake_log = env.tmp / "wake-logs" / f"{run_id}.md"
        deadline = time.time() + 15  # one poll behind the processes; the load allowance above
        while "autonomouse run was gone; its watcher ended process" not in (
            log := wake_log.read_text()
        ):
            assert time.time() < deadline, f"the watcher wrote no line to the wake log: {log}"
            time.sleep(0.05)
    else:
        assert record.ended_at is not None and record.status.value == "stopped"
        assert record.exit_code == 128 + sig
        assert record.summary.startswith(f"autonomouse run got {sig.name}; it ended")
        assert record.leftover_processes >= 3  # the stub claude and both sleeps
        assert f"ended by {sig.name}" in _git(["log", "-1", "--format=%s"], env.root)


def test_a_session_that_leaves_nothing_counts_zero(env):
    assert env.wake() == 0
    run_id = _value(env.lines, "run_id")
    assert _value(env.lines, "leftover_processes") == "0"
    assert load_run(env.root, run_id).leftover_processes == 0
    assert "autonomouse run ended process" not in (
        env.tmp / "wake-logs" / f"{run_id}.md"
    ).read_text()


def _pool_tools(tmp_path, monkeypatch) -> Path:
    """A fake `node` and `playwright-mcp` first on PATH, so `_pool_mcp_config` (and so
    `_start`, with `browser.pool` set) finds them without needing either really
    installed."""
    bin_dir = tmp_path / "pool-tools"
    bin_dir.mkdir()
    for name in ("node", "playwright-mcp"):
        tool = bin_dir / name
        tool.write_text("#!/usr/bin/env bash\necho fake $0 \"$@\"\n")
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def test_browser_pool_set_registers_one_pool_server_per_slot(env, monkeypatch, tmp_path):
    """`browser.pool` set (sys-w1, this plan): the session's argv gets one `pool-N`
    server per slot (`browser.slots`, 4 by default) at the pool machine's port 930N, and
    `--strict-mcp-config`, and the wake's own release backstop ran
    (`leased_browsers_released=0`: nothing was leased in this fake wake)."""
    node_dir = _pool_tools(tmp_path, monkeypatch)
    assert env.wake(browser_pool="deploy@192.0.2.14") == 0
    args = (env.fake_dir / "args").read_text().splitlines()
    assert "--strict-mcp-config" in args
    mcp_config = json.loads(args[args.index("--mcp-config") + 1])
    servers = mcp_config["mcpServers"]
    assert set(servers) == {f"pool-{n}" for n in range(1, 5)}
    assert servers["pool-1"] == {
        "type": "stdio",
        "command": str(node_dir / "node"),
        "args": [str(node_dir / "playwright-mcp"), "--cdp-endpoint", "http://192.0.2.14:9301"],
        "env": {},
    }
    assert servers["pool-4"]["args"][-1] == "http://192.0.2.14:9304"
    assert "leased_browsers_released=0" in env.lines


def test_browser_slots_sets_how_many_pool_servers_the_session_gets(env, monkeypatch, tmp_path):
    _pool_tools(tmp_path, monkeypatch)
    assert env.wake(browser_pool="deploy@192.0.2.14", browser_slots=2) == 0
    args = (env.fake_dir / "args").read_text().splitlines()
    servers = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]
    assert set(servers) == {"pool-1", "pool-2"}


def test_browser_pool_unset_adds_no_mcp_flags_and_releases_nothing(env):
    """Unset `browser.pool`: today's argv, byte for byte (the user's rule — no runtime
    fallback either way) and no release backstop line, since no pool means no lease
    could exist."""
    assert env.wake() == 0
    args = (env.fake_dir / "args").read_text().splitlines()
    assert "--mcp-config" not in args
    assert "--strict-mcp-config" not in args
    assert not any(line.startswith("leased_browsers_released") for line in env.lines)


# The user's Claude Code config as this machine holds it once the owner registered the
# Linear MCP server at user scope (`claude mcp add --scope user --transport http linear
# https://mcp.linear.app/mcp`), beside the local browser servers `setup/install.sh`
# registers.
LINEAR_SERVER = {"type": "http", "url": "https://mcp.linear.app/mcp"}
USER_MCP_SERVERS = {
    "playwright": {"type": "stdio", "command": "npx", "args": ["@playwright/mcp@latest"]},
    "chrome-devtools": {"type": "stdio", "command": "npx", "args": ["chrome-devtools-mcp"]},
    "linear": LINEAR_SERVER,
}


def _user_mcp_config(tmp_path: Path) -> None:
    """Write `USER_MCP_SERVERS` as the user config Claude Code reads under the fixture's
    `CLAUDE_CONFIG_DIR` (`$CLAUDE_CONFIG_DIR/.claude.json`)."""
    config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".claude.json").write_text(json.dumps({"mcpServers": USER_MCP_SERVERS}))


def _link_high_to_linear(env) -> None:
    project_edit(env.root, "high", tracker="https://linear.app/acme/project/autonomouse-1a2b")
    commit_board(env.root, "link high to linear")


def test_browser_pool_keeps_the_linked_projects_linear_server(env, monkeypatch, tmp_path):
    """sys-w27: a project linked to Linear, woken with `browser.pool` set, gets the user's
    Linear MCP server, exactly as registered, beside the pool servers; the local
    browser servers stay out, as `--strict-mcp-config` has kept them since sys-w1."""
    _pool_tools(tmp_path, monkeypatch)
    _user_mcp_config(tmp_path)
    _link_high_to_linear(env)
    assert env.wake(browser_pool="deploy@192.0.2.14") == 0
    args = (env.fake_dir / "args").read_text().splitlines()
    assert "--strict-mcp-config" in args
    servers = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]
    assert set(servers) == {f"pool-{n}" for n in range(1, 5)} | {"linear"}
    assert servers["linear"] == LINEAR_SERVER
    assert "tracker_mcp=linear" in env.lines


def test_browser_pool_keeps_no_tracker_server_for_an_unlinked_project(
    env, monkeypatch, tmp_path
):
    """An unlinked project with `browser.pool` set: only the pool servers, as
    before sys-w27, though the user's config registers a Linear server."""
    _pool_tools(tmp_path, monkeypatch)
    _user_mcp_config(tmp_path)
    assert env.wake(browser_pool="deploy@192.0.2.14") == 0
    args = (env.fake_dir / "args").read_text().splitlines()
    servers = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]
    assert set(servers) == {f"pool-{n}" for n in range(1, 5)}
    assert not any(line.startswith("tracker_mcp=") for line in env.lines)


def test_no_browser_pool_leaves_a_linked_projects_session_unchanged(env, tmp_path):
    """No pool: a linked project's session gets no MCP flags at all, so Claude Code loads
    every server the user registered, the Linear one among them, as before sys-w27."""
    _user_mcp_config(tmp_path)
    _link_high_to_linear(env)
    assert env.wake() == 0
    args = (env.fake_dir / "args").read_text().splitlines()
    assert "--mcp-config" not in args
    assert "--strict-mcp-config" not in args
    assert not any(line.startswith("tracker_mcp=") for line in env.lines)


def test_pool_mcp_config_needs_node_on_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="node is not on PATH"):
        controller._pool_mcp_config("deploy@192.0.2.14", 4)


def test_pool_mcp_config_needs_playwright_mcp_on_path(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    node = bin_dir / "node"
    node.write_text("#!/usr/bin/env bash\n")
    node.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    with pytest.raises(FileNotFoundError, match="playwright-mcp is not on PATH"):
        controller._pool_mcp_config("deploy@192.0.2.14", 4)


# The ssh argv `browser._run_ssh` builds for `release <slot>`: -o ConnectTimeout=10
# <pool> browsermaze release <slot> — so the slot is argv[6] ("$6").
FAKE_SSH_RELEASE = """#!/usr/bin/env bash
slot="$6"
if [ "$slot" = "${FAKE_SSH_FAIL_SLOT:-}" ]; then
    echo "boom: release $slot failed" >&2
    exit 1
fi
echo "$slot" >> "$FAKE_SSH_DIR/released"
echo "released $slot"
"""


def _fake_ssh_release(tmp_path, monkeypatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(FAKE_SSH_RELEASE)
    ssh.chmod(0o755)
    ssh_dir = tmp_path / "ssh-calls"
    ssh_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_DIR", str(ssh_dir))
    monkeypatch.delenv("FAKE_SSH_FAIL_SLOT", raising=False)
    return ssh_dir


def _started(state_dir: Path, run_id: str, *, browser_pool: str = "") -> controller.Started:
    return controller.Started(
        run_id=run_id,
        session_id="s",
        resumed=None,
        proc=None,
        result_path=state_dir / f"{run_id}.result.json",
        stderr_path=state_dir / f"{run_id}.stderr.log",
        wake_log=state_dir / "wake-log.md",
        browser_pool=browser_pool,
    )


def test_release_leased_browsers_releases_everything_the_record_names(tmp_path, monkeypatch):
    ssh_dir = _fake_ssh_release(tmp_path, monkeypatch)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "run-1.leases.json").write_text(json.dumps([2, 5]))
    started = _started(state_dir, "run-1", browser_pool="deploy@192.0.2.14")

    lines: list[str] = []
    controller._release_leased_browsers(started, lines.append)

    assert sorted((ssh_dir / "released").read_text().split()) == ["2", "5"]
    assert lines == ["leased_browsers_released=2"]
    assert not (state_dir / "run-1.leases.json").exists()


def test_release_leased_browsers_reports_a_failed_release_and_keeps_going(tmp_path, monkeypatch):
    ssh_dir = _fake_ssh_release(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_SSH_FAIL_SLOT", "2")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "run-1.leases.json").write_text(json.dumps([2, 5]))
    started = _started(state_dir, "run-1", browser_pool="deploy@192.0.2.14")

    lines: list[str] = []
    controller._release_leased_browsers(started, lines.append)

    assert (ssh_dir / "released").read_text().split() == ["5"]
    assert lines == [
        (
            "leased_browser_release_failed=slot 2: ssh deploy@192.0.2.14 browsermaze "
            "release 2 failed: boom: release 2 failed"
        ),
        "leased_browsers_released=1",
    ]
    # slot 2 was never released, so it is still on the record for the next attempt.
    assert browser.leased_slots(state_dir, "run-1") == [2]


def test_release_leased_browsers_prints_nothing_when_the_run_used_no_pool(tmp_path):
    state_dir = tmp_path / "state"
    started = _started(state_dir, "run-1")  # browser_pool="" — no pool, no lease

    lines: list[str] = []
    controller._release_leased_browsers(started, lines.append)
    assert lines == []


def test_a_wait_cut_off_ends_the_session_and_what_it_started(tmp_path, spawned):
    """Ctrl-C on a wake started by hand no longer reaches the session, which runs in a
    process group of its own; the controller ends it and its processes before the
    interrupt goes on."""
    pidfile = tmp_path / "child.pid"
    proc = subprocess.Popen(
        ["bash", "-c", f'setsid sleep 300 & echo $! > "{pidfile}"; exec sleep 300'],
        env={**os.environ, "AUTONOMOUSE_RUN_ID": "20260927-000000-cut0"},
        start_new_session=True,
    )
    spawned.append(proc.pid)
    while not pidfile.exists() or not pidfile.read_text().strip():
        time.sleep(0.05)
    spawned.append(int(pidfile.read_text()))

    def interrupted():
        raise KeyboardInterrupt

    proc.wait = interrupted
    started = controller.Started(
        run_id="20260927-000000-cut0",
        session_id="s",
        resumed=None,
        proc=proc,
        result_path=tmp_path / "result.json",
        stderr_path=tmp_path / "stderr.log",
        wake_log=tmp_path / "wake-log.md",
    )
    lines: list[str] = []
    with pytest.raises(KeyboardInterrupt):
        controller._wait(tmp_path, started, lines.append)
    assert not any(_alive(pid) for pid in spawned)
    assert lines == ["leftover_processes=2"]
    subprocess.Popen.wait(proc)  # reap the session this test started


# Run by the fake orchestrator ($FAKE_ON_NEW) on its own run record, the way a newer
# autonomouse merged in during the wake writes it (sys-d31): sets field argv[2] to the
# JSON value argv[3].
EDIT_RECORD = """
import json, sys
path, key, value = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
data = json.loads(open(path).read())
data[key] = value
open(path, "w").write(json.dumps(data))
"""


def _orchestrator_writes(env, monkeypatch, key: str, value: str) -> None:
    script = env.tmp / "edit_record.py"
    script.write_text(EDIT_RECORD)
    monkeypatch.setenv(
        "FAKE_ON_NEW",
        f"{sys.executable} {script} {env.root}/runs/$run_id.json {key} '{value}'",
    )


def test_a_run_record_field_this_version_does_not_know_is_loaded_and_kept(
    env, monkeypatch
):
    """Run 20260922-180917-e38d: PR #81 merged while the wake ran, the orchestrator's
    `run end` wrote the new `effort` field, and the wake's older controller refused the
    record, so the result was never copied and the claim stayed held."""
    _orchestrator_writes(env, monkeypatch, "added_by_a_newer_version", '{"x": 1}')
    assert env.wake() == 0
    run_id = _value(env.lines, "run_id")

    run = load_run(env.root, run_id)
    assert run.extra == {"added_by_a_newer_version": {"x": 1}}
    assert run.result == {"type": "result", "is_error": False, "total_cost_usd": 0.01}
    data = json.loads((env.root / "runs" / f"{run_id}.json").read_text())
    assert data["added_by_a_newer_version"] == {"x": 1}
    assert project_show(env.root, "high").claim.run_id is None
    assert not any(line.startswith("record_invalid=") for line in env.lines)


def test_a_record_the_loader_refuses_after_the_session_still_gets_the_result(
    env, monkeypatch
):
    """The orchestrator leaves its record in a shape this version refuses (`version` an
    integer): the controller's loader fails once the session has ended. The result and
    the end still reach the record, the claim is released and the board committed, and
    the wake exits with the session's own code."""
    _orchestrator_writes(env, monkeypatch, "version", "2")
    monkeypatch.setenv("FAKE_EXIT", "5")
    assert env.wake() == 5
    run_id = _value(env.lines, "run_id")

    assert "'version' must be string or null" in _value(env.lines, "record_invalid")
    data = json.loads((env.root / "runs" / f"{run_id}.json").read_text())
    assert data["result"] == {
        "type": "result",
        "is_error": False,
        "total_cost_usd": 0.01,
    }
    assert data["status"] == "stopped" and data["ended_at"] is not None
    assert data["summary"] == "orchestrator exited 5 without writing a summary"
    assert data["version"] == 2
    assert _value(env.lines, "claim") == "released"
    assert project_show(env.root, "high").claim.run_id is None
    log = _git(["log", "--format=%s"], env.root)
    assert f"run {run_id}: ended (exit 5); result recorded; claim released" in log


def test_a_failed_claim_release_or_commit_still_exits_with_the_session_code(
    env, monkeypatch
):
    def refuse_release(root, slug, *, run_id):
        raise CommandError("release refused for the test")

    real_commit = controller.commit_board

    def refuse_end_commit(root, message):
        if ": ended (" in message:
            raise GitCommitError("commit refused for the test")
        return real_commit(root, message)

    monkeypatch.setattr(controller, "project_release", refuse_release)
    monkeypatch.setattr(controller, "commit_board", refuse_end_commit)
    monkeypatch.setenv("FAKE_EXIT", "7")
    assert env.wake() == 7
    assert _value(env.lines, "claim") == (
        "failed: CommandError: release refused for the test"
    )
    assert _value(env.lines, "commit") == (
        "failed: GitCommitError: commit refused for the test"
    )
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.result is not None


def _finish_in_the_wake(
    env, monkeypatch, *card_ids: str, branches: dict[str, str] | None = None
) -> None:
    """The fake orchestrator starts and finishes each of `card_ids` under its own run id,
    as `card start` and `card finish` from a worker's report do, each on `branches[id]`
    (default `card/<id>`)."""
    cli = f"{sys.executable} -m autonomouse.cli"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    branches = branches or {}
    steps = [
        f"{cli} card start {card_id} --base-branch main "
        f"--branch {branches.get(card_id, f'card/{card_id}')} "
        '--session-id "$sid" --pid $$ --model sonnet --effort high '
        '--agent-type worker-high --run-id "$run_id" >/dev/null && '
        f"{cli} card finish {card_id} --pr https://github.com/t/t/pull/{n} "
        '--last-step "worker report" --run-id "$run_id" >/dev/null'
        for n, card_id in enumerate(card_ids, start=1)
    ]
    monkeypatch.setenv("FAKE_ON_NEW", " && ".join(steps))


def _pr(head: str, base: str, *, fork: bool = False) -> dict:
    return {"headRefName": head, "baseRefName": base, "isCrossRepository": fork}


def test_a_wake_ends_with_one_report_of_cards_finished_and_prs_waiting(env, monkeypatch):
    """sys-w23: the owner gets one message as the wake ends, naming the project by its name
    (no board id): the cards this wake moved to done, and autonomouse's own open pull
    requests into the default branch or stacked on a card's branch."""
    path = env.root / "projects" / "high" / "project.json"
    data = json.loads(path.read_text())
    data["name"] = "Widgets"
    path.write_text(json.dumps(data))
    # A card an earlier run finished is not this wake's.
    card_new(env.root, card_id="hi-z9", project="high", title="t", goal="g", test="t")
    card_path = env.root / "projects" / "high" / "cards" / "hi-z9.json"
    card = load_card(card_path)
    card.stack = Stack.IN_PROGRESS
    save_card(card_path, card)
    card_finish(env.root, "hi-z9", last_step="done before", no_code=True, run_id="earlier")
    commit_board(env.root, "an earlier run finished hi-z9")
    _finish_in_the_wake(env, monkeypatch, "hi-a1", "hi-a2")
    asked = []

    def open_prs(repo):
        asked.append(repo)
        return [
            _pr("card/hi-a1", "main"),
            _pr("wake/20260929-000000-abcd", "main"),
            _pr("card/hi-a3", "card/hi-a1"),  # stacked on a card's branch
            _pr("card/hi-a4", "wake/20260929-000000-abcd"),  # the worker's to merge
            _pr("feature/mine", "main"),  # not autonomouse's
            _pr("card/hi-a5", "main", fork=True),  # a fork's
        ]

    monkeypatch.setattr(controller, "_open_prs", open_prs)
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append((text, kw)))

    assert env.wake() == 0

    assert card_show(env.root, "hi-a1").stack == Stack.DONE
    assert card_show(env.root, "hi-a2").stack == Stack.DONE
    assert asked == ["t/t"]
    [(text, kw)] = sent
    assert text == (
        "<b>The wake on Widgets has ended</b>\n"
        "Finished: 2 cards\n"
        "Waiting for you to merge: 3 pull requests"
    )
    assert "high" not in text and "hi-" not in text
    assert kw["run_id"] == _value(env.lines, "run_id")
    assert kw["html"] is True
    assert _value(env.lines, "wake_report") == " / ".join(text.splitlines())


def _wake_on_typed_branches(env, monkeypatch, prs: list[dict]) -> str:
    """Run a wake whose three cards finish on `feat/hi-a1`, `fix/hi-a2` and `docs/hi-a3`
    (sys-w18's branch names) with `prs` open on the repository, and return the text of the
    one report it sends."""
    card_new(env.root, card_id="hi-a3", project="high", title="t", goal="g", test="t",
             position=2)
    card_move(env.root, "hi-a3", to="todo")
    commit_board(env.root, "a third ready card")
    _finish_in_the_wake(
        env, monkeypatch, "hi-a1", "hi-a2", "hi-a3",
        branches={"hi-a1": "feat/hi-a1", "hi-a2": "fix/hi-a2", "hi-a3": "docs/hi-a3"},
    )
    monkeypatch.setattr(controller, "_open_prs", lambda repo: prs)
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    assert env.wake(workers_per_wake=3) == 0
    assert [card_show(env.root, c).stack for c in ("hi-a1", "hi-a2", "hi-a3")] == [Stack.DONE] * 3
    [text] = sent
    return text


def test_the_wake_report_counts_prs_on_the_typed_branch_names(env, monkeypatch):
    """sys-w41: a card's PR on a `feat/` branch into the default branch and one stacked on
    another card's `fix/` branch both wait for the owner, as the sweep reads them: a branch
    a card records is a card's, a human's branch with the same prefix is not."""
    text = _wake_on_typed_branches(env, monkeypatch, [
        _pr("feat/hi-a1", "main"),
        _pr("docs/hi-a3", "fix/hi-a2"),  # stacked on another card's fix/ branch
        _pr("fix/hi-a2", "wake/20260929-000000-abcd"),  # the worker's to merge
        _pr("feat/mine", "main"),  # a human's: no card records it
        _pr("fix/mine", "fix/hi-a2"),  # a human's, stacked on a card's branch
        _pr("feat/hi-a1", "main", fork=True),  # a fork's, whatever its name
        _pr("docs/hi-a3", "docs/mine"),  # stacked on a branch no card records
    ])
    assert text.splitlines()[-1] == "Waiting for you to merge: 2 pull requests"


def test_a_pr_stacked_on_a_recorded_branch_counts_whatever_its_own_name(env, monkeypatch):
    """sys-w41: the stacked base is read the same way the sweep reads a head: a branch a
    card records is a card's, even under a PR from before the typed names (`card/`)."""
    text = _wake_on_typed_branches(env, monkeypatch, [
        _pr("card/hi-a9", "fix/hi-a2"),
        _pr("card/hi-a9", "feat/mine"),
    ])
    assert text.splitlines()[-1] == "Waiting for you to merge: 1 pull request"


def test_the_wake_report_counts_a_cards_pr_by_the_branch_the_card_records(env, monkeypatch):
    """sys-w18: a card's branch is named by its kind of change. The wake's report counts
    hi-a1's PR from `feat/board-search` into main and hi-a2's from `fix/viewer-crash`
    stacked on it, and leaves out a human's `fix/typo` PR that no card records."""
    _finish_in_the_wake(
        env,
        monkeypatch,
        "hi-a1",
        "hi-a2",
        branches={"hi-a1": "feat/board-search", "hi-a2": "fix/viewer-crash"},
    )
    monkeypatch.setattr(
        controller,
        "_open_prs",
        lambda repo: [
            _pr("feat/board-search", "main"),
            _pr("fix/viewer-crash", "feat/board-search"),
            _pr("fix/typo", "main"),  # not autonomouse's
        ],
    )
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))

    assert env.wake() == 0

    assert card_show(env.root, "hi-a1").branch == "feat/board-search"
    [text] = sent
    assert text.splitlines()[1:] == [
        "Finished: 2 cards",
        "Waiting for you to merge: 2 pull requests",
    ]


def test_a_failed_wake_still_ends_with_its_report(env, monkeypatch):
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    monkeypatch.setattr(controller, "_open_prs", lambda repo: [_pr("card/hi-a1", "main")])
    monkeypatch.setenv("FAKE_EXIT", "3")
    assert env.wake() == 3
    assert sent == [
        (
            "<b>The wake on high has ended</b>\n"
            "Finished: 0 cards\n"
            "Waiting for you to merge: 1 pull request"
        )
    ]


def test_a_wake_that_starts_no_session_sends_no_report(env, monkeypatch):
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    assert env.wake() == 0
    assert len(sent) == 1  # the first wake started a session, and reported
    # A usage limit in force: the next wake finds work and starts no session.
    run = load_run(env.root, _value(env.lines, "run_id"))
    reset = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    run.usage_limit = {"hit_at": run.ended_at, "reset_at": reset, "text": "limit"}
    save_run(env.root, run)
    commit_board(env.root, "a usage limit in force")
    env.lines.clear()
    assert env.wake() == 0
    assert any(line.startswith("not started: a usage limit") for line in env.lines)
    # Nothing to do anywhere.
    for card_id in ("hi-a1", "hi-a2", "low-a1"):
        card_cancel(env.root, card_id, reason="test")
    assert env.wake(project="low") == 0
    assert sent[1:] == []
    assert not any(line.startswith("wake_report") for line in env.lines)


def test_a_report_that_cannot_be_sent_leaves_the_wake_as_it_was(env):
    """The fixture's process has no Telegram token, so the send fails: it is recorded on
    the run and committed with the rest, and the wake ends as it would have."""
    assert env.wake() == 0
    run = load_run(env.root, _value(env.lines, "run_id"))
    [failure] = run.notify_failures
    assert failure["text"].startswith("<b>The wake on high has ended</b>\nFinished: 0 cards")
    assert "TELEGRAM_BOT_TOKEN" in failure["error"]
    assert run.result is not None
    assert project_show(env.root, "high").claim.run_id is None
    assert not _git(["status", "--porcelain"], env.root)  # the failure is committed


def test_a_report_that_raises_or_cannot_read_github_never_fails_the_wake(env, monkeypatch):
    def unreadable(repo):
        raise github.GitHubError("gh pr list failed: HTTP 502")

    monkeypatch.setattr(controller, "_open_prs", unreadable)
    sent = []
    monkeypatch.setattr(controller, "notify", lambda text, **kw: sent.append(text))
    assert env.wake() == 0
    assert sent == [
        (
            "<b>The wake on high has ended</b>\n"
            "Finished: 0 cards\n"
            "Waiting for you to merge: not counted, GitHub could not be read"
        )
    ]
    assert _value(env.lines, "wake_report_prs") == "failed: gh pr list failed: HTTP 502"

    def broken(text, **kw):
        raise RuntimeError("notify broke")

    monkeypatch.setattr(controller, "notify", broken)
    env.lines.clear()
    assert env.wake() == 0  # the fake orchestrator started neither card: both are ready
    assert "wake_report=failed: RuntimeError: notify broke" in env.lines
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.result is not None
    assert project_show(env.root, "high").claim.run_id is None


def test_cli_run_honours_pause(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "PAUSE").touch()
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "run"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "AUTONOMOUSE_HOME": str(tmp_path / "board"),
            "AUTONOMOUSE_CONFIG": str(config),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == controller.EXIT_NOT_STARTED
    assert result.stdout.startswith(f"paused: {config / 'PAUSE'} exists")


def test_a_resumed_card_with_a_pr_is_followed_up_before_the_session_starts(env):
    # sys-d8: the wake follows up on the PR of each card it resumes, with the run's own
    # id, and a follow-up that fails is printed and never stops the wake.
    _die_holding(env, "hi-a2", session_id="old-session")
    path = env.root / "projects" / "high" / "cards" / "hi-a2.json"
    card = load_card(path)
    card.pr = "https://github.com/t/t/pull/2"
    save_card(path, card)
    commit_board(env.root, "hi-a2 has a PR")
    seen = []

    def follow_up(root, card, project, *, run_id):
        seen.append((card.id, project.slug, run_id))
        assert not (env.fake_dir / "args.0").exists(), "the session started first"
        raise github.GitHubError("gh is offline")

    env.wake(follow_up=follow_up)

    run_id = _value(env.lines, "run_id")
    assert seen == [("hi-a2", "high", run_id)]
    assert "follow_up=hi-a2: failed, left as it was: gh is offline" in env.lines
    assert "Resume these in_progress cards: hi-a2." in _prompt(_args(env, 0))


# A fake planner for `plan step`: records that it ran, answers the thread named in its
# prompt through the real CLI (`plan say`), prints a claude -p result, and exits with
# $FAKE_PLANNER_EXIT (0 when unset).
FAKE_PLANNER = f"""#!{sys.executable}
import json, os, re, subprocess, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
thread_id = re.search(r"plan-\\d{{8}}-\\d{{6}}-[0-9a-f]{{4}}", prompt).group(0)
open(os.path.join(os.environ["FAKE_DIR"], "planner-ran"), "w").write(thread_id)
subprocess.run(
    [sys.executable, "-m", "autonomouse.cli", "plan", "say", thread_id, "a question?"],
    check=True, capture_output=True,
)
print(json.dumps({{"type": "result", "is_error": False, "result": "ok"}}))
sys.exit(int(os.environ.get("FAKE_PLANNER_EXIT", "0")))
"""


@pytest.fixture
def planning(env, monkeypatch):
    """`env` plus a pending planning thread on `high` and a `plan_step` that runs the real
    `planner.step` with the fake planner."""
    from functools import partial

    from autonomouse import planner
    from autonomouse.board.plans import thread_open
    from autonomouse.config import DEFAULTS

    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.delenv("FAKE_PLANNER_EXIT", raising=False)
    fake = env.fake_dir / "planner"
    fake.write_text(FAKE_PLANNER)
    fake.chmod(0o755)
    env.thread = thread_open(env.root, "high", "add a dark mode")
    env.plan_step = partial(
        planner.step,
        env.root,
        config=DEFAULTS,
        claude=str(fake),
        home=env.kwargs_for()["home"],
        secrets=env.kwargs_for()["secrets"],
        state_dir=env.tmp / "plan-state",
    )
    return env


def test_a_wake_runs_plan_step_before_it_picks_a_project(planning):
    from autonomouse.board.plans import Author, load_thread

    assert planning.wake(plan_step=planning.plan_step) == 0

    assert (planning.fake_dir / "planner-ran").read_text() == planning.thread.id
    thread = load_thread(planning.root, planning.thread.id)
    assert (thread.messages[-1].from_, thread.messages[-1].text) == (
        Author.PLANNER,
        "a question?",
    )
    lines = planning.lines
    ok = lines.index("plan_step=ok")
    assert any(line.startswith(f"plan_step: {thread.id}: new planner") for line in lines[:ok])
    first_pick_line = next(i for i, line in enumerate(lines) if line.startswith("run_id="))
    assert ok < first_pick_line
    # the orchestrator session still ran on the picked project
    assert "project high" in (planning.fake_dir / "args").read_text()


@pytest.mark.parametrize("how", ["planner exits 1", "plan step raises"])
@pytest.mark.parametrize("orchestrator_exit", ["0", "7"])
def test_a_failing_plan_step_leaves_the_wake_exit_code_unchanged(
    planning, monkeypatch, how, orchestrator_exit
):
    monkeypatch.setenv("FAKE_EXIT", orchestrator_exit)
    if how == "planner exits 1":
        monkeypatch.setenv("FAKE_PLANNER_EXIT", "1")
        plan_step, failed = planning.plan_step, "plan_step=failed: exit 1"
    else:

        def plan_step(**kwargs):
            raise RuntimeError("planner bug")

        failed = "plan_step=failed: RuntimeError: planner bug"

    assert planning.wake(plan_step=plan_step) == int(orchestrator_exit)
    assert failed in planning.lines
    assert _value(planning.lines, "run_id")
    if how == "planner exits 1":
        assert (planning.fake_dir / "planner-ran").exists()


def test_a_paused_wake_runs_no_plan_step(planning):
    pause = planning.tmp / "config" / "PAUSE"
    pause.parent.mkdir()
    pause.touch()
    assert planning.wake(plan_step=planning.plan_step) == controller.EXIT_NOT_STARTED
    assert not (planning.fake_dir / "planner-ran").exists()


def test_autonomouse_run_loads_the_secrets_file_from_the_config_folder(
    env, monkeypatch, capsys
):
    """The card's test (sys-f9): the wake `autonomouse run` starts (cli._run_wake, with
    its own secrets path, claude from PATH and the default state folder) loads
    `~/.config/autonomouse/.env` — with no AUTONOMOUSE_CONFIG, on a temp HOME holding
    a dummy key — into the orchestrator session's environment, and says so."""
    from types import SimpleNamespace

    from autonomouse import cli

    home = env.tmp / "home"
    secrets = home / ".config" / "autonomouse" / ".env"
    secrets.parent.mkdir(parents=True)
    secrets.write_text("TEST_SECRET_KEY=dummy-from-config-folder\n")
    secrets.chmod(0o600)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AUTONOMOUSE_CONFIG")
    monkeypatch.delenv("AUTONOMOUSE_STATE", raising=False)
    monkeypatch.setenv("PATH", f"{env.fake_dir}{os.pathsep}{os.environ['PATH']}")

    args = SimpleNamespace(
        wake_model="testmodel", wake_effort=None, max_budget_usd=None, wake_project=None
    )
    with pytest.raises(SystemExit) as exit_:
        cli._run_wake(env.root, args)
    assert exit_.value.code == 0

    lines = capsys.readouterr().out.splitlines()
    assert _value(lines, "secrets") == f"{secrets} (loaded)"
    assert (env.fake_dir / "env").read_text().strip() == "dummy-from-config-folder"
    assert _value(lines, "result").startswith(str(home / ".local" / "state"))


def _autonomouse_run(env, monkeypatch, capsys, *flags, config: str | None = None):
    """`autonomouse run [flags]` through the CLI's own argument parser, with the config
    folder holding `config` as its config.toml (no file when None) and claude from PATH.
    Returns the fake claude's arguments and the wake's printed lines."""
    from autonomouse import cli

    config_dir = env.tmp / "config-dir"
    config_dir.mkdir(exist_ok=True)
    if config is not None:
        (config_dir / "config.toml").write_text(config)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setenv("AUTONOMOUSE_STATE", str(env.tmp / "state"))
    monkeypatch.setenv("PATH", f"{env.fake_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("sys.argv", ["autonomouse", "run", *flags])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    lines = capsys.readouterr().out.splitlines()
    assert exit_.value.code == 0, lines
    return (env.fake_dir / "args").read_text().splitlines(), lines


def _pair(args: list[str]) -> tuple[str, str]:
    return args[args.index("--model") + 1], args[args.index("--effort") + 1]


def test_the_orchestrator_tier_comes_from_the_config(env, monkeypatch, capsys):
    """The card's test (sys-d22): [orchestrator] model and effort reach the
    orchestrator's claude -p call and the run record."""
    args, lines = _autonomouse_run(
        env,
        monkeypatch,
        capsys,
        config='[orchestrator]\nmodel = "opus"\neffort = "xhigh"\n',
    )
    i = args.index("--model")
    assert args[i : i + 4] == ["--model", "opus", "--effort", "xhigh"]
    run = load_run(env.root, _value(lines, "run_id"))
    assert (run.model, run.effort) == ("opus", "xhigh")
    assert _value(lines, "effort") == "xhigh"


def test_with_no_orchestrator_table_the_tier_is_fable_medium(env, monkeypatch, capsys):
    """sys-v6: every wake's orchestrator runs on Fable at medium effort by default."""
    args, lines = _autonomouse_run(env, monkeypatch, capsys, config="[jev]\n")
    i = args.index("--model")
    assert args[i : i + 4] == ["--model", "fable", "--effort", "medium"]
    run = load_run(env.root, _value(lines, "run_id"))
    assert (run.model, run.effort) == ("fable", "medium")


def test_run_accepts_effort_medium(env, monkeypatch, capsys):
    """sys-v6: medium is itself an accepted --effort value, not just the default."""
    args, lines = _autonomouse_run(env, monkeypatch, capsys, "--effort", "medium")
    assert _pair(args) == ("fable", "medium")
    run = load_run(env.root, _value(lines, "run_id"))
    assert (run.model, run.effort) == ("fable", "medium")


def test_run_flags_override_the_config_tier(env, monkeypatch, capsys):
    args, lines = _autonomouse_run(
        env,
        monkeypatch,
        capsys,
        "--model",
        "opus",
        "--effort",
        "max",
        config='[orchestrator]\nmodel = "fable"\neffort = "high"\n',
    )
    assert _pair(args) == ("opus", "max")
    run = load_run(env.root, _value(lines, "run_id"))
    assert (run.model, run.effort) == ("opus", "max")


def test_an_orchestrator_effort_below_medium_fails_loudly(env, monkeypatch, capsys):
    from autonomouse import cli

    config_dir = env.tmp / "config-dir"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[orchestrator]\neffort = "low"\n')
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setattr("sys.argv", ["autonomouse", "run"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code != 0
    err = capsys.readouterr().err
    assert "orchestrator.effort" in err and "'low'" in err
    assert "medium, high, xhigh, max" in err
    assert not (env.fake_dir / "args").exists()
    # sys-j5: the refusal has a wake record of its own, with no session, naming the key.
    [record] = [
        r for r in (load_run(env.root, p.stem) for p in (env.root / "runs").glob("*.json"))
        if r.refusals
    ]
    assert (record.kind.value, record.session_id, record.status.value) == (
        "wake", None, "stopped"
    )
    [refusal] = record.refusals
    assert refusal["command"] == "run" and "orchestrator.effort" in refusal["reason"]


def test_an_orchestrator_effort_that_is_not_a_string_is_recorded(env, monkeypatch, capsys):
    """sys-j5: `effort = 3` is refused like any effort off the list, and its run record
    still gets written, with the effort null and the reason quoting the value."""
    from autonomouse import cli

    config_dir = env.tmp / "config-dir"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text("[orchestrator]\neffort = 3\n")
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setattr("sys.argv", ["autonomouse", "run"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 1
    [line] = capsys.readouterr().err.splitlines()
    assert line.startswith("error: orchestrator.effort") and line.endswith("got 3")
    [record] = [
        r for r in (load_run(env.root, p.stem) for p in (env.root / "runs").glob("*.json"))
        if r.refusals
    ]
    assert record.effort is None and record.refusals[0]["reason"] == line[len("error: "):]
