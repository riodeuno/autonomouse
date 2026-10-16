"""Every session that touches a project has a run record: a record says its `kind` (`wake`,
`planner`, `session`) and, for a planner, its `thread`; every claude process the planner
starts writes one; the wake's readers (`controller.all_runs`) see wakes only.

The planner runs against a fake claude of this file's own, modelled on tests/test_plans.py's;
the wakes use the board, claude and Jev fakes of tests/test_run_controller.py and
tests/test_run_jev.py.
"""

from __future__ import annotations

import itertools
import json
import sys

import pytest

from autonomouse import controller, planner
from autonomouse.board import runs as board_runs
from autonomouse.board.commands import CommandError, project_new
from autonomouse.board.plans import (
    ThreadStatus,
    load_thread,
    thread_open,
    thread_set_session,
)
from autonomouse.board.runs import load_run, run_start, save_run
from autonomouse.board.schema import BoardValidationError
from autonomouse.config import load_config
from autonomouse.home import Home
from autonomouse.jev.client import ChoiceAnswer
from test_run_controller import _value, env  # noqa: F401  (env is a fixture)
from test_run_jev import _answers, jev  # noqa: F401  (jev is a fixture)
from test_run_usage_limit import _limit_in_force

THREAD = "plan-20260927-060000-abcd"
RESULT = {"type": "result", "is_error": False, "result": "ok", "total_cost_usd": 0.42}

# A fake `claude -p`: stands for the planner. It records its arguments and its own pid in
# call.<n> (JSON) and prints RESULT, as claude prints its result. With FAKE_RESUME_FAILS set,
# `--resume` fails the way claude's does when the session's transcript is gone.
FAKE_PLANNER = f"""#!{sys.executable}
import json, os, sys

fake = os.environ["FAKE_DIR"]
n = len([f for f in os.listdir(fake) if f.startswith("call.")]) + 1
args = sys.argv[1:]
with open(os.path.join(fake, f"call.{{n}}"), "w") as f:
    json.dump({{"argv": args, "pid": os.getpid()}}, f)
if "--resume" in args and os.environ.get("FAKE_RESUME_FAILS"):
    sid = args[args.index("--resume") + 1]
    print("No conversation found with session ID: " + sid, file=sys.stderr)
    sys.exit(1)
print(json.dumps({RESULT!r}))
"""

# A fake interactive `claude`: stands for the human's live session. It records its arguments
# and its own pid in live.json and exits; an interactive session prints no result.
FAKE_LIVE = f"""#!{sys.executable}
import json, os, sys

with open(os.path.join(os.environ["FAKE_DIR"], "live.json"), "w") as f:
    json.dump({{"argv": sys.argv[1:], "pid": os.getpid()}}, f)
"""

LIVE_SUMMARY = (
    "live planner session on {}: an interactive session prints no result, so its cost is "
    "not on this record"
)


@pytest.fixture
def board(tmp_path, monkeypatch):
    """A board with project `t`, both fake claudes, and the planner's arguments."""
    root = tmp_path / "board"
    root.mkdir()
    project_new(
        root,
        slug="t",
        name="t",
        goal="g",
        repo="https://github.com/t/t",
        repo_path=str(tmp_path),
        default_branch="main",
        priority=1,
    )
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    for name, text in (("claude", FAKE_PLANNER), ("live-claude", FAKE_LIVE)):
        (fake_dir / name).write_text(text)
        (fake_dir / name).chmod(0o755)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "planner.md").write_text("# planner\n")
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("FAKE_DIR", str(fake_dir))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.delenv("FAKE_RESUME_FAILS", raising=False)

    class Board:
        pass

    b = Board()
    b.root, b.fake_dir, b.lines = root, fake_dir, []
    b.kwargs = {
        "home": Home(repo, "autonomouse-cli"),
        "secrets": tmp_path / ".env",
        "state_dir": tmp_path / "state",
        "out": b.lines.append,
    }
    # the defaults, Jev off: every step's session runs at the `complex` tier (sys-v7)
    config = load_config(tmp_path / "no-config.toml")
    b.step = lambda: planner.step(
        root, config=config, claude=str(fake_dir / "claude"), **b.kwargs
    )
    b.join = lambda thread_id: planner.join(
        root,
        thread_id,
        model="testmodel",
        effort="high",
        claude=str(fake_dir / "live-claude"),
        **b.kwargs,
    )
    return b


