"""sys-s31: `autonomouse card follow-up <id> --run-id <run id>` runs the wake's PR
follow-up (`autonomouse.followup.follow_up`) for one in_progress card, so a wake that
claimed after waiting, which `autonomouse run` does not follow up, gets the same
follow-up a normal wake gets (`docs/orchestrator.md`, "A wake", step 2).

The command runs in-process with every `gh` call faked at `subprocess.run` inside
`autonomouse.github` (`test_followup.FakeGh`), so it goes through the real follow-up
and the real board writes without the network."""

from __future__ import annotations

from pathlib import Path

import pytest

from autonomouse import cli, github
from autonomouse.board.commands import card_new, card_show, project_new
from autonomouse.board.schema import Phase, Stack
from autonomouse.board.store import load_card, save_card
from test_followup import FAILED_RUN, PASSED_RUN, PR_URL, REPO, FakeGh

RUN_ID = "20260926-073030-a3ad"


@pytest.fixture
def board(tmp_path: Path, monkeypatch) -> Path:
    """A board with project `p` and card `p-a1` in_progress on `card/p-a0`, with an
    open PR, at iteration 1 in the review phase."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    project_new(
        root, slug="p", name="P", goal="g", repo=REPO, repo_path=str(tmp_path),
        default_branch="main", priority=0,
    )
    card_new(root, card_id="p-a1", project="p", title="t", goal="g", test="t")
    _edit(root, stack=Stack.IN_PROGRESS, pr=PR_URL, branch="card/p-a1",
          base_branch="card/p-a0", phase=Phase.REVIEW, iteration=1)  # fmt: skip
    return root


def _path(root: Path) -> Path:
    return root / "projects" / "p" / "cards" / "p-a1.json"


def _edit(root: Path, **fields) -> None:
    card = load_card(_path(root))
    for name, value in fields.items():
        setattr(card, name, value)
    save_card(_path(root), card)


def _follow_up(monkeypatch, capsys, fake: FakeGh) -> tuple[int, str, str]:
    """Run `autonomouse card follow-up p-a1 --run-id <RUN_ID>`; return its exit code,
    stdout and stderr."""
    monkeypatch.setattr(github.subprocess, "run", fake)
    monkeypatch.setattr(
        "sys.argv", ["autonomouse", "card", "follow-up", "p-a1", "--run-id", RUN_ID]
    )
    try:
        cli.main()
        code = 0
    except SystemExit as e:
        code = e.code or 0
    out, err = capsys.readouterr()
    return code, out, err


def test_a_card_whose_pr_base_merged_is_retargeted_to_the_default_branch(
    board, monkeypatch, capsys
):
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, runs=[PASSED_RUN], base_state="MERGED")

    code, out, err = _follow_up(monkeypatch, capsys, fake)

    assert code == 0, err
    assert ["gh", "pr", "edit", "7", "--repo", "o/r", "--base", "main"] in fake.calls
    card = card_show(board, "p-a1")
    assert card.base_branch == "main"
    assert card.history[-1].run_id == RUN_ID
    assert "retargeted: base_branch card/p-a0 -> main" in card.history[-1].note
    assert out.strip() == (
        "follow_up=p-a1: retargeted to main; 0 failed check(s), 0 unanswered comment(s); "
        "nothing to hand back"
    )


def test_a_card_whose_pr_has_failed_runs_is_handed_back_one_iteration_higher(
    board, monkeypatch, capsys
):
    _edit(board, base_branch="main")
    fake = FakeGh(runs=[FAILED_RUN])

    code, out, err = _follow_up(monkeypatch, capsys, fake)

    assert code == 0, err
    card = card_show(board, "p-a1")
    assert card.iteration == 2
    assert card.phase == Phase.WRITING
    assert card.handoff.run_id == RUN_ID
    assert "check 'CI' failure (https://github.com/o/r/actions/runs/1)" in card.handoff.last_step
    assert "fix each failed check" in card.handoff.next_step
    assert out.strip() == (
        "follow_up=p-a1: 1 failed check(s), 0 unanswered comment(s); "
        "handed back for the next iteration"
    )


def test_a_card_in_todo_is_refused_and_nothing_changes(board, monkeypatch, capsys):
    _edit(board, stack=Stack.TODO)
    before = _path(board).read_bytes()
    fake = FakeGh(pr={"baseRefName": "card/p-a0"}, runs=[FAILED_RUN], base_state="MERGED")

    code, out, err = _follow_up(monkeypatch, capsys, fake)

    assert code != 0
    assert "in_progress" in err
    assert out == ""
    assert fake.calls == []
    assert _path(board).read_bytes() == before


def test_an_in_progress_card_with_no_pr_is_refused_and_nothing_changes(
    board, monkeypatch, capsys
):
    _edit(board, pr=None)
    before = _path(board).read_bytes()
    fake = FakeGh(runs=[FAILED_RUN])

    code, out, err = _follow_up(monkeypatch, capsys, fake)

    assert code != 0
    assert "no PR" in err
    assert out == ""
    assert fake.calls == []
    assert _path(board).read_bytes() == before
