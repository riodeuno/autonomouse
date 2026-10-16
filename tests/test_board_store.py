"""Tests for the board module."""

import json
from pathlib import Path

import pytest

from autonomouse.board.schema import (
    Assigned,
    BoardValidationError,
    Card,
    Claim,
    Decision,
    Handoff,
    HistoryEntry,
    Phase,
    Project,
    Size,
    Stack,
)
from autonomouse.board.store import load_card, load_project, save_card, save_project


class TestProjectRoundTrip:
    """Test round-tripping a project through save/load."""

    def test_project_roundtrip(self, tmp_path):
        """Build a project with every field populated, save, load, assert equality."""
        project = Project(
            slug="test-project",
            name="Test Project",
            goal="This is a test project for testing round-trip behavior.",
            repo="https://github.com/test/test",
            repo_path="/home/test/projects/test",
            default_branch="main",
            priority=1,
            tracker={"kind": "linear", "team": "test"},
            claim=Claim(
                run_id="20260922-120000-abcd",
                session_id="sess-123",
                pid=12345,
                model="opus",
                claimed_at="2026-09-22T12:00:00Z",
            ),
            created_at="2026-09-22T06:00:00Z",
            updated_at="2026-09-22T12:00:00Z",
        )

        path = tmp_path / "project.json"
        save_project(path, project)
        loaded = load_project(path)

        assert loaded == project

    def test_done_at_round_trips_and_is_absent_while_the_project_is_not_done(self, tmp_path):
        """`done_at` is written only while set: a project that is not done has the file
        older versions wrote, and a done one keeps its time through save and load."""
        project = Project(
            slug="p",
            name="P",
            goal="g",
            repo="https://github.com/test/test",
            repo_path="/home/test/p",
            default_branch="main",
            priority=1,
            tracker=None,
            claim=Claim(run_id=None, session_id=None, pid=None, model=None, claimed_at=None),
            created_at="2026-10-16T06:00:00Z",
            updated_at="2026-10-16T06:00:00Z",
        )
        path = tmp_path / "project.json"

        save_project(path, project)
        assert "done_at" not in json.loads(path.read_text())
        assert load_project(path).done_at is None

        project.done_at = "2026-10-16T09:00:00Z"
        save_project(path, project)
        assert json.loads(path.read_text())["done_at"] == "2026-10-16T09:00:00Z"
        assert load_project(path) == project
        assert load_project(path).extra == {}  # known, not carried as an unknown field


class TestCardRoundTrip:
    """Test round-tripping a card through save/load."""

    def test_card_roundtrip(self, tmp_path):
        """Build a card with every field populated, save, load, assert equality."""
        card = Card(
            id="sys-b1",
            project="system",
            title="load, validate, save",
            stack=Stack.TODO,
            position=1,
            size=Size.S,
            goal="Read a project or card file into a checked object.",
            limits=["docs/rules.md applies"],
            test="uv run pytest passes",
            depends_on=[],
            assigned=Assigned(
                model="opus",
                effort="high",
                agent_type="worker-high",
                session_id="sess-456",
                pid=54321,
                picked_up_at="2026-09-22T12:00:00Z",
            ),
            worktree="/home/test/worktrees/system/sys-b1",
            branch="card/sys-b1",
            base_branch="main",
            pr="https://github.com/test/test/pull/1",
            phase=Phase.WRITING,
            iteration=1,
            handoff=Handoff(
                last_step="Working on validation logic",
                next_step="Write tests",
                updated_at="2026-09-22T12:00:00Z",
                run_id="20260922-120000-abcd",
            ),
            decisions=[
                Decision(
                    id="dec-1",
                    question="Should we use dataclasses?",
                    options=["yes", "no"],
                    recommendation="yes",
                    asked_at="2026-09-22T06:00:00Z",
                    answer="yes",
                    answered_at="2026-09-22T12:00:00Z",
                )
            ],
            history=[
                HistoryEntry(
                    at="2026-09-22T06:00:00Z",
                    run_id="seed",
                    from_=None,
                    to="todo",
                    note="seeded by planning",
                ),
                HistoryEntry(
                    at="2026-09-22T12:00:00Z",
                    run_id="20260922-120000-abcd",
                    from_="todo",
                    to="in_progress",
                    note="started work",
                ),
            ],
            created_at="2026-09-22T06:00:00Z",
            updated_at="2026-09-22T12:00:00Z",
            attachments=["docs/screenshot.svg", "https://example.com/log.txt"],
        )

        path = tmp_path / "card.json"
        save_card(path, card)
        loaded = load_card(path)

        assert loaded == card


