"""The behaviour review: how the agents of a window of runs behaved, from their behaviour
logs, the board's cards and the board's git history.

`autonomouse review behaviour --since <days>` reads the behaviour log (`<run_id>.summary.json`
in the `logs` folder beside the board; `autonomouse.behaviour.log` says what each count
means) of every run whose id, its UTC start time, falls in the last <days> days, and
writes `reviews/behaviour-<date>.md` for the UTC date it is written. `docs/behaviour-log.md`
lists every row and where it comes from. No row reads a shell command: the board's rows
come from its cards' history rows and decisions (`behaviour.log.board_rows`), read when
the review is written, and from its git history; the rest come from the logs, which read
the transcripts' records and the run records. It reads no code, and copies classes,
counts, ids, times and URLs, never prose.

Every section lists its rows per run and ends with its totals over the window. Every row
names the run and the card; the Runs section gives each run's session id, which names its
transcript.

1. Runs: the logs read, the cards each run worked, and why a log has no agents.
2. Cards done: each card whose last stack move in a run took it to `done`.
3. Decisions: each decision a card raised in a run: its kind, and the run id of the
   history row that answered it.
4. Iterations: each iteration increment and its cause.
5. Cost: per run, per card and per tier (model/effort), each agent priced from its own
   transcript messages; the rest of the run's total is shown as not attributed. Each agent
   without a cost is counted with the model that had no rate.
6. Rule breaches, by class: hand edits of cards (a change to a card file in a board
   commit that no command recorded as a history row) and times on the board that cannot
   be true.
7. Waits: each agent's longest idle gap.
8. PR state at finish, as `gh` saw it when the log was derived.
9. Jev and the orchestrator: every answer, and each answer Jev gave against the
   orchestrator's answer to the same question on the same card nearest in time.

The report is written, never committed: the board's next commit carries it.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from autonomouse.behaviour import price
from autonomouse.behaviour.log import board_rows, clock_text, logs_dir
from autonomouse.behaviour.transcript import parse_time
from autonomouse.board.commands import CommandError, card_list
from autonomouse.board.runs import load_run
from autonomouse.board.schema import BoardValidationError, Card

RUN_ID = re.compile(r"(\d{8}-\d{6})-[0-9a-f]{4}")
ROLES = ("orchestrator", "worker", "helper")
_CARD_PATH = re.compile(r"projects/([^/]+)/cards/([^/]+)\.json")
_RUN_IN_MESSAGE = re.compile(r"\brun (\d{8}-\d{6}-[0-9a-f]{4})\b")


@dataclass
class Log:
    run_id: str
    started: datetime
    summary: dict[str, Any]


def run_started(run_id: str) -> datetime | None:
    """The UTC start time a run id carries; None for a name that is not a run id."""
    match = RUN_ID.fullmatch(run_id)
    if match is None:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)


def load_logs(folder: Path, since: datetime) -> list[Log]:
    """Every behaviour log in `folder` whose run started at or after `since`, oldest
    first."""
    logs = []
    for path in sorted(folder.glob("*.summary.json")):
        run_id = path.name.removesuffix(".summary.json")
        started = run_started(run_id)
        if started is None or started < since:
            continue
        try:
            summary = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ValueError(
                f"behaviour log of run {run_id}: not valid JSON — {e}"
            ) from e
        logs.append(Log(run_id, started, summary))
    return logs


# --------------------------------------------------------------- hand edits --


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, input=stdin, capture_output=True, check=True
    ).stdout


def _cards_at(root: Path, specs: list[str]) -> list[dict[str, Any] | None]:
    """The card at each `<commit>:<path>`, read by one `git cat-file --batch`; None for
    one git does not have or that is not a JSON object."""
    out = _git(
        root, "cat-file", "--batch", stdin="".join(f"{s}\n" for s in specs).encode()
    )
    found: list[dict[str, Any] | None] = []
    at = 0
    for _ in specs:
        end = out.index(b"\n", at)
        header, at = out[at:end].split(), end + 1
        if len(header) != 3:  # `<spec> missing`
            found.append(None)
            continue
        size = int(header[2])
        try:
            card = json.loads(out[at : at + size])
        except ValueError:
            card = None
        found.append(card if isinstance(card, dict) else None)
        at += size + 1
    return found


def unrecorded(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[str]:
    """What a change to a card file wrote that no command recorded: nothing when it
    appended history rows and kept the earlier ones as they were, as every command
    does; else the fields it changed (`not JSON` when either side is not a card)."""
    if old is None or new is None:
        return ["not JSON"]
    before, after = old.get("history") or [], new.get("history") or []
    if len(after) > len(before) and after[: len(before)] == before:
        return []
    return sorted(
        key for key in old.keys() | new.keys() if old.get(key) != new.get(key)
    )


def _utc(stamp: str) -> datetime:
    return parse_time(stamp).astimezone(UTC)


def hand_edits(root: Path, logs: list[Log], since: datetime) -> list[list[Any]] | None:
    """Each change to a card file in a board commit since `since` that no command
    recorded (`unrecorded`), as `[run, card, commit, at, writes]`, for the runs of
    `logs`: the run the commit's message names (`run <id>`), else the run of the card's
    project whose start and end hold the commit's time. None when the board's git
    history cannot be read."""
    ids = {log.run_id for log in logs}
    windows = []
    for log in logs:
        try:
            record = load_run(root, log.run_id)
        except (CommandError, BoardValidationError, OSError, ValueError):
            continue
        if record.started_at:
            end = _utc(record.ended_at) if record.ended_at else None
            windows.append((log.run_id, record.project, _utc(record.started_at), end))
    try:
        listing = _git(
            root,
            "log",
            f"--since={since.isoformat()}",
            "--format=%x1e%H %cI %s",
            "--name-status",
            "--",
            "projects",
        ).decode()
    except (OSError, subprocess.CalledProcessError):
        return None
    changes = []
    for block in listing.split("\x1e")[1:]:
        head, _, files = block.partition("\n")
        sha, at, message = (head.split(" ", 2) + [""])[:3]
        for line in files.splitlines():
            status, _, path = line.partition("\t")
            match = _CARD_PATH.fullmatch(path)
            if status == "M" and match:
                changes.append(
                    (sha, _utc(at), message, match.group(1), match.group(2), path)
                )
    if not changes:
        return []
    cards = _cards_at(
        root, [f"{c[0]}{side}:{c[5]}" for c in changes for side in ("^", "")]
    )
    rows = []
    for i, (sha, at, message, project, card, _) in enumerate(changes):
        writes = unrecorded(cards[2 * i], cards[2 * i + 1])
        named = _RUN_IN_MESSAGE.search(message)
        if named and named.group(1) in ids:
            run = named.group(1)
        else:
            run = next(
                (
                    run_id
                    for run_id, run_project, start, end in windows
                    if run_project == project
                    and start <= at
                    and (end is None or at <= end)
                ),
                None,
            )
        if writes and run is not None:
            rows.append(
                [
                    run,
                    card,
                    sha[:8],
                    at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    ", ".join(writes),
                ]
            )
    return sorted(rows, key=lambda row: (row[0], row[3]))


# ------------------------------------------------------------------ helpers --


def _cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return " ".join(str(value).split()).replace("|", "\\|")


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["None."]
    return [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows),
    ]


def _where(pairs: list[tuple[str, str | None]]) -> str:
    """`<run> <card>` for each pair, once each, in order."""
    return ", ".join(dict.fromkeys(f"{run} {card or '-'}" for run, card in pairs))


def _usd(value: float | None) -> str:
    return "unknown" if value is None else f"${round(value, 2) + 0.0:.2f}"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _who(row: dict[str, Any]) -> str:
    """The agent a row names: `orchestrator`, or its role and agent id."""
    if row.get("role") == "orchestrator":
        return "orchestrator"
    return f"{row.get('role')} {row.get('agent')}"


def _n(count: int, noun: str) -> str:
    """`1 run`, `2 runs`."""
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _rate(agreed: int, compared: int) -> str:
    return f"{100 * agreed / compared:.0f}%" if compared else "-"


def _tier(agent: dict[str, Any]) -> str:
    """`<model family>/<effort>`, the pair a tier answer names."""
    model = agent.get("model") or ""
    match = re.search(r"claude-([a-z]+)", model)
    family = match.group(1) if match else model or "?"
    return f"{family}/{agent.get('effort') or '?'}"


def _counted(title: str, groups: dict[str, list[tuple[str, str | None]]]) -> list[str]:
    """A window table: each group's count and the runs and cards it counts; nothing
    when there is nothing to count."""
    if not groups:
        return []
    return [
        "",
        *_table(
            [title, "count", "runs and cards"],
            [[name, len(p), _where(p)] for name, p in sorted(groups.items())],
        ),
    ]


def _older(logs: list[Log], key: str) -> list[str]:
    """Logs derived before the log carried `key`."""
    return [log.run_id for log in logs if key not in log.summary]


def _older_note(logs: list[Log], key: str, what: str) -> list[str]:
    older = _older(logs, key)
    if not older:
        return []
    return [
        "",
        (
            f"Not in the logs of {_n(len(older), 'run')}, derived before the log carried "
            f"{what} "
            f"(`autonomouse log derive <run>` derives one again): {', '.join(older)}."
        ),
    ]


# ----------------------------------------------------------------- sections --


def _runs(logs: list[Log]) -> list[str]:
    rows = []
    for log in logs:
        s = log.summary
        roles = Counter(a["role"] for a in s.get("agents", []))
        agents = ", ".join(
            f"{roles[r]} {r}{'s' if roles[r] > 1 else ''}" for r in ROLES if roles[r]
        )
        cards = [a["card"] for a in s.get("agents", []) if a["role"] == "worker"]
        cards += [p["card"] for p in s.get("prs", [])]
        rows.append(
            [
                log.run_id,
                ", ".join(dict.fromkeys(c for c in cards if c)),
                s.get("project"),
                s.get("session_id"),
                agents or "none",
                s.get("reason"),
            ]
        )
    empty = [log for log in logs if log.summary.get("reason")]
    reasons = Counter(log.summary["reason"] for log in empty)
    return [
        *_table(["run", "cards", "project", "session", "agents", "empty log"], rows),
        "",
        f"Window: {_n(len(logs), 'run')}, {len(logs) - len(empty)} with a session read"
        + "".join(f"; {n} {reason}" for reason, n in sorted(reasons.items()))
        + ".",
    ]


def _cards_done(logs: list[Log], board: dict[str, dict[str, Any]]) -> list[str]:
    rows = []
    for log in logs:
        last = {t["card"]: t for t in board[log.run_id]["transitions"]}
        rows += sorted(
            (
                [log.run_id, t["card"], t["at"]]
                for t in last.values()
                if t["to"] == "done"
            ),
            key=lambda row: row[2],
        )
    return [
        (
            "From the board's card histories: each card whose last stack move in the run "
            "took it to `done`, at the time of that move."
        ),
        "",
        *_table(["run", "card", "at"], rows),
        "",
        f"Window: {_n(len(rows), 'card')} done.",
    ]


def _decisions(logs: list[Log], board: dict[str, dict[str, Any]]) -> list[str]:
    rows = []
    kinds: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    answered: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for log in logs:
        for d in board[log.run_id]["decisions"]:
            by = d["answered_by"] or "-"
            rows.append(
                [
                    log.run_id,
                    d["card"],
                    d["decision"],
                    d["kind"],
                    by,
                    d["at"],
                    d["answered_at"],
                ]
            )
            kinds[d["kind"]].append((log.run_id, d["card"]))
            answered[by].append((log.run_id, d["card"]))
    return [
        (
            "From the board's cards: each decision whose card's history row into "
            "`decision_needed`, written when it was asked, names the run. The kind is read "
            "from the question's words; `answered by` is the run id of the history row "
            "that answered it: a run's id, `telegram` (the human's reply), `screen` (the "
            "board screen), `human` (an agent session acting for the human), `manual` (the "
            "command line) or a planning thread's id."
        ),
        "",
        *_table(
            ["run", "card", "decision", "kind", "answered by", "asked", "answered"],
            rows,
        ),
        "",
        f"Window: {_n(len(rows), 'decision')} raised.",
        *_counted("kind", kinds),
        *_counted("answered by", answered),
    ]


def _iterations(logs: list[Log], board: dict[str, dict[str, Any]]) -> list[str]:
    rows = []
    causes: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for log in logs:
        for i in board[log.run_id]["iterations"]:
            rows.append([log.run_id, i["card"], i["iteration"], i["cause"], i["at"]])
            causes[i["cause"]].append((log.run_id, i["card"]))
    return [
        (
            "From the board's card histories: each `card progress` row of the run that "
            "raised the card's iteration, and the phase the card was in, its cause."
        ),
        "",
        *_table(["run", "card", "iteration", "cause", "at"], rows),
        "",
        f"Window: {_n(len(rows), 'iteration')}.",
        *_counted("cause", causes),
    ]


def _cost(logs: list[Log]) -> list[str]:
    per_run = []
    total = 0.0
    no_cost: list[str] = []
    by_model: dict[str, float] = defaultdict(float)
    cards: dict[str, dict[str, Any]] = {}
    tiers: dict[tuple[str, str], dict[str, Any]] = {}
    for log in logs:
        cost = log.summary.get("cost") or {}
        agents = log.summary.get("agents", [])
        attributed = sum(a["cost_usd"] for a in agents if a["cost_usd"] is not None)
        run_total = cost.get("total_usd")
        if run_total is None:
            no_cost.append(log.run_id)
        else:
            total += run_total
        models = cost.get("by_model") or {}
        for model, usd in models.items():
            by_model[model] += usd or 0.0
        per_run.append(
            [
                log.run_id,
                ", ".join(dict.fromkeys(a["card"] for a in agents if a["card"])),
                _usd(run_total),
                ", ".join(f"{m} {_usd(c)}" for m, c in models.items()),
                _usd(attributed),
                _usd(None if run_total is None else run_total - attributed),
            ]
        )
        for a in agents:
            groups = [tiers.setdefault((_tier(a), a["role"]), _cost_group())]
            if a["card"]:
                groups.append(cards.setdefault(a["card"], _cost_group()))
            for group in groups:
                group["agents"] += 1
                group["where"].append((log.run_id, a["card"]))
                if a["cost_usd"] is None:
                    group["unknown"] += 1
                    group["no_rate"].update(dict.fromkeys(_no_rate(a, log)))
                else:
                    group["usd"] += a["cost_usd"]
    card_rows = [
        [
            card,
            ", ".join(dict.fromkeys(r for r, _ in g["where"])),
            g["agents"],
            _usd(None if g["unknown"] == g["agents"] else g["usd"]),
            _without(g),
        ]
        for card, g in sorted(cards.items())
    ]
    tier_rows = [
        [
            tier,
            role,
            g["agents"],
            _usd(None if g["unknown"] == g["agents"] else g["usd"]),
            _without(g),
            _where(g["where"]),
        ]
        for (tier, role), g in sorted(tiers.items())
    ]
    window = (
        f"Window: {_usd(total)} over {_n(len(logs) - len(no_cost), 'run')} with a cost"
        + (f"; no cost recorded for {', '.join(no_cost)}" if no_cost else "")
        + ". By model: "
        + (", ".join(f"{m} {_usd(c)}" for m, c in sorted(by_model.items())) or "none")
        + "."
    )
    return [
        (
            "An agent's cost is priced from the token usage of its own transcript "
            "messages; what no agent's messages hold (Claude Code's own calls, web "
            "searches) is the run's, not attributed. An agent on a model with no rates has "
            "no cost, and its row names that model. A card's or a tier's cost is the sum "
            "over its agents that have one, and unknown when none has."
        ),
        *_older_priced(logs),
        *_rated_since(logs),
        "",
        "### Per run",
        "",
        *_table(
            [
                "run",
                "cards",
                "total",
                "by model",
                "attributed to agents",
                "not attributed",
            ],
            per_run,
        ),
        "",
        window,
        "",
        "### Per card",
        "",
        *_table(["card", "runs", "agents", "cost", "agents without a cost"], card_rows),
        "",
        "### Per tier",
        "",
        *_table(
            [
                "tier",
                "role",
                "agents",
                "cost",
                "agents without a cost",
                "runs and cards",
            ],
            tier_rows,
        ),
    ]


def _older_priced(logs: list[Log]) -> list[str]:
    """Logs derived before each agent was priced from its transcript: there an agent has
    a cost only when it was the only agent of its run on its model."""
    older = [
        log.run_id
        for log in logs
        if "unpriced_models" not in (log.summary.get("cost") or {})
        and log.summary.get("agents")
    ]
    if not older:
        return []
    return [
        "",
        (
            f"The logs of {_n(len(older), 'run')} were derived before each agent was priced "
            "from its transcript; there an agent has a cost only when it was the only "
            "agent of its run on its model (`autonomouse log derive <run>` derives one "
            f"again): {', '.join(older)}."
        ),
    ]


def _rated_since(logs: list[Log]) -> list[str]:
    """Logs that name a model with no rate which the rate table now prices: their agents
    on it get a cost when the log is derived again."""
    stale = [
        (log.run_id, [m for m in _unpriced(log) if price.rates_of(m) is not None])
        for log in logs
    ]
    stale = [(run, models) for run, models in stale if models]
    if not stale:
        return []
    return [
        "",
        (
            f"The logs of {_n(len(stale), 'run')} name a model as having no rate that the "
            "rate table prices now; their agents on it have no cost here until the log is "
            "derived again (`autonomouse log derive <run>`): "
            + ", ".join(f"{run} ({', '.join(models)})" for run, models in stale)
            + "."
        ),
    ]


def _unpriced(log: Log) -> list[str]:
    """The models the run's log names as having no rate when it was derived."""
    return (log.summary.get("cost") or {}).get("unpriced_models") or []


