"""A card's `waits_for`: the PR URLs it waits on, ready only once every one has merged.

The field, its commands and the wake's check (`release_merged_waits`): the wake reads each
waited PR's state once and drops the merged ones from the list, so the Situation, the pick
and the orchestrator's Monitor read only the board and `depends_on` keeps meaning "done"."""

import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonomouse import situation
from autonomouse.board.commands import (
    CommandError,
    card_edit,
    card_list,
    card_move,
    card_new,
    card_show,
    project_new,
    release_merged_waits,
)
from autonomouse.board.schema import BoardValidationError, Stack, card_from_dict
from autonomouse.board.store import load_card, save_card
from autonomouse.config import DEFAULTS
from autonomouse.jev.questions import base_state

PR_1 = "https://github.com/o/r/pull/1"
PR_2 = "https://github.com/o/r/pull/2"
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def root(tmp_path):
    project_new(
        tmp_path,
        slug="widgets",
        name="Widgets",
        goal="g",
        repo="https://github.com/o/r",
        repo_path="/tmp/r",
        default_branch="main",
        priority=1,
    )
    return tmp_path


def _card(root, card_id, **kwargs):
    card = card_new(
        root, card_id=card_id, project="widgets", title="t", goal="g", test="t", **kwargs
    )
    return card_move(root, card_id, to="todo") if card.stack == Stack.BACKLOG else card


def _ready(root):
    return [c.id for c in situation.ready(card_list(root), "widgets")]


def _states(*, pr1=None, pr2=None):
    """A `pr_state` that answers `pr1` and `pr2` for the two URLs and records each URL it
    is asked."""
    by_url = {PR_1: pr1, PR_2: pr2}
    asked = []

    def pr_state(url):
        asked.append(url)
        return by_url[url]

    pr_state.asked = asked
    return pr_state


# ---------------------------------------------------------------- the field --


def test_a_card_file_written_before_the_field_loads_with_an_empty_list(root):
    _card(root, "wid-a1")
    path = root / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(path.read_text())
    del data["waits_for"]
    path.write_text(json.dumps(data))

    card = load_card(path)

    assert card.waits_for == []
    save_card(path, card)
    assert json.loads(path.read_text())["waits_for"] == []


def test_waits_for_round_trips_and_must_be_a_list_of_strings(root):
    card = _card(root, "wid-a1", waits_for=[PR_1, PR_2, PR_1])
    assert card.waits_for == [PR_1, PR_2]  # once each, in order
    data = load_card(root / "projects" / "widgets" / "cards" / "wid-a1.json").to_dict()
    assert data["waits_for"] == [PR_1, PR_2]
    for bad in ("not a list", [1]):
        with pytest.raises(BoardValidationError, match="'waits_for' must be list of strings"):
            card_from_dict({**data, "waits_for": bad}, source="bad")


