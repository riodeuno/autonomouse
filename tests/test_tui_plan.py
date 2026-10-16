"""The board screen's `p` key: the project's planning threads, the planner's latest
message on the selected one, and the keys that answer it, join a planner live, start one
live, or leave a request for the next wake.

The live keys run `autonomouse plan join` / `plan new` from PATH. Here a fake
`autonomouse` first on PATH records its argv and exits; the one for `plan new` also
adds a thread, as the real one would. The headless test driver cannot suspend, so
`App.suspend` here only hands back the process's stdout; the real screen's suspend is exercised on a terminal.
"""

import contextlib
import io
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rich.console import Console
from textual.color import Color
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Static
from textual.widgets._footer import FooterKey

from autonomouse.board import commands as board_commands
from autonomouse.board.plans import (
    Author,
    Thread,
    ThreadStatus,
    thread_close,
    thread_join,
    thread_list,
    thread_open,
    thread_path,
    thread_reply,
    thread_say,
)
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import BoardScreen
from autonomouse.tui.footer import AppFooter, footer_label
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.logo import AppHeader
from autonomouse.tui.look import (
    FRAME,
    THREAD_WORD,
    ProjectStatus,
    project_status_pill,
    thread_pill,
)
from autonomouse.tui.plan import (
    STATUS_WORDS,
    PlanModal,
    ReplyModal,
    ThreadsScreen,
    last_changed,
    project_threads,
    request_line,
    thread_markdown,
)
from autonomouse.tui.reading import ReadingView
from tui_wait import board_settled, legend, legend_shows, pause_until

FAKE = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
if [ "$1 $2" = "plan new" ]; then cp "$FAKE_NEW_THREAD" "$AUTONOMOUSE_HOME/plans/"; fi
exit 0
"""


def _at(minutes_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _set_times(root, thread: Thread, minutes_ago: int) -> None:
    # test setup only: date a thread's opening and messages in the past, so the three
    # threads have distinct "last changed" times
    path = thread_path(root, thread.id)
    data = json.loads(path.read_text())
    data["opened_at"] = _at(minutes_ago + 1)
    for message in data["messages"]:
        message["at"] = _at(minutes_ago)
    path.write_text(json.dumps(data))


@pytest.fixture
def board(tmp_path, monkeypatch):
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    for slug, priority in (("system", 1), ("other", 2)):
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
    return root


@pytest.fixture
def threads(board):
    """Three threads on `system`, newest first: waiting on you, you answered, closed;
    and one on another project that the list leaves out."""
    waiting_human = thread_open(board, "system", "add a dark mode\nwith a toggle")
    thread_say(board, waiting_human.id, "which screens?")
    waiting_planner = thread_open(board, "system", "add a light mode")
    thread_say(board, waiting_planner.id, "which screens?")
    thread_reply(board, waiting_planner.id, "all of them")
    closed = thread_open(board, "system", "add a sepia mode")
    thread_close(board, closed.id, "done: sys-z1")
    thread_open(board, "other", "other request")
    for minutes_ago, thread in enumerate((waiting_human, waiting_planner, closed)):
        _set_times(board, thread, 5 + 10 * minutes_ago)
    return waiting_human.id, waiting_planner.id, closed.id


@pytest.fixture
def fake(tmp_path, board, monkeypatch):
    """A fake `autonomouse` first on PATH: `log` is the file it writes each call's argv
    to, `new_thread_id` the thread its `plan new` adds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "autonomouse"
    script.write_text(FAKE)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "argv.log"
    # the thread `plan new` adds, written aside so the fake can copy it into plans/
    new_thread = thread_open(board, "system", "started live")
    staged = tmp_path / f"{new_thread.id}.json"
    thread_path(board, new_thread.id).rename(staged)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_NEW_THREAD", str(staged))
    return SimpleNamespace(log=log, script=script, new_thread_id=new_thread.id)


def _calls(log) -> list[str]:
    return log.read_text().splitlines() if log.exists() else []


