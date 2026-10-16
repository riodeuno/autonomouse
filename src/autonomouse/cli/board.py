"""`autonomouse board`: whole-board operations."""

import json
from pathlib import Path

from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.board.store import GitCommitError, commit_board
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    board_parser = subparsers.add_parser("board", help="whole-board operations")
    board_sub = board_parser.add_subparsers(dest="board_command")

    export_parser = board_sub.add_parser(
        "export", help="export every project and every card to one JSON file"
    )
    export_parser.add_argument(
        "--out", help="file to write the export to; default: print to stdout"
    )

    import_parser = board_sub.add_parser(
        "import",
        help=(
            "copy every project and card under DIR/projects/... into the board; "
            "a project or card already on the board is left untouched"
        ),
    )
    import_parser.add_argument("dir")
    import_parser.add_argument("--json", action="store_true")

    commit_parser = board_sub.add_parser(
        "commit",
        help=(
            "stage every change on the board and commit it under the board's commit "
            "lock; prints the sha, or 'nothing to commit'"
        ),
    )
    commit_parser.add_argument("-m", "--message", required=True)

    board_parser.set_defaults(_parser=board_parser)


def run(args) -> None:
    root = board_root()
    try:
        if args.board_command == "export":
            data = board_commands.board_export(root)
            text = json.dumps(data, indent=2)
            if args.out:
                Path(args.out).write_text(text + "\n")
                print(
                    f"wrote {len(data['projects'])} project(s), "
                    f"{len(data['cards'])} card(s) to {args.out}"
                )
            else:
                print(text)
        elif args.board_command == "import":
            result = board_commands.board_import(root, Path(args.dir))
            if args.json:
                print(json.dumps(result.to_dict(), indent=2))
            else:
                print(_import_plain(result))
        elif args.board_command == "commit":
            sha = commit_board(root, args.message)
            print(sha if sha is not None else "nothing to commit")
        else:
            args._parser.error("choose a board command: export, import, commit")
    except (
        board_commands.CommandError,
        BoardValidationError,
        GitCommitError,
        OSError,
    ) as e:
        _fail(str(e))


def _import_plain(result: board_commands.ImportResult) -> str:
    def _line(label: str, ids: list[str]) -> str:
        names = ", ".join(ids) if ids else "(none)"
        return f"{label}: {len(ids)} ({names})"

    return "\n".join(
        [
            _line("projects created", result.projects_created),
            _line("projects already present", result.projects_skipped),
            _line("cards created", result.cards_created),
            _line("cards already present", result.cards_skipped),
        ]
    )
