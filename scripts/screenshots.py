"""Save SVG screenshots of the board screens for the human to judge how they look.

    uv run python scripts/screenshots.py OUT_DIR [--board DIR] [--project SLUG]
    uv run python scripts/screenshots.py OUT_DIR --fixture status-strip [--crashed-wake]
    uv run python scripts/screenshots.py OUT_DIR --fixture every-view [--nerd-font]
        [--crashed-wake] [--light] [--paused]

With no `--fixture`, first copies the board folder (default: the live board,
`board_root()`) into a temporary directory, so the screens show a still snapshot and
nothing touches the live board. Then it drives the app headless and saves one SVG per
view into OUT_DIR:

  projects.svg             the project list
  board.svg                the project's board, focus on the first card
  board-in-progress.svg    focus on the in-progress stack, its detail panel below
  board-far-stacks.svg     scrolled right to backlog and cancelled
  board-light.svg          the board in the light theme
  board-narrow.svg         the board on an 80x30 terminal

`--fixture status-strip` (sys-c10) ignores `--board`/the live board and instead
builds one card in every phase (writing, testing, ci, review) plus one in every other
stack. A board column is always a quarter of the screen (`autonomouse.tui.board`), so
only four of the six stacks are ever on screen at once regardless of width; this saves
one screenshot on the first four stacks (decision_needed, todo, in_progress, done —
every phase card lives here) and one scrolled to the last two (backlog, cancelled),
per theme: `status-strip-<theme>.svg` and `status-strip-<theme>-far.svg` — for judging
the status strip (an icon, a size badge, an iteration count, a model-tier initial and
elapsed time) against real cards in both themes.

Both fixtures hold the project's claim by a wake whose process is this script's own, so
the in_progress cards show their phases. `--crashed-wake` makes that wake's process one
that has exited, so every in_progress card shows `stopped` in place of its phase
(sys-w108).

`--fixture every-view` (sys-w104) builds a board that holds every state an icon marks
(`autonomouse.tui.icons`): each stack and phase, a question open and one answered, a
wake holding the project, a pull request, a card another waits on, an in-progress
card's notes, the project's brief, a second project with nothing on it, and planning
threads waiting on you, on the planner and closed. It saves one SVG per screen of the
viewer, `NN-<view>.svg`, numbered in the order the screens open: the project list, the
board (the decision card, an in-progress card's details, a done card's details, the
last stacks), the decision and where the card goes, the cancel prompt, the project page,
its end, the planning threads, the reply and request prompts, and a board file that
cannot be read; then, on a terminal tall enough to read a card's details and the
project page at length (sys-w105), the details of an in-progress card, a done card and
a decision card with the rest of it shown, and the project page. The project page's
open pull requests come from a stand-in, not gh, and the viewer's error log is a file
in the temporary folder, not this machine's own.
Each of these screenshots waits until its view has drawn: until the screen shows words
of that view (a card's details, a prompt's question, the footer's keys) and has stayed
the same for a second, with the phase spinners left out of the comparison. A card's
details can take seconds to draw on a busy machine, so a fixed pause could save an empty
pane (sys-w105). If a view has not drawn after half a minute, the script stops and
prints what the screen showed.
`--nerd-font` starts the viewer as `viewer.nerd_font = true` does; without it the
viewer shows its plain icons, and the script also runs on code from before the setting
existed. An SVG names its font but does not carry it: open it where a Nerd Font such as
JetBrainsMono Nerd Font is installed to see the Nerd Font glyphs.

`--light` (sys-w119) starts the every-view viewer in the light theme, as a press of `t`
does, so every view is shot in it; without it every view is dark. `--paused` puts the
PAUSE file in the fixture's own config folder (`AUTONOMOUSE_CONFIG` is a temporary
folder; the real `~/.config/autonomouse` is never read or written) and takes the wake
off the project, since the board says `paused` only while no wake holds the project;
with `--crashed-wake` the crashed wake keeps its claim. The four flags `--nerd-font`,
`--crashed-wake`, `--light` and `--paused` combine in any set; `--light` and `--paused`
are refused without `--fixture every-view`, so a picture is never taken in the wrong
state.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from textual.widgets import DataTable

WIDE = (160, 48)
NARROW = (80, 30)
STATUS_STRIP_SIZE = (150, 60)  # tall/wide enough for six columns and every phase card
EVERY_VIEW_SIZE = (140, 44)
EVERY_VIEW_TALL = (140, 100)
DRAWN_TIMEOUT = 30.0  # seconds a view may take to draw before the script stops
STILL_READS = 3  # the same screen text on this many reads, 0.5 s apart
SPINNER = re.compile("[⠀-⣿]")  # a phase mark's braille spinner frame
EVERY_VIEW_RUN = "20260930-080000-abcd"
EVERY_VIEW_PR = "https://github.com/test/system/pull/9"
EVERY_VIEW_BRIEF = """# System — project brief

