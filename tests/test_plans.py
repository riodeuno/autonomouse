"""Planning threads (`autonomouse plan …`) on a temp board with a fake planner."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys

import pytest

from autonomouse import planner
from autonomouse.board.commands import CommandError, project_new
from autonomouse.board.plans import (
    LEFT_TEXT,
    ThreadStatus,
    load_thread,
    thread_add_cards,
    thread_close,
    thread_join,
    thread_open,
    thread_path,
    thread_reply,
    thread_say,
)
from autonomouse.config import load_config
from autonomouse.home import Home
from autonomouse.jev.client import ChoiceAnswer, JevRequestError

VAGUE = "make decisions easier to answer"
SPECIFIED = (
    "Goal: the decision screen shows the recommendation first. Limits: no new "
    "dependencies. Test: open a decision card and see the recommendation on top."
)

# A fake `claude -p`: stands for the planner. Like claude, `--resume <id>` fails when no
# transcript exists for that session id. It records its arguments in args.<n> (JSON) and the
# background-wait-ceiling env var the planner sets (env-ceiling), writes a
# transcript for its session, then reads the thread through the CLI and answers through the
# CLI only: while the request is the vague one and fewer than two questions were asked, it
# asks the next question; otherwise it writes the cards (two for the vague request, built
# from the human's answers, one for the specified one), records them, and closes the thread.
# With FAKE_LATE set, the human's `plan reply` "late" lands while it runs, and it then says
# "answer" without having read it.
FAKE_PLANNER = f"""#!{sys.executable}
import glob, json, os, re, subprocess, sys

fake = os.environ["FAKE_DIR"]
n = len(glob.glob(os.path.join(fake, "args.*"))) + 1
with open(os.path.join(fake, f"args.{{n}}"), "w") as f:
    json.dump(sys.argv[1:], f)
with open(os.path.join(fake, "env-ceiling"), "w") as f:
    f.write(os.environ.get("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", "unset"))
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
resume = "--resume" in args
sid = args[args.index("--resume" if resume else "--session-id") + 1]
transcripts = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "-fake")
transcript = os.path.join(transcripts, sid + ".jsonl")
if resume and not os.path.exists(transcript):
    print("No conversation found with session ID: " + sid, file=sys.stderr)
    sys.exit(1)
os.makedirs(transcripts, exist_ok=True)
open(transcript, "a").write("{{}}\\n")

def cli(*a):
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *a],
        check=True, capture_output=True, text=True,
    ).stdout

thread_id = re.search(r"plan-\\d{{8}}-\\d{{6}}-[0-9a-f]{{4}}", prompt).group(0)
thread = json.loads(cli("plan", "show", thread_id, "--json"))
if os.environ.get("FAKE_LATE"):
    # the human's terminal reply lands while this session runs, before its answer
    cli("plan", "reply", thread_id, "late")
    cli("plan", "say", thread_id, "answer")
    print(json.dumps({{"type": "result", "is_error": False, "result": "ok"}}))
    sys.exit(0)
request = thread["messages"][0]["text"]
asked = [m for m in thread["messages"] if m["from"] == "planner"]
answers = [m["text"] for m in thread["messages"][1:] if m["from"] == "human"]
if "{VAGUE}" in request and len(asked) < 2:
    cli("plan", "say", thread_id, f"question {{len(asked) + 1}}: what should change?")
else:
    prefix = "v" if "{VAGUE}" in request else "s"
    count = 2 if prefix == "v" else 1
    ids = [f"t-{{prefix}}{{i}}" for i in range(1, count + 1)]
    for i, card_id in enumerate(ids):
        goal = answers[i] if prefix == "v" else request
        cli("card", "new", "--id", card_id, "--project", "t", "--title", "planned",
            "--goal", goal, "--test", "see the goal")
    cli("plan", "cards", thread_id, *ids)
    cli("plan", "close", thread_id, "wrote " + ", ".join(ids))
