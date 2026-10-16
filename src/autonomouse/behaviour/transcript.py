"""A wake's agents, read from Claude Code's session transcripts, which stay where Claude
Code writes them and are only read.

The orchestrator's transcript is `<projects>/<cwd-slug>/<session_id>.jsonl`; every
sub-agent of the session, at any depth, has `<session_id>/subagents/agent-<id>.jsonl`, and
a `.meta.json` beside it. The meta names the agent that spawned it (`parentAgentId`, absent
when the session's own agent did) and, for an agent the Agent tool spawned, that tool call
(`toolUseId`); a skill's fork names no tool call.

A resumed session keeps its session id, so one transcript can hold several runs: a run's
part starts at the prompt that gives its run id ("Your run id is <id>", or "… is now <id>"
on a resume) and ends at the next prompt that gives another. Its sub-agents are every
agent in the folder whose first record falls inside its part, whatever its depth or its
spawner: a session is resumed only after its last run's `claude` has exited.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_PROMPT_RUN_ID = re.compile(r"[Yy]our run id is (?:now )?`?(\d{8}-\d{6}-[0-9a-f]{4})")


class TranscriptUnavailable(Exception):
    """The run's transcript cannot be found or read, or holds nothing of the run."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    at: str  # when the model asked for it
    done_at: str | None  # when its result came back; None when it never did
    is_error: bool
    output: str  # the result's text; read for classes, never logged
    cwd: str | None
    result_meta: dict[str, Any]  # the record's `toolUseResult` when it is an object


@dataclass
class Agent:
    """One agent's part of the transcripts: the orchestrator's, or a sub-agent's."""

    key: str  # "orchestrator", or the sub-agent's id
    records: list[dict[str, Any]]
    calls: list[ToolCall]
    meta: dict[str, Any] = field(default_factory=dict)  # a sub-agent's .meta.json
    parent: str | None = None  # the key of the agent that spawned it
    spawn: ToolCall | None = None  # the tool call that spawned it


@dataclass
class Session:
    orchestrator: Agent
    subagents: list[Agent]  # in the order they started
    skipped_lines: int  # transcript lines that were not JSON
    whole_session: bool  # no prompt named any run id, so the whole transcript is used


