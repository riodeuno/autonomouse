"""Tests for `autonomouse review improve` (autonomouse.review.improve): a behaviour review
written by `build_review` from behaviour logs holding the rehearsal's recurring patterns
(hand edits of cards in two runs, sibling PRs conflicting in five), read back into
proposal cards on a temp board."""

import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from autonomouse.board.commands import card_cancel, card_list, card_new, project_new
from autonomouse.review import improve
from autonomouse.review.behaviour import Log, build_review, run_started

REPO = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
RUNS = [
    "20260922-080000-aaaa",
    "20260922-120000-bbbb",
    "20260922-160000-cccc",
    "20260922-200000-dddd",
    "20260923-000000-eeee",
]


def _agent(key, role, card, *, idle_s=300.0):
    return {
        "agent": key,
        "role": role,
        "card": card,
        "model": "claude-sonnet-5",
        "effort": "high",
        "cost_usd": 1.0,
        "longest_idle_s": idle_s,
        "longest_idle_from": "2026-09-22T08:01:00.000Z",
    }


def _summary(run_id, *, agents=(), prs=(), clock=()):
    return {
        "run_id": run_id,
        "session_id": f"session-{run_id[-4:]}",
        "project": "system",
        "reason": None,
        "agents": list(agents),
        "prs": list(prs),
        "cost": {"total_usd": 5.0, "by_model": {}, "unpriced_models": []},
        "jev": [],
        "clock": list(clock),
    }


# A card of each of the first two runs edited by hand, as `hand_edits` finds them.
HAND_EDITS = [
    [RUNS[0], "sys-x1", "0a1b2c3d", "2026-09-22T08:30:00Z", "stack"],
    [RUNS[1], "sys-x2", "4e5f6a7b", "2026-09-22T12:30:00Z", "history, phase"],
]


def _rehearsal_logs() -> list[Log]:
    """Five runs, each leaving a PR conflicting with its sibling's; `HAND_EDITS` has a
    hand edit in the first two. Below the threshold: a time on the board that cannot be
    true in one run, and one worker idling for 45 minutes in one run."""
    summaries = []
    for i, run in enumerate(RUNS):
        card = f"sys-x{i + 1}"
        worker = f"a{i}worker"
        agents = [
            _agent("orchestrator", "orchestrator", None, idle_s=2400.0),
            _agent(worker, "worker", card, idle_s=2700.0 if i == 2 else 300.0),
        ]
        clock = []
        if i == 3:
            clock.append(
                {
                    "class": "answered_before_asked",
                    "card": card,
                    "decision": "d1",
                    "asked_at": "2026-09-22T20:30:00Z",
                    "answered_at": "2026-09-22T20:10:00Z",
                }
            )
        prs = [
            {
                "card": card,
                "state": "conflicting",
                "url": f"https://github.com/o/r/pull/{i + 1}",
            },
            {
                "card": f"sys-y{i + 1}",
                "state": "merged",
                "url": "https://github.com/o/r/pull/9",
            },
        ]
        summaries.append(_summary(run, agents=agents, prs=prs, clock=clock))
    return [Log(s["run_id"], run_started(s["run_id"]), s) for s in summaries]


def _write_review(board: Path, logs: list[Log]) -> Path:
    text = build_review(
        logs,
        cards=[],  # the improvement step reads no section built from the board's cards
        edits=HAND_EDITS,
        days=1,
        since=NOW - timedelta(days=1),
        now=NOW,
        folder=board.parent / "logs",
    )
    path = board / "reviews" / "behaviour-2026-09-23.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch) -> Path:
    """An empty config folder, so `improve` reads the defaults, never the real
    `~/.config/autonomouse/config.toml`; a test writes `config.toml` here to set a key."""
    folder = tmp_path / "config"
    folder.mkdir()
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(folder))
    return folder


@pytest.fixture
def board(tmp_path) -> Path:
    root = tmp_path / "board"
    project_new(
        root,
        slug="system",
        name="System",
        goal="g",
        repo="https://github.com/o/r",
        repo_path=str(tmp_path / "repo"),
        default_branch="main",
        priority=1,
    )
    card_new(
        root,
        card_id="sys-d7",
        project="system",
        title="instructions",
        goal="g",
        test="t",
    )
    card_new(
        root, card_id="sys-g2", project="system", title="review", goal="g", test="t"
    )
    return root


