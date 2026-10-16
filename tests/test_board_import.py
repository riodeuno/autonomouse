"""Tests for board.commands.board_import: the card's own test is "Import into an
empty board creates everything; a second import changes nothing."
"""

import json

import pytest

from autonomouse.board.commands import (
    CommandError,
    board_export,
    board_import,
    board_import_export,
    card_new,
    project_new,
)
from autonomouse.board.schema import Stack
from autonomouse.board.store import load_card, load_project, save_card


def _seed_project(seed_root, slug="widgets", priority=1):
    return project_new(
        seed_root,
        slug=slug,
        name="Widgets",
        goal="Build widgets.",
        repo="https://github.com/test/widgets",
        repo_path="/home/test/widgets",
        default_branch="main",
        priority=priority,
    )


def _seed_card(seed_root, project, card_id, *, position=1, to_todo=True):
    """Build a seed card: created by `card_new` in `backlog`, then, with `to_todo`,
    moved to `todo` with a "seeded" history row, so a verbatim import is told apart
    from `card_new`'s fresh `backlog`."""
    card_new(
        seed_root,
        card_id=card_id,
        project=project,
        title=f"card {card_id}",
        goal="goal text",
        test="test text",
        position=position,
        run_id="seed",
    )
    path = seed_root / "projects" / project / "cards" / f"{card_id}.json"
    if to_todo:
        card = load_card(path)
        card.stack = Stack.TODO
        card.history[0].to = "todo"
        card.history[0].note = "seeded by the planning session"
        save_card(path, card)
    return load_card(path)


def _build_seed(seed_root):
    _seed_project(seed_root, slug="widgets", priority=2)
    _seed_project(seed_root, slug="gadgets", priority=1)
    _seed_card(seed_root, "widgets", "wid-a1", position=1)
    _seed_card(seed_root, "widgets", "wid-a2", position=2)
    _seed_card(seed_root, "gadgets", "gad-a1", position=1)


class TestBoardImport:
    def test_refuses_dir_without_projects(self, tmp_path):
        seed_root = tmp_path / "seed"
        seed_root.mkdir()
        with pytest.raises(CommandError, match="no projects/"):
            board_import(tmp_path / "board", seed_root)

    def test_import_into_empty_board_creates_everything(self, tmp_path):
        seed_root = tmp_path / "seed"
        board_root = tmp_path / "board"
        _build_seed(seed_root)

        result = board_import(board_root, seed_root)

        assert sorted(result.projects_created) == ["gadgets", "widgets"]
        assert result.projects_skipped == []
        assert sorted(result.cards_created) == ["gad-a1", "wid-a1", "wid-a2"]
        assert result.cards_skipped == []

        assert (board_root / "projects" / "widgets" / "project.json").is_file()
        assert (board_root / "projects" / "gadgets" / "project.json").is_file()
        assert (
            board_root / "projects" / "widgets" / "cards" / "wid-a1.json"
        ).is_file()
        assert (
            board_root / "projects" / "widgets" / "cards" / "wid-a2.json"
        ).is_file()
        assert (
            board_root / "projects" / "gadgets" / "cards" / "gad-a1.json"
        ).is_file()

        # copied verbatim: the seed's own stack and history ride along unchanged,
        # not reset to card_new's fresh `backlog`.
        imported = load_card(
            board_root / "projects" / "widgets" / "cards" / "wid-a1.json"
        )
        seeded = load_card(seed_root / "projects" / "widgets" / "cards" / "wid-a1.json")
        assert imported.stack == Stack.TODO
        assert imported.to_dict() == seeded.to_dict()

        imported_project = load_project(board_root / "projects" / "widgets" / "project.json")
        seeded_project = load_project(seed_root / "projects" / "widgets" / "project.json")
        assert imported_project.to_dict() == seeded_project.to_dict()

        # the run record under seed/ is not the goal's concern — only cards and
        # projects are copied.
        assert not (board_root / "runs").exists()

    def test_second_import_changes_nothing(self, tmp_path):
        seed_root = tmp_path / "seed"
        board_root = tmp_path / "board"
        _build_seed(seed_root)

        board_import(board_root, seed_root)
        before = {
            path: path.read_text()
            for path in sorted(board_root.rglob("*.json"))
        }

        result = board_import(board_root, seed_root)

        assert result.projects_created == []
        assert sorted(result.projects_skipped) == ["gadgets", "widgets"]
        assert result.cards_created == []
        assert sorted(result.cards_skipped) == ["gad-a1", "wid-a1", "wid-a2"]

        after = {path: path.read_text() for path in sorted(board_root.rglob("*.json"))}
        assert after == before

    def test_leaves_a_project_already_on_the_board_untouched(self, tmp_path):
        seed_root = tmp_path / "seed"
        board_root = tmp_path / "board"
        _build_seed(seed_root)

        # the board already has its own "widgets", created independently of the seed
        _seed_project(board_root, slug="widgets", priority=99)
        on_board_before = load_project(board_root / "projects" / "widgets" / "project.json")

        result = board_import(board_root, seed_root)

        assert "widgets" in result.projects_skipped
        assert "widgets" not in result.projects_created
        on_board_after = load_project(board_root / "projects" / "widgets" / "project.json")
        assert on_board_after.to_dict() == on_board_before.to_dict()
        assert on_board_after.priority == 99


