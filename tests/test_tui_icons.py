"""The viewer's icons: Nerd Font glyphs when `viewer.nerd_font` is on, plain Unicode
symbols in the same places when it is off (`autonomouse.tui.icons`).

The two walks below open every screen of the viewer on one board that holds every
state an icon marks: each stack and phase, a question answered and one open, planning
threads waiting on the human, on the planner and closed, a wake holding the project, a
pull request, a card another waits on, and a board file that cannot be read. With the
setting off, no text of any screen holds a character of a Private Use Area, where every
Nerd Font glyph sits; with it on, every screen shows its Nerd Font icons. The walk with
the setting on reaches the cards and a thread with the mouse.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual.screen import Screen
from textual.widgets import DataTable, Markdown, OptionList, Static

from autonomouse import cli
from autonomouse.board import commands as board_commands
from autonomouse.board.plans import (
    ThreadStatus,
    thread_close,
    thread_open,
    thread_reply,
    thread_say,
)
from autonomouse.board.runs import Run, RunStatus, save_run
from autonomouse.board.schema import Phase, Stack
from autonomouse.config import DEFAULTS, load_config, set_key, viewer_nerd_font
from autonomouse.overview import ProjectDetails, project_details
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.decision import AnswerModal, CancelModal
from autonomouse.tui.icons import NERD, PLAIN, Icons, icon_set
from autonomouse.tui.plan import PlanModal, ReplyModal, ThreadsScreen
from autonomouse.tui.project import ProjectScreen, format_project
from tui_wait import focus_lands, pause_until

RUN_ID = "20260930-080000-abcd"
PR_URL = "https://github.com/test/system/pull/9"


def private_use(text: str) -> list[str]:
    """The characters of `text` in a Private Use Area: U+E000 to U+F8FF, and every code
    point from U+F0000 up (planes 15 and 16). Every Nerd Font glyph sits there."""
    return sorted(
        {c for c in text if 0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000}
    )


# The icons the viewer's new look adds (sys-w110).
NEW_ICONS = (
    "project",
    "worker",
    "clock",
    "elapsed",
    "size",
    "model",
    "iteration",
    "paused",
    "stopped",
    "limits",
    "test",
    "attachment",
    "step",
    "brief",
    "recommendation",
)


def _all_icons(icons: Icons) -> dict[str, str]:
    """Every icon of the set by a name for it."""
    named = {
        "passed": icons.passed,
        "failed": icons.failed,
        "waiting": icons.waiting,
        "live": icons.live,
        "card": icons.card,
        "pr": icons.pr,
        "thread": icons.thread,
        "wake": icons.wake,
        "goal": icons.goal,
        "progress": icons.progress,
        "history": icons.history,
        "settings": icons.settings,
    }
    named |= {name: getattr(icons, name) for name in NEW_ICONS}
    named |= {f"stack {s.value}": icon for s, icon in icons.stack.items()}
    named |= {f"phase {p.value}": icon for p, icon in icons.phase.items()}
    named |= {f"thread {s.value}": icon for s, icon in icons.thread_status.items()}
    return named


# ------------------------------------------------------------------- the icon set --


@pytest.mark.parametrize("icons", [PLAIN, NERD], ids=["plain", "nerd"])
def test_each_form_has_an_icon_for_every_state_and_kind_one_cell_wide(icons):
    assert set(icons.stack) == set(Stack)
    assert set(icons.phase) == set(Phase)
    assert set(icons.thread_status) == set(ThreadStatus)
    assert len(set(icons.stack.values())) == len(Stack)
    assert len(set(icons.phase.values())) == len(Phase)
    for name, icon in _all_icons(icons).items():
        assert len(icon) == 1, name
        assert cell_len(icon) == 1, f"{name} {icon!r} is {cell_len(icon)} cells wide"
    kinds = [
        icons.card,
        icons.pr,
        icons.thread,
        icons.wake,
        icons.goal,
        icons.progress,
        icons.history,
        icons.settings,
    ]
    assert len(set(kinds)) == len(kinds)


@pytest.mark.parametrize("icons", [PLAIN, NERD], ids=["plain", "nerd"])
def test_each_new_icon_is_one_cell_wide_and_marks_no_meaning_another_icon_has(icons):
    named = _all_icons(icons)
    for name in NEW_ICONS:
        icon = getattr(icons, name)
        assert len(icon) == 1 and cell_len(icon) == 1, name
        others = {v for n, v in named.items() if n != name}
        assert icon not in others, f"{name} {icon!r} is also another icon"


def test_a_pill_has_rounded_ends_only_in_the_nerd_font_form():
    assert PLAIN.pill_ends == ("", "")
    for end in NERD.pill_ends:
        assert private_use(end) == [end]
        assert cell_len(end) == 1


def test_the_plain_form_holds_no_nerd_font_glyph_and_the_nerd_form_only_those():
    for name, icon in _all_icons(PLAIN).items():
        assert not private_use(icon), name
    for name, icon in _all_icons(NERD).items():
        assert private_use(icon) == [icon], name


def test_the_setting_is_off_by_default_and_config_set_turns_it_on(tmp_path):
    path = tmp_path / "config.toml"
    assert DEFAULTS["viewer"]["nerd_font"] is False
    assert viewer_nerd_font(load_config(path)) is False
    assert icon_set(nerd_font=False) is PLAIN

    assert set_key(path, "viewer.nerd_font", "true") is True
    assert viewer_nerd_font(load_config(path)) is True
    assert icon_set(nerd_font=True) is NERD

    path.write_text('[viewer]\nnerd_font = "yes"\n')  # a hand edit
    with pytest.raises(TypeError, match="viewer.nerd_font"):
        viewer_nerd_font(load_config(path))


@pytest.mark.parametrize("on", [False, True])
def test_the_autonomouse_command_starts_the_viewer_with_the_configs_setting(
    tmp_path, monkeypatch, on
):
    config = tmp_path / "config"
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    if on:
        set_key(config / "config.toml", "viewer.nerd_font", "true")
    started: list[Icons] = []
    monkeypatch.setattr(AutonomouseApp, "run", lambda app: started.append(app.icons))
    monkeypatch.setattr(sys, "argv", ["autonomouse"])
    with pytest.raises(SystemExit):
        cli.main()
    assert started == [NERD if on else PLAIN]


# ------------------------------------------------------------------------ the board --


def _patch_card(root: Path, card_id: str, **fields) -> None:
    # test setup only: sets the fields no command sets directly
    path = root / "projects" / "system" / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


def _decision(answer: str | None) -> dict:
    return {
        "id": "d1",
        "question": "which layout?",
        "options": ["a", "b", "other: tell me"],
        "recommendation": "a",
        "asked_at": "2026-09-30T07:00:00Z",
        "answer": answer,
        "answered_at": None if answer is None else "2026-09-30T07:30:00Z",
    }


def _assigned(model: str) -> dict:
    return {
        "model": model,
        "effort": "high",
        "agent_type": "worker-high",
        "session_id": "s1",
        "pid": 1,
        "picked_up_at": "2026-09-30T07:00:00Z",
    }


@pytest.fixture
def board(tmp_path, monkeypatch):
    """One project, `system`, whose board holds every state an icon marks, held by a
    wake, with three planning threads."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "config"))
    board_commands.project_new(
        root,
        slug="system",
        name="System",
        goal="The system builds itself.",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )

    def new(card_id: str, title: str, **fields) -> None:
        board_commands.card_new(
            root, card_id=card_id, project="system", title=title, goal="g", test="t"
        )
        _patch_card(root, card_id, **fields)

    new(
        "sys-a1",
        "needs a human's call",
        stack="decision_needed",
        phase="ci",
        decisions=[_decision(None)],
    )
    new("sys-t1", "waits on another card", stack="todo", depends_on=["sys-d1"])
    for n, phase in enumerate(Phase, 1):
        new(
            f"sys-p{n}",
            f"a card in {phase.value}",
            stack="in_progress",
            phase=phase.value,
            assigned=_assigned("opus"),
        )
    new(
        "sys-d1",
        "shipped it",
        stack="done",
        pr=PR_URL,
        decisions=[_decision("a")],
        history=[
            {
                "at": "2026-09-30T07:00:00Z",
                "run_id": RUN_ID,
                "from": "in_progress",
                "to": "done",
                "note": "finished",
            }
        ],
    )
    new("sys-b1", "someday", stack="backlog")
    new("sys-c1", "not needed after all", stack="cancelled")

    save_run(
        root,
        Run(
            id=RUN_ID,
            project="system",
            session_id=None,
            # this test's own process: the wake runs, so its cards show their phases
            pid=os.getpid(),
            model="fable",
            started_at="2026-09-30T08:00:00Z",
            ended_at=None,
            cards=[],
            jev=[],
            notify_failures=[],
            result=None,
            end_reason=None,
            status=RunStatus.STOPPED,
            summary="the wake that holds the project",
        ),
    )
    project = root / "projects" / "system" / "project.json"
    data = json.loads(project.read_text())
    data["claim"] = {
        "run_id": RUN_ID,
        "session_id": "s1",
        "pid": 1,
        "model": "fable",
        "claimed_at": "2026-09-30T08:00:00Z",
    }
    project.write_text(json.dumps(data))

    waiting_human = thread_open(root, "system", "add a dark mode")
    thread_say(root, waiting_human.id, "which screens?")
    waiting_planner = thread_open(root, "system", "add a light mode")
    thread_say(root, waiting_planner.id, "which screens?")
    thread_reply(root, waiting_planner.id, "all of them")
    closed = thread_open(root, "system", "add a sepia mode")
    thread_close(root, closed.id, "done: sys-d1")

    # the project page's open pull requests: sys-d1's, with no call to gh
    monkeypatch.setattr(
        "autonomouse.tui.project.gh_open_prs",
        lambda _repo: [{"url": PR_URL, "title": "ship it"}],
    )
    return root


