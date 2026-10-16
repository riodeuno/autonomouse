"""sys-s12: `autonomouse card new` puts a card in `todo`; `--backlog` puts it in `backlog`,
which means set aside for the human to decide later. In the human's words (2026-09-26):
"Cards by default, go into todo right? backlog, only if I mark them as (I will decide
later, or put it in the backlog)". The stack is the command's choice: Python callers of
`card_new` keep `backlog` unless they pass the stack, so `review improve` still files its
proposed instruction changes in `backlog`, where the human judges them first.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.board.commands import CommandError, card_new, project_new
from test_review_improve import _rehearsal_logs, _write_review


def _cli(*args: str, env: dict) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result


@pytest.fixture
def env(tmp_path: Path) -> dict:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _cli(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    return env


def _new(card_id: str, *extra: str, env: dict) -> dict:
    made = _cli(
        "card", "new", "--id", card_id, "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", "--run-id", "plan-1", *extra, "--json", env=env,
    )
    return json.loads(made.stdout)


def _todo_ids(env: dict) -> list[str]:
    listed = _cli(
        "card", "list", "--project", "widgets", "--stack", "todo", "--json", env=env
    )
    return [c["id"] for c in json.loads(listed.stdout)]


def test_card_new_through_the_shell_puts_the_card_in_todo(env):
    card = _new("wid-a1", env=env)

    assert card["stack"] == "todo"
    [row] = card["history"]
    assert (row["from"], row["to"], row["run_id"]) == (None, "todo", "plan-1")
    assert row["note"] == "created via card new"
    assert _todo_ids(env) == ["wid-a1"]


def test_card_new_backlog_sets_the_card_aside_until_a_move_makes_it_ready(env):
    card = _new("wid-a1", "--backlog", env=env)

    assert card["stack"] == "backlog"
    assert [(h["from"], h["to"]) for h in card["history"]] == [(None, "backlog")]
    assert _todo_ids(env) == []

    moved = _cli("card", "move", "wid-a1", "--to", "todo", "--json", env=env)
    assert json.loads(moved.stdout)["stack"] == "todo"
    assert _todo_ids(env) == ["wid-a1"]


def test_card_new_note_is_the_creation_rows_note(env):
    # A split's child starts in todo with no `card move`, so the note naming its parent
    # and the cause rides on its `card new` (docs/orchestrator/splitting-and-replanning.md).
    card = _new("wid-a1", "--note", "split from wid-a0: too big", env=env)

    [row] = card["history"]
    assert (row["from"], row["to"], row["note"]) == (
        None, "todo", "created via card new; why: split from wid-a0: too big"
    )

    blank = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "card", "new", "--id", "wid-a2",
         "--project", "widgets", "--title", "t", "--goal", "g", "--test", "t",
         "--note", " "],
        env=env, capture_output=True, text=True, check=False,
    )
    assert blank.returncode != 0
    assert "--note is blank" in blank.stderr


def test_card_new_in_python_keeps_backlog_unless_the_caller_names_the_stack(tmp_path):
    root = tmp_path / "board"
    project_new(root, slug="widgets", name="W", goal="g", repo="r", repo_path="p",
                default_branch="main", priority=1)

    kept = card_new(root, card_id="wid-a1", project="widgets", title="t", goal="g",
                    test="t")
    named = card_new(root, card_id="wid-a2", project="widgets", title="t", goal="g",
                     test="t", stack="todo")

    assert (kept.stack.value, kept.history[0].to) == ("backlog", "backlog")
    assert (named.stack.value, named.history[0].to) == ("todo", "todo")
    with pytest.raises(CommandError, match="backlog or todo"):
        card_new(root, card_id="wid-a3", project="widgets", title="t", goal="g",
                 test="t", stack="in_progress")


def test_review_improve_still_files_its_proposals_in_backlog(tmp_path):
    board = tmp_path / "board"
    project_new(board, slug="system", name="System", goal="g", repo="r",
                repo_path=str(tmp_path / "repo"), default_branch="main", priority=1)
    review = _write_review(board, _rehearsal_logs())
    env = dict(
        os.environ, AUTONOMOUSE_HOME=str(board), AUTONOMOUSE_CONFIG=str(tmp_path / "cfg")
    )

    _cli("review", "improve", "--review", str(review), env=env)

    listed = _cli("card", "list", "--project", "system", "--json", env=env)
    filed = [c for c in json.loads(listed.stdout) if c["title"].startswith("improve: ")]
    assert filed
    for card in filed:
        assert card["stack"] == "backlog"
        assert card["history"][0]["to"] == "backlog"


def test_card_new_reads_backlog_as_a_flag_without_a_value(env):
    # `--backlog` takes no value: the flag after it still writes its field to the card.
    _cli(
        "card", "new", "--backlog", "--files", "a.py", "--id", "wid-a1",
        "--project", "widgets", "--title", "t", "--goal", "g", "--test", "t", env=env,
    )  # fmt: skip

    cards = Path(env["AUTONOMOUSE_HOME"]) / "projects" / "widgets" / "cards"
    card = json.loads((cards / "wid-a1.json").read_text())
    assert (card["id"], card["stack"], card["files"]) == ("wid-a1", "backlog", ["a.py"])
