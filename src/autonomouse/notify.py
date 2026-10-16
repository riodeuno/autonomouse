"""Send a message to a human.

`autonomouse notify "<text>"` and `card_ask` (`autonomouse.board.commands`) both send
to Telegram, the channel named `notify.channel` in config.toml (and the only one
configured). Telegram reads its credential from the process environment — populated
from the secrets file `~/.config/autonomouse/.env`, per `docs/rules.md` — and is never read from a file by
this module. A channel missing its credential or chat id fails loudly with the exact
fix; the channel rejecting the message fails loudly with its HTTP status and body.

Telegram also needs `notify.telegram_chat_id`, the id of the chat the human's one
message to the bot opened. `setup()` (`autonomouse notify setup`) reads it from the
bot's own `getUpdates` so nobody has to find or type a chat id by hand.

`notify()` wraps `send()` for a caller — `card_ask` — that must never let a failed
send stop a wake: it logs the failure onto the run record instead of raising.

Every message about a card or a planning thread opens with a line holding a two-digit
reference number and the project's name, `07 Widgets` (`send_about()`), and the board's
reference register (`autonomouse.board.references`) records which card or thread, and
which Telegram message, the number belongs to, so the human's reply to it, or a message
starting with `07`, can be routed back: `updates()` and `confirm()` are the two
`getUpdates` calls `autonomouse notify poll` (`autonomouse.inbox`) reads replies with.
`notify(about=...)` sends the same way; given no `about`, it reads one from a text that
starts with a `[<id>]` tag (`tag()`). `send()` returns the sent message's Telegram id and
can send a message as a reply to an earlier one (`reply_to`): `card_ask` stores its
brief's id on the decision, and the follow-up `card_answer` or `card_cancel` sends
replies to it.

Telegram shows a text as it is written unless the send names a parse mode. With `html`,
each send here names HTML, so `<b>` and `<i>` in the text render as bold and italics, and
the text must write `&`, `<` and `>` as `&amp;`, `&lt;` and `&gt;` everywhere else
(`html.escape`); `send_about` then puts the number line in bold. Without it, a text is
sent as plain text, as before.
"""

from __future__ import annotations

import html as html_text
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

from autonomouse.config import load_config

CONFIG_DOC = "docs/config.md"
_TELEGRAM_API = "{base}/bot{token}/{method}"
# The Bot API's address; `AUTONOMOUSE_TELEGRAM_API` replaces it only for a test that stands a
# stub in for Telegram (setup/test-install-questions.sh), since no test may reach the real bot.
TELEGRAM_API_ENV = "AUTONOMOUSE_TELEGRAM_API"
TELEGRAM_API_BASE = "https://api.telegram.org"


class NotifyError(Exception):
    """Raised when a message could not be sent; the message names the fix."""


def _telegram_request(token: str, method: str, payload: dict) -> dict:
    base = os.environ.get(TELEGRAM_API_ENV) or TELEGRAM_API_BASE
    url = _TELEGRAM_API.format(base=base.rstrip("/"), token=token, method=method)
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise NotifyError(f"telegram rejected {method}: HTTP {e.code} {detail}") from e
    except urllib.error.URLError as e:
        raise NotifyError(f"could not reach api.telegram.org: {e.reason}") from e
    if not result.get("ok"):
        raise NotifyError(f"telegram rejected {method}: {result}")
    return result


def _send_telegram(
    text: str,
    environ: Mapping[str, str],
    config: Mapping,
    reply_to: int | None = None,
    html: bool = False,
) -> int | None:
    token = _token(environ, "the telegram channel")
    chat_id = config.get("notify", {}).get("telegram_chat_id", "")
    if not chat_id:
        raise NotifyError(
            "no notify.telegram_chat_id is configured. Message the bot once on "
            "Telegram, then run `autonomouse notify setup` to read the chat id from "
            f"the bot's own updates and write it to config.toml. See {CONFIG_DOC}."
        )
    payload: dict = {"chat_id": chat_id, "text": text}
    if html:
        payload["parse_mode"] = "HTML"
    if reply_to is not None:
        # Still sent when the original message was deleted from the chat.
        payload["reply_parameters"] = {
            "message_id": reply_to,
            "allow_sending_without_reply": True,
        }
    result = _telegram_request(token, "sendMessage", payload)
    return result.get("result", {}).get("message_id")


