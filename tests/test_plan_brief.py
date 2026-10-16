"""`autonomouse plan brief <thread-id> <file>` writes the thread's project's brief,
`projects/<slug>/brief.md` on the board, and refuses a file that lacks one of the brief's
seven headings or whose goal or intent sentence is empty, so the sentences the workers get
are never lost to a mistyped heading (sys-t36).

The board is a temporary one (`AUTONOMOUSE_HOME`), and the command runs as a subprocess,
as the planner runs it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.board import commands
from autonomouse.board.brief import brief_path, load_brief
from autonomouse.board.plans import thread_close, thread_open

GOAL_SENTENCE = "Every widget the team needs comes out of the factory in one step."
INTENT_SENTENCE = "The creator wants the team to stop thinking about widgets."

SECTIONS = {
    "Why this project exists": "The team loses a day a week to widgets.",
    "Goal": GOAL_SENTENCE,
    "Intent": f"{INTENT_SENTENCE}\n\nNobody should have to learn how the factory works.",
    "Who uses it and where": "The widget team, on their laptops.",
    "How it works": "One command reads the order and builds the widget.",
    "How we work": "Cards, one pull request each.",
    "Out of scope": "Selling widgets.",
}


def _brief(sections: dict[str, str]) -> str:
    return "# Widgets\n\n" + "".join(f"## {h}\n\n{body}\n\n" for h, body in sections.items())


VALID = _brief(SECTIONS)


@pytest.fixture
def board(tmp_path) -> Path:
    """Project widgets with an open thread and no brief."""
    root = tmp_path / "board"
    commands.project_new(
        root,
        slug="widgets",
        name="Widgets",
        goal="Widgets builds the factory.",
        repo="https://github.com/acme/widgets",
        repo_path="/tmp/widgets",
        default_branch="main",
        priority=1,
    )
    return root


def _plan_brief(root: Path, thread_id: str, file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "plan", "brief", thread_id, str(file)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "AUTONOMOUSE_HOME": str(root)},
    )


def _file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "draft.md"
    path.write_text(text)
    return path


def test_plan_brief_refuses_a_brief_that_lacks_intent_and_leaves_no_brief_md(board, tmp_path):
    thread = thread_open(board, "widgets", "write the brief")
    draft = {h: body for h, body in SECTIONS.items() if h != "Intent"}

    done = _plan_brief(board, thread.id, _file(tmp_path, _brief(draft)))

    assert done.returncode == 1
    assert "brief not written: it lacks '## Intent'" in done.stderr
    assert not brief_path(board, "widgets").exists()


def test_plan_brief_writes_a_valid_brief_at_the_projects_brief_md(board, tmp_path):
    thread = thread_open(board, "widgets", "write the brief")

    done = _plan_brief(board, thread.id, _file(tmp_path, VALID))

    assert done.returncode == 0, done.stderr
    path = board / "projects" / "widgets" / "brief.md"
    assert path.read_text() == VALID
    assert done.stdout == (
        f"wrote {path}\ngoal: {GOAL_SENTENCE}\nintent: {INTENT_SENTENCE}\n"
    )
    brief = load_brief(board, "widgets")
    assert (brief.goal, brief.intent) == (GOAL_SENTENCE, INTENT_SENTENCE)
    assert [f"## {h}" for h in brief.sections] == [f"## {h}" for h in SECTIONS]


@pytest.mark.parametrize(
    ("sections", "refusal"),
    [
        (
            {("Intnet" if h == "Intent" else h): b for h, b in SECTIONS.items()},
            "it lacks '## Intent'",
        ),
        (
            {h: b for h, b in SECTIONS.items() if h not in ("Goal", "Out of scope")},
            "it lacks '## Goal', '## Out of scope'",
        ),
        ({**SECTIONS, "Goal": ""}, "'## Goal' is empty"),
        ({**SECTIONS, "Intent": ""}, "'## Intent' is empty"),
    ],
    ids=["mistyped-intent", "two-missing", "empty-goal", "empty-intent"],
)
def test_plan_brief_refuses_a_missing_heading_or_an_empty_sentence_and_keeps_the_old_brief(
    board, tmp_path, sections, refusal
):
    thread = thread_open(board, "widgets", "write the brief")
    assert _plan_brief(board, thread.id, _file(tmp_path, VALID)).returncode == 0

    done = _plan_brief(board, thread.id, _file(tmp_path, _brief(sections)))

    assert done.returncode == 1
    assert refusal in done.stderr
    assert brief_path(board, "widgets").read_text() == VALID


def test_plan_brief_refuses_a_closed_thread(board, tmp_path):
    thread = thread_open(board, "widgets", "write the brief")
    thread_close(board, thread.id, "done")

    done = _plan_brief(board, thread.id, _file(tmp_path, VALID))

    assert done.returncode == 1
    assert "is closed" in done.stderr
    assert not brief_path(board, "widgets").exists()
