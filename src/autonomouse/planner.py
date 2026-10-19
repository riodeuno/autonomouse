"""`autonomouse plan step`: run the planner on every thread with an unanswered human message.

For each such thread (`Thread.unanswered`), oldest thread first:

0. Jev judges the kind of the thread's next planner session (`PLANNER_SESSION` in
   `autonomouse.jev.questions`): `complex`, `simple` or `collect-user-intent`, a session that
   only collects the human's intent. The config's `planner` table maps the kind to the
   session's model and reasoning effort (`autonomouse.config.planner_tiers`), which the
   session runs with (`--model`, `--effort`). When `jev.enabled` is off, the request fails
   or Jev's confidence is below the question's floor, the session runs at the `complex`
   tier. The thread's `tiers` list records Jev's answer, the kind applied and the tier, one
   entry per thread stepped (`judge`), and the session of steps 1 to 3 runs at that tier.
1. A thread with no `session_id` gets a new planner session: `claude -p` with a session id
   chosen here, recorded on the thread before claude starts, on the thread's first message.
2. A thread with a `session_id` resumes it (`claude -p --resume <session_id>`) with the
   human messages the planner has not answered.
3. A resume that exits non-zero without printing a result failed (claude prints `No
   conversation found with session ID` when the transcript is gone). The thread gets a
   fresh session instead, whose prompt carries every message on the thread: the thread
   record, not the session, is the memory of record.

The planner answers only through `autonomouse plan say / cards / close`, which write the
thread, and sends each message to the human with `autonomouse notify` (`docs/planner.md`,
its charter); this module reads the thread again after claude exits and reports what changed.
Each session is started in the folder that holds the charters (`autonomouse.home`: the
repository checkout, or the installed package) with the secrets file
(`autonomouse.config.secrets_path`) loaded, like the orchestrator (`autonomouse.controller`); claude's printed result and its stderr go to
`<state dir>/<thread-id>-<first 8 of session id>-<n>.result.json` and `.stderr.log`.

Before any of that, `plan step` runs `autonomouse notify poll` (`autonomouse.inbox`), so a
Telegram reply is on its thread before the planner reads the thread. With Telegram off
(`notify.telegram = false`) it polls nothing, and the human answers on the board screen.

Only one `plan step` runs at a time: a second one finds the lock
`<state dir>/plan-step.lock` held, prints that, and does nothing.

`autonomouse project new` with no terminal ends with `brief`: it opens the new project's
brief thread and runs the planner on it once, the way a plan step would, so the planner's
first question about the project's brief (`docs/planner.md`, "The brief") reaches the human
on Telegram at once. On a terminal it ends with `setup_session` instead: an interactive
claude in the project's repository, on a two-line instruction (`setup_prompt`), at the
model and effort the human chose (`SETUP_MODEL`, `SETUP_EFFORT`), which writes the
project's goal, its brief and the human's instructions for it with the human, on the
brief thread joined as below.

`autonomouse plan join <thread-id>` (`join`) and `autonomouse plan new <project>` (`new`)
put the human into a planner session live: an interactive `claude` in that folder, on
the thread's session (`--resume`), or on a new one for a new thread or one no planner has
started, at the `complex` tier of the config's `planner` table. The thread is marked
`human_joined` with the pid of that claude (`thread_join`), a claim `plan step` leaves
alone; when claude exits, `thread_leave` marks it `waiting_planner` with a message saying
the human left (a thread the planner closed stays closed), so the next `plan step` resumes the session unattended. The mark is taken while
holding `plan-step.lock`, so it never lands while a step runs the planner on the same
session; a join whose pid is gone is released by the next step (`release_dead_join`).
Claude is started first, for its pid, and held (`_EXEC_WHEN_JOINED`) until the thread's
claim names that pid, so nothing claude runs ever reads the thread with another pid on it.

Every claude process the planner starts gets a run record of `kind` `planner`
(`autonomouse.board.runs`), naming its thread and the thread's project, so its cost is on
the board: `plan step` writes one right after claude starts and ends it with the printed
result when claude exits (a failed resume and the fresh session after it are two
processes, so two records); a live session gets one once claude runs, ended with no
result, since an interactive session prints none. Cards keep the thread id as their
`run_id` (`docs/planner.md`); the record's `thread` links the cost to them. A record that
cannot be written is printed and never stops the planner, since the thread is the memory
of record. Nothing here commits the board: the record rides in the next board commit.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from autonomouse.board.brief import brief_path
from autonomouse.board.commands import CommandError
from autonomouse.board.history import now_iso
from autonomouse.board.plans import (
    Message,
    Thread,
    ThreadStatus,
    load_thread,
    release_dead_join,
    thread_add_tier,
    thread_close,
    thread_given,
    thread_join,
    thread_join_pid,
    thread_leave,
    thread_list,
    thread_open,
    thread_set_session,
)
from autonomouse.board.runs import RunKind, new_run_id, run_end, run_start
from autonomouse.config import planner_tiers
from autonomouse.controller import _EXEC_WITH_SECRETS, WATCHDOG_ENV, _secrets_in_environ
from autonomouse.home import Home
from autonomouse.jev.client import JevError
from autonomouse.jev.client import ask as jev_ask
from autonomouse.jev.questions import PLANNER_SESSION, planner_state, questions_body
from autonomouse.usage_limit import fallback_tier, session_limit

Ask = Callable[..., Mapping[str, Any]]


def _commands(thread: Thread, home: Home) -> str:
    cli = home.cli
    return (
        f"Your charter is {home.charter('planner.md')}; read it first. Your thread's "
        f"commands:\n"
        f'- `{cli} plan say {thread.id} "<text>"` writes your message (a question, '
        f"or what you did) on the thread.\n"
        f'- `{cli} notify --about {thread.id} "<text>"` sends the same text to the '
        f"human on Telegram, where the human replies to this thread. Send it only while "
        f"`{cli} config show` does not print `notify.telegram = false`; with Telegram off "
        f"the human reads and answers the thread on the board screen. Your turn's final "
        f"text reaches nobody.\n"
        f"- `{cli} plan cards {thread.id} <card-id>...` records the cards you wrote "
        f"on the thread.\n"
        f"- `{cli} plan brief {thread.id} <file>` writes <file> as the project's "
        f"brief, refusing one that lacks one of the seven headings or a goal or "
        f"intent sentence.\n"
        f'- `{cli} plan close {thread.id} "<closing message>"` closes the thread.\n'
        f"- `{cli} plan show {thread.id}` prints the whole thread."
    )


def _transcript(messages: list[Message]) -> str:
    return "\n".join(f"[{m.at}] {m.from_.value}: {m.text}" for m in messages)


def first_prompt(thread: Thread, home: Home) -> str:
    return (
        f"You are the planner for planning thread {thread.id} on project "
        f"{thread.project}.\n{_commands(thread, home)}\n\n"
        f"The human's request:\n{thread.messages[0].text}"
    )


def resume_prompt(thread: Thread, unanswered: list[Message]) -> str:
    return (
        f"The human replied on planning thread {thread.id}:\n"
        + "\n".join(m.text for m in unanswered)
    )


def fresh_prompt(thread: Thread, home: Home) -> str:
    return (
        f"You are the planner for planning thread {thread.id} on project "
        f"{thread.project}. An earlier planner session held this conversation and cannot "
        f"be resumed; the thread record is the memory of record. Read every message on "
        f"it first, then continue from the last one.\n{_commands(thread, home)}\n\n"
        f"Cards already recorded on the thread: "
        f"{', '.join(thread.cards_written) or 'none'}.\n\n"
        f"The thread so far:\n{_transcript(thread.messages)}"
    )


@dataclass
class Session:
    code: int
    result: dict | None
    stderr_path: Path


def _record_start(
    root: Path,
    thread: Thread,
    *,
    session_id: str,
    pid: int,
    model: str,
    out: Callable[[str], None],
) -> str | None:
    """Write the `planner` run record of the claude process `pid` just started on
    `thread`; its id, or None when the write failed, which is printed and never stops
    the planner."""
    try:
        return run_start(
            root,
            run_id=new_run_id(),
            kind=RunKind.PLANNER,
            project=thread.project,
            thread=thread.id,
            session_id=session_id,
            pid=pid,
            model=model,
        ).id
    except Exception as e:  # noqa: BLE001 — the thread, not the record, is the memory
        out(f"{thread.id}: run record not written: {type(e).__name__}: {e}")
        return None


def _record_end(
    root: Path,
    run_id: str | None,
    *,
    thread_id: str,
    result: dict | None,
    summary: str,
    out: Callable[[str], None],
) -> None:
    """End the run record `_record_start` wrote (none when it wrote none); a failure is
    printed and never stops the planner."""
    if run_id is None:
        return
    try:
        from autonomouse.board.runs import save_run

        run = run_end(root, run_id, result=result, summary=summary)
        hit = session_limit(run.session_id, run.model or "",
                            since=datetime.fromisoformat(run.started_at)) if run.session_id else None
        if hit:
            run.usage_limit = hit
            save_run(root, run)
    except Exception as e:  # noqa: BLE001 — the thread, not the record, is the memory
        out(f"{thread_id}: run record {run_id} not ended: {type(e).__name__}: {e}")


def _run_claude(
    prompt: str,
    *,
    root: Path,
    thread: Thread,
    session_id: str,
    resume: bool,
    model: str,
    effort: str,
    claude: str,
    home: Home,
    secrets: Path,
    result_path: Path,
    stderr_path: Path,
    out: Callable[[str], None],
) -> Session:
    argv = [claude, "-p", prompt, "--resume" if resume else "--session-id", session_id]
    argv += [
        "--model",
        model,
        "--effort",
        effort,
        "--dangerously-skip-permissions",
        "--output-format",
        "json",
    ]
    # CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0, as the wake's orchestrator session gets it
    # (autonomouse.controller): unattended mode (`-p`) otherwise gives up on background
    # workers (an Agent tool call with run_in_background) 600000ms after the planner's
    # last turn and kills them mid-probe. 0 means no ceiling: the session waits for every
    # sub-agent it starts.
    with open(result_path, "w") as stdout, open(stderr_path, "w") as stderr:
        proc = subprocess.Popen(
            ["bash", "-c", _EXEC_WITH_SECRETS, str(secrets), *argv],
            cwd=home.root,
            env={
                **os.environ,
                **WATCHDOG_ENV,
                "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
            },
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
        )
    run_id, result = None, None
    try:
        try:
            # The wrapper execs claude, so its pid is claude's.
            run_id = _record_start(
                root, thread, session_id=session_id, pid=proc.pid, model=model, out=out
            )
            proc.wait()
        except BaseException:
            proc.kill()  # as subprocess.run did: claude never outlives the step
            proc.wait()
            raise
        try:
            parsed = json.loads(result_path.read_text())
        except json.JSONDecodeError:
            parsed = None
        result = parsed if isinstance(parsed, dict) else None
    finally:
        _record_end(
            root,
            run_id,
            thread_id=thread.id,
            result=result,
            summary=f"planner step on {thread.id}: exit {proc.returncode}",
            out=out,
        )
    return Session(code=proc.returncode, result=result, stderr_path=stderr_path)


def _first_line(path: Path) -> str:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return lines[0] if lines else "no stderr"


def judge(
    thread: Thread,
    *,
    tiers: Mapping[str, tuple[str, str]],
    jev: bool,
    ask: Ask,
    secrets: Path,
) -> dict[str, Any]:
    """The tier the thread's next planner session runs at, as the thread's `tiers` entry:
    Jev's `answer`, `confidence` and `probabilities` (null when Jev gave no usable answer),
    the kind `applied`, its `tier` (`model/effort` from `tiers`), the `source` (`jev`, or
    `fallback` when the kind is the `complex` fallback) and the `reason` for a fallback.

    Jev is asked only when `jev` (`jev.enabled`) is on, with `secrets` loaded into this
    process's environment for the request, as a wake asks it (`TYPESAFE_API_KEY`)."""
    if not jev:
        verdict = PLANNER_SESSION.decide(None)
        reason = "jev.enabled is off"
    else:
        try:
            with _secrets_in_environ(secrets):
                answers = ask(
                    planner_state(thread.to_dict(), [m.text for m in thread.unanswered()]),
                    questions_body([PLANNER_SESSION]),
                    timeout=30.0,
                )
            verdict = PLANNER_SESSION.decide(answers.get(PLANNER_SESSION.key))
            reason = verdict.fallback_reason
        except (JevError, OSError, ValueError, KeyError) as exc:
            verdict = PLANNER_SESSION.decide(None)
            reason = f"Jev request failed: {exc!r}"
    model, effort = tiers[verdict.applied]
    return {
        "at": now_iso(),
        "source": "jev" if reason is None else "fallback",
        "answer": verdict.pick,
        "confidence": verdict.confidence,
        "probabilities": None if verdict.probabilities is None else dict(verdict.probabilities),
        "applied": verdict.applied,
        "reason": reason,
        "tier": f"{model}/{effort}",
    }


