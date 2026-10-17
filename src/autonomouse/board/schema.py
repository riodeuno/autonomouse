"""Board schema: dataclasses and validation."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class BoardValidationError(Exception):
    """A board file failed validation; the message is plain-language and names the file and the field."""


def _check_keys(
    data: dict[str, Any], required: set[str], *, kind: str, source: str
) -> None:
    """Require every one of `required`; a key outside it is the caller's to keep.

    A key this code does not know is not refused, because processes running different
    versions of autonomouse read and write the same board: the human's board viewer,
    started before a merge added the card's `access` field, refused the first card
    written with it and died (sys-t1). Each loader keeps such keys in its object's
    `extra` and writes them back unchanged, as run records do (sys-d31).
    """
    missing = required - set(data.keys())
    if missing:
        raise BoardValidationError(
            f"{source}: missing field(s) in {kind}: {', '.join(sorted(missing))}"
        )


def _unknown(data: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """The fields of `data` this code does not know, as they are."""
    return {k: v for k, v in data.items() if k not in known}


class Stack(str, enum.Enum):
    """Card stack state."""

    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DECISION_NEEDED = "decision_needed"
    DONE = "done"
    CANCELLED = "cancelled"


class Size(str, enum.Enum):
    """Card size estimate."""

    XS = "xs"
    S = "s"
    M = "m"


class Phase(str, enum.Enum):
    """Card work phase."""

    WRITING = "writing"
    TESTING = "testing"
    CI = "ci"
    REVIEW = "review"


class MergeMethod(str, enum.Enum):
    """How a project's green PR merges into the wake's interim branch."""

    SQUASH = "squash"
    MERGE = "merge"
    REBASE = "rebase"


@dataclass
class Claim:
    """Project claim (who is working on it)."""

    run_id: str | None
    session_id: str | None
    pid: int | None
    model: str | None
    claimed_at: str | None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "pid": self.pid,
            "model": self.model,
            "claimed_at": self.claimed_at,
            **self.extra,
        }


@dataclass
class Assigned:
    """Card assignment (who is working on it)."""

    model: str | None
    effort: str | None
    agent_type: str | None
    session_id: str | None
    pid: int | None
    picked_up_at: str | None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "model": self.model,
            "effort": self.effort,
            "agent_type": self.agent_type,
            "session_id": self.session_id,
            "pid": self.pid,
            "picked_up_at": self.picked_up_at,
            **self.extra,
        }


@dataclass
class Handoff:
    """Card handoff note."""

    last_step: str | None
    next_step: str | None
    updated_at: str | None
    run_id: str | None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "last_step": self.last_step,
            "next_step": self.next_step,
            "updated_at": self.updated_at,
            "run_id": self.run_id,
            **self.extra,
        }


# The last option of every decision (`docs/rules.md` item 8): the human answers in their
# own words instead of picking a listed option. `card ask` appends it.
OTHER_OPTION = "other: tell me"


@dataclass
class Decision:
    """A decision the card asked for."""

    id: str
    question: str
    options: list[str]
    recommendation: str | None
    asked_at: str
    answer: str | None
    answered_at: str | None
    # The Telegram id of the message that sent this decision's brief; null when the
    # send failed or the decision was recorded before the field existed.
    message_id: int | None = None
    # The plain-words brief the asking agent wrote for a human who has not read the
    # code (`docs/rules.md` item 8); null on a decision recorded before `card ask`
    # required it.
    brief: str | None = None
    # The human's latest Telegram reply to this decision after it left decision_needed
    # — answered, or left unanswered by a card_cancel — and when `autonomouse notify
    # poll` recorded it (`autonomouse.inbox`); the answer, or the lack of one, stays as
    # it was. Null until such a reply comes, and on a decision recorded before the
    # fields existed.
    reply: str | None = None
    replied_at: str | None = None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "question": self.question,
            "options": self.options,
            "recommendation": self.recommendation,
            "asked_at": self.asked_at,
            "answer": self.answer,
            "answered_at": self.answered_at,
            "message_id": self.message_id,
            "brief": self.brief,
            "reply": self.reply,
            "replied_at": self.replied_at,
            **self.extra,
        }


@dataclass
class HistoryEntry:
    """A card history entry."""

    at: str
    run_id: str
    from_: str | None  # JSON key is "from"
    to: str | None
    note: str | None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict with 'from' as JSON key."""
        return {
            "at": self.at,
            "run_id": self.run_id,
            "from": self.from_,
            "to": self.to,
            "note": self.note,
            **self.extra,
        }