def screen_text(screen: Screen) -> str:
    """Every piece of text the screen holds: what its widgets show, on screen or
    scrolled out of view (a label, a border title, a table cell, a document's source,
    an option), and the screen as the terminal draws it."""
    parts = [screen.app.export_screenshot()]
    for widget in screen.walk_children(with_self=True):
        parts += [str(widget.border_title or ""), str(widget.border_subtitle or "")]
        if isinstance(widget, Static):
            parts.append(str(widget.content))
        elif isinstance(widget, Markdown):
            parts.append(widget.source)
        elif isinstance(widget, DataTable):
            parts += [str(label) for label in widget.columns.values()]
            for row in range(widget.row_count):
                parts += [str(cell) for cell in widget.get_row_at(row)]
        elif isinstance(widget, OptionList):
            for index in range(widget.option_count):
                parts.append(str(widget.get_option_at_index(index).prompt))
    return "\n".join(parts)


def _card_item(screen: BoardScreen, card_id: str) -> BoardItem:
    return next(
        item for items in screen.grid for item in items if item.card and item.card.id == card_id
    )


async def _screen(pilot, kind: type[Screen], ready=lambda screen: True) -> Screen:
    return await pause_until(
        pilot,
        lambda: pilot.app.screen,
        lambda screen: isinstance(screen, kind) and ready(screen),
    )