def _records(root) -> list[dict]:
    """Every run record under `root/runs`, as the JSON on disk."""
    folder = root / "runs"
    if not folder.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def _call(board, n: int) -> dict:
    return json.loads((board.fake_dir / f"call.{n}").read_text())


_ids = itertools.count(1)


def _start(root, **kwargs):
    """`run_start` with an id of its own and placeholder session fields; `kwargs` add the
    rest."""
    run_id = f"20260927-06{next(_ids):04d}-abcd"
    return run_start(root, run_id=run_id, session_id="s", pid=1, model="opus", **kwargs)


# ------------------------------------------------------------------ the record --


def test_a_record_written_before_kind_and_thread_loads_as_a_wake_and_saves_both(tmp_path):
    run = _start(tmp_path)
    path = board_runs.run_path(tmp_path, run.id)
    old = json.loads(path.read_text())
    del old["kind"], old["thread"]
    path.write_text(json.dumps(old))

    loaded = load_run(tmp_path, run.id)
    assert loaded.kind is board_runs.RunKind.WAKE and loaded.thread is None

    save_run(tmp_path, loaded)
    saved = json.loads(path.read_text())
    assert saved["kind"] == "wake" and saved["thread"] is None


def test_a_kind_outside_the_three_is_refused(tmp_path):
    run = _start(tmp_path)
    path = board_runs.run_path(tmp_path, run.id)
    path.write_text(json.dumps({**json.loads(path.read_text()), "kind": "spike"}))
    with pytest.raises(BoardValidationError, match="'kind' must be one of wake, planner"):
        load_run(tmp_path, run.id)


def test_run_start_writes_kind_project_and_thread_and_a_hand_start_is_a_session(tmp_path):
    planned = _start(tmp_path, kind="planner", project="t", thread=THREAD)
    data = json.loads(board_runs.run_path(tmp_path, planned.id).read_text())
    assert (data["kind"], data["project"], data["thread"]) == ("planner", "t", THREAD)

    by_hand = _start(tmp_path)  # no kind: `run start` by hand
    data = json.loads(board_runs.run_path(tmp_path, by_hand.id).read_text())
    assert (data["kind"], data["project"], data["thread"]) == ("session", None, None)


def test_new_run_id_lives_in_runs_and_still_resolves_from_the_controller():
    assert controller.new_run_id is board_runs.new_run_id
    assert board_runs._RUN_ID_RE.fullmatch(board_runs.new_run_id())


# ------------------------------------------------------------ the wake's readers --


def test_all_runs_returns_the_wake_records_only(tmp_path):
    wake = _start(tmp_path, kind="wake")
    _start(tmp_path, kind="planner", project="t", thread=THREAD)
    _start(tmp_path)  # a session that registered itself
    assert [r["id"] for r in controller.all_runs(tmp_path)] == [wake.id]


def test_a_wake_writes_a_wake_record_with_no_thread(env):  # noqa: F811
    assert env.wake() == 0
    run_id = _value(env.lines, "run_id")
    data = json.loads(board_runs.run_path(env.root, run_id).read_text())
    assert data["kind"] == "wake" and data["thread"] is None
    assert data["project"] == "high"
    assert [r["id"] for r in controller.all_runs(env.root)] == [run_id]


def test_a_wake_that_starts_no_session_is_recorded_as_a_wake(env, jev):  # noqa: F811
    """Before sys-j6 the wake that started no session was one Jev skipped; since then Jev's
    `go` is advice, and it is one a usage limit held back."""
    _limit_in_force(env)
    _, settings = jev(_answers(go=ChoiceAnswer("skip", {}, 0.9)))
    assert env.wake(jev=settings) == 0
    run = load_run(env.root, _value(env.lines, "run_id"))
    assert run.session_id is None  # no session started
    assert run.kind is board_runs.RunKind.WAKE and run.thread is None


