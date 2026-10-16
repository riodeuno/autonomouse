"""`autonomouse notify`: Telegram, when it is on (`notify.telegram`)."""

import sys

from autonomouse import controller, human_messages, inbox
from autonomouse.board.commands import CommandError
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.cli.shared import _fail
from autonomouse.config import (
    config_path,
    load_config,
    secrets_path,
    set_config_section,
)
from autonomouse.notify import NotifyError
from autonomouse.notify import send as notify_send
from autonomouse.notify import send_about as notify_send_about
from autonomouse.notify import setup as notify_setup


def register(subparsers) -> None:
    notify_parser = subparsers.add_parser(
        "notify",
        help="send a message to a human on Telegram, or 'setup' the chat id",
        description=(
            "'autonomouse notify \"<text>\"' sends text to Telegram; it fails while "
            "notify.telegram is false (Telegram off). 'autonomouse notify setup' reads "
            "the chat id of the human's one message to the bot from its getUpdates, "
            "writes it to notify.telegram_chat_id in config.toml and turns Telegram on "
            "(notify.telegram = true). 'autonomouse notify poll' reads "
            "the human's new messages to the bot and routes them onto the board: a "
            "reply to a decision, or a message starting with its reference number, "
            "answers it; one to a planning thread's message is appended to that "
            "thread; any other message answers the one open question, or opens a "
            "planning thread when none or several are open (docs/config.md). 'plan "
            "step' runs it first."
        ),
    )
    notify_parser.add_argument(
        "text", help="the message to send, or the literal word 'setup' or 'poll'"
    )
    notify_parser.add_argument(
        "--about",
        metavar="ID",
        help=(
            "the card id or planning thread id the message is about; the message "
            "opens with a two-digit reference number and the project's name, and a "
            "reply to it on Telegram, or a message starting with that number, reaches "
            "that card or thread"
        ),
    )
    notify_parser.add_argument(
        "--bold",
        metavar="TEXT",
        help=(
            "with --about: a part of the message, word for word, usually its question, "
            "that Telegram shows in bold"
        ),
    )


def run(args) -> None:
    """`notify setup`, `notify poll` and `notify "<text>"` each load the secrets file into
    their own environment for the bot's token, so each runs from a plain shell."""
    if args.text == "setup":
        first = not load_config().get("notify", {}).get("telegram_chat_id")
        try:
            with controller._secrets_in_environ(secrets_path()):
                chat_id = notify_setup(confirm_read=first)
        except NotifyError as e:
            print(f"notify setup: {e}", file=sys.stderr)
            raise SystemExit(1) from None
        set_config_section(
            config_path(), "notify", {"telegram": True, "telegram_chat_id": chat_id}
        )
        print(f"chat id {chat_id} saved to notify.telegram_chat_id in {config_path()}.")
        return
    if args.text == "poll":
        root = board_root()
        config = load_config()
        with controller._secrets_in_environ(secrets_path()):
            raise SystemExit(inbox.poll(root, config=config))
    if args.bold and not args.about:
        _fail("notify: --bold needs --about")
    try:
        with controller._secrets_in_environ(secrets_path()):
            if args.about:
                text = human_messages.with_bold(args.text, args.bold)
                notify_send_about(board_root(), args.about, text, html=True)
            else:
                notify_send(args.text)
    except (
        NotifyError,
        CommandError,
        BoardValidationError,
        human_messages.NotInText,
    ) as e:
        print(f"notify: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    print("sent.")