def _process(
    root: Path,
    thread: Thread,
    *,
    tiers: Mapping[str, tuple[str, str]],
    jev: bool,
    ask: Ask,
    claude: str,
    home: Home,
    secrets: Path,
    state_dir: Path,
    out: Callable[[str], None],
) -> bool:
    """Run the planner on one thread; True when its session exited cleanly."""
    entry = judge(thread, tiers=tiers, jev=jev, ask=ask, secrets=secrets)
    thread_add_tier(root, thread.id, entry)
    model, effort = fallback_tier(root, *entry["tier"].split("/"))
    entry["tier"] = f"{model}/{effort}"
    why = entry["reason"] or f"Jev's answer at confidence {entry['confidence']:.2f}"
    out(f"{thread.id}: a {entry['applied']} session, on {entry['tier']} ({why})")
    attempt = 0

    def run(prompt: str, session_id: str, resume: bool) -> Session:
        nonlocal attempt
        attempt += 1
        stem = state_dir / f"{thread.id}-{session_id[:8]}-{attempt}"
        return _run_claude(
            prompt,
            root=root,
            thread=thread,
            session_id=session_id,
            resume=resume,
            model=model,
            effort=effort,
            claude=claude,
            home=home,
            secrets=secrets,
            result_path=stem.with_suffix(".result.json"),
            stderr_path=stem.with_suffix(".stderr.log"),
            out=out,
        )

    def fresh(prompt: str) -> Session:
        session_id = str(uuid.uuid4())
        thread_set_session(root, thread.id, session_id)
        out(f"{thread.id}: new planner session {session_id}")
        return run(prompt, session_id, resume=False)

    # every prompt below is built from `thread` as read here; a human message written
    # from here on stays unanswered for the next step, however the session ends
    thread_given(root, thread.id, len(thread.messages))
    if thread.session_id is None:
        session = fresh(first_prompt(thread, home))
    else:
        out(f"{thread.id}: resuming planner session {thread.session_id}")
        session = run(resume_prompt(thread, thread.unanswered()), thread.session_id, True)
        if (session.code != 0 and session.result is None
                and not session_limit(thread.session_id, model)):
            out(
                f"{thread.id}: resume of session {thread.session_id} failed "
                f"({_first_line(session.stderr_path)}); starting a fresh session "
                f"from the thread's messages"
            )
            session = fresh(fresh_prompt(thread, home))

    after = load_thread(root, thread.id)
    state = f"status {after.status.value}"
    if after.unanswered():
        state += "; the planner left the human's message unanswered"
    if after.cards_written:
        state += f"; cards {', '.join(after.cards_written)}"
    ok = session.code == 0 and session.result is not None
    out(
        f"{thread.id}: planner exited {session.code}"
        + ("" if session.result is not None else ", printed no result")
        + f"; {state}"
    )
    return ok