async def _open_threads(pilot) -> ThreadsScreen:
    table = pilot.app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    await pilot.press("enter")
    # the board's legend, once drawn, shows p (on a narrow terminal other keys may be
    # cut off it)
    await pause_until(
        pilot,
        lambda: (type(pilot.app.screen), legend(pilot.app.screen)[0]),
        lambda state: state[0] is BoardScreen and "p" in state[1],
    )
    await pilot.press("p")
    # the threads, once their legend is drawn: the screen has composed and laid out
    return await pause_until(
        pilot,
        lambda: pilot.app.screen,
        lambda screen: isinstance(screen, ThreadsScreen) and "enter" in legend(screen)[0],
    )


def _pill(status: ThreadStatus) -> str:
    """A thread's `whose turn` cell as text: its pill, in the plain icons."""
    return str(thread_pill(status, icons=PLAIN))


def _threads_by_id(board) -> dict[str, Thread]:
    return {thread.id: thread for thread in thread_list(board)}


def _rows(screen: ThreadsScreen, section: str = "#threads") -> list[list[str]]:
    """The rows of the open section (`#threads`) or the closed one (`#closed`)."""
    table = screen.query_one(section, DataTable)
    return [[str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)]


def _screen_text(app: AutonomouseApp) -> str:
    """The screen as the terminal shows it, as plain text (`App.export_screenshot`'s
    render, exported as text in place of SVG)."""
    width, height = app.size
    console = Console(
        width=width, height=height, file=io.StringIO(), record=True, legacy_windows=False
    )
    console.print(
        app.screen._compositor.render_update(
            full=True, screen_stack=app._background_screens
        )
    )
    return console.export_text()


def _app(monkeypatch, *, nerd_font: bool = False) -> AutonomouseApp:
    app = AutonomouseApp(nerd_font=nerd_font)
    # the headless driver cannot suspend; like the real suspend, this one gives the
    # command the process's own stdout
    monkeypatch.setattr(
        app, "suspend", lambda: contextlib.redirect_stdout(sys.__stdout__)
    )
    return app


def _notes(app, monkeypatch) -> list[str]:
    """Every notification the app shows from here on, in order; each still shows."""
    notes: list[str] = []
    show = app.notify

    def record(message, **kwargs):
        notes.append(message)
        return show(message, **kwargs)

    monkeypatch.setattr(app, "notify", record)
    return notes


async def _pane(pilot, screen: ThreadsScreen, ending: str | None = None) -> ReadingView:
    """The pane under the list, once it has drawn the thread it shows and scrolled.

    A cursor move reaches the pane one message after the key, so a test that moved
    the cursor passes the end of the text it expects as `ending`: the wait below
    starts once the pane's worker for that text runs."""
    pane = screen.query_one("#message-pane", ReadingView)
    assert pane.display
    if ending is not None:
        await pause_until(pilot, lambda: pane.source, lambda source: source.endswith(ending))
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
    return pane


def _headings(pane: ReadingView) -> list[str]:
    return [str(h.content) for h in pane.document.query("MarkdownH2")]


def _when(at: str) -> str:
    return at[:16].replace("T", " ")


# a planner question longer than the screen, with a `[` that must print as itself
LONG_QUESTION = "\n".join(
    [
        "Which screens should the dark mode cover? [pick any]",
        "",
        *(f"{n}. screen {n}: the board, its header and the card panel" for n in range(1, 41)),
        "",
        "Tell me also whether the toggle lives in the header or in the settings.",
    ]
)
CLOSED_REFUSAL_WORDS = "This thread is closed; there is no planner to join."


async def test_p_lists_the_projects_threads_newest_first_each_with_its_pill(
    board, threads, monkeypatch
):
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        open_rows, closed_rows = _rows(screen), _rows(screen, "#closed")
        legend = {key.key for key in screen.query_one(Footer).query(FooterKey)}
    assert open_rows == [
        [_pill(ThreadStatus.WAITING_HUMAN), "5m ago", "add a dark mode"],
        [_pill(ThreadStatus.WAITING_PLANNER), "15m ago", "add a light mode"],
    ]
    assert closed_rows == [[_pill(ThreadStatus.CLOSED), "25m ago", "add a sepia mode"]]
    assert _pill(ThreadStatus.WAITING_HUMAN).strip() == (
        f"{PLAIN.thread_status[ThreadStatus.WAITING_HUMAN]} WAITING ON YOU"
    )
    assert {"enter", "n", "m", "escape"} <= legend


