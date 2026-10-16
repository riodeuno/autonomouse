"""`autonomouse plan`: planning threads."""

import json
from pathlib import Path

from autonomouse import controller, planner
from autonomouse.board import commands as board_commands
from autonomouse.board import plans as board_plans
from autonomouse.board.brief import parse_brief
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail, _plan_step
from autonomouse.config import config_path, load_config, planner_tiers, secrets_path
from autonomouse.home import locate


def register(subparsers) -> None:
    plan_parser = subparsers.add_parser(
        "plan",
        help="planning threads: a request becomes cards through a talk with a planner",
        description=(
            "A planning thread is plans/<thread-id>.json on the board. The human opens "
            "it and replies; `plan step` runs the planner (claude -p, charter "
            "docs/planner.md) on every thread with an unanswered human message, at the "
            "tier the config's planner table gives the kind of session Jev judges it: "
            "complex, simple or collect-user-intent. The planner answers only through "
            "say, cards and close, and on a new project's brief thread writes the brief "
            "with brief."
        ),
    )
    plan_sub = plan_parser.add_subparsers(dest="plan_command")

    open_parser = plan_sub.add_parser("open", help="open a thread with the human's request")
    open_parser.add_argument("project")
    open_parser.add_argument("text")
    open_parser.add_argument("--json", action="store_true")

    reply_parser = plan_sub.add_parser("reply", help="append the human's message")
    reply_parser.add_argument("thread_id")
    reply_parser.add_argument("text")
    reply_parser.add_argument("--json", action="store_true")

    step_parser = plan_sub.add_parser(
        "step", help="run the planner on every thread with an unanswered human message"
    )
    step_parser.add_argument(
        "--claude", default="claude", help="the claude binary (default: claude on PATH)"
    )

    join_parser = plan_sub.add_parser(
        "join",
        help="join a thread's planner session live in this terminal",
        description=(
            "Runs the thread's planner session interactively (claude --resume) in "
            "autonomouse's folder, so you continue the same conversation. While you "
            "are in it the thread is human_joined and plan step leaves it alone; when "
            "you exit it waits for the planner again, so unattended steps continue."
        ),
    )
    join_parser.add_argument("thread_id")
    join_parser.add_argument(
        "--claude", default="claude", help="the claude binary (default: claude on PATH)"
    )

    new_parser = plan_sub.add_parser(
        "new",
        help="start a planner session live in this terminal, on a new thread",
        description=(
            "Opens a new thread on the project and joins it at once, as plan join "
            "does: you tell the planner your request in the session."
        ),
    )
    new_parser.add_argument("project")
    new_parser.add_argument(
        "--claude", default="claude", help="the claude binary (default: claude on PATH)"
    )

    say_parser = plan_sub.add_parser(
        "say", help="the planner's message to the human (the planner calls this)"
    )
    say_parser.add_argument("thread_id")
    say_parser.add_argument("text")
    say_parser.add_argument("--json", action="store_true")

    cards_parser = plan_sub.add_parser(
        "cards", help="record cards the planner wrote (the planner calls this)"
    )
    cards_parser.add_argument("thread_id")
    cards_parser.add_argument("card_ids", nargs="+", metavar="card_id")
    cards_parser.add_argument("--json", action="store_true")

    brief_parser = plan_sub.add_parser(
        "brief",
        help="write the thread's project's brief from a file (the planner calls this)",
        description=(
            "Writes FILE as projects/<slug>/brief.md on the board, <slug> being the "
            "thread's project, and prints the goal sentence and the intent sentence it "
            "holds. Refuses, and writes nothing, when the thread is closed, or when "
            "FILE lacks one of the seven '## ' headings of a brief "
            "(docs/board-schema/project.md) or its '## Goal' or '## Intent' has no "
            "first paragraph."
        ),
    )
    brief_parser.add_argument("thread_id")
    brief_parser.add_argument("file", type=Path, help="the brief, as markdown")

    close_parser = plan_sub.add_parser(
        "close", help="close the thread, with a closing message (the planner calls this)"
    )
    close_parser.add_argument("thread_id")
    close_parser.add_argument("text", nargs="?")
    close_parser.add_argument("--json", action="store_true")

    show_parser = plan_sub.add_parser("show", help="print a thread")
    show_parser.add_argument("thread_id")
    show_parser.add_argument("--json", action="store_true")

    list_parser = plan_sub.add_parser("list", help="list every thread")
    list_parser.add_argument("--json", action="store_true")

    plan_parser.set_defaults(_parser=plan_parser)


