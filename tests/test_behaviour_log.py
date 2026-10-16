"""Tests for the behaviour log (autonomouse.behaviour): derived from synthetic Claude Code
transcripts written into a temp projects folder, and from a temp board whose cards the
board's own commands wrote."""

import itertools
import json
import os
import pkgutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autonomouse.behaviour import log as behaviour_log
from autonomouse.board.commands import (
    card_answer,
    card_ask,
    card_edit,
    card_finish,
    card_move,
    card_new,
    card_progress,
    card_show,
    project_new,
)
from autonomouse.board.runs import (
    card_start,
    load_run,
    project_claim,
    run_end,
    run_start,
    save_run,
)
from autonomouse.board.schema import Decision, HistoryEntry
from autonomouse.board.store import save_card
from autonomouse.review import behaviour as behaviour_review

RUN = "20260922-075309-e2ed"
OLD_RUN = "20260922-070000-aaaa"
SESSION = "c3ef72c8-9444-405e-a105-d1593c16fa89"
WORKER = "a1ddf0c5fee892e63"
HELPER = "b2eef0c5fee892e64"
FORK = "c3ffa0c5fee892e65"
OLD_FORK = "e5bbb0c5fee892e67"
FINDER = "d4aab0c5fee892e66"
PROSE = "PROSE-THAT-MUST-NOT-LEAK"
PR = "https://github.com/t/t/pull/16"
_CALL_IDS = itertools.count(1)


class Transcript:
    """Records in Claude Code's transcript shape, one second apart."""

    def __init__(
        self, start: datetime, cwd: str, model: str, *, sidechain: bool = False
    ):
        self.records: list[dict] = []
        self.clock = start
        self.cwd = cwd
        self.model = model
        self.sidechain = sidechain

    def _tick(self, seconds: float = 1) -> str:
        self.clock += timedelta(seconds=seconds)
        return self.clock.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def prompt(self, text: str) -> None:
        self.records.append(
            {
                "type": "user",
                "isSidechain": self.sidechain,
                "message": {"role": "user", "content": text},
                "timestamp": self._tick(),
                "cwd": self.cwd,
            }
        )

    def wait(self, seconds: float) -> None:
        self.clock += timedelta(seconds=seconds)

    def call(
        self,
        name: str,
        tool_input: dict,
        output: str = "",
        *,
        is_error: bool = False,
        meta: dict | None = None,
        cwd: str | None = None,
        usage: dict | None = None,
    ) -> str:
        tool_id = f"toolu_{next(_CALL_IDS):04d}"  # unique, as claude's are
        self.records.append(
            {
                "type": "assistant",
                "isSidechain": self.sidechain,
                "message": {
                    "id": f"msg_{tool_id}",
                    "model": self.model,
                    "usage": usage or {},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": name,
                            "input": tool_input,
                        }
                    ],
                },
                "effort": "high",
                "timestamp": self._tick(),
                "cwd": cwd or self.cwd,
            }
        )
        self.records.append(
            {
                "type": "user",
                "isSidechain": self.sidechain,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": output,
                            "is_error": is_error,
                        }
                    ],
                },
                "toolUseResult": meta,
                "timestamp": self._tick(),
                "cwd": cwd or self.cwd,
            }
        )
        return tool_id

    def bash(self, command: str, output: str = "", **kw) -> str:
        return self.call("Bash", {"command": command}, output, **kw)

    def api_error(self, *, reset_epoch: int, seconds: float = 1) -> str:
        """A `system`/`api_error` record: a 429 rate-limit error naming a reset time,
        as Claude Code's own retry logic writes one each time a request is refused for
        the account's usage limit. Returns its timestamp."""
        timestamp = self._tick(seconds)
        self.records.append(
            {
                "type": "system",
                "subtype": "api_error",
                "level": "error",
                "error": {
                    "status": 429,
                    "rateLimits": {
                        "rateLimitType": "five_hour",
                        "resetsAt": reset_epoch,
                    },
                },
                "timestamp": timestamp,
            }
        )
        return timestamp

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in self.records))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A board with project `system` (repo checkout tmp/repo) and card sys-b6 in `todo`,
    an empty Claude projects folder, and no notify channel (`card ask` sends nothing)."""
    root = tmp_path / "board"
    repo = tmp_path / "repo"
    for folder in (root, repo):
        folder.mkdir(parents=True)
    project_new(
        root,
        slug="system",
        name="System",
        goal="g",
        repo="https://github.com/t/t",
        repo_path=str(repo),
        default_branch="main",
        priority=0,
    )
    card_new(root, card_id="sys-b6", project="system", title="t", goal="g", test="t")
    card_move(root, "sys-b6", to="todo")
    projects = tmp_path / "claude" / "projects"
    projects.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))

    class World:
        pass

    w = World()
    w.root, w.repo, w.projects, w.tmp = root, repo, projects, tmp_path
    w.slug_dir = projects / "-home-t-repo"
    return w


def _start_run(
    w, run_id: str = RUN, *, session: str | None = SESSION, cards=("sys-b6",)
):
    """The run record, alive and holding the project's claim."""
    run_start(w.root, run_id=run_id, session_id=session or "none", pid=1, model="opus")
    run = load_run(w.root, run_id)
    run.session_id = session  # None: a wake Jev said not to start
    run.cards = list(cards)
    save_run(w.root, run)
    project_claim(w.root, "system", run_id=run_id)


