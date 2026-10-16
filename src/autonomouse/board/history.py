"""History rows: the audit trail on a card.

Per `docs/board-schema.md`, a card's `history` list is one entry per write: when it
happened, which run made it, which stack it moved from and to, and why. This module
builds that one entry; `board.store.commit_board` is the matching save point that
checkpoints the write in the board's own git repository.

It also owns the note of a `card progress` row (`progress_note`, `read_progress_note`):
the fields the row set, the text of the last step it wrote and the text of the next step
it wrote, so the card keeps every note its workers wrote (`board.stuck` reads the next
steps).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from autonomouse.board.schema import Card, HistoryEntry


def now_iso() -> str:
    """Return the current time as ISO-8601 UTC with a trailing `Z`, the board's time format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_history(
    card: Card,
    *,
    run_id: str,
    to: str | None,
    note: str | None,
    from_: str | None = None,
    at: str | None = None,
) -> HistoryEntry:
    """Append one history row to `card.history` and bump `card.updated_at`, in place.

    `at` defaults to now. Returns the entry appended, so a caller can fold it into a
    commit message.
    """
    at = at or now_iso()
    entry = HistoryEntry(at=at, run_id=run_id, from_=from_, to=to, note=note)
    card.history.append(entry)
    card.updated_at = at
    return entry


PROGRESS = "progress: "
_NEXT_STEP = "next_step: "
_LAST_STEP = "last_step="
_ITEM = re.compile(r"^(phase|iteration|pr)=(.*)$")
_JSON = json.JSONDecoder()


@dataclass(frozen=True)
class ProgressNote:
    """What one `card progress` row's note says the row set."""

    phase: str | None = None
    iteration: int | None = None
    pr: str | None = None
    # The last step's text; None when the row set none, or was written before rows
    # carried the text (its note holds a bare `last_step`).
    last_step: str | None = None
    sets_next_step: bool = False
    # The next step's text; None when the row set none, or was written before rows
    # carried the text (its note ends in a bare `next_step`).
    next_step: str | None = None


def progress_note(
    changes: list[str], next_step: str | None, last_step: str | None = None
) -> str:
    """The note of a `card progress` row: `progress: ` and the fields the row set,
    comma-separated (`phase=ci`, `iteration=1`, `last_step`, `pr=<url>`,
    `attach=<path>`). The `last_step` item carries the last step's text as a JSON
    string, `last_step="ran pytest, 3 failed"`, so a comma or a `next_step: ` inside
    the text is read as text. When the row set `next_step`, its text comes last, after
    `next_step: `, and runs to the end of the note:
    `progress: phase=testing, last_step="wrote it", next_step: run pytest, then open the PR`.
    """
    items = [
        _LAST_STEP + json.dumps(last_step, ensure_ascii=False)
        if item == "last_step" and last_step is not None
        else item
        for item in changes
    ]
    if next_step is not None:
        items.append(_NEXT_STEP + next_step)
    return PROGRESS + ", ".join(items)


def read_progress_note(note: str | None) -> ProgressNote | None:
    """Read back what `progress_note` wrote; None for a row `card progress` did not write.

    Reads the note item by item, left to right, so the text of a `last_step="…"` item
    is never taken for a field or for the next step. Reads rows written before rows
    carried the texts too: a bare `last_step`, and a bare `next_step` at the end.
    """
    if not note or not note.startswith(PROGRESS):
        return None
    body, at = note, len(PROGRESS)
    items: list[str] = []
    last_step = next_step = None
    while at < len(body):
        if body.startswith(_NEXT_STEP, at):
            next_step = body[at + len(_NEXT_STEP) :]
            break
        if body.startswith(_LAST_STEP + '"', at):
            last_step, at = _JSON.raw_decode(body, at + len(_LAST_STEP))
            items.append("last_step")
        else:
            end = body.find(", ", at)
            end = len(body) if end < 0 else end
            items.append(body[at:end])
            at = end
        if body.startswith(", ", at):
            at += 2
    fields = dict(m.groups() for item in items if (m := _ITEM.match(item)))
    return ProgressNote(
        phase=fields.get("phase"),
        iteration=int(fields["iteration"]) if "iteration" in fields else None,
        pr=fields.get("pr"),
        last_step=last_step,
        sets_next_step=next_step is not None or "next_step" in items,
        next_step=next_step,
    )
