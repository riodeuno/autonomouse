"""Projects that finish: a project is marked done by hand (`project done`) or by a wake that
finds it finished; a done project is left alone, listed last and shown as DONE; a new todo
card reopens it; a wake that finds every project done turns the timers off, and a new todo
card on a done project installs them again.

The wake tests use the controller tests' board (`env`: projects `low`, priority 1, and
`high`, priority 2) and the timer tests' fake crontab (`cron`); no test reaches GitHub or
the machine's real crontab."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from autonomouse import controller, github, timer
from autonomouse.board.commands import (
    CommandError,
    card_answer,
    card_ask,
    card_move,
    card_new,
    project_done,
    project_edit,
    project_list,
    project_new,
    project_reopen,
    project_show,
)
from autonomouse.board.done import every_project_done, finished, mark_finished
from autonomouse.board.schema import BoardValidationError, Stack, project_from_dict
from autonomouse.board.store import load_card, load_project, save_card
from test_run_controller import _git, _value, env  # noqa: F401 — the wake fixture
from test_timer import (  # noqa: F401 — the fake crontab
    FAKE_CRONTAB,
    FOREIGN,
    INVOCATION,
    Cron,
    cron,
)

DONE_MESSAGE = (
    "every card is done or cancelled and every done card's work has reached the default branch"
)


def _reads(states=None, *, bases=None, branches=None, asked=None):
    """The wake's two pull request reads over a table. `states` maps a PR url to
    `MERGED` (the default), `OPEN`, `CLOSED`, or None for a read that fails. `bases` maps
    a PR url to the branch it merged into (default `main`, the project's default branch).
    `branches` maps a branch to whether its own PR merged. Every read is appended to
    `asked` as ("pr", url) or ("branch", name)."""
    states, bases, branches = states or {}, bases or {}, branches or {}

    def merged_into(repo, pr):
        if asked is not None:
            asked.append(("pr", pr))
        state = states.get(pr, "MERGED")
        if state is None:
            raise github.GitHubError(f"gh pr view {pr} failed: no network")
        return bases.get(pr, "main") if state == "MERGED" else None

    def branch_merged(repo, branch):
        if asked is not None:
            asked.append(("branch", branch))
        return branches[branch]

    return {"merged_into": merged_into, "branch_merged": branch_merged}


# ------------------------------------------------------------------- helpers --


def _project(root: Path, slug: str, priority: int = 1) -> None:
    project_new(
        root,
        slug=slug,
        name=slug,
        goal="g",
        repo="https://github.com/t/t",
        repo_path=f"/tmp/{slug}",
        default_branch="main",
        priority=priority,
    )


def _card(root: Path, card_id: str, slug: str, stack: Stack, pr: str | None = None) -> None:
    """A card straight in `stack` with `pr`: a wake's own commands reach these states one
    step at a time, which this test does not need."""
    card_new(root, card_id=card_id, project=slug, title="t", goal="g", test="t")
    path = root / "projects" / slug / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack, card.pr = stack, pr
    card.branch = f"card/{card_id}" if pr else None  # `card start` sets it for a card with a PR
    save_card(path, card)


def _pr(n: int) -> str:
    return f"https://github.com/t/t/pull/{n}"


@pytest.fixture
def board(tmp_path):
    root = tmp_path / "board"
    root.mkdir()
    return root


# ------------------------------------------------- the project file and its marks --


def test_done_at_is_written_only_while_the_project_is_done(board):
    _project(board, "solo")
    path = board / "projects" / "solo" / "project.json"
    assert "done_at" not in json.loads(path.read_text())

    marked = project_done(board, "solo")

    assert marked.done_at is not None
    assert json.loads(path.read_text())["done_at"] == marked.done_at
    assert load_project(path) == marked
    assert project_reopen(board, "solo") is True
    assert "done_at" not in json.loads(path.read_text())
    assert project_reopen(board, "solo") is False  # nothing to clear


def test_a_project_already_done_keeps_the_time_it_was_marked(board):
    _project(board, "solo")
    first = project_done(board, "solo").done_at
    time.sleep(1.1)
    assert project_done(board, "solo").done_at == first


def test_marking_an_unknown_project_is_refused(board):
    with pytest.raises(CommandError, match="no project 'nope'"):
        project_done(board, "nope")


def test_a_done_at_that_is_not_a_time_string_is_refused(board):
    _project(board, "solo")
    data = project_show(board, "solo").to_dict()
    assert project_from_dict(data, source="x").done_at is None  # a file from before the field
    with pytest.raises(BoardValidationError, match="done_at"):
        project_from_dict({**data, "done_at": 7}, source="x")


def test_a_wakes_claim_release_keeps_done_at(board):
    """A wake that goes on after `project done` ends by releasing its claim, which rewrites
    the project file: the mark must survive that."""
    from autonomouse.board.runs import project_claim, project_release, run_start

    _project(board, "solo")
    run_start(board, run_id="20260101-000000-aaaa", session_id="s", pid=os.getpid(), model="m")
    project_claim(board, "solo", run_id="20260101-000000-aaaa")
    marked = project_done(board, "solo")
    project_release(board, "solo", run_id="20260101-000000-aaaa")
    assert project_show(board, "solo").done_at == marked.done_at


def test_the_project_list_puts_done_projects_last_in_priority_order(board):
    for slug, priority in (("a", 1), ("b", 2), ("c", 3), ("d", 4)):
        _project(board, slug, priority)
    project_done(board, "a")
    project_done(board, "c")
    assert [p.slug for p in project_list(board)] == ["b", "d", "a", "c"]
    project_edit(board, "d", priority=0)
    assert [p.slug for p in project_list(board)] == ["d", "b", "a", "c"]


# --------------------------------------------------------- a card reopens a project --


def test_a_new_todo_card_reopens_a_done_project_and_a_backlog_card_does_not(board):
    _project(board, "solo")
    project_done(board, "solo")

    card_new(board, card_id="solo-a1", project="solo", title="t", goal="g", test="t")
    assert project_show(board, "solo").done_at is not None  # backlog: nothing is ready

    card_new(
        board, card_id="solo-a2", project="solo", title="t", goal="g", test="t", stack="todo"
    )
    assert project_show(board, "solo").done_at is None


def test_a_card_moved_into_todo_reopens_a_done_project(board):
    _project(board, "solo")
    card_new(board, card_id="solo-a1", project="solo", title="t", goal="g", test="t")
    project_done(board, "solo")
    card_move(board, "solo-a1", to="todo")
    assert project_show(board, "solo").done_at is None


def test_a_decision_answered_into_todo_reopens_a_done_project_and_into_done_does_not(
    board, tmp_path, monkeypatch
):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)  # the question and the follow-up
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "no-config"))  # reach no chat
    _project(board, "solo")
    for card_id in ("solo-a1", "solo-a2"):
        _card(board, card_id, "solo", Stack.IN_PROGRESS)
        card_ask(
            board, card_id, question="Which?", options=["x", "y"], brief="b",
            recommendation="x",
        )
    project_done(board, "solo")

    card_answer(board, "solo-a1", answer="x", done=True)
    assert project_show(board, "solo").done_at is not None
    card_answer(board, "solo-a2", answer="y")
    assert project_show(board, "solo").done_at is None


def test_a_go_ahead_reply_on_telegram_reopens_a_done_project(tmp_path, monkeypatch):
    from test_inbox import SET_ASIDE_ACK, _message, _poll, _set_aside

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "no-config"))
    _set_aside(tmp_path)
    project_done(tmp_path, "widgets")
    _poll(tmp_path, [_message("go ahead now", reply_to=SET_ASIDE_ACK)], [])
    assert load_card(tmp_path / "projects" / "widgets" / "cards" / "wid-a3.json").stack == Stack.TODO
    assert project_show(tmp_path, "widgets").done_at is None


# ----------------------------------------------- which projects a wake marks done --


def _board_with(root: Path, cards: list[tuple[Stack, str | None]]) -> None:
    _project(root, "solo")
    for n, (stack, pr) in enumerate(cards, 1):
        _card(root, f"solo-a{n}", "solo", stack, pr)


@pytest.mark.parametrize(
    ("cards", "pr_states", "expect"),
    [
        # the owner's example: three done cards, every PR merged long ago
        ([(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2)), (Stack.DONE, _pr(3))], {}, True),
        ([(Stack.DONE, _pr(1)), (Stack.CANCELLED, None)], {}, True),
        ([(Stack.CANCELLED, None), (Stack.CANCELLED, None)], {}, True),
        # a card with no code counts as merged
        ([(Stack.DONE, None), (Stack.DONE, _pr(2))], {}, True),
        # one PR that is open, closed without merging, or could not be read
        ([(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2))], {_pr(2): "OPEN"}, False),
        ([(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2))], {_pr(2): "CLOSED"}, False),
        ([(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2))], {_pr(2): None}, False),
        # a card that is not done or cancelled
        ([(Stack.DONE, _pr(1)), (Stack.TODO, None)], {}, False),
        ([(Stack.DONE, _pr(1)), (Stack.IN_PROGRESS, None)], {}, False),
        ([(Stack.DONE, _pr(1)), (Stack.BACKLOG, None)], {}, False),
        ([(Stack.DONE, _pr(1)), (Stack.DECISION_NEEDED, None)], {}, False),
        # a project with no card has not started planning
        ([], {}, False),
    ],
)
def test_a_wake_marks_done_exactly_the_projects_that_finished(board, cards, pr_states, expect):
    _board_with(board, cards)
    lines = []

    marked = mark_finished(board, **_reads(pr_states), out=lines.append)

    assert marked == (["solo"] if expect else [])
    assert (project_show(board, "solo").done_at is not None) is expect
    unreadable = any(state is None for state in pr_states.values())
    assert lines == (
        [f"project_done=solo: {DONE_MESSAGE}"]
        if expect
        else ["project_done=solo: left open, gh pr view " + _pr(2) + " failed: no network"]
        if unreadable
        else []
    )
    assert every_project_done(board) is expect


def test_the_cost_is_one_read_per_done_card_and_per_wake_branch_of_a_project_that_may_have_finished(
    board,
):
    _board_with(board, [(Stack.DONE, _pr(1)), (Stack.DONE, None), (Stack.DONE, _pr(3))])
    _project(board, "busy")
    _card(board, "busy-a1", "busy", Stack.DONE, _pr(4))
    _card(board, "busy-a2", "busy", Stack.TODO)
    asked = []

    mark_finished(board, **_reads(asked=asked))
    assert sorted(asked) == [("pr", _pr(1)), ("pr", _pr(3))]  # none for `busy` or the no-code card

    asked.clear()
    mark_finished(board, **_reads(asked=asked))
    assert asked == []  # `solo` is done: it is not read again


WAKE_BRANCH = "wake/20261017-000000-0001"


@pytest.mark.parametrize(("wake_pr_merged", "expect"), [(False, False), (True, True)])
def test_a_card_merged_into_a_wake_branch_counts_only_once_that_branchs_own_pr_has_merged(
    board, wake_pr_merged, expect
):
    """Three done cards; one PR merged into the wake's shared branch, not the default
    branch. The work has reached the default branch only when the wake branch's own PR
    has merged (`github.waiting_branch`, the rule `github base-branch` uses)."""
    _board_with(board, [(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2)), (Stack.DONE, _pr(3))])
    asked, lines = [], []

    marked = mark_finished(
        board,
        **_reads(
            bases={_pr(2): WAKE_BRANCH}, branches={WAKE_BRANCH: wake_pr_merged}, asked=asked
        ),
        out=lines.append,
    )

    assert marked == (["solo"] if expect else [])
    assert (project_show(board, "solo").done_at is not None) is expect
    assert lines == ([f"project_done=solo: {DONE_MESSAGE}"] if expect else [])
    assert ("branch", WAKE_BRANCH) in asked


def test_a_wake_branchs_own_pr_is_read_once_per_wake_however_many_cards_merged_into_it(board):
    _board_with(board, [(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2)), (Stack.DONE, _pr(3))])
    _project(board, "other")
    _card(board, "other-a1", "other", Stack.DONE, _pr(4))
    asked = []

    mark_finished(
        board,
        **_reads(
            bases={_pr(2): WAKE_BRANCH, _pr(3): WAKE_BRANCH, _pr(4): WAKE_BRANCH},
            branches={WAKE_BRANCH: False},
            asked=asked,
        ),
    )

    # one read for `solo`'s two cards and for `other`, which is in the same repository
    assert [a for a in asked if a[0] == "branch"] == [("branch", WAKE_BRANCH)]


def test_a_card_merged_into_the_branch_of_a_merged_pr_has_reached_the_default_branch(board):
    """A card stacked on another card's branch, whose PR merged into the default branch."""
    _board_with(board, [(Stack.DONE, _pr(1)), (Stack.DONE, _pr(2))])
    assert mark_finished(
        board, **_reads(bases={_pr(2): "card/solo-a1"}, branches={"card/solo-a1": True})
    ) == ["solo"]


def test_a_project_already_done_is_not_marked_again(board):
    _board_with(board, [(Stack.DONE, _pr(1))])
    marked = project_done(board, "solo")
    card = load_card(board / "projects/solo/cards/solo-a1.json")
    assert not finished(marked, [card], **_reads())


def test_a_board_with_no_project_is_not_every_project_done(board):
    assert every_project_done(board) is False


# ------------------------------------------------------------ the wake and the board --


def _finish_every_card(root: Path) -> None:
    """Every card of the fixture board is done with a PR."""
    for n, path in enumerate(sorted(root.glob("projects/*/cards/*.json")), 1):
        card = load_card(path)
        card.stack, card.pr = Stack.DONE, _pr(n)
        save_card(path, card)


def test_three_done_cards_with_merged_prs_are_marked_done_on_the_next_wake(env):  # noqa: F811
    """The owner's example. `solo` has three done cards whose PRs merged long ago: the next
    wake marks it done and commits that, then goes on to a project with work."""
    _project(env.root, "solo", priority=0)
    for n in (1, 2, 3):
        _card(env.root, f"solo-a{n}", "solo", Stack.DONE, _pr(n))
    _git(["add", "-A"], env.root)
    _git(["commit", "-q", "-m", "solo finished"], env.root)
    asked = []

    assert env.wake(**_reads(asked=asked)) == 0

    assert f"project_done=solo: {DONE_MESSAGE}" in env.lines
    assert sorted(asked) == [("pr", _pr(1)), ("pr", _pr(2)), ("pr", _pr(3))]
    assert project_show(env.root, "solo").done_at is not None
    assert _value(env.lines, "project") == "high"  # solo had priority 0 and was passed over
    assert "project done: solo" in _git(["log", "--format=%s"], env.root)

    asked.clear()
    env.lines.clear()
    assert env.wake(**_reads(asked=asked)) == 0
    assert asked == []  # a done project is not read again
    assert not any(line.startswith("project_done=") for line in env.lines)


def test_an_open_wake_branch_pr_keeps_its_project_unmarked_and_swept_until_it_merges(env):  # noqa: F811
    """`solo` has three done cards; one PR merged into the wake's shared branch, whose own
    PR is open. The wake does not mark `solo` done, so its sweep still reads the open pull
    request. After the wake branch's PR merges, the next wake marks `solo` done and sweeps
    it no more."""
    _project(env.root, "solo", priority=0)
    for n in (1, 2, 3):
        _card(env.root, f"solo-a{n}", "solo", Stack.DONE, _pr(n))
    _git(["add", "-A"], env.root)
    _git(["commit", "-q", "-m", "solo finished"], env.root)
    swept = []

    def sweep(root, slug):
        swept.append(slug)
        return []

    open_wake_pr = _reads(bases={_pr(2): WAKE_BRANCH}, branches={WAKE_BRANCH: False})
    assert env.wake(sweep=sweep, **open_wake_pr) == 0

    assert project_show(env.root, "solo").done_at is None
    assert not any(line.startswith("project_done=") for line in env.lines)
    assert "solo" in swept

    swept.clear()
    env.lines.clear()
    merged_wake_pr = _reads(bases={_pr(2): WAKE_BRANCH}, branches={WAKE_BRANCH: True})
    assert env.wake(sweep=sweep, **merged_wake_pr) == 0

    assert f"project_done=solo: {DONE_MESSAGE}" in env.lines
    assert project_show(env.root, "solo").done_at is not None
    assert "solo" not in swept


def test_when_every_project_is_done_the_wake_stops_and_turns_the_timers_off(env):  # noqa: F811
    _finish_every_card(env.root)
    commit = _git(["rev-parse", "HEAD"], env.root).strip()
    off = []

    def timers_off():
        off.append("called")
        return ["wake", "review", "plan"]

    assert env.wake(**_reads(), timers_off=timers_off) == 0

    assert env.lines == [
        f"project_done=low: {DONE_MESSAGE}",
        f"project_done=high: {DONE_MESSAGE}",
        "all_done: every project is done; nothing is left to wake",
        "timers_off: removed 3 autonomouse crontab lines",
    ]
    assert off == ["called"]
    assert not (env.fake_dir / "args").exists()  # no session
    assert not (env.root / "runs").exists() or not list((env.root / "runs").glob("*.json"))
    assert _git(["log", "--format=%s", f"{commit}..HEAD"], env.root).strip() == "project done: low, high"
    assert [p.slug for p in project_list(env.root)] == ["low", "high"]
    assert all(p.done_at for p in project_list(env.root))


def test_a_wake_with_projects_left_does_not_touch_the_timers(env):  # noqa: F811
    for card_id in ("hi-a1", "hi-a2"):
        path = env.root / "projects" / "high" / "cards" / f"{card_id}.json"
        card = load_card(path)
        card.stack, card.pr = Stack.DONE, _pr(int(card_id[-1]))
        save_card(path, card)
    off = []

    # high is done, low is not: low-a1 waits on hi-a2, which is done now, so low starts
    assert env.wake(**_reads(), timers_off=lambda: off.append(1) or []) == 0

    assert off == []
    assert project_show(env.root, "high").done_at is not None
    assert project_show(env.root, "low").done_at is None


def test_the_wake_removes_exactly_the_three_lines_and_keeps_the_others(env, monkeypatch):  # noqa: F811
    # a crontab of its own beside the board's: `cron` would share the controller fixture's
    # folder names, and its HOME keeps `timer.install` off this machine's state folder
    folder = env.tmp / "crontab-dir"
    folder.mkdir()
    store = folder / "crontab.txt"
    crontab = folder / "crontab"
    crontab.write_text(FAKE_CRONTAB.format(store=store))
    crontab.chmod(0o755)
    home = env.tmp / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AUTONOMOUSE_STATE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: str(home / ".local" / "bin" / name))
    store.write_text("".join(f"{line}\n" for line in FOREIGN))
    timer.install(crontab=str(crontab), invocation=INVOCATION)
    assert sum("# autonomouse " in line for line in store.read_text().splitlines()) == 3
    _finish_every_card(env.root)

    code = env.wake(**_reads(), timers_off=lambda: timer.remove(crontab=str(crontab)))

    assert code == 0
    assert store.read_text().splitlines() == FOREIGN
    assert env.lines[-1] == "timers_off: removed 3 autonomouse crontab lines"

    # a wake started by hand later finds the same board and nothing left to remove
    env.lines.clear()
    assert env.wake(**_reads(), timers_off=lambda: timer.remove(crontab=str(crontab))) == 0
    assert env.lines[-1] == "timers_off: the crontab holds no autonomouse lines"