def step(
    root: Path,
    *,
    config: Mapping[str, Any],
    claude: str,
    home: Home,
    secrets: Path,
    state_dir: Path,
    poll: Callable[[Callable[[str], None]], None] | None = None,
    ask: Ask = jev_ask,
    out: Callable[[str], None] = print,
) -> int:
    """Process every thread with an unanswered human message. Exit code: 0 when every
    planner session exited cleanly (or nothing was pending), 1 otherwise.

    `config` is the effective config: its `planner` table maps each kind of session to its
    tier (`planner_tiers`, which raises `ValueError` on a bad value before anything runs),
    and `jev.enabled` says whether Jev (`ask`) judges each session's kind (`judge`).

    `poll` (`autonomouse notify poll`, bound by the CLI) runs first, under the same lock,
    so the human's Telegram replies are on their threads before the planner reads them
    and two plan steps never route the same message twice. A poll that raises is printed
    and the planner still runs."""
    tiers = planner_tiers(config)
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "plan-step.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            out("another plan step is running; nothing done")
            return 0
        if poll is not None:
            try:
                poll(out)
            except Exception as e:  # noqa: BLE001 — the planner runs whatever the poll did
                out(f"poll: failed: {type(e).__name__}: {e}")
        for thread in thread_list(root):
            if thread.status is ThreadStatus.HUMAN_JOINED:
                if release_dead_join(root, thread.id):
                    out(f"{thread.id}: the live join's process is gone; released it")
                else:
                    out(f"{thread.id}: the human is in it live; left alone")
        pending = [t for t in thread_list(root) if t.unanswered()]
        if not pending:
            out("no thread waits for the planner")
            return 0
        failed = 0
        for thread in pending:
            ok = _process(
                root,
                thread,
                tiers=tiers,
                jev=bool(config["jev"]["enabled"]),
                ask=ask,
                claude=claude,
                home=home,
                secrets=secrets,
                state_dir=state_dir,
                out=out,
            )
            failed += not ok
        return 1 if failed else 0


