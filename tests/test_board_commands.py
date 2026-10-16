"""Tests for board.commands: project new/show/list/edit, card new/show/list."""

import json
import re
import sys

import pytest

from autonomouse.board.commands import (
    CommandError,
    board_export,
    card_answer,
    card_ask,
    card_cancel,
    card_delete,
    card_edit,
    card_finish,
    card_list,
    card_move,
    card_new,
    card_progress,
    card_show,
    project_edit,
    project_list,
    project_new,
    project_show,
)
from autonomouse.board.schema import BoardValidationError, Phase, Size, Stack
from autonomouse.board.store import load_card, save_card


@pytest.fixture(autouse=True)
def _isolated_notify_env(monkeypatch, tmp_path):
    """`card_ask` sends a real notify. Keep every test in this module off this
    machine's real config.toml and real credentials, so none can ever make a live
    send: without this, a card_ask test would read `~/.config/autonomouse/config.toml`
    and this process's real environment, and once a human has run `notify setup`
    for real, that config carries a real chat id a real TELEGRAM_BOT_TOKEN can send to.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "notify-config"))


def _new_project(root, slug="widgets", priority=1):
    return project_new(
        root,
        slug=slug,
        name="Widgets",
        goal="Build widgets.",
        repo="https://github.com/test/widgets",
        repo_path="/home/test/widgets",
        default_branch="main",
        priority=priority,
    )


def _set_stack(root, project, card_id, stack: Stack):
    """Force a card straight into `stack`, bypassing the transition commands —
    `start`/`ask`/`answer`/`finish` don't exist yet, so this stands in for them
    when a move/cancel test needs a card that isn't sitting in backlog."""
    path = root / "projects" / project / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = stack
    save_card(path, card)
    return card


class TestProjectNew:
    def test_creates_project_file_and_cards_dir(self, tmp_path):
        project = _new_project(tmp_path)

        assert (tmp_path / "projects" / "widgets" / "project.json").is_file()
        assert (tmp_path / "projects" / "widgets" / "cards").is_dir()
        assert project.slug == "widgets"
        assert project.default_branch == "main"
        assert project.claim.run_id is None

    def test_refuses_duplicate_slug(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="already exists"):
            _new_project(tmp_path)

    def test_refuses_bad_slug(self, tmp_path):
        with pytest.raises(CommandError, match="lowercase"):
            _new_project(tmp_path, slug="Not Ok")


class TestProjectShowAndList:
    def test_show_missing_project(self, tmp_path):
        with pytest.raises(CommandError, match="no project"):
            project_show(tmp_path, "nope")

    def test_show_round_trips(self, tmp_path):
        _new_project(tmp_path)
        assert project_show(tmp_path, "widgets").name == "Widgets"

    def test_list_empty_board(self, tmp_path):
        assert project_list(tmp_path) == []

    def test_list_sorted_by_priority_then_slug(self, tmp_path):
        _new_project(tmp_path, slug="second", priority=2)
        _new_project(tmp_path, slug="first", priority=1)

        slugs = [p.slug for p in project_list(tmp_path)]
        assert slugs == ["first", "second"]


class TestProjectEdit:
    def test_edits_priority_only(self, tmp_path):
        _new_project(tmp_path)
        edited = project_edit(tmp_path, "widgets", priority=9)

        assert edited.priority == 9
        assert edited.name == "Widgets"
        assert project_show(tmp_path, "widgets").priority == 9

    def test_edits_goal_only(self, tmp_path):
        _new_project(tmp_path)
        edited = project_edit(tmp_path, "widgets", goal="New goal.")
        assert edited.goal == "New goal."

    def test_edits_name_and_keeps_the_slug(self, tmp_path):
        _new_project(tmp_path)
        edited = project_edit(tmp_path, "widgets", name="Widget factory")
        assert edited.name == "Widget factory"
        shown = project_show(tmp_path, "widgets")
        assert (shown.slug, shown.name, shown.priority) == ("widgets", "Widget factory", 1)
        with pytest.raises(CommandError, match="name must not be empty"):
            project_edit(tmp_path, "widgets", name="  ")

    def test_refuses_empty_edit(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="nothing to edit"):
            project_edit(tmp_path, "widgets")

    def test_refuses_missing_project(self, tmp_path):
        with pytest.raises(CommandError, match="no project"):
            project_edit(tmp_path, "nope", priority=1)


# A project.json exactly as `project new` wrote it before test_temp_env, merge_method and
# charter_files existed.
_OLD_PROJECT = {
    "slug": "widgets",
    "name": "Widgets",
    "goal": "Build widgets.",
    "repo": "https://github.com/test/widgets",
    "repo_path": "/home/test/widgets",
    "default_branch": "main",
    "priority": 1,
    "tracker": None,
    "claim": {
        "run_id": None,
        "session_id": None,
        "pid": None,
        "model": None,
        "claimed_at": None,
    },
    "created_at": "2026-09-01T00:00:00Z",
    "updated_at": "2026-09-01T00:00:00Z",
}


def _write_project(root, data):
    path = root / "projects" / "widgets" / "project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


