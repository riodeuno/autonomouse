"""`autonomouse list` (`autonomouse.overview`)."""

from autonomouse import overview
from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    subparsers.add_parser(
        "list",
        help="print one line per project: its count per stack and its claim",
        description=(
            "Prints one line per project: slug, name, the count of cards per stack "
            "and the claim (the holding run and its liveness, or free). Plain text, "
            "so it works over ssh with no terminal screen. Reads the board only."
        ),
    )


def run(args) -> None:
    root = board_root()
    try:
        print(overview.format_list(overview.project_summaries(root)))
    except (board_commands.CommandError, BoardValidationError) as e:
        _fail(str(e))