## Why this project exists

A demo project for the screenshots.

## Goal

"The board shows every state an icon marks."

## Intent

The human finds what needs them first on every view.

## Who uses it and where

One developer, over ssh.

## How it works

A wake works the cards every four hours.

## How we work

One pull request per card.

## Out of scope

Everything else.
"""


def _screen_text(app) -> str:
    """The screen as plain text: the same render `App.export_screenshot` saves."""
    console = Console(
        width=app.size.width,
        height=app.size.height,
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        record=True,
        legacy_windows=False,
        safe_box=False,
    )
    console.print(
        app.screen._compositor.render_update(full=True, screen_stack=app._background_screens)
    )
    return console.export_text()


async def _drawn(
    pilot, view: str, words: tuple[str, ...], until: Callable[[], bool] | None
) -> None:
    """Wait until the screen shows every one of `words`, `until()` is true, and the
    screen text (spinners left out) is the same on `STILL_READS` reads 0.5 s apart.
    Stops the script, naming the view and what it lacks, after `DRAWN_TIMEOUT`."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DRAWN_TIMEOUT
    reads: list[str] = []
    while True:
        text = SPINNER.sub("", _screen_text(pilot.app))
        reads = [*reads[-(STILL_READS - 1) :], text]
        missing = [word for word in words if word not in text]
        waiting = until is not None and not until()
        if not missing and not waiting and len(reads) == STILL_READS and len(set(reads)) == 1:
            return
        if loop.time() > deadline:
            lack = missing or ("its data" if waiting else "a screen that stays the same")
            raise SystemExit(
                f"{view}: not drawn after {DRAWN_TIMEOUT:.0f} s; it lacks {lack}. "
                f"The screen showed:\n{text}"
            )
        await pilot.pause(0.5)


async def _open_board(pilot, slug: str) -> None:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index(slug))
    await pilot.press("enter")
    await pilot.pause()


async def _shoot(out: Path, slug: str) -> list[Path]:
    from autonomouse.tui.app import AutonomouseApp
    from autonomouse.tui.board import STACK_ORDER

    saved: list[Path] = []

    def save(app, name: str) -> None:
        path = out / name
        path.write_text(app.export_screenshot(title=f"autonomouse — {name}"))
        saved.append(path)

    app = AutonomouseApp()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        save(app, "projects.svg")
        await _open_board(pilot, slug)
        save(app, "board.svg")
        await pilot.press("right", "right")  # decision needed -> todo -> in progress
        await pilot.pause()
        save(app, "board-in-progress.svg")
        await pilot.press(*["right"] * len(STACK_ORDER))  # to the last stack
        await pilot.pause()
        save(app, "board-far-stacks.svg")

    app = AutonomouseApp()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await _open_board(pilot, slug)
        save(app, "board-light.svg")

    app = AutonomouseApp()
    async with app.run_test(size=NARROW) as pilot:
        await pilot.pause()
        await _open_board(pilot, slug)
        save(app, "board-narrow.svg")
    return saved


def _patch_card(root: Path, project: str, card_id: str, **fields) -> None:
    path = root / "projects" / project / "cards" / f"{card_id}.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