print(json.dumps({{"type": "result", "is_error": False, "result": "ok"}}))
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
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
    claude = fake_dir / "claude"
    claude.write_text(FAKE_PLANNER)
    claude.chmod(0o755)
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "planner.md").write_text("# planner\n")
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("FAKE_DIR", str(fake_dir))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    # Cleared, not just left as it is: this test's own process may itself be running
    # inside a session that already carries this var (a worker of a `claude -p` planner
    # or orchestrator session), which would make the ceiling assertion pass by accident
    # of the ambient environment instead of the planner's own env dict.
    monkeypatch.delenv("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", raising=False)

    class Env:
        pass

    e = Env()
    e.root, e.fake_dir, e.tmp, e.lines = root, fake_dir, tmp_path, []
    # the defaults, Jev off: every session runs at the `complex` tier; a test that turns
    # Jev on sets `e.ask`, the fake Jev request
    e.config = load_config(tmp_path / "no-config.toml")
    e.ask = None

    def step():
        return planner.step(
            root,
            config=e.config,
            ask=e.ask,
            claude=str(claude),
            home=Home(repo, "autonomouse-cli"),
            secrets=tmp_path / ".env",
            state_dir=tmp_path / "state",
            out=e.lines.append,
        )

    e.step = step
    return e


def _cli(*args: str) -> str:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _args(env, n: int) -> list[str]:
    return json.loads((env.fake_dir / f"args.{n}").read_text())


def _calls(env) -> int:
    return len(list(env.fake_dir.glob("args.*")))


def _open(text: str) -> str:
    return json.loads(_cli("plan", "open", "t", text, "--json"))["id"]


def _card_goal(env, card_id: str) -> str:
    path = env.root / "projects" / "t" / "cards" / f"{card_id}.json"
    return json.loads(path.read_text())["goal"]


def test_a_vague_request_closes_after_two_questions_with_two_cards(env):
    thread_id = _open(VAGUE)
    thread = load_thread(env.root, thread_id)
    assert thread.status is ThreadStatus.OPEN and thread.session_id is None

    assert env.step() == 0
    first = _args(env, 1)
    session = first[first.index("--session-id") + 1]
    assert first[first.index("--model") + 1] == "fable"
    assert first[first.index("--effort") + 1] == "high"
    assert VAGUE in first[first.index("-p") + 1]
    assert "docs/planner.md" in first[first.index("-p") + 1]
    assert f"plan brief {thread_id} <file>" in first[first.index("-p") + 1]
    thread = load_thread(env.root, thread_id)
    assert thread.session_id == session
    assert thread.status is ThreadStatus.WAITING_HUMAN
    assert thread.messages[-1].text.startswith("question 1")

    _cli("plan", "reply", thread_id, "show the recommendation first")
    assert load_thread(env.root, thread_id).status is ThreadStatus.WAITING_PLANNER
    assert env.step() == 0
    second = _args(env, 2)
    assert second[second.index("--resume") + 1] == session
    assert "show the recommendation first" in second[second.index("-p") + 1]
    assert load_thread(env.root, thread_id).messages[-1].text.startswith("question 2")

    _cli("plan", "reply", thread_id, "say what each option does")
    assert env.step() == 0
    third = _args(env, 3)
    assert third[third.index("--resume") + 1] == session

    thread = load_thread(env.root, thread_id)
    assert thread.status is ThreadStatus.CLOSED
    assert thread.cards_written == ["t-v1", "t-v2"]
    assert _card_goal(env, "t-v1") == "show the recommendation first"
    assert _card_goal(env, "t-v2") == "say what each option does"
    assert [m.from_.value for m in thread.messages] == [
        "human",
        "planner",
        "human",
        "planner",
        "human",
        "planner",
    ]
    assert thread.messages[-1].text == "wrote t-v1, t-v2"


def test_a_specified_request_closes_without_questions(env):
    thread_id = _open(SPECIFIED)
    assert env.step() == 0
    assert _calls(env) == 1
    thread = load_thread(env.root, thread_id)
    assert thread.status is ThreadStatus.CLOSED
    assert thread.cards_written == ["t-s1"]
    assert [m.from_.value for m in thread.messages] == ["human", "planner"]


