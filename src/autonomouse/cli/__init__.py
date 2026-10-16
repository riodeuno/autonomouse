"""Command-line entry point for autonomouse.

Each top-level command is one module of this package, named as the command: its
register(subparsers) adds the command's parser, and its run(args) runs the command.
main() finds the commands on every call by walking the package's folder: every module
whose name does not start with an underscore is imported, and each one that defines
register() is a command. A module that defines no register(), such as `shared`, adds no
command. Adding a command is adding one module; nothing lists the module names.

Importing a command module binds its name here, so `list` and `config` in this file
are the command modules, not the builtin or `autonomouse.config`.
"""

import argparse
import importlib
import pkgutil
import sys

from autonomouse import __version__, errors_log
from autonomouse.cli import shared
from autonomouse.cli.run import _run_wake as _run_wake


def main() -> None:
    """The `autonomouse` command. An exception no command catches is appended to the
    error log (`autonomouse.errors_log`), then raised on, so the terminal shows it and the
    command exits non-zero as before."""
    try:
        _main()
    except Exception as error:
        errors_log.record(error)
        raise


def _main() -> None:
    parser = argparse.ArgumentParser(prog="autonomouse")
    parser.add_argument(
        "--version",
        action="version",
        version=f"autonomouse {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    commands = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(module, "register"):
            module.register(subparsers)
            commands[info.name] = module

    args = parser.parse_args()
    shared._refusal_target = shared._refusal_run(args)

    if args.command is None:
        from autonomouse.config import load_config, viewer_nerd_font
        from autonomouse.tui.app import AutonomouseApp

        app = AutonomouseApp(nerd_font=viewer_nerd_font(load_config()))
        app.run()
        # 1 after a crash (Textual has printed the traceback), 0 when the human quits
        sys.exit(app.return_code)

    commands[args.command].run(args)