class TestCardPredatingAttachments:
    """A card written before `attachments` existed has no such key."""

    def test_missing_attachments_loads_as_empty_and_self_heals(self, tmp_path):
        path = tmp_path / "card.json"
        path.write_text(
            '{"id":"test","project":"system","title":"test","stack":"todo",'
            '"position":1,"size":"s","goal":"test","limits":[],"test":"test","depends_on":[],'
            '"assigned":{"model":null,"effort":null,"agent_type":null,"session_id":null,"pid":null,"picked_up_at":null},'
            '"worktree":null,"branch":null,"base_branch":null,"pr":null,"phase":null,"iteration":0,'
            '"handoff":{"last_step":null,"next_step":null,"updated_at":null,"run_id":null},'
            '"decisions":[],"history":[],"created_at":"2026-09-22T06:00:00Z","updated_at":"2026-09-22T06:00:00Z"}'
        )

        card = load_card(path)
        assert card.attachments == []

        save_card(path, card)
        assert json.loads(path.read_text())["attachments"] == []


class TestSeedCard:
    """Test loading a real seed card."""

    def test_load_seed_card(self):
        """Load a real seed card and verify it parses correctly."""
        seed_path = (
            Path(__file__).parent.parent
            / "seed"
            / "projects"
            / "system"
            / "cards"
            / "sys-a1.json"
        )
        if not seed_path.exists():
            pytest.skip(f"Seed file not found at {seed_path}")

        card = load_card(seed_path)
        assert card.id == "sys-a1"
        assert card.project == "system"
        assert card.stack == Stack.BACKLOG


class TestInvalidJson:
    """Test handling of invalid JSON."""

    def test_invalid_json_rejected(self, tmp_path):
        """Attempt to load syntactically broken JSON and assert BoardValidationError."""
        bad_path = tmp_path / "bad.json"
        bad_path.write_text("{not json")

        with pytest.raises(BoardValidationError) as exc_info:
            load_card(bad_path)

        assert str(bad_path) in str(exc_info.value)
        assert "not valid JSON" in str(exc_info.value)

    def test_save_does_not_corrupt_on_invalid_object(self, tmp_path):
        """Save a manually-constructed invalid card, old file untouched."""
        # Create a baseline valid card file
        baseline_path = tmp_path / "baseline.json"
        baseline_card = Card(
            id="sys-a1",
            project="system",
            title="test",
            stack=Stack.TODO,
            position=1,
            size=Size.XS,
            goal="test",
            limits=[],
            test="test",
            depends_on=[],
            assigned=Assigned(None, None, None, None, None, None),
            worktree=None,
            branch=None,
            base_branch=None,
            pr=None,
            phase=None,
            iteration=0,
            handoff=Handoff(None, None, None, None),
            decisions=[],
            history=[],
            created_at="2026-09-22T06:00:00Z",
            updated_at="2026-09-22T06:00:00Z",
        )
        save_card(baseline_path, baseline_card)
        baseline_bytes = baseline_path.read_bytes()

        # Try to save an invalid card with a bad stack
        invalid_card = Card(
            id="sys-b1",
            project="system",
            title="test",
            stack=Stack.TODO,  # Will be manually corrupted
            position=1,
            size=Size.S,
            goal="test",
            limits=[],
            test="test",
            depends_on=[],
            assigned=Assigned(None, None, None, None, None, None),
            worktree=None,
            branch=None,
            base_branch=None,
            pr=None,
            phase=None,
            iteration=0,
            handoff=Handoff(None, None, None, None),
            decisions=[],
            history=[],
            created_at="2026-09-22T06:00:00Z",
            updated_at="2026-09-22T06:00:00Z",
        )

        # Corrupt the card by setting stack to invalid value via object.__setattr__
        object.__setattr__(invalid_card, "stack", "invalid_stack")

        with pytest.raises(BoardValidationError):
            save_card(baseline_path, invalid_card)

        # Verify baseline file is unchanged
        assert baseline_path.read_bytes() == baseline_bytes

        # Verify no temp files left behind
        tmp_files = list(baseline_path.parent.glob(f".{baseline_path.name}.*.tmp"))
        assert len(tmp_files) == 0