async def test_at_90_columns_the_open_thread_waiting_on_you_leads_and_closed_sit_apart(
    board, monkeypatch
):
    """One open thread waiting on the human and two closed ones, on a 90-column
    terminal: the open one comes first with its WAITING ON YOU pill, the closed ones
    only in their own list below it, each list with its heading on its frame, and
    the title and hint say this is the list of planning threads and what enter, n
    and m do."""
    first_closed = thread_open(board, "system", "add a sepia mode")
    thread_close(board, first_closed.id, "done: sys-z1")
    waiting = thread_open(board, "system", "add a dark mode\nwith a toggle")
    thread_say(board, waiting.id, "which screens?")
    second_closed = thread_open(board, "system", "rename the wake log")
    thread_close(board, second_closed.id, "done: sys-z2")
    app = _app(monkeypatch)
    # 34 rows: tall enough for the closed list to show both its rows
    async with app.run_test(size=(90, 34)) as pilot:
        await _open_threads(pilot)
        # every key of the legend in view: the screen is laid out to its last line
        await legend_shows(pilot, lambda keys: {"enter", "n", "m"} <= keys)
        # the title and the hint sit in the header, under the app's title
        # each line without the frame round the header (`AppHeader`, sys-w111) or
        # round a list
        lines = [line.strip().strip("┃").strip() for line in _screen_text(app).splitlines()]

    assert f"{PLAIN.thread} The list of planning threads on system" in lines
    # each key's hint starts on the first line that begins with the key, and runs on
    # until the next key's, as a hint may wrap onto a second line. The footer's legend
    # comes last.
    keys = ["enter", "r", "n", "m"]
    starts = [next(i for i, line in enumerate(lines) if line.split()[:1] == [key]) for key in keys]
    open_at = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(f"┏━ {PLAIN.thread} Open threads ━")
    )
    starts.append(open_at)
    hint = {key: " ".join(lines[a:b]) for key, a, b in zip(keys, starts, starts[1:])}
    assert "join" in hint["enter"] and "live" in hint["enter"]
    assert "new live chat" in hint["n"]
    assert "request" in hint["m"] and "next wake" in hint["m"]

    closed_at = next(
        i
        for i, line in enumerate(lines)
        if line.startswith(f"┏━ {PLAIN.thread_status[ThreadStatus.CLOSED]} Closed threads ━")
    )
    # the closed section ends where the pane below it starts; the pane shows the chosen
    # thread, the open one, once its worker has drawn it
    pane_at = next(i for i, line in enumerate(lines) if line.startswith("┏━ the chosen thread"))
    assert open_at < closed_at < pane_at
    open_section, closed_section = lines[open_at:closed_at], lines[closed_at:pane_at]
    [waiting_row] = [line for line in open_section if "add a dark mode" in line]
    assert waiting_row.startswith(_pill(ThreadStatus.WAITING_HUMAN).strip())
    assert not any("sepia" in line or "wake log" in line for line in open_section)
    assert sum("sepia" in line for line in closed_section) == 1
    assert sum("rename the wake log" in line for line in closed_section) == 1
    assert not any("dark mode" in line for line in closed_section)


async def test_enter_joins_a_thread_that_is_not_closed_and_nothing_on_a_closed_one(
    board, threads, fake, monkeypatch
):
    waiting_human, _, _ = threads
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await pilot.press("enter")  # the cursor starts on the one waiting on you
        await pilot.pause()
        assert _calls(fake.log) == [f"plan join {waiting_human}"]
        assert app.screen is screen

        await pilot.press("tab")  # to the closed section
        await pilot.pause()
        assert app.focused is screen.query_one("#closed", DataTable)
        await pilot.press("enter")
        await pilot.pause()
        assert _calls(fake.log) == [f"plan join {waiting_human}"]
        assert app.screen is screen