async def _walk_every_view(pilot, root: Path, *, click: bool) -> dict[str, str]:
    """Open every screen of the viewer in turn and return each one's text, by a name
    for the view. With `click`, the project, two cards and a closed thread are chosen
    with the mouse; a click on a closed thread's row joins nothing."""
    app = pilot.app
    views: dict[str, str] = {}

    await pause_until(pilot, lambda: app.query_one(DataTable).row_count, lambda n: n == 1)
    views["project list"] = screen_text(app.screen)

    if click:
        await pilot.click(DataTable, offset=(2, 1))  # the row under the cursor opens
    else:
        await pilot.press("enter")
    board = await _screen(pilot, BoardScreen, lambda s: s.query_one("#stacks").display)
    await focus_lands(pilot, "sys-a1")
    views["board, a decision"] = screen_text(board)

    for card_id in ("sys-p2", "sys-d1"):
        if click:
            await pilot.click(_card_item(board, card_id).query_one("#card-title"))
        else:
            _card_item(board, card_id).focus()
        await focus_lands(pilot, card_id)
        views[f"board, {card_id}"] = screen_text(board)
    _card_item(board, "sys-c1").focus()  # the last stacks: backlog, cancelled
    await focus_lands(pilot, "sys-c1")
    views["board, far stacks"] = screen_text(board)

    _card_item(board, "sys-a1").focus()
    await focus_lands(pilot, "sys-a1")
    await pilot.press("a")
    answer = await _screen(pilot, AnswerModal)
    views["decision"] = screen_text(answer)
    await pilot.press("enter")  # the first option: the modal asks where the card goes
    await pause_until(pilot, lambda: answer.query_one("#landing", OptionList).display, bool)
    views["decision, where the card goes"] = screen_text(answer)
    await pilot.press("escape")
    await _screen(pilot, BoardScreen)
    await focus_lands(pilot, "sys-a1")
    await pilot.press("x")
    views["cancel"] = screen_text(await _screen(pilot, CancelModal))
    await pilot.press("escape")
    await _screen(pilot, BoardScreen)

    await pilot.press("i")
    page = await _screen(
        pilot,
        ProjectScreen,
        lambda s: PR_URL in s.query_one("#project-details").document.source,
    )
    views["project page"] = screen_text(page)
    await pilot.press("escape")
    await _screen(pilot, BoardScreen)

    await pilot.press("p")
    threads = await _screen(
        pilot, ThreadsScreen, lambda s: s.query_one("#threads", DataTable).row_count == 2
    )
    if click:
        await pilot.click("#closed", offset=(2, 1))
        await pause_until(pilot, lambda: threads.focused, lambda f: f and f.id == "closed")
    views["planning threads"] = screen_text(threads)
    await pilot.press("r")  # on the open table, whichever thread the cursor is on
    if click:  # `r` on the closed thread says so and opens no prompt
        threads.query_one("#threads", DataTable).focus()
        await pilot.press("r")
    views["reply"] = screen_text(await _screen(pilot, ReplyModal))
    await pilot.press("escape")
    await _screen(pilot, ThreadsScreen)
    await pilot.press("m")
    views["request"] = screen_text(await _screen(pilot, PlanModal))
    await pilot.press("escape")
    await _screen(pilot, ThreadsScreen)
    await pilot.press("escape")
    board = await _screen(pilot, BoardScreen)

    bad = root / "projects" / "system" / "cards" / "sys-b1.json"
    good = bad.read_text()
    bad.write_text("{ not json")
    await pause_until(pilot, lambda: board.query_one("ReadError").display, bool)
    views["board, a file that cannot be read"] = screen_text(board)
    bad.write_text(good)
    await pause_until(pilot, lambda: board.query_one("ReadError").display, lambda d: not d)
    return views