def _previous_release_schema(folder):
    """`board/schema.py` as the newest tag that is an ancestor of HEAD shipped it: the
    reader a viewer or a wake started before this change still runs (rule 11)."""
    tags = subprocess.run(
        ["git", "tag", "--merged", "HEAD", "--sort=-v:refname"],
        cwd=REPO, capture_output=True, text=True, check=False,
    ).stdout.split()
    if not tags:
        pytest.skip("no release tag in this checkout")
    source = subprocess.run(
        ["git", "show", f"{tags[0]}:src/autonomouse/board/schema.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    path = folder / "previous_release_schema.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location("previous_release_schema", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses looks its module up here
    spec.loader.exec_module(module)
    return module


def test_the_previous_release_still_loads_a_card_with_waits_for(root, tmp_path_factory):
    card = _card(root, "wid-a1", waits_for=[PR_1])
    data = load_card(root / "projects" / "widgets" / "cards" / "wid-a1.json").to_dict()
    assert data["waits_for"] == [PR_1]

    previous = _previous_release_schema(tmp_path_factory.mktemp("previous"))
    old = previous.card_from_dict(data, source="new file")

    assert old.id == card.id
    assert old.to_dict()["waits_for"] == [PR_1]  # kept as an unknown field, written back


# ----------------------------------------------------------------- commands --


def test_card_new_and_edit_set_the_list_and_edit_clears_it(root):
    _card(root, "wid-a1")
    edited = card_edit(root, "wid-a1", waits_for=[PR_1, PR_2], run_id="run-1")
    assert edited.waits_for == [PR_1, PR_2]
    assert edited.history[-1].note == "edited: waits_for"
    assert card_edit(root, "wid-a1", waits_for=[PR_2]).waits_for == [PR_2]  # replaces

    cleared = card_edit(root, "wid-a1", clear_waits_for=True)

    assert cleared.waits_for == []
    assert "--clear-waits-for" in cleared.history[-1].note


def test_a_value_that_is_not_a_pr_url_is_refused(root):
    _card(root, "wid-a1")
    for bad in ("1", "https://github.com/o/r/issues/1", f"{PR_1},{PR_2}", "after PR 1 merges"):
        with pytest.raises(CommandError, match="not a pull request URL"):
            card_edit(root, "wid-a1", waits_for=[bad])
        with pytest.raises(CommandError, match="not a pull request URL"):
            card_new(
                root, card_id="wid-b1", project="widgets", title="t", goal="g", test="t",
                waits_for=[bad],
            )
    assert card_show(root, "wid-a1").waits_for == []


def test_waits_for_and_clear_waits_for_together_are_refused(root):
    _card(root, "wid-a1")
    with pytest.raises(CommandError, match="cannot both be given"):
        card_edit(root, "wid-a1", waits_for=[PR_1], clear_waits_for=True)


# --------------------------------------------------------------- the ready rule --


def test_a_card_that_waits_is_not_ready_and_is_once_its_list_is_empty(root):
    _card(root, "wid-a1", waits_for=[PR_1])
    _card(root, "wid-a2")
    assert _ready(root) == ["wid-a2"]
    card_edit(root, "wid-a1", clear_waits_for=True)
    assert _ready(root) == ["wid-a1", "wid-a2"]


def test_depends_on_still_means_done_and_is_independent_of_waits_for(root):
    _card(root, "wid-a1")
    _card(root, "wid-a2", depends_on=["wid-a1"])
    _card(root, "wid-a3", depends_on=["wid-a1"], waits_for=[PR_1])
    path = root / "projects" / "widgets" / "cards" / "wid-a1.json"
    done = load_card(path)
    done.stack = Stack.DONE  # done with its PR open, as a stacked card's dependency is
    save_card(path, done)

    assert _ready(root) == ["wid-a2"]  # wid-a3 waits for PR_1, whatever wid-a1's state


# ---------------------------------------------------------- release_merged_waits --


def test_a_merged_pr_is_dropped_and_an_open_one_keeps_the_card_waiting(root):
    _card(root, "wid-a1", waits_for=[PR_1])
    _card(root, "wid-a2", waits_for=[PR_2])
    _card(root, "wid-a3", waits_for=[PR_1, PR_2])
    lines = []

    released = release_merged_waits(
        root, pr_state=_states(pr1="MERGED", pr2="OPEN"), out=lines.append
    )

    assert released == ["wid-a1", "wid-a3"]
    assert card_show(root, "wid-a1").waits_for == []
    assert card_show(root, "wid-a2").waits_for == [PR_2]
    assert card_show(root, "wid-a3").waits_for == [PR_2]
    row = card_show(root, "wid-a3").history[-1]
    assert row.note == f"waits_for: {PR_1} merged; still waiting for {PR_2}"
    assert (row.from_, row.to) == ("todo", "todo")
    assert lines == [f"waits_for=wid-a1: {PR_1} merged", f"waits_for=wid-a3: {PR_1} merged"]
    assert _ready(root) == ["wid-a1"]


def test_each_pr_is_read_once_and_a_failed_read_keeps_the_card_waiting(root):
    _card(root, "wid-a1", waits_for=[PR_1])
    _card(root, "wid-a2", waits_for=[PR_1, PR_2])
    pr_state = _states(pr1=None, pr2=None)  # `gh` failed, as with no network

    assert release_merged_waits(root, pr_state=pr_state) == []

    assert pr_state.asked == [PR_1, PR_2]
    assert card_show(root, "wid-a2").waits_for == [PR_1, PR_2]
    assert len(card_show(root, "wid-a2").history) == 2  # created, moved to todo


def test_a_pr_closed_unmerged_never_releases_the_card_and_is_named(root):
    _card(root, "wid-a1", waits_for=[PR_1])
    lines = []

    assert release_merged_waits(root, pr_state=_states(pr1="CLOSED"), out=lines.append) == []

    assert card_show(root, "wid-a1").waits_for == [PR_1]
    assert lines == [f"waits_for=wid-a1: {PR_1} is closed, not merged; the card keeps waiting"]


def test_only_todo_cards_of_the_asked_project_are_read(root):
    project_new(
        root, slug="other", name="o", goal="g", repo="https://github.com/o/o",
        repo_path="/tmp/o", default_branch="main", priority=2,
    )
    card_new(
        root, card_id="oth-a1", project="other", title="t", goal="g", test="t",
        waits_for=[PR_2], stack="todo",
    )
    _card(root, "wid-a1", waits_for=[PR_1])
    card_new(root, card_id="wid-b1", project="widgets", title="t", goal="g", test="t",
             waits_for=[PR_1])  # backlog
    pr_state = _states(pr1="MERGED", pr2="MERGED")

    assert release_merged_waits(root, pr_state=pr_state, project="widgets") == ["wid-a1"]

    assert pr_state.asked == [PR_1]  # no read for another project's or a backlog card's PR
    assert card_show(root, "oth-a1").waits_for == [PR_2]
    assert card_show(root, "wid-b1").waits_for == [PR_1]


# ------------------------------------------- one ready rule in every place it lives --


@pytest.mark.skipif(shutil.which("jq") is None, reason="needs jq")
def test_the_monitor_script_the_jev_state_and_the_pick_agree_on_which_cards_are_ready(root):
    """The orchestrator's Monitor script (a `jq` filter in its charter) reads the cards'
    JSON as `situation.ready` and Jev's `ready_cards` do: all three give one answer."""
    _card(root, "wid-a1")  # ready
    _card(root, "wid-a2", waits_for=[PR_1])  # waits
    _card(root, "wid-a4")
    _card(root, "wid-a3", depends_on=["wid-a4"])  # its dependency is done: ready
    _card(root, "wid-a6", waits_for=[PR_1, PR_2])
    _card(root, "wid-a5", depends_on=["wid-a6"])  # its dependency is todo
    _card(root, "wid-a7", depends_on=["wid-a4"], waits_for=[PR_2])  # both rules say wait
    _card(root, "wid-a8")  # a card file from before the field: no key
    cards_dir = root / "projects" / "widgets" / "cards"
    path = cards_dir / "wid-a4.json"
    done = load_card(path)
    done.stack = Stack.DONE
    save_card(path, done)
    old = cards_dir / "wid-a8.json"
    data = json.loads(old.read_text())
    del data["waits_for"]
    old.write_text(json.dumps(data))

    doc = (REPO / "docs" / "orchestrator" / "while-workers-run.md").read_text()
    start = doc.index("jq -rs '[.[] | select(.stack==\"done\")")
    command = doc[start : doc.index("../../*/cards/*.json", start) + len("../../*/cards/*.json")]
    ran = subprocess.run(
        ["bash", "-c", command.replace("<slug>", "widgets")],
        cwd=cards_dir, capture_output=True, text=True, check=True,
    )
    monitor = sorted(line.removesuffix(" ready") for line in ran.stdout.splitlines())

    pick = sorted(c.id for c in situation.ready(card_list(root), "widgets"))
    jev = sorted(
        c["id"]
        for c in base_state(
            now=datetime(2026, 11, 16, tzinfo=UTC),
            config=DEFAULTS,
            cards=[c.to_dict() for c in card_list(root)],
            runs=[],
        )["ready_cards"]
    )
    assert pick == ["wid-a1", "wid-a3", "wid-a8"]
    assert monitor == pick
    assert jev == pick