def _end_run(w, run_id: str = RUN) -> None:
    """Claude's result: the run's total cost."""
    run_end(w.root, run_id, result={"total_cost_usd": 5.5}, summary="s")


def _record_run(w, run_id: str = RUN, **kw) -> None:
    _start_run(w, run_id, **kw)
    _end_run(w, run_id)


def _start(w, card_id: str = "sys-b6", run_id: str = RUN) -> None:
    card_start(
        w.root,
        card_id,
        branch=f"card/{card_id}",
        base_branch="main",
        session_id=SESSION,
        pid=1,
        model="sonnet",
        effort="high",
        agent_type="worker-high",
        run_id=run_id,
        worktrees_root=w.tmp / "worktrees",
        create_worktree=lambda repo, path, **_: path.mkdir(parents=True),
    )


def _wake_on_board(w) -> None:
    """What the sys-b6 wake wrote through the board's commands: the orchestrator starts
    the card, the worker notes its steps, and the orchestrator finishes it."""
    _start(w)
    card_progress(
        w.root, "sys-b6", phase="testing", iteration=0, last_step=PROSE, run_id=RUN
    )
    card_progress(w.root, "sys-b6", phase="ci", pr=PR, run_id=RUN)
    card_finish(w.root, "sys-b6", pr=PR, last_step=PROSE, run_id=RUN)


def _transcripts(w) -> None:
    """The sys-b6 wake's transcripts: the orchestrator on claude-opus-5 spawns the worker
    and runs `card finish` from a bash array, as wake 20260926-145454-bb39 did; the worker
    on claude-sonnet-5 edits the repo checkout, runs pytest and ruff, sleeps, and spawns a
    helper on a model with no rates. None of their shell commands is read."""
    t0 = datetime(2026, 9, 22, 7, 53, tzinfo=UTC)
    card_file = f"{w.root}/projects/system/cards/sys-b6.json"
    orch = Transcript(t0, str(w.repo), "claude-opus-5")
    orch.prompt(f"Read docs/orchestrator.md and run one wake. Your run id is {RUN}.")
    spawn = orch.call(
        "Agent",
        {
            "description": "Worker for sys-b6",
            "subagent_type": "worker-high",
            "model": "sonnet",
            "prompt": f"Your charter is `docs/worker.md`; your card is `{card_file}`.",
        },
        "Async agent launched successfully.",
        meta={"agentId": WORKER, "isAsync": True},
        usage={"output_tokens": 50_000},  # $1.25 at claude-opus-5's rates
    )
    orch.wait(300)
    orch.prompt(
        f"Your run id is {RUN}."
    )  # the same run's prompt again is not a new part
    orch.bash(
        f'cmds=("card finish sys-b6 --pr {PR} --last-step {PROSE} --run-id {RUN}"); '
        'for c in "${cmds[@]}"; do uv run autonomouse $c; done'
    )
    orch.write(w.slug_dir / f"{SESSION}.jsonl")

    worker = Transcript(
        t0 + timedelta(seconds=10), str(w.repo), "claude-sonnet-5", sidechain=True
    )
    worker.prompt(f"Your charter is `docs/worker.md`; your card is `{card_file}`.")
    worker.call(
        "Edit",
        {"file_path": f"{w.repo}/src/cli.py", "old_string": "a", "new_string": PROSE},
        usage={  # $3.75 at claude-sonnet-5's rates
            "input_tokens": 50_000,  # $0.10
            "output_tokens": 250_000,  # $2.50
            "cache_read_input_tokens": 2_500_000,  # $0.50
            "cache_creation_input_tokens": 200_000,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 100_000,  # $0.25
                "ephemeral_1h_input_tokens": 100_000,  # $0.40
            },
        },
    )
    worker.bash("uv run pytest -q 2>&1 | tail -3", "F..\n1 failed, 2 passed in 0.50s")
    worker.bash("uv run ruff check src tests", "Found 2 errors.", is_error=True)
    worker.bash("sleep 30 && gh api repos/t/t/actions/runs")
    helper = worker.call(
        "Agent",
        {
            "description": "look",
            "subagent_type": "Explore",
            "prompt": "find the parser",
        },
        "done",
        meta={"agentId": HELPER},
    )
    worker.write(w.slug_dir / SESSION / "subagents" / f"agent-{WORKER}.jsonl")
    (w.slug_dir / SESSION / "subagents" / f"agent-{WORKER}.meta.json").write_text(
        json.dumps({"agentType": "worker-high", "toolUseId": spawn, "model": "sonnet"})
    )

    sub = Transcript(
        t0 + timedelta(seconds=60), str(w.repo), "claude-new-9", sidechain=True
    )
    sub.prompt("find the parser")
    sub.call("Read", {"file_path": f"{w.repo}/src/cli.py"}, usage={"output_tokens": 10})
    sub.write(w.slug_dir / SESSION / "subagents" / f"agent-{HELPER}.jsonl")
    (w.slug_dir / SESSION / "subagents" / f"agent-{HELPER}.meta.json").write_text(
        json.dumps({"agentType": "Explore", "toolUseId": helper})
    )


def _derive(w, run_id: str = RUN):
    return behaviour_log.derive(w.root, run_id, pr_state=lambda url: "mergeable")


