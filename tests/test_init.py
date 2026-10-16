"""`autonomouse init`, the first-board wizard, run through the shell the way an agent
runs it: with no question and no terminal. Each test points
`AUTONOMOUSE_HOME` at a board folder that does not exist yet and `AUTONOMOUSE_CONFIG`
at an empty folder, and git at an empty global config, as on a fresh machine. Projects
are `autonomouse project new`'s (tests/test_project_wizard.py); here one is made with
its `--repo-path` flags, the path that records an existing checkout.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    global_git = tmp_path / "gitconfig"
    global_git.write_text("")
    return dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        AUTONOMOUSE_CONFIG=str(tmp_path / "config"),
        GIT_CONFIG_GLOBAL=str(global_git),
        GIT_CONFIG_NOSYSTEM="1",
    )


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _projects(env: dict[str, str]) -> list[dict]:
    listed = _run("project", "list", "--json", env=env)
    assert listed.returncode == 0, listed.stderr
    return json.loads(listed.stdout)


def _git(board: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=board, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_fresh_board_asks_nothing(env) -> None:
    board = Path(env["AUTONOMOUSE_HOME"])

    result = _run("init", env=env)

    assert result.returncode == 0, result.stderr
    assert _git(board, "rev-parse", "--is-inside-work-tree") == "true"
    assert _git(board, "rev-parse", "--show-toplevel") == str(board)
    assert _git(board, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(board, "log", "--format=%s") == "autonomouse init"
    assert _git(board, "status", "--porcelain") == ""
    assert _projects(env) == []
    assert "create one with: autonomouse project new <name>" in result.stdout
    # A fresh machine has no git author; the board repository gets its own.
    assert _git(board, "config", "--local", "user.email") == "autonomouse@localhost"


def test_a_project_made_with_repo_path_is_listed_and_not_recreated(env) -> None:
    assert _run("init", env=env).returncode == 0
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "Build widgets.",
        "--repo", "https://github.com/test/widgets", "--repo-path", "/srv/widgets",
        "--default-branch", "main",
        "--priority", "2", env=env,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr

    again = _run("init", env=env)

    assert again.returncode == 0, again.stderr
    assert "projects: widgets" in again.stdout
    [project] = _projects(env)
    assert (project["slug"], project["priority"]) == ("widgets", 2)


def test_a_second_run_with_an_export_imports_it(env, tmp_path) -> None:
    # A board somewhere else, exported to one file.
    other = dict(env, AUTONOMOUSE_HOME=str(tmp_path / "other-board"))
    made = _run(
        "project", "new", "--slug", "gadgets", "--name", "Gadgets", "--goal", "g",
        "--repo", "https://github.com/test/gadgets", "--repo-path", "/srv/gadgets",
        "--default-branch", "main",
        "--priority", "5", env=other,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    card = _run(
        "card", "new", "--id", "gad-a1", "--project", "gadgets", "--title", "t",
        "--goal", "g", "--test", "t", "--size", "s", env=other,
    )  # fmt: skip
    assert card.returncode == 0, card.stderr
    export = tmp_path / "export.json"
    assert _run("board", "export", "--out", str(export), env=other).returncode == 0

    first = _run("init", env=env)
    assert first.returncode == 0, first.stderr
    widgets = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "w",
        "--repo", "https://github.com/test/widgets", "--repo-path", "/srv/widgets",
        "--default-branch", "main",
        "--priority", "1", env=env,
    )  # fmt: skip
    assert widgets.returncode == 0, widgets.stderr

    second = _run("init", "--import", str(export), env=env)

    assert second.returncode == 0, second.stderr
    assert "1 project(s) and 1 card(s) created" in second.stdout
    assert [p["slug"] for p in _projects(env)] == ["widgets", "gadgets"]
    shown = _run("card", "show", "gad-a1", "--json", env=env)
    assert json.loads(shown.stdout)["project"] == "gadgets"
    board = Path(env["AUTONOMOUSE_HOME"])
    assert _git(board, "status", "--porcelain") == ""
    assert _git(board, "log", "--format=%s").splitlines() == ["autonomouse init"] * 2

    # The same import again writes nothing and still asks nothing.
    third = _run("init", "--import", str(export), env=env)
    assert third.returncode == 0, third.stderr
    assert "1 project(s) and 1 card(s) already on the board" in third.stdout
    assert "board commit: nothing to commit" in third.stdout


def test_a_bad_export_file_writes_nothing(env, tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"projects": [{"slug": "x"}], "cards": []}))

    result = _run("init", "--import", str(bad), env=env)

    assert result.returncode == 1
    assert _projects(env) == []