def _no_rate(agent: dict[str, Any], log: Log) -> list[str]:
    """The models an agent without a cost had no rate for: its own model when its log
    names that one, else every model its log names (none for a log derived before each
    agent was priced)."""
    named = _unpriced(log)
    return [agent["model"]] if agent.get("model") in named else named


def _without(group: dict[str, Any]) -> str:
    """How many of a group's agents have no cost, and the models that had no rate."""
    if not group["no_rate"]:
        return str(group["unknown"])
    return f"{group['unknown']}, no rate for {', '.join(group['no_rate'])}"


def _cost_group() -> dict[str, Any]:
    return {"agents": 0, "usd": 0.0, "unknown": 0, "no_rate": {}, "where": []}


def _clock(logs: list[Log]) -> tuple[list[list[Any]], int]:
    rows = []
    seen = set()
    for log in logs:
        for c in log.summary.get("clock") or []:
            rows.append([log.run_id, c["card"], c["class"], clock_text(c)])
            seen.add(json.dumps(c, sort_keys=True))  # two runs can log one time
    return rows, len(seen)


def _unread_board(logs: list[Log]) -> list[str]:
    unread = [log.run_id for log in logs if log.summary.get("clock", []) is None]
    if not unread:
        return []
    return [
        "",
        (
            f"Not checked for {_n(len(unread), 'run')}, whose board could not be read when "
            f"the log was derived: {', '.join(unread)}."
        ),
    ]