def test_the_summary_draws_its_rows_from_the_board_and_the_transcripts_usage(world):
    """sys-s55 (decision H, part 2): the shell reader's counts were wrong in each of
    three wakes, so no row reads a shell command. The transitions come from the card's
    history, though the transcript's `card finish` hides in a bash array; the cost per
    model comes from the transcripts' usage; the file-write, test and sleep rows, which
    only the shell reader could fill, are gone."""
    _start_run(world)
    _wake_on_board(world)
    _end_run(world)
    _transcripts(world)

    events, summary = _derive(world)

    moves = [
        (t["card"], t["from"], t["to"], t["means"]) for t in summary["transitions"]
    ]
    assert moves == [
        ("sys-b6", "todo", "in_progress", "card start"),
        ("sys-b6", "in_progress", "done", "card finish"),
    ]
    assert summary["cost"] == {
        "total_usd": 5.5,
        "by_model": {
            "claude-opus-5": pytest.approx(1.25),
            "claude-sonnet-5": pytest.approx(3.75),
            "claude-new-9": None,
        },
        "unpriced_models": ["claude-new-9"],
    }
    dropped = {"file_writes", "tests", "lint", "sleep_poll_calls"}
    assert all(not dropped & set(agent) for agent in summary["agents"])
    assert {e["event"] for e in events} <= {
        "agent_start", "agent_end", "pr", "transition", "phase", "iteration", "decision"
    }  # fmt: skip
    shown = behaviour_log.format_summary(summary)
    for row in ("file writes", "tests:", "lint:", "sleep/poll"):
        assert row not in shown
    assert "sys-b6: todo -> in_progress by card start" in shown
    assert (
        "cost: $5.50 (claude-opus-5 $1.25, claude-sonnet-5 $3.75, claude-new-9 unknown)"
        in shown
    )


def test_each_agent_is_read_from_its_transcript(world):
    _record_run(world)
    _transcripts(world)
    events, summary = _derive(world)

    roles = {a["agent"]: (a["role"], a["card"]) for a in summary["agents"]}
    assert roles == {
        "orchestrator": ("orchestrator", None),
        WORKER: ("worker", "sys-b6"),
        HELPER: ("helper", "sys-b6"),
    }
    orchestrator, worker, helper = summary["agents"]
    assert orchestrator["cost_usd"] == pytest.approx(1.25)
    assert orchestrator["longest_idle_s"] >= 300
    assert orchestrator["tools"] == {"Agent": 1, "Bash": 1}
    assert worker["cost_usd"] == pytest.approx(3.75)  # its own messages' usage
    assert worker["tools"] == {"Agent": 1, "Bash": 3, "Edit": 1}
    assert (worker["model"], worker["effort"], worker["agent_type"]) == (
        "claude-sonnet-5",
        "high",
        "worker-high",
    )
    assert worker["subagents_spawned"] == 1
    assert helper["cost_usd"] is None  # claude-new-9 has no rates
    assert summary["helpers_spawned_by_workers"] == [
        {"worker": WORKER, "card": "sys-b6", "agent": HELPER, "agent_type": "Explore"}
    ]
    assert summary["prs"] == [{"card": "sys-b6", "url": None, "state": "none"}]
    assert [e["at"] for e in events] == sorted(e["at"] for e in events)


def test_a_usage_limit_stall_gives_its_start_and_reset_in_the_summary(world):
    """sys-u10 (wake 7): the account's five-hour limit stalled every agent for 13 min
    20 s, and the run record's `usage_limit` stayed null, so nothing on the board
    showed it. Two of the 27 `rate_limit_error` records the orchestrator's transcript
    held that stall (wake 7 orchestrator transcript, lines 2035 and 2037) give one
    stall, with the earlier record's time as its start."""
    _record_run(world, cards=())
    orch = Transcript(
        datetime(2026, 9, 27, 6, 36, 40, tzinfo=UTC),
        str(world.repo),
        "claude-fable-5-1",
    )
    orch.prompt(f"Your run id is {RUN}.")
    reset_epoch = int(datetime(2026, 9, 27, 6, 50, tzinfo=UTC).timestamp())
    start = orch.api_error(reset_epoch=reset_epoch)
    orch.api_error(reset_epoch=reset_epoch, seconds=30)
    orch.write(world.slug_dir / f"{SESSION}.jsonl")

    _, summary = _derive(world)

    assert summary["stalls"] == [
        {"start": start, "reset": "2026-09-27T06:50:00Z", "agents": ["orchestrator"]}
    ]
    shown = behaviour_log.format_summary(summary)
    assert f"{start} - 2026-09-27T06:50:00Z: orchestrator" in shown