class TestProjectCharterFields:
    """sys-s86: the project record holds the per-project facts the charters need —
    test_temp_env, merge_method and charter_files."""

    def test_project_written_without_the_fields_loads_their_defaults(self, tmp_path):
        path = _write_project(tmp_path, _OLD_PROJECT)

        project = project_show(tmp_path, "widgets")

        assert project.test_temp_env == []
        assert project.merge_method == "squash"
        assert project.charter_files == []
        # The next save writes the defaults back in.
        project_edit(tmp_path, "widgets", priority=2)
        on_disk = json.loads(path.read_text())
        assert on_disk["test_temp_env"] == []
        assert on_disk["merge_method"] == "squash"
        assert on_disk["charter_files"] == []

    def test_project_written_with_the_fields_round_trips_them(self, tmp_path):
        facts = {
            "test_temp_env": ["AUTONOMOUSE_HOME", "AUTONOMOUSE_CONFIG"],
            "merge_method": "rebase",
            "charter_files": ["docs/rules.md", "docs/orchestrator/*"],
        }
        path = _write_project(tmp_path, {**_OLD_PROJECT, **facts})

        project = project_show(tmp_path, "widgets")
        assert project.test_temp_env == facts["test_temp_env"]
        assert project.merge_method == "rebase"
        assert project.charter_files == facts["charter_files"]

        project_edit(tmp_path, "widgets", priority=2)
        on_disk = json.loads(path.read_text())
        assert {key: on_disk[key] for key in facts} == facts

        edited = project_edit(
            tmp_path,
            "widgets",
            test_temp_env=["HOME"],
            merge_method="merge",
            charter_files=["docs/worker.md"],
        )
        assert project_show(tmp_path, "widgets") == edited
        assert edited.test_temp_env == ["HOME"]
        assert edited.merge_method == "merge"
        assert edited.charter_files == ["docs/worker.md"]

    def test_project_show_prints_all_three(self, tmp_path, monkeypatch, capsys):
        from autonomouse import cli

        monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
        _new_project(tmp_path)

        def run_cli(*argv):
            monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
            cli.main()
            return capsys.readouterr().out

        shown = run_cli("project", "show", "widgets").splitlines()
        assert "test_temp_env: (none)" in shown
        assert "merge_method: squash" in shown
        assert "charter_files: (none)" in shown

        run_cli(
            "project", "edit", "widgets",
            "--test-temp-env", "AUTONOMOUSE_HOME",
            "--merge-method", "rebase",
            "--charter-files", "docs/rules.md",
            "--charter-files", "docs/orchestrator/*",
        )  # fmt: skip
        shown = run_cli("project", "show", "widgets").splitlines()
        assert "test_temp_env: AUTONOMOUSE_HOME" in shown
        assert "merge_method: rebase" in shown
        assert "charter_files: docs/rules.md, docs/orchestrator/*" in shown

    def test_merge_method_outside_the_three_is_refused_with_the_key_named(
        self, tmp_path
    ):
        _write_project(tmp_path, _OLD_PROJECT)
        with pytest.raises(CommandError, match="merge_method must be one of"):
            project_edit(tmp_path, "widgets", merge_method="fast-forward")
        assert project_show(tmp_path, "widgets").merge_method == "squash"

        _write_project(tmp_path, {**_OLD_PROJECT, "merge_method": "fast-forward"})
        with pytest.raises(
            BoardValidationError,
            match="'merge_method' must be one of squash, merge, rebase — got 'fast-forward'",
        ):
            project_show(tmp_path, "widgets")


LINEAR_PROJECT = "https://linear.app/acme/project/widgets-launch-5b1a2c3d4e5f"
LINEAR_ISSUE = "https://linear.app/acme/issue/WID-42/ship-the-widget-page"


