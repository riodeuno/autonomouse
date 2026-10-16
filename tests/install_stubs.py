"""Helpers for the installer's question tests (sys-w2), shared by
`tests/test_install_questions.py` and `setup/test-install-questions.sh`, which runs this file
as a script inside its container:

- `drive` runs a command on a pseudo-terminal and answers its prompts the way a person at the
  keyboard does: it waits for each prompt's text, then types the answer. The command's
  /dev/tty is that terminal, so a prompt that reads the terminal gets the answer, and one
  that reads stdin does not.
- `serve_telegram` stands in for the Telegram Bot API's `getUpdates`, which `autonomouse
  notify setup` reads the owner's chat id from (`AUTONOMOUSE_TELEGRAM_API` points
  autonomouse at it). It knows one bot token and refuses any other with Telegram's 401; the
  owner's message arrives after a set number of reads, as if the owner were still typing it.

    python install_stubs.py drive STEPS_JSON TRANSCRIPT -- COMMAND...
        STEPS_JSON is a list of [prompt regex, keys to type]; exits with COMMAND's status,
        or 124 when a prompt does not come within 180 seconds (the transcript shows why)
    python install_stubs.py telegram TOKEN EMPTY_READS PORT_FILE LOG
        serves until killed, on 127.0.0.1 at the port it writes to PORT_FILE
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHAT_ID = 424242
# What a terminal answers when a program asks its background colour or its cursor position;
# gum asks both, and without an answer it waits before it draws.
_QUERIES = {b"\x1b]11;?": b"\x1b]11;rgb:0000/0000/0000\x1b\\", b"\x1b[6n": b"\x1b[1;1R"}


class PromptTimeout(Exception):
    """A prompt did not come; the message names it and holds the transcript's tail."""


def drive(
    argv: list[str],
    steps: list[tuple[str, str]],
    *,
    env: dict[str, str] | None = None,
    transcript: Path | None = None,
    timeout: float = 180.0,
) -> tuple[int, str]:
    """Run `argv` on a new pseudo-terminal, 250 columns wide; for each (prompt regex, keys)
    in `steps`, wait until the output since the last answer matches the regex, then type the
    keys. Then read to the end. Returns the exit status and everything the command printed
    (escape codes included), also written to `transcript`. Raises `PromptTimeout` when a
    prompt does not come within `timeout` seconds, or the command ends before it, after
    killing the command.

    The command starts through `setsid -c` (util-linux), which gives it a session of its own
    with the pseudo-terminal as its controlling terminal, its /dev/tty; subprocess starts it
    without a fork of this process, which may run threads (a stub server, pytest-xdist)."""
    fd, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 250, 0, 0))
    proc = subprocess.Popen(
        ["setsid", "-c", *argv],
        stdin=terminal, stdout=terminal, stderr=terminal,
        env={"TERM": "xterm-256color", **(env or os.environ)},
    )  # fmt: skip
    os.close(terminal)
    output = bytearray()
    seen = 0

    def read(wait: float) -> bool:
        ready, _, _ = select.select([fd], [], [], wait)
        if not ready:
            return True
        try:
            data = os.read(fd, 65536)
        except OSError:  # the command exited and closed the terminal
            return False
        if not data:
            return False
        output.extend(data)
        for query, reply in _QUERIES.items():
            if query in data:
                os.write(fd, reply)
        return True

    def save() -> str:
        text = output.decode(errors="replace")
        if transcript is not None:
            transcript.write_text(text)
        return text

    try:
        for prompt, keys in steps:
            end = time.monotonic() + timeout
            while not re.search(prompt, output[seen:].decode(errors="replace")):
                if not read(0.2):
                    raise PromptTimeout(
                        f"the command ended before a prompt matching {prompt!r}; the last "
                        f"output:\n{save()[-2000:]}"
                    )
                if time.monotonic() > end:
                    raise PromptTimeout(
                        f"no prompt matching {prompt!r} within {timeout:.0f}s; the last output:"
                        f"\n{save()[-2000:]}"
                    )
            seen = len(output)
            time.sleep(0.2)  # let the prompt finish drawing before the keys arrive
            os.write(fd, keys.encode())
        end = time.monotonic() + timeout
        while read(0.2):
            if time.monotonic() > end:
                raise PromptTimeout(f"the command did not end within {timeout:.0f}s:\n{save()[-2000:]}")
        return proc.wait(timeout=timeout), save()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(fd)
        save()


def _update(update_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": CHAT_ID, "is_bot": False, "first_name": "Owner"},
            "chat": {"id": CHAT_ID, "first_name": "Owner", "type": "private"},
            "date": 1790000000,
            "text": "hello",
        },
    }


def serve_telegram(token: str, empty_reads: int, log: Path) -> ThreadingHTTPServer:
    """A server on 127.0.0.1 (its `server_address` holds the port) answering
    `POST /bot<token>/getUpdates` as Telegram does: 401 for any other token; a read that
    confirms (an `offset`) gets nothing; the first `empty_reads` plain reads get no update,
    and every later one gets the owner's one message from chat `CHAT_ID`. Each request is a
    line in `log`: `getUpdates <read|confirm> token=<ok|wrong>`. Call `serve_forever`."""
    reads = 0
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            nonlocal reads
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            given, _, method = self.path.removeprefix("/bot").partition("/")
            kind = "confirm" if "offset" in body else "read"
            ok = given == token
            with lock:
                with log.open("a") as f:
                    f.write(f"{method} {kind} token={'ok' if ok else 'wrong'}\n")
                if not ok:
                    self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                    return
                result = []
                if kind == "read":
                    reads += 1
                    if reads > empty_reads:
                        result = [_update(900001)]
            self._send(200, {"ok": True, "result": result})

        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # the log file above is the record
            pass

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def main(args: list[str]) -> int:
    if args[:1] == ["drive"] and "--" in args:
        steps_json, transcript = args[1], args[2]
        command = args[args.index("--") + 1 :]
        steps = [(prompt, keys) for prompt, keys in json.loads(steps_json)]
        try:
            status, _ = drive(command, steps, transcript=Path(transcript))
        except PromptTimeout as e:
            print(e, file=sys.stderr)
            return 124
        return status
    if args[:1] == ["telegram"] and len(args) == 5:
        token, empty_reads, port_file, log = args[1], int(args[2]), Path(args[3]), Path(args[4])
        server = serve_telegram(token, empty_reads, log)
        port_file.write_text(str(server.server_address[1]))
        server.serve_forever()
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
