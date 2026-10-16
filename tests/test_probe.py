"""Tests for the probe runner (autonomouse.probe) with a stub claude.

The stub records its argv, working folder and whether it saw the token in its
environment, then prints the stream-json events of $FAKE_DIR/events, one per line. An
event line `WAIT` holds the stub until $FAKE_DIR/go exists (10 seconds at most, then it
writes $FAKE_DIR/timed-out): the test's `say` makes that file on the first step line, so
the stub only goes on when the runner streamed that line while claude was still running.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.home import Home
from autonomouse.probe import EFFORT, MODEL, ProbeError, run_probe

TOKEN = "ghp_probeTestTokenValue0123456789"

FAKE_CLAUDE = f"""#!{sys.executable}
import json, os, sys, time
d = os.environ["FAKE_DIR"]
json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(),
           "token_in_env": os.environ.get("GH_TOKEN") == {TOKEN!r}}},
          open(os.path.join(d, "call.json"), "w"))
for line in open(os.path.join(d, "events")).read().splitlines():
    if line == "WAIT":
        end = time.time() + 10
        while not os.path.exists(os.path.join(d, "go")):
            if time.time() > end:
                open(os.path.join(d, "timed-out"), "w").close()
                break
            time.sleep(0.02)
        continue
    print(line, flush=True)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""

CHARTER = "# Probe: fake service\n\nCheck the fake permissions.\n"


def _text(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )


def _tool() -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
        }
    )


def _result(text: str, is_error: bool = False) -> str:
    return json.dumps({"type": "result", "is_error": is_error, "result": text})


def _report(*permissions: dict) -> str:
    return json.dumps({"permissions": list(permissions)})