def test_stalls_merge_across_agents_by_reset_time_and_split_on_a_different_one(world):
    """Two agents' transcripts hold a 429 with the same reset time: one stall names
    both. A later 429 with a different reset time is a second, separate stall."""
    _start_run(world, cards=())
    t0 = datetime(2026, 9, 27, 6, 36, 40, tzinfo=UTC)
    reset_a = int(datetime(2026, 9, 27, 6, 50, tzinfo=UTC).timestamp())
    reset_b = int(datetime(2026, 9, 27, 11, 50, tzinfo=UTC).timestamp())
    orch = Transcript(t0, str(world.repo), "claude-fable-5-1")
    orch.prompt(f"Your run id is {RUN}.")
    orch.api_error(reset_epoch=reset_a)
    orch.call(
        "Agent",
        {"prompt": "go"},
        "Async agent launched successfully.",
        meta={"agentId": HELPER, "isAsync": True},
    )
    orch.api_error(reset_epoch=reset_b, seconds=18_000)
    orch.write(world.slug_dir / f"{SESSION}.jsonl")

    helper = Transcript(
        t0 + timedelta(seconds=5), str(world.repo), "claude-sonnet-5", sidechain=True
    )
    helper.prompt("go")
    helper.api_error(reset_epoch=reset_a)
    helper.write(world.slug_dir / SESSION / "subagents" / f"agent-{HELPER}.jsonl")
    _end_run(world)

    _, summary = _derive(world)

    by_reset = {s["reset"]: sorted(s["agents"]) for s in summary["stalls"]}
    assert by_reset == {
        "2026-09-27T06:50:00Z": sorted(["orchestrator", HELPER]),
        "2026-09-27T11:50:00Z": ["orchestrator"],
    }
    assert [s["reset"] for s in summary["stalls"]] == [
        "2026-09-27T06:50:00Z",
        "2026-09-27T11:50:00Z",
    ]  # earliest first


def _meta(w, agent: str, meta: dict) -> None:
    (w.slug_dir / SESSION / "subagents" / f"agent-{agent}.meta.json").write_text(
        json.dumps(meta)
    )


def _fork_transcripts(w) -> float:
    """sys-s62's shape in wake 6: the orchestrator spawns the worker; the worker runs the
    code-review skill, whose fork's meta names the worker (`parentAgentId`) and no tool
    call; the fork spawns a finder on Haiku at depth 3. Returns the total claude prints
    for these tokens."""
    t0 = datetime(2026, 9, 22, 7, 53, tzinfo=UTC)
    card_file = f"{w.root}/projects/system/cards/sys-b6.json"
    folder = w.slug_dir / SESSION / "subagents"
    orch = Transcript(t0, str(w.repo), "claude-opus-5")
    orch.prompt(f"Read docs/orchestrator.md and run one wake. Your run id is {RUN}.")
    spawn = orch.call(
        "Agent",
        {"subagent_type": "worker-high", "prompt": f"Your card is `{card_file}`."},
        "Async agent launched successfully.",
        meta={"agentId": WORKER, "isAsync": True},
        usage={"output_tokens": 50_000},  # $1.25 at claude-opus-5's rates
    )
    orch.write(w.slug_dir / f"{SESSION}.jsonl")

    worker = Transcript(
        t0 + timedelta(seconds=10), str(w.repo), "claude-sonnet-5", sidechain=True
    )
    worker.prompt(f"Your card is `{card_file}`.")
    worker.call(  # the Skill call's result names no agent
        "Skill",
        {"skill": "code-review", "args": "medium"},
        usage={"output_tokens": 100_000},  # $1.00 at claude-sonnet-5's rates
    )
    worker.write(folder / f"agent-{WORKER}.jsonl")
    _meta(w, WORKER, {"agentType": "worker-high", "toolUseId": spawn, "spawnDepth": 1})

    fork = Transcript(
        t0 + timedelta(seconds=20), str(w.repo), "claude-sonnet-5", sidechain=True
    )
    fork.prompt("Review target: the card's PR, medium")
    finder_call = fork.call(
        "Agent",
        {
            "description": "Angle A line-by-line diff scan",
            "subagent_type": "worker-high",
            "model": "haiku",
            "run_in_background": True,
            "prompt": "find the bugs",
        },
        "Async agent launched successfully.",
        usage={"output_tokens": 40_000},  # $0.40 at claude-sonnet-5's rates
    )
    fork.write(folder / f"agent-{FORK}.jsonl")
    fork_meta = {
        "agentType": "general-purpose",
        "parentAgentId": WORKER,
        "spawnDepth": 2,
    }
    _meta(w, FORK, fork_meta)  # a fork's meta names no tool call

    finder = Transcript(
        t0 + timedelta(seconds=30),
        str(w.repo),
        "claude-haiku-4-5-20251001",
        sidechain=True,
    )
    finder.prompt("find the bugs")
    finder.call(
        "Read",
        {"file_path": f"{w.repo}/src/cli.py"},
        usage={"output_tokens": 200_000},  # $1.00 at claude-haiku-4-5's rates
    )
    finder.write(folder / f"agent-{FINDER}.jsonl")
    _meta(
        w,
        FINDER,
        {
            "agentType": "worker-high",
            "toolUseId": finder_call,
            "parentAgentId": FORK,
            "spawnDepth": 3,
            "model": "haiku",
        },
    )
    return 1.25 + 1.00 + 0.40 + 1.00