def test_plan_step_gives_its_claude_session_no_background_wait_ceiling(env):
    """sys-j7: the `claude -p` session `plan step` starts gets
    CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0, as the wake's orchestrator session does
    (autonomouse.controller), so unattended mode's 600s background-task kill never ends a
    planner's sub-agent mid-probe."""
    _open(SPECIFIED)
    assert env.step() == 0
    assert (env.fake_dir / "env-ceiling").read_text().strip() == "0"


def test_a_session_that_cannot_be_resumed_gets_a_fresh_one_from_the_thread(env):
    thread_id = _open(VAGUE)
    env.step()
    old = load_thread(env.root, thread_id).session_id
    (env.tmp / "claude-config" / "projects" / "-fake" / f"{old}.jsonl").unlink()

    _cli("plan", "reply", thread_id, "show the recommendation first")
    assert env.step() == 0

    failed, fresh = _args(env, 2), _args(env, 3)
    assert failed[failed.index("--resume") + 1] == old
    new = fresh[fresh.index("--session-id") + 1]
    assert new != old
    prompt = fresh[fresh.index("-p") + 1]
    assert "cannot be resumed" in prompt
    assert VAGUE in prompt and "question 1" in prompt
    assert "show the recommendation first" in prompt
    thread = load_thread(env.root, thread_id)
    assert thread.session_id == new
    assert thread.messages[-1].text.startswith("question 2")
    assert any(f"resume of session {old} failed" in line for line in env.lines)


def test_a_reply_written_while_the_planner_runs_is_answered_by_the_next_step(
    env, monkeypatch
):
    thread_id = _open(VAGUE)
    monkeypatch.setenv("FAKE_LATE", "1")
    assert env.step() == 0
    thread = load_thread(env.root, thread_id)
    assert [m.text for m in thread.messages[1:]] == ["late", "answer"]
    assert [m.text for m in thread.unanswered()] == ["late"]

    monkeypatch.delenv("FAKE_LATE")
    assert env.step() == 0
    resumed = _args(env, 2)
    assert resumed[resumed.index("--resume") + 1] == thread.session_id
    assert "late" in resumed[resumed.index("-p") + 1]
    assert load_thread(env.root, thread_id).unanswered() == []


def test_step_with_nothing_pending_does_nothing(env, tmp_path, monkeypatch):
    thread_id = _open(SPECIFIED)
    env.step()
    before = thread_path(env.root, thread_id).read_bytes()
    calls = _calls(env)

    assert env.step() == 0
    assert env.lines[-1] == "no thread waits for the planner"

    # The CLI's own step, with the config and state folders moved to temp ones.
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AUTONOMOUSE_STATE", str(tmp_path / "state-cli"))
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "autonomouse.cli",
            "plan",
            "step",
            "--claude",
            str(env.fake_dir / "claude"),
        ],
        capture_output=True,
        text=True,
        env=os.environ,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    # the poll runs first; with no chat id configured it reads nothing and says why
    assert done.stdout.splitlines() == [
        (
            "poll: no notify.telegram_chat_id is configured; run `autonomouse notify "
            "setup` first (docs/config.md)"
        ),
        "no thread waits for the planner",
    ]
    assert _calls(env) == calls
    assert thread_path(env.root, thread_id).read_bytes() == before


