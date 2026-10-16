"""Planning threads: `plans/<thread-id>.json` (shape in `docs/board-schema.md`).

A thread is a conversation between the human and a planner session that ends in cards,
or, on a new project's brief thread, in the project's brief. The human writes to it with
`thread_open` and `thread_reply`; the planner writes to it only through `thread_say`,
`thread_add_cards` and `thread_close`, and writes the thread's project's brief through
`thread_brief` (each an `autonomouse plan` command). `autonomouse plan step`
(`autonomouse.planner`) finds the threads that have an unanswered human message and runs
the planner on them, recording the session id here with `thread_set_session`.
`autonomouse plan join` and `plan new` put the human in the planner's session live,
holding the thread with `thread_join` (see "live join" below).

Every write to a thread holds an exclusive `flock` on the `plans/` folder, so the human's
reply and the planner's message never overwrite each other; `thread_brief` changes no
thread and replaces the brief in one step instead. Nothing here commits the board; the
writes ride in the next board commit.
"""

from __future__ import annotations

import enum
import fcntl
import json
import os
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autonomouse.board.brief import HEADINGS, parse_brief, problems, write_brief
from autonomouse.board.commands import CommandError, _find_card_path, project_show
from autonomouse.board.history import now_iso
from autonomouse.board.runs import Liveness, liveness
from autonomouse.board.schema import BoardValidationError, _check_keys, _unknown
from autonomouse.board.store import _atomic_write_json

_THREAD_ID_RE = re.compile(r"^plan-\d{8}-\d{6}-[0-9a-f]{4}$")
_KEYS = {
    "id",
    "project",
    "opened_at",
    "session_id",
    "status",
    "joined",
    "messages_given",
    "messages",
    "cards_written",
    "tiers",
}
_JOINED_KEYS = {"pid", "at"}
_MESSAGE_KEYS = {"at", "from", "text"}


class ThreadStatus(str, enum.Enum):
    """Where a thread stands.

    `open`: the human's request, no planner has answered yet. `waiting_planner`: the
    human replied and the planner has not answered. `waiting_human`: the planner's last
    message waits for the human. `human_joined`: the human is in the planner's session
    live (`plan step` leaves it alone). `closed`: the planner finished it.
    """

    OPEN = "open"
    WAITING_HUMAN = "waiting_human"
    WAITING_PLANNER = "waiting_planner"
    HUMAN_JOINED = "human_joined"
    CLOSED = "closed"


class Author(str, enum.Enum):
    HUMAN = "human"
    PLANNER = "planner"


@dataclass
class Message:
    at: str
    from_: Author
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"at": self.at, "from": self.from_.value, "text": self.text}


@dataclass
class Thread:
    id: str
    project: str
    opened_at: str
    session_id: str | None
    status: ThreadStatus
    messages: list[Message] = field(default_factory=list)
    cards_written: list[str] = field(default_factory=list)
    # The human's live join, `{pid, at}`, while `status` is `human_joined`; else None.
    # A claim like a project's: the pid is the interactive claude's, and a join whose
    # pid is gone is released (`release_dead_join`).
    joined: dict[str, Any] | None = None
    # How many of `messages` the last `plan step` session was given (`thread_given`);
    # None on a thread no step has run on since it was written, and after a live join.
    messages_given: int | None = None
    # One entry per planner session `plan step` started or resumed on the thread: Jev's
    # judgment of the session's kind and the tier it ran at (`autonomouse.planner`).
    tiers: list[dict[str, Any]] = field(default_factory=list)
    # Fields this code does not know, kept as they were and written back last, as a
    # board's cards and projects already do (sys-t1): a viewer running old code must
    # not stop on a thread a newer version wrote with a field it does not know.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "opened_at": self.opened_at,
            "session_id": self.session_id,
            "status": self.status.value,
            "joined": None if self.joined is None else dict(self.joined),
            "messages_given": self.messages_given,
            "messages": [m.to_dict() for m in self.messages],
            "cards_written": list(self.cards_written),
            "tiers": [dict(entry) for entry in self.tiers],
            **self.extra,
        }

    def unanswered(self) -> list[Message]:
        """What the planner has not answered yet: every human message from index
        `messages_given` on, which no session was given, even one written while that
        session ran and before its last message; when `messages_given` is None, the human
        messages after the planner's last message. Empty for a closed thread and for one
        the human has joined live."""
        if self.status in (ThreadStatus.CLOSED, ThreadStatus.HUMAN_JOINED):
            return []
        if self.messages_given is not None:
            return [
                m for m in self.messages[self.messages_given :] if m.from_ is Author.HUMAN
            ]
        tail: list[Message] = []
        for message in reversed(self.messages):
            if message.from_ is Author.PLANNER:
                break
            tail.append(message)
        return list(reversed(tail))


