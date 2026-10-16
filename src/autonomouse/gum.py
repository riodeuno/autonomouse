"""How `autonomouse project new` asks its questions on a terminal: with gum
(https://github.com/charmbracelet/gum), as `setup/install.sh` asks its own (sys-w101).

A line of text is `gum input`, with a default shown in brackets after the question and as
the field's placeholder, so typing replaces it and Enter keeps it (`init.answer`); a secret
is `gum input --password`; a question with fixed answers (`init.Choice`) is `gum choose`,
its default under the cursor. gum draws on the
terminal and prints the answer on stdout, which is all that is read here.

The CLI uses these only when stdin is a terminal. With no terminal (a pipe, a file, stdin
ended), `project new` asks on stdin with `init.ask_stdin` and `getpass`, so a script or an
agent runs it with flags and piped answers and never needs gum.

Esc or ctrl-c on a question stops the wizard, as they stop the installer. A gum that
cannot run stops the wizard too,
since install.sh always installs one. Each raises `InitError` naming the question and the fix.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap

from autonomouse.init import Choice, InitError

# What gum exits with when the question is left: esc, then ctrl-c.
ESC = 1
CTRL_C = 130


def ask(question: str, default: str | None, *, choice: Choice | None = None) -> str:
    """`init.Ask` on a terminal: the question with gum, the answer as the stdin one would be
    (a choice's answer, not its words; "" for Enter on a blank field)."""
    if choice is None:
        shown = f" [{default}]" if default else ""
        placeholder = ["--placeholder", default] if default else []
        return _gum(
            question, "input", "--header", _fold(question + shown), "--width", "0",
            "--char-limit", "0", *placeholder,
        )  # fmt: skip
    selected = ["--selected", choice.labels[default]] if default else []
    words = _gum(
        choice.header, "choose", "--header", _fold(choice.header), *selected,
        *choice.labels.values(),
    )  # fmt: skip
    return next(a for a, w in choice.labels.items() if w == words)


def read_secret(prompt: str) -> str:
    """`getpass.getpass` on a terminal: the secret typed into a masked field."""
    header = prompt.rstrip(": ")
    return _gum(
        header, "input", "--password", "--header", _fold(header), "--width", "0",
        "--char-limit", "0",
    )  # fmt: skip


def _fold(text: str) -> str:
    """gum cuts a header off at the terminal's edge; fold it into lines that fit instead."""
    width = shutil.get_terminal_size().columns - 2
    return "\n".join(textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False))


def _gum(question: str, *args: str) -> str:
    try:
        result = subprocess.run(["gum", *args], stdout=subprocess.PIPE, text=True, check=False)
    except OSError as e:
        raise InitError(
            f"gum: cannot ask {question!r} on this terminal: {e}\nfix: run the install "
            "command again, which installs gum into ~/.local/bin, then run this command again"
        ) from e
    if result.returncode != 0:
        raise _stopped(question, result.returncode)
    return result.stdout.removesuffix("\n")


def _stopped(question: str, code: int) -> InitError:
    why = {ESC: "esc, or gum's error above", CTRL_C: "ctrl-c"}.get(code, "gum's error above")
    return InitError(
        f"stopped at {question!r}: gum exited {code} ({why})\nfix: run this command again"
    )
