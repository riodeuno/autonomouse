"""The reference register: which card or planning thread each Telegram message is about.

Every message autonomouse sends the human about a card or a planning thread opens with a
two-digit reference number, 01 to 99, and the project's name (`autonomouse.notify`,
`send_about`). The human cannot read a board id; they can read `07 Widgets`, and answer
by replying to the message or by starting a message with `07`.

The register is the board file `telegram-references.json`, written only by
`numbered_send` (the `notify` side) and read by `autonomouse notify poll`
(`autonomouse.inbox`). Each entry holds a number, the Telegram id of the message that
carried it, the card or thread id it is about, that card's or thread's project slug and
when it was sent. Numbers go up by one per message and wrap after 99, and a new message
replaces the entry that held its number: the register never holds more than 99 entries.
A number whose message asks a question still open — a card's unanswered decision, or a
planning thread waiting on the human — is skipped, so a reply to that message reaches its
question after any number of later sends. A message whose send failed takes no number.

Every change holds an exclusive `flock` on the board folder, so two senders (a wake's
`card ask` and a planner's `notify --about`) never take one number.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from autonomouse.board.history import now_iso
from autonomouse.board.schema import BoardValidationError, Stack
from autonomouse.board.store import _atomic_write_json, load_card, load_project

REGISTER = "telegram-references.json"
LAST = 99
_KEYS = {"number", "message_id", "about", "project", "sent_at"}

CARD_ID_RE = re.compile(r"^[a-z0-9]+-[a-z][0-9]+$")
THREAD_ID_RE = re.compile(r"^plan-\d{8}-\d{6}-[0-9a-f]{4}$")


def is_about(word: str) -> bool:
    """Whether `word` is a card id or a planning thread id."""
    return bool(CARD_ID_RE.fullmatch(word) or THREAD_ID_RE.fullmatch(word))


@dataclass
class Reference:
    number: int
    message_id: int
    about: str
    project: str
    sent_at: str
    # Keys a later version wrote; kept and written back as they are.
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.number:02d}"

    def to_dict(self) -> dict:
        return {
            **self.extra,
            "number": self.number,
            "message_id": self.message_id,
            "about": self.about,
            "project": self.project,
            "sent_at": self.sent_at,
        }


def register_path(root: Path) -> Path:
    return root / REGISTER


def _from_dict(data: object, where: str) -> Reference:
    if not isinstance(data, dict) or not _KEYS <= data.keys():
        raise BoardValidationError(f"{where}: an entry needs the keys {sorted(_KEYS)}")
    number, message_id = data["number"], data["message_id"]
    if not isinstance(number, int) or not 1 <= number <= LAST:
        raise BoardValidationError(f"{where}: number must be 1 to {LAST}")
    if not isinstance(message_id, int):
        raise BoardValidationError(f"{where}: message_id must be an integer")
    for key in ("about", "project", "sent_at"):
        if not isinstance(data[key], str):
            raise BoardValidationError(f"{where}: {key} must be a string")
    extra = {k: v for k, v in data.items() if k not in _KEYS}
    return Reference(
        number, message_id, data["about"], data["project"], data["sent_at"], extra
    )


def load(root: Path) -> list[Reference]:
    """Every entry, oldest first; none when the register does not exist yet."""
    path = register_path(root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise BoardValidationError(f"{path}: not valid JSON — {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("references"), list):
        raise BoardValidationError(f"{path}: must be an object with a list 'references'")
    return [
        _from_dict(entry, f"{path}: references[{i}]")
        for i, entry in enumerate(data["references"])
    ]


def next_number(entries: list[Reference], held: frozenset[int] = frozenset()) -> int:
    """The number the next message takes: counting up from one past the newest, 01
    after 99, the first whose entry's message is not in `held` (`open_messages`).

    Raises:
        CommandError: every number is on a message in `held`.
    """
    newest = entries[-1].number if entries else 0
    taken = {e.number for e in entries if e.message_id in held}
    for step in range(1, LAST + 1):
        number = (newest + step - 1) % LAST + 1
        if number not in taken:
            return number
    from autonomouse.board.commands import CommandError

    raise CommandError(
        f"every reference number, 01 to {LAST}, is on a message whose question still "
        "waits for the human; answer one to free its number"
    )


def open_messages(root: Path, entries: list[Reference]) -> frozenset[int]:
    """The Telegram ids of the messages that ask a question still open: the brief of a
    card's unanswered decision (`Decision.message_id`), and every message about a
    planning thread that waits on the human, the planner's question among them (a thread
    records no message id).

    A card or thread file that cannot be read is skipped: its question cannot be
    answered through `autonomouse notify poll` either.
    """
    from autonomouse.board import plans
    from autonomouse.board.commands import CommandError

    held: set[int] = set()
    for path in root.glob("projects/*/cards/*.json"):
        try:
            card = load_card(path)
        except BoardValidationError:
            continue
        last = card.decisions[-1] if card.decisions else None
        if (
            card.stack == Stack.DECISION_NEEDED
            and last is not None
            and last.answer is None
            and last.message_id is not None
        ):
            held.add(last.message_id)
    for path in plans.plans_dir(root).glob("plan-*.json"):
        try:
            thread = plans.load_thread(root, path.stem)
        except (BoardValidationError, CommandError):
            continue
        if thread.status is plans.ThreadStatus.WAITING_HUMAN:
            held.update(e.message_id for e in entries if e.about == thread.id)
    return frozenset(held)


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def numbered_send(
    root: Path, about: str, project: str, send: Callable[[int], int | None]
) -> int | None:
    """Take the next number (`next_number`, skipping the numbers of open questions), call
    `send(number)`, and register the message it returns.

    `send` sends the message carrying the number and returns its Telegram id, or None
    when Telegram gave none; either way, or when `send` raises, no entry is written. The
    lock is held across the send, so the number a message carries is the one registered.

    Raises:
        CommandError: every number is on an open question's message (`next_number`);
            nothing is sent.
    """
    with _locked(root):
        entries = load(root)
        number = next_number(entries, open_messages(root, entries))
        message_id = send(number)
        if message_id is None:
            return None
        entries = [e for e in entries if e.number != number]
        entries.append(Reference(number, message_id, about, project, now_iso()))
        _atomic_write_json(
            register_path(root), {"references": [e.to_dict() for e in entries]}
        )
        return message_id


def by_message(entries: list[Reference], message_id: int) -> Reference | None:
    """The entry of the message with this Telegram id."""
    return next((e for e in reversed(entries) if e.message_id == message_id), None)


def by_number(entries: list[Reference], number: int) -> Reference | None:
    return next((e for e in entries if e.number == number), None)


def number_of(entries: list[Reference], about: str) -> Reference | None:
    """The newest entry about this card or thread."""
    return next((e for e in reversed(entries) if e.about == about), None)


def project_of(root: Path, about: str) -> str:
    """The slug of the project the card or thread `about` belongs to.

    Raises:
        CommandError: no card or thread with that id is on the board.
    """
    from autonomouse.board import plans
    from autonomouse.board.commands import _find_card_path

    if THREAD_ID_RE.fullmatch(about):
        return plans.load_thread(root, about).project
    return _find_card_path(root, about, None).parent.parent.name


def project_name(root: Path, slug: str) -> str:
    """The project's name as the human reads it."""
    return load_project(root / "projects" / slug / "project.json").name