def _gone_pid() -> int:
    """The pid of a process that has exited: a crashed wake's."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _hold_claim(root: Path, project: str, *, pid: int) -> None:
    """Run `EVERY_VIEW_RUN`, with `pid` and no recorded end, holds `project`'s claim:
    a wake alive while `pid` runs, crashed once it has exited."""
    from autonomouse.board.runs import Run, RunStatus, save_run

    save_run(
        root,
        Run(
            id=EVERY_VIEW_RUN,
            project=project,
            session_id=None,
            pid=pid,
            model="fable",
            started_at="2026-09-30T08:00:00Z",
            ended_at=None,
            cards=[],
            jev=[],
            notify_failures=[],
            result=None,
            end_reason=None,
            status=RunStatus.ALIVE,
            summary="the wake that holds the project",
        ),
    )
    path = root / "projects" / project / "project.json"
    data = json.loads(path.read_text())
    data["claim"] = {
        "run_id": EVERY_VIEW_RUN,
        "session_id": None,
        "pid": pid,
        "model": "fable",
        "claimed_at": "2026-09-30T08:00:00Z",
    }
    path.write_text(json.dumps(data))


def _build_status_strip_fixture(
    root: Path, project: str, *, crashed_wake: bool = False, hold_claim: bool = True
) -> None:
    """One card per phase (writing/testing/ci/review), plus one in every other
    stack — everything `_strip_content` (`autonomouse.tui.board`) draws, so the
    screenshot shows every mark, badge and count it can produce — and a wake holding
    the project, alive (this process) or, with `crashed_wake`, crashed; with no
    `hold_claim`, no wake holds the project.
    """
    from autonomouse.board import commands as board_commands

    board_commands.project_new(
        root,
        slug=project,
        name=project.title(),
        goal="demo project for the status-strip screenshots",
        repo=f"https://github.com/test/{project}",
        repo_path=f"/home/test/{project}",
        default_branch="main",
        priority=1,
    )

    def new(card_id: str, title: str, size: str, **overrides) -> None:
        board_commands.card_new(
            root,
            card_id=card_id,
            project=project,
            title=title,
            goal="g",
            test="t",
            size=size,
        )
        if overrides:
            _patch_card(root, project, card_id, **overrides)

    new("sys-w1", "waiting on sys-z9", "s", stack="todo", depends_on=["sys-z9"])
    new(
        "sys-a1",
        "needs a human's call",
        "m",
        stack="decision_needed",
        decisions=[
            {
                "id": "d1",
                "question": "which layout?",
                "options": ["a", "b"],
                "recommendation": "a",
                "asked_at": "2026-09-22T07:00:00Z",
                "answer": None,
                "answered_at": None,
            }
        ],
    )
    phases = [
        ("sys-p1", "writing the change", "writing", "sonnet", 0),
        ("sys-p2", "testing the change", "testing", "opus", 0),
        ("sys-p3", "bringing the branch up to date", "ci", "haiku", 0),
        ("sys-p4", "reading review comments", "review", "sonnet", 3),
    ]
    for card_id, title, phase, model, iteration in phases:
        new(
            card_id,
            title,
            "s" if iteration == 0 else "m",
            stack="in_progress",
            phase=phase,
            iteration=iteration,
            assigned={
                "model": model,
                "effort": "high",
                "agent_type": "worker-high",
                "session_id": "s1",
                "pid": 1,
                "picked_up_at": "2026-09-22T10:00:00Z",
            },
        )
    new("sys-d1", "shipped it", "xs", stack="done", pr="https://github.com/test/system/pull/9")
    new("sys-b1", "someday", "s", stack="backlog")
    new("sys-c1", "not needed after all", "s", stack="cancelled")
    if hold_claim:
        _hold_claim(root, project, pid=_gone_pid() if crashed_wake else os.getpid())


async def _shoot_status_strip(out: Path, project: str) -> list[Path]:
    from autonomouse.tui.app import AutonomouseApp
    from autonomouse.tui.board import STACK_ORDER

    saved: list[Path] = []

    def save(app, name: str) -> None:
        path = out / name
        path.write_text(app.export_screenshot(title=f"autonomouse — {name}"))
        saved.append(path)

    async def shoot(theme_key: str) -> None:
        app = AutonomouseApp()
        async with app.run_test(size=STATUS_STRIP_SIZE) as pilot:
            await pilot.pause()
            if theme_key == "light":
                await pilot.press("t")
            await _open_board(pilot, project)
            save(app, f"status-strip-{theme_key}.svg")
            await pilot.press(*["right"] * len(STACK_ORDER))  # scroll to backlog/cancelled
            await pilot.pause()
            save(app, f"status-strip-{theme_key}-far.svg")

    await shoot("dark")
    await shoot("light")
    return saved


def _build_every_view_fixture(
    root: Path, project: str, *, crashed_wake: bool = False, paused: bool = False
) -> None:
    """The status-strip board, with its wake holding the project, and on it: a done
    card with an answered question and a history row, an in-progress card's notes, the
    project's brief, a second project with nothing on it, and three planning threads,
    the one waiting on you opened last.

    A `paused` project has no wake working it: the board says `paused` only while no
    wake holds the project, so the wake's claim is left out, unless `crashed_wake`
    keeps the crashed wake's."""
    from unittest.mock import patch

    from autonomouse.board import commands as board_commands
    from autonomouse.board import plans as board_plans
    from autonomouse.board.plans import (
        thread_close,
        thread_open,
        thread_reply,
        thread_say,
    )

    _build_status_strip_fixture(
        root, project, crashed_wake=crashed_wake, hold_claim=crashed_wake or not paused
    )
    _patch_card(
        root,
        project,
        "sys-d1",
        pr=EVERY_VIEW_PR,
        decisions=[
            {
                "id": "d1",
                "question": "ship behind a flag?",
                "options": ["yes", "no", "other: tell me"],
                "recommendation": "yes",
                "asked_at": "2026-09-30T07:00:00Z",
                "answer": "yes",
                "answered_at": "2026-09-30T07:30:00Z",
            }
        ],
        history=[
            {
                "at": "2026-09-30T06:00:00Z",
                "run_id": "manual",
                "from": None,
                "to": "todo",
                "note": "created",
            },
            {
                "at": "2026-09-30T07:00:00Z",
                "run_id": EVERY_VIEW_RUN,
                "from": "in_progress",
                "to": "done",
                "note": "finished",
            },
        ],
    )
    _patch_card(
        root,
        project,
        "sys-p2",
        branch="feat/test-the-change",
        handoff={
            "last_step": "Wrote the change and its test; the test fails on main.",
            "next_step": "Run the card's test and the checks CI names.",
            "updated_at": "2026-09-30T08:10:00Z",
            "run_id": EVERY_VIEW_RUN,
        },
    )
    (root / "projects" / project / "brief.md").write_text(EVERY_VIEW_BRIEF)
    board_commands.project_new(
        root,
        slug="integrations",
        name="Integrations",
        goal="demo project with nothing on it",
        repo="https://github.com/test/integrations",
        repo_path="/home/test/integrations",
        default_branch="main",
        priority=2,
    )
    # thread ids in the order they open, so the board lists them the same on every run.
    # The real `new_thread_id` comes back after the block: a test that builds this
    # fixture in its own process otherwise leaves the spent ids to every test after it
    # on that process, whose `thread_open` then raises StopIteration (sys-w120).
    fixed_ids = iter(f"plan-20260930-070000-000{n}" for n in range(1, 4))
    with patch.object(board_plans, "new_thread_id", fixed_ids.__next__):
        closed = thread_open(root, project, "add a sepia mode")
        thread_close(root, closed.id, "done: sys-d1")
        waiting_planner = thread_open(root, project, "add a light mode")
        thread_say(root, waiting_planner.id, "Which screens should it cover?")
        thread_reply(root, waiting_planner.id, "all of them")
        waiting_human = thread_open(root, project, "add a dark mode")
        thread_say(root, waiting_human.id, "Which screens should it cover?")


