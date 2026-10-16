"""The status line, status pill and description a project's screens show in their
header (sys-w113): the status the pill names, in the order the rules hold; the in
progress cards counted under each phase word; what waits on the human; the time since
the project last changed; the one sentence on what the project is about; and a board
read error raised as every other reader raises it."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from textual.content import Content

from autonomouse.board import commands as board_commands
from autonomouse.board import plans as board_plans
from autonomouse.board.brief import brief_path
from autonomouse.tui import project_header
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.live import READ_ERRORS
from autonomouse.tui.look import ProjectStatus, project_status_pill
from test_board_stopped_phase import _gone_pid, hold_claim
from test_tui_icons import private_use

GOAL = "This project builds the system itself: the board and the screen. It also wires the rest."


def _card_path(root: Path, card_id: str) -> Path:
    return root / "projects" / "system" / "cards" / f"{card_id}.json"


def _patch(path: Path, **fields) -> None:
    # test setup only: write fields a wake would write through its own commands
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


def _card(root: Path, card_id: str, stack: str, **fields) -> None:
    board_commands.card_new(
        root, card_id=card_id, project="system", title=card_id, goal="g", test="t"
    )
    _patch(_card_path(root, card_id), stack=stack, **fields)


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Project system with no card, no claim, no thread and no PAUSE file."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir()
    board_commands.project_new(
        root,
        slug="system",
        name="Autonomouse system",
        goal=GOAL,
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    return root


def _pause(root: Path) -> None:
    (root.parent / "config" / "PAUSE").write_text("")


def _style_of(line: Content, text: str) -> str:
    """The style of the span that covers `text` in `line`."""
    start = line.plain.index(text)
    styles = [
        str(span.style)
        for span in line.spans
        if span.start <= start and start + len(text) <= span.end
    ]
    assert styles, f"no span covers {text!r}"
    return styles[-1]


# ------------------------------------------------------------- the status --


@pytest.mark.parametrize(
    ("setup", "status"),
    [
        ("working wake, PAUSE file", ProjectStatus.ACTIVE),
        ("dead wake holds the claim, PAUSE file", ProjectStatus.PAUSED),
        ("dead wake holds the claim", ProjectStatus.STOPPED),
        ("nothing open", ProjectStatus.DONE),
        ("todo cards, no claim", ProjectStatus.WAITING),
    ],
)
def test_the_pill_names_the_first_status_that_holds(board, setup, status):
    _card(board, "sys-d1", "done")
    if setup != "nothing open":
        _card(board, "sys-t1", "todo")
    if setup.startswith("working wake"):
        hold_claim(board, "system", pid=os.getpid())
    if setup.startswith("dead wake"):
        hold_claim(board, "system", pid=_gone_pid())
    if setup.endswith("PAUSE file"):
        _pause(board)

    assert project_header.project_status(board, "system") is status
    pill = project_status_pill(status)
    assert status.value.upper() in pill.plain
    # the whole line leads with the same pill
    line = project_header.status_line(board, "system")
    assert line.plain.startswith(pill.plain)


# ------------------------------------------------------------ the line --


def _in_progress(board: Path) -> None:
    """Four in_progress cards: two writing, one testing, one no phase yet."""
    _card(board, "sys-w1", "in_progress", phase="writing")
    _card(board, "sys-w2", "in_progress", phase="writing")
    _card(board, "sys-w3", "in_progress", phase="testing")
    _card(board, "sys-w4", "in_progress", phase=None)
    _card(board, "sys-t1", "todo")


def test_the_line_counts_in_progress_cards_by_phase_word_while_a_wake_works(board):
    _in_progress(board)
    hold_claim(board, "system", pid=os.getpid())
    line = project_header.status_line(board, "system", icons=PLAIN)
    assert "cards ✎ 2 writing · ⚗ 1 testing · ◐ 1 starting | " in line.plain, line.plain
    # only the words some card has
    for word in ("ci", "review", "stopped"):
        assert f" {word}" not in line.plain.split("|")[1]
    # left to right: pill, slug, cards, waiting on you, last change
    order = ["ACTIVE", "system", "cards", "waiting on you", "last change"]
    positions = [line.plain.index(word) for word in order]
    assert positions == sorted(positions), line.plain


def test_the_line_counts_every_in_progress_card_stopped_when_its_wake_is_dead(board):
    _in_progress(board)
    hold_claim(board, "system", pid=_gone_pid())
    line = project_header.status_line(board, "system", icons=PLAIN)
    assert "cards ■ 4 stopped | " in line.plain, line.plain
    assert "writing" not in line.plain


def test_the_line_says_no_card_is_in_progress(board):
    _card(board, "sys-t1", "todo")
    line = project_header.status_line(board, "system", icons=PLAIN)
    assert "cards no card in progress | " in line.plain, line.plain


def test_waiting_on_you_counts_decision_cards_and_threads_whose_turn_is_the_humans(
    board,
):
    line = project_header.status_line(board, "system")
    assert "waiting on you 0" in line.plain
    assert "$text-warning" not in _style_of(line, "waiting on you 0")

    _card(board, "sys-q1", "decision_needed")
    asked = board_plans.thread_open(board, "system", "plan the header")
    board_plans.thread_say(board, asked.id, "Which colour?")  # the human's turn
    board_plans.thread_open(board, "system", "plan the footer")  # the planner's turn
    line = project_header.status_line(board, "system")
    assert "waiting on you 2" in line.plain, line.plain
    assert "$text-warning" in _style_of(line, "waiting on you 2")


def test_last_change_is_the_newest_thread_message_when_it_is_newer_than_every_card(
    board,
):
    old = "2026-09-30T08:00:00Z"
    _card(board, "sys-t1", "todo")
    _card(board, "sys-d1", "done")
    for card_id in ("sys-t1", "sys-d1"):
        _patch(_card_path(board, card_id), updated_at=old, created_at=old)
    _patch(board / "projects" / "system" / "project.json", updated_at=old)
    thread = board_plans.thread_open(board, "system", "plan the header")
    path = board_plans.thread_path(board, thread.id)
    data = json.loads(path.read_text())
    data["opened_at"] = "2026-09-30T08:30:00Z"
    data["messages"][0]["at"] = "2026-09-30T08:30:00Z"
    data["messages"].append(
        {"at": "2026-09-30T09:57:00Z", "from": "planner", "text": "A question."}
    )
    path.write_text(json.dumps(data))

    now = datetime(2026, 9, 30, 10, 0, tzinfo=UTC)
    line = project_header.status_line(board, "system", now=now)
    assert line.plain.endswith("last change 3m ago"), line.plain


def test_the_plain_icons_put_no_nerd_font_glyph_in_the_line(board):
    _in_progress(board)
    hold_claim(board, "system", pid=os.getpid())
    plain = project_header.status_line(board, "system", icons=PLAIN).plain
    assert not private_use(plain), plain
    nerd = project_header.status_line(board, "system", icons=NERD).plain
    assert private_use(nerd)


# ------------------------------------------------------- the description --


def test_the_description_is_the_first_sentence_of_the_goal_without_a_brief(board):
    assert project_header.description(board, "system") == (
        "This project builds the system itself: the board and the screen."
    )


def test_the_description_is_the_briefs_goal_sentence_without_its_quotes(board):
    brief_path(board, "system").write_text(
        "# system — project brief\n\n"
        "## Why this project exists\n\nBecause.\n\n"
        '## Goal\n\n"You install it with one command\nand it works your cards."\n\n'
        "## Intent\n\nThe creator's time.\n"
    )
    assert project_header.description(board, "system") == (
        "You install it with one command and it works your cards."
    )


# ------------------------------------------------------ a board read error --


def test_a_card_file_that_cannot_be_read_raises_a_board_read_error(board):
    _card(board, "sys-t1", "todo")
    _card_path(board, "sys-t1").write_text("{ not json")
    with pytest.raises(READ_ERRORS):
        project_header.status_line(board, "system")