def test_plan_step_with_telegram_off_polls_nothing_and_prints_nothing_about_it(
    env, tmp_path, monkeypatch
):
    """`notify.telegram = false` is a choice: the CLI's step runs no poll, so the plan
    job's log, written every five minutes, says nothing about Telegram."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.toml").write_text("[notify]\ntelegram = false\n")
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config))
    monkeypatch.setenv("AUTONOMOUSE_STATE", str(tmp_path / "state-cli"))

    done = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "plan", "step"],
        capture_output=True,
        text=True,
        env=os.environ,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["no thread waits for the planner"]
    assert done.stderr == ""


def test_a_thread_with_nobody_waiting_on_the_planner_is_not_stepped(env):
    waiting = _open(VAGUE)
    thread_say(env.root, waiting, "a question")  # the planner already asked
    closed = _open(VAGUE)
    thread_close(env.root, closed)
    assert env.step() == 0
    assert _calls(env) == 0


def test_the_record_refuses_writes_it_cannot_hold(env):
    with pytest.raises(CommandError, match="no project"):
        thread_open(env.root, "nope", "x")
    thread = thread_open(env.root, "t", "x")
    with pytest.raises(CommandError, match="empty"):
        thread_reply(env.root, thread.id, "  ")
    with pytest.raises(CommandError, match="no card"):
        thread_add_cards(env.root, thread.id, ["t-z9"])
    thread_close(env.root, thread.id, "done")
    with pytest.raises(CommandError, match="closed"):
        thread_reply(env.root, thread.id, "more")
    data = json.loads(thread_path(env.root, thread.id).read_text())
    assert set(data) == {
        "id",
        "project",
        "opened_at",
        "session_id",
        "status",
        "joined",
        "messages_given",
        "messages",
        "cards_written",
        "tiers",
    }
    assert data["messages"][-1] == {
        "at": data["messages"][-1]["at"],
        "from": "planner",
        "text": "done",
    }



# ------------------------------------------------------------ the planner's tier --
#
# Before it starts or resumes a planner session, `plan step` asks Jev what kind of session it
# is (complex, simple or collect-user-intent); the config's `planner` table maps the kind to
# the session's --model and --effort, and the thread's `tiers` records the answer and tier.


def _jev(pick: str, confidence: float, seen: list | None = None):
    """A fake Jev request that answers `planner_session` with `pick` at `confidence`, and
    records the state, the questions and the key it saw in `seen`."""

    def ask(state, questions, *, timeout):
        if seen is not None:
            seen.append((state, questions, os.environ.get("TYPESAFE_API_KEY")))
        return {
            "planner_session": ChoiceAnswer(
                pick=pick, probabilities={pick: confidence}, confidence=confidence
            )
        }

    return ask


def _jev_on(env) -> None:
    env.config["jev"]["enabled"] = True


def _tier(argv: list[str]) -> str:
    return f"{argv[argv.index('--model') + 1]}/{argv[argv.index('--effort') + 1]}"


@pytest.mark.parametrize(
    ("kind", "tier"),
    [
        ("complex", "fable/high"),
        ("simple", "opus/xhigh"),
        ("collect-user-intent", "opus/medium"),
    ],
)
def test_the_session_runs_at_the_tier_jev_s_kind_maps_to(env, kind, tier):
    _jev_on(env)
    (env.tmp / ".env").write_text("TYPESAFE_API_KEY=the-key\n")
    seen: list = []
    env.ask = _jev(kind, 0.9, seen)
    thread_id = _open(VAGUE)

    assert env.step() == 0
    assert _tier(_args(env, 1)) == tier
    [(state, questions, key)] = seen
    assert list(questions) == ["planner_session"]
    assert key == "the-key"  # the secrets file is loaded for the request
    assert state["session"] == "new" and state["new_messages"] == [VAGUE]
    [entry] = load_thread(env.root, thread_id).tiers
    assert entry["source"] == "jev" and entry["reason"] is None
    assert (entry["answer"], entry["confidence"]) == (kind, 0.9)
    assert (entry["applied"], entry["tier"]) == (kind, tier)
    assert f"{thread_id}: a {kind} session, on {tier}" in env.lines[0]

    # the resumed session is judged again, on the human's reply
    _cli("plan", "reply", thread_id, "show the recommendation first")
    env.ask = _jev("simple", 0.8, seen)
    assert env.step() == 0
    assert _tier(_args(env, 2)) == "opus/xhigh"
    assert seen[-1][0]["session"] == "resume"
    assert seen[-1][0]["new_messages"] == ["show the recommendation first"]
    assert [t["applied"] for t in load_thread(env.root, thread_id).tiers] == [kind, "simple"]


def _fails(state, questions, *, timeout):
    raise JevRequestError(503, "unavailable")


@pytest.mark.parametrize(
    ("jev_on", "ask", "reason"),
    [
        (True, _jev("simple", 0.3), "confidence 0.30 below floor 0.50"),
        (True, _fails, "Jev request failed"),
        (False, None, "jev.enabled is off"),
    ],
    ids=["low-confidence", "jev-fails", "jev-off"],
)
def test_the_session_runs_at_the_complex_tier_when_jev_cannot_judge(env, jev_on, ask, reason):
    env.config["jev"]["enabled"] = jev_on
    env.ask = ask
    thread_id = _open(VAGUE)

    assert env.step() == 0
    assert _tier(_args(env, 1)) == "fable/high"
    [entry] = load_thread(env.root, thread_id).tiers
    assert entry["source"] == "fallback" and reason in entry["reason"]
    assert (entry["applied"], entry["tier"]) == ("complex", "fable/high")


def test_jev_judges_the_reply_written_while_the_planner_ran(env, monkeypatch):
    """The merge of sys-v7 with `messages_given`: the reply that landed while the last
    session ran, before its answer, is among the messages Jev reads as the next session's."""
    thread_id = _open(VAGUE)
    monkeypatch.setenv("FAKE_LATE", "1")
    assert env.step() == 0
    monkeypatch.delenv("FAKE_LATE")
    _jev_on(env)
    seen: list = []
    env.ask = _jev("simple", 0.9, seen)
    assert env.step() == 0
    [(state, _questions, _key)] = seen
    assert state["session"] == "resume" and state["new_messages"] == ["late"]
    assert load_thread(env.root, thread_id).tiers[-1]["applied"] == "simple"