class TestMissingFields:
    """Test rejection of missing/wrong-type fields."""

    def test_missing_goal_field(self, tmp_path):
        """Card JSON missing 'goal' is rejected with plain message."""
        bad_path = tmp_path / "missing_goal.json"
        bad_path.write_text('{"id":"test","project":"system","title":"test"}')

        with pytest.raises(BoardValidationError) as exc_info:
            load_card(bad_path)

        assert "goal" in str(exc_info.value)

    def test_wrong_type_priority(self, tmp_path):
        """Project JSON with string priority is rejected."""
        bad_path = tmp_path / "bad_priority.json"
        bad_path.write_text(
            '{"slug":"test","name":"test","goal":"test","repo":"http://test",'
            '"repo_path":"/test","default_branch":"main","priority":"not_an_int",'
            '"tracker":null,"claim":{"run_id":null,"session_id":null,"pid":null,"model":null,"claimed_at":null},'
            '"created_at":"2026-09-22T06:00:00Z","updated_at":"2026-09-22T06:00:00Z"}'
        )

        with pytest.raises(BoardValidationError) as exc_info:
            load_project(bad_path)

        assert "priority" in str(exc_info.value)

    def test_invalid_stack_enum(self, tmp_path):
        """Card with invalid stack value is rejected."""
        bad_path = tmp_path / "bad_stack.json"
        bad_path.write_text(
            '{"id":"test","project":"system","title":"test","stack":"bogus",'
            '"position":1,"size":"s","goal":"test","limits":[],"test":"test","depends_on":[],'
            '"assigned":{"model":null,"effort":null,"agent_type":null,"session_id":null,"pid":null,"picked_up_at":null},'
            '"worktree":null,"branch":null,"base_branch":null,"pr":null,"phase":null,"iteration":0,'
            '"handoff":{"last_step":null,"next_step":null,"updated_at":null,"run_id":null},'
            '"decisions":[],"history":[],"created_at":"2026-09-22T06:00:00Z","updated_at":"2026-09-22T06:00:00Z"}'
        )

        with pytest.raises(BoardValidationError) as exc_info:
            load_card(bad_path)

        assert "stack" in str(exc_info.value)
        assert "bogus" in str(exc_info.value)


class TestAtomicWrite:
    """Test atomic write behavior on crash."""

    def test_crash_between_write_and_rename_leaves_old_file(
        self, tmp_path, monkeypatch
    ):
        """Simulate a crash between temp write and os.replace; old file untouched."""
        # Create baseline
        baseline_path = tmp_path / "baseline.json"
        baseline_card = Card(
            id="sys-a1",
            project="system",
            title="original",
            stack=Stack.TODO,
            position=1,
            size=Size.XS,
            goal="test",
            limits=[],
            test="test",
            depends_on=[],
            assigned=Assigned(None, None, None, None, None, None),
            worktree=None,
            branch=None,
            base_branch=None,
            pr=None,
            phase=None,
            iteration=0,
            handoff=Handoff(None, None, None, None),
            decisions=[],
            history=[],
            created_at="2026-09-22T06:00:00Z",
            updated_at="2026-09-22T06:00:00Z",
        )
        save_card(baseline_path, baseline_card)
        baseline_bytes = baseline_path.read_bytes()

        # Build a new card to save
        new_card = Card(
            id="sys-b1",
            project="system",
            title="new",
            stack=Stack.TODO,
            position=2,
            size=Size.S,
            goal="test",
            limits=[],
            test="test",
            depends_on=[],
            assigned=Assigned(None, None, None, None, None, None),
            worktree=None,
            branch=None,
            base_branch=None,
            pr=None,
            phase=None,
            iteration=0,
            handoff=Handoff(None, None, None, None),
            decisions=[],
            history=[],
            created_at="2026-09-22T06:00:00Z",
            updated_at="2026-09-22T06:00:00Z",
        )

        # Monkeypatch os.replace to raise an exception
        def failing_replace(src, dst):
            raise RuntimeError("Simulated crash during replace")

        monkeypatch.setattr("os.replace", failing_replace)

        # Attempt to save; should raise
        with pytest.raises(RuntimeError, match="Simulated crash"):
            save_card(baseline_path, new_card)

        # Verify baseline file is unchanged
        assert baseline_path.read_bytes() == baseline_bytes

        # Verify no temp files left behind
        tmp_files = list(baseline_path.parent.glob(f".{baseline_path.name}.*.tmp"))
        assert len(tmp_files) == 0