# ------------------------------------------------------------------ the brief --

BRIEF_TEXT = (
    "I created this project with `autonomouse project new`. Its brief comes first: ask "
    'me for it as your charter\'s section "The brief" says, then write it with '
    "`autonomouse plan brief <thread-id> <file>`."
)


def brief(
    root: Path,
    project: str,
    *,
    config: Mapping[str, Any],
    claude: str,
    home: Home,
    secrets: Path,
    state_dir: Path,
    ask: Ask = jev_ask,
    out: Callable[[str], None] = print,
) -> tuple[Thread, bool]:
    """`autonomouse project new`'s last step: open `project`'s brief thread, whose request
    is `BRIEF_TEXT`, and run the planner on it at once, so its first question reaches the
    human while the human is still at the terminal. The thread is opened holding
    `plan-step.lock`, so no plan step runs a second planner on it; while a plan step holds
    the lock, the thread is opened without it and the next plan step starts the planner.
    The session runs at the tier a plan step would give it (`config` and `ask` as in
    `step`). Returns the thread as the planner left it, and False when the planner's
    session failed (`_process`)."""
    tiers = planner_tiers(config)
    text = BRIEF_TEXT
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "plan-step.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            thread = thread_open(root, project, text)
            out(f"{thread.id}: a plan step is running now; the next one starts the planner")
            return thread, True
        thread = thread_open(root, project, text)
        ok = _process(
            root,
            thread,
            tiers=tiers,
            jev=bool(config["jev"]["enabled"]),
            ask=ask,
            claude=claude,
            home=home,
            secrets=secrets,
            state_dir=state_dir,
            out=out,
        )
    return load_thread(root, thread.id), ok