# the view, and the icons it shows, from the icon set
EXPECTED = {
    "project list": lambda i: [i.wake, i.waiting],
    "board, a decision": lambda i: [
        *i.stack.values(),
        *i.phase.values(),
        i.waiting,
        i.card,
        i.pr,
        i.wake,
        i.thread,
        i.thread_status[ThreadStatus.WAITING_HUMAN],
        i.thread_status[ThreadStatus.WAITING_PLANNER],
    ],
    "board, sys-p2": lambda i: [i.card, i.phase[Phase.TESTING], i.pr, i.progress],
    "board, sys-d1": lambda i: [
        i.passed,
        i.stack[Stack.IN_PROGRESS],
        i.stack[Stack.DONE],
        i.stack[Stack.BACKLOG],
    ],
    "board, far stacks": lambda i: [i.stack[Stack.BACKLOG], i.stack[Stack.CANCELLED]],
    "decision": lambda i: [i.waiting],
    "decision, where the card goes": lambda i: [i.stack[Stack.TODO], i.stack[Stack.DONE]],
    "cancel": lambda i: [i.stack[Stack.CANCELLED]],
    "project page": lambda i: [*i.stack.values(), i.card, i.pr, i.wake, i.goal, i.settings],
    "planning threads": lambda i: [
        i.thread,
        i.thread_status[ThreadStatus.WAITING_HUMAN],
        i.thread_status[ThreadStatus.WAITING_PLANNER],
        i.thread_status[ThreadStatus.CLOSED],
    ],
    "reply": lambda i: [i.thread],
    "request": lambda i: [i.thread],
    "board, a file that cannot be read": lambda i: [i.failed],
}

SIZE = (160, 50)


async def test_with_the_setting_off_no_view_holds_a_nerd_font_glyph(board):
    app = AutonomouseApp()  # `viewer.nerd_font` off, its default
    async with app.run_test(size=SIZE) as pilot:
        views = await _walk_every_view(pilot, board, click=False)
    assert set(views) == set(EXPECTED)
    for view, text in views.items():
        assert not private_use(text), f"{view} shows {private_use(text)}"
        missing = [icon for icon in EXPECTED[view](PLAIN) if icon not in text]
        assert not missing, f"{view} lacks its plain icons {missing}"


async def test_with_the_setting_on_every_view_shows_its_nerd_font_icons(board):
    app = AutonomouseApp(nerd_font=True)
    async with app.run_test(size=SIZE) as pilot:
        views = await _walk_every_view(pilot, board, click=True)
    assert set(views) == set(EXPECTED)
    for view, text in views.items():
        missing = [icon for icon in EXPECTED[view](NERD) if icon not in text]
        assert not missing, f"{view} lacks its Nerd Font icons {missing}"


def test_the_project_page_marks_a_list_it_could_not_read_as_failed(board):
    details = project_details(board, "system", read_prs=lambda _repo: [])
    failed = ProjectDetails(
        **{**details.__dict__, "pulls": None, "pulls_error": "HTTP 401: Bad credentials"}
    )
    assert f"{NERD.failed} **Could not read them:**" in format_project(failed, icons=NERD)
    plain = format_project(failed)
    assert f"{PLAIN.failed} **Could not read them:**" in plain
    assert not private_use(plain)