def run(args) -> None:
    root = board_root()
    try:
        if args.plan_command == "open":
            thread = board_plans.thread_open(root, args.project, args.text)
        elif args.plan_command == "reply":
            thread = board_plans.thread_reply(root, args.thread_id, args.text)
        elif args.plan_command == "say":
            thread = board_plans.thread_say(root, args.thread_id, args.text)
        elif args.plan_command == "cards":
            thread = board_plans.thread_add_cards(root, args.thread_id, args.card_ids)
        elif args.plan_command == "close":
            thread = board_plans.thread_close(root, args.thread_id, args.text)
        elif args.plan_command == "brief":
            path = board_plans.thread_brief(root, args.thread_id, args.file.read_text())
            written = parse_brief(path.read_text())
            print(f"wrote {path}\ngoal: {written.goal}\nintent: {written.intent}")
            return
        elif args.plan_command == "show":
            thread = board_plans.load_thread(root, args.thread_id)
        elif args.plan_command == "list":
            threads = board_plans.thread_list(root)
            if args.json:
                print(json.dumps([t.to_dict() for t in threads], indent=2))
            else:
                print(
                    "\n".join(
                        f"{t.id}  {t.project}  {t.status.value}  "
                        f"messages={len(t.messages)}  cards={len(t.cards_written)}"
                        for t in threads
                    )
                    or "no planning threads"
                )
            return
        elif args.plan_command == "step":
            raise SystemExit(_plan_step(root, _planner_config(), claude=args.claude)())
        elif args.plan_command == "join":
            live = _plan_live(_planner_config(), claude=args.claude)
            raise SystemExit(planner.join(root, args.thread_id, **live))
        elif args.plan_command == "new":
            live = _plan_live(_planner_config(), claude=args.claude)
            raise SystemExit(planner.new(root, args.project, **live))
        else:
            args._parser.error(
                "choose a plan command: open, reply, step, join, new, say, cards, "
                "brief, close, show, list"
            )
            return
    except (board_commands.CommandError, BoardValidationError, OSError) as e:
        _fail(str(e))
    _print_thread(thread, as_json=args.json)


def _thread_plain(thread: board_plans.Thread) -> str:
    lines = [
        f"id: {thread.id}",
        f"project: {thread.project}",
        f"opened_at: {thread.opened_at}",
        f"session_id: {thread.session_id}",
        f"status: {thread.status.value}",
        f"cards_written: {', '.join(thread.cards_written) or 'none'}",
        "messages:",
    ]
    lines += [f"  [{m.at}] {m.from_.value}: {m.text}" for m in thread.messages]
    return "\n".join(lines)


def _print_thread(thread: board_plans.Thread, *, as_json: bool) -> None:
    print(json.dumps(thread.to_dict(), indent=2) if as_json else _thread_plain(thread))


def _planner_config() -> dict:
    """The config, or a clean failure naming the key when its `planner` table, which maps
    each kind of planner session to its tier, holds a bad value (`planner_tiers`)."""
    config = load_config()
    try:
        planner_tiers(config)
    except ValueError as e:
        _fail(f"{e} (in {config_path()})")
    return config


def _plan_live(config: dict, *, claude: str) -> dict:
    """What `plan join` and `plan new` pass to `planner.join` / `planner.new`: a live
    session runs at the `complex` tier of the config's `planner` table."""
    model, effort = planner_tiers(config)["complex"]
    return {
        "model": model,
        "effort": effort,
        "claude": claude,
        "home": locate(),
        "secrets": secrets_path(),
        "state_dir": controller.default_state_dir() / "plans",
    }