def test_a_timer_removal_that_fails_fails_the_wake(env):  # noqa: F811
    _finish_every_card(env.root)

    def refused():
        raise timer.TimerError("crontab - failed: refused")

    assert env.wake(**_reads(), timers_off=refused) == 1
    assert env.lines[-1] == "timers_off=failed: TimerError: crontab - failed: refused"
    assert all(p.done_at for p in project_list(env.root))  # the marks stand; a wake retries


def test_a_board_with_no_project_leaves_the_timers_on(env, tmp_path):  # noqa: F811
    empty = tmp_path / "empty-board"
    empty.mkdir()
    off = []

    code = controller.wake(
        empty, **env.kwargs_for(timers_off=lambda: off.append(1) or [])
    )

    assert code == 0 and off == []
    assert env.lines == ["nothing to do: no project has ready or reclaimable cards"]


def test_a_done_project_is_left_alone_by_the_pick_and_the_follow_up(env):  # noqa: F811
    """`high` has ready cards and a done card with a stacked PR. While it is not done the
    wake follows the stacked PR up and starts it; marked done by hand, the wake does neither."""
    path = env.root / "projects" / "high" / "cards" / "hi-a1.json"
    card = load_card(path)
    card.stack, card.pr, card.base_branch = Stack.DONE, _pr(1), "card/other"
    save_card(path, card)
    followed = []

    def follow_up_done(root, card, project, *, run_id):
        followed.append(card.id)
        raise controller.followup.MergeError("not under test")

    project_done(env.root, "high")
    env.lines.clear()
    assert env.wake(follow_up_done=follow_up_done) == 0
    assert followed == []
    assert not any(line.startswith("run_id=") for line in env.lines)
    assert env.lines[-1] == "nothing to do: no project has ready or reclaimable cards"

    project_reopen(env.root, "high")
    env.lines.clear()
    assert env.wake(follow_up_done=follow_up_done) == 0
    assert followed == ["hi-a1"]
    assert _value(env.lines, "project") == "high"


