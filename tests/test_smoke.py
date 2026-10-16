"""Smoke test: the package imports and reports the version pyproject.toml declares."""

import tomllib
from pathlib import Path

import autonomouse


def test_import_has_version():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["version"]
    assert autonomouse.__version__ == declared
