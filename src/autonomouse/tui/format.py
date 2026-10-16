"""Small text formatters shared by more than one TUI view.

Kept separate and dependency-free (no widget imports) so each can be checked as a
pure function, the same reasoning `autonomouse.tui.card_detail.format_card`
documents for itself.
"""

from __future__ import annotations

from datetime import UTC, datetime


def elapsed(picked_up_at: str | None, *, now: datetime) -> str:
    """Time since `picked_up_at`, coarsest two units, e.g. `2h 5m ago`."""
    if picked_up_at is None:
        return "unknown"
    picked_up = datetime.strptime(picked_up_at, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )
    minutes = int((now - picked_up).total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h ago"
    if hours:
        return f"{hours}h {minutes}m ago"
    return f"{minutes}m ago"
