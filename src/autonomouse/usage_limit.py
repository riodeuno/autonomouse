"""Find Claude Code's usage-limit message in a session's output, and when the limit resets.

A subscription session that runs out of usage ends with a message such as
``You've hit your session limit · resets 3:45pm`` (also ``weekly``, ``Opus`` and ``Sonnet``
limits, and ``resets Mon 12:00am`` for a reset on another day; code.claude.com/docs/en/errors).
Watchdog retries can include usage-limit 429s. Sessions therefore cap retry waiting and
read structured transcript errors as well as the older printed messages.
Older releases printed ``Claude AI usage limit reached|<unix time>``.

The reset time in the new form is a clock time on the machine's local clock (or in the zone
named in brackets after it, e.g. ``resets 3pm (Europe/London)``), with no date: it is the first
such moment at or after the hit. A message whose time cannot be read still counts as a hit,
with ``reset_at`` ``None``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# One line that says a usage limit was hit. "You've used 85% of your session limit" is a
# warning, not a hit, and does not match.
_HIT = re.compile(
    r"^.*(?:you['’]ve hit your [^\n]*?limit|usage limit reached)[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)
_EPOCH = re.compile(r"usage limit reached\|(\d{9,11})", re.IGNORECASE)
_ISO = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))")
_WHEN_AFTER = re.compile(r"\breset(?:s| at|s at)?\s+(?:at\s+)?(.+)$", re.IGNORECASE)

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_CLOCK = re.compile(
    r"^(?:(?P<wd>mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?"
    r"(?:(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(?P<day>\d{1,2}),?\s+"
    r"(?:at\s+)?)?"
    r"(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm)?\b"
    r"\s*(?:\((?P<tz>[^)]+)\))?",
    re.IGNORECASE,
)
# A reset shown as a bare clock time can equal the minute of the hit itself.
_CLOCK_TOLERANCE = timedelta(minutes=1)


@dataclass(frozen=True)
class UsageLimit:
    """A hit: when it was seen, when it resets (``None`` when unreadable), and the line."""

    hit_at: datetime
    reset_at: datetime | None
    text: str

    def to_dict(self) -> dict[str, str | None]:
        """The run record's ``usage_limit`` field (``docs/board-schema.md``)."""
        return {
            "hit_at": _iso(self.hit_at),
            "reset_at": None if self.reset_at is None else _iso(self.reset_at),
            "text": self.text,
        }


def detect(text: str, *, hit_at: datetime, local_tz: tzinfo | None = None) -> UsageLimit | None:
    """The first usage-limit line in ``text``, or ``None`` when there is none.

    ``hit_at`` is timezone-aware: when the session ended with the message. ``local_tz`` is the
    clock a bare reset time is read on; the machine's own zone when ``None``.
    """
    if hit_at.tzinfo is None:
        raise ValueError("detect needs a timezone-aware `hit_at`")
    found = _HIT.search(text)
    if found is None:
        return None
    line = found.group(0).strip()
    zone = local_tz or datetime.now().astimezone().tzinfo
    return UsageLimit(hit_at=hit_at, reset_at=_reset_at(line, hit_at, zone), text=line[:300])


def _reset_at(line: str, hit_at: datetime, zone: tzinfo) -> datetime | None:
    epoch = _EPOCH.search(line)
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), UTC)
    iso = _ISO.search(line)
    if iso:
        return datetime.fromisoformat(iso.group(1))
    after = _WHEN_AFTER.search(line)
    if after is None:
        return None
    clock = _CLOCK.match(after.group(1).strip())
    if clock is None:
        return None
    return _next_clock_time(clock, hit_at, zone)