async def test_n_runs_plan_new_and_the_screens_show_the_thread_it_added(
    board, threads, fake, monkeypatch
):
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        assert len(_rows(screen)) == 2
        await pilot.press("n")
        await pilot.pause()
        assert _calls(fake.log) == ["plan new system"]
        assert app.screen is screen
        # the fake's thread is the newest: the list shows it at once, on top of the
        # other open threads but below the one waiting on you
        assert [[row[0], row[2]] for row in _rows(screen)] == [
            [_pill(ThreadStatus.WAITING_HUMAN), "add a dark mode"],
            [_pill(ThreadStatus.OPEN), "started live"],
            [_pill(ThreadStatus.WAITING_PLANNER), "add a light mode"],
        ]

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, BoardScreen)
        header = str(app.screen.query_one("#project-header", Static).render())
    assert f"plan: started live · {PLAIN.thread_status[ThreadStatus.OPEN]} the planner is working\n" in header
    assert header.count("press p") == 1  # said once, on the thread waiting on you
    assert "plan join" not in header


async def test_a_failed_command_waits_for_enter_before_the_screen_comes_back(
    board, threads, fake, monkeypatch, capfd
):
    fake.script.write_text(FAKE.replace("exit 0", "exit 3"))
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await pilot.press("n")
        await pilot.pause()
        assert app.screen is screen
    assert prompts == ["Press enter to return to the board. "]
    assert "`autonomouse plan new system` exited with code 3." in capfd.readouterr().out


async def test_m_and_a_typed_request_open_a_thread_for_the_next_wake(
    board, fake, monkeypatch
):
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        assert _rows(screen) == []
        assert screen.query_one("#threads-empty").display

        await pilot.press("m")
        await pilot.pause()
        assert isinstance(app.screen, PlanModal)
        request = "add a dark mode"
        await pilot.press(*request)
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        assert [[row[0], row[2]] for row in _rows(screen)] == [
            [_pill(ThreadStatus.OPEN), request]
        ]
        # the open section keeps the focus, so enter reaches the new thread
        assert app.focused is screen.query_one("#threads", DataTable)
    assert _calls(fake.log) == []  # a request left for the next wake runs no command
    [thread] = thread_list(board)
    assert thread.project == "system"
    assert thread.status == ThreadStatus.OPEN
    assert [(m.from_, m.text) for m in thread.messages] == [(Author.HUMAN, request)]
    assert thread.session_id is None


async def test_an_empty_request_keeps_the_prompt_open_and_escape_writes_nothing(
    board, monkeypatch
):
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await pilot.press("m")
        await pilot.pause()
        await pilot.press("enter")  # empty request
        await pilot.pause()
        assert isinstance(app.screen, PlanModal)
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is screen
    assert thread_list(board) == []


async def test_the_header_names_each_open_threads_status_and_the_p_key_not_a_command(
    board,
):
    thread_open(board, "system", "add a dark mode\nwith a toggle")
    asked = thread_open(board, "system", "add a light mode")
    thread_say(board, asked.id, "which screens?")
    done = thread_open(board, "system", "add a sepia mode")
    thread_close(board, done.id)
    thread_open(board, "other", "other request")
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        table = pilot.app.query_one(DataTable)
        table.move_cursor(row=table.get_row_index("system"))
        await pilot.press("enter")
        # the board, once its first focus has landed: it has composed and drawn
        screen = await pause_until(pilot, lambda: app.screen, board_settled)
        header = str(screen.query_one("#project-header", Static).render())
    # the thread waiting on you first, with the key said once (sys-w105)
    lines = [line for line in header.splitlines() if "plan:" in line]
    assert lines == [
        f"{PLAIN.thread} plan: add a light mode · {PLAIN.thread_status[ThreadStatus.WAITING_HUMAN]} waiting on you · press p for planning",
        f"{PLAIN.thread} plan: add a dark mode · {PLAIN.thread_status[ThreadStatus.OPEN]} the planner is working",
    ]
    assert "sepia" not in header and "other request" not in header
    assert "plan join" not in header and "autonomouse" not in header