class TestTrackerLinks:
    """sys-w14: a project is linked to a Linear project or issue, at `project new` or later
    with `project edit --tracker`, and a card records its own issue with `card edit
    --tracker-issue`. Nothing here talks to Linear. Each test fails on card/sys-w21, where
    `tracker` is always null and a card has no `tracker_issue`."""

    def _cli(self, monkeypatch, capsys, *argv):
        from autonomouse import cli

        monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
        cli.main()
        return capsys.readouterr().out

    def test_project_new_stores_a_project_link_an_issue_link_or_none(self, tmp_path):
        for slug, url, tracker in (
            ("a", LINEAR_PROJECT, {"kind": "linear", "url": LINEAR_PROJECT, "link": "project"}),
            ("b", LINEAR_ISSUE, {"kind": "linear", "url": LINEAR_ISSUE, "link": "issue"}),
            ("c", None, None),
        ):
            project_new(
                tmp_path, slug=slug, name=slug, goal="g", repo="https://github.com/t/w",
                repo_path="/tmp/w", default_branch="main", priority=1, tracker=url,
            )  # fmt: skip
            on_disk = json.loads((tmp_path / "projects" / slug / "project.json").read_text())
            assert on_disk["tracker"] == tracker
            assert project_show(tmp_path, slug).tracker == tracker

    def test_project_edit_tracker_sets_replaces_and_none_clears(
        self, tmp_path, monkeypatch, capsys
    ):
        from autonomouse import project_wizard

        monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
        # The Linear access check (sys-w15, tests/test_probe.py) is proven elsewhere; here it
        # passes and records the link it was asked to prove.
        checked = []
        monkeypatch.setattr(
            project_wizard, "check_tracker",
            lambda url, *, checkout, say: checked.append((url, str(checkout))),
        )  # fmt: skip
        _new_project(tmp_path)
        assert "tracker: (none)" in self._cli(monkeypatch, capsys, "project", "show", "widgets").splitlines()

        shown = self._cli(monkeypatch, capsys, "project", "edit", "widgets", "--tracker", LINEAR_PROJECT)
        assert f"tracker: linear project {LINEAR_PROJECT}" in shown.splitlines()
        assert checked == [(LINEAR_PROJECT, "/home/test/widgets")]
        assert project_show(tmp_path, "widgets").tracker == {
            "kind": "linear", "url": LINEAR_PROJECT, "link": "project",
        }  # fmt: skip

        edited = project_edit(tmp_path, "widgets", tracker=LINEAR_ISSUE)
        assert edited.tracker == {"kind": "linear", "url": LINEAR_ISSUE, "link": "issue"}

        shown = self._cli(monkeypatch, capsys, "project", "edit", "widgets", "--tracker", "none")
        assert "tracker: (none)" in shown.splitlines()
        assert len(checked) == 1, "clearing the link runs no access check"
        path = tmp_path / "projects" / "widgets" / "project.json"
        assert json.loads(path.read_text())["tracker"] is None

    @pytest.mark.parametrize(
        ("url", "says"),
        [
            (
                "https://github.com/orgs/acme/projects/1",
                "is not a tracker autonomouse knows; the trackers it knows are Linear (linear.app)",
            ),
            ("linear.app/acme/project/x", "is not a tracker autonomouse knows"),
            (
                "https://linear.app/acme/team/WID/active",
                (
                    "is neither a Linear project nor a Linear issue; give "
                    "https://linear.app/<workspace>/project/<name> or "
                    "https://linear.app/<workspace>/issue/<ABC-123>"
                ),
            ),
        ],
    )
    def test_a_url_no_known_tracker_reads_is_refused_and_nothing_changes(
        self, tmp_path, url, says
    ):
        _new_project(tmp_path)
        path = tmp_path / "projects" / "widgets" / "project.json"
        before = path.read_text()

        with pytest.raises(CommandError, match=re.escape(says)):
            project_edit(tmp_path, "widgets", tracker=url)
        with pytest.raises(CommandError, match=re.escape(says)):
            project_new(
                tmp_path, slug="other", name="o", goal="g", repo="https://github.com/t/o",
                repo_path="/tmp/o", default_branch="main", priority=2, tracker=url,
            )  # fmt: skip

        assert path.read_text() == before
        assert not (tmp_path / "projects" / "other").exists()

    def test_card_edit_tracker_issue_stores_it_and_both_shows_print_it(
        self, tmp_path, monkeypatch, capsys
    ):
        from autonomouse import overview

        monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
        _one_card(tmp_path)
        project_edit(tmp_path, "widgets", tracker=LINEAR_PROJECT)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        card_progress(tmp_path, "wid-a1", run_id="run-1", phase="writing", next_step="go on")
        assert "tracker_issue: (none)" in self._cli(monkeypatch, capsys, "card", "show", "wid-a1").splitlines()

        shown = self._cli(
            monkeypatch, capsys, "card", "edit", "wid-a1", "--tracker-issue", LINEAR_ISSUE,
            "--run-id", "run-1",
        )  # fmt: skip

        assert f"tracker_issue: WID-42 {LINEAR_ISSUE}" in shown.splitlines()
        card = card_show(tmp_path, "wid-a1")
        assert card.tracker_issue == {"url": LINEAR_ISSUE, "identifier": "WID-42"}
        assert card.history[-1].note == "edited: tracker_issue WID-42"
        # Recording the issue is no new plan: the attempt state stays.
        assert (card.phase, card.handoff.next_step) == (Phase.WRITING, "go on")
        on_disk = json.loads((tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json").read_text())
        assert on_disk["tracker_issue"] == {"url": LINEAR_ISSUE, "identifier": "WID-42"}

        text = overview.format_show(
            overview.project_details(tmp_path, "widgets", read_prs=lambda repo: [])
        )
        assert f"tracker: linear project {LINEAR_PROJECT}" in text.splitlines()
        [line] = [line for line in text.splitlines() if "wid-a1" in line]
        assert line.split()[-1] == "WID-42"

    @pytest.mark.parametrize(
        ("url", "says"),
        [
            (LINEAR_PROJECT, "is a Linear project, not an issue"),
            ("https://example.com/issue/WID-1", "is not a tracker autonomouse knows"),
            ("https://linear.app/acme/issue/not-an-id", "is neither a Linear project nor"),
        ],
    )
    def test_card_edit_refuses_a_url_that_is_no_issue(self, tmp_path, url, says):
        _one_card(tmp_path)
        path = tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json"
        before = path.read_text()

        with pytest.raises(CommandError, match=re.escape(says)):
            card_edit(tmp_path, "wid-a1", tracker_issue=url)

        assert path.read_text() == before

    def test_a_project_and_a_card_written_before_the_links_still_load(self, tmp_path):
        _write_project(tmp_path, _OLD_PROJECT)
        card = card_new(tmp_path, card_id="wid-a1", project="widgets", title="t", goal="g", test="t")
        path = tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json"
        old_card = card.to_dict()
        del old_card["tracker_issue"]
        path.write_text(json.dumps(old_card))

        assert project_show(tmp_path, "widgets").tracker is None
        assert card_show(tmp_path, "wid-a1").tracker_issue is None
        # The next save writes the field back in.
        card_edit(tmp_path, "wid-a1", title="t2")
        assert json.loads(path.read_text())["tracker_issue"] is None


class TestCardNew:
    def test_creates_card_in_backlog(self, tmp_path):
        _new_project(tmp_path)
        card = card_new(
            tmp_path,
            card_id="wid-a1",
            project="widgets",
            title="Do the thing",
            goal="The thing is done.",
            test="run the thing",
        )

        assert (tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json").is_file()
        assert card.stack == Stack.BACKLOG
        assert card.limits == ["docs/rules.md applies"]
        assert len(card.history) == 1
        assert card.history[0].to == "backlog"

    def test_extra_limits_keep_rules_first(self, tmp_path):
        _new_project(tmp_path)
        card = card_new(
            tmp_path,
            card_id="wid-a1",
            project="widgets",
            title="t",
            goal="g",
            test="t",
            limits=["never touch prod"],
        )
        assert card.limits == ["docs/rules.md applies", "never touch prod"]

    def test_refuses_bad_id(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="prefix"):
            card_new(
                tmp_path,
                card_id="bad id",
                project="widgets",
                title="t",
                goal="g",
                test="t",
            )

    def test_refuses_missing_project(self, tmp_path):
        with pytest.raises(CommandError, match="no project"):
            card_new(
                tmp_path,
                card_id="wid-a1",
                project="widgets",
                title="t",
                goal="g",
                test="t",
            )

    def test_refuses_duplicate_id(self, tmp_path):
        _new_project(tmp_path)
        card_new(
            tmp_path, card_id="wid-a1", project="widgets", title="t", goal="g", test="t"
        )
        with pytest.raises(CommandError, match="already exists"):
            card_new(
                tmp_path,
                card_id="wid-a1",
                project="widgets",
                title="t2",
                goal="g2",
                test="t2",
            )

    def test_refuses_bad_size(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="size"):
            card_new(
                tmp_path,
                card_id="wid-a1",
                project="widgets",
                title="t",
                goal="g",
                test="t",
                size="xl",
            )


class TestCardShow:
    def test_finds_across_projects(self, tmp_path):
        _new_project(tmp_path)
        card_new(
            tmp_path, card_id="wid-a1", project="widgets", title="t", goal="g", test="t"
        )

        assert card_show(tmp_path, "wid-a1").project == "widgets"
        assert card_show(tmp_path, "wid-a1", project="widgets").id == "wid-a1"

    def test_missing_card(self, tmp_path):
        with pytest.raises(CommandError, match="no card"):
            card_show(tmp_path, "nope-a1")


class TestCardList:
    def _three_cards(self, tmp_path):
        _new_project(tmp_path)
        card_new(
            tmp_path,
            card_id="wid-a1",
            project="widgets",
            title="one",
            goal="g",
            test="t",
            position=1,
        )
        card_new(
            tmp_path,
            card_id="wid-a2",
            project="widgets",
            title="two",
            goal="g",
            test="t",
            position=2,
        )
        card_new(
            tmp_path,
            card_id="wid-a3",
            project="widgets",
            title="three",
            goal="g",
            test="t",
            position=3,
        )

    def test_lists_all_in_position_order(self, tmp_path):
        self._three_cards(tmp_path)
        ids = [c.id for c in card_list(tmp_path)]
        assert ids == ["wid-a1", "wid-a2", "wid-a3"]

    def test_filters_by_stack(self, tmp_path):
        self._three_cards(tmp_path)
        assert [c.id for c in card_list(tmp_path, stack="backlog")] == [
            "wid-a1",
            "wid-a2",
            "wid-a3",
        ]
        assert card_list(tmp_path, stack="done") == []

    def test_filters_by_project(self, tmp_path):
        self._three_cards(tmp_path)
        _new_project(tmp_path, slug="other", priority=2)
        card_new(
            tmp_path, card_id="oth-a1", project="other", title="x", goal="g", test="t"
        )

        assert [c.id for c in card_list(tmp_path, project="widgets")] == [
            "wid-a1",
            "wid-a2",
            "wid-a3",
        ]

    def test_bad_stack_filter(self, tmp_path):
        with pytest.raises(CommandError, match="stack"):
            card_list(tmp_path, stack="bogus")

    def test_empty_board(self, tmp_path):
        assert card_list(tmp_path) == []


def _one_card(root):
    _new_project(root)
    card_new(root, card_id="wid-a1", project="widgets", title="t", goal="g", test="t")


class TestCardMove:
    def test_backlog_to_todo_lands_with_history_row(self, tmp_path):
        _one_card(tmp_path)
        moved = card_move(tmp_path, "wid-a1", "todo", run_id="run-1")

        assert moved.stack == Stack.TODO
        assert moved.history[-1].from_ == "backlog"
        assert moved.history[-1].to == "todo"
        assert moved.history[-1].run_id == "run-1"
        assert card_show(tmp_path, "wid-a1").stack == Stack.TODO

    def test_in_progress_to_todo_needs_a_note(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="note"):
            card_move(tmp_path, "wid-a1", "todo")

    def test_in_progress_to_todo_with_note_lands(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        moved = card_move(tmp_path, "wid-a1", "todo", note="reclaimed: session died")

        assert moved.stack == Stack.TODO
        assert moved.history[-1].from_ == "in_progress"
        assert moved.history[-1].note == "reclaimed: session died"

    def test_todo_to_backlog_needs_a_note(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.TODO)
        with pytest.raises(CommandError, match="--note"):
            card_move(tmp_path, "wid-a1", "backlog")
        with pytest.raises(CommandError, match="--note"):
            card_move(tmp_path, "wid-a1", "backlog", note="  ")
        assert card_show(tmp_path, "wid-a1").stack == Stack.TODO

    def test_todo_to_backlog_with_note_lands(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.TODO)
        note = "set aside: the human answered 'leave this card for now'"
        moved = card_move(tmp_path, "wid-a1", "backlog", note=note, run_id="run-2")

        assert moved.stack == Stack.BACKLOG
        row = moved.history[-1]
        assert (row.from_, row.to, row.run_id) == ("todo", "backlog", "run-2")
        assert row.note == note
        assert card_show(tmp_path, "wid-a1").stack == Stack.BACKLOG

    def test_todo_to_in_progress_refused_needs_start(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.TODO)
        with pytest.raises(CommandError, match="card start"):
            card_move(tmp_path, "wid-a1", "in_progress")

    def test_move_to_a_stack_that_does_not_exist_is_refused(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="no such stack"):
            card_move(tmp_path, "wid-a1", "bogus")

    def test_other_illegal_transitions_refused(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.DONE)
        with pytest.raises(CommandError, match="cannot move"):
            card_move(tmp_path, "wid-a1", "backlog")

    def test_done_to_todo_reopens_the_card_at_the_top(self, tmp_path):
        _one_card(tmp_path)
        card_edit(tmp_path, "wid-a1", position=4)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.DONE)
        path = tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json"
        done = load_card(path)
        done.phase = Phase.REVIEW
        done.iteration = 2
        done.handoff.last_step = "verification note"
        save_card(path, done)

        moved = card_move(
            tmp_path,
            "wid-a1",
            "todo",
            note="reopened: PR u conflicts with main after #7 merged",
            next_step="reopened: merge origin/main into card/wid-a1, resolve",
            run_id="run-9",
        )

        assert moved.stack == Stack.TODO
        assert moved.position == 0
        assert moved.phase == Phase.WRITING
        assert moved.iteration == 2  # a conflict is not a failed attempt
        assert moved.handoff.next_step == (
            "reopened: merge origin/main into card/wid-a1, resolve"
        )
        assert moved.handoff.last_step == "verification note"
        assert moved.handoff.run_id == "run-9"
        assert moved.handoff.updated_at == moved.history[-1].at
        row = moved.history[-1]
        assert (row.from_, row.to, row.run_id) == ("done", "todo", "run-9")
        assert row.note == "reopened: PR u conflicts with main after #7 merged"
        assert load_card(path).to_dict() == moved.to_dict()

    def test_done_to_todo_needs_a_note(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.DONE)
        with pytest.raises(CommandError, match="--note"):
            card_move(tmp_path, "wid-a1", "todo", next_step="merge")
        with pytest.raises(CommandError, match="--note"):
            card_move(tmp_path, "wid-a1", "todo", note="  ", next_step="merge")
        assert card_show(tmp_path, "wid-a1").stack == Stack.DONE

    def test_done_to_todo_needs_a_next_step(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.DONE)
        with pytest.raises(CommandError, match="--next-step"):
            card_move(tmp_path, "wid-a1", "todo", note="reopened")
        assert card_show(tmp_path, "wid-a1").stack == Stack.DONE

    def test_next_step_is_refused_outside_a_reopen(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="--next-step is only"):
            card_move(tmp_path, "wid-a1", "todo", next_step="merge")
        assert card_show(tmp_path, "wid-a1").stack == Stack.BACKLOG

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_move(tmp_path, "wid-a1", "todo")


class TestCardCancel:
    def test_cancel_records_the_reason(self, tmp_path):
        _one_card(tmp_path)
        cancelled = card_cancel(tmp_path, "wid-a1", reason="superseded by wid-a2")

        assert cancelled.stack == Stack.CANCELLED
        assert cancelled.history[-1].from_ == "backlog"
        assert cancelled.history[-1].to == "cancelled"
        assert cancelled.history[-1].note == "superseded by wid-a2"
        assert card_show(tmp_path, "wid-a1").stack == Stack.CANCELLED

    def test_cancel_from_any_stack(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        cancelled = card_cancel(tmp_path, "wid-a1", reason="abandoned")

        assert cancelled.stack == Stack.CANCELLED
        assert cancelled.history[-1].from_ == "in_progress"

    def test_refuses_empty_reason(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="reason"):
            card_cancel(tmp_path, "wid-a1", reason="   ")

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_cancel(tmp_path, "wid-a1", reason="x")


class TestCardAsk:
    def test_ask_moves_card_and_stores_the_question(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        asked = card_ask(
            tmp_path,
            "wid-a1",
            brief="the brief",
            question="Which spend limit?",
            options=["$10", "$50"],
            recommendation="$10",
            run_id="run-1",
        )

        assert asked.stack == Stack.DECISION_NEEDED
        assert len(asked.decisions) == 1
        decision = asked.decisions[0]
        assert decision.question == "Which spend limit?"
        assert decision.options == ["$10", "$50", "other: tell me"]
        assert decision.recommendation == "$10"
        assert decision.brief == "the brief"
        assert card_show(tmp_path, "wid-a1").decisions[0].brief == "the brief"
        assert decision.answer is None
        assert decision.answered_at is None
        assert decision.asked_at
        assert asked.history[-1].from_ == "in_progress"
        assert asked.history[-1].to == "decision_needed"
        assert asked.history[-1].run_id == "run-1"
        assert card_show(tmp_path, "wid-a1").stack == Stack.DECISION_NEEDED

    def test_ask_lists_other_once_when_the_caller_already_did(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        asked = card_ask(
            tmp_path,
            "wid-a1",
            brief="the brief",
            question="q?",
            options=["other: tell me", "a"],
        )
        assert asked.decisions[0].options == ["a", "other: tell me"]

    def test_ask_without_recommendation(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        asked = card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=["a"])
        assert asked.decisions[0].recommendation is None

    def test_refuses_unless_in_progress(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="in_progress"):
            card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=["a"])

    def test_refuses_empty_question(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="question"):
            card_ask(tmp_path, "wid-a1", brief="the brief", question="  ", options=["a"])

    def test_refuses_a_blank_brief(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        for blank in ("", "  "):
            with pytest.raises(CommandError, match="--brief"):
                card_ask(tmp_path, "wid-a1", brief=blank, question="q?", options=["a"])
        after = card_show(tmp_path, "wid-a1")
        assert after.decisions == []
        assert after.stack == Stack.IN_PROGRESS

    def test_refuses_no_options(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="option"):
            card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=[])

    def test_refuses_empty_recommendation(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="--recommendation"):
            card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=["a"], recommendation="")
        after = card_show(tmp_path, "wid-a1")
        assert after.decisions == []
        assert after.stack == Stack.IN_PROGRESS

    def test_refuses_whitespace_only_recommendation(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="--recommendation"):
            card_ask(
                tmp_path, "wid-a1", brief="the brief", question="q?", options=["a"], recommendation="  "
            )
        after = card_show(tmp_path, "wid-a1")
        assert after.decisions == []
        assert after.stack == Stack.IN_PROGRESS

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=["a"])


class TestCardAnswer:
    def _asked_card(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=["a", "b"])

    def test_answer_returns_to_top_of_todo_with_a_time(self, tmp_path):
        self._asked_card(tmp_path)
        answered = card_answer(tmp_path, "wid-a1", answer="a", run_id="run-2")

        assert answered.stack == Stack.TODO
        assert answered.position == 0
        assert answered.decisions[0].answer == "a"
        assert answered.decisions[0].answered_at
        assert answered.history[-1].from_ == "decision_needed"
        assert answered.history[-1].to == "todo"
        assert answered.history[-1].run_id == "run-2"
        assert card_show(tmp_path, "wid-a1").stack == Stack.TODO

    def test_answer_in_the_humans_own_words_is_recorded(self, tmp_path):
        self._asked_card(tmp_path)
        answered = card_answer(tmp_path, "wid-a1", answer="neither, use c")

        assert answered.stack == Stack.TODO
        assert answered.decisions[0].options == ["a", "b", "other: tell me"]
        assert answered.decisions[0].answer == "neither, use c"
        assert card_show(tmp_path, "wid-a1").decisions[0].answer == "neither, use c"

    def test_answer_done_goes_straight_to_done(self, tmp_path):
        self._asked_card(tmp_path)
        answered = card_answer(tmp_path, "wid-a1", answer="a", done=True)

        assert answered.stack == Stack.DONE
        assert answered.decisions[0].answer == "a"

    def test_refuses_unless_decision_needed(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="decision_needed"):
            card_answer(tmp_path, "wid-a1", answer="a")

    def test_refuses_empty_answer(self, tmp_path):
        self._asked_card(tmp_path)
        with pytest.raises(CommandError, match="answer"):
            card_answer(tmp_path, "wid-a1", answer="  ")

    def test_refuses_when_already_answered(self, tmp_path):
        self._asked_card(tmp_path)
        card_answer(tmp_path, "wid-a1", answer="a")
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.DECISION_NEEDED)
        with pytest.raises(CommandError, match="no open decision"):
            card_answer(tmp_path, "wid-a1", answer="a")

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_answer(tmp_path, "wid-a1", answer="a")


class TestCardProgress:
    def test_sets_phase_iteration_and_handoff(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        progressed = card_progress(
            tmp_path,
            "wid-a1",
            phase="testing",
            iteration=1,
            last_step="ran pytest, all green",
            next_step="open the PR",
            run_id="run-1",
        )

        assert progressed.stack == Stack.IN_PROGRESS
        assert progressed.phase == Phase.TESTING
        assert progressed.iteration == 1
        assert progressed.handoff.last_step == "ran pytest, all green"
        assert progressed.handoff.next_step == "open the PR"
        assert progressed.handoff.run_id == "run-1"
        assert progressed.history[-1].from_ == "in_progress"
        assert progressed.history[-1].to == "in_progress"
        assert progressed.history[-1].run_id == "run-1"
        assert card_show(tmp_path, "wid-a1").phase == Phase.TESTING

    def test_sets_pr(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        progressed = card_progress(
            tmp_path, "wid-a1", pr="https://github.com/test/widgets/pull/1"
        )
        assert progressed.pr == "https://github.com/test/widgets/pull/1"

    def test_rows_appear_in_order(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        card_progress(tmp_path, "wid-a1", phase="writing", last_step="started")
        card_progress(tmp_path, "wid-a1", phase="testing", last_step="ran tests")
        card_progress(tmp_path, "wid-a1", phase="ci", last_step="pushed branch")

        notes = [h.note for h in card_show(tmp_path, "wid-a1").history]
        assert notes[-3:] == [
            'progress: phase=writing, last_step="started"',
            'progress: phase=testing, last_step="ran tests"',
            'progress: phase=ci, last_step="pushed branch"',
        ]

    def test_leaves_fields_not_passed_untouched(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        card_progress(tmp_path, "wid-a1", phase="writing", iteration=2)

        progressed = card_progress(tmp_path, "wid-a1", last_step="still writing")
        assert progressed.phase == Phase.WRITING
        assert progressed.iteration == 2

    def test_refuses_unless_in_progress(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="in_progress"):
            card_progress(tmp_path, "wid-a1", phase="writing")

    def test_refuses_bad_phase(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="phase"):
            card_progress(tmp_path, "wid-a1", phase="bogus")

    def test_refuses_negative_iteration(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="iteration"):
            card_progress(tmp_path, "wid-a1", iteration=-1)

    def test_refuses_nothing_to_set(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="nothing to progress"):
            card_progress(tmp_path, "wid-a1")

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_progress(tmp_path, "wid-a1", phase="writing")

    def test_attach_appends_to_attachments(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        progressed = card_progress(tmp_path, "wid-a1", attach=["docs/screenshot.svg"])
        assert progressed.attachments == ["docs/screenshot.svg"]

        progressed = card_progress(
            tmp_path, "wid-a1", attach=["https://example.com/log.txt"]
        )
        assert progressed.attachments == [
            "docs/screenshot.svg",
            "https://example.com/log.txt",
        ]
        assert card_show(tmp_path, "wid-a1").attachments == progressed.attachments

    def test_attach_alone_is_enough_to_progress(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        progressed = card_progress(tmp_path, "wid-a1", attach=["a.svg"])
        assert progressed.history[-1].note == "progress: attach=a.svg"


class TestCardFinish:
    def test_finish_needs_pr_and_last_step(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        finished = card_finish(
            tmp_path,
            "wid-a1",
            pr="https://github.com/test/widgets/pull/1",
            last_step="ran pytest and ruff; both clean",
            run_id="run-1",
        )

        assert finished.stack == Stack.DONE
        assert finished.pr == "https://github.com/test/widgets/pull/1"
        assert finished.handoff.last_step == "ran pytest and ruff; both clean"
        assert finished.history[-1].from_ == "in_progress"
        assert finished.history[-1].to == "done"
        assert finished.history[-1].run_id == "run-1"
        assert card_show(tmp_path, "wid-a1").stack == Stack.DONE

    def test_refuses_without_pr_link(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="--pr"):
            card_finish(tmp_path, "wid-a1", last_step="all green")

    def test_refuses_without_verification_note(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="last-step"):
            card_finish(
                tmp_path, "wid-a1", pr="https://github.com/test/widgets/pull/1"
            )

    def test_no_code_card_finishes_without_a_pr(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)

        finished = card_finish(
            tmp_path, "wid-a1", last_step="findings: X is not feasible", no_code=True
        )
        assert finished.stack == Stack.DONE
        assert finished.pr is None
        assert finished.handoff.last_step == "findings: X is not feasible"

    def test_refuses_unless_in_progress(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="in_progress"):
            card_finish(
                tmp_path,
                "wid-a1",
                pr="https://github.com/test/widgets/pull/1",
                last_step="all green",
            )

    def test_refuses_open_decision(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        card_ask(tmp_path, "wid-a1", brief="the brief", question="q?", options=["a"])
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        with pytest.raises(CommandError, match="open decision"):
            card_finish(
                tmp_path,
                "wid-a1",
                pr="https://github.com/test/widgets/pull/1",
                last_step="all green",
            )

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_finish(
                tmp_path,
                "wid-a1",
                pr="https://github.com/test/widgets/pull/1",
                last_step="all green",
            )


class TestCardEdit:
    def test_edits_one_field_and_leaves_the_rest(self, tmp_path):
        _one_card(tmp_path)
        before = card_show(tmp_path, "wid-a1")

        edited = card_edit(
            tmp_path, "wid-a1", goal="new goal", note="n", run_id="run-1"
        )

        assert edited.goal == "new goal"
        assert edited.test == before.test
        assert edited.limits == before.limits
        assert edited.size == before.size
        assert edited.position == before.position
        assert edited.depends_on == before.depends_on
        assert edited.title == before.title
        assert card_show(tmp_path, "wid-a1").goal == "new goal"

    def test_edits_every_field_at_once(self, tmp_path):
        _one_card(tmp_path)
        card_new(tmp_path, card_id="wid-a0", project="widgets", title="t", goal="g", test="t")
        edited = card_edit(
            tmp_path,
            "wid-a1",
            goal="new goal",
            limits=["never touch prod"],
            test="new test",
            size="m",
            position=5,
            depends_on=["wid-a0"],
            note="n",
        )

        assert edited.goal == "new goal"
        assert edited.limits == ["docs/rules.md applies", "never touch prod"]
        assert edited.test == "new test"
        assert edited.size == Size.M
        assert edited.position == 5
        assert edited.depends_on == ["wid-a0"]

    def test_edits_the_title_with_a_history_row(self, tmp_path):
        _one_card(tmp_path)
        before = card_show(tmp_path, "wid-a1")

        edited = card_edit(tmp_path, "wid-a1", title="new title", run_id="run-1")

        assert edited.title == "new title"
        assert edited.goal == before.goal
        assert card_show(tmp_path, "wid-a1").title == "new title"
        assert len(edited.history) == len(before.history) + 1
        row = edited.history[-1]
        assert (row.from_, row.to, row.run_id) == ("backlog", "backlog", "run-1")
        assert row.note == "edited: title"

    def test_limits_edit_keeps_rules_first_even_when_not_passed(self, tmp_path):
        _one_card(tmp_path)
        edited = card_edit(tmp_path, "wid-a1", limits=["custom limit"], note="n")
        assert edited.limits == ["docs/rules.md applies", "custom limit"]

    def test_does_not_move_the_stack(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        edited = card_edit(tmp_path, "wid-a1", goal="new goal", note="n")

        assert edited.stack == Stack.IN_PROGRESS
        assert edited.history[-1].from_ == "in_progress"
        assert edited.history[-1].to == "in_progress"
        assert "goal" in edited.history[-1].note

    def test_refuses_empty_edit(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="nothing to edit"):
            card_edit(tmp_path, "wid-a1")

    def test_note_ends_the_history_row(self, tmp_path):
        # sys-d36: the why of a rewritten test lands in the edit's own history row,
        # so a test rewritten before a card's first start (no `card move`) keeps it.
        _one_card(tmp_path)
        card_edit(tmp_path, "wid-a1", size="m", note="n", run_id="run-1")
        edited = card_edit(
            tmp_path, "wid-a1", test="new test", note="x", run_id="run-1"
        )

        assert [h.note for h in edited.history[-2:]] == [
            "edited: size; why: n",
            "edited: test; why: x",
        ]
        assert edited.history[-1].run_id == "run-1"
        assert card_show(tmp_path, "wid-a1").history[-1].note == "edited: test; why: x"

    def test_note_follows_the_attempts_reset(self, tmp_path):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        card_progress(tmp_path, "wid-a1", phase="review", iteration=2)

        edited = card_edit(
            tmp_path,
            "wid-a1",
            test="at most two review rounds",
            limits=["no third review round"],
            note="the test asked for review until clean",
        )

        assert edited.history[-1].note == (
            "edited: limits, test; attempts reset (iteration 0, phase and next_step "
            "cleared); why: the test asked for review until clean"
        )

    def test_note_alone_appends_a_history_row_and_changes_nothing_else(
        self, tmp_path
    ):
        _one_card(tmp_path)
        _set_stack(tmp_path, "widgets", "wid-a1", Stack.IN_PROGRESS)
        card_progress(tmp_path, "wid-a1", phase="review", iteration=2)
        before = card_show(tmp_path, "wid-a1")

        edited = card_edit(tmp_path, "wid-a1", note="x", run_id="run-1")

        assert edited.history[-1].note.endswith("x")
        assert (edited.history[-1].from_, edited.history[-1].to) == (
            "in_progress",
            "in_progress",
        )
        assert edited.history[-1].run_id == "run-1"
        assert len(edited.history) == len(before.history) + 1
        assert edited.title == before.title
        assert edited.goal == before.goal
        assert edited.limits == before.limits
        assert edited.test == before.test
        assert edited.size == before.size
        assert edited.position == before.position
        assert edited.depends_on == before.depends_on
        assert edited.files == before.files
        assert edited.access == before.access
        assert edited.stack == before.stack
        assert edited.phase == before.phase
        assert edited.iteration == before.iteration
        assert edited.handoff.next_step == before.handoff.next_step
        assert card_show(tmp_path, "wid-a1").history[-1].note.endswith("x")

    def test_refuses_a_blank_note(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="--note is blank"):
            card_edit(tmp_path, "wid-a1", test="new test", note="  ")
        assert card_show(tmp_path, "wid-a1").test == "t"

    def test_refuses_bad_size(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="size"):
            card_edit(tmp_path, "wid-a1", size="xl")

    def test_refuses_missing_card(self, tmp_path):
        _new_project(tmp_path)
        with pytest.raises(CommandError, match="no card"):
            card_edit(tmp_path, "wid-a1", goal="g")

    def test_clear_depends_on_empties_the_list_with_a_history_row(self, tmp_path):
        # sys-t15: a card whose dependency was cancelled is blocked forever
        # (situation.ready() counts only done cards) — --clear-depends-on is
        # the command that unblocks it, in place of a hand edit of the JSON.
        _new_project(tmp_path)
        card_new(tmp_path, card_id="wid-d1", project="widgets", title="d1", goal="g", test="t")
        card_new(tmp_path, card_id="wid-d2", project="widgets", title="d2", goal="g", test="t")
        card_new(
            tmp_path,
            card_id="wid-a1",
            project="widgets",
            title="t",
            goal="g",
            test="t",
            depends_on=["wid-d1", "wid-d2"],
        )
        before = card_show(tmp_path, "wid-a1")
        assert before.depends_on == ["wid-d1", "wid-d2"]

        edited = card_edit(tmp_path, "wid-a1", clear_depends_on=True, run_id="run-1")

        assert edited.depends_on == []
        assert card_show(tmp_path, "wid-a1").depends_on == []
        row = edited.history[-1]
        assert row.run_id == "run-1"
        assert "--clear-depends-on" in row.note

    def test_refuses_depends_on_and_clear_depends_on_together(self, tmp_path):
        _one_card(tmp_path)
        with pytest.raises(CommandError, match="cannot both be given"):
            card_edit(
                tmp_path, "wid-a1", depends_on=["wid-a1"], clear_depends_on=True
            )


class TestCardDelete:
    def test_delete_removes_the_file(self, tmp_path):
        _one_card(tmp_path)
        path = tmp_path / "projects" / "widgets" / "cards" / "wid-a1.json"
        assert path.is_file()

        deleted_path = card_delete(tmp_path, "wid-a1")

        assert deleted_path == path
        assert not path.exists()

    def test_delete_leaves_other_cards_untouched(self, tmp_path):
        _new_project(tmp_path)
        card_new(
            tmp_path, card_id="wid-a1", project="widgets", title="t", goal="g", test="t"
        )
        card_new(
            tmp_path,
            card_id="wid-a2",
            project="widgets",
            title="t2",
            goal="g",
            test="t",
        )

        card_delete(tmp_path, "wid-a1")

        assert card_show(tmp_path, "wid-a2").id == "wid-a2"
        with pytest.raises(CommandError, match="no card"):
            card_show(tmp_path, "wid-a1")


class TestBoardExport:
    def test_export_contains_every_card(self, tmp_path):
        _new_project(tmp_path, slug="widgets", priority=1)
        _new_project(tmp_path, slug="other", priority=2)
        card_new(
            tmp_path, card_id="wid-a1", project="widgets", title="t", goal="g", test="t"
        )
        card_new(
            tmp_path,
            card_id="wid-a2",
            project="widgets",
            title="t2",
            goal="g",
            test="t",
        )
        card_new(
            tmp_path, card_id="oth-a1", project="other", title="t3", goal="g", test="t"
        )

        export = board_export(tmp_path)

        assert {p["slug"] for p in export["projects"]} == {"widgets", "other"}
        assert {c["id"] for c in export["cards"]} == {"wid-a1", "wid-a2", "oth-a1"}
        assert export["exported_at"]

    def test_export_empty_board(self, tmp_path):
        export = board_export(tmp_path)
        assert export["projects"] == []
        assert export["cards"] == []

    def test_exported_cards_round_trip_through_card_from_dict(self, tmp_path):
        from autonomouse.board.schema import card_from_dict

        _one_card(tmp_path)
        export = board_export(tmp_path)
        card = card_from_dict(export["cards"][0], source="export")
        assert card.id == "wid-a1"
