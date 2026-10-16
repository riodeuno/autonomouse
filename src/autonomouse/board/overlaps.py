"""Which open cards would change the same file as a card being planned: the check behind
`autonomouse card overlaps` (sys-i20).

The planner, and the orchestrator when it files a card, run it before writing a card, so a
file two cards would both change is designed out at planning time (`docs/planner.md`,
"Cards that share a file"): every ready card starts in the same wake, and the second of two
such pull requests to merge conflicts.

What a card would change is its `files` list, the files its PR is expected to change. A card
with an empty list falls back to the files and modules its goal names (:func:`goal_names`).
Two names are the same file when one is the other written shorter (`controller.py` and
`src/autonomouse/controller.py`), or when one is a folder or package that holds the other
(`autonomouse.jev` and `src/autonomouse/jev/wake.py`, `docs/orchestrator/` and
`docs/orchestrator/fire.md`). A name may be a glob, which stands for every path it matches
(`docs/orchestrator/*.md` and `docs/orchestrator/fire.md`, sys-s76): `*`, `?` and `[...]`
match inside one folder, and a `**` folder matches any number of folders. A glob loses its
`.py` as a file does, so `src/autonomouse/*.py` stands for everything in that package.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from fnmatch import fnmatchcase

from autonomouse.board.schema import Card

Name = tuple[str, ...]

# A file named in a goal: a path or bare file name with one of these extensions, e.g.
# `src/autonomouse/jev/questions.py`, `docs/orchestrator.md`, `controller.py`.
_FILE_NAME = re.compile(
    r"(?<![\w./-])((?:[\w-]+/)*[\w.-]*\w\.(?:py|md|sh|toml|json|txt|ya?ml|service|timer))\b"
)
# A module named in a goal: `autonomouse.jev.wake`, `autonomouse.board`.
_MODULE_NAME = re.compile(r"(?<![\w./-])(autonomouse(?:\.[a-z_][a-z0-9_]*)+)")


def path_name(path: str) -> Name:
    """``path`` as its components, a leading ``src`` dropped and a Python file without its
    ``.py``, so ``src/autonomouse/jev/wake.py`` and ``autonomouse.jev.wake`` are one name."""
    parts = [p for p in path.strip().split("/") if p not in ("", ".")]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1].removesuffix(".py")
    return tuple(parts)


def goal_names(goal: str) -> set[Name]:
    """The files and modules ``goal`` names, each as a :func:`path_name`."""
    names = {path_name(path) for path in _FILE_NAME.findall(goal)}
    return names | {tuple(module.split(".")) for module in _MODULE_NAME.findall(goal)}


def card_names(card: Card) -> tuple[set[Name], str]:
    """What ``card`` would change, and where that came from: its ``files`` (``"files"``), or,
    when that list is empty, the names its goal holds (``"goal"``)."""
    if card.files:
        return {path_name(path) for path in card.files}, "files"
    return goal_names(card.goal), "goal"


_ANY = ("**",)


def same_file(a: Name, b: Name) -> bool:
    """One name is the other written shorter, or a folder or package that holds it, where
    either may be a glob. An empty name (a path of only slashes and dots) is no file."""
    if not a or not b:
        return False
    # a holds b, b holds a, a is b written shorter, b is a written shorter
    pairs = ((a + _ANY, b), (a, b + _ANY), (_ANY + a, b), (a, _ANY + b))
    return any(_match(x, y) for x, y in pairs)


def _match(a: Name, b: Name) -> bool:
    """Whether one path fits both ``a`` and ``b``. A ``**`` component stands for any number
    of components; two other components match when they are equal or one, read as a glob,
    matches the other, so two globs that only partly meet, such as ``fire*`` and ``*.md``,
    do not match."""
    if not a or not b:
        return all(part == "**" for part in a + b)
    if a[0] == "**":
        return _match(a[1:], b) or _match(a, b[1:])
    if b[0] == "**":
        return _match(a, b[1:]) or _match(a[1:], b)
    x, y = a[0], b[0]
    return (x == y or fnmatchcase(x, y) or fnmatchcase(y, x)) and _match(a[1:], b[1:])


def overlaps(files: Iterable[str], cards: Iterable[Card]) -> list[tuple[Card, list[str], str]]:
    """Each card in ``cards`` that would change one of ``files``: the card, the entries of
    ``files`` it shares, and where its own names came from (:func:`card_names`). A card that
    shares none is left out; so is every card when ``files`` names nothing."""
    planned = [(path, path_name(path)) for path in dict.fromkeys(files)]
    found = []
    for card in cards:
        names, source = card_names(card)
        shared = [path for path, name in planned if any(same_file(name, n) for n in names)]
        if shared:
            found.append((card, shared, source))
    return found