def tag(about: str, text: str) -> str:
    """`text` marked with what it is about — a card id or a planning thread id — so a
    Telegram reply to it can be routed back to that card or thread. `notify()` numbers
    a text so marked (`send_about()`)."""
    return f"[{about}] {text}"


_TAG_RE = re.compile(r"^\[([a-z0-9-]+)\] ")


def _token(environ: Mapping[str, str], what: str) -> str:
    token = environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise NotifyError(
            f"{what} needs TELEGRAM_BOT_TOKEN in the environment. Add "
            "TELEGRAM_BOT_TOKEN=<the bot's token, from @BotFather> to "
            f"~/.config/autonomouse/.env (mode 600) and rerun. See {CONFIG_DOC}."
        )
    return token


def updates(env: Mapping[str, str] | None = None) -> list[dict]:
    """Every update the bot holds that `confirm()` has not confirmed yet, oldest first.

    Telegram keeps an unconfirmed update for 24 hours; reading does not confirm it.
    """
    environ = env if env is not None else os.environ
    token = _token(environ, "telegram poll")
    result = _telegram_request(
        token, "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}
    )
    return list(result.get("result", []))


def confirm(last_update_id: int, env: Mapping[str, str] | None = None) -> None:
    """Confirm every update up to `last_update_id`, so `updates()` never returns them
    again (Telegram's `getUpdates` with `offset` one past it)."""
    environ = env if env is not None else os.environ
    token = _token(environ, "telegram poll")
    _telegram_request(
        token,
        "getUpdates",
        {"offset": last_update_id + 1, "limit": 1, "timeout": 0},
    )


# channel name -> sender(text, environ, config, reply_to, html) -> the sent message's id;
# raises NotifyError on any failure to deliver, naming the exact fix. `telegram` is the
# only configured channel.
_CHANNELS: dict[
    str, Callable[[str, Mapping[str, str], Mapping, int | None, bool], int | None]
] = {
    "telegram": _send_telegram,
}


def send(
    text: str,
    config: Mapping | None = None,
    env: Mapping[str, str] | None = None,
    reply_to: int | None = None,
    html: bool = False,
) -> int | None:
    """Send `text` to Telegram; return the sent message's id.

    Args:
        text: the message to send.
        config: the effective config; defaults to `load_config()`.
        env: the environment to read credentials from; defaults to `os.environ`.
        reply_to: the id of an earlier message in the chat this one replies to.
        html: `text` is Telegram HTML (module docstring), not plain text.

    Raises:
        NotifyError: a missing credential or chat id, or the channel rejected the
            message — the message names the exact fix.
    """
    cfg = config if config is not None else load_config()
    environ = env if env is not None else os.environ

    return _CHANNELS["telegram"](text, environ, cfg, reply_to, html)


def send_about(
    root: Path,
    about: str,
    text: str,
    config: Mapping | None = None,
    env: Mapping[str, str] | None = None,
    reply_to: int | None = None,
    html: bool = False,
) -> int | None:
    """Send `text` about the card or planning thread `about`; return the message's id.

    Line one of the message is the next reference number and the project's name
    (`07 Widgets`), in bold when `text` is HTML (`html`), then `text`. The number, the
    message's id and `about` go in the
    board's reference register (`autonomouse.board.references`), which `autonomouse
    notify poll` routes the human's reply by. A failed send takes no number.

    Raises:
        NotifyError: as `send()`.
        CommandError: no card or thread `about` is on the board.
        BoardValidationError: the register or the card's project file is malformed.
    """
    from autonomouse.board import references

    project = references.project_of(root, about)
    name = references.project_name(root, project)

    def line_one(n: int) -> str:
        if html:
            return f"<b>{n:02d} {html_text.escape(name, quote=False)}</b>"
        return f"{n:02d} {name}"

    return references.numbered_send(
        root,
        about,
        project,
        lambda n: send(f"{line_one(n)}\n{text}", config, env, reply_to, html),
    )


