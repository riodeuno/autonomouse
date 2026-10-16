"""How each viewer screen is organized (sys-w105): what waits on the human comes first,
related facts sit together under a heading with an icon, and a fact the screen already
shows is not repeated.

- The project list gives, per project, what waits on the human and the wake status; no
  run id or priority.
- The board header lists the planning threads waiting on the human first, and says once
  which key opens them.
- A card's status strip does not name its stack; a decision card's strip leads with its
  open questions, a done card's with its pull request.
- A card's pane groups its facts under Progress, Goal and History; a decision shows its
  question before the asking agent's brief, in the pane and in the answer prompt.
- The project page shows where the project stands (cards, pull requests, wakes) before
  its goal and settings, and the brief last.

The board is a temporary one (`AUTONOMOUSE_HOME`) and the config folder too
(`AUTONOMOUSE_CONFIG`, which holds the PAUSE file the wake status reads). The project
page reads its pull requests through a fake `autonomouse.tui.project.gh_open_prs`.
"""

from __future__ import annotations

import json

import pytest
from textual.widgets import DataTable, Static

from autonomouse.board import commands as board_commands
from autonomouse.board import plans as board_plans
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.card_detail import CardDetail
from autonomouse.tui.decision import AnswerModal
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.look import ProjectStatus, project_status_pill
from autonomouse.tui.project import ProjectScreen
from autonomouse.tui.reading import ReadingView
from tui_wait import board_settled, pause_until

QUESTION = "Which layout should the page use?"
BRIEF = "The two layouts differ only in how wide the columns are."
PR = "https://github.com/test/system/pull/9"
RUN = "20260930-080000-abcd"
PROJECT_BRIEF = """# System

## Why this project exists

A board for the screenshots.

## Goal

"The board shows where the human is needed."

## Intent

The human finds what needs them first.

## Who uses it and where

One developer.

## How it works

Wakes.

## How we work

Cards.

## Out of scope

Everything else.
"""


def _patch_card(root, card_id: str, **fields) -> None:
    # test setup only: put a card straight into a state a full run would reach
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


@pytest.fixture
def board(tmp_path, monkeypatch):
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    for slug, priority in (("system", 1), ("integrations", 2)):
        board_commands.project_new(
            root,
            slug=slug,
            name=slug.title(),
            goal=f"goal for {slug}",
            repo=f"https://github.com/test/{slug}",
            repo_path=f"/home/test/{slug}",
            default_branch="main",
            priority=priority,
        )
    for card_id in ("sys-a1", "sys-p1", "sys-d1"):
        board_commands.card_new(
            root,
            card_id=card_id,
            project="system",
            title=f"title of {card_id}",
            goal=f"goal of {card_id}",
            test="t",
            size="m",
        )
    _patch_card(
        root,
        "sys-a1",
        stack="decision_needed",
        decisions=[
            {
                "id": "d1",
                "question": QUESTION,
                "options": ["wide", "narrow", "other: tell me"],
                "recommendation": "wide",
                "asked_at": "2026-09-30T07:00:00Z",
                "answer": None,
                "answered_at": None,
                "brief": BRIEF,
            }
        ],
    )
    _patch_card(
        root,
        "sys-p1",
        stack="in_progress",
        phase="writing",
        handoff={
            "last_step": "Wrote the test.",
            "next_step": "Run it.",
            "updated_at": "2026-09-30T08:10:00Z",
            "run_id": RUN,
        },
    )
    _patch_card(root, "sys-d1", stack="done", pr=PR)
    (root / "projects" / "system" / "brief.md").write_text(PROJECT_BRIEF)
    # thread ids in the order they open, so the planner's thread lists first by id
    monkeypatch.setattr(
        board_plans,
        "new_thread_id",
        iter(f"plan-20260930-070000-000{n}" for n in range(1, 3)).__next__,
    )
    working = board_plans.thread_open(root, "system", "add a light mode")
    board_plans.thread_say(root, working.id, "Which screens?")
    board_plans.thread_reply(root, working.id, "all of them")
    waiting = board_plans.thread_open(root, "system", "add a dark mode")
    board_plans.thread_say(root, waiting.id, "Which screens?")
    return root


async def _open_system_board(pilot) -> BoardScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    return await pause_until(pilot, lambda: pilot.app.screen, board_settled)


def _item(screen: BoardScreen, card_id: str) -> BoardItem:
    return next(i for i in screen.query(BoardItem) if i.card and i.card.id == card_id)


def _strip(screen: BoardScreen, card_id: str) -> str:
    return str(_item(screen, card_id).query_one("#card-strip", Static).render())


async def _click_card(pilot, screen: BoardScreen, card_id: str) -> str:
    """Click the card, as a mouse would, and return the pane's text once it shows it."""
    await pilot.click(_item(screen, card_id))
    pane = screen.query_one(CardDetail)
    await pause_until(
        pilot, lambda: pane.card, lambda card: card and card.id == card_id
    )
    return pane.source