def _next_clock_time(clock: re.Match[str], hit_at: datetime, zone: tzinfo) -> datetime | None:
    """The first moment at or after ``hit_at`` (less a minute) that the clock text names."""
    hour, minute, ampm = int(clock["h"]), int(clock["m"] or 0), (clock["ap"] or "").lower()
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif clock["m"] is None or hour > 23:
        return None  # a bare number with no minutes and no am/pm is not a time
    if minute > 59:
        return None
    if clock["tz"]:
        try:
            zone = ZoneInfo(clock["tz"].strip())
        except (ZoneInfoNotFoundError, ValueError):
            pass  # an unknown zone name: read the time on the local clock
    local = hit_at.astimezone(zone)
    earliest = hit_at - _CLOCK_TOLERANCE
    weekday = _WEEKDAYS.index(clock["wd"].lower()[:3]) if clock["wd"] else None
    if clock["mon"]:
        month, day = _MONTHS.index(clock["mon"].lower()[:3]) + 1, int(clock["day"])
        for year in (local.year, local.year + 1):
            try:
                at = datetime(year, month, day, hour, minute, tzinfo=zone)
            except ValueError:
                return None
            if at >= earliest:
                return at.astimezone(UTC)
        return None
    for days in range(8):
        date = local.date() + timedelta(days=days)
        if weekday is not None and date.weekday() != weekday:
            continue
        at = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
        if at >= earliest:
            return at.astimezone(UTC)
    return None


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def transcript_limit(path: Path, model: str, *, since: datetime | None = None) -> dict | None:
    """Read the observed Claude Code 429 shape; ignore capacity errors and old turns."""
    found = None
    current_model = model
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            entry = json.loads(line)
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "assistant":
                current_model = entry.get("message", {}).get("model") or current_model
            error = entry.get("error") or {}
            limits = error.get("rateLimits") or {}
            if not (entry.get("type") == "system" and entry.get("subtype") == "api_error"
                    and error.get("status") == 429 and limits.get("rateLimitType")):
                continue
            at = datetime.fromisoformat(entry["timestamp"])
            if at.tzinfo is None or (since is not None and at < since):
                continue
            reset = limits.get("resetsAt")
            reset_at = datetime.fromtimestamp(float(reset), UTC) if reset is not None else None
            found = UsageLimit(at, reset_at, "429 " + str(limits["rateLimitType"])).to_dict()
            found.update(model=error.get("model") or current_model,
                         rateLimitType=str(limits["rateLimitType"]))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            continue
    return found


def session_limit(session_id: str, model: str, *, since: datetime | None = None) -> dict | None:
    """Include workers: a Fable worker can exhaust usage under an Opus orchestrator."""
    from autonomouse.board.runs import claude_projects_dir

    hits = []
    for main in claude_projects_dir().glob(f"*/{session_id}.jsonl"):
        paths = [main, *main.with_suffix("").glob("subagents/*.jsonl")]
        for path in paths:
            hit = transcript_limit(path, model, since=since)
            if hit:
                hits.append(hit)
    return max(hits, key=lambda h: h["hit_at"]) if hits else None


def fable_limit(limit: dict, model: str | None = None) -> bool:
    """The observed Fable-only weekly 429, or a message explicitly naming its limit.

    A shared quota hit while running Fable must still hold back every model.
    """
    limited_model = limit.get("model") or model or ""
    return bool(
        ("fable" in limited_model.lower()
         and limit.get("rateLimitType") == "seven_day_overage_included")
        or re.search(r"\bfable\s+limit\b", limit.get("text", ""), re.IGNORECASE)
    )


def fallback_tier(root: Path, model: str, effort: str, *, now: datetime | None = None) -> tuple[str, str]:
    """Use Opus/xhigh while a recorded Fable limit lasts; never rewrite preferences."""
    from autonomouse.board.runs import load_run
    from autonomouse.board.schema import BoardValidationError

    if "fable" not in model.lower():
        return model, effort
    now = now or datetime.now(UTC)
    for path in (root / "runs").glob("*.json"):
        try:
            run = load_run(root, path.stem)
            limit = run.usage_limit or {}
            if not fable_limit(limit, run.model):
                continue
            reset = limit.get("reset_at")
            # An unknown weekly reset gets a bounded seven-day fallback.
            until = (datetime.fromisoformat(reset) if reset else
                     datetime.fromisoformat(limit["hit_at"]) + timedelta(days=7))
            if until > now:
                return "opus", "xhigh"
        except (BoardValidationError, OSError, ValueError, KeyError, TypeError):
            continue
    return model, effort