@dataclass
class Access:
    """A login or tool a card's test needs on this machine.

    `check` is a shell command that exits 0 when that login or tool is present
    (`gh auth status`). Nothing in autonomouse runs it; the card only records it.
    """

    name: str
    check: str
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {"name": self.name, "check": self.check, **self.extra}


@dataclass
class Project:
    """A project."""

    slug: str
    name: str
    goal: str
    repo: str
    repo_path: str
    default_branch: str
    priority: int
    tracker: dict[str, Any] | None
    claim: Claim
    created_at: str
    updated_at: str
    # The per-project facts the charters need (docs/board-schema.md, project.json).
    test_temp_env: list[str] = field(default_factory=list)
    merge_method: MergeMethod = MergeMethod.SQUASH
    charter_files: list[str] = field(default_factory=list)
    # When the project was marked done (`project done`, or a wake that found every card
    # done or cancelled and every done card's PR merged); None while it is not done.
    done_at: str | None = None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict. `done_at` is written only while it is set,
        so the file of a project that is not done is the one older versions wrote."""
        return {
            "slug": self.slug,
            "name": self.name,
            "goal": self.goal,
            "repo": self.repo,
            "repo_path": self.repo_path,
            "default_branch": self.default_branch,
            "priority": self.priority,
            "tracker": self.tracker,
            "test_temp_env": list(self.test_temp_env),
            "merge_method": self.merge_method.value,
            "charter_files": list(self.charter_files),
            "claim": self.claim.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **({"done_at": self.done_at} if self.done_at is not None else {}),
            **self.extra,
        }


@dataclass
class Card:
    """A card in the board."""

    id: str
    project: str
    title: str
    stack: Stack
    position: int
    size: Size
    goal: str
    limits: list[str]
    test: str
    depends_on: list[str]
    assigned: Assigned
    worktree: str | None
    branch: str | None
    base_branch: str | None
    pr: str | None
    phase: Phase | None
    iteration: int
    handoff: Handoff
    decisions: list[Decision]
    history: list[HistoryEntry]
    created_at: str
    updated_at: str
    attachments: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    access: list[Access] = field(default_factory=list)
    # The card's issue in its project's tracker, `{url, identifier}` (`board.trackers`).
    tracker_issue: dict[str, str] | None = None
    # Fields this code does not know, kept as they were and written back last.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "id": self.id,
            "project": self.project,
            "title": self.title,
            "stack": self.stack.value,
            "position": self.position,
            "size": self.size.value,
            "goal": self.goal,
            "limits": self.limits,
            "test": self.test,
            "depends_on": self.depends_on,
            "files": list(self.files),
            "access": [a.to_dict() for a in self.access],
            "assigned": self.assigned.to_dict(),
            "worktree": self.worktree,
            "branch": self.branch,
            "base_branch": self.base_branch,
            "pr": self.pr,
            "phase": self.phase.value if self.phase is not None else None,
            "iteration": self.iteration,
            "handoff": self.handoff.to_dict(),
            "decisions": [d.to_dict() for d in self.decisions],
            "history": [h.to_dict() for h in self.history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attachments": list(self.attachments),
            "tracker_issue": (
                dict(self.tracker_issue) if self.tracker_issue is not None else None
            ),
            **self.extra,
        }


def claim_from_dict(data: dict[str, Any], *, source: str) -> Claim:
    """Parse a Claim from a dict."""
    required = {"run_id", "session_id", "pid", "model", "claimed_at"}
    _check_keys(data, required, kind="claim", source=source)
    extra = _unknown(data, required)

    run_id = data.get("run_id")
    session_id = data.get("session_id")
    pid = data.get("pid")
    model = data.get("model")
    claimed_at = data.get("claimed_at")

    if run_id is not None and not isinstance(run_id, str):
        raise BoardValidationError(
            f"{source}: claim.run_id must be string or null — got {type(run_id).__name__}"
        )
    if session_id is not None and not isinstance(session_id, str):
        raise BoardValidationError(
            f"{source}: claim.session_id must be string or null — got {type(session_id).__name__}"
        )
    if pid is not None and not isinstance(pid, int):
        raise BoardValidationError(
            f"{source}: claim.pid must be integer or null — got {type(pid).__name__}"
        )
    if model is not None and not isinstance(model, str):
        raise BoardValidationError(
            f"{source}: claim.model must be string or null — got {type(model).__name__}"
        )
    if claimed_at is not None and not isinstance(claimed_at, str):
        raise BoardValidationError(
            f"{source}: claim.claimed_at must be string or null — got {type(claimed_at).__name__}"
        )

    return Claim(
        run_id=run_id,
        session_id=session_id,
        pid=pid,
        model=model,
        claimed_at=claimed_at,
        extra=extra,
    )


def assigned_from_dict(data: dict[str, Any], *, source: str) -> Assigned:
    """Parse an Assigned from a dict."""
    required = {"model", "effort", "agent_type", "session_id", "pid", "picked_up_at"}
    _check_keys(data, required, kind="assigned", source=source)
    extra = _unknown(data, required)

    model = data.get("model")
    effort = data.get("effort")
    agent_type = data.get("agent_type")
    session_id = data.get("session_id")
    pid = data.get("pid")
    picked_up_at = data.get("picked_up_at")

    if model is not None and not isinstance(model, str):
        raise BoardValidationError(
            f"{source}: assigned.model must be string or null — got {type(model).__name__}"
        )
    if effort is not None and not isinstance(effort, str):
        raise BoardValidationError(
            f"{source}: assigned.effort must be string or null — got {type(effort).__name__}"
        )
    if agent_type is not None and not isinstance(agent_type, str):
        raise BoardValidationError(
            f"{source}: assigned.agent_type must be string or null — got {type(agent_type).__name__}"
        )
    if session_id is not None and not isinstance(session_id, str):
        raise BoardValidationError(
            f"{source}: assigned.session_id must be string or null — got {type(session_id).__name__}"
        )
    if pid is not None and not isinstance(pid, int):
        raise BoardValidationError(
            f"{source}: assigned.pid must be integer or null — got {type(pid).__name__}"
        )
    if picked_up_at is not None and not isinstance(picked_up_at, str):
        raise BoardValidationError(
            f"{source}: assigned.picked_up_at must be string or null — got {type(picked_up_at).__name__}"
        )

    return Assigned(
        model=model,
        effort=effort,
        agent_type=agent_type,
        session_id=session_id,
        pid=pid,
        picked_up_at=picked_up_at,
        extra=extra,
    )


def handoff_from_dict(data: dict[str, Any], *, source: str) -> Handoff:
    """Parse a Handoff from a dict."""
    required = {"last_step", "next_step", "updated_at", "run_id"}
    _check_keys(data, required, kind="handoff", source=source)
    extra = _unknown(data, required)

    last_step = data.get("last_step")
    next_step = data.get("next_step")
    updated_at = data.get("updated_at")
    run_id = data.get("run_id")

    if last_step is not None and not isinstance(last_step, str):
        raise BoardValidationError(
            f"{source}: handoff.last_step must be string or null — got {type(last_step).__name__}"
        )
    if next_step is not None and not isinstance(next_step, str):
        raise BoardValidationError(
            f"{source}: handoff.next_step must be string or null — got {type(next_step).__name__}"
        )
    if updated_at is not None and not isinstance(updated_at, str):
        raise BoardValidationError(
            f"{source}: handoff.updated_at must be string or null — got {type(updated_at).__name__}"
        )
    if run_id is not None and not isinstance(run_id, str):
        raise BoardValidationError(
            f"{source}: handoff.run_id must be string or null — got {type(run_id).__name__}"
        )

    return Handoff(
        last_step=last_step,
        next_step=next_step,
        updated_at=updated_at,
        run_id=run_id,
        extra=extra,
    )


def decision_from_dict(data: dict[str, Any], *, source: str) -> Decision:
    """Parse a Decision from a dict.

    `message_id`, `brief`, `reply` and `replied_at` default to null when absent, so a
    decision written before any of them existed still loads.
    """
    data = {
        "message_id": None,
        "brief": None,
        "reply": None,
        "replied_at": None,
        **data,
    }
    required = {
        "id",
        "question",
        "options",
        "recommendation",
        "asked_at",
        "answer",
        "answered_at",
        "message_id",
        "brief",
        "reply",
        "replied_at",
    }
    _check_keys(data, required, kind="decision", source=source)
    extra = _unknown(data, required)

    decision_id = data.get("id")
    question = data.get("question")
    options = data.get("options")
    recommendation = data.get("recommendation")
    asked_at = data.get("asked_at")
    answer = data.get("answer")
    answered_at = data.get("answered_at")
    message_id = data.get("message_id")
    brief = data.get("brief")
    reply = data.get("reply")
    replied_at = data.get("replied_at")

    if not isinstance(decision_id, str):
        raise BoardValidationError(
            f"{source}: decision.id must be string — got {type(decision_id).__name__}"
        )
    if not isinstance(question, str):
        raise BoardValidationError(
            f"{source}: decision.question must be string — got {type(question).__name__}"
        )
    if not isinstance(options, list) or not all(
        isinstance(opt, str) for opt in options
    ):
        raise BoardValidationError(
            f"{source}: decision.options must be list of strings"
        )
    if brief is not None and not isinstance(brief, str):
        raise BoardValidationError(
            f"{source}: decision.brief must be string or null — got {type(brief).__name__}"
        )
    if recommendation is not None and not isinstance(recommendation, str):
        raise BoardValidationError(
            f"{source}: decision.recommendation must be string or null — got {type(recommendation).__name__}"
        )
    if not isinstance(asked_at, str):
        raise BoardValidationError(
            f"{source}: decision.asked_at must be string — got {type(asked_at).__name__}"
        )
    if answer is not None and not isinstance(answer, str):
        raise BoardValidationError(
            f"{source}: decision.answer must be string or null — got {type(answer).__name__}"
        )
    if answered_at is not None and not isinstance(answered_at, str):
        raise BoardValidationError(
            f"{source}: decision.answered_at must be string or null — got {type(answered_at).__name__}"
        )
    if message_id is not None and (
        not isinstance(message_id, int) or isinstance(message_id, bool)
    ):
        raise BoardValidationError(
            f"{source}: decision.message_id must be integer or null — got {type(message_id).__name__}"
        )
    if reply is not None and not isinstance(reply, str):
        raise BoardValidationError(
            f"{source}: decision.reply must be string or null — got {type(reply).__name__}"
        )
    if replied_at is not None and not isinstance(replied_at, str):
        raise BoardValidationError(
            f"{source}: decision.replied_at must be string or null — got {type(replied_at).__name__}"
        )

    return Decision(
        id=decision_id,
        question=question,
        options=options,
        recommendation=recommendation,
        asked_at=asked_at,
        answer=answer,
        answered_at=answered_at,
        message_id=message_id,
        brief=brief,
        reply=reply,
        replied_at=replied_at,
        extra=extra,
    )


def access_from_dict(data: Any, *, source: str) -> Access:
    """Parse one `access` entry, `{name, check}`, from a dict."""
    if not isinstance(data, dict):
        raise BoardValidationError(
            f"{source}: each access entry must be an object — got {type(data).__name__}"
        )
    required = {"name", "check"}
    _check_keys(data, required, kind="access entry", source=source)
    extra = _unknown(data, required)
    name = data.get("name")
    check = data.get("check")
    if not isinstance(name, str):
        raise BoardValidationError(
            f"{source}: access.name must be string — got {type(name).__name__}"
        )
    if not isinstance(check, str):
        raise BoardValidationError(
            f"{source}: access.check must be string — got {type(check).__name__}"
        )
    return Access(name=name, check=check, extra=extra)


def history_entry_from_dict(data: dict[str, Any], *, source: str) -> HistoryEntry:
    """Parse a HistoryEntry from a dict."""
    required = {"at", "run_id", "from", "to", "note"}
    _check_keys(data, required, kind="history entry", source=source)
    extra = _unknown(data, required)

    at = data.get("at")
    run_id = data.get("run_id")
    from_ = data.get("from")
    to = data.get("to")
    note = data.get("note")

    if not isinstance(at, str):
        raise BoardValidationError(
            f"{source}: history.at must be string — got {type(at).__name__}"
        )
    if not isinstance(run_id, str):
        raise BoardValidationError(
            f"{source}: history.run_id must be string — got {type(run_id).__name__}"
        )
    if from_ is not None and not isinstance(from_, str):
        raise BoardValidationError(
            f"{source}: history.from must be string or null — got {type(from_).__name__}"
        )
    if to is not None and not isinstance(to, str):
        raise BoardValidationError(
            f"{source}: history.to must be string or null — got {type(to).__name__}"
        )
    if note is not None and not isinstance(note, str):
        raise BoardValidationError(
            f"{source}: history.note must be string or null — got {type(note).__name__}"
        )

    return HistoryEntry(
        at=at,
        run_id=run_id,
        from_=from_,
        to=to,
        note=note,
        extra=extra,
    )


def project_from_dict(data: dict[str, Any], *, source: str) -> Project:
    """Parse a Project from a dict.

    `test_temp_env`, `merge_method` and `charter_files` take their defaults when absent,
    so a project written before any of them existed still loads; `save_project` writes
    them back in on the next save. `done_at` is optional: absent or null means not done.
    """
    data = {
        "test_temp_env": [],
        "merge_method": MergeMethod.SQUASH.value,
        "charter_files": [],
        **data,
    }
    required = {
        "slug",
        "name",
        "goal",
        "repo",
        "repo_path",
        "default_branch",
        "priority",
        "tracker",
        "test_temp_env",
        "merge_method",
        "charter_files",
        "claim",
        "created_at",
        "updated_at",
    }
    _check_keys(data, required, kind="project", source=source)
    extra = _unknown(data, required | {"done_at"})

    slug = data.get("slug")
    name = data.get("name")
    goal = data.get("goal")
    repo = data.get("repo")
    repo_path = data.get("repo_path")
    default_branch = data.get("default_branch")
    priority = data.get("priority")
    tracker = data.get("tracker")
    test_temp_env = data.get("test_temp_env")
    merge_method_str = data.get("merge_method")
    charter_files = data.get("charter_files")
    claim_data = data.get("claim")
    created_at = data.get("created_at")
    updated_at = data.get("updated_at")
    done_at = data.get("done_at")

    if not isinstance(slug, str):
        raise BoardValidationError(
            f"{source}: 'slug' must be string — got {type(slug).__name__}"
        )
    if not isinstance(name, str):
        raise BoardValidationError(
            f"{source}: 'name' must be string — got {type(name).__name__}"
        )
    if not isinstance(goal, str):
        raise BoardValidationError(
            f"{source}: 'goal' must be string — got {type(goal).__name__}"
        )
    if not isinstance(repo, str):
        raise BoardValidationError(
            f"{source}: 'repo' must be string — got {type(repo).__name__}"
        )
    if not isinstance(repo_path, str):
        raise BoardValidationError(
            f"{source}: 'repo_path' must be string — got {type(repo_path).__name__}"
        )
    if not isinstance(default_branch, str):
        raise BoardValidationError(
            f"{source}: 'default_branch' must be string — got {type(default_branch).__name__}"
        )
    if not isinstance(priority, int):
        raise BoardValidationError(
            f"{source}: 'priority' must be integer — got {type(priority).__name__}"
        )
    if tracker is not None and not isinstance(tracker, dict):
        raise BoardValidationError(
            f"{source}: 'tracker' must be object or null — got {type(tracker).__name__}"
        )
    if not isinstance(test_temp_env, list) or not all(
        isinstance(v, str) for v in test_temp_env
    ):
        raise BoardValidationError(f"{source}: 'test_temp_env' must be list of strings")
    if not isinstance(merge_method_str, str):
        raise BoardValidationError(
            f"{source}: 'merge_method' must be string — got {type(merge_method_str).__name__}"
        )
    try:
        merge_method = MergeMethod(merge_method_str)
    except ValueError:
        raise BoardValidationError(
            f"{source}: 'merge_method' must be one of "
            f"{', '.join(m.value for m in MergeMethod)} — got '{merge_method_str}'"
        )
    if not isinstance(charter_files, list) or not all(
        isinstance(g, str) for g in charter_files
    ):
        raise BoardValidationError(f"{source}: 'charter_files' must be list of strings")
    if not isinstance(claim_data, dict):
        raise BoardValidationError(
            f"{source}: 'claim' must be object — got {type(claim_data).__name__}"
        )
    if not isinstance(created_at, str):
        raise BoardValidationError(
            f"{source}: 'created_at' must be string — got {type(created_at).__name__}"
        )
    if not isinstance(updated_at, str):
        raise BoardValidationError(
            f"{source}: 'updated_at' must be string — got {type(updated_at).__name__}"
        )
    if done_at is not None and not isinstance(done_at, str):
        raise BoardValidationError(
            f"{source}: 'done_at' must be string or null — got {type(done_at).__name__}"
        )

    claim = claim_from_dict(claim_data, source=source)

    return Project(
        slug=slug,
        name=name,
        goal=goal,
        repo=repo,
        repo_path=repo_path,
        default_branch=default_branch,
        priority=priority,
        tracker=tracker,
        claim=claim,
        created_at=created_at,
        updated_at=updated_at,
        test_temp_env=test_temp_env,
        merge_method=merge_method,
        charter_files=charter_files,
        done_at=done_at,
        extra=extra,
    )


def card_from_dict(data: dict[str, Any], *, source: str) -> Card:
    """Parse a Card from a dict.

    `files`, `access` and `attachments` default to `[]`, and `tracker_issue` to null,
    when absent, so a card written before any of them existed still loads; `save_card`
    writes them back in on the next save.
    """
    data = {"files": [], "access": [], "attachments": [], "tracker_issue": None, **data}
    required = {
        "id",
        "project",
        "title",
        "stack",
        "position",
        "size",
        "goal",
        "limits",
        "test",
        "depends_on",
        "files",
        "access",
        "assigned",
        "worktree",
        "branch",
        "base_branch",
        "pr",
        "phase",
        "iteration",
        "handoff",
        "decisions",
        "history",
        "created_at",
        "updated_at",
        "attachments",
        "tracker_issue",
    }
    _check_keys(data, required, kind="card", source=source)
    extra = _unknown(data, required)

    card_id = data.get("id")
    project = data.get("project")
    title = data.get("title")
    stack_str = data.get("stack")
    position = data.get("position")
    size_str = data.get("size")
    goal = data.get("goal")
    limits = data.get("limits")
    test = data.get("test")
    depends_on = data.get("depends_on")
    files = data.get("files")
    access_data = data.get("access")
    assigned_data = data.get("assigned")
    worktree = data.get("worktree")
    branch = data.get("branch")
    base_branch = data.get("base_branch")
    pr = data.get("pr")
    phase_str = data.get("phase")
    iteration = data.get("iteration")
    handoff_data = data.get("handoff")
    decisions_data = data.get("decisions")
    history_data = data.get("history")
    created_at = data.get("created_at")
    updated_at = data.get("updated_at")
    attachments = data.get("attachments")
    tracker_issue = data.get("tracker_issue")

    # Type checking
    if not isinstance(card_id, str):
        raise BoardValidationError(
            f"{source}: 'id' must be string — got {type(card_id).__name__}"
        )
    if not isinstance(project, str):
        raise BoardValidationError(
            f"{source}: 'project' must be string — got {type(project).__name__}"
        )
    if not isinstance(title, str):
        raise BoardValidationError(
            f"{source}: 'title' must be string — got {type(title).__name__}"
        )
    if not isinstance(stack_str, str):
        raise BoardValidationError(
            f"{source}: 'stack' must be string — got {type(stack_str).__name__}"
        )
    if not isinstance(position, int):
        raise BoardValidationError(
            f"{source}: 'position' must be integer — got {type(position).__name__}"
        )
    if not isinstance(size_str, str):
        raise BoardValidationError(
            f"{source}: 'size' must be string — got {type(size_str).__name__}"
        )
    if not isinstance(goal, str):
        raise BoardValidationError(
            f"{source}: 'goal' must be string — got {type(goal).__name__}"
        )
    if not isinstance(limits, list) or not all(isinstance(l, str) for l in limits):
        raise BoardValidationError(f"{source}: 'limits' must be list of strings")
    if not isinstance(test, str):
        raise BoardValidationError(
            f"{source}: 'test' must be string — got {type(test).__name__}"
        )
    if not isinstance(depends_on, list) or not all(
        isinstance(d, str) for d in depends_on
    ):
        raise BoardValidationError(f"{source}: 'depends_on' must be list of strings")
    if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
        raise BoardValidationError(f"{source}: 'files' must be list of strings")
    if not isinstance(access_data, list):
        raise BoardValidationError(
            f"{source}: 'access' must be list — got {type(access_data).__name__}"
        )
    if not isinstance(assigned_data, dict):
        raise BoardValidationError(
            f"{source}: 'assigned' must be object — got {type(assigned_data).__name__}"
        )
    if worktree is not None and not isinstance(worktree, str):
        raise BoardValidationError(
            f"{source}: 'worktree' must be string or null — got {type(worktree).__name__}"
        )
    if branch is not None and not isinstance(branch, str):
        raise BoardValidationError(
            f"{source}: 'branch' must be string or null — got {type(branch).__name__}"
        )
    if base_branch is not None and not isinstance(base_branch, str):
        raise BoardValidationError(
            f"{source}: 'base_branch' must be string or null — got {type(base_branch).__name__}"
        )
    if pr is not None and not isinstance(pr, str):
        raise BoardValidationError(
            f"{source}: 'pr' must be string or null — got {type(pr).__name__}"
        )
    if phase_str is not None and not isinstance(phase_str, str):
        raise BoardValidationError(
            f"{source}: 'phase' must be string or null — got {type(phase_str).__name__}"
        )
    if not isinstance(iteration, int):
        raise BoardValidationError(
            f"{source}: 'iteration' must be integer — got {type(iteration).__name__}"
        )
    if not isinstance(handoff_data, dict):
        raise BoardValidationError(
            f"{source}: 'handoff' must be object — got {type(handoff_data).__name__}"
        )
    if not isinstance(decisions_data, list):
        raise BoardValidationError(
            f"{source}: 'decisions' must be list — got {type(decisions_data).__name__}"
        )
    if not isinstance(history_data, list):
        raise BoardValidationError(
            f"{source}: 'history' must be list — got {type(history_data).__name__}"
        )
    if not isinstance(created_at, str):
        raise BoardValidationError(
            f"{source}: 'created_at' must be string — got {type(created_at).__name__}"
        )
    if not isinstance(updated_at, str):
        raise BoardValidationError(
            f"{source}: 'updated_at' must be string — got {type(updated_at).__name__}"
        )
    if not isinstance(attachments, list) or not all(
        isinstance(a, str) for a in attachments
    ):
        raise BoardValidationError(f"{source}: 'attachments' must be list of strings")
    if tracker_issue is not None and not (
        isinstance(tracker_issue, dict)
        and isinstance(tracker_issue.get("url"), str)
        and isinstance(tracker_issue.get("identifier"), str)
    ):
        raise BoardValidationError(
            f"{source}: 'tracker_issue' must be null or an object with string 'url' and "
            "'identifier'"
        )

    # Enum validation
    try:
        stack = Stack(stack_str)
    except ValueError:
        raise BoardValidationError(
            f"{source}: 'stack' must be one of backlog, todo, in_progress, decision_needed, done, cancelled — got '{stack_str}'"
        )

    try:
        size = Size(size_str)
    except ValueError:
        raise BoardValidationError(
            f"{source}: 'size' must be one of xs, s, m — got '{size_str}'"
        )

    phase = None
    if phase_str is not None:
        try:
            phase = Phase(phase_str)
        except ValueError:
            raise BoardValidationError(
                f"{source}: 'phase' must be one of writing, testing, ci, review or null — got '{phase_str}'"
            )

    assigned = assigned_from_dict(assigned_data, source=source)
    handoff = handoff_from_dict(handoff_data, source=source)
    decisions = [decision_from_dict(d, source=source) for d in decisions_data]
    history = [history_entry_from_dict(h, source=source) for h in history_data]
    access = [access_from_dict(a, source=source) for a in access_data]

    return Card(
        id=card_id,
        project=project,
        title=title,
        stack=stack,
        position=position,
        size=size,
        goal=goal,
        limits=limits,
        test=test,
        depends_on=depends_on,
        files=files,
        access=access,
        assigned=assigned,
        worktree=worktree,
        branch=branch,
        base_branch=base_branch,
        pr=pr,
        phase=phase,
        iteration=iteration,
        handoff=handoff,
        decisions=decisions,
        history=history,
        created_at=created_at,
        updated_at=updated_at,
        attachments=attachments,
        tracker_issue=tracker_issue,
        extra=extra,
    )
