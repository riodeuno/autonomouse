"""`autonomouse timer`: the crontab lines."""

from autonomouse.cli.shared import _fail
from autonomouse.timer import TimerError
from autonomouse.timer import install as timer_install
from autonomouse.timer import remove as timer_remove
from autonomouse.timer import status as timer_status


def register(subparsers) -> None:
    timer_parser = subparsers.add_parser(
        "timer",
        help="install, inspect or remove the crontab lines (the wake, the review, plan step)",
    )
    timer_sub = timer_parser.add_subparsers(dest="timer_command")
    timer_sub.add_parser(
        "install",
        help=(
            "write three marked lines into your crontab: the wake every wake.interval_hours, "
            "the morning review at 06:00 and plan step every five minutes"
        ),
    )
    timer_sub.add_parser(
        "status", help="the three lines, the next wake and whether a cron daemon runs"
    )
    timer_sub.add_parser("remove", help="delete the three marked lines from your crontab")
    timer_parser.set_defaults(_parser=timer_parser)


def run(args) -> None:
    try:
        if args.timer_command == "install":
            written = timer_install()
            print("installed in the crontab:")
            for line in written:
                print(f"  {line}")
        elif args.timer_command == "status":
            print(timer_status())
        elif args.timer_command == "remove":
            removed = timer_remove()
            if removed:
                print("removed from the crontab:")
                for line in removed:
                    print(f"  {line}")
            else:
                print("nothing installed")
        else:
            args._parser.error("choose a timer command: install, status, remove")
    except TimerError as e:
        _fail(str(e))