def _breaches(logs: list[Log], edits: list[list[Any]] | None) -> list[str]:
    clock, clock_count = _clock(logs)
    classes = [
        (
            "hand edits of cards",
            "not checked" if edits is None else len(edits),
            edits or [],
        ),
        ("times on the board that cannot be true", clock_count, clock),
    ]
    return [
        *_table(
            ["class", "count", "runs and cards"],
            [
                [name, n, _where([(r[0], r[1]) for r in rows])]
                for name, n, rows in classes
            ],
        ),
        "",
        "### Hand edits of cards",
        "",
        (
            "From the board's git history: each change to a card file in a commit that "
            "appended no history row, or rewrote an earlier one, which no `autonomouse` "
            "command does. `writes` names the fields the change set. The run is the one "
            "the commit's message names, else the run of the card's project whose start "
            "and end hold the commit's time. A hand edit committed together with a "
            "command's write to the same card does not show. `git show <commit>` in the "
            "board shows the change."
        ),
        "",
        *(
            ["Not checked: the board's git history could not be read."]
            if edits is None
            else _table(["run", "card", "commit", "at", "writes"], edits)
        ),
        "",
        "### Times on the board that cannot be true",
        "",
        (
            "History rows out of order, history rows later than their run's last transcript "
            "message, and decisions answered before they were asked, each on the log of the "
            "run that wrote it; one time can show on two runs' logs."
        ),
        "",
        *_table(["run", "card", "class", "times"], clock),
        *_older_note(logs, "clock", "board times"),
        *_unread_board(logs),
    ]