def test_project_on_a_done_project_says_it_is_done(env):  # noqa: F811
    project_done(env.root, "high")
    assert env.wake(project="high") == 0
    assert env.lines == ["nothing to do: project 'high' is done"]


def test_a_wake_running_on_a_project_goes_on_when_the_project_is_marked_done(env, monkeypatch):  # noqa: F811
    go = env.tmp / "go"
    monkeypatch.setenv("FAKE_WAIT", str(go))
    codes = []
    wake = threading.Thread(target=lambda: codes.append(env.wake()))
    wake.start()
    try:
        deadline = time.time() + 10
        while project_show(env.root, "high").claim.run_id is None:
            assert time.time() < deadline, "the wake never claimed high"
            time.sleep(0.05)
        project_done(env.root, "high")
        assert project_show(env.root, "high").claim.run_id is not None  # still working it
    finally:
        go.touch()
        wake.join(timeout=15)

    assert codes == [0]
    ended = project_show(env.root, "high")
    assert ended.claim.run_id is None  # the wake released its claim
    assert ended.done_at is not None  # and the mark survived that write


# ------------------------------------------------------------------ the command line --


@pytest.fixture
def shell(cron: Cron, tmp_path):  # noqa: F811
    """`autonomouse` in a subprocess on a board of its own, with the fake crontab and
    fake tools first on PATH and a HOME of their own."""
    board = tmp_path / "cli-board"
    env_vars = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(board),
        HOME=str(cron.home),
        AUTONOMOUSE_CONFIG=str(cron.config_dir),
        PATH=f"{Path(cron.crontab).parent}:{os.environ['PATH']}",
    )
    env_vars.pop("AUTONOMOUSE_STATE", None)

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", *args],
            env=env_vars,
            capture_output=True,
            text=True,
            check=False,
        )

    class Shell:
        pass

    sh = Shell()
    sh.run, sh.board, sh.cron = run, board, cron
    for slug, priority in (("alpha", "1"), ("beta", "2")):
        made = run(
            "project", "new", "--slug", slug, "--name", slug, "--goal", "g",
            "--repo", "https://github.com/t/t", "--repo-path", f"/tmp/{slug}",
            "--default-branch", "main", "--priority", priority,
        )  # fmt: skip
        assert made.returncode == 0, made.stderr
    return sh


