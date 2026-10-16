"""A project's brief: `projects/<slug>/brief.md` on the board, the project's own page.

The brief is markdown with seven `## ` headings in the order `HEADINGS` names; an
optional `# ` title line may come first, and a deeper heading (`### `) stays inside its
section. The first paragraph under `## Goal` is the goal sentence the human confirmed,
and the first paragraph under `## Intent` is the intent sentence: what the project's
creator wants from it. A blank line ends each of the two; the source may wrap it over
several lines, which markdown shows as one. `docs/board-schema.md` is the format's
reference.

`load_brief` reads it. `autonomouse show <slug>` prints it first and the board screen's
project details page shows it above the counts, both through
`autonomouse.overview.project_details`. `write_brief` writes it for `autonomouse plan
brief` (`autonomouse.board.plans.thread_brief`), which first refuses a text in which
`problems` finds anything, so that a mistyped heading never loses the goal sentence and
the intent sentence the workers get.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

HEADINGS: tuple[str, ...] = (
    "Why this project exists",
    "Goal",
    "Intent",
    "Who uses it and where",
    "How it works",
    "How we work",
    "Out of scope",
)


@dataclass(frozen=True)
class Brief:
    text: str  # the file as written
    sections: dict[str, str]  # each `## ` heading -> the text under it, in file order
    goal: str | None  # the goal sentence; None when `## Goal` is missing or empty
    intent: str | None  # the intent sentence; None when `## Intent` is missing or empty


def brief_path(root: Path, slug: str) -> Path:
    return root / "projects" / slug / "brief.md"


def load_brief(root: Path, slug: str) -> Brief | None:
    """The brief of project `slug` on the board at `root`, or None when the project has
    none."""
    try:
        text = brief_path(root, slug).read_text()
    except FileNotFoundError:
        return None
    return parse_brief(text)


def parse_brief(text: str) -> Brief:
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None  # None before the first `## ` heading
    for line in text.splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current.append(line)
    bodies = {heading: "\n".join(lines).strip() for heading, lines in sections.items()}
    return Brief(
        text=text,
        sections=bodies,
        goal=_first_paragraph(bodies.get("Goal", "")),
        intent=_first_paragraph(bodies.get("Intent", "")),
    )


def _first_paragraph(body: str) -> str | None:
    """The lines of `body` up to its first blank line, joined into one line."""
    paragraph: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            break
        paragraph.append(line.strip())
    return " ".join(paragraph) or None


def problems(brief: Brief) -> list[str]:
    """Each thing that keeps `brief` from being a project's brief, in plain words: the
    headings of `HEADINGS` it lacks, and an empty goal or intent sentence under a heading it
    has. Empty when `brief` is one."""
    found = []
    missing = [heading for heading in HEADINGS if heading not in brief.sections]
    if missing:
        found.append("it lacks " + ", ".join(f"'## {heading}'" for heading in missing))
    for heading, sentence, what in (
        ("Goal", brief.goal, "the goal sentence the human confirmed"),
        ("Intent", brief.intent, "the intent sentence"),
    ):
        if heading in brief.sections and sentence is None:
            found.append(f"'## {heading}' is empty; its first paragraph is {what}")
    return found


def write_brief(root: Path, slug: str, text: str) -> Path:
    """Write `text` as project `slug`'s brief and return its path. The file is replaced in
    one step (a temporary file in the same folder, then `os.replace`), so a reader sees the
    old brief or the new one, never half of one. Checks nothing: `problems` is the
    caller's."""
    path = brief_path(root, slug)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with open(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path