def test_the_config_s_planner_table_sets_each_kind_s_tier(env):
    _jev_on(env)
    env.config["planner"]["collect-user-intent"] = "sonnet/high"
    env.ask = _jev("collect-user-intent", 0.9)
    _open(VAGUE)
    assert env.step() == 0
    assert _tier(_args(env, 1)) == "sonnet/high"


def test_a_bad_planner_table_runs_no_planner(env):
    env.config["planner"]["simple"] = "opus"
    _open(VAGUE)
    with pytest.raises(ValueError, match="planner.simple must be <model>/<effort>"):
        env.step()
    assert _calls(env) == 0


# ------------------------------------------------------------------ live join --

# A fake interactive `claude`: stands for the human's live session. It records its
# arguments and its pid in live.json, the thread as it stands while it runs, and what a
# `plan step` run from another terminal does meanwhile; with FAKE_LIVE_CLOSE set, the
# planner closes the thread in the session.
FAKE_LIVE = f"""#!{sys.executable}
import json, os, re, subprocess, sys

def cli(*a):
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *a],
        check=True, capture_output=True, text=True,
    ).stdout

args = sys.argv[1:]
thread_id = re.search(r"plan-\\d{{8}}-\\d{{6}}-[0-9a-f]{{4}}", args[-1]).group(0)
during = json.loads(cli("plan", "show", thread_id, "--json"))
step = subprocess.run(
    [sys.executable, "-m", "autonomouse.cli", "plan", "step",
     "--claude", os.path.join(os.environ["FAKE_DIR"], "claude")],
    capture_output=True, text=True,
)
if os.environ.get("FAKE_LIVE_CLOSE"):
    cli("plan", "close", thread_id, "closed live")
with open(os.path.join(os.environ["FAKE_DIR"], "live.json"), "w") as f:
    json.dump({{"argv": args, "pid": os.getpid(), "during": during,
               "step": step.stdout, "step_code": step.returncode}}, f)
"""


@pytest.fixture
def live(env, tmp_path, monkeypatch):
    fake = env.fake_dir / "live-claude"
    fake.write_text(FAKE_LIVE)
    fake.chmod(0o755)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    monkeypatch.setenv("AUTONOMOUSE_STATE", str(tmp_path / "state-cli"))

    def run(*args: str) -> dict:
        done = subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", "plan", *args, "--claude", str(fake)],
            capture_output=True,
            text=True,
            env=os.environ,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return json.loads((env.fake_dir / "live.json").read_text())

    return run