def _card_new(sh, card_id: str, project: str, *extra: str):
    return sh.run(
        "card", "new", "--id", card_id, "--project", project, "--title", "t", "--goal", "g",
        "--test", "t", *extra,
    )  # fmt: skip


def test_project_done_marks_the_project_and_the_lists_show_it_last(shell):
    done = shell.run("project", "done", "alpha")
    assert done.returncode == 0, done.stderr
    assert "done_at: " in done.stdout

    listed = shell.run("project", "list").stdout.splitlines()
    assert [line.split()[0] for line in listed] == ["beta", "alpha"]
    assert listed[1].endswith("  done") and not listed[0].endswith("  done")
    assert "done_at: " in shell.run("project", "show", "alpha").stdout
    assert "done_at" not in shell.run("project", "show", "beta").stdout
    assert json.loads(shell.run("project", "list", "--json").stdout)[1]["done_at"]

    assert shell.run("project", "done", "nope").returncode == 1


def test_a_new_todo_card_on_a_done_project_installs_the_missing_timers(shell):
    shell.cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))
    assert shell.run("project", "done", "alpha").returncode == 0

    made = _card_new(shell, "alp-a1", "alpha", "--json")

    assert made.returncode == 0, made.stderr
    assert json.loads(made.stdout)["id"] == "alp-a1"  # stdout stays one JSON document
    assert "project alpha has work again: the three crontab lines are installed again" in made.stderr
    assert "done_at" not in json.loads(shell.run("project", "show", "alpha", "--json").stdout)
    assert len(shell.cron.ours()) == 3
    assert shell.cron.lines()[: len(FOREIGN)] == FOREIGN

    # the lines are there now: a second card says nothing and writes nothing
    before = shell.cron.lines()
    assert shell.run("project", "done", "alpha").returncode == 0
    again = _card_new(shell, "alp-a2", "alpha")
    assert again.returncode == 0 and again.stderr == ""
    assert shell.cron.lines() == before