async def test_the_chosen_thread_shows_every_message_and_follows_the_cursor(
    board, threads, monkeypatch
):
    waiting_human, waiting_planner, _ = threads
    thread_say(board, waiting_human, LONG_QUESTION)
    _set_times(board, _threads_by_id(board)[waiting_human], 5)
    at = _when(_threads_by_id(board)[waiting_human].messages[0].at)
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        pane = await _pane(pilot, screen)
        # every message of the thread, oldest first, each under its own heading
        assert _headings(pane) == [f"You, {at}", f"The planner, {at}", f"The planner, {at}"]
        assert pane.source == thread_markdown(_threads_by_id(board)[waiting_human])
        # the newest message is longer than the pane: the pane opens on its heading,
        # the `[pick any]` shows as itself, and the human scrolls up for the rest
        newest = pane.document.query("MarkdownH2").last()
        # the heading's own top margin stays above it, a blank gap under the pane's top;
        # the scroll shows on screen after the next layout
        top = pane.content_region.y + newest.styles.margin.top
        await pause_until(pilot, lambda: newest.region.y, lambda y: y == top)
        assert "Which screens should the dark mode cover? [pick any]" in [
            str(p.content) for p in pane.document.query("MarkdownParagraph")
        ]
        assert 0 < pane.scroll_y < pane.max_scroll_y

        await pilot.press("down")  # planner working: its question, the human answered
        pane = await _pane(pilot, screen, "\n\nall of them\n")
        assert [h.split(",")[0] for h in _headings(pane)] == ["You", "The planner", "You"]
        await pilot.press("tab")  # closed: the request and the planner's closing message
        pane = await _pane(pilot, screen, "\n\ndone: sys-z1\n")
        assert app.focused is screen.query_one("#closed", DataTable)
        assert [h.split(",")[0] for h in _headings(pane)] == ["You", "The planner"]
        assert pane.source.endswith("\n\ndone: sys-z1\n")
        await pilot.press("shift+tab")  # back to the open table, cursor where it was
        await pilot.pause()

        # the planner answers the selected thread from another terminal: it moves to
        # the top of the list, and the cursor and the pane go with it
        thread_say(board, waiting_planner, "and the toggle?")
        await pause_until(
            pilot,
            lambda: _rows(screen)[0],
            lambda row: (row[0], row[2]) == (_pill(ThreadStatus.WAITING_HUMAN), "add a light mode"),
        )
        assert screen.query_one("#threads", DataTable).cursor_row == 0
        pane = await _pane(pilot, screen, "\n\nand the toggle?\n")
        assert [h.split(",")[0] for h in _headings(pane)][-1] == "The planner"
        assert pane.source.endswith("\n\nand the toggle?\n")

        # a change on another thread refreshes the list and keeps the place scrolled to
        await pilot.press("down")
        pane = await _pane(pilot, screen, LONG_QUESTION.splitlines()[-1] + "\n")
        pane.scroll_home(animate=False)
        await pilot.pause()
        thread_reply(board, waiting_planner, "the toggle goes in the header")
        # dark mode still waits on the human, so it leads; light mode dropped to
        # "you answered", below it; the cursor stayed on dark mode throughout, so the
        # pane still shows its thread and the scroll position survives the refresh
        await pause_until(
            pilot,
            lambda: _rows(screen)[1],
            lambda row: (row[0], row[2])
            == (_pill(ThreadStatus.WAITING_PLANNER), "add a light mode"),
        )
        pane = await _pane(pilot, screen)
        assert pane.source == thread_markdown(_threads_by_id(board)[waiting_human])
        assert pane.scroll_y == 0 < pane.max_scroll_y