def test_skill_forks_and_agents_at_any_depth_are_read_and_haiku_is_priced(world):
    """sys-s75: wake 6's log listed 23 agents and missed sys-s62's two code-review forks
    and the ten Haiku finders they spawned at depth 3, $2.12 in all. A fork's meta names
    no tool call, so a walk from the orchestrator's spawn calls never reached the forks
    or their finders; and Haiku had no rates, so `by_model` summed to $54.56 against the
    run's $56.75."""
    _start_run(world, cards=())
    total = _fork_transcripts(world)
    run_end(world.root, RUN, result={"total_cost_usd": total}, summary="s")

    _, summary = _derive(world)

    rows = {a["agent"]: a for a in summary["agents"]}
    assert list(rows) == ["orchestrator", WORKER, FORK, FINDER]
    assert [
        (rows[key]["role"], rows[key]["card"], rows[key]["parent"])
        for key in (FORK, FINDER)
    ] == [("helper", "sys-b6", WORKER), ("helper", "sys-b6", FORK)]
    assert (rows[FORK]["agent_type"], rows[FINDER]["model"]) == (
        "general-purpose",
        "claude-haiku-4-5-20251001",
    )
    assert [rows[key]["subagents_spawned"] for key in (WORKER, FORK)] == [1, 1]
    assert summary["cost"]["unpriced_models"] == []
    assert abs(sum(summary["cost"]["by_model"].values()) - total) < 0.01
    shown = behaviour_log.format_summary(summary)
    assert f"helper sys-b6 ({FINDER}): worker-high claude-haiku-4-5-20251001" in shown


def test_phases_iterations_decisions_and_board_writes_come_from_the_card_histories(
    world,
):
    _start_run(world, cards=())
    _start(world)
    card_progress(world.root, "sys-b6", phase="testing", iteration=0, run_id=RUN)
    card_progress(world.root, "sys-b6", phase="ci", last_step=PROSE, run_id=RUN)
    card_progress(world.root, "sys-b6", phase="writing", iteration=1, run_id=RUN)
    card_edit(world.root, "sys-b6", test="t2", note=PROSE, run_id=RUN)  # attempts reset
    card_progress(world.root, "sys-b6", iteration=1, next_step=PROSE, run_id=RUN)
    card_ask(
        world.root,
        "sys-b6",
        question=f"Which API token should the bot use? {PROSE}",
        options=["a", "b"],
        brief=PROSE,
        recommendation="a",
        run_id=RUN,
    )
    card_answer(world.root, "sys-b6", answer="a", run_id="telegram")
    _end_run(world)

    events, summary = _derive(world)  # no transcript: the board's rows still show

    assert summary["reason"] == "transcript unavailable"
    assert [(t["from"], t["to"], t["means"]) for t in summary["transitions"]] == [
        ("todo", "in_progress", "card start"),
        ("in_progress", "decision_needed", "card ask"),
    ]  # the human's answer on Telegram is not the run's
    assert [p["phase"] for p in summary["phases"]] == ["testing", "ci", "writing"]
    assert [(i["iteration"], i["cause"]) for i in summary["iterations"]] == [
        (1, "check"),  # raised while the card was in ci
        (1, "other"),  # raised again after the new test reset the attempts
    ]
    assert [(d["card"], d["kind"], d["answered_by"]) for d in summary["decisions"]] == [
        ("sys-b6", "credential", "telegram")
    ]
    assert summary["board_writes"] == [
        {"means": "card ask", "count": 1, "cards": ["sys-b6"]},
        {"means": "card edit", "count": 1, "cards": ["sys-b6"]},
        {"means": "card progress", "count": 4, "cards": ["sys-b6"]},
        {"means": "card start", "count": 1, "cards": ["sys-b6"]},
    ]
    assert [e["event"] for e in events].count("iteration") == 2
    text = json.dumps([events, summary])
    assert PROSE not in text and "API token" not in text


@pytest.mark.parametrize(
    ("row", "command"),
    [
        (("manual", None, "backlog", "created via card new"), "card new"),
        (("r", "backlog", "todo", "x"), "card move"),
        (("r", "todo", "in_progress", "started"), "card start"),
        (
            ("r", "in_progress", "in_progress", "resumed; previous claim was crashed"),
            "card start",
        ),
        (("r", "in_progress", "in_progress", "progress: phase=ci"), "card progress"),
        (("r", "in_progress", "in_progress", "edited: goal"), "card edit"),
        (("r", "todo", "todo", "a note alone"), "card edit"),
        (("r", "in_progress", "decision_needed", "asked: q"), "card ask"),
        (("telegram", "decision_needed", "done", "answered: a"), "card answer"),
        (("r", "in_progress", "done", "finished: u"), "card finish"),
        (("r", "todo", "cancelled", "why"), "card cancel"),
        (
            (
                "r",
                "done",
                "done",
                "retargeted: base_branch a -> main (its base merged)",
            ),
            "follow-up",
        ),
        (
            ("r", "done", "done", "worktree removed (/w): its PR merged"),
            "worktree cleanup",
        ),
        (
            ("telegram", "done", "done", "reply to answered decision d1: ok"),
            "notify poll",
        ),
        (
            (
                "telegram",
                "cancelled",
                "cancelled",
                "reply to cancelled decision d1: ok",
            ),
            "notify poll",
        ),
        (
            (
                "telegram",
                "backlog",
                "todo",
                "reply to answered decision d1: go ahead",
            ),
            "notify poll",  # sys-t35: a go-ahead reply's row leaves the stack, too
        ),
        (("r", "done", "in_progress", "x"), "no command"),
    ],
)
def test_each_history_row_names_the_command_that_writes_it(row, command):
    run_id, from_, to, note = row
    entry = HistoryEntry("2026-09-22T07:00:00Z", run_id, from_, to, note)
    assert behaviour_log.history_command(entry) == command