def thread_from_dict(data: Any, *, source: str) -> Thread:
    def bad(what: str) -> BoardValidationError:
        return BoardValidationError(f"{source}: {what}")

    if not isinstance(data, dict):
        raise bad("a thread must be a JSON object")
    # a thread written before `joined`, `messages_given` or `tiers` existed loads them as
    # null, null and no tiers
    data = {"joined": None, "messages_given": None, "tiers": [], **data}
    _check_keys(data, _KEYS, kind="thread", source=source)
    extra = _unknown(data, _KEYS)
    for key in ("id", "project", "opened_at"):
        if not isinstance(data[key], str):
            raise bad(f"{key} must be a string")
    if not _THREAD_ID_RE.fullmatch(data["id"]):
        raise bad(f"id must look like plan-YYYYMMDD-HHMMSS-xxxx — got {data['id']!r}")
    if data["session_id"] is not None and not isinstance(data["session_id"], str):
        raise bad("session_id must be a string or null")
    try:
        status = ThreadStatus(data["status"])
    except ValueError:
        raise bad(
            f"status must be one of {', '.join(s.value for s in ThreadStatus)} — "
            f"got {data['status']!r}"
        )
    joined = data["joined"]
    if joined is not None:
        if not isinstance(joined, dict) or set(joined) != _JOINED_KEYS:
            raise bad("joined must be null or an object with keys pid, at")
        if not isinstance(joined["pid"], int) or isinstance(joined["pid"], bool):
            raise bad("joined.pid must be an integer")
        if not isinstance(joined["at"], str):
            raise bad("joined.at must be a string")
    if (joined is not None) != (status is ThreadStatus.HUMAN_JOINED):
        raise bad("joined is set exactly when status is human_joined")
    if not isinstance(data["messages"], list):
        raise bad("messages must be a list")
    given = data["messages_given"]
    if given is not None and (
        not isinstance(given, int)
        or isinstance(given, bool)
        or not 0 <= given <= len(data["messages"])
    ):
        raise bad("messages_given must be null or a count of the thread's messages")
    messages = []
    for i, m in enumerate(data["messages"]):
        if not isinstance(m, dict) or set(m) != _MESSAGE_KEYS:
            raise bad(f"messages[{i}] must be an object with keys at, from, text")
        if not isinstance(m["at"], str) or not isinstance(m["text"], str):
            raise bad(f"messages[{i}]: at and text must be strings")
        try:
            author = Author(m["from"])
        except ValueError:
            raise bad(f"messages[{i}].from must be human or planner — got {m['from']!r}")
        messages.append(Message(at=m["at"], from_=author, text=m["text"]))
    cards = data["cards_written"]
    if not isinstance(cards, list) or not all(isinstance(c, str) for c in cards):
        raise bad("cards_written must be a list of card ids")
    tiers = data["tiers"]
    if not isinstance(tiers, list) or not all(isinstance(t, dict) for t in tiers):
        raise bad("tiers must be a list of objects")
    return Thread(
        id=data["id"],
        project=data["project"],
        opened_at=data["opened_at"],
        session_id=data["session_id"],
        status=status,
        messages=messages,
        cards_written=list(cards),
        joined=None if joined is None else dict(joined),
        messages_given=given,
        tiers=[dict(t) for t in tiers],
        extra=extra,
    )


def plans_dir(root: Path) -> Path:
    return root / "plans"


def thread_path(root: Path, thread_id: str) -> Path:
    return plans_dir(root) / f"{thread_id}.json"


def load_thread(root: Path, thread_id: str) -> Thread:
    path = thread_path(root, thread_id)
    if not path.is_file():
        raise CommandError(f"no planning thread {thread_id!r} on the board")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise BoardValidationError(f"{path}: not valid JSON — {e}") from e
    return thread_from_dict(data, source=str(path))


def save_thread(root: Path, thread: Thread) -> None:
    path = thread_path(root, thread.id)
    data = thread.to_dict()
    thread_from_dict(data, source=str(path))
    _atomic_write_json(path, data)