def notify(
    text: str,
    *,
    root: Path | None = None,
    run_id: str | None = None,
    config: Mapping | None = None,
    env: Mapping[str, str] | None = None,
    reply_to: int | None = None,
    about: str | None = None,
    html: bool = False,
) -> int | None:
    """Send `text`, never raising: a failed send is logged, not propagated.

    Returns the sent message's id, or None when the send failed.

    For a caller like `card_ask` that must not let the human's bot, chat id or
    network trouble stop a wake. When `root` and `run_id` are given and that run
    record exists, the failure is appended to it (`autonomouse.board.runs`); either
    way it also prints to stderr, so a human watching the session sees it too.

    With `root` and `about`, a card or thread id, it sends with `send_about()`, numbered.
    With `root` and no `about`, a text that starts with a `[<id>]` tag (`tag()`) is
    about that id and is numbered the same way, its tag kept. An `about` the board does
    not hold, or a malformed register, is printed and the text goes out unnumbered; a
    register that cannot be written is printed, and the text is not sent again. `html`
    is `send()`'s, for the numbered and the unnumbered send alike.
    """
    from autonomouse.board import references
    from autonomouse.board.commands import CommandError
    from autonomouse.board.schema import BoardValidationError

    if about is None and root is not None:
        found = _TAG_RE.match(text)
        if found and references.is_about(found.group(1)):
            about = found.group(1)
    try:
        if root is not None and about is not None:
            try:
                return send_about(root, about, text, config, env, reply_to, html)
            except (CommandError, BoardValidationError) as e:
                print(f"notify: sent without a reference number: {e}", file=sys.stderr)
            except OSError as e:
                # The register is written after the send, so the message may be out;
                # sending it again would repeat it.
                print(f"notify: the reference register failed: {e}", file=sys.stderr)
                return None
        return send(text, config=config, env=env, reply_to=reply_to, html=html)
    except NotifyError as e:
        print(f"notify: {e}", file=sys.stderr)
        if root is not None and run_id is not None:
            from autonomouse.board import runs as board_runs

            if board_runs.run_path(root, run_id).is_file():
                board_runs.record_notify_failure(root, run_id, text=text, error=str(e))
        return None


def setup(env: Mapping[str, str] | None = None, *, confirm_read: bool = False) -> str:
    """Read the human's chat id from the bot's own updates.

    The human messages the bot once on Telegram; `getUpdates` then carries that
    message, and this returns its chat id as a string for the caller to write to
    `notify.telegram_chat_id` (`autonomouse notify setup` does that).

    With `confirm_read`, it confirms every update it read (`confirm()`) before it
    returns, so `autonomouse notify poll` never routes the message the human sent to
    find the chat into a new planning thread. `autonomouse notify setup` asks for that
    only while no chat id is configured: once one is, the bot's updates can hold
    replies `notify poll` has not routed yet.

    Raises:
        NotifyError: TELEGRAM_BOT_TOKEN is unset, the bot has no messages yet, or
            a request fails.
    """
    environ = env if env is not None else os.environ
    token = _token(environ, "telegram setup")
    read = _telegram_request(token, "getUpdates", {}).get("result", [])
    chat_ids = [
        update["message"]["chat"]["id"]
        for update in read
        if "message" in update and "chat" in update["message"]
    ]
    if not chat_ids:
        raise NotifyError(
            "the bot has no messages yet. Open a chat with it on Telegram and send "
            "it any message, then rerun `autonomouse notify setup`."
        )
    if confirm_read:
        confirm(read[-1]["update_id"], environ)
    return str(chat_ids[-1])
