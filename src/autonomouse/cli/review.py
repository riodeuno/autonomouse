"""`autonomouse review`: reports from the board and the behaviour logs."""

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from autonomouse.board import commands as board_commands
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail
from autonomouse.config import load_config


def register(subparsers) -> None:
    review_parser = subparsers.add_parser(
        "review", help="write reports from the board and the behaviour logs"
    )
    review_subparsers = review_parser.add_subparsers(dest="review_command")
    daily_parser = review_subparsers.add_parser(
        "daily", help="write reviews/<date>.md for one UTC day (default: yesterday)"
    )
    daily_parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="the day to review, YYYY-MM-DD (default: yesterday, UTC)",
    )
    behaviour_parser = review_subparsers.add_parser(
        "behaviour",
        help=(
            "write reviews/behaviour-<date>.md from the behaviour logs of the runs of "
            "the last DAYS days"
        ),
    )
    behaviour_parser.add_argument(
        "--since",
        type=_days,
        required=True,
        metavar="DAYS",
        help="review the runs that started in the last DAYS days (a whole number, 1 or more)",
    )
    improve_parser = review_subparsers.add_parser(
        "improve",
        help=(
            "propose instruction changes: one backlog card, in the project improve.project "
            "names, per pattern the latest behaviour review shows in improve.min_runs runs "
            "or more"
        ),
    )
    improve_parser.add_argument(
        "--review",
        type=Path,
        metavar="PATH",
        help="the behaviour review to read (default: the latest reviews/behaviour-<date>.md)",
    )
    review_parser.set_defaults(_parser=review_parser)


def run(args) -> None:
    if args.review_command == "daily":
        from autonomouse.review.daily import write_review

        now = datetime.now(UTC)
        day = args.date or (now.date() - timedelta(days=1))
        print(write_review(board_root(), day, now))
        return
    if args.review_command == "behaviour":
        from autonomouse.review.behaviour import write_review as write_behaviour_review

        try:
            print(write_behaviour_review(board_root(), args.since))
        except (ValueError, OSError) as e:
            _fail(f"cannot write the behaviour review: {e}")
        return
    if args.review_command == "improve":
        from autonomouse.review import improve

        root = board_root()
        project = load_config()["improve"]["project"]
        if improve.project_missing(root, project):
            print(
                f"improve.project names {project!r}, not a project on this board — "
                "see docs/config.md"
            )
            return
        try:
            review = args.review or improve.latest_review(root)
            outcomes = improve.improve(
                root, review, load_config()["improve"]["min_runs"]
            )
        except (
            ValueError,
            OSError,
            board_commands.CommandError,
            BoardValidationError,
        ) as e:
            _fail(f"cannot propose instruction changes: {e}")
        print(improve.format_outcomes(outcomes, review))
        return
    args._parser.error("choose a review command: daily, behaviour, improve")


def _days(value: str) -> int:
    """`--since`: a whole number of days, 1 or more."""
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(
            f"expected a whole number of days, 1 or more; got {value!r}"
        )
    return int(value)