OK_PUSH = {"name": "push", "required": True, "status": "ok", "fix": ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    fake.mkdir()
    claude = fake / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    home = tmp_path / "home"
    (home / "docs" / "probes").mkdir(parents=True)
    (home / "docs" / "probes" / "fake.md").write_text(CHARTER)
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.setenv("FAKE_DIR", str(fake))
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    monkeypatch.delenv("FAKE_EXIT", raising=False)

    class Env:
        def events(self, *lines: str) -> None:
            (fake / "events").write_text("\n".join(lines) + "\n")

        def say(self, line: str) -> None:
            self.said.append(line)
            (fake / "go").touch()

        def run(self):
            return run_probe(
                "fake",
                cwd=clone,
                facts={"repository": "owner/name", "branch": "develop"},
                home=Home(home, "autonomouse"),
                say=self.say,
                claude=str(claude),
            )

        def call(self) -> dict:
            return json.loads((fake / "call.json").read_text())

    e = Env()
    e.said = []
    e.fake, e.clone = fake, clone
    return e


def test_the_runner_starts_claude_with_sonnet_high_effort_and_the_charter(env):
    env.events(_result(_report(OK_PUSH)))
    env.run()
    call = env.call()
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == MODEL == "sonnet"
    assert argv[argv.index("--effort") + 1] == EFFORT == "high"
    assert "--dangerously-skip-permissions" in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    prompt = argv[argv.index("-p") + 1]
    assert CHARTER.strip() in prompt
    assert "- repository: owner/name" in prompt
    assert "- branch: develop" in prompt
    assert Path(call["cwd"]) == env.clone


def test_the_step_lines_stream_in_order_while_the_agent_works(env):
    env.events(
        _text("STEP: I am creating a throwaway pull request; it will be removed"),
        "WAIT",
        _tool(),
        _text("thinking aloud, not a step\nSTEP: Checking that CI checks can be read"),
        _text(
            "STEP: Closing the pull request\nSTEP: Removed the branch; nothing changed"
        ),
        _text(_report(OK_PUSH)),
        _result(_report(OK_PUSH)),
    )
    env.run()
    assert not (env.fake / "timed-out").exists(), (
        "the first step reached say only at the end"
    )
    assert env.said == [
        "I am creating a throwaway pull request; it will be removed",
        "Checking that CI checks can be read",
        "Closing the pull request",
        "Removed the branch; nothing changed",
    ]


def test_the_final_report_is_parsed(env):
    env.events(_result("```json\n" + _report(OK_PUSH) + "\n```"))
    report = env.run()
    assert [(p.name, p.required, p.status) for p in report.permissions] == [
        ("push", True, "ok")
    ]
    assert report.blocking == () and report.warnings == ()


def test_a_missing_required_permission_blocks_with_its_fix_and_an_optional_one_warns(
    env,
):
    fix = "Grant Pull requests read and write on the token"
    env.events(
        _result(
            _report(
                OK_PUSH,
                {
                    "name": "pull_requests",
                    "required": True,
                    "status": "missing",
                    "fix": fix,
                },
                {
                    "name": "cancel_run",
                    "required": False,
                    "status": "missing",
                    "fix": "x",
                },
            )
        )
    )
    report = env.run()
    assert [(p.name, p.fix) for p in report.blocking] == [("pull_requests", fix)]
    assert [p.name for p in report.warnings] == ["cancel_run"]


def test_a_session_that_ends_without_a_report_is_a_failure_that_says_so(env):
    env.events(_text("STEP: Starting"), _result("I could not finish."))
    with pytest.raises(ProbeError, match="ended without a report"):
        env.run()


def test_a_session_that_prints_no_result_is_a_failure_that_says_so(env, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "1")
    env.events(_text("STEP: Starting"))
    with pytest.raises(ProbeError, match=r"ended without a report \(claude exited 1"):
        env.run()


def test_the_token_in_the_environment_appears_in_no_argv_and_no_said_line(env):
    env.events(_text("STEP: Checking access"), _result(_report(OK_PUSH)))
    env.run()
    call = env.call()
    assert call["token_in_env"], "the session gets the token through its environment"
    assert not any(TOKEN in arg for arg in call["argv"])
    assert not any(TOKEN in line for line in env.said)


# ---------------------------------------------------------------- Linear (sys-w15) --
#
# `autonomouse project edit <slug> --tracker <url>` run as the owner runs it, a process
# whose stdout is read line by line, with the stub claude above first on PATH. Each test
# fails on card/sys-w26, where a link is saved without any probe.

LINEAR_PROJECT = "https://linear.app/acme/project/widgets-launch-5b1a2c3d4e5f"
LINEAR_ISSUE = "https://linear.app/acme/issue/WID-42/ship-the-widget-page"
REPO = Path(__file__).resolve().parents[1]
MCP_FIX = (
    "connect Linear in Claude Code: run `claude`, type `/mcp`, choose the Linear server "
    "and log in"
)
CREATE_FIX = (
    "the Linear account the MCP is logged in as may not create issues in Widgets: make it "
    "a member"
)


def _linear(name: str, status: str = "ok", fix: str = "") -> dict:
    return {"name": name, "required": True, "status": status, "fix": fix}


def _linear_report(notes: list[str], **changed: dict) -> str:
    names = ("linear_mcp", "read_link", "create_issue", "remove_test_issue")
    return json.dumps(
        {
            "permissions": [changed.get(n, _linear(n)) for n in names],
            "notes": notes,
        }
    )


@pytest.fixture
def board(env, tmp_path, monkeypatch):
    """A board with the project `widgets`, unlinked, whose checkout is `env.clone`; the
    stub claude is `claude` on PATH."""
    from autonomouse.board.commands import project_new

    root = tmp_path / "board"
    root.mkdir()
    project_new(
        root, slug="widgets", name="Widgets", goal="g", repo="https://github.com/t/w",
        repo_path=str(env.clone), default_branch="main", priority=1,
    )  # fmt: skip
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("PATH", f"{env.fake}:{os.environ['PATH']}")
    return root


def _edit(tracker: str, go: Path | None = None) -> tuple[int, list[str]]:
    """Run `project edit widgets --tracker <tracker>`; return its exit code and output
    lines. With `go`, touch it once a line from the probe's agent has been read, which lets
    the stub claude, held at `WAIT`, go on."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "autonomouse.cli", "project", "edit", "widgets",
         "--tracker", tracker],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO,
    )  # fmt: skip
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        if go is not None and line.startswith("linear: Created the test issue"):
            go.touch()
    return proc.wait(), lines


def _tracker(board: Path) -> dict | None:
    return json.loads((board / "projects" / "widgets" / "project.json").read_text())[
        "tracker"
    ]


def test_linear_step_lines_reach_the_terminal_while_the_probe_runs(env, board):
    env.events(
        _text("STEP: Reading the Linear project Widgets launch"),
        _text("STEP: Created the test issue WID-57 in Widgets launch"),
        "WAIT",
        _text("STEP: Deleting the test issue WID-57"),
        _result(_linear_report(["Deleted the test issue WID-57"])),
    )
    code, lines = _edit(LINEAR_PROJECT, go=env.fake / "go")

    assert code == 0, lines
    assert not (env.fake / "timed-out").exists(), (
        "the step line reached the terminal only after the probe ended"
    )
    steps = [
        "linear: Reading the Linear project Widgets launch",
        "linear: Created the test issue WID-57 in Widgets launch",
        "linear: Deleting the test issue WID-57",
        "linear: Deleted the test issue WID-57",
        f"linear: the agents can read {LINEAR_PROJECT} and create issues there",
    ]
    assert [line for line in lines if line in steps] == steps


def test_the_runner_starts_the_linear_probe_with_its_charter_on_sonnet_at_high_effort(
    env, board
):
    env.events(_result(_linear_report(["Deleted the test issue WID-57"])))
    code, lines = _edit(LINEAR_ISSUE)

    assert code == 0, lines
    call = env.call()
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"
    prompt = argv[argv.index("-p") + 1]
    charter = (REPO / "docs" / "probes" / "linear.md").read_text()
    assert charter.strip() in prompt
    assert "Only through the Linear MCP" in charter
    assert f"- link: {LINEAR_ISSUE}" in prompt
    assert "- link points at: issue" in prompt
    assert re.search(
        r"^- test issue title: autonomouse access check \d{8}T\d{6}Z-[0-9a-f]{8}$",
        prompt,
        re.MULTILINE,
    )
    assert Path(call["cwd"]) == env.clone


def test_a_report_of_success_stores_the_link_and_prints_how_the_issue_was_removed(
    env, board
):
    note = "The Linear MCP has no delete or archive; moved the test issue WID-57 to Canceled"
    env.events(_result(_linear_report([note])))
    code, lines = _edit(LINEAR_PROJECT)

    assert code == 0, lines
    assert f"linear: {note}" in lines
    assert f"tracker: linear project {LINEAR_PROJECT}" in lines
    assert _tracker(board) == {"kind": "linear", "url": LINEAR_PROJECT, "link": "project"}


@pytest.mark.parametrize(
    ("changed", "fix"),
    [
        (
            {
                "linear_mcp": _linear("linear_mcp", "missing", MCP_FIX),
                "read_link": _linear("read_link", "unchecked", "the Linear MCP is not connected"),
                "create_issue": _linear("create_issue", "unchecked", "the Linear MCP is not connected"),
                "remove_test_issue": _linear("remove_test_issue", "unchecked", "the Linear MCP is not connected"),
            },
            MCP_FIX,
        ),
        (
            {
                "create_issue": _linear("create_issue", "missing", CREATE_FIX),
                "remove_test_issue": _linear("remove_test_issue", "unchecked", "no test issue was created"),
            },
            CREATE_FIX,
        ),
    ],
    ids=["missing-linear-mcp", "refused-write"],
)  # fmt: skip
def test_a_missing_linear_mcp_or_a_refused_write_stores_no_link_and_prints_its_fix(
    env, board, changed, fix
):
    env.events(_result(_linear_report([], **changed)))
    code, lines = _edit(LINEAR_PROJECT)

    assert code != 0, lines
    assert f"fix: {fix}" in lines
    assert any("the link is not saved" in line for line in lines)
    assert _tracker(board) is None


def test_none_clears_the_link_with_no_probe(env, board):
    env.events(_result(_linear_report([])))
    assert _edit(LINEAR_PROJECT)[0] == 0
    (env.fake / "call.json").unlink()

    code, lines = _edit("none")

    assert code == 0, lines
    assert _tracker(board) is None
    assert not (env.fake / "call.json").exists()
