"""`autonomouse init`: the first-board wizard."""

from pathlib import Path

from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.board.store import GitCommitError
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    init_parser = subparsers.add_parser(
        "init",
        help="first-board wizard: make the board folder and its git repository, and "
        "import a board export",
        description=(
            "Creates the board folder and makes it a git repository, imports a 'board "
            "export' file when --import is given, and commits the board. Running it "
            "again keeps what is already in place. Projects are created with: "
            "autonomouse project new <name>."
        ),
    )
    init_parser.add_argument(
        "--import",
        dest="import_file",
        type=Path,
        metavar="FILE",
        help="a file written by 'autonomouse board export --out FILE' to import",
    )


def run(args) -> None:
    from autonomouse import init

    try:
        init.run_init(board_root(), import_file=args.import_file, say=print)
    except (
        init.InitError,
        board_commands.CommandError,
        BoardValidationError,
        GitCommitError,
        OSError,
    ) as e:
        _fail(str(e))
