"""A board file may carry fields this version of autonomouse does not know (sys-t1).

Processes running different versions of autonomouse read and write the same board: the
human's board viewer, started before a merge, kept the old code in memory and died on
2026-09-26 when a card written by the new code carried `access`. Every loader keeps such a
field as it is and every save writes it back, as run records already do (sys-d31).
"""

import json
from pathlib import Path

import pytest

from autonomouse.board.plans import (
    Author,
    Message,
    Thread,
    ThreadStatus,
    load_thread,
    save_thread,
    thread_path,
)
from autonomouse.board.schema import (
    Access,
    Assigned,
    BoardValidationError,
    Card,
    Claim,
    Decision,
    Handoff,
    HistoryEntry,
    Project,
    Size,
    Stack,
)
from autonomouse.board.store import load_card, load_project, save_card, save_project

NEWER = {"from_a_newer_version": {"kept": [1, "two"]}}


def _project() -> Project:
    return Project(
        slug="system",
        name="System",
        goal="g",
        repo="https://github.com/o/r",
        repo_path="/tmp/r",
        default_branch="main",
        priority=1,
        tracker=None,
        claim=Claim(
            run_id="20260927-000000-abcd",
            session_id="s",
            pid=1,
            model="opus",
            claimed_at="2026-09-27T00:00:00Z",
        ),
        created_at="2026-09-27T00:00:00Z",
        updated_at="2026-09-27T00:00:00Z",
    )


def _card() -> Card:
    return Card(
        id="sys-a1",
        project="system",
        title="t",
        stack=Stack.IN_PROGRESS,
        position=0,
        size=Size.S,
        goal="g",
        limits=[],
        test="t",
        depends_on=[],
        assigned=Assigned(
            model="opus",
            effort="high",
            agent_type="worker-high",
            session_id="s",
            pid=1,
            picked_up_at="2026-09-27T00:00:00Z",
        ),
        worktree=None,
        branch=None,
        base_branch=None,
        pr=None,
        phase=None,
        iteration=0,
        handoff=Handoff(
            last_step=None,
            next_step=None,
            updated_at=None,
            run_id="20260927-000000-abcd",
        ),
        decisions=[
            Decision(
                id="d1",
                question="q",
                options=["a", "other: tell me"],
                recommendation="a",
                asked_at="2026-09-27T00:00:00Z",
                answer=None,
                answered_at=None,
            )
        ],
        history=[
            HistoryEntry(
                at="2026-09-27T00:00:00Z",
                run_id="manual",
                from_=None,
                to="todo",
                note="created",
            )
        ],
        created_at="2026-09-27T00:00:00Z",
        updated_at="2026-09-27T00:00:00Z",
        access=[Access(name="gh", check="gh auth status")],
    )


def _thread() -> Thread:
    return Thread(
        id="plan-20260927-000000-abcd",
        project="system",
        opened_at="2026-09-27T00:00:00Z",
        session_id=None,
        status=ThreadStatus.OPEN,
        messages=[Message(at="2026-09-27T00:00:00Z", from_=Author.HUMAN, text="hello")],
        cards_written=[],
    )


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def _edit_card(path: Path) -> dict:
    """Load the card through the store, change one field as `card progress` does, save it,
    and return the JSON the save wrote."""
    card = load_card(path)
    card.handoff.next_step = "edited"
    save_card(path, card)
    return json.loads(path.read_text())


def test_a_card_file_with_a_field_the_code_does_not_know_loads_and_keeps_it(tmp_path):
    path = tmp_path / "sys-a1.json"
    _write(path, {**_card().to_dict(), **NEWER})

    written = _edit_card(path)

    assert written["handoff"]["next_step"] == "edited"
    assert written["from_a_newer_version"] == NEWER["from_a_newer_version"]


def test_a_project_file_with_a_field_the_code_does_not_know_loads_and_keeps_it(tmp_path):
    path = tmp_path / "project.json"
    _write(path, {**_project().to_dict(), **NEWER})

    project = load_project(path)
    project.priority = 2
    save_project(path, project)
    written = json.loads(path.read_text())

    assert written["priority"] == 2
    assert written["from_a_newer_version"] == NEWER["from_a_newer_version"]


def test_a_decision_entry_with_a_field_the_code_does_not_know_loads_and_keeps_it(tmp_path):
    path = tmp_path / "sys-a1.json"
    data = _card().to_dict()
    data["decisions"][0].update(NEWER)
    _write(path, data)

    card = load_card(path)
    card.decisions[0].answer = "a"
    save_card(path, card)
    written = json.loads(path.read_text())["decisions"][0]

    assert written["answer"] == "a"
    assert written["from_a_newer_version"] == NEWER["from_a_newer_version"]


@pytest.mark.parametrize("where", ["assigned", "handoff"])
def test_a_card_object_with_a_field_the_code_does_not_know_keeps_it(tmp_path, where):
    path = tmp_path / "sys-a1.json"
    data = _card().to_dict()
    data[where].update(NEWER)
    _write(path, data)

    assert _edit_card(path)[where]["from_a_newer_version"] == NEWER["from_a_newer_version"]


@pytest.mark.parametrize("where", ["access", "history"])
def test_a_card_list_entry_with_a_field_the_code_does_not_know_keeps_it(tmp_path, where):
    path = tmp_path / "sys-a1.json"
    data = _card().to_dict()
    data[where][0].update(NEWER)
    _write(path, data)

    entry = _edit_card(path)[where][0]
    assert entry["from_a_newer_version"] == NEWER["from_a_newer_version"]


def test_a_project_claim_with_a_field_the_code_does_not_know_keeps_it(tmp_path):
    path = tmp_path / "project.json"
    data = _project().to_dict()
    data["claim"].update(NEWER)
    _write(path, data)

    project = load_project(path)
    project.priority = 2
    save_project(path, project)

    claim = json.loads(path.read_text())["claim"]
    assert claim["from_a_newer_version"] == NEWER["from_a_newer_version"]


def test_a_card_missing_a_field_is_still_refused_and_named(tmp_path):
    path = tmp_path / "sys-a1.json"
    data = {**_card().to_dict(), **NEWER}
    del data["title"]
    _write(path, data)

    with pytest.raises(BoardValidationError, match=r"missing field\(s\) in card: title$"):
        load_card(path)


def test_a_plan_thread_file_with_a_field_the_code_does_not_know_loads_and_keeps_it(
    tmp_path,
):
    (tmp_path / "plans").mkdir()
    thread_id = _thread().id
    path = thread_path(tmp_path, thread_id)
    _write(path, {**_thread().to_dict(), **NEWER})

    thread = load_thread(tmp_path, thread_id)
    save_thread(tmp_path, thread)
    written = json.loads(path.read_text())

    assert written["from_a_newer_version"] == NEWER["from_a_newer_version"]


def test_a_plan_thread_missing_a_field_is_still_refused_and_named(tmp_path):
    (tmp_path / "plans").mkdir()
    thread_id = _thread().id
    path = thread_path(tmp_path, thread_id)
    data = {**_thread().to_dict(), **NEWER}
    del data["status"]
    _write(path, data)

    with pytest.raises(BoardValidationError, match=r"missing field\(s\) in thread: status$"):
        load_thread(tmp_path, thread_id)