def _waits(logs: list[Log]) -> list[str]:
    rows = []
    roles: dict[str, dict[str, Any]] = {}
    for log in logs:
        for a in log.summary.get("agents", []):
            rows.append(
                [
                    log.run_id,
                    a["card"],
                    _who(a),
                    _duration(a["longest_idle_s"]),
                    a["longest_idle_from"],
                ]
            )
            role = roles.setdefault(a["role"], {"agents": 0, "longest": None})
            role["agents"] += 1
            if role["longest"] is None or a["longest_idle_s"] > role["longest"][0]:
                role["longest"] = (a["longest_idle_s"], log.run_id, a["card"], _who(a))
    window = [
        [
            role,
            g["agents"],
            _duration(g["longest"][0]),
            f"{g['longest'][1]} {g['longest'][2] or '-'} {g['longest'][3]}",
        ]
        for role, g in sorted(roles.items(), key=lambda kv: ROLES.index(kv[0]))
    ]
    return [
        (
            "From the transcripts' timestamps: each agent's longest time between two of "
            "its messages, and when it began."
        ),
        "",
        *_table(["run", "card", "agent", "longest idle", "idle from"], rows),
        "",
        "Window, by role:",
        "",
        *_table(["role", "agents", "longest idle", "run, card, agent"], window),
    ]