# ----------------------------------------------------------------- the planner --


def test_a_plan_step_writes_one_planner_record_with_its_result(board):
    thread = thread_open(board.root, "t", "plan the thing")
    assert board.step() == 0

    [record] = _records(board.root)
    call = _call(board, 1)
    argv = call["argv"]
    assert record["kind"] == "planner"
    assert record["thread"] == thread.id
    assert record["project"] == "t"
    assert record["session_id"] == argv[argv.index("--session-id") + 1]
    assert record["session_id"] == load_thread(board.root, thread.id).session_id
    assert record["pid"] == call["pid"]
    assert record["model"] == argv[argv.index("--model") + 1] == "fable"
    assert record["status"] == "stopped"
    assert record["ended_at"] is not None
    assert record["result"] == RESULT
    assert record["summary"] == f"planner step on {thread.id}: exit 0"


def test_a_failed_resume_and_the_fresh_session_after_it_are_two_records(
    board, monkeypatch
):
    old = "11111111-1111-1111-1111-111111111111"
    thread = thread_open(board.root, "t", "plan the thing")
    thread_set_session(board.root, thread.id, old)
    monkeypatch.setenv("FAKE_RESUME_FAILS", "1")
    assert board.step() == 0

    records = _records(board.root)
    assert len(records) == 2
    [resumed] = [r for r in records if r["session_id"] == old]
    [fresh] = [r for r in records if r["session_id"] != old]
    assert resumed["pid"] == _call(board, 1)["pid"]
    assert resumed["result"] is None
    assert resumed["summary"] == f"planner step on {thread.id}: exit 1"
    assert fresh["session_id"] == load_thread(board.root, thread.id).session_id
    assert fresh["pid"] == _call(board, 2)["pid"]
    assert fresh["result"] == RESULT
    for record in (resumed, fresh):
        assert record["kind"] == "planner" and record["thread"] == thread.id
        assert record["status"] == "stopped" and record["ended_at"] is not None


def test_a_record_that_cannot_be_written_is_printed_and_the_step_goes_on(
    board, monkeypatch
):
    def refuse(*args, **kwargs):
        raise CommandError("refused")

    monkeypatch.setattr(planner, "run_start", refuse)
    thread = thread_open(board.root, "t", "plan the thing")
    assert board.step() == 0
    assert _records(board.root) == []
    assert f"{thread.id}: run record not written: CommandError: refused" in board.lines
    assert any(line.startswith(f"{thread.id}: planner exited 0") for line in board.lines)


def test_a_live_session_writes_one_record_once_claude_runs_and_none_before(
    board, monkeypatch
):
    thread = thread_open(board.root, "t", "plan the thing")

    # A join whose claim cannot move never runs claude, so it writes no record.
    def refuse(*args, **kwargs):
        raise CommandError("refused")

    with monkeypatch.context() as m:
        m.setattr(planner, "thread_join_pid", refuse)
        with pytest.raises(CommandError, match="refused"):
            board.join(thread.id)
    assert _records(board.root) == []

    assert board.join(thread.id) == 0
    [record] = _records(board.root)
    live = json.loads((board.fake_dir / "live.json").read_text())
    after = load_thread(board.root, thread.id)
    assert record["kind"] == "planner"
    assert record["thread"] == thread.id
    assert record["project"] == "t"
    assert record["session_id"] == after.session_id
    # The refused join gave the thread its session, so this one resumes it.
    assert live["argv"][live["argv"].index("--resume") + 1] == after.session_id
    assert record["pid"] == live["pid"]
    assert record["status"] == "stopped" and record["ended_at"] is not None
    assert record["result"] is None
    assert record["summary"] == LIVE_SUMMARY.format(thread.id)
    assert after.status is ThreadStatus.WAITING_PLANNER
