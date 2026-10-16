"""Hand-built situations with a known right answer, three to seven per catalogue question.

Each situation is a board, a set of run records and (for `recover`) a failure, built through the
catalogue's own state builders, plus the one question it asks and the option a careful human
would pick. `tests/record_jev_situations.py` sends each one to Jev once and saves the request
and the reply in `tests/fixtures/jev/situations.json`; `tests/test_jev_questions.py` replays the
saved replies offline and checks the request it builds today is still the one that was recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from autonomouse.config import DEFAULTS
from autonomouse.jev.questions import (
    RECOVER,
    CatalogueQuestion,
    base_state,
    card_state,
    go,
    go_state,
    recover_state,
    size,
)

NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)  # a Tuesday


@dataclass(frozen=True)
class Situation:
    """One state, the question asked about it, and the known right answer."""

    name: str
    entry: CatalogueQuestion
    state: dict[str, Any]
    expected: str

    @property
    def question(self) -> str:
        """The catalogue question's name: ``go``, ``size`` or ``recover``."""
        return self.entry.key.split(":")[0]

    @property
    def entries(self) -> tuple[CatalogueQuestion, ...]:
        """The Jev questions sent in this situation's one request."""
        return (self.entry,)


def card(
    card_id: str,
    *,
    project: str = "system",
    stack: str = "todo",
    position: int = 1,
    size: str = "s",
    title: str = "",
    goal: str = "",
    test: str = "",
    depends_on: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A card dict in the board schema's shape, with the fields a situation cares about."""
    return {
        "id": card_id,
        "project": project,
        "title": title or card_id,
        "stack": stack,
        "position": position,
        "size": size,
        "goal": goal or f"goal of {card_id}",
        "limits": ["docs/rules.md applies"],
        "test": test or f"test of {card_id}",
        "depends_on": list(depends_on),
        "assigned": {
            "model": None,
            "effort": None,
            "agent_type": None,
            "session_id": None,
            "pid": None,
            "picked_up_at": None,
        },
        "worktree": None,
        "branch": None,
        "base_branch": None,
        "pr": None,
        "phase": None,
        "iteration": 0,
        "handoff": {"last_step": None, "next_step": None, "updated_at": None, "run_id": "seed"},
        "decisions": [],
        "history": [],
        "created_at": "2026-09-22T06:20:11Z",
        "updated_at": "2026-09-22T06:20:11Z",
    }


def run(
    run_id: str,
    *,
    started_at: str,
    ended_at: str | None,
    status: str = "stopped",
    summary: str = "",
    cards: tuple[str, ...] = (),
    is_error: bool = False,
    result_text: str = "Done.",
    end_reason: str | None = "other",
) -> dict[str, Any]:
    """A run record in the board schema's shape."""
    return {
        "id": run_id,
        "project": "system",
        "session_id": f"session-{run_id}",
        "pid": 1000,
        "model": "claude-opus-5",
        "started_at": started_at,
        "ended_at": ended_at,
        "cards": list(cards),
        "jev": [],
        "result": {
            "is_error": is_error,
            "stop_reason": "end_turn",
            "num_turns": 40,
            "duration_ms": 1_800_000,
            "total_cost_usd": 3.5,
            "result": result_text,
        },
        "end_reason": end_reason,
        "status": status,
        "summary": summary,
    }


CLEAN_RUNS = [
    run(
        "20260922-000000-aaaa",
        started_at="2026-09-22T00:00:05Z",
        ended_at="2026-09-22T00:41:10Z",
        summary="sys-b1 done (PR #4)",
        cards=("sys-b1",),
    ),
    run(
        "20260922-040000-bbbb",
        started_at="2026-09-22T04:00:05Z",
        ended_at="2026-09-22T04:52:30Z",
        summary="sys-b2 done (PR #5), int-j1 done (PR #3)",
        cards=("sys-b2", "int-j1"),
    ),
    run(
        "20260922-080000-cccc",
        started_at="2026-09-22T08:00:05Z",
        ended_at="2026-09-22T08:47:00Z",
        summary="sys-b3 done (PR #8), sys-e3 done (PR #7)",
        cards=("sys-b3", "sys-e3"),
    ),
]

DONE = [
    card("sys-a1", stack="done", title="package skeleton"),
    card("sys-b1", stack="done", title="board load, validate, save"),
    card("sys-b3", stack="done", title="card and project commands"),
    card("int-j1", project="integrations", stack="done", title="Jev client"),
]

TUI_CARD = card(
    "sys-c1",
    title="project list screen",
    size="s",
    goal="A Textual screen listing projects with their stack counts; Enter opens a project",
    test="Launch the app on the seed board; every project is listed; Enter opens it",
    depends_on=("sys-a1",),
)
INSTALLER_CARD = card(
    "sys-f1",
    position=2,
    title="tools list",
    size="s",
    goal="setup/tools.txt: every tool the system needs with a pinned version and digest",
    test="Every tool the instructions name is in the list; each pin resolves",
    depends_on=("sys-a1",),
)
BLOCKED_CARD = card(
    "sys-d2",
    position=5,
    title="Jev in the run",
    depends_on=("sys-d1",),
)
SYS_D1_TODO = card("sys-d1", position=4, title="run controller", size="m", depends_on=("sys-b8",))
SYS_B8_TODO = card("sys-b8", position=3, title="run records", depends_on=("sys-b9",))


def _base(cards: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
    return base_state(now=NOW, config=DEFAULTS, cards=cards, runs=runs)


# ---------------------------------------------------------------------------- go

_LIMIT_SOON = run(
    "20260922-110000-dddd",
    started_at="2026-09-22T11:00:05Z",
    ended_at="2026-09-22T11:20:00Z",
    status="stopped",
    summary="blocked: usage limit reached",
    is_error=True,
    result_text="Claude usage limit reached. Your limit will reset at 2026-09-22T12:20:00Z.",
    end_reason="usage_limit",
)
_LIMIT_LATER = run(
    "20260922-110000-eeee",
    started_at="2026-09-22T11:00:05Z",
    ended_at="2026-09-22T11:20:00Z",
    status="stopped",
    summary="blocked: usage limit reached",
    is_error=True,
    result_text="Claude usage limit reached. Your limit will reset at 2026-09-22T17:00:00Z.",
    end_reason="usage_limit",
)


def _go(
    cards: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    findings: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """The state `go` reads: the base plus the sweep's findings (sys-s22)."""
    return go_state(_base(cards, runs), findings)


# One open pull request autonomouse opened that needs work, as `autonomouse github sweep --json`
# reports it: sys-b3 is done, and its pull request now conflicts with its base branch.
_CONFLICTING_PR = {
    "number": 8,
    "url": "https://github.com/example/autonomouse/pull/8",
    "head_branch": "card/sys-b3",
    "head_sha": "8" * 40,
    "card_id": "sys-b3",
    "mergeable": "CONFLICTING",
    "runs": "pass",
    "unanswered": 0,
    "conflicts": [],
    "finding": True,
}


def _crash(run_id: str, hour: int) -> dict[str, Any]:
    return run(
        run_id,
        started_at=f"2026-09-22T{hour:02d}:00:05Z",
        ended_at=None,
        status="crashed",
        summary="",
        is_error=True,
        result_text=(
            "Error: ENOENT: no such file or directory, open "
            "'/home/user/.claude/settings.json'"
        ),
        end_reason="crashed",
    )


# Every situation below runs on `DEFAULTS`, whose `wake.interval_hours` is 4, so the request
# each records is built on the project's own cadence like any other (sys-t33).
_GO = go(DEFAULTS["wake"]["interval_hours"])

GO_SITUATIONS = [
    Situation(
        "go-ready-and-clean",
        _GO,
        _go(DONE + [TUI_CARD, INSTALLER_CARD], CLEAN_RUNS),
        "go",
    ),
    Situation(
        "go-nothing-ready",
        _GO,
        _go(DONE + [BLOCKED_CARD, SYS_D1_TODO, SYS_B8_TODO], CLEAN_RUNS),
        "skip",
    ),
    # The same board as go-nothing-ready, and one pull request that conflicts: a finding is
    # work, so the wake starts and its orchestrator works the finding (sys-s22).
    Situation(
        "go-sweep-finding-and-nothing-ready",
        _GO,
        _go(DONE + [BLOCKED_CARD, SYS_D1_TODO, SYS_B8_TODO], CLEAN_RUNS, (_CONFLICTING_PR,)),
        "go",
    ),
    Situation(
        "go-limit-resets-in-twenty-minutes",
        _GO,
        _go(DONE + [TUI_CARD, INSTALLER_CARD], CLEAN_RUNS + [_LIMIT_SOON]),
        "wait_30m",
    ),
    Situation(
        "go-limit-resets-in-five-hours",
        _GO,
        _go(DONE + [TUI_CARD, INSTALLER_CARD], CLEAN_RUNS + [_LIMIT_LATER]),
        "skip",
    ),
    Situation(
        "go-three-identical-crashes",
        _GO,
        _go(
            DONE + [TUI_CARD, INSTALLER_CARD],
            [_crash("20260922-000000-ffff", 0), _crash("20260922-040000-ffff", 4),
             _crash("20260922-080000-ffff", 8)],
        ),
        "skip",
    ),
]

# ---------------------------------------------------------------------------- size
#
# Each card carries a size of its own, as every card does; two carry a size other than the known
# answer, so a reply that only repeats the card's own size misses. The two `split` cards are
# larger than `m`: several deliverables, or a rewrite of every subsystem, that no one worker
# finishes in one turn (sys-i29; they were the `fits` question's `split` cases before it).

_EVERYTHING = card(
    "sys-z1",
    size="m",
    title="screens, installer and notifications",
    goal="Build the Textual TUI (project list, board view, card detail panel, answer "
    "dialog), write install.sh for bare Ubuntu with every pinned tool, and add "
    "`autonomouse notify` with Telegram notifications and retry logic",
    test="Every TUI screen works by keyboard on the seed board; install.sh runs twice on a "
    "clean container with an all-green checklist; a decision sends one message to Telegram",
)
_MIGRATION = card(
    "sys-z2",
    size="m",
    title="move the board to SQLite",
    goal="Replace the JSON board with a SQLite database: new store module, a migration "
    "command that converts every existing board, every CLI command rewritten against it, "
    "the TUI reading from it, the daily review queries rewritten, and the NAS backup "
    "switched from git push to database dumps",
    test="Migrate a copy of the live board and diff every card; every CLI test passes "
    "against SQLite; the TUI shows the same board; a backup restores on a second machine",
)
_RENAME = card(
    "sys-z3",
    size="xs",
    title="rename `handoff.last_step` to `handoff.done`",
    goal="Rename the handoff field `last_step` to `done` in board/schema.py, its tests and "
    "docs/board-schema.md",
    test="pytest passes; grep finds no `last_step` left",
)

_MOVE_CANCEL = card(
    "sys-b4",
    size="xs",
    title="card move, card cancel",
    goal="Move a card along the allowed transitions only (todo to in_progress needs "
    "`start`, not `move`); cancel from any stack with a reason",
    test="Legal moves land with a history row; `move` to a stack that does not exist is "
    "refused; cancel records the reason",
)
_SPIKE = card(
    "sys-d9",
    size="xs",
    title="usage-limit signal spike",
    goal="Find out whether a headless run can read remaining subscription usage "
    "(status-line `rate_limits`, the stream-json rate-limit event, or nothing); write the "
    "evidence and a recommendation in the card's notes and report the choice for the human",
    test="The card's notes carry the evidence and a recommendation; no code merged",
)
_ONE_CONSTANT = card(
    "sys-z4",
    size="xs",
    title="keep ten run records in Jev's state",
    goal="In src/autonomouse/jev/questions.py change `LAST_RUNS = 5` to `LAST_RUNS = 10`",
    test="pytest passes",
)
_LOST_CLAIM = card(
    "sys-z5",
    size="s",
    title="find why a live run loses its project claim",
    goal="About one wake in twenty, a second `autonomouse run` starts on a project whose claim "
    "an alive run holds, and two orchestrators work the same cards. Nobody knows why: the "
    "claim is taken under the decision lock, written to the board, committed under the board "
    "commit lock and pushed by the backup, and the liveness rule reads the claimant's pid and "
    "transcript. Find the cause across the run controller, the board store, the two locks and "
    "the liveness rule, prove it with a test that reproduces it, and fix it",
    test="A test that starts two wakes against one project reproduces the double claim on "
    "main and passes with the fix; pytest passes",
)

SIZE_SITUATIONS = [
    Situation("size-one-constant", size("sys-z4", "xs"),
              card_state(_base(DONE, CLEAN_RUNS), [_ONE_CONSTANT]), "xs"),
    Situation("size-mechanical-rename", size("sys-z3", "xs"),
              card_state(_base(DONE, CLEAN_RUNS), [_RENAME]), "xs"),
    Situation("size-contained-command", size("sys-b4", "xs"),
              card_state(_base(DONE, CLEAN_RUNS), [_MOVE_CANCEL]), "s"),
    Situation("size-bounded-investigation", size("sys-d9", "xs"),
              card_state(_base(DONE, CLEAN_RUNS), [_SPIKE]), "s"),
    Situation("size-cause-across-subsystems", size("sys-z5", "s"),
              card_state(_base(DONE, CLEAN_RUNS), [_LOST_CLAIM]), "m"),
    Situation("size-three-deliverables-in-one", size("sys-z1", "m"),
              card_state(_base(DONE, CLEAN_RUNS), [_EVERYTHING]), "split"),
    Situation("size-migration-across-every-subsystem", size("sys-z2", "m"),
              card_state(_base(DONE, CLEAN_RUNS), [_MIGRATION]), "split"),
]

# ---------------------------------------------------------------------------- recover

_RECOVER_BASE = _base(DONE + [TUI_CARD], CLEAN_RUNS)

RECOVER_SITUATIONS = [
    Situation(
        "recover-one-network-timeout",
        RECOVER,
        recover_state(_RECOVER_BASE, error="API Error: Request timed out (ETIMEDOUT) after "
                      "600000ms", attempts=1, reset_at=None),
        "retry",
    ),
    Situation(
        "recover-limit-resets-in-three-hours",
        RECOVER,
        recover_state(_RECOVER_BASE, error="Claude usage limit reached. Your limit will reset "
                      "at 2026-09-22T15:00:00Z.", attempts=1, reset_at="2026-09-22T15:00:00Z"),
        "next_wake",
    ),
    Situation(
        "recover-limit-already-reset",
        RECOVER,
        recover_state(_RECOVER_BASE, error="Claude usage limit reached. Your limit will reset "
                      "at 2026-09-22T11:30:00Z.", attempts=1, reset_at="2026-09-22T11:30:00Z"),
        "retry",
    ),
    Situation(
        "recover-authentication-error",
        RECOVER,
        recover_state(_RECOVER_BASE, error='API Error: 401 {"type":"error","error":{"type":'
                      '"authentication_error","message":"invalid x-api-key"}}', attempts=1,
                      reset_at=None),
        "raise",
    ),
    Situation(
        "recover-same-error-three-times",
        RECOVER,
        recover_state(_RECOVER_BASE, error="Error: git worktree add failed: fatal: "
                      "'card/sys-c1' is already checked out at '/tmp/old-worktree'",
                      attempts=3, reset_at=None),
        "raise",
    ),
]

SITUATIONS = GO_SITUATIONS + SIZE_SITUATIONS + RECOVER_SITUATIONS
