"""The third-party trackers a project can be linked to, read from a URL's host.

A project's `tracker` is `{"kind", "url", "link"}`: `kind` names the tracker, `url` is
the URL the owner gave, and `link` says what it points at, a `project` (every card's issue
goes in it) or an `issue` (every card's issue is a sub-issue of it). A card's
`tracker_issue` is `{"url", "identifier"}`, the issue the agent that filed it recorded,
e.g. `ABC-123`. `docs/board-schema/project.md` and `docs/board-schema/card.md` describe
both fields.

Nothing here talks to a tracker: it only reads URLs. Agents reach a tracker only through
its MCP server, registered in the user's Claude Code config; `mcp_servers` picks that
server out of the config by its URL's host, so a session started with only named servers
(`--strict-mcp-config`) can keep it. Adding a tracker, such as GitHub Projects, is one
more `Tracker` in `TRACKERS`; nothing else changes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


class TrackerError(ValueError):
    """A URL is no link autonomouse knows; the message is plain and names the fix."""


@dataclass(frozen=True)
class Tracker:
    """One tracker: its `kind`, the URL host it lives on, and `read`, which takes a URL's
    path parts and gives what the URL points at (`project` or `issue`) and, for an issue,
    its identifier; None for a URL of that host that is neither."""

    kind: str
    name: str
    host: str
    project_shape: str  # a project URL's shape, for the refusal messages
    issue_shape: str  # an issue URL's shape
    mcp_host: str  # the host of the URL the tracker's MCP server is registered with
    read: Callable[[list[str]], tuple[str, str | None] | None]


_LINEAR_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]*-[0-9]+")


def _read_linear(parts: list[str]) -> tuple[str, str | None] | None:
    """`<workspace>/project/<name>[/...]` is a project; `<workspace>/issue/<ABC-123>[/...]`
    is an issue, whose identifier Linear writes in capitals."""
    if len(parts) < 3:
        return None
    if parts[1] == "project":
        return "project", None
    if parts[1] == "issue" and _LINEAR_IDENTIFIER.fullmatch(parts[2]):
        return "issue", parts[2].upper()
    return None


TRACKERS = (
    Tracker(
        kind="linear",
        name="Linear",
        host="linear.app",
        project_shape="https://linear.app/<workspace>/project/<name>",
        issue_shape="https://linear.app/<workspace>/issue/<ABC-123>",
        mcp_host="mcp.linear.app",
        read=_read_linear,
    ),
)


def _known() -> str:
    return ", ".join(f"{t.name} ({t.host})" for t in TRACKERS)


def _read(url: str) -> tuple[Tracker, str, str | None]:
    """The tracker `url` belongs to, what it points at, and an issue's identifier."""
    url = url.strip()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    tracker = next((t for t in TRACKERS if host == t.host), None)
    if parts.scheme not in ("http", "https") or tracker is None:
        raise TrackerError(
            f"{url!r} is not a tracker autonomouse knows; the trackers it knows are "
            f"{_known()}"
        )
    read = tracker.read([p for p in parts.path.split("/") if p])
    if read is None:
        raise TrackerError(
            f"{url!r} is neither a {tracker.name} project nor a {tracker.name} issue; "
            f"give {tracker.project_shape} or {tracker.issue_shape}"
        )
    return tracker, *read


def project_link(url: str) -> dict[str, Any]:
    """The project `tracker` value for `url`: a tracker's project or issue URL."""
    tracker, link, _ = _read(url)
    return {"kind": tracker.kind, "url": url.strip(), "link": link}


def issue_link(url: str) -> dict[str, str]:
    """The card `tracker_issue` value for `url`, which must be a tracker's issue URL."""
    tracker, link, identifier = _read(url)
    if link != "issue" or identifier is None:
        raise TrackerError(
            f"{url!r} is a {tracker.name} {link}, not an issue; give the URL of the "
            f"card's own issue, {tracker.issue_shape}"
        )
    return {"url": url.strip(), "identifier": identifier}


def mcp_servers(
    tracker: dict[str, Any] | None, servers: dict[str, Any]
) -> dict[str, Any]:
    """The servers in `servers` (a Claude Code config's `mcpServers`) that reach the
    project's `tracker`: each whose `url` is on its tracker's MCP host, e.g.
    `https://mcp.linear.app/mcp`. Empty for a project with no link."""
    if tracker is None:
        return {}
    known = next((t for t in TRACKERS if t.kind == tracker.get("kind")), None)
    if known is None:
        return {}
    return {
        name: server
        for name, server in servers.items()
        if (urlsplit(str(server.get("url", ""))).hostname or "").lower() == known.mcp_host
    }


def describe(tracker: dict[str, Any] | None) -> str:
    """One line for a project's `tracker`, as `project show` and `autonomouse show` print
    it: `linear project <url>`, or `(none)`."""
    if tracker is None:
        return "(none)"
    return " ".join(
        str(tracker[key]) for key in ("kind", "link", "url") if tracker.get(key) is not None
    )
