"""The first-board wizard, `autonomouse init`: turn a fresh install into a board.

In order, each step skipped when its result is already in place, so running it again
changes only what it is told to change:

1. create the board folder (`board_root()`: `$AUTONOMOUSE_HOME`, else
   `~/.local/share/autonomouse/board`);
2. make it its own git repository on branch `main`; when git resolves no author there
   (a fresh machine has no global `user.email`), give the board repository its own
   (`autonomouse`), or every board commit would fail;
3. with `--import`, import a `board export` file by `board_import_export`'s rule
   (whatever is already on the board stays as it is). Projects are not this wizard's:
   on a board with none it prints the command that creates one, `autonomouse project
   new <name>` (`autonomouse.project_wizard`, which reuses `make_folder` and
   `make_repository` so it can run first on a fresh machine);
4. commit the board.

The wizard asks nothing, so an agent runs it with no terminal. The question helpers below
(`Choice`, `Ask`, `answer`, `ask_stdin`) are `autonomouse project new`'s.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autonomouse.board import commands as board_commands
from autonomouse.board.store import commit_board

GIT_NAME = "autonomouse"
GIT_EMAIL = "autonomouse@localhost"
COMMIT_MESSAGE = "autonomouse init"
PROJECT_NEW_HINT = "create one with: autonomouse project new <name>"


class InitError(Exception):
    """The wizard cannot go on; the message names what to do."""


@dataclass(frozen=True)
class Choice:
    """A question with fixed answers: `header` is the question itself, and `labels` maps each
    answer to its words, in the order they are offered."""

    header: str
    labels: dict[str, str]

    def text(self) -> str:
        """The question as stdin asks it, its answers typed: `<header> (1) <label>, or
        (2) <label>`."""
        return f"{self.header} " + ", or ".join(f"({a}) {w}" for a, w in self.labels.items())


class Ask(Protocol):
    """ask(question, default, choice=None) returns the answer line; it raises EOFError when
    stdin ends. `question` is the text to show; for a question with fixed answers `choice`
    holds them too, so a terminal can offer them as a list (`autonomouse.gum`)."""

    def __call__(
        self, question: str, default: str | None, *, choice: Choice | None = None
    ) -> str: ...


Say = Callable[[str], None]


def run_init(root: Path, *, import_file: Path | None, say: Say) -> None:
    """Run the wizard's four steps on the board at `root` (module docstring)."""
    make_folder(root, say)
    make_repository(root, say)
    _fill_board(root, import_file, say)
    sha = commit_board(root, COMMIT_MESSAGE)
    say(f"board commit: {sha if sha is not None else 'nothing to commit'}")


def make_folder(root: Path, say: Say) -> None:
    """Create the board folder at `root`; a folder already there is kept."""
    if root.is_dir():
        say(f"board folder: {root} (already there)")
        return
    root.mkdir(parents=True)
    say(f"board folder: {root} (created)")


def make_repository(root: Path, say: Say) -> None:
    """Make `root` a git repository on branch `main` with an author it can commit as; a
    repository already there is kept."""
    if (root / ".git").exists():
        say("git repository: already there")
    else:
        _git(root, "init", "-q", "-b", "main")
        say("git repository: created, branch main")
    has_author = subprocess.run(
        ["git", "config", "user.email"], cwd=root, capture_output=True, check=False
    ).returncode == 0
    if not has_author:
        _git(root, "config", "user.name", GIT_NAME)
        _git(root, "config", "user.email", GIT_EMAIL)
        say(f"git author: none configured; the board commits as {GIT_NAME} <{GIT_EMAIL}>")


def _fill_board(root: Path, import_file: Path | None, say: Say) -> None:
    if import_file is not None:
        _import(root, import_file, say)
        return
    existing = [p.slug for p in board_commands.project_list(root)]
    if existing:
        say(f"projects: {', '.join(existing)}")
    else:
        say(f"projects: none yet; {PROJECT_NEW_HINT}")


def _import(root: Path, import_file: Path, say: Say) -> None:
    if not import_file.is_file():
        raise InitError(f"no board export file at {import_file}")
    result = board_commands.board_import_export(root, import_file)
    say(
        f"imported {import_file}: {len(result.projects_created)} project(s) and "
        f"{len(result.cards_created)} card(s) created, {len(result.projects_skipped)} "
        f"project(s) and {len(result.cards_skipped)} card(s) already on the board"
    )


def answer(
    ask: Ask,
    question: str | Choice,
    default: str | None = None,
    check: Callable[[str], None] | None = None,
) -> str:
    """Ask until the answer is non-empty (or the default applies) and passes `check`.

    `question` is the question's text, or a `Choice` for one with fixed answers.
    `default` "" allows a blank answer; None requires one. `check` raises `ValueError`
    on an answer to refuse; its message is printed and the question asked again. A
    question stdin cannot answer (it ended) fails the wizard and names the question.
    """
    choice = question if isinstance(question, Choice) else None
    text = choice.text() if choice is not None else question
    while True:
        try:
            reply = ask(text, default, choice=choice).strip()
        except EOFError:
            raise InitError(f"no answer to {text!r}: stdin ended") from None
        if not reply and default is not None:
            reply = default
        if not reply and default is None:
            continue
        if check is not None:
            try:
                check(reply)
            except ValueError as e:
                print(e)
                continue
        return reply


def _git(root: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise InitError(f"git {' '.join(args)} failed in {root}: {result.stderr.strip()}")


def ask_stdin(question: str, default: str | None, *, choice: Choice | None = None) -> str:
    """Print the question (with its default in brackets) and read one line of stdin. A
    choice's answers are already in the question's text, so `choice` changes nothing."""
    shown = f" [{default}]" if default else ""
    return input(f"{question}{shown}: ")