def _set_depends_on(seed_root, project, card_id, depends_on):
    path = seed_root / "projects" / project / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.depends_on = depends_on
    save_card(path, card)


class TestImportDependsOn:
    """`board import` refuses a `depends_on` that names no card on the board or in
    the import, and writes nothing when it does."""

    def test_a_dependency_on_a_card_in_the_import_or_on_the_board_is_accepted(
        self, tmp_path
    ):
        board_root = tmp_path / "board"
        _seed_project(board_root, slug="tools")
        _seed_card(board_root, "tools", "too-a1")
        seed_root = tmp_path / "seed"
        _build_seed(seed_root)
        _set_depends_on(seed_root, "widgets", "wid-a2", ["wid-a1", "gad-a1", "too-a1"])

        result = board_import(board_root, seed_root)

        assert sorted(result.cards_created) == ["gad-a1", "wid-a1", "wid-a2"]

    def test_an_unknown_dependency_is_refused_and_nothing_is_written(self, tmp_path):
        seed_root = tmp_path / "seed"
        board_root = tmp_path / "board"
        _build_seed(seed_root)
        _set_depends_on(seed_root, "widgets", "wid-a2", ["wid-a1", "sys-nope"])

        with pytest.raises(CommandError, match="sys-nope"):
            board_import(board_root, seed_root)
        assert not (board_root / "projects").exists()

    def test_a_card_that_depends_on_itself_is_refused_naming_it(self, tmp_path):
        seed_root = tmp_path / "seed"
        board_root = tmp_path / "board"
        _build_seed(seed_root)
        _set_depends_on(seed_root, "widgets", "wid-a2", ["wid-a1", "wid-a2"])

        with pytest.raises(CommandError, match="wid-a2"):
            board_import(board_root, seed_root)
        assert not (board_root / "projects").exists()

    def test_a_comma_in_a_dependency_is_refused_with_the_repeat_hint(self, tmp_path):
        seed_root = tmp_path / "seed"
        _build_seed(seed_root)
        _set_depends_on(seed_root, "widgets", "wid-a2", ["wid-a1,gad-a1"])

        with pytest.raises(CommandError, match="--depends-on <id> --depends-on <id>"):
            board_import(tmp_path / "board", seed_root)

    def test_an_export_file_gets_the_same_check(self, tmp_path):
        seed_root = tmp_path / "seed"
        _build_seed(seed_root)
        _set_depends_on(seed_root, "widgets", "wid-a2", ["sys-nope"])
        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps(board_export(seed_root)))

        board_root = tmp_path / "board"
        with pytest.raises(CommandError, match="sys-nope"):
            board_import_export(board_root, export_file)
        assert not (board_root / "projects").exists()