def test_the_rehearsal_patterns_become_exactly_two_backlog_cards_and_no_duplicates(
    board,
):
    review = _write_review(board, _rehearsal_logs())

    outcomes = improve.improve(board, review, min_runs=2)

    created = [o for o in outcomes if o.action == "created"]
    assert [(o.card, o.pattern.name) for o in created] == [
        ("sys-d8", "hand edits of cards"),
        ("sys-d9", "PRs conflicting at finish"),
    ]
    below = {o.pattern.name: o.runs for o in outcomes if o.action == "below threshold"}
    assert below == {
        "times on the board that cannot be true": [RUNS[3]],
        "workers or their helpers idle 30 minutes or more": [RUNS[2]],
    }
    new = {c.id: c for c in card_list(board) if c.title.startswith("improve: ")}
    assert sorted(new) == ["sys-d8", "sys-d9"]
    for card in new.values():
        assert card.stack.value == "backlog"
        assert card.project == "system"
        assert card.history[0].run_id == "improve"
        assert str(review) in card.goal

    edits = new["sys-d8"]
    assert edits.title == "improve: hand edits of cards"
    section = "1. **Write to the board only through `autonomouse` commands**"
    assert f'in docs/rules.md, section "{section}"' in edits.goal
    assert f"{RUNS[0]}, {RUNS[1]}." in edits.goal
    assert RUNS[2] not in edits.goal
    assert f"| {RUNS[0]} | sys-x1 | 0a1b2c3d |" in edits.goal  # the review's row
    assert "| run | card | commit | at | writes |" in edits.goal
    assert f'docs/rules.md, section "{section}"' in edits.test

    conflicts = new["sys-d9"]
    assert 'in docs/orchestrator.md, section "## A wake"' in conflicts.goal
    assert ", ".join(RUNS) in conflicts.goal
    assert "shows in 5 runs" in conflicts.goal
    assert "https://github.com/o/r/pull/5" in conflicts.goal
    assert "sys-y" not in conflicts.goal  # the merged siblings are not evidence

    again = improve.improve(board, review, min_runs=2)

    assert [c.id for c in card_list(board) if c.title.startswith("improve: ")] == [
        "sys-d8",
        "sys-d9",
    ]
    assert [(o.action, o.card) for o in again if o.action != "below threshold"] == [
        ("open already", "sys-d8"),
        ("open already", "sys-d9"),
    ]


def test_the_cards_go_to_the_project_improve_project_names(board, config_dir, tmp_path):
    project_new(
        board,
        slug="tool",
        name="Tool",
        goal="g",
        repo="https://github.com/o/tool",
        repo_path=str(tmp_path / "tool"),
        default_branch="main",
        priority=2,
    )
    card_new(board, card_id="tl-a1", project="tool", title="t", goal="g", test="t")
    (config_dir / "config.toml").write_text('[improve]\nproject = "tool"\n')
    review = _write_review(board, _rehearsal_logs())

    outcomes = improve.improve(board, review, min_runs=2)

    assert [o.card for o in outcomes if o.action == "created"] == ["tl-d1", "tl-d2"]
    new = [c for c in card_list(board) if c.title.startswith("improve: ")]
    assert {(c.id, c.project, c.stack.value) for c in new} == {
        ("tl-d1", "tool", "backlog"),
        ("tl-d2", "tool", "backlog"),
    }
    again = improve.improve(board, review, min_runs=2)
    assert [(o.action, o.card) for o in again if o.action != "below threshold"] == [
        ("open already", "tl-d1"),
        ("open already", "tl-d2"),
    ]


def test_a_closed_card_does_not_stop_a_new_proposal(board):
    review = _write_review(board, _rehearsal_logs())
    improve.improve(board, review, min_runs=2)
    card_cancel(board, "sys-d8", reason="not this way")

    outcomes = improve.improve(board, review, min_runs=2)

    assert [(o.action, o.card) for o in outcomes if o.action != "below threshold"] == [
        ("created", "sys-d10"),
        ("open already", "sys-d9"),
    ]


