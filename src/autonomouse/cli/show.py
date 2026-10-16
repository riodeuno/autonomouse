"""`autonomouse show <slug>` (`autonomouse.overview`)."""

from autonomouse import overview
from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    show_parser = subparsers.add_parser(
        "show",
        help="print a project's brief and details, then its cards by stack",
        description=(
            "Prints the project's brief (projects/<slug>/brief.md on the board) when "
            "it has one; then its goal, repository, default branch, priority, tracker "
            "link, claim, count per stack, its repository's open pull requests (from gh "
            "pr list) with the card each belongs to, and its last five wakes; then its "
            "cards grouped by stack, each with its tracker issue when it has one. Plain text, so it works over ssh with no terminal "
            "screen. Reads the board only."
        ),
    )
    show_parser.add_argument("slug", help="the project's slug")


def run(args) -> None:
    root = board_root()
    try:
        print(overview.format_show(overview.project_details(root, args.slug)))
    except (board_commands.CommandError, BoardValidationError) as e:
        _fail(str(e))
