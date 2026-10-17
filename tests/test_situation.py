"""Tests for `autonomouse.situation` (sys-s7): the Situation block every orchestrator prompt
ends with, read from a temporary board — the next fire, the usage-limit reset in force, and
the ready cards — and the run record's `waiting_for` field, which versions before one wake
per machine wrote and records still load. A wake that starts while another wake is alive
starts nothing, so the block no longer says who holds the project or who waits for it.

The next fire comes from a fake `crontab` in a temporary folder (sys-u9), so no test reads or
writes the machine's real crontab."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autonomouse import situation, timer
from autonomouse.board.commands import (
    card_move,
    card_new,
    project_new,
    project_show,
)
from autonomouse.board.runs import (
    load_run,
    run_from_dict,
    run_start,
    save_run,
)
from autonomouse.board.schema import BoardValidationError, Stack
from autonomouse.board.store import load_card, save_card

LOCAL = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 9, 26, 9, 10, tzinfo=LOCAL)
WAITER = "20260926-090000-bbbb"

# `crontab -l` prints the stored crontab, or fails as the real one does when there is none,
# or fails otherwise when the store holds FAIL.
FAKE_CRONTAB = """#!/bin/sh
STORE={store}
[ "$1" = "-l" ] || {{ echo "fake crontab: unexpected arguments: $*" >&2; exit 2; }}
[ -f "$STORE" ] || {{ echo "no crontab for $(id -un)" >&2; exit 1; }}
[ "$(cat "$STORE")" = FAIL ] && {{ echo "permission denied" >&2; exit 1; }}
cat "$STORE"
"""


def _crontab(folder: Path, lines: list[str] | None) -> str:
    """A fake `crontab` command in `folder` whose `crontab -l` prints `lines`, or, with None,
    fails as the real one does for a user with no crontab."""
    folder.mkdir(parents=True, exist_ok=True)
    store = folder / "crontab.txt"
    if lines is not None:
        store.write_text("".join(f"{line}\n" for line in lines))
    command = folder / "crontab"
    command.write_text(FAKE_CRONTAB.format(store=store))
    command.chmod(0o755)
    return str(command)


def _timer_lines(hours: int = 4) -> list[str]:
    """The three lines `autonomouse timer install` writes: wake, review, plan."""
    return timer.cron_lines(
        hours, "/opt/autonomouse", home=Path("/home/u"), state_dir=Path("/home/u/.state")
    )


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Project `high` with cards hi-a1 (todo, ready), hi-a2 (todo, depends on hi-a1) and hi-a3
    (done); project `low` with low-a1 ready. No transcripts folder yet. A fake crontab
    beside the board holds the three timer lines."""
    root = tmp_path / "board"
    root.mkdir()
    for slug, priority in (("high", 1), ("low", 2)):
        project_new(
            root,
            slug=slug,
            name=slug,
            goal="g",
            repo="https://github.com/t/t",
            repo_path=str(tmp_path / slug),
            default_branch="main",
            priority=priority,
        )
    for card_id, project, position, deps in (
        ("hi-a3", "high", 2, []),
        ("hi-a1", "high", 0, []),
        ("hi-a2", "high", 1, ["hi-a1"]),
        ("low-a1", "low", 0, []),
    ):
        card_new(
            root, card_id=card_id, project=project, title="t", goal="g", test="t",
            position=position, depends_on=deps,
        )
        card_move(root, card_id, to="todo")
    _set_stack(root, "hi-a3", Stack.DONE)
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(tmp_path / "no-config"))
    # The timer is installed: `_build` reads this crontab unless a test passes another.
    _crontab(tmp_path / "cron", _timer_lines())
    return root


def _set_stack(root: Path, card_id: str, stack: Stack) -> None:
    """Put a card in `stack` directly: `card move` refuses the steps only `card start` and
    `card finish` take, and the Situation reads only the stack."""
    path = root / "projects" / card_id.split("-")[0].replace("hi", "high") / "cards" / f"{card_id}.json"
    card = load_card(path)
    card.stack = stack
    save_card(path, card)


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _build(
    root: Path,
    slug: str,
    *,
    now: datetime = NOW,
    hours: int = 4,
    crontab: str | None = None,
):
    return situation.build(
        root,
        project_show(root, slug),
        interval_hours=hours,
        now=now,
        crontab=crontab or str(root.parent / "cron" / "crontab"),
    )


