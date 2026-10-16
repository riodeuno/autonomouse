"""`autonomouse log`: a wake's behaviour log."""

import json

from autonomouse.behaviour import log as behaviour_log
from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    log_parser = subparsers.add_parser(
        "log",
        help="a wake's behaviour log: how each agent behaved",
        description=(
            "Each wake's behaviour log is derived from its session transcripts and the "
            "board's card histories when the wake ends, into <run_id>.jsonl and "
            "<run_id>.summary.json in the logs folder beside the board."
        ),
    )
    log_sub = log_parser.add_subparsers(dest="log_command")
    show_parser = log_sub.add_parser("show", help="print a run's behaviour summary")
    show_parser.add_argument("run_id")
    show_parser.add_argument(
        "--json", action="store_true", help="print the summary file as it is"
    )
    derive_parser = log_sub.add_parser(
        "derive",
        help="derive a run's behaviour log again, and write it",
    )
    derive_parser.add_argument("run_id")
    log_parser.set_defaults(_parser=log_parser)


def run(args) -> None:
    root = board_root()
    if args.log_command == "show":
        try:
            summary = behaviour_log.read_summary(root, args.run_id)
        except FileNotFoundError:
            _fail(
                f"no behaviour log for run {args.run_id} in "
                f"{behaviour_log.logs_dir(root)}; `autonomouse log derive "
                f"{args.run_id}` writes it"
            )
        except (OSError, json.JSONDecodeError) as e:
            _fail(f"cannot read the behaviour log of run {args.run_id}: {e}")
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(behaviour_log.format_summary(summary))
    elif args.log_command == "derive":
        try:
            path = behaviour_log.write(root, args.run_id)
        except (board_commands.CommandError, BoardValidationError, OSError) as e:
            _fail(str(e))
        print(path)
    else:
        args._parser.error("choose a log command: show, derive")