# ------------------------------------------------------------------ live join --

NEW_TEXT = (
    "I started this thread live in the terminal with `autonomouse plan new`; the "
    "request is in the planner's session."
)

# `_EXEC_WITH_SECRETS`, run only after one line arrives on the pipe whose read end is the
# file descriptor numbered in "$1" (claude's stdin stays the human's terminal). `_live`
# starts the human's claude this way to learn its pid, moves the thread's claim to that
# pid, and then writes the line; so claude never runs before the thread names it. When the
# pipe closes with no line (the claim did not move), the wrapper exits 1 without running
# claude. The pipe is closed before claude starts.
_EXEC_WHEN_JOINED = (
    'fd=$1; shift; read -r go <&"$fd" || exit 1; exec {fd}<&-; ' + _EXEC_WITH_SECRETS
)


def live_prompt(thread: Thread, home: Home, *, started: bool) -> str:
    """The message a live session starts on. `started`: the session already exists and
    holds the conversation (`plan join` on a thread a planner has answered)."""
    live = (
        "The human has joined this session live, in the terminal: the thread's status "
        "is `human_joined` (see your charter for that status)."
    )
    if started:
        return f"{live} They joined planning thread {thread.id} with `plan join`."
    if thread.messages[0].text == NEW_TEXT:
        ask = "They started the thread with `plan new`; ask them what they want planned."
    else:
        ask = (
            "They joined the thread with `plan join` before any planner answered it. "
            f"The thread so far:\n{_transcript(thread.messages)}"
        )
    return (
        f"You are the planner for planning thread {thread.id} on project "
        f"{thread.project}.\n{_commands(thread, home)}\n\n{live} {ask}"
    )


