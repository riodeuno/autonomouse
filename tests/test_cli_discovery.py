"""The card's test (sys-k1): the `autonomouse` command finds its commands by discovery.

A new module in the `autonomouse.cli` package that defines register(subparsers) and
run(args) is a new top-level command, with no edit to any existing file.
"""

import sys

import pytest

from autonomouse import cli

PROBE = '''
calls = []


def register(subparsers) -> None:
    subparsers.add_parser("probe", help="a command only this test adds")


def run(args) -> None:
    calls.append(args.command)
'''


def test_a_new_module_in_the_package_is_a_new_command(tmp_path, monkeypatch):
    (tmp_path / "probe.py").write_text(PROBE)
    monkeypatch.setattr(cli, "__path__", [*cli.__path__, str(tmp_path)])
    monkeypatch.setattr(sys, "argv", ["autonomouse", "probe"])

    try:
        cli.main()
        assert sys.modules["autonomouse.cli.probe"].calls == ["probe"]
    finally:
        sys.modules.pop("autonomouse.cli.probe", None)


def test_the_top_level_help_lists_the_commands_alphabetically(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["autonomouse", "--help"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 0
    listed = capsys.readouterr().out.split("positional arguments:\n  {")[1].split("}")[0]
    names = listed.split(",")
    assert names == sorted(names)
    assert {"project", "card", "run", "plan", "list", "show"} <= set(names)