def test_a_card_that_does_not_reopen_a_project_leaves_the_crontab_alone(shell):
    # beta is not done; alpha is done and gets a backlog card
    assert shell.run("project", "done", "alpha").returncode == 0
    assert _card_new(shell, "bet-a1", "beta").returncode == 0
    assert _card_new(shell, "alp-a1", "alpha", "--backlog").returncode == 0
    assert shell.cron.lines() == []
    assert "done_at" in json.loads(shell.run("project", "show", "alpha", "--json").stdout)


def test_a_card_moved_into_todo_on_a_done_project_installs_the_timers(shell):
    assert _card_new(shell, "alp-a1", "alpha", "--backlog").returncode == 0
    assert shell.run("project", "done", "alpha").returncode == 0

    moved = shell.run("card", "move", "alp-a1", "--to", "todo")

    assert moved.returncode == 0, moved.stderr
    assert "project alpha has work again" in moved.stderr
    assert len(shell.cron.ours()) == 3
    assert "done_at" not in json.loads(shell.run("project", "show", "alpha", "--json").stdout)


def test_a_crontab_that_refuses_the_write_warns_and_keeps_the_card(shell):
    refusing = Path(shell.cron.crontab)
    refusing.write_text(
        "#!/bin/sh\n"
        'case "$1" in -l) echo "no crontab for u" >&2; exit 1 ;; '
        '*) echo "refused" >&2; exit 1 ;; esac\n'
    )
    assert shell.run("project", "done", "alpha").returncode == 0

    made = _card_new(shell, "alp-a1", "alpha")

    assert made.returncode == 0, made.stderr
    assert "warning: project alpha has work again, but the timers were not installed" in made.stderr
    assert "refused" in made.stderr
    assert "id: alp-a1" in made.stdout