def parse_time(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _cleaned(record: dict[str, Any]) -> dict[str, Any]:
    """The record with every field this package reads in the shape it reads: a `message`
    that is not an object, a `timestamp` that is not an ISO-8601 time with a zone, and a
    `type`, `cwd`, `effort`, `requestId` or `uuid` that is not a string are dropped, and
    a message's `model`, `id` and `content` likewise. Transcripts are Claude Code's
    format, not ours, so a record in another shape is read as far as it goes rather than
    stopping the derivation."""
    if not isinstance(record.get("message"), dict):
        record.pop("message", None)
    else:
        message = record["message"]
        for key in ("model", "id"):
            if not isinstance(message.get(key), str):
                message.pop(key, None)
        if not isinstance(message.get("content"), (str, list)):
            message.pop("content", None)
    for key in ("type", "cwd", "effort", "requestId", "uuid", "timestamp"):
        if key in record and not isinstance(record[key], str):
            record.pop(key)
    if "timestamp" in record:
        try:
            aware = parse_time(record["timestamp"]).tzinfo is not None
        except ValueError:
            aware = False
        if not aware:
            record.pop("timestamp")
    if not isinstance(record.get("toolUseResult"), (dict, type(None))):
        record.pop("toolUseResult")
    return record


def find_transcript(session_id: str, projects_dir: Path) -> Path | None:
    """`<projects_dir>/<any cwd-slug>/<session_id>.jsonl`, or None."""
    if not projects_dir.is_dir():
        return None
    for slug_dir in sorted(projects_dir.iterdir()):
        path = slug_dir / f"{session_id}.jsonl"
        if path.is_file():
            return path
    return None


def read_records(path: Path) -> tuple[list[dict[str, Any]], int]:
    """The JSON objects in a JSONL file, and how many lines were not one."""
    records, skipped = [], 0
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(record, dict):
                records.append(_cleaned(record))
            else:
                skipped += 1
    return records, skipped


def _prompt_run_id(record: dict[str, Any]) -> str | None:
    """The run id a `claude -p` prompt gives, for a prompt record; None otherwise."""
    if (
        record.get("type") != "user"
        or record.get("isSidechain")
        or record.get("origin")
    ):
        return None
    content = (record.get("message") or {}).get("content")
    if not isinstance(content, str):
        return None
    match = _PROMPT_RUN_ID.search(content)
    return match.group(1) if match else None


def run_part(records: list[dict[str, Any]], run_id: str) -> tuple[int, int, bool]:
    """Where `run_id`'s part of a session's records starts and ends (`records[start:end]`,
    empty when no prompt names the run), and whether no prompt named any run (then every
    record is the run's)."""
    named = [(i, _prompt_run_id(r)) for i, r in enumerate(records)]
    named = [(i, rid) for i, rid in named if rid]
    if not named:
        return 0, len(records), True
    start = next((i for i, rid in named if rid == run_id), None)
    if start is None:
        return 0, 0, False
    end = next((i for i, rid in named if i > start and rid != run_id), len(records))
    return start, end, False


def _first_time(records: list[dict[str, Any]]) -> datetime | None:
    """The earliest time of the records; None when none has one."""
    return min(
        (parse_time(r["timestamp"]) for r in records if r.get("timestamp")),
        default=None,
    )


def _within(
    time: datetime | None, since: datetime | None, until: datetime | None
) -> bool:
    """Whether `time` is at or after `since` and before `until`; a missing bound is
    open, and a missing time is never within."""
    return (
        time is not None
        and (since is None or time >= since)
        and (until is None or time < until)
    )


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def tool_calls(records: list[dict[str, Any]]) -> list[ToolCall]:
    """Every tool call in the records, with its result when the records hold it."""
    calls: dict[str, ToolCall] = {}
    for record in records:
        message = record.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            named = _str(part.get("id")) and _str(part.get("name"))
            if (
                record.get("type") == "assistant"
                and part.get("type") == "tool_use"
                and named
            ):
                calls[_str(part.get("id"))] = ToolCall(
                    id=_str(part.get("id")),
                    name=_str(part.get("name")),
                    input=part.get("input")
                    if isinstance(part.get("input"), dict)
                    else {},
                    at=record.get("timestamp", ""),
                    done_at=None,
                    is_error=False,
                    output="",
                    cwd=record.get("cwd"),
                    result_meta={},
                )
            elif record.get("type") == "user" and part.get("type") == "tool_result":
                call = calls.get(_str(part.get("tool_use_id")))
                if call is not None:
                    call.done_at = record.get("timestamp")
                    call.is_error = bool(part.get("is_error"))
                    call.output = _text(part.get("content"))
                    meta = record.get("toolUseResult")
                    call.result_meta = meta if isinstance(meta, dict) else {}
    return [c for c in calls.values() if c.at]


def load_session(session_id: str, run_id: str, projects_dir: Path) -> Session:
    """The orchestrator's part of the session for `run_id`, and every sub-agent that
    started inside it. Raises TranscriptUnavailable when the transcript is missing or
    unreadable, or holds no part for the run."""
    path = find_transcript(session_id, projects_dir)
    if path is None:
        raise TranscriptUnavailable(f"no transcript for session {session_id}")
    try:
        records, skipped = read_records(path)
    except OSError as e:
        raise TranscriptUnavailable(f"{path}: {e.strerror or e}") from e
    start, end, whole = run_part(records, run_id)
    part = records[start:end]
    if not part:
        raise TranscriptUnavailable(
            f"no prompt in session {session_id} names run {run_id}"
        )
    orchestrator = Agent("orchestrator", part, tool_calls(part))
    since, until = _first_time(part), _first_time(records[end:])

    started: list[tuple[datetime, Agent]] = []
    folder = path.parent / session_id / "subagents"
    if folder.is_dir():
        for file in sorted(folder.glob("agent-*.jsonl")):
            try:
                sub_records, sub_skipped = read_records(file)
            except OSError:
                continue
            skipped += sub_skipped
            meta_path = file.with_suffix(".meta.json")
            try:
                meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
            except (OSError, json.JSONDecodeError):
                meta = {}
            if isinstance(meta, dict):  # only the string fields are read
                meta = {k: v for k, v in meta.items() if isinstance(v, str)}
            first = _first_time(sub_records)
            if not _within(first, since, until):
                continue  # another run's agent, or one with no record to place
            key = file.stem.removeprefix("agent-")
            agent = Agent(
                key,
                sub_records,
                tool_calls(sub_records),
                meta if isinstance(meta, dict) else {},
            )
            started.append((first, agent))
    started.sort(key=lambda found: found[0])
    subagents = [agent for _, agent in started]

    # Each sub-agent's spawner: the agent whose call its meta's toolUseId names, or whose
    # call's result names its id; else its meta's parentAgentId; else the session's own
    # agent, which a meta without parentAgentId means.
    spawned_by: dict[str, tuple[str, ToolCall]] = {}  # call id or agent id -> spawner
    for agent in [orchestrator, *subagents]:
        for call in agent.calls:
            spawned_by[call.id] = (agent.key, call)
            if _str(call.result_meta.get("agentId")):
                spawned_by[call.result_meta["agentId"]] = (agent.key, call)
    for agent in subagents:
        spawner, agent.spawn = (
            spawned_by.get(agent.meta.get("toolUseId", ""))
            or spawned_by.get(agent.key)
            or (None, None)
        )
        agent.parent = spawner or agent.meta.get("parentAgentId") or "orchestrator"
    return Session(orchestrator, subagents, skipped, whole)