def test_a_resumed_session_is_read_from_the_prompt_that_names_the_run(world):
    _record_run(world)
    t0 = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
    orch = Transcript(t0, str(world.repo), "claude-opus-5")
    orch.prompt(
        f"Read docs/orchestrator.md and run one wake. Your run id is {OLD_RUN}."
    )
    old_spawn = orch.call(
        "Agent",
        {"prompt": "your card is `/b/cards/sys-b6.json`"},
        meta={"agentId": WORKER},
    )
    orch.bash("uv run autonomouse card show sys-b6")
    orch.wait(3600)
    orch.prompt(
        f"Your session stopped before its wake ended (run {OLD_RUN}), and the run "
        f"controller has resumed it as run {RUN}. Your run id is now {RUN}: pass it on "
        "every write."
    )
    orch.bash("uv run autonomouse card progress sys-b6 --phase ci")
    orch.write(world.slug_dir / f"{SESSION}.jsonl")
    worker = Transcript(t0, str(world.repo), "claude-sonnet-5", sidechain=True)
    worker.call("Read", {"file_path": f"{world.repo}/x.py"})
    worker.write(world.slug_dir / SESSION / "subagents" / f"agent-{WORKER}.jsonl")
    _meta(world, WORKER, {"toolUseId": old_spawn})
    # A skill the orchestrator itself runs forks an agent whose meta names neither a
    # tool call nor a parent: one in each run's part.
    for key, seconds in ((OLD_FORK, 60), (FORK, 3606)):
        fork = Transcript(
            t0 + timedelta(seconds=seconds),
            str(world.repo),
            "claude-sonnet-5",
            sidechain=True,
        )
        fork.call("Read", {"file_path": f"{world.repo}/y.py"})
        fork.write(world.slug_dir / SESSION / "subagents" / f"agent-{key}.jsonl")
        _meta(world, key, {"agentType": "general-purpose", "spawnDepth": 1})

    _, summary = _derive(world)
    assert [(a["agent"], a["parent"]) for a in summary["agents"]] == [
        ("orchestrator", None),
        (FORK, "orchestrator"),
    ]  # the old run's worker and fork are not this run's
    assert summary["agents"][0]["tools"] == {"Bash": 1}
    assert summary["agents"][0]["started_at"].startswith("2026-09-22T08:00:")


@pytest.mark.parametrize("transcript", [None, "not json\n{also not\n"])
def test_an_unreadable_transcript_gives_a_summary_with_the_reason(world, transcript):
    _start_run(world)
    _wake_on_board(world)
    _end_run(world)
    if transcript is not None:
        world.slug_dir.mkdir(parents=True)
        (world.slug_dir / f"{SESSION}.jsonl").write_text(transcript)
    path = behaviour_log.write(world.root, RUN, pr_state=lambda url: "open")
    summary = json.loads(path.read_text())
    assert summary["reason"] == "transcript unavailable"
    assert summary["agents"] == [] and summary["run_id"] == RUN
    assert [t["to"] for t in summary["transitions"]] == ["in_progress", "done"]
    events = (path.parent / f"{RUN}.jsonl").read_text().splitlines()
    assert sorted(json.loads(e)["event"] for e in events) == [
        "phase",
        "phase",
        "transition",
        "transition",
    ]  # the board's rows only: no transcript was read
    assert path.parent == world.tmp / "logs"
    shown = behaviour_log.format_summary(summary)
    assert "no transcript read: transcript unavailable" in shown
    assert "sys-b6: in_progress -> done by card finish" in shown


def test_a_wake_without_a_session_says_so(world):
    _record_run(world, session=None)
    _, summary = _derive(world)
    assert summary["reason"] == "no session started"
    assert summary["transitions"] == [] and summary["agents"] == []


def test_log_show_prints_the_summary_and_log_derive_writes_it(world):
    _start_run(world, cards=())
    _start(world)
    _end_run(world)
    t0 = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
    orch = Transcript(t0, str(world.repo), "claude-opus-5")
    orch.prompt(f"Your run id is {RUN}.")
    orch.bash("uv run autonomouse card show sys-b6")
    orch.write(world.slug_dir / f"{SESSION}.jsonl")
    env = dict(os.environ, AUTONOMOUSE_HOME=str(world.root))

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", "log", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    missing = cli("show", RUN)
    assert missing.returncode == 1 and "log derive" in missing.stderr
    derived = cli("derive", RUN)
    assert derived.returncode == 0, derived.stderr
    assert derived.stdout.strip() == str(world.tmp / "logs" / f"{RUN}.summary.json")
    shown = cli("show", RUN)
    assert shown.returncode == 0, shown.stderr
    assert "orchestrator: - claude-opus-5 high" in shown.stdout
    assert "sys-b6: todo -> in_progress by card start" in shown.stdout
    assert "  card start: 1 (sys-b6)" in shown.stdout
    as_json = json.loads(cli("show", RUN, "--json").stdout)
    assert as_json["transitions"][0]["to"] == "in_progress"