# ----------------------------------------------------------- rearm in the timer module --


def test_rearm_installs_all_three_lines_only_when_one_is_missing(cron: Cron) -> None:  # noqa: F811
    cron.store.write_text("".join(f"{line}\n" for line in FOREIGN))

    written = timer.rearm(crontab=cron.crontab, invocation=INVOCATION)
    assert written is not None and len(written) == 3
    assert cron.lines() == FOREIGN + written

    assert timer.rearm(crontab=cron.crontab, invocation=INVOCATION) is None
    assert cron.lines() == FOREIGN + written  # nothing was written

    partial = [line for line in cron.lines() if not line.endswith("# autonomouse review")]
    cron.store.write_text("".join(f"{line}\n" for line in partial))
    rewritten = timer.rearm(crontab=cron.crontab, invocation=INVOCATION)
    assert rewritten is not None
    assert len(cron.ours()) == 3  # one each, none doubled
    assert cron.lines()[: len(FOREIGN)] == FOREIGN


def test_remove_then_rearm_round_trips(cron: Cron) -> None:  # noqa: F811
    timer.install(crontab=cron.crontab, invocation=INVOCATION)
    assert len(timer.remove(crontab=cron.crontab)) == 3
    assert cron.ours() == []
    assert timer.rearm(crontab=cron.crontab, invocation=INVOCATION) is not None
    assert len(cron.ours()) == 3
