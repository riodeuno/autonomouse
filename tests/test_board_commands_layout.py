"""The board commands are one package with a module per record type (sys-k2).

`autonomouse.board.commands` was one module that every new command edited, whatever
record type it served. It is a package now: `projects`, `cards` and `transfer` (the
whole board's export and import), with what they share in `common`. The package
re-exports all four, so every importer keeps `from autonomouse.board.commands import
...` as it was.
"""

import ast
import importlib
import inspect
from types import ModuleType

import pytest

from autonomouse.board import commands

MODULES = ("common", "projects", "cards", "transfer")


def _defined_public_names(module: ModuleType) -> set[str]:
    """The public names a module's own top-level code defines: its functions, classes
    and assignments, leaving out what it imports."""
    names = set()
    for node in ast.parse(inspect.getsource(module)).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if not name.startswith("_")}


def test_commands_is_a_package() -> None:
    assert hasattr(commands, "__path__")


@pytest.mark.parametrize("name", MODULES)
def test_each_module_of_the_package_imports(name: str) -> None:
    module = importlib.import_module(f"autonomouse.board.commands.{name}")
    assert module.__name__ == f"autonomouse.board.commands.{name}"


def test_each_command_lives_in_its_record_types_module() -> None:
    assert commands.card_new.__module__.endswith(".cards")
    assert commands.project_new.__module__.endswith(".projects")
    assert commands.board_export.__module__.endswith(".transfer")
    assert commands.CommandError.__module__.endswith(".common")


@pytest.mark.parametrize("name", MODULES)
def test_the_package_re_exports_every_public_name_each_module_defines(name: str) -> None:
    module = importlib.import_module(f"autonomouse.board.commands.{name}")
    defined = _defined_public_names(module)
    assert defined
    # `is`, not `hasattr`: two modules that define the same name would leave the
    # package holding only one of them.
    missing = sorted(n for n in defined if getattr(commands, n, None) is not getattr(module, n))
    assert missing == []


# `board.runs`, `board.plans` and `inbox` import `_find_card_path` from the package, and
# `inbox` imports `_one_line`.
@pytest.mark.parametrize("name", ["_find_card_path", "_one_line", "_SLUG_RE"])
def test_the_package_re_exports_three_private_names_by_name(name: str) -> None:
    assert hasattr(commands, name)