def _prs(logs: list[Log]) -> list[str]:
    rows = []
    states: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    for log in logs:
        for p in log.summary.get("prs", []):
            rows.append([log.run_id, p["card"], p["state"], p["url"]])
            states[p["state"]].append((log.run_id, p["card"]))
    return [
        "The state `gh` gave when each log was derived.",
        "",
        *_table(["run", "card", "state", "PR"], rows),
        "",
        f"Window: {_n(len(rows), 'PR state')}.",
        *_counted("state", states),
    ]


def _jev(logs: list[Log]) -> list[str]:
    rows = []
    answers = []
    for log in logs:
        for j in log.summary.get("jev") or []:
            rows.append(
                [
                    log.run_id,
                    j["card"] or "(wake)",
                    j["question"],
                    j["source"],
                    j["answer"],
                    j["confidence"],
                    j["applied"],
                ]
            )
            if j["answer"] is not None:
                answers.append((log, j))
    jev = [(log, j) for log, j in answers if j["source"] in ("jev", "fallback")]
    orchestrator = [(log, j) for log, j in answers if j["source"] == "orchestrator"]
    compared = []
    by_question: dict[str, Counter] = defaultdict(Counter)
    for log, j in jev:
        by_question[j["question"]]["jev"] += 1
        same = [
            (other, o)
            for other, o in orchestrator
            if (o["card"], o["question"]) == (j["card"], j["question"])
        ]
        if not same:
            continue
        other, o = min(
            same, key=lambda p: (abs(p[0].started - log.started), p[0].started)
        )
        agree = o["answer"] == j["answer"]
        compared.append(
            [
                log.run_id,
                j["card"] or "(wake)",
                j["question"],
                f"{j['answer']} ({j['source']})",
                o["answer"],
                other.run_id,
                "yes" if agree else "no",
            ]
        )
        by_question[j["question"]]["compared"] += 1
        by_question[j["question"]]["agreed"] += agree
    agreed = sum(1 for r in compared if r[-1] == "yes")
    sources = Counter(r[3] for r in rows)
    return [
        (
            "Every answer in the runs' records. `answer` is the answerer's pick; `applied` is "
            "what was done."
        ),
        "",
        *_table(
            ["run", "card", "question", "source", "answer", "confidence", "applied"],
            rows,
        ),
        *_older_note(logs, "jev", "Jev answers"),
        "",
        "### Jev against the orchestrator",
        "",
        (
            "Each answer Jev gave (source `jev`, or `fallback` when Jev's answer was below its "
            "floor), next to the orchestrator's answer to the same question on the same card "
            "in the window, from the run nearest in time."
        ),
        "",
        *_table(
            [
                "run",
                "card",
                "question",
                "Jev's answer",
                "orchestrator's answer",
                "orchestrator's run",
                "agree",
            ],
            compared,
        ),
        "",
        f"Window: {_n(len(rows), 'answer')} ("
        + (", ".join(f"{s} {n}" for s, n in sorted(sources.items())) or "none")
        + f"); {len(jev)} by Jev, {len(compared)} compared with the orchestrator, "
        f"{agreed} agreeing: agreement rate {_rate(agreed, len(compared))}.",
        "",
        *_table(
            ["question", "Jev answers", "compared", "agreed", "agreement rate"],
            [
                [
                    q,
                    c["jev"],
                    c["compared"],
                    c["agreed"],
                    _rate(c["agreed"], c["compared"]),
                ]
                for q, c in sorted(by_question.items())
            ],
        ),
    ]