@contextmanager
def _step_lock(state_dir: Path) -> Iterator[None]:
    """Hold `plan-step.lock`, or refuse while a plan step holds it."""
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "plan-step.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommandError(
                "a plan step is running the planner now; join again when it ends"
            ) from None
        yield


def _live(
    root: Path,
    claim: Callable[[], str],
    *,
    model: str,
    effort: str,
    claude: str,
    home: Home,
    secrets: Path,
    state_dir: Path,
    out: Callable[[str], None] = print,
) -> int:
    """Claim a thread (`claim` returns its id) and run the planner on it interactively
    until the human exits; the exit code is claude's."""
    holder = os.getpid()
    with _step_lock(state_dir):
        thread_id = claim()
        thread = thread_join(root, thread_id, holder)
        started = thread.session_id is not None
        if not started:
            thread = thread_set_session(root, thread_id, str(uuid.uuid4()))
    argv = [claude, "--resume" if started else "--session-id", thread.session_id]
    argv += ["--model", model, "--effort", effort, live_prompt(thread, home, started=started)]
    out(f"{thread_id}: joined live, planner session {thread.session_id}")
    return _attend(
        root, thread, holder, argv, cwd=home.root, session_id=thread.session_id, model=model,
        secrets=secrets, out=out, release=lambda pid: thread_leave(root, thread_id, pid),
    )  # fmt: skip