def test_join_resumes_the_session_live_and_plan_step_leaves_it_alone(env, live):
    thread_id = _open(VAGUE)
    env.step()  # the planner asks question 1
    session = load_thread(env.root, thread_id).session_id
    _cli("plan", "reply", thread_id, "show the recommendation first")
    calls = _calls(env)

    seen = live("join", thread_id)

    argv = seen["argv"]
    assert argv[argv.index("--resume") + 1] == session
    assert "--session-id" not in argv and "-p" not in argv
    assert seen["during"]["status"] == "human_joined"
    # claude runs only once the claim names its pid (planner._EXEC_WHEN_JOINED)
    assert seen["during"]["joined"]["pid"] == seen["pid"]
    assert seen["step_code"] == 0
    assert f"{thread_id}: the human is in it live; left alone" in seen["step"]
    assert _calls(env) == calls  # the step ran no planner during the join

    thread = load_thread(env.root, thread_id)
    assert thread.status is ThreadStatus.WAITING_PLANNER and thread.joined is None
    assert thread.messages[-1].text == LEFT_TEXT
    assert thread.messages_given is None  # the live session read the thread itself

    # the unattended step resumes the same session afterwards
    assert env.step() == 0
    resumed = _args(env, calls + 1)
    assert resumed[resumed.index("--resume") + 1] == session
    assert LEFT_TEXT in resumed[resumed.index("-p") + 1]


def test_plan_new_starts_a_live_session_on_a_new_thread(env, live):
    seen = live("new", "t")
    [thread] = [load_thread(env.root, p.stem) for p in (env.root / "plans").iterdir()]
    argv = seen["argv"]
    assert argv[argv.index("--session-id") + 1] == thread.session_id
    assert "plan new" in argv[-1] and "docs/planner.md" in argv[-1]
    assert _tier(argv) == "fable/high"  # a live session runs at the complex tier
    assert seen["during"]["status"] == "human_joined"
    assert seen["during"]["joined"]["pid"] == seen["pid"]
    assert thread.status is ThreadStatus.WAITING_PLANNER
    assert [m.from_.value for m in thread.messages] == ["human", "human"]


def test_a_thread_the_planner_closed_live_stays_closed(env, live, monkeypatch):
    thread_id = _open(VAGUE)
    monkeypatch.setenv("FAKE_LIVE_CLOSE", "1")
    live("join", thread_id)
    thread = load_thread(env.root, thread_id)
    assert thread.status is ThreadStatus.CLOSED and thread.joined is None
    assert thread.messages[-1].text == "closed live"


def test_a_join_whose_claim_cannot_move_never_runs_claude(env, live, monkeypatch):
    """claude is started first, for its pid, and held until the thread's claim names
    that pid; a claude that ran at once could read the thread with the `plan join`
    process's pid still on it (sys-i12). When the claim cannot move, claude never runs."""
    thread_id = _open(VAGUE)
    started = []
    real_popen = subprocess.Popen

    def popen(*args, **kwargs):
        started.append(real_popen(*args, **kwargs))
        return started[-1]

    def refuse(*args, **kwargs):
        raise CommandError("refused")

    monkeypatch.setattr(planner.subprocess, "Popen", popen)
    monkeypatch.setattr(planner, "thread_join_pid", refuse)
    with pytest.raises(CommandError, match="refused"):
        planner.join(
            env.root,
            thread_id,
            model="m",
            effort="high",
            claude=str(env.fake_dir / "live-claude"),
            home=Home(env.tmp / "repo", "autonomouse-cli"),
            secrets=env.tmp / ".env",
            state_dir=env.tmp / "state",
            out=env.lines.append,
        )

    [held] = started
    assert held.wait() == 1  # the held wrapper ends without running claude
    assert not (env.fake_dir / "live.json").exists()
    assert load_thread(env.root, thread_id).status is ThreadStatus.WAITING_PLANNER


def test_a_live_join_is_a_claim(env, tmp_path):
    thread_id = _open(VAGUE)
    thread_join(env.root, thread_id, os.getpid())  # alive: this test's own pid
    with pytest.raises(CommandError, match="already joined live"):
        thread_join(env.root, thread_id, os.getpid())
    assert env.step() == 0 and _calls(env) == 0

    gone = subprocess.Popen(["true"])
    gone.wait()
    other = _open(VAGUE)
    thread_join(env.root, other, gone.pid)  # its process is gone: a crashed claim
    thread_join(env.root, other, gone.pid)  # so a new join takes it over
    assert env.step() == 0
    assert any(f"{other}: the live join's process is gone" in x for x in env.lines)
    assert _calls(env) == 1  # the released thread got its planner; the live one did not
    assert load_thread(env.root, thread_id).status is ThreadStatus.HUMAN_JOINED