def _in_order(text: str, *parts: str) -> bool:
    places = [text.index(part) for part in parts]
    return places == sorted(places)


async def test_the_project_list_says_what_waits_on_you_and_the_wake(board):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        table = app.query_one(DataTable)
        assert [str(c.label) for c in table.columns.values()] == [
            "project",
            "waiting on you",
            "wake",
            "slug",
        ]
        name, needs_you, wake, slug = (str(c) for c in table.get_row("system"))
        assert (name, slug) == ("System", "system")
        assert needs_you == f"{PLAIN.waiting} 1 decision, 1 planning thread"
        # a card is in progress, no wake holds it (sys-w116: the wake cell is a pill)
        assert wake == project_status_pill(ProjectStatus.WAITING).plain
        assert [str(c) for c in table.get_row("integrations")][1:3] == [
            "nothing",
            project_status_pill(ProjectStatus.WAITING).plain,
        ]

        # a wake takes the project: the row says ACTIVE, and names no run id
        path = board / "projects" / "system" / "project.json"
        data = json.loads(path.read_text())
        data["claim"] = {
            "run_id": RUN,
            "session_id": "s1",
            "pid": 1,
            "model": "fable",
            "claimed_at": "2026-09-30T08:00:00Z",
        }
        path.write_text(json.dumps(data))
        await pause_until(
            pilot,
            lambda: str(table.get_row("system")[2]),
            lambda text: text == project_status_pill(ProjectStatus.ACTIVE).plain,
        )
        assert not any(RUN in str(cell) for cell in table.get_row("system"))


async def test_the_board_header_lists_threads_waiting_on_you_first(board):
    app = AutonomouseApp()
    async with app.run_test(size=(140, 40)) as pilot:
        screen = await _open_system_board(pilot)
        header = str(screen.query_one("#project-header", Static).render())
        threads = [line for line in header.splitlines() if "plan:" in line]
        assert len(threads) == 2
        assert "add a dark mode" in threads[0] and "waiting on you" in threads[0]
        assert "add a light mode" in threads[1]
        assert "press p" in threads[0]
        assert "press p" not in threads[1]  # said once


async def test_a_card_strip_does_not_repeat_its_stack(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        decision = _strip(screen, "sys-a1")
        assert decision.startswith(f"{PLAIN.waiting} 1 open question")
        assert "decision needed" not in decision
        done = _strip(screen, "sys-d1")
        assert done.startswith(f"{PLAIN.pr} PR 9")
        assert "done" not in done


async def test_a_clicked_card_shows_its_facts_in_groups(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 60)) as pilot:
        screen = await _open_system_board(pilot)
        text = await _click_card(pilot, screen, "sys-p1")
        assert _in_order(
            text,
            f"## {PLAIN.progress} Progress",
            "Wrote the test.",
            "Run it.",
            f"## {PLAIN.goal} Goal",
            "goal of sys-p1",
            f"### {PLAIN.test} Test",
            f"## {PLAIN.history} History",
        )
        assert "Limits" not in text  # the only limit is the rules every card has


async def test_a_decision_asks_its_question_before_the_brief(board):
    app = AutonomouseApp()
    async with app.run_test(size=(240, 60)) as pilot:
        screen = await _open_system_board(pilot)
        text = await _click_card(pilot, screen, "sys-a1")
        assert _in_order(
            text,
            f"## {PLAIN.waiting} Your decision",
            "### Question",
            QUESTION,
            f"### {PLAIN.brief} Brief",
            BRIEF,
            "### Options",
            f"### {PLAIN.recommendation} Recommendation",
            "### Answer",
        )

        await pilot.press("a")
        modal = await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, AnswerModal)
        )
        prompt = modal.query_one("#question", ReadingView).source
        assert _in_order(
            prompt, "## Question", QUESTION, f"## {PLAIN.brief} Brief", BRIEF
        )


async def test_the_project_page_shows_where_the_project_stands_first(
    board, monkeypatch
):
    monkeypatch.setattr("autonomouse.tui.project.gh_open_prs", lambda _repo: [])
    app = AutonomouseApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await _open_system_board(pilot)
        await pilot.press("i")
        page = await pause_until(
            pilot, lambda: app.screen, lambda s: isinstance(s, ProjectScreen)
        )
        view = page.query_one("#project-details", ReadingView)
        text = await pause_until(
            pilot, lambda: view.source, lambda t: "Open pull requests" in t
        )
        assert _in_order(
            text,
            f"## {PLAIN.card} Cards",
            "Open pull requests",
            f"## {PLAIN.wake} Wakes",
            f"## {PLAIN.goal} Goal",
            f"## {PLAIN.settings} Settings",
            "**Priority:** 1",
            "\n---\n",
            "## Why this project exists",
        )
        cards = text[text.index("Cards") : text.index("Open pull requests")]
        assert _in_order(cards, "decision needed", "todo", "in progress", "done")
