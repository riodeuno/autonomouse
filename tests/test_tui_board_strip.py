"""The status strip: `_strip_content` is checked as a pure function (same reasoning
`card_detail.format_card` documents for itself), one test through the live app
confirms the strip and the title are separate widgets — not a second line of the same
one — the way `test_tui_look.py` checks the border symbol and `test_tui_card_detail.py`
checks `format_card`, and one test drives the screen's own animation timer to confirm
only an in_progress card's strip moves."""

import json
import os
from datetime import UTC, datetime

import pytest
from textual.widgets import Static

from autonomouse.board import commands as board_commands
from autonomouse.board.schema import (
    Assigned,
    Card,
    Decision,
    Handoff,
    Phase,
    Size,
    Stack,
)
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import ANIMATION_INTERVAL, BoardItem, _strip_content
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.look import PHASE_MARK
from test_board_stopped_phase import hold_claim
from test_tui_board_detail import _open_system_board, board  # noqa: F401

NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)

_NO_ASSIGNMENT = Assigned(
    model=None, effort=None, agent_type=None, session_id=None, pid=None, picked_up_at=None
)
_HANDOFF = Handoff(last_step=None, next_step=None, updated_at=None, run_id=None)


def _card(**overrides) -> Card:
    base = {
        "id": "sys-x1",
        "project": "system",
        "title": "a title",
        "stack": Stack.TODO,
        "position": 0,
        "size": Size.S,
        "goal": "g",
        "limits": [],
        "test": "t",
        "depends_on": [],
        "assigned": _NO_ASSIGNMENT,
        "worktree": None,
        "branch": None,
        "base_branch": None,
        "pr": None,
        "phase": None,
        "iteration": 0,
        "handoff": _HANDOFF,
        "decisions": [],
        "history": [],
        "created_at": "2026-09-22T06:00:00Z",
        "updated_at": "2026-09-22T06:00:00Z",
    }
    base.update(overrides)
    return Card(**base)


def test_every_phase_has_its_own_mark():
    assert set(PHASE_MARK) == set(Phase)
    for icons in (PLAIN, NERD):
        assert set(icons.phase) == set(Phase)
        assert len(set(icons.phase.values())) == len(Phase)


def test_in_progress_strip_shows_phase_size_iteration_model_and_time():
    card = _card(
        stack=Stack.IN_PROGRESS,
        phase=Phase.TESTING,
        size=Size.M,
        iteration=2,
        assigned=Assigned(
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            session_id="s1",
            pid=1,
            picked_up_at="2026-09-22T10:00:00Z",
        ),
    )
    text = _strip_content(card, ascii_only=False, now=NOW).plain
    assert "⚗ testing" in text
    assert "[m]" in text
    assert "iter 2" in text
    assert "S" in text  # sonnet's model-tier initial
    assert "2h 0m ago" in text  # picked up 2 hours before NOW


def test_fresh_in_progress_card_has_no_iteration_no_model_and_says_starting():
    card = _card(stack=Stack.IN_PROGRESS)
    text = _strip_content(card, ascii_only=False, now=NOW).plain
    assert "starting" in text
    assert "iter" not in text


def test_done_card_leads_with_its_pr_number():
    # the column and the border already say done; the strip does not repeat it (sys-w105)
    card = _card(stack=Stack.DONE, pr="https://github.com/x/y/pull/42")
    text = _strip_content(card, ascii_only=False, now=NOW).plain
    assert text.startswith(f"{PLAIN.pr} PR 42")
    assert "done" not in text


def test_decision_needed_card_leads_with_its_open_count():
    card = _card(
        stack=Stack.DECISION_NEEDED,
        decisions=[
            Decision(
                id="d1",
                question="which?",
                options=["a", "b"],
                recommendation="a",
                asked_at="2026-09-22T07:00:00Z",
                answer=None,
                answered_at=None,
            )
        ],
    )
    text = _strip_content(card, ascii_only=False, now=NOW).plain
    assert text.startswith(f"{PLAIN.waiting} 1 open question")
    assert "decision needed" not in text  # the column and the border say it (sys-w105)


def test_todo_card_waiting_on_another_shows_it():
    card = _card(stack=Stack.TODO, depends_on=["sys-z9"])
    text = _strip_content(card, ascii_only=False, now=NOW).plain
    assert f"after {PLAIN.card} sys-z9" in text


def test_ascii_fallback_swaps_every_mark_for_ascii():
    in_progress = _card(stack=Stack.IN_PROGRESS, phase=Phase.WRITING)
    done = _card(stack=Stack.DONE)
    decision = _card(stack=Stack.DECISION_NEEDED)

    unicode_text = _strip_content(in_progress, ascii_only=False, now=NOW).plain
    ascii_text = _strip_content(in_progress, ascii_only=True, now=NOW).plain
    assert "✎" in unicode_text and "✎" not in ascii_text
    assert "W writing" in ascii_text

    # a done card with no pull request has no mark to swap
    assert _strip_content(done, ascii_only=True, now=NOW).plain == "[s]"

    # decision_needed's mark is already ASCII ("?"), so it is unchanged either way
    assert _strip_content(decision, ascii_only=True, now=NOW).plain == _strip_content(
        decision, ascii_only=False, now=NOW
    ).plain


async def test_strip_and_title_are_separate_widgets(board):  # noqa: F811
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        await pilot.press("right", "right")  # decision_needed -> todo -> in_progress
        await pilot.pause()
        item = next(i for i in screen.query(BoardItem) if i.card and i.card.id == "sys-b1")

        title = item.query_one("#card-title", Static)
        strip = item.query_one("#card-strip", Static)
        assert title is not strip
        assert isinstance(title, Static) and isinstance(strip, Static)
        assert "working card" in str(title.content)  # the title, nothing else
        assert "working card" not in str(strip.content)
        assert "testing" in str(strip.content)  # sys-b1's phase, from the `board` fixture


def _put_in_stack(root, card_id: str, stack: str) -> None:
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data["stack"] = stack
    path.write_text(json.dumps(data))


@pytest.fixture
def animation_board(tmp_path, monkeypatch):
    """One in_progress card, freshly started (no phase, so it shows "starting"),
    and one done card — just enough to tell an animating strip from a still one."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    board_commands.project_new(
        tmp_path,
        slug="system",
        name="System",
        goal="goal for system",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    board_commands.card_new(
        tmp_path,
        card_id="sys-c1",
        project="system",
        title="working card",
        goal="g",
        test="t",
        position=0,
    )
    _put_in_stack(tmp_path, "sys-c1", "in_progress")
    board_commands.card_new(
        tmp_path,
        card_id="sys-d1",
        project="system",
        title="finished card",
        goal="g",
        test="t",
        position=0,
    )
    _put_in_stack(tmp_path, "sys-d1", "done")
    # a live wake works the project: a card no wake works shows stopped, which never moves
    hold_claim(tmp_path, "system", pid=os.getpid())
    return tmp_path


async def test_in_progress_strip_animates_and_done_strip_does_not(animation_board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        in_progress = next(
            i for i in screen.query(BoardItem) if i.card and i.card.id == "sys-c1"
        )
        done = next(i for i in screen.query(BoardItem) if i.card and i.card.id == "sys-d1")
        in_progress_strip = in_progress.query_one("#card-strip", Static)
        done_strip = done.query_one("#card-strip", Static)
        first_in_progress = str(in_progress_strip.content)
        first_done = str(done_strip.content)

        await pilot.pause(2 * ANIMATION_INTERVAL + 0.1)  # past two animation ticks

        assert str(in_progress_strip.content) != first_in_progress
        assert str(done_strip.content) == first_done