async def test_r_appends_the_typed_reply_as_the_humans_message_for_the_next_plan_step(
    board, threads, fake, monkeypatch
):
    waiting_human, _, _ = threads
    app = _app(monkeypatch)
    notes = _notes(app, monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        assert "which screens?" in (await _pane(pilot, screen)).source
        assert "r" in {key.key for key in screen.query_one(Footer).query(FooterKey)}

        await pilot.press("r")  # the cursor starts on the newest: waits on you
        await pilot.pause()
        assert isinstance(app.screen, ReplyModal)
        # the question being answered shows above the field
        question = app.screen.query_one("#reply-question", ReadingView).source
        assert question.startswith("## The planner, ")
        assert question.endswith("\n\nwhich screens?")
        answer = "only the board screen"
        await pilot.press(*answer)
        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        row = _rows(screen)[0]
        assert (row[0], row[2]) == (
            _pill(ThreadStatus.WAITING_PLANNER),
            "add a dark mode",
        )
        # the pane redraws the thread with the reply in a worker; the app quits after
        await app.workers.wait_for_complete()
        assert answer in screen.query_one("#message-pane", ReadingView).source
    assert notes == ["Your reply is on the thread; the planner answers it on its next step."]
    assert _calls(fake.log) == []  # a reply runs no command
    data = json.loads(thread_path(board, waiting_human).read_text())
    assert data["status"] == "waiting_planner"
    assert [(m["from"], m["text"]) for m in data["messages"]][-2:] == [
        ("planner", "which screens?"),
        ("human", answer),
    ]
    # what `plan step` answers: the thread's human messages after the planner's last
    [unanswered] = _threads_by_id(board)[waiting_human].unanswered()
    assert (unanswered.from_, unanswered.text) == (Author.HUMAN, answer)


async def test_an_empty_reply_keeps_the_field_open_and_escape_writes_nothing(
    board, threads, monkeypatch
):
    waiting_human, _, _ = threads
    before = thread_path(board, waiting_human).read_text()
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("space", "enter")  # nothing but a space
        await pilot.pause()
        assert isinstance(app.screen, ReplyModal)
        await pilot.press(*"half an answer", "escape")
        await pilot.pause()
        assert app.screen is screen
    assert thread_path(board, waiting_human).read_text() == before


async def test_a_reply_on_a_closed_thread_writes_nothing_and_says_what_enter_says(
    board, threads, fake, monkeypatch
):
    waiting_human, _, closed = threads
    closed_before = thread_path(board, closed).read_text()
    app = _app(monkeypatch)
    notes = _notes(app, monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await pilot.press("tab")  # to the closed section
        await pilot.pause()
        assert app.focused is screen.query_one("#closed", DataTable)
        await pilot.press("enter")  # enter on the closed one
        await pilot.pause()
        await pilot.press("r")  # r on the closed one: no field opens
        await pilot.pause()
        assert app.screen is screen
        assert notes == [CLOSED_REFUSAL_WORDS, CLOSED_REFUSAL_WORDS]

        # the planner closes a thread while the human types the reply to it
        await pilot.press("shift+tab", "r")  # back to the open table; r on its cursor
        await pilot.pause()
        assert isinstance(app.screen, ReplyModal)
        thread_close(board, waiting_human, "done: sys-z2")
        waiting_human_before = thread_path(board, waiting_human).read_text()
        await pilot.press(*"too late", "enter")
        await pilot.pause()
        assert app.screen is screen
        assert notes[2:] == [CLOSED_REFUSAL_WORDS]
        assert [row[2] for row in _rows(screen)] == ["add a light mode"]
        assert _rows(screen, "#closed")[0][2] == "add a dark mode"
        # the pane redraws the refreshed thread in a worker; the app quits after
        await app.workers.wait_for_complete()
    assert _calls(fake.log) == []
    assert thread_path(board, closed).read_text() == closed_before
    assert thread_path(board, waiting_human).read_text() == waiting_human_before


def test_status_words_cover_every_status_and_last_changed_is_the_newest_time(board):
    assert set(STATUS_WORDS) == set(ThreadStatus)
    thread = thread_open(board, "system", "  first line\nsecond line")
    assert request_line(thread) == "first line"
    thread.messages[0].at = "2026-01-01T00:00:00Z"
    thread.opened_at = "2026-01-01T00:00:00Z"
    thread.joined = {"pid": 1, "at": "2026-01-02T00:00:00Z"}
    assert last_changed(thread) == "2026-01-02T00:00:00Z"
    other = thread_open(board, "other", "x")
    assert project_threads([thread, other], "system") == [thread]


# ------------------------------------------------------------ sys-w118 --
# The planning threads take the look every screen of the viewer has.


async def test_the_header_is_titled_planning_threads_with_the_projects_status_and_description(
    board, threads, monkeypatch
):
    """The header's title is `Planning threads`; under it the project's status line
    (its pill, and the one thread waiting on the human counted) and its description,
    then the list's own title and the keys' hint, as before, inside the frame."""
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        header = screen.query_one(AppHeader)
        status = header.query_one("#header-status", Static)
        await pause_until(pilot, lambda: status.display, bool)
        title = str(header.query_one("#header-title", Static).visual)
        status_text = str(status.content)
        description = str(header.query_one("#header-description", Static).content)
        side = str(header.query_one("#threads-title", Static).content)
        hint = str(header.query_one(".hint", Static).content)
    assert title == "Planning threads"
    # nothing on `system` is in todo or in progress, and no wake holds it
    assert status_text.startswith(str(project_status_pill(ProjectStatus.DONE, icons=PLAIN)))
    assert " · system | " in status_text
    assert "waiting on you 1" in status_text
    assert description == "goal for system"
    assert side == f"{PLAIN.thread} The list of planning threads on system"
    assert hint.startswith("Each row is one conversation")


@pytest.mark.parametrize("icons_on", [False, True])
async def test_each_of_the_four_turns_shows_its_pill(board, monkeypatch, icons_on):
    """A thread waiting on the human, one the planner works on, one the human is in
    live, and a closed one: each row's `whose turn` cell is that turn's pill, drawn in
    full on its own filled background in a text colour of its own, on the cursor's
    row too, with the list focused or not."""
    waiting = thread_open(board, "system", "waits on you")
    thread_say(board, waiting.id, "which screens?")
    thread_open(board, "system", "the planner works on it")
    live = thread_open(board, "system", "you are in it")
    thread_join(board, live.id, os.getpid())
    closed = thread_open(board, "system", "all done")
    thread_close(board, closed.id, "done: sys-z1")
    expected = {
        "waits on you": ThreadStatus.WAITING_HUMAN,
        "the planner works on it": ThreadStatus.OPEN,
        "you are in it": ThreadStatus.HUMAN_JOINED,
        "all done": ThreadStatus.CLOSED,
    }
    icons = NERD if icons_on else PLAIN
    app = _app(monkeypatch, nerd_font=icons_on)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        open_table = screen.query_one("#threads", DataTable)
        closed_table = screen.query_one("#closed", DataTable)
        await pause_until(pilot, lambda: open_table.row_count, lambda n: n == 3)
        by_id = _threads_by_id(board)
        cells = {
            request_line(by_id[table.coordinate_to_cell_key((row, 0)).row_key.value]): (
                table,
                row,
            )
            for table in (open_table, closed_table)
            for row in range(table.row_count)
        }
        assert set(cells) == set(expected)
        # the open list focused, its cursor on the thread waiting on you; then the
        # pane focused, so both lists show their cursor rows unfocused
        for focus in (open_table, screen.query_one("#message-pane")):
            focus.focus()
            await pause_until(pilot, lambda: app.focused, lambda f, w=focus: f is w)
            await pilot.pause()
            for request, (table, row) in cells.items():
                pill = thread_pill(expected[request], icons=icons)
                shown = table.get_row_at(row)[0]
                assert (shown.plain, shown.spans) == (pill.plain, pill.spans), request
                # the table's line for the row: its first line holds the column names
                segments = list(table.render_line(row + 1))
                word = THREAD_WORD[expected[request]].upper()
                [style] = [seg.style for seg in segments if word in seg.text]
                row_background = segments[-1].style.bgcolor
                where = (request, focus.id)
                assert style.bgcolor is not None, where
                assert style.bgcolor != row_background, where
                assert style.color is not None and style.color != style.bgcolor, where
    assert {THREAD_WORD[s].upper() for s in expected.values()} == {
        "WAITING ON YOU",
        "PLANNER WORKING",
        "LIVE",
        "CLOSED",
    }


def _border(widget) -> set[tuple[str, Color]]:
    styles = widget.styles
    return {styles.border_top, styles.border_right, styles.border_bottom, styles.border_left}


def _theme_colour(app, variable: str) -> Color:
    return Color.parse(app.theme_variables[variable.removeprefix("$")])


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
async def test_the_lists_the_pane_and_both_dialogs_have_the_frame_and_focus_takes_its_colour(
    board, threads, monkeypatch, theme
):
    """The open list, the closed list and the message pane sit in the viewer's frame
    at rest; the one that holds focus takes the focus colour, in the dark and the light
    theme. The reply and the request dialogs sit in the frame in its focus colour."""
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        app.theme = theme
        screen = await _open_threads(pilot)
        await _pane(pilot, screen)
        rest = {(FRAME.border, _theme_colour(app, FRAME.colour))}
        focused = {(FRAME.border, _theme_colour(app, FRAME.focus))}
        assert rest != focused
        panels = {
            name: screen.query_one(name)
            for name in ("#threads", "#closed", "#message-pane")
        }
        for name, panel in panels.items():
            panel.focus()
            await pause_until(pilot, lambda: app.focused, lambda f, p=panel: f is p)
            await pilot.pause()
            borders = {other: _border(widget) for other, widget in panels.items()}
            assert borders == {
                other: focused if other == name else rest for other in panels
            }, (theme, name)

        panels["#threads"].focus()
        await pilot.pause()
        for key, modal in (("r", ReplyModal), ("m", PlanModal)):
            await pilot.press(key)
            dialog = await pause_until(pilot, lambda: app.screen, lambda s, m=modal: isinstance(s, m))
            await pilot.pause()
            assert _border(dialog.query_one(Vertical)) == focused, (theme, key)
            await pilot.press("escape")
            await pause_until(pilot, lambda: app.screen, lambda s: s is screen)


async def test_the_footer_names_each_key_and_ends_with_the_slug_and_the_version(
    board, threads, monkeypatch
):
    """The footer is the viewer's framed one: each key with what it does, all in view,
    then the project's slug and the autonomouse version. esc goes back to the board."""
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await legend_shows(pilot, lambda keys: {"enter", "r", "n", "m", "escape"} <= keys)
        footer = screen.query_one(Footer)
        assert isinstance(footer, AppFooter)
        said = {key.key: key.description for key in footer.query(FooterKey)}
        about = str(footer.query_one(".footer-about", Static).content)
        await pilot.press("escape")
        await pause_until(pilot, lambda: app.screen, lambda s: isinstance(s, BoardScreen))
    assert {k: said[k] for k in ("enter", "escape", "r", "n", "m")} == {
        "enter": "Join live",
        "escape": "Board",
        "r": "Reply",
        "n": "New",
        "m": "Request",
    }
    assert about == footer_label("system")
    assert "system" in about and "autonomouse v" in about


async def test_a_click_on_a_thread_row_chooses_it(board, threads, monkeypatch):
    """A mouse click on a row of either list chooses that thread: the cursor goes to
    it and the pane shows it, as the arrow keys and tab do."""
    _, waiting_planner, closed = threads
    app = _app(monkeypatch)
    async with app.run_test(size=(160, 40)) as pilot:
        screen = await _open_threads(pilot)
        await _pane(pilot, screen)
        open_table = screen.query_one("#threads", DataTable)
        # the second row: under the frame's top edge and the column names
        await pilot.click("#threads", offset=(4, 3))
        await pause_until(pilot, lambda: open_table.cursor_row, lambda row: row == 1)
        pane = await _pane(pilot, screen, "\n\nall of them\n")
        assert pane.source == thread_markdown(_threads_by_id(board)[waiting_planner])

        await pilot.click("#closed", offset=(4, 2))
        await pause_until(pilot, lambda: app.focused, lambda f: f is screen.query_one("#closed"))
        pane = await _pane(pilot, screen, "\n\ndone: sys-z1\n")
        assert pane.source == thread_markdown(_threads_by_id(board)[closed])
