"""`autonomouse config`: inspect the config file, or set one key in it."""

import sys

from autonomouse.board.paths import board_root
from autonomouse.config import (
    config_path,
    effective_config,
    format_config,
    load_config,
    mask_secrets,
    set_key,
)


def register(subparsers) -> None:
    config_parser = subparsers.add_parser("config", help="inspect the config file, or set a key")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser(
        "show", help="print the effective config, secrets masked"
    )
    set_parser = config_subparsers.add_parser(
        "set",
        help="write one key into config.toml, keeping every other line",
        description=(
            "Writes KEY, <section>.<name> as 'autonomouse config show' prints it, into "
            "config.toml as the type its default has (a whole number, true or false, or "
            "text), and keeps every other key in the file as it is."
        ),
    )
    set_parser.add_argument("key", help="for example browser.pool")
    set_parser.add_argument("value")
    config_parser.set_defaults(_parser=config_parser)


def run(args) -> None:
    if args.config_command == "show":
        print(format_config(mask_secrets(effective_config(load_config(), board_root()))))
        return
    if args.config_command == "set":
        try:
            set_key(config_path(), args.key, args.value)
        except ValueError as e:
            print(f"config set: {e}", file=sys.stderr)
            raise SystemExit(1) from None
        print(f"{args.key} saved in {config_path()}")
        return
    args._parser.error("choose a config command: show, set")