async def _shoot_every_view(out: Path, project: str, *, nerd_font: bool, light: bool) -> list[Path]:
    import autonomouse.tui.project as project_page
    from autonomouse.tui.app import AutonomouseApp
    from autonomouse.tui.board import BoardScreen

    project_page.gh_open_prs = lambda _repo: [{"url": EVERY_VIEW_PR, "title": "shipped it"}]
    saved: list[Path] = []

    async def settle(pilot) -> None:
        await pilot.pause(0.6)  # past one live-refresh poll

    async def save(
        pilot, view: str, *words: str, until: Callable[[], bool] | None = None
    ) -> None:
        """Save the screen once it shows `words` and has drawn (`_drawn`). The words
        are ones every version of the view shows, before and after sys-w105."""
        await _drawn(pilot, view, words, until)
        path = out / f"{len(saved) + 1:02d}-{view}.svg"
        path.write_text(pilot.app.export_screenshot(title=f"autonomouse — {view}"))
        saved.append(path)

    def focus(app, card_id: str) -> None:
        screen = app.screen
        assert isinstance(screen, BoardScreen)
        next(i for items in screen.grid for i in items if i.card and i.card.id == card_id).focus()

    def pulls_read() -> bool:
        """The project page holds the stand-in list of pull requests, read on a thread."""
        return getattr(app.screen, "_pulls_details", None) is not None

    # a card's details pane: its first line (id and title, the stack's pill, the size
    # badge, the times), and the board's footer. The in-progress card's wait words leave
    # its pill out: the pill reads STOPPED on a paused project or one with a dead wake.
    in_progress = ("sys-p2  testing the change", "updated", "Projects")
    done = ("sys-d1  shipped it", "DONE", "updated", "Projects")
    decision = ("sys-a1  needs a human's call", "DECISION NEEDED", "updated", "Projects")

    app = AutonomouseApp(nerd_font=True) if nerd_font else AutonomouseApp()
    async with app.run_test(size=EVERY_VIEW_SIZE) as pilot:
        if light:
            await settle(pilot)
            await pilot.press("t")  # the viewer starts dark; every later screen follows
            await settle(pilot)
        await save(pilot, "project-list", "Integrations", "Open board")
        await _open_board(pilot, project)
        await save(pilot, "board", *decision)
        focus(app, "sys-p2")
        await save(pilot, "board-in-progress-card", *in_progress)
        focus(app, "sys-d1")
        await save(pilot, "board-done-card", *done)
        focus(app, "sys-c1")
        await save(
            pilot,
            "board-last-stacks",
            "sys-c1  not needed after all",
            "CANCELLED",
            "someday",
            "Projects",
        )
        focus(app, "sys-a1")
        await _drawn(pilot, "the board, back on sys-a1", decision, None)
        await pilot.press("a")
        await save(
            pilot, "decision", "sys-a1 needs your decision", "which layout?", "enter: choose"
        )
        await pilot.press("enter")
        await save(
            pilot, "decision-where-the-card-goes", "Where does sys-a1 go now?", "enter: confirm"
        )
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("x")
        await save(pilot, "cancel", "Cancel sys-a1 — reason:", "enter: cancel")
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("i")
        await save(pilot, "project-page", "System", "Back", until=pulls_read)
        await pilot.press("end")  # the end of the page
        await save(pilot, "project-page-end", "Back", until=pulls_read)
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("p")
        await save(
            pilot,
            "planning-threads",
            "The list of planning threads on system",
            "add a sepia mode",
            "Which screens should it cover?",
            "Join live",
        )
        await pilot.press("r")
        await save(
            pilot,
            "reply",
            "Reply to the planner on: add a dark mode",
            "enter: send to the planner",
        )
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("m")
        await save(
            pilot,
            "request",
            "Plan for system: what should be built or changed?",
            "enter: send to the planner",
        )
        await pilot.press("escape")
        await settle(pilot)
        await pilot.press("escape")
        await _drawn(pilot, "the board, back from the planning threads", decision, None)
        bad = Path(os.environ["AUTONOMOUSE_HOME"]) / "projects" / project / "cards" / "sys-b1.json"
        good = bad.read_text()
        bad.write_text("{ not json")
        await save(
            pilot,
            "board-file-cannot-be-read",
            "Cannot read the board",
            "sys-b1.json: not valid JSON",
            *decision,
        )
        bad.write_text(good)

    app = AutonomouseApp(nerd_font=True) if nerd_font else AutonomouseApp()
    async with app.run_test(size=EVERY_VIEW_TALL) as pilot:
        if light:
            await settle(pilot)
            await pilot.press("t")  # this second viewer starts dark too
            await settle(pilot)
        await _open_board(pilot, project)
        await _drawn(pilot, "the tall board", decision, None)
        focus(app, "sys-p2")
        await save(
            pilot, "card-details-in-progress", *in_progress, "Model: opus", "Reasoning: high", "Goal"
        )
        focus(app, "sys-d1")
        await save(pilot, "card-details-done", *done, "Goal", "Test")
        focus(app, "sys-a1")
        await _drawn(pilot, "the tall board, back on sys-a1", decision, None)
        await pilot.press("d")  # the rest of the card, under its decision
        await save(
            pilot, "card-details-decision", *decision, "which layout?", "3. other: tell me"
        )
        await pilot.press("i")
        await save(
            pilot,
            "project-page-tall",
            "Open pull requests (1)",
            "Why this project exists",
            "Slug: system",
            "Back",
            until=pulls_read,
        )
    return saved