def _attend(
    root: Path,
    thread: Thread,
    holder: int,
    argv: list[str],
    *,
    cwd: Path,
    session_id: str,
    model: str,
    secrets: Path,
    out: Callable[[str], None],
    release: Callable[[int], Thread],
) -> int:
    """Run `argv`, an interactive claude on the human's terminal, in `cwd` until the human
    exits it, on `thread`, which `holder` (this process) has joined: the join's claim moves
    to claude's pid before claude runs, and when claude exits, `release(<the pid the claim
    names>)` ends the join and returns the thread as it left it. The exit code is claude's."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)  # ctrl-c is claude's
    go_read, go_write = os.pipe()
    run_id = None
    try:
        with open(go_write, "w") as go:  # closed with no line: claude never runs
            proc = subprocess.Popen(
                ["bash", "-c", _EXEC_WHEN_JOINED, str(secrets), str(go_read), *argv],
                cwd=cwd,
                pass_fds=(go_read,),
            )
            os.close(go_read)
            thread_join_pid(root, thread.id, old=holder, new=proc.pid)
            holder = proc.pid
            go.write("go\n")  # claude runs from here on
        run_id = _record_start(
            root, thread, session_id=session_id, pid=proc.pid, model=model, out=out
        )
        code = proc.wait()
    finally:
        signal.signal(signal.SIGINT, previous)
        _record_end(
            root,
            run_id,
            thread_id=thread.id,
            result=None,
            summary=(
                f"live planner session on {thread.id}: an interactive session prints no "
                f"result, so its cost is not on this record"
            ),
            out=out,
        )
        after = release(holder)
    out(f"{thread.id}: left the live session; status {after.status.value}")
    return code


def join(root: Path, thread_id: str, **kwargs) -> int:
    """`autonomouse plan join <thread-id>`: see the module docstring."""
    return _live(root, lambda: thread_id, **kwargs)


def new(root: Path, project: str, **kwargs) -> int:
    """`autonomouse plan new <project>`: a new thread, joined live from its start."""
    return _live(root, lambda: thread_open(root, project, NEW_TEXT).id, **kwargs)


# ------------------------------------------------------------ the setup session --

# The live session `autonomouse project new` ends in on a terminal: the human's choice of
# 2026-09-30 (card sys-w103).
SETUP_MODEL = "claude-fable-5-1"
SETUP_EFFORT = "high"

SETUP_LEFT_TEXT = (
    "I left the live session that `autonomouse project new` started in the project's "
    "repository before the brief was written. Ask me for the brief as your charter's "
    'section "The brief" says.'
)
SETUP_DONE_TEXT = (
    "The brief was written with the human in the live session that `autonomouse project "
    "new` started in the project's repository."
)


def setup_prompt(name: str, slug: str, thread_id: str, home: Home) -> str:
    """The instruction the setup session starts on, two lines: that it plans the project
    with the human, and the commands that write the project's goal, its brief and the
    human's instructions for its orchestrator and workers once the human agrees."""
    cli = home.cli
    return (
        f"You are planning the autonomouse project {name} ({slug}) with the human, here in "
        f"its repository.\nWhen the human agrees, write the project's goal with `{cli} "
        f'project edit {slug} --goal "<goal>"`, its brief with `{cli} plan brief '
        f"{thread_id} <file>`, and any instructions the human gives for the project's "
        f'orchestrator or for every worker with `{cli} project edit {slug} '
        f'--orchestrator-instruction "<text>"` or `--worker-instruction "<text>"`, one '
        f"flag per instruction, the whole list each time."
    )


def setup_session(
    root: Path,
    claim: Callable[[], str],
    *,
    name: str,
    cwd: Path,
    claude: str,
    home: Home,
    secrets: Path,
    state_dir: Path,
    out: Callable[[str], None] = print,
) -> int:
    """`autonomouse project new`'s last step on a terminal: an interactive claude in `cwd`,
    the project's repository, at `SETUP_MODEL` and `SETUP_EFFORT` with
    `--dangerously-skip-permissions`, started on `setup_prompt` and on a new session each
    time, until the human exits it; the exit code is claude's.

    `claim` returns the id of the project's brief thread, opening it when there is none;
    it runs under `plan-step.lock`, and the thread is joined as `plan join` joins one, so
    no plan step runs the planner on it while the human is in the session. When claude
    exits and the project's brief is written, the thread is closed (the session may have
    closed it already); else the join ends as a `plan join` ends, with `SETUP_LEFT_TEXT`,
    and the next plan step starts the planner, which asks the rest on Telegram. The
    session is not recorded on the thread: `plan step` runs in `home.root`, where a
    session started in `cwd` cannot be resumed."""
    holder = os.getpid()
    with _step_lock(state_dir):
        thread = thread_join(root, claim(), holder)
    session_id = str(uuid.uuid4())
    model, effort = fallback_tier(root, SETUP_MODEL, SETUP_EFFORT)
    argv = [claude, "--session-id", session_id, "--model", model, "--effort"]
    argv += [effort, "--dangerously-skip-permissions"]
    argv.append(setup_prompt(name, thread.project, thread.id, home))
    out(f"{thread.id}: planning live with {model}, {effort} effort, in {cwd}")

    def release(pid: int) -> Thread:
        if not brief_path(root, thread.project).is_file():
            return thread_leave(root, thread.id, pid, text=SETUP_LEFT_TEXT)
        after = load_thread(root, thread.id)
        if after.status is ThreadStatus.CLOSED:
            return after
        return thread_close(root, thread.id, SETUP_DONE_TEXT)

    return _attend(
        root, thread, holder, argv, cwd=cwd, session_id=session_id, model=model,
        secrets=secrets, out=out, release=release,
    )  # fmt: skip
