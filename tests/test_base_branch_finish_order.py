"""`compute_base_branch` orders done dependencies by when each last moved into `done`
from another stack, not by the latest history row that merely stays in `done` — a
worktree removal, or the `merge` row `autonomouse.merge` adds once a card's PR merges
(`docs/orchestrator/conflict-check.md`). A card finished early must not read as the
newest finish just because a later row on it stays in `done`."""

from __future__ import annotations

from autonomouse import github
from autonomouse.board.schema import Assigned, Card, Handoff, HistoryEntry, Size, Stack


def _assigned() -> Assigned:
    return Assigned(model=None, effort=None, agent_type=None, session_id=None, pid=None, picked_up_at=None)


def _handoff() -> Handoff:
    return Handoff(last_step=None, next_step=None, updated_at=None, run_id=None)


def _card(card_id: str, *, branch: str, history: list[HistoryEntry]) -> Card:
    return Card(
        id=card_id,
        project="system",
        title=card_id,
        stack=Stack.DONE,
        position=0,
        size=Size.S,
        goal="g",
        limits=[],
        test="t",
        depends_on=[],
        assigned=_assigned(),
        worktree=None,
        branch=branch,
        base_branch=None,
        pr="https://github.com/riodeuno/autonomouse/pull/1",
        phase=None,
        iteration=0,
        handoff=_handoff(),
        decisions=[],
        history=history,
        created_at="2026-09-27T06:00:00Z",
        updated_at="2026-09-27T12:00:00Z",
    )


def test_a_done_to_done_row_does_not_make_an_early_finish_look_newest():
    # A finished at 10:00, then picked up a done-to-done row (a merge row, or a
    # worktree removal) at 12:00 that stays in `done`.
    a = _card(
        "sys-a1",
        branch="card/sys-a1",
        history=[
            HistoryEntry(at="2026-09-27T10:00:00Z", run_id="r", from_="in_progress", to="done", note=None),
            HistoryEntry(at="2026-09-27T12:00:00Z", run_id="r", from_="done", to="done", note="merge"),
        ],
    )
    # B finished at 11:00 — later than A's real finish, earlier than A's done-to-done row.
    b = _card(
        "sys-b1",
        branch="card/sys-b1",
        history=[
            HistoryEntry(at="2026-09-27T11:00:00Z", run_id="r", from_="in_progress", to="done", note=None),
        ],
    )

    plan = github.compute_base_branch(
        [a, b],
        default_branch="main",
        merged_into=lambda card: None,  # neither dependency's PR has merged
        branch_merged=lambda card, branch: False,
    )

    assert plan.base_branch == "card/sys-b1"
    assert plan.also_merge == ["card/sys-a1"]