def test_join_is_refused_while_a_plan_step_runs(env, tmp_path):
    thread_id = _open(VAGUE)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    with open(state / "plan-step.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(CommandError, match="plan step is running"):
            planner.join(
                env.root,
                thread_id,
                model="m",
                effort="high",
                claude="false",
                home=Home(tmp_path, "autonomouse-cli"),
                secrets=tmp_path / ".env",
                state_dir=state,
                out=env.lines.append,
            )
    assert load_thread(env.root, thread_id).status is ThreadStatus.OPEN


def test_a_brief_while_a_plan_step_runs_opens_the_thread_for_the_next_step(env, tmp_path):
    """sys-t5: `project new`'s brief step finds `plan-step.lock` held by a plan step. It
    opens the brief thread, whose request names `plan brief`, runs no planner, and
    the next plan step starts the planner on it."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    with open(state / "plan-step.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        thread, ok = planner.brief(
            env.root,
            "t",
            config=env.config,
            claude=str(env.fake_dir / "claude"),
            home=Home(tmp_path / "repo", "autonomouse-cli"),
            secrets=tmp_path / ".env",
            state_dir=state,
            out=env.lines.append,
        )

    assert ok and _calls(env) == 0
    assert thread.status is ThreadStatus.OPEN and thread.session_id is None
    assert "plan brief" in thread.messages[0].text
    assert thread.messages[0].text.endswith(
        "write it with `autonomouse plan brief <thread-id> <file>`."
    )
    assert env.lines == [f"{thread.id}: a plan step is running now; the next one starts the planner"]
    assert env.step() == 0
    assert _calls(env) == 1
    assert load_thread(env.root, thread.id).session_id is not None


def test_the_brief_session_runs_at_the_tier_jev_judges_as_a_plan_step_s_does(env, tmp_path):
    """The merge of sys-v7 with sys-t5: `project new`'s brief runs the planner the way a plan
    step would, so Jev judges its session and the thread's `tiers` records the tier."""
    _jev_on(env)
    seen: list = []
    env.ask = _jev("collect-user-intent", 0.9, seen)
    thread, ok = planner.brief(
        env.root,
        "t",
        config=env.config,
        ask=env.ask,
        claude=str(env.fake_dir / "claude"),
        home=Home(tmp_path / "repo", "autonomouse-cli"),
        secrets=tmp_path / ".env",
        state_dir=tmp_path / "state",
        out=env.lines.append,
    )
    assert ok and _calls(env) == 1
    assert _tier(_args(env, 1)) == "opus/medium"
    [(state, _questions, _key)] = seen
    assert state["session"] == "new" and state["new_messages"] == [planner.BRIEF_TEXT]
    [entry] = thread.tiers
    assert (entry["source"], entry["applied"], entry["tier"]) == (
        "jev",
        "collect-user-intent",
        "opus/medium",
    )


def test_a_thread_written_before_joined_existed_loads(env):
    thread_id = _open(VAGUE)
    path = thread_path(env.root, thread_id)
    data = json.loads(path.read_text())
    del data["joined"]
    path.write_text(json.dumps(data))
    assert load_thread(env.root, thread_id).joined is None


def test_a_thread_written_before_messages_given_existed_keeps_todays_rule(env):
    thread_id = _open(VAGUE)
    thread_say(env.root, thread_id, "question 1")
    thread_reply(env.root, thread_id, "an answer")
    path = thread_path(env.root, thread_id)
    data = json.loads(path.read_text())
    del data["messages_given"]
    path.write_text(json.dumps(data))
    thread = load_thread(env.root, thread_id)
    assert thread.messages_given is None
    assert [m.text for m in thread.unanswered()] == ["an answer"]