def test_the_block_names_the_project_the_next_fire_the_limit_and_the_ready_cards(board):
    facts = _build(board, "high", now=NOW, hours=4)
    assert facts.interval_hours == 4
    assert facts.next_fire == datetime(2026, 9, 26, 12, 0, tzinfo=LOCAL)
    assert facts.usage_limit_reset is None
    # hi-a2 depends on hi-a1, still todo; hi-a3 is done; low-a1 is another project's.
    assert facts.ready == ["hi-a1"]
    assert facts.text() == (
        "Situation:\n"
        "Project: high.\n"
        "Wake interval: every 4 hours; next fire: 2026-09-26 12:00 UTC+05:30.\n"
        "Usage limit: none in force.\n"
        "Ready cards: hi-a1."
    )
    assert _build(board, "high", hours=6).next_fire == datetime(
        2026, 9, 26, 12, 0, tzinfo=LOCAL
    )
    assert _build(board, "high", hours=24).next_fire == datetime(
        2026, 9, 27, 0, 0, tzinfo=LOCAL
    )


def test_ready_cards_come_in_position_order_once_their_dependencies_are_done(board, tmp_path):
    _set_stack(board, "hi-a1", Stack.DONE)
    card_new(board, card_id="hi-a0", project="high", title="t", goal="g", test="t", position=-1)
    card_move(board, "hi-a0", to="todo")
    assert _build(board, "high").ready == ["hi-a0", "hi-a2"]
    assert _build(board, "low").ready == ["low-a1"]


def test_a_future_usage_limit_reset_shows_and_a_past_one_does_not(board):
    def limited(run_id: str, reset_at: str) -> None:
        run = run_start(board, run_id=run_id, session_id=f"s-{run_id}", pid=_dead_pid(), model="m")
        run.usage_limit = {"hit_at": "2026-09-26T03:00:00Z", "reset_at": reset_at, "text": "hit"}
        save_run(board, run)

    limited("20260926-030000-0001", "2026-09-26T03:00:00Z")  # 08:30 local: past
    assert _build(board, "high").usage_limit_reset is None
    limited("20260926-030100-0002", "2026-09-26T04:00:00Z")  # 09:30 local: in force
    facts = _build(board, "high")
    assert facts.usage_limit_reset == "2026-09-26T04:00:00Z"
    assert "Usage limit: resets at 2026-09-26T04:00:00Z." in facts.text()
    limited("20260926-030200-0003", "2026-09-26T05:00:00Z")  # the latest one in force
    assert _build(board, "high").usage_limit_reset == "2026-09-26T05:00:00Z"
    later = NOW + timedelta(hours=2)  # 11:10 local: both have passed
    assert _build(board, "high", now=later).usage_limit_reset is None


def test_build_refuses_a_naive_clock(board, tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        _build(board, "high", now=datetime(2026, 9, 26, 9, 10))  # noqa: DTZ001


def test_waiting_for_is_a_run_record_field_that_old_records_still_carry(board):
    waiter = run_start(board, run_id=WAITER, session_id="sess-waiter", pid=_dead_pid(), model="m")
    assert waiter.waiting_for is None  # nothing writes it any more
    waiter.waiting_for = "high"
    save_run(board, waiter)
    assert load_run(board, WAITER).waiting_for == "high"
    assert load_run(board, WAITER).to_dict()["waiting_for"] == "high"
    # A record written before the field existed loads as if it were null.
    old = {k: v for k, v in waiter.to_dict().items() if k != "waiting_for"}
    assert run_from_dict(old, source="old").waiting_for is None
    with pytest.raises(BoardValidationError, match="waiting_for"):
        run_from_dict({**waiter.to_dict(), "waiting_for": 7}, source="bad")


def test_the_next_fire_is_named_only_when_the_wake_line_is_in_the_crontab(board, tmp_path):
    """sys-u9: in wake 7 the Situation named an 08:00 fire on a machine with no crontab at all,
    and the orchestrator stopped starting cards 30 minutes early for a fire that never came."""
    fire_line = "Wake interval: every 4 hours; next fire: 2026-09-26 12:00 UTC+05:30."
    no_timer = "Wake interval: every 4 hours; no timer is installed, so no fire is due."

    installed = _build(board, "high", crontab=_crontab(
        tmp_path / "installed", _timer_lines()))
    assert installed.next_fire == datetime(2026, 9, 26, 12, 0, tzinfo=LOCAL)
    assert fire_line in installed.text().splitlines()

    for name, crontab in (
        ("no crontab command", str(tmp_path / "nowhere" / "crontab")),
        ("no crontab for the user", _crontab(tmp_path / "empty", None)),
        ("other jobs only", _crontab(
            tmp_path / "others",
            ["30 1 * * * /usr/local/bin/backup --nightly", *_timer_lines()[1:]],
        )),
    ):
        facts = _build(board, "high", crontab=crontab)
        assert facts.next_fire is None, name
        assert facts.timer_error is None, name
        text = facts.text()
        assert no_timer in text.splitlines(), name
        assert "next fire:" not in text and "12:00" not in text, name

    unreadable = _build(board, "high", crontab=_crontab(
        tmp_path / "broken", ["FAIL"]))
    assert unreadable.next_fire is None
    assert "Wake interval: every 4 hours; next fire unknown: " in unreadable.text()
    assert "permission denied" in unreadable.text()
    assert "no timer is installed" not in unreadable.text()
