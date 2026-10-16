"""A project's brief, `projects/<slug>/brief.md` on the board: the reader returns its
sections, its goal sentence and its intent sentence; `autonomouse show <slug>` prints it
first; the board screen's project details page shows it last, below where the project
stands (sys-w105). A project with no brief reads as None and its page has no brief.

The board is a temporary one (`AUTONOMOUSE_HOME`). `show` runs as a subprocess with a
fake `gh` first on PATH, and the details page reads its pull requests through a fake
`autonomouse.tui.project.gh_open_prs`, so no test calls GitHub.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from textual.widgets import DataTable

from autonomouse.board import commands
from autonomouse.board.brief import HEADINGS, brief_path, load_brief, parse_brief
from autonomouse.tui.app import AutonomouseApp
from autonomouse.tui.icons import PLAIN
from autonomouse.tui.project import ProjectScreen
from autonomouse.tui.reading import ReadingView

PROJECT_GOAL = "Alpha builds the widget factory for the whole team."
WHY = "The team loses a day a week to making widgets by hand."
GOAL_SENTENCE = "Every widget the team needs comes out of the factory in one step."
INTENT_SENTENCE = "The creator wants the team to stop thinking about widgets."
INTENT_MORE = "Nobody on the team should have to learn how the factory works."

BRIEF = f"""\
## Why this project exists

{WHY}

## Goal

{GOAL_SENTENCE}

## Intent

{INTENT_SENTENCE}

{INTENT_MORE}

## Who uses it and where

The widget team, on their laptops.

## How it works

One command reads the order and builds the widget.

### The order file

A plain text file, one widget per line.

## How we work

Cards, one pull request each.

## Out of scope

Selling widgets.
"""


@pytest.fixture
def board(tmp_path, monkeypatch) -> Path:
    """Project alpha, with one todo card; no brief yet."""
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    commands.project_new(
        tmp_path,
        slug="alpha",
        name="Alpha Works",
        goal=PROJECT_GOAL,
        repo="https://github.com/acme/widgets",
        repo_path="/tmp/widgets",
        default_branch="main",
        priority=1,
    )
    commands.card_new(
        tmp_path, card_id="alp-a1", project="alpha", title="t", goal="g", test="t"
    )
    return tmp_path


def _write_brief(root: Path, text: str = BRIEF) -> None:
    brief_path(root, "alpha").write_text(text)


# ------------------------------------------------------------------- reader --


def test_the_reader_returns_the_goal_and_intent_sentences(board):
    _write_brief(board)

    brief = load_brief(board, "alpha")

    assert brief is not None
    assert brief.goal == GOAL_SENTENCE
    assert brief.intent == INTENT_SENTENCE
    assert brief.text == BRIEF


def test_the_reader_returns_the_sections_by_heading_in_order(board):
    _write_brief(board)

    brief = load_brief(board, "alpha")

    assert brief is not None
    assert tuple(brief.sections) == HEADINGS
    assert brief.sections["Why this project exists"] == WHY
    assert brief.sections["Intent"] == f"{INTENT_SENTENCE}\n\n{INTENT_MORE}"
    # a deeper heading stays inside its section
    assert "### The order file" in brief.sections["How it works"]


def test_a_title_line_is_not_a_section_and_a_wrapped_goal_reads_whole():
    brief = parse_brief(
        "# Alpha's brief\n\n## Goal\n\nEvery widget the team needs comes out\n"
        "of the factory in one step.\n\nMore about the goal.\n"
    )

    assert list(brief.sections) == ["Goal"]
    assert brief.goal == GOAL_SENTENCE
    assert brief.intent is None


def test_a_missing_brief_returns_none(board):
    assert not brief_path(board, "alpha").exists()

    assert load_brief(board, "alpha") is None
    assert load_brief(board, "no-such-project") is None


# --------------------------------------------------------- autonomouse show --


def _show(root: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """`autonomouse show alpha` with no terminal, and a `gh` that lists no pull
    requests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\necho '[]'\n")
    gh.chmod(0o755)
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(root),
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    )
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "show", "alpha"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )


def test_show_prints_the_brief_first(board, tmp_path):
    _write_brief(board)

    result = _show(board, tmp_path)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert out.startswith(f"## Why this project exists\n\n{WHY}\n"), out[:200]
    # the whole brief, then the project's details and its cards
    assert out.index("## Out of scope") < out.index("slug: alpha")
    assert out.index(GOAL_SENTENCE) < out.index(f"goal: {PROJECT_GOAL}")
    assert "alp-a1" in out.split("slug: alpha", 1)[1]


def test_show_without_a_brief_prints_the_details_first(board, tmp_path):
    result = _show(board, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("slug: alpha\nname: Alpha Works\n")


# ---------------------------------------------------------- details page --


async def _details_page_text(monkeypatch) -> str:
    """Open alpha's details page with `i` from the project list and return its Markdown
    text once the pull requests are read."""
    monkeypatch.setattr("autonomouse.tui.project.gh_open_prs", lambda _repo: [])
    app = AutonomouseApp()
    async with app.run_test(size=(120, 60)) as pilot:
        table = app.query_one(DataTable)
        table.move_cursor(row=table.get_row_index("alpha"))
        await pilot.press("i")
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, ProjectScreen)
        return app.screen.query_one("#project-details", ReadingView).source


async def test_the_details_page_shows_the_brief_below_the_counts(board, monkeypatch):
    _write_brief(board)

    text = await _details_page_text(monkeypatch)

    assert GOAL_SENTENCE in text
    assert INTENT_SENTENCE in text
    counts = text.index(f"\n## {PLAIN.card} Cards\n")  # the heading of the count per stack
    assert counts < text.index(PROJECT_GOAL) < text.index("\n---\n")  # the brief's rule
    assert text.index("\n---\n") < text.index("## Why this project exists")
    assert text.index("Selling widgets.") > text.index(PROJECT_GOAL)


async def test_the_details_page_without_a_brief_has_no_brief(board, monkeypatch):
    text = await _details_page_text(monkeypatch)

    assert text.startswith(f"# {PLAIN.project} Alpha Works\n\n## {PLAIN.card} Cards\n\n")
    assert f"## {PLAIN.goal} Goal\n\n{PROJECT_GOAL}\n\n" in text
    assert "\n---\n" not in text  # the rule that ends a brief