# ------------------------------------------------------------------- report --


def build_review(
    logs: list[Log],
    *,
    cards: list[Card],
    edits: list[list[Any]] | None,
    days: int,
    since: datetime,
    now: datetime,
    folder: Path,
) -> str:
    """The review of `logs`, the board's `cards` and the hand edits its git history shows
    (`hand_edits`; None when it could not be read) as markdown."""
    board = {log.run_id: board_rows(cards, log.run_id) for log in logs}
    sections = [
        ("Runs", _runs(logs)),
        ("Cards done", _cards_done(logs, board)),
        ("Decisions", _decisions(logs, board)),
        ("Iterations", _iterations(logs, board)),
        ("Cost", _cost(logs)),
        ("Rule breaches", _breaches(logs, edits)),
        ("Waits", _waits(logs)),
        ("PR state at finish", _prs(logs)),
        ("Jev and the orchestrator", _jev(logs)),
    ]
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    out = [
        f"# Behaviour review — {now.date().isoformat()}",
        "",
        (
            f"Written {now.strftime(stamp)} from the behaviour logs of {_n(len(logs), 'run')} that "
            f"started in the last {_n(days, 'day')} (at or after {since.strftime(stamp)}), read "
            f"from `{folder}`, and the board's cards and git history. Times are UTC. A run's "
            "transcript is `<session>.jsonl` in its "
            "folder under `~/.claude/projects/`, and a sub-agent's is "
            "`<session>/subagents/agent-<agent>.jsonl` beside it."
        ),
    ]
    for title, body in sections:
        out += ["", f"## {title}", "", *body]
    return "\n".join(out) + "\n"


def write_review(root: Path, days: int, now: datetime | None = None) -> Path:
    """Review the behaviour logs of the runs of the last `days` days and write
    `reviews/behaviour-<date>.md` under the board at `root`."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    folder = logs_dir(root)
    try:
        cards = card_list(root)
    except BoardValidationError as e:
        raise ValueError(f"the board's cards cannot be read — {e}") from e
    logs = load_logs(folder, since)
    text = build_review(
        logs,
        cards=cards,
        edits=hand_edits(root, logs, since),
        days=days,
        since=since,
        now=now,
        folder=folder,
    )
    path = root / "reviews" / f"behaviour-{now.date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
