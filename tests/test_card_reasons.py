"""sys-b16: every judgment on a card is a history row with a reason the command demands,
and every transition on the board is one the schema's table names.

`card edit` refuses a new goal, limits, test or size without `--note`; `card new --note`
writes the creation row `created via card new; why: <text>`; every card starts in
`backlog` by the creation transition `null → backlog`; the run id
`human` retires, and a follow-up names a run by its record's `kind`; `run start` makes
its own run id when given none.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.board.commands import (
    CommandError,
    card_answer,
    card_ask,
    card_edit,
    card_new,
    card_show,
    project_new,
)
from autonomouse.board.commands import cards as cards_module
from autonomouse.board.runs import run_path, run_start
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card

RUN = "20260927-010203-ab12"


def _board(root: Path) -> None:
    project_new(
        root,
        slug="widgets",
        name="Widgets",
        goal="g",
        repo="https://github.com/test/widgets",
        repo_path="/home/test/widgets",
        default_branch="main",
        priority=1,
    )
    card_new(root, card_id="wid-a1", project="widgets", title="t", goal="g", test="t")


# ------------------------------------------------------------- card edit --


@pytest.mark.parametrize(
    "field",
    [
        {"goal": "new goal"},
        {"limits": ["never touch prod"]},
        {"test": "new test"},
        {"size": "m"},
    ],
    ids=["goal", "limits", "test", "size"],
)
def test_an_edit_of_the_plan_without_a_note_is_refused(tmp_path, field):
    _board(tmp_path)
    before = card_show(tmp_path, "wid-a1").to_dict()

    with pytest.raises(CommandError, match="--note"):
        card_edit(tmp_path, "wid-a1", **field)

    assert card_show(tmp_path, "wid-a1").to_dict() == before


def test_an_edit_of_a_missing_card_reports_the_card_first(tmp_path):
    _board(tmp_path)
    with pytest.raises(CommandError, match="no card"):
        card_edit(tmp_path, "wid-a9", goal="g")


def test_an_edit_of_the_plan_with_a_note_lands_with_its_why(tmp_path):
    _board(tmp_path)
    edited = card_edit(tmp_path, "wid-a1", size="m", note="two parts")
    assert edited.size.value == "m"
    assert edited.history[-1].note == "edited: size; why: two parts"


@pytest.mark.parametrize(
    "field",
    [
        {"title": "new title"},
        {"position": 4},
        {"depends_on": ["wid-a0"]},
        {"files": ["src/x.py"]},
    ],
    ids=["title", "position", "depends_on", "files"],
)
def test_an_edit_outside_the_plan_needs_no_note(tmp_path, field):
    _board(tmp_path)
    card_new(tmp_path, card_id="wid-a0", project="widgets", title="t", goal="g", test="t")

    edited = card_edit(tmp_path, "wid-a1", **field)

    [(name, value)] = field.items()
    assert getattr(edited, name) == value
    assert edited.history[-1].note == f"edited: {name}"


# -------------------------------------------------------------- card new --


def test_card_new_with_a_note_writes_the_reason_on_the_creation_row(tmp_path):
    _board(tmp_path)
    card = card_new(
        tmp_path,
        card_id="wid-a2",
        project="widgets",
        title="t",
        goal="g",
        test="t",
        note="split from wid-a1: too big",
    )
    [row] = card.history
    assert (row.from_, row.to) == (None, "backlog")
    assert row.note == "created via card new; why: split from wid-a1: too big"


def test_card_new_without_a_note_writes_created_via_card_new(tmp_path):
    _board(tmp_path)
    [row] = card_show(tmp_path, "wid-a1").history
    assert row.note == "created via card new"


# ------------------------------------------------------ who answered it --


@pytest.fixture
def sent(monkeypatch) -> list[str]:
    """Every text the board commands send to Telegram, in order."""
    texts: list[str] = []
    monkeypatch.setattr(cards_module, "notify", lambda text, **_: texts.append(text))
    return texts


def _asked(root: Path) -> None:
    _board(root)
    path = root / "projects" / "widgets" / "cards" / "wid-a1.json"
    card = load_card(path)
    card.stack = Stack.IN_PROGRESS
    save_card(path, card)
    card_ask(
        root,
        "wid-a1",
        brief="the brief",
        question="which?",
        options=["a", "b"],
        recommendation="a",
    )


def _record(root: Path, kind: str | None) -> None:
    """The run record `RUN`, holding `kind` in its JSON unless `kind` is None (as a
    record written before `kind` existed)."""
    run_start(root, run_id=RUN, session_id="s", pid=1, model="opus")
    path = run_path(root, RUN)
    data = json.loads(path.read_text())
    if kind is None:
        data.pop("kind", None)
    else:
        data["kind"] = kind
    path.write_text(json.dumps(data))


def test_an_answer_with_the_retired_run_id_human_names_only_an_agent(tmp_path, sent):
    _asked(tmp_path)
    card_answer(tmp_path, "wid-a1", answer="a", run_id="human")
    assert sent[-1] == (
        "<b>Answered: which?</b>\nAnswer: a\nAnswered by: an agent\n"
        "The card is now in the top of todo."
    )


@pytest.mark.parametrize(
    ("kind", "who"),
    [
        ("session", "an agent session acting for you"),
        ("planner", "the planner"),
        ("wake", "the orchestrator"),
        (None, "the orchestrator"),
    ],
    ids=["session", "planner", "wake", "no-kind"],
)
def test_an_answer_names_the_run_by_its_records_kind(tmp_path, sent, kind, who):
    _asked(tmp_path)
    _record(tmp_path, kind)
    card_answer(tmp_path, "wid-a1", answer="a", run_id=RUN)
    assert sent[-1] == (
        f"<b>Answered: which?</b>\nAnswer: a\nAnswered by: {who}\n"
        "The card is now in the top of todo."
    )


def test_an_answer_with_an_orchestrator_shaped_id_and_no_record_names_the_orchestrator(
    tmp_path, sent
):
    # No run record at all is the common case for a wake's own answer: the id's shape
    # alone is enough, without loading a record.
    _asked(tmp_path)
    card_answer(tmp_path, "wid-a1", answer="a", run_id=RUN)
    assert sent[-1] == (
        "<b>Answered: which?</b>\nAnswer: a\nAnswered by: the orchestrator\n"
        "The card is now in the top of todo."
    )


# ------------------------------------------------------------- the shell --


def _cli(*args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def env(tmp_path: Path) -> dict:
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        CLAUDE_CONFIG_DIR=str(tmp_path / "claude"),
    )
    _board(tmp_path / "board")
    return env


def test_card_edit_of_the_goal_without_a_note_exits_1_naming_note(env):
    result = _cli("card", "edit", "wid-a1", "--goal", "x", env=env)
    assert result.returncode == 1
    assert "--note" in result.stderr


def test_card_new_with_a_note_writes_the_row_through_the_shell(env):
    result = _cli(
        "card", "new", "--id", "wid-a2", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", "--note", "split from sys-x: cause", "--json",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    [row] = json.loads(result.stdout)["history"]
    assert row["note"] == "created via card new; why: split from sys-x: cause"


def test_run_start_without_an_id_makes_one_that_run_status_finds(env):
    started = _cli(
        "run", "start", "--model", "opus", "--session-id", "s", "--pid", "1", env=env
    )
    assert started.returncode == 0, started.stderr
    match = re.match(r"run (\d{8}-\d{6}-[0-9a-f]{4}): ", started.stdout)
    assert match, started.stdout

    status = _cli("run", "status", match.group(1), env=env)
    assert status.returncode == 0, status.stderr