def test_the_threshold_decides_which_patterns_recur(board):
    review = _write_review(board, _rehearsal_logs())

    outcomes = improve.improve(board, review, min_runs=1)
    assert sorted(o.pattern.name for o in outcomes if o.action == "created") == [
        "PRs conflicting at finish",
        "hand edits of cards",
        "times on the board that cannot be true",
        "workers or their helpers idle 30 minutes or more",
    ]

    fresh = _write_review(board, _rehearsal_logs())
    for card in card_list(board):
        if card.title.startswith("improve: "):
            card_cancel(board, card.id, reason="reset")
    assert [o.action for o in improve.improve(board, fresh, min_runs=6)] == [
        "below threshold"
    ] * 4

    with pytest.raises(ValueError, match="min_runs must be 1 or more"):
        improve.improve(board, fresh, min_runs=0)


def test_every_proposal_names_a_section_its_doc_has():
    for pattern in improve.PATTERNS:
        text = (REPO / pattern.doc).read_text()
        lines = text.splitlines()
        assert any(line.startswith(pattern.section) for line in lines), pattern.section


def test_no_proposed_sentence_names_a_branch():
    """The sentences go into charters every project's agents read, and a project's
    default branch is not always `main`."""
    for pattern in improve.PATTERNS:
        assert not re.search(r"\bmain\b", pattern.sentence), pattern.sentence


def test_the_tables_are_read_by_header_with_escaped_pipes():
    text = """## PR state at finish

| run | card | state | PR |
|---|---|---|---|
| r1 | a\\|b | conflicting | u |

Window: 1 PR state.

| state | count | runs and cards |
|---|---|---|
| conflicting | 1 | r1 a |

## Waits

None.
"""
    tables = improve.read_tables(text)
    assert [r.cells for r in tables["## PR state at finish"]] == [
        {"run": "r1", "card": "a|b", "state": "conflicting", "PR": "u"}
    ]
    assert tables["## Waits"] == []


def test_a_review_without_a_section_fails_loudly(tmp_path):
    review = tmp_path / "behaviour-2026-09-23.md"
    review.write_text("# Behaviour review\n\n## Waits\n\nNone.\n")
    with pytest.raises(ValueError, match="has no section '### Hand edits of cards'"):
        improve.improve(tmp_path, review, min_runs=2)


def test_idle_durations_as_the_review_writes_them():
    assert improve._idle_minutes("52m10s") == pytest.approx(52 + 10 / 60)
    assert improve._idle_minutes("1h02m") == 62
    assert improve._idle_minutes("-") == 0


def test_the_command_reads_the_latest_review_and_the_threshold_from_config(
    board, config_dir
):
    config = config_dir
    env = {
        **os.environ,
        "AUTONOMOUSE_HOME": str(board),
        "AUTONOMOUSE_CONFIG": str(config),
    }

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", "review", "improve", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    missing = cli()
    assert missing.returncode == 1
    assert "no behaviour review in" in missing.stderr

    older = board / "reviews" / "behaviour-2026-09-01.md"
    older.parent.mkdir(parents=True)
    older.write_text("# Behaviour review\n")  # no sections: reading it would fail
    review = _write_review(board, _rehearsal_logs())
    (config / "config.toml").write_text("[improve]\nmin_runs = 5\n")

    done = cli()
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert (
        f"below threshold: - improve: hand edits of cards — 2 runs: {RUNS[0]}, "
        f"{RUNS[1]}" in lines
    )
    assert "created: sys-d8 improve: PRs conflicting at finish — 5 runs" in done.stdout
    assert lines[-1] == f"{review}: 4 patterns, 1 card created"

    named = cli("--review", str(older))
    assert named.returncode == 1
    assert "cannot propose instruction changes" in named.stderr


def test_a_board_without_the_configured_project_files_no_card_and_exits_0(
    tmp_path, config_dir
):
    """A fresh install's board has no `system` project yet (`improve.project`'s
    default) until `autonomouse project new` makes one. Even with a pattern that
    recurs in enough runs to otherwise propose a card, the command names the setting
    and this doc, files nothing, and exits 0 rather than failing the morning review."""
    board = tmp_path / "board"
    board.mkdir()
    _write_review(board, _rehearsal_logs())
    env = {
        **os.environ,
        "AUTONOMOUSE_HOME": str(board),
        "AUTONOMOUSE_CONFIG": str(config_dir),
    }

    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "review", "improve"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "improve.project names 'system', not a project on this board — "
        "see docs/config.md"
    )
    assert card_list(board) == []