def main() -> None:
    from autonomouse.board.paths import board_root

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--board", type=Path, default=None)
    parser.add_argument("--project", default="system")
    parser.add_argument("--fixture", choices=["status-strip", "every-view"], default=None)
    parser.add_argument("--nerd-font", action="store_true")
    parser.add_argument("--crashed-wake", action="store_true")
    parser.add_argument("--light", action="store_true")
    parser.add_argument("--paused", action="store_true")
    args = parser.parse_args()
    if (args.light or args.paused) and args.fixture != "every-view":
        parser.error("--light and --paused need --fixture every-view")

    args.out.mkdir(parents=True, exist_ok=True)

    from autonomouse.tui.app import AutonomouseApp

    AutonomouseApp.SPLASH = False  # a board shows at once, not after the splash animation

    if args.fixture == "every-view":
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "board"
            _build_every_view_fixture(
                fixture, args.project, crashed_wake=args.crashed_wake, paused=args.paused
            )
            os.environ["AUTONOMOUSE_HOME"] = str(fixture)
            os.environ["AUTONOMOUSE_CONFIG"] = str(Path(tmp) / "config")
            # the fixture's unreadable card file is logged; keep it out of the real log
            os.environ["AUTONOMOUSE_ERRORS_LOG"] = str(Path(tmp) / "errors.log")
            if args.paused:
                from autonomouse.config import pause_path

                pause_path().parent.mkdir(parents=True, exist_ok=True)
                pause_path().touch()
            shots = _shoot_every_view(
                args.out, args.project, nerd_font=args.nerd_font, light=args.light
            )
            for path in asyncio.run(shots):
                print(path)
        return

    if args.fixture == "status-strip":
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "board"
            _build_status_strip_fixture(fixture, args.project, crashed_wake=args.crashed_wake)
            os.environ["AUTONOMOUSE_HOME"] = str(fixture)
            for path in asyncio.run(_shoot_status_strip(args.out, args.project)):
                print(path)
        return

    source = args.board or board_root()
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "board"
        shutil.copytree(source, snapshot, ignore=shutil.ignore_patterns(".git"))
        os.environ["AUTONOMOUSE_HOME"] = str(snapshot)
        for path in asyncio.run(_shoot(args.out, args.project)):
            print(path)


if __name__ == "__main__":
    main()