def thread_list(root: Path) -> list[Thread]:
    folder = plans_dir(root)
    if not folder.is_dir():
        return []
    return [load_thread(root, p.stem) for p in sorted(folder.glob("plan-*.json"))]


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    """Hold an exclusive `flock` on the `plans/` folder for one read-change-write."""
    folder = plans_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _change(root: Path, thread_id: str, change: Callable[[Thread], None]) -> Thread:
    with _locked(root):
        thread = load_thread(root, thread_id)
        change(thread)
        save_thread(root, thread)
        return thread


def _refuse_closed(thread: Thread) -> None:
    if thread.status is ThreadStatus.CLOSED:
        raise CommandError(
            f"planning thread {thread.id} is closed; `autonomouse plan open` starts a new one"
        )


def _require_text(text: str) -> str:
    if not text.strip():
        raise CommandError("the message text is empty")
    return text


def new_thread_id() -> str:
    return f"plan-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def thread_open(root: Path, project: str, text: str) -> Thread:
    """Open a thread on `project` whose first message is the human's request."""
    project_show(root, project)  # refuses a project that does not exist
    _require_text(text)
    at = now_iso()
    with _locked(root):
        thread_id = new_thread_id()
        while thread_path(root, thread_id).exists():
            thread_id = new_thread_id()
        thread = Thread(
            id=thread_id,
            project=project,
            opened_at=at,
            session_id=None,
            status=ThreadStatus.OPEN,
            messages=[Message(at=at, from_=Author.HUMAN, text=text)],
        )
        save_thread(root, thread)
    return thread


def thread_reply(root: Path, thread_id: str, text: str) -> Thread:
    """Append the human's message; the thread waits for the planner."""
    _require_text(text)

    def change(thread: Thread) -> None:
        _refuse_closed(thread)
        thread.messages.append(Message(at=now_iso(), from_=Author.HUMAN, text=text))
        if thread.status is not ThreadStatus.HUMAN_JOINED:
            thread.status = ThreadStatus.WAITING_PLANNER

    return _change(root, thread_id, change)


def thread_say(root: Path, thread_id: str, text: str) -> Thread:
    """Append the planner's message; the thread waits for the human."""
    _require_text(text)

    def change(thread: Thread) -> None:
        _refuse_closed(thread)
        thread.messages.append(Message(at=now_iso(), from_=Author.PLANNER, text=text))
        if thread.status is not ThreadStatus.HUMAN_JOINED:
            thread.status = ThreadStatus.WAITING_HUMAN

    return _change(root, thread_id, change)


def thread_add_cards(root: Path, thread_id: str, card_ids: list[str]) -> Thread:
    """Record cards the planner wrote; each must exist on the board."""
    if not card_ids:
        raise CommandError("name at least one card id")
    for card_id in card_ids:
        _find_card_path(root, card_id, None)  # refuses a card that does not exist

    def change(thread: Thread) -> None:
        _refuse_closed(thread)
        for card_id in card_ids:
            if card_id not in thread.cards_written:
                thread.cards_written.append(card_id)

    return _change(root, thread_id, change)


def thread_close(root: Path, thread_id: str, text: str | None = None) -> Thread:
    """Close the thread, appending the planner's closing message when one is given."""
    if text is not None:
        _require_text(text)

    def change(thread: Thread) -> None:
        _refuse_closed(thread)
        if text is not None:
            thread.messages.append(Message(at=now_iso(), from_=Author.PLANNER, text=text))
        thread.status = ThreadStatus.CLOSED
        thread.joined = None

    return _change(root, thread_id, change)


def thread_brief(root: Path, thread_id: str, text: str) -> Path:
    """Write `text` as the brief of the thread's project, `projects/<slug>/brief.md`, and
    return its path. Refuses a closed thread, and a text in which `brief.problems` finds
    anything (a missing heading, an empty goal or intent sentence), naming each problem;
    a refused brief leaves the file as it was, or absent. The thread itself is unchanged."""
    thread = load_thread(root, thread_id)
    _refuse_closed(thread)
    found = problems(parse_brief(text))
    if found:
        raise CommandError(
            f"brief not written: {'; '.join(found)}. A brief carries these headings, in "
            f"this order: {', '.join(f"'## {h}'" for h in HEADINGS)} "
            '(docs/board-schema/project.md, "brief.md")'
        )
    return write_brief(root, thread.project, text)


