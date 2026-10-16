"""sys-i20: every card carries a `files` list, the files its PR is expected to change, and
`autonomouse card overlaps` reads it to list the open cards that would change a planned
card's files, falling back to the files a card's goal names when its list is empty."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.board.commands import (
    CommandError,
    card_edit,
    card_new,
    card_overlaps,
    card_show,
    project_new,
)
from autonomouse.board.overlaps import card_names, overlaps, path_name, same_file
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, save_card


def _project(root: Path) -> None:
    project_new(
        root,
        slug="widgets",
        name="Widgets",
        goal="Build widgets.",
        repo="https://github.com/test/widgets",
        repo_path="/home/test/widgets",
        default_branch="main",
        priority=1,
    )


def _card(root: Path, card_id: str, goal: str, files: list[str] | None = None):
    return card_new(
        root, card_id=card_id, project="widgets", title=card_id, goal=goal, test="t",
        files=files,
    )  # fmt: skip


def _ids(found) -> list[str]:
    return [card.id for card, _, _ in found]


def test_card_new_stores_files_and_card_edit_replaces_them(tmp_path):
    _project(tmp_path)
    _card(tmp_path, "wid-a1", "g", files=["docs/a.md", "src/autonomouse/cli.py", "docs/a.md"])
    assert card_show(tmp_path, "wid-a1").files == ["docs/a.md", "src/autonomouse/cli.py"]

    edited = card_edit(tmp_path, "wid-a1", files=["docs/b.md"], note="one owner")
    assert edited.files == ["docs/b.md"]
    assert edited.history[-1].note == "edited: files; why: one owner"
    assert card_show(tmp_path, "wid-a1").files == ["docs/b.md"]


@pytest.mark.parametrize("value", ["docs/a.md,docs/b.md", " "])
def test_a_files_value_that_is_blank_or_holds_a_comma_is_refused(tmp_path, value):
    _project(tmp_path)
    with pytest.raises(CommandError, match="--files takes one path and repeats"):
        _card(tmp_path, "wid-a1", "g", files=[value])
    _card(tmp_path, "wid-a1", "g")
    with pytest.raises(CommandError, match="--files takes one path and repeats"):
        card_edit(tmp_path, "wid-a1", files=[value])


def test_a_card_written_before_files_existed_loads_it_as_empty_and_writes_it_back(tmp_path):
    _project(tmp_path)
    _card(tmp_path, "wid-a1", "g")
    path = tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(path.read_text())
    del data["files"]
    path.write_text(json.dumps(data))

    loaded = load_card(path)
    assert loaded.files == []
    save_card(path, loaded)
    assert json.loads(path.read_text())["files"] == []


def test_overlaps_reads_the_files_list_when_a_card_has_one(tmp_path):
    _project(tmp_path)
    # the goal names wake.py, the files list does not: the list wins
    card = _card(tmp_path, "wid-a1", "Change src/autonomouse/jev/wake.py.", files=["docs/a.md"])
    assert card_names(card)[1] == "files"
    assert overlaps(["src/autonomouse/jev/wake.py"], [card]) == []
    assert overlaps(["docs/a.md"], [card]) == [(card, ["docs/a.md"], "files")]


def test_overlaps_falls_back_to_the_goal_text_when_the_files_list_is_empty(tmp_path):
    _project(tmp_path)
    card = _card(tmp_path, "wid-a1", "Rewrite docs/orchestrator.md and autonomouse.jev.wake.")
    assert card_names(card)[1] == "goal"
    found = overlaps(["src/autonomouse/jev/wake.py", "docs/orchestrator.md", "docs/x.md"], [card])
    assert found == [(card, ["src/autonomouse/jev/wake.py", "docs/orchestrator.md"], "goal")]


def test_two_cards_with_disjoint_files_are_independent_even_when_their_goals_name_one_module(
    tmp_path,
):
    _project(tmp_path)
    goal = "Change how autonomouse.jev.wake builds the batch (src/autonomouse/jev/wake.py)."
    first = _card(tmp_path, "wid-a1", goal, files=["src/autonomouse/jev/wake.py"])
    second = _card(tmp_path, "wid-a2", goal, files=["docs/config.md", "tests/test_x.py"])
    assert overlaps(second.files, [first]) == []
    assert overlaps(first.files, [second]) == []
    # without their files lists the same two goals share the module
    bare = _card(tmp_path, "wid-a3", goal)
    assert _ids(overlaps(first.files, [bare])) == ["wid-a3"]


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (("autonomouse", "cli"), ("cli",), True),  # written shorter: cli.py
        (("autonomouse", "jev"), ("autonomouse", "jev", "wake"), True),  # package and module
        (("docs", "orchestrator.md"), ("docs", "planner.md"), False),
        (("docs", "cli.md"), ("autonomouse", "cli"), False),
        ((), ("autonomouse", "cli"), False),  # `--files /` names no file
    ],
)
def test_same_file_is_a_shorter_spelling_or_a_folder_that_holds_it(a, b, same):
    assert same_file(a, b) is same
    assert same_file(b, a) is same


def test_a_glob_in_one_cards_files_overlaps_a_file_it_matches_in_anothers(tmp_path):
    """sys-s76: in wake 6 sys-s51's `docs/orchestrator/*.md` and sys-s61's
    `docs/orchestrator/decision-gate.md` were not found to share a file, and the orchestrator
    put sys-s61 on the interim branch by hand."""
    _project(tmp_path)
    glob = _card(tmp_path, "wid-a1", "g", files=["docs/orchestrator.md", "docs/orchestrator/*.md"])
    fire = _card(tmp_path, "wid-a2", "g", files=["docs/orchestrator/fire.md"])
    other = _card(tmp_path, "wid-a3", "g", files=["docs/planner.md"])

    assert overlaps(fire.files, [glob, other]) == [(glob, ["docs/orchestrator/fire.md"], "files")]
    assert overlaps(glob.files, [fire, other]) == [(fire, ["docs/orchestrator/*.md"], "files")]
    found = card_overlaps(tmp_path, project="widgets", files=["docs/orchestrator/fire.md"])
    assert _ids(found) == ["wid-a1", "wid-a2"]


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("docs/orchestrator/*.md", "docs/orchestrator/fire.md", True),
        ("docs/orchestrator/*.md", "fire.md", True),  # written shorter
        ("docs/orchestrator/*.md", "docs/orchestrator/decision-*.md", True),  # one holds one
        ("docs/orchestrator/*.md", "docs/orchestrator.md", False),
        ("docs/orchestrator/*.md", "docs/orchestrator/fire.py", False),
        ("docs/*.md", "docs/orchestrator/fire.md", False),  # `*` stays inside one folder
        ("docs/**/*.md", "docs/orchestrator/old/fire.md", True),  # `**` is any depth
        ("docs/**/*.md", "docs/fire.md", True),
        ("docs/orchestrator/fire.m?", "docs/orchestrator/fire.md", True),
        ("docs/orchestrator/[a-f]*.md", "docs/orchestrator/fire.md", True),
        ("docs/orchestrator/[g-z]*.md", "docs/orchestrator/fire.md", False),
        ("tests/test_board_*.py", "tests/test_board_overlaps.py", True),
        ("docs/orchestrator/", "docs/orchestrator/fire.md", True),  # a folder holds its files
        ("docs/orchestrator", "docs/orchestrator/old/fire.md", True),
        ("docs/orchestrator/", "docs/planner.md", False),
    ],
)
def test_a_glob_or_a_folder_is_every_path_it_matches(a, b, same):
    assert same_file(path_name(a), path_name(b)) is same
    assert same_file(path_name(b), path_name(a)) is same


def test_card_overlaps_lists_only_the_projects_cards_not_yet_done_or_cancelled(tmp_path):
    _project(tmp_path)
    for card_id in ("wid-a1", "wid-a2", "wid-a3", "wid-a4"):
        _card(tmp_path, card_id, "g", files=["docs/orchestrator.md"])
    for card_id, stack in (("wid-a2", Stack.DONE), ("wid-a3", Stack.CANCELLED)):
        path = tmp_path / "projects" / "widgets" / "cards" / f"{card_id}.json"
        card = load_card(path)
        card.stack = stack
        save_card(path, card)

    found = card_overlaps(tmp_path, project="widgets", files=["docs/orchestrator.md"])
    assert _ids(found) == ["wid-a1", "wid-a4"]


def _run(*args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


def test_the_commands_store_print_and_check_files_through_the_shell(tmp_path):
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _project(tmp_path / "board")
    created = _run(
        "card", "new", "--project", "widgets", "--id", "wid-a1", "--title", "one",
        "--goal", "Change src/autonomouse/jev/wake.py.", "--test", "t",
        "--files", "docs/planner.md", "--files", "src/autonomouse/cli.py", "--run-id", "R",
        "--json", env=env,
    )  # fmt: skip
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["files"] == ["docs/planner.md", "src/autonomouse/cli.py"]
    # The card file holds the list and the history row the behaviour log counts as the
    # run's `card new`.
    path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    on_disk = json.loads(path.read_text())
    assert on_disk["files"] == ["docs/planner.md", "src/autonomouse/cli.py"]
    assert [(h["from"], h["to"], h["run_id"]) for h in on_disk["history"]] == [
        (None, "todo", "R")
    ]
    shown = _run("card", "show", "wid-a1", env=env)
    assert "files: docs/planner.md, src/autonomouse/cli.py" in shown.stdout

    fallback = _run(
        "card", "new", "--project", "widgets", "--id", "wid-a2", "--title", "two",
        "--goal", "Split docs/orchestrator.md by topic.", "--test", "t", env=env,
    )  # fmt: skip
    assert fallback.returncode == 0, fallback.stderr
    assert "files: (none)" in _run("card", "show", "wid-a2", env=env).stdout

    listed = _run(
        "card", "overlaps", "--project", "widgets",
        "--files", "cli.py", "--files", "docs/orchestrator.md", env=env,
    )  # fmt: skip
    assert listed.returncode == 0, listed.stderr
    assert listed.stdout.splitlines() == [
        "wid-a1  todo  cli.py  one",
        "wid-a2  todo  docs/orchestrator.md (read from its goal; it has no files list)  two",
    ]
    as_json = _run(
        "card", "overlaps", "--project", "widgets", "--files", "docs/orchestrator.md",
        "--json", env=env,
    )  # fmt: skip
    assert json.loads(as_json.stdout) == [
        {"id": "wid-a2", "stack": "todo", "title": "two",
         "shared": ["docs/orchestrator.md"], "from": "goal"},
    ]  # fmt: skip
    none = _run("card", "overlaps", "--project", "widgets", "--files", "docs/x.md", env=env)
    assert none.stdout.strip() == "no open card changes these files"

    edited = _run(
        "card", "edit", "wid-a1", "--files", "docs/b.md", "--run-id", "R", env=env
    )  # fmt: skip
    assert edited.returncode == 0, edited.stderr
    assert "files: docs/b.md" in edited.stdout
    # and, for `card edit`, the new list and a row naming the field and the run
    on_disk = json.loads(path.read_text())
    assert on_disk["files"] == ["docs/b.md"]
    row = on_disk["history"][-1]
    assert (row["from"], row["to"], row["note"], row["run_id"]) == (
        "todo", "todo", "edited: files", "R"
    )  # fmt: skip