def test_records_in_other_shapes_are_read_as_far_as_they_go(world):
    """Transcripts are Claude Code's format: a record whose fields have other shapes is
    read around, and never stops the derivation."""
    _record_run(world, cards=())
    t0 = datetime(2026, 9, 22, 7, 0, tzinfo=UTC)
    orch = Transcript(t0, str(world.repo), "claude-opus-5")
    orch.prompt(f"Your run id is {RUN}.")
    orch.bash("uv run autonomouse card progress sys-b6 --phase ci")
    orch.call("MultiEdit", {"file_path": f"{world.root}/runs/{RUN}.json", "edits": 3})
    odd = [
        {
            "type": "user",
            "message": f"Your run id is {RUN}.",
            "timestamp": "2026-09-22T07:00:09Z",
        },
        {
            "type": "assistant",
            "message": {"model": {"x": 1}, "id": [1]},
            "timestamp": 12345,
        },
        {
            "type": "assistant",
            "message": {"content": 7},
            "timestamp": "also-not-a-timestamp",
        },
        {
            "type": "assistant",
            "message": {"content": []},
            "timestamp": "2026-09-22T07:00:10",
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-22T07:00:11Z",
            "cwd": ["/x"],
            "message": {
                "content": [{"type": "tool_use", "id": {"a": 1}, "name": ["Bash"]}]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-22T07:00:12Z",
            "toolUseResult": "text",
            "message": {"content": [{"type": "tool_result", "tool_use_id": {"a": 1}}]},
        },
        ["not", "an", "object"],
    ]
    orch.records += odd
    orch.write(world.slug_dir / f"{SESSION}.jsonl")
    meta_dir = world.slug_dir / SESSION / "subagents"
    meta_dir.mkdir(parents=True)
    (meta_dir / f"agent-{WORKER}.jsonl").write_text('{"type": "user", "message": 5}\n')
    (meta_dir / f"agent-{WORKER}.meta.json").write_text(
        json.dumps({"toolUseId": {"x": 1}})
    )

    _, summary = _derive(world)
    assert summary["reason"] is None and summary["skipped_lines"] == 1
    assert summary["agents"][0]["tools"] == {"Bash": 1, "MultiEdit": 1}
    assert summary["phases"] == []  # a command in a transcript writes nothing


def _jev_entries(w, run_id: str = RUN) -> None:
    run = load_run(w.root, run_id)
    run.jev = [
        {
            "question": "go",
            "card": None,
            "source": "fallback",
            "answer": "go",
            "confidence": 0.25,
            "applied": "skip",
            "reason": f"confidence 0.25 below floor 0.60 {PROSE}",
        },
        {
            "question": "tier",
            "card": "sys-b6",
            "source": "orchestrator",
            "answer": "sonnet/high",
            "confidence": None,
            "applied": "sonnet/high",
            "reason": None,
        },
    ]
    save_run(w.root, run)


def _impossible_times(w) -> None:
    """sys-b6 as the sys-a1 rehearsal left it: RUN asked a decision with a made-up time
    later than its own end, and the human's answer, written at the true time, sits after
    it in the history; a second card has a row of another run that is ahead of RUN."""
    card = card_show(w.root, "sys-b6")
    card.history = [
        HistoryEntry("2026-09-22T06:20:11Z", "seed", None, "todo", "x"),
        HistoryEntry("2026-09-22T06:37:01Z", RUN, "todo", "in_progress", "x"),
        HistoryEntry(
            "2026-09-22T06:53:30Z", RUN, "in_progress", "decision_needed", "x"
        ),
        HistoryEntry("2026-09-22T06:47:29Z", "human", "decision_needed", "todo", "x"),
        HistoryEntry("2026-09-22T06:48:02Z", OLD_RUN, "todo", "in_progress", "x"),
    ]
    card.decisions = [
        Decision(
            "sys-b6-d1",
            f"Which token? {PROSE}",
            ["a"],
            None,
            "2026-09-22T06:53:30Z",
            "a",
            "2026-09-22T06:47:29Z",
        )
    ]
    save_card(w.root / "projects" / "system" / "cards" / "sys-b6.json", card)
    card_new(w.root, card_id="sys-b7", project="system", title="t", goal="g", test="t")
    other = card_show(w.root, "sys-b7")
    other.history = [
        HistoryEntry("2026-09-22T06:20:11Z", "seed", None, "todo", "x"),
        HistoryEntry("2026-09-22T09:00:00Z", OLD_RUN, "todo", "todo", "x"),
    ]
    save_card(w.root / "projects" / "system" / "cards" / "sys-b7.json", other)


def test_the_log_carries_jev_answers_and_times_that_cannot_be_true(world):
    _record_run(world, cards=())
    _jev_entries(world)
    _impossible_times(world)
    orch = Transcript(
        datetime(2026, 9, 22, 6, 37, tzinfo=UTC), str(world.repo), "claude-opus-5"
    )
    orch.prompt(f"Your run id is {RUN}.")
    orch.bash("uv run autonomouse card show sys-b6")
    orch.write(world.slug_dir / f"{SESSION}.jsonl")

    path = behaviour_log.write(world.root, RUN, pr_state=lambda url: "open")
    summary = json.loads(path.read_text())
    assert summary["jev"] == [
        {
            "question": "go",
            "card": None,
            "source": "fallback",
            "answer": "go",
            "confidence": 0.25,
            "applied": "skip",
        },
        {
            "question": "tier",
            "card": "sys-b6",
            "source": "orchestrator",
            "answer": "sonnet/high",
            "confidence": None,
            "applied": "sonnet/high",
        },
    ]
    assert summary["clock"] == [
        {
            "class": "out_of_order",
            "card": "sys-b6",
            "at": "2026-09-22T06:53:30Z",
            "run_id": RUN,
            "next_at": "2026-09-22T06:47:29Z",
            "next_run_id": "human",
        },
        {
            "class": "ahead_of_run_clock",
            "card": "sys-b6",
            "at": "2026-09-22T06:53:30Z",
            "to": "decision_needed",
            "last_message": "2026-09-22T06:37:03.000Z",
        },
        {
            "class": "answered_before_asked",
            "card": "sys-b6",
            "decision": "sys-b6-d1",
            "asked_at": "2026-09-22T06:53:30Z",
            "answered_at": "2026-09-22T06:47:29Z",
        },
    ]  # OLD_RUN's rows are in order on sys-b6, and its clock is not RUN's on sys-b7
    assert PROSE not in path.read_text()
    shown = behaviour_log.format_summary(summary)
    assert "(wake) go: fallback answered go, applied skip" in shown
    assert "decision sys-b6-d1 answered 2026-09-22T06:47:29Z" in shown
    assert "sys-b6 sys-b6-d1: credential, answered by human" in shown


def test_a_wake_without_a_session_keeps_its_jev_answers_and_board_times(world):
    _record_run(world, session=None, cards=())
    _jev_entries(world)
    _impossible_times(world)
    _, summary = _derive(world)
    assert summary["reason"] == "no session started"
    assert [j["question"] for j in summary["jev"]] == ["go", "tier"]
    assert [c["class"] for c in summary["clock"]] == [
        "out_of_order",
        "answered_before_asked",
    ]  # no transcript, no clock to be ahead of
    shown = behaviour_log.format_summary(summary)
    assert "no transcript read: no session started" in shown and "sys-b6 tier" in shown


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


def test_the_behaviour_review_reads_the_derived_log_and_the_boards_git_history(world):
    """End to end: the sys-b6 wake's derived log, its board's cards and the board's
    commits, reviewed; the hand edit of the card's `pr` shows, the commands' writes do
    not."""
    _git(world.root, "init", "-q")
    _git(world.root, "add", "-A")
    _git(world.root, "commit", "-q", "-m", "seed")
    _start_run(world)
    _jev_entries(world)
    _wake_on_board(world)
    _git(world.root, "add", "-A")
    _git(world.root, "commit", "-q", "-m", f"run {RUN}: sys-b6 done")
    card = card_show(world.root, "sys-b6")
    card.pr = "https://github.com/t/t/pull/17"
    save_card(world.root / "projects" / "system" / "cards" / "sys-b6.json", card)
    _git(world.root, "commit", "-q", "-am", "the pr, by hand")  # in the run's time
    _end_run(world)
    _transcripts(world)
    behaviour_log.write(world.root, RUN, pr_state=lambda url: "open")

    text = behaviour_review.write_review(
        world.root, 1, now=datetime(2026, 9, 22, 12, tzinfo=UTC)
    ).read_text()

    assert f"| {RUN} | sys-b6 | " in text.split("## Cards done", 1)[1]
    edits = text.split("### Hand edits of cards", 1)[1].split("###", 1)[0]
    assert f"| {RUN} | sys-b6 |" in edits and "| pr |" in edits
    assert edits.count(f"| {RUN} |") == 1  # the commands' writes are not hand edits
    assert f"| hand edits of cards | 1 | {RUN} sys-b6 |" in text
    assert f"| {RUN} | sys-b6 | mergeable |" not in text  # gh's state when derived
    assert f"| {RUN} | sys-b6 | open | https://github.com/t/t/pull/17 |" in text
    assert f"| {RUN} | (wake) | go | fallback | go | 0.25 | skip |" in text
    assert "claude-opus-5 $1.25, claude-sonnet-5 $3.75, claude-new-9 unknown" in text
    assert PROSE not in text


def test_a_board_that_cannot_be_read_leaves_its_rows_unchecked(world):
    _record_run(world, session=None, cards=())
    (world.root / "projects" / "system" / "cards" / "sys-b8.json").write_text("{")
    _, summary = _derive(world)
    assert summary["clock"] is None and summary["transitions"] is None
    shown = behaviour_log.format_summary(summary)
    assert shown.count("not checked: the board could not be read") == 6


def test_the_doc_lists_each_section_of_the_review_and_the_rows_dropped():
    """docs/behaviour-log.md says where each row comes from: it names every section the
    review writes, and the rows only the shell reader could fill."""
    doc = (
        Path(__file__).resolve().parent.parent / "docs" / "behaviour-log.md"
    ).read_text()
    now = datetime(2026, 9, 22, 12, tzinfo=UTC)
    text = behaviour_review.build_review(
        [], cards=[], edits=[], days=1, since=now, now=now, folder=Path("/logs")
    )
    titles = [line[3:] for line in text.splitlines() if line.startswith("## ")]
    assert len(titles) == 9
    for title in titles:
        assert f"\n| {title}" in doc, title
    for dropped in (
        "file writes by location",
        "test and lint runs",
        "sleep and poll calls",
    ):
        assert dropped in doc


def test_the_shell_reader_is_gone():
    """sys-s56 (decision H, part 2): no row reads a shell command, so the module that
    read them is deleted."""
    package = Path(behaviour_log.__file__).parent
    assert "shell" not in {m.name for m in pkgutil.iter_modules([str(package)])}