def thread_set_session(root: Path, thread_id: str, session_id: str) -> Thread:
    """Record the planner session that holds the thread's conversation now."""

    def change(thread: Thread) -> None:
        thread.session_id = session_id

    return _change(root, thread_id, change)


def thread_given(root: Path, thread_id: str, count: int) -> Thread:
    """Record that the planner session `plan step` starts now is given the thread's first
    `count` messages; a human message from that index on stays unanswered (`unanswered`)."""

    def change(thread: Thread) -> None:
        thread.messages_given = count

    return _change(root, thread_id, change)


def thread_add_tier(root: Path, thread_id: str, entry: dict[str, Any]) -> Thread:
    """Record the tier the planner's next session runs at, and why (`plan step`)."""

    def change(thread: Thread) -> None:
        thread.tiers.append(dict(entry))

    return _change(root, thread_id, change)


# ------------------------------------------------------------------ live join --
#
# `autonomouse plan join` (`autonomouse.planner.join`) puts the human into the planner's
# session live. The join is a claim, like a project's: `joined` holds the pid of the
# interactive claude, and while that pid lives nothing else resumes the session. A join
# whose pid is gone without `thread_leave` having run (the terminal closed, the process
# was killed) is released by `release_dead_join`, which `plan step` and `plan join` run.

LEFT_TEXT = (
    "I left the live session. Write on this thread with `plan say` what we settled "
    "there, then continue as your charter says: the next question, or the cards and "
    "the close."
)
DEAD_JOIN_TEXT = (
    "The live session ended without recording its end (its process is gone). Write on "
    "this thread with `plan say` what was settled there, then continue as your charter "
    "says: the next question, or the cards and the close."
)


def join_alive(thread: Thread) -> bool:
    """Whether `thread`'s live join still holds it: the rule for a project claim
    (`runs.liveness`), where only a crashed claim (pid gone) may be taken over."""
    if thread.joined is None:
        return False
    report = liveness(
        session_id=thread.session_id, pid=thread.joined["pid"], recorded_end=False
    )
    return report.status is not Liveness.CRASHED


def thread_join(root: Path, thread_id: str, pid: int) -> Thread:
    """Mark the thread `human_joined` with `pid` as the join's claim. Refuses a closed
    thread and one another live join holds; takes over a join whose pid is gone."""

    def change(thread: Thread) -> None:
        _refuse_closed(thread)
        if thread.status is ThreadStatus.HUMAN_JOINED and join_alive(thread):
            raise CommandError(
                f"planning thread {thread.id} is already joined live by pid "
                f"{thread.joined['pid']} since {thread.joined['at']}; two sessions "
                f"never resume one conversation at once"
            )
        thread.status = ThreadStatus.HUMAN_JOINED
        thread.joined = {"pid": pid, "at": now_iso()}
        # the live session reads the thread itself; after it, today's rule applies
        thread.messages_given = None

    return _change(root, thread_id, change)


def thread_join_pid(root: Path, thread_id: str, *, old: int, new: int) -> Thread:
    """Move the join's claim from `old` to `new` (the planner process, once started)."""

    def change(thread: Thread) -> None:
        if thread.joined is not None and thread.joined["pid"] == old:
            thread.joined["pid"] = new

    return _change(root, thread_id, change)


def _leave(thread: Thread, text: str) -> None:
    thread.messages.append(Message(at=now_iso(), from_=Author.HUMAN, text=text))
    thread.status = ThreadStatus.WAITING_PLANNER
    thread.joined = None


def thread_leave(root: Path, thread_id: str, pid: int, *, text: str = LEFT_TEXT) -> Thread:
    """End the live join held by `pid`: the thread waits for the planner again, with
    `text`, a message that says the human left, so the next `plan step` resumes the
    session. A thread the planner closed, or whose join `pid` no longer holds, is left as
    it is."""

    def change(thread: Thread) -> None:
        if thread.status is ThreadStatus.HUMAN_JOINED and thread.joined["pid"] == pid:
            _leave(thread, text)

    return _change(root, thread_id, change)


def release_dead_join(root: Path, thread_id: str) -> bool:
    """Release a live join whose pid is gone, the way `thread_leave` would have; True
    when it did."""
    released = False

    def change(thread: Thread) -> None:
        nonlocal released
        if thread.status is ThreadStatus.HUMAN_JOINED and not join_alive(thread):
            _leave(thread, DEAD_JOIN_TEXT)
            released = True

    _change(root, thread_id, change)
    return released
