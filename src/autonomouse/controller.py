"""`autonomouse run`: one wake of the system, from the kill switch to the board commit.

In order:

1. The `PAUSE` file exists → start nothing (exit 3), and write a run record with no session
   whose `refusals` holds `{at, command: PAUSE, reason: <path> exists}` and whose summary is
   `refused: PAUSE - <path> exists` (`autonomouse.board.runs.run_refused`, sys-j5); a record
   that cannot be written or committed is printed as `refusal_record=failed:` and the exit
   stays 3.
1a. Run `autonomouse plan step` (`autonomouse.planner.step`, passed in as `plan_step`): the
   planner answers every planning thread with an unanswered human message before the
   wake decides what to work on. It runs outside the decision lock (a planner
   session can take minutes) and before the pick. A plan step that exits non-zero or raises
   is printed as `plan_step=failed …` and the wake goes on; it never changes the exit code.
1b. Under the decision lock: take back the working folder of every `done` card whose PR
   has merged, and of every `cancelled` card whose project no alive or stalled run holds
   (`cleanup_worktrees`, `docs/board-schema.md`'s worktree lifecycle, `sys-e6`) and
   commit the board if any were removed. Every wake does this, whether or not it goes on
   to pick a project. A wake merges nothing into a project's default branch: only the
   super-orchestrator does, for the PRs the owner told it to merge (`autonomouse.merge`). Once a
   project is picked, record the worktrees its repository has registered (`git worktree
   list`): the wake's end never touches one of them (step 7).
2. Under the decision lock, so two wakes never decide at once: when a run the liveness
   rule calls alive or stalled holds any project's claim, another wake is working, so
   start nothing (exit 3, no run record). One wake works at a time on a machine, on one
   project. A claim held by a stopped or crashed run does not stop this wake.
2a. Mark finished projects done (`autonomouse.board.done.mark_finished`): a project that
   is not done, has at least one card, has every card `done` or `cancelled` and has
   every `done` card's work on the default branch (`github.waiting_branch`: its PR merged
   into it, or into a branch whose own PR merged, such as a wake's `wake/<run id>`; a
   card with no PR counts as there) gets
   `done_at`, one `project_done=<slug>` line, and a board commit. When the board has a
   project and every project is now done, the wake says `all_done`, removes the three
   crontab lines (`timers_off`, the CLI's `autonomouse.timer.remove`), prints
   `timers_off: …` and exits 0 with no run record; a removal that fails exits 1. A new
   `todo` card on a done project clears `done_at` again (`project_reopen`), and
   `autonomouse card new` then installs the three lines when they are missing.
2b. In every project that is not done, follow up on each `done` card's stacked PR
   (`followup.follow_up_done`, `sys-t13`): once the branch it targets has merged,
   retarget it to the default branch, merge that branch into it and push, and reopen the
   card to `todo` when the merge conflicts, so the pick below starts it in this wake.
   Commit the board if any card changed; a failure is printed and the wake goes on.
3. Pick a project with work that is not done: `in_progress` cards whose own claim is dead
   (reclaimable, resumed first) and every `todo` card whose `depends_on` are all `done`
   (ready, in `position` order). The batch, the cards the wake starts at once, is the
   resumed cards then the first ready cards, `ceilings.workers_per_wake` of them at most
   (the number of workers alive at once); the ready cards after them wait, and the
   orchestrator starts each whenever a slot is free (`docs/orchestrator.md`). Projects are
   taken by `priority`, the smallest first, so a project the owner sets to priority 0 is
   picked before one a stopped wake left half done; among projects of equal priority, one
   whose claim is held by a stopped or crashed run comes first. A project with no work
   falls through to the next, so the wake returns to the earlier project once the
   priority-0 project has nothing left for the agents. Nothing anywhere → exit 0.
   `--project <slug>` considers only that project: done or with no work → exit 0. An
   exit 3 or 0 here writes no run record. A project with no resumable or ready card
   is asked for its open pull requests (`sweep`, bound by the CLI to
   `autonomouse.sweep.sweep`, `sys-s8`): a finding — a PR autonomouse opened that
   conflicts, whose runs failed or whose review comment is unanswered — counts as work,
   and the project is picked with no cards and its findings, so the orchestrator works the
   findings (`docs/orchestrator/conflict-check.md`). A sweep that fails is printed as
   `sweep=<slug>: failed …` and counts as no finding. A done project is never swept.
   Every prompt ends with the Situation block (`autonomouse.situation`):
   the wake interval and next fire, the usage-limit reset in force, and the ready cards.
   Before it, the prompt of a project with a brief (`autonomouse.board.brief`) carries,
   under the heading "The project's brief", the goal sentence and the intent sentence each
   worker's prompt carries, then the whole brief (`brief_prompt`, sys-t6); a project
   with no brief gets neither. After the brief comes the owner's own instructions for the
   project, under the heading "The owner's instructions" (`instructions_prompt`): those
   for the orchestrator, and those every worker's prompt carries; a project with neither
   gets no such block.
3b. A usage limit in force (`usage_limit_in_force`: a run record's `usage_limit.reset_at`
   still ahead, the rule the Situation reports) → start nothing: write a run record with no
   session, no cards and no claim, whose summary names the reset, commit the board, back it
   up and exit 0. This is the controller's own check before any session starts; it asks
   Jev nothing. It reads every run record on the board, the same
   folder Jev's request reads (3c); one that fails validation (`BoardValidationError`: a
   bad file under `runs/`) is printed as `run_record_unreadable=` naming the record and
   its error and left out of the scan, rather than stopping every wake before its claim
   the way one bad file used to (sys-t17); a real limit recorded on another, valid record
   still holds the wake back.
3c. Only with `jev.enabled`: ask Jev `go`, and one `size` per ready card, started or
   waiting, in one request (`autonomouse.jev.wake`), with the secrets file loaded into this
   process's environment for the request only, and count the width from the cards. A pick
   with no card but a finding hands Jev the findings as its work (`sys-s22`). Jev's `go` is
   advice: the run record keeps the answer and the wake starts whatever it says. The batch
   is the width (`autonomouse.jev.wake.width_rule`: every resumed and ready card the pick
   found, capped at `ceilings.workers_per_wake`) and the prompt carries every ready card's
   tier, the one its size maps to in the config's `tiers`: Jev's size whenever it names one
   of the four sizes, whatever its confidence, and the card's own size when the answer is
   missing or malformed or the request failed (bad or missing key, network, timeout,
   unparsable response); a Jev size of `split` (larger than `m`) tells the orchestrator to
   split the card. Answering the request reads every run record on the board
   (`all_runs`); one that fails validation (`BoardValidationError`: a bad file under
   `runs/`) is printed as `run_record_unreadable=` naming the record and its error, and
   the wake goes on as it does with `jev.enabled` false: `plan` stays null, and nothing
   is asked or recorded for this wake. Unlike the usage-limit check (3b) and the
   Situation block (step 4), which skip only the bad record and still answer from the
   rest, one bad record here raises before Jev is asked anything, so the whole request is
   skipped for the wake (sys-t17).
4. Start `claude -p --model <model> --effort <effort>` (`orchestrator.model` and
   `orchestrator.effort` in the config, or `autonomouse run --model/--effort`) in the
   folder that holds the charters (`autonomouse.home`: the repository checkout, or the
   installed package, which ships `docs/`) with the secrets file (`~/.config/autonomouse/.env`,
   `autonomouse.config.secrets_path`) and `CLAUDE_CODE_RETRY_WATCHDOG=1` (retry capacity
   errors instead of failing) in its environment. With `browser.pool` set, the session gets
   `--mcp-config` naming one Playwright MCP server per slot (`browser.slots`), `pool-1`
   up, each pointed at one of the pool's leased Chromes over CDP, and `--strict-mcp-config`, so this is the only
   browser access this session, and every worker it spawns, has for the whole wake — no
   local Playwright or Chrome DevTools MCP tools, and no fallback to them
   (`_pool_mcp_config`; unset keeps today's local tools, unchanged). For a project linked
   to a tracker, the same `--mcp-config` also keeps the user's MCP server for that tracker
   as their Claude Code config registers it (`_tracker_mcp_servers`), so agents can keep
   its issues in step, and a `tracker_mcp=` line names the servers kept.
   When the picked project's claim is held by a crashed run (its session never reached a
   clean end) whose session holds fewer than `ceilings.resume_tokens` tokens
   (`resume_choice`; an unknown count is over), that run's session is resumed
   (`--resume <its session id>`, its model and effort) with "continue your cards from their
   notes";
   otherwise — no dead run, a run that ended cleanly, or a big session — a new session
   starts with the charter's path, the board command's invocation, the project and the
   picked cards in its prompt, and
   a session id chosen here; the board's handoff notes carry the cards. The `resumed=`
   line says which case applied and the token count it saw. Every session gets its
   run's wake log: `~/projects/autonomouse/wake-logs/<run id>.md`
   (`wake_logs_root`), made here with a header naming the run id, the project, the ready
   cards and the launch time, and named in the prompt; the orchestrator appends its
   observations to it as the wake goes (`docs/orchestrator.md`), and no code reads it.
   Before the session starts, write the run
   record (`status: alive`, the session id, claude's pid, the batch), claim the
   project for it (taking over the dead claim), commit the board, and only then let
   claude run: it is started first because the record needs its pid, and waits before
   running anything (`_EXEC_WHEN_RECORDED`) until that commit is made, so the session's
   first `card start` always finds its project claimed. A claim refused there kills the
   held claude and fails the wake, and the refusal is written on the run's `refusals`
   (`command: project claim`). Then release the decision lock. Every board commit and
   push, here and in the orchestrator, takes the board's commit lock (`board_commit_lock`),
   so the run, its workers and the plan step never commit at the same moment.
5. Wait for claude to exit, then end every process the session left alive (`_leftovers`,
   sys-u4): the ones still in its process group (the session runs in one of its own) and the
   ones whose environment carries this run's `AUTONOMOUSE_RUN_ID`, TERM, then KILL after
   `LEFTOVER_GRACE_S` seconds; one wake-log line per process, the count on the run record's
   `leftover_processes`. Then, with `browser.pool` set, release every pool slot this run's own
   lease record still names (`_release_leased_browsers`): the backstop for a `card` report
   whose `autonomouse browser release` never ran, because the session crashed or ended before
   it. A wait cut off ends them, the session with them, before the error goes on; when
   SIGINT, SIGTERM or SIGHUP cut it off (`STOP_SIGNALS`, sys-w106), the run's end is
   recorded too (exit code 128 plus the signal's number) and the board committed, and
   `autonomouse run` exits with that code. For SIGKILL, which no handler sees, each
   session has a watcher (`_WATCH`): a small process of its own that ends the session and
   every process it started once `autonomouse run` is gone, and the next wake finds the
   run crashed, as before. A resume that exits non-zero without printing a result failed
   (claude found no such session, or could not start it): its run is recorded as ended
   and a new session starts from the notes, as in step 4, taking over its claim.
6. Copy claude's printed JSON result into the run record (`run_end`), with the usage limit
   when the result or stderr carries Claude Code's usage-limit message (`usage_limit`: when
   it hit, when it resets), and derive the run's behaviour log from its transcripts
   (`autonomouse.behaviour.log`). A wake a usage limit kept from starting gets a behaviour
   log too, with no agents. Deriving the log never fails the wake: a failure is printed and
   the wake goes on. A record this version refuses — a merge under the live wake changed a
   field's shape — still gets the result, written into its JSON as it is
   (`run_end_unchecked`), and the refusal is printed as `record_invalid=`. A session that
   ended on `--max-budget-usd` (its result's `subtype` `error_max_budget_usd`, or
   `terminal_reason` `budget_exhausted`) gets a refusal on its record (`command: claude
   --max-budget-usd`) whose reason quotes the result's error that names the budget, or,
   when no error names it, says what the result holds (`_budget_refusal`).
6a. Only with `jev.enabled`, when the session failed (non-zero exit, no result, or an error
   result): ask Jev `recover` with the error, the project's failed sessions in a row and the
   limit's reset time, and record the answer on the failed run. `retry` starts a new session
   from the notes, as in step 4, and comes back here (at most `ceilings.retries_per_wake`
   times a wake; a `retry` past it is applied as `next_wake`, the reason on the run record,
   and nobody is notified); `next_wake` goes on; a usable `raise` from Jev sends the
   failure to the human with `autonomouse notify` and goes on. An answer below the floor,
   or a failed request, is `retry`.
7. Under the decision lock, leave no worktree the wake did not mean to keep (sys-u5):
   run `cleanup_worktrees` again, then remove with `git worktree remove --force` every
   worktree registered in the project's repository during the wake that is no card's
   `worktree`, keeping its branch, with one line per removal in the wake log. A worktree
   registered before the wake started is never touched (`_clean_worktrees_at_end`).
   Then send the owner the wake's one report over `autonomouse notify` (`_wake_report`,
   sys-w23): the project by its name, how many cards the wake's runs moved to `done`, and
   how many of autonomouse's own open pull requests into the project's default branch or
   stacked on a card's branch wait for the owner to merge, reading a branch as a card's
   the way the sweep does (a branch a card records, or one starting with `card/`). A
   failed wake sends it too, after its failure notice; a wake that started no session
   (steps 1, 3, 3b) sends none. A send that fails is recorded on the run's
   `notify_failures`, and nothing in the report changes the exit code.
   Release the claim if the run still holds it, and commit the board. The wake's own exit
   code is always the orchestrator session's: a claim release or board commit that fails is
   printed (`claim=failed:`, `commit=failed:`), the next of them still runs, and the exit
   code stays the session's.

The orchestrator session does everything between the claim and its own `run end`.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autonomouse import (
    __version__,
    browser,
    control,
    followup,
    github,
    human_messages,
    session_end_hook,
    situation,
    usage_limit,
)
from autonomouse.behaviour import log as behaviour_log
from autonomouse.board import trackers
from autonomouse.board.brief import Brief, load_brief
from autonomouse.board.commands import (
    CommandError,
    card_list,
    project_list,
    project_show,
)
from autonomouse.board.done import (
    BranchMerged,
    MergedInto,
    every_project_done,
    mark_finished,
)
from autonomouse.board.history import now_iso
from autonomouse.board.paths import wake_logs_root
from autonomouse.board.runs import (
    TAKEOVER_ALLOWED,
    Liveness,
    Run,
    RunKind,
    RunStatus,
    claim_liveness,
    claude_projects_dir,
    cleanup_worktrees,
    liveness,
    load_run,
    new_run_id,
    project_claim,
    project_release,
    record_refusal,
    run_end,
    run_end_unchecked,
    run_liveness,
    run_refused,
    run_start,
    save_run,
)
from autonomouse.board.schema import BoardValidationError, Card, Project, Stack
from autonomouse.board.store import commit_board
from autonomouse.board.worktrees import WorktreeError, registered, remove_registered
from autonomouse.config import DEFAULTS, effective_config
from autonomouse.home import Home, locate
from autonomouse.jev import wake as jev_wake
from autonomouse.jev.client import JevError
from autonomouse.jev.client import ask as jev_ask
from autonomouse.jev.questions import (
    RECOVER,
    RESULT_TEXT_TAIL,
    base_state,
    questions_body,
    recover_state,
)
from autonomouse.notify import notify
from autonomouse.sweep import Line as SweepLine
from autonomouse.sweep import is_own_branch, recorded_branches

# Exit codes besides the session's own.
EXIT_NOT_STARTED = 3  # PAUSE, or another wake is alive or stalled (one wake per machine)


# Set on every claude session this starts: retry 429 and 529 capacity errors instead of
# failing after a few attempts. A request refused for exhausted usage still fails at once.
WATCHDOG_ENV = {
    "CLAUDE_CODE_RETRY_WATCHDOG": "1",
    "CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS": "60000",
    "CLAUDE_CODE_RETRY_WATCHDOG_MAX_USAGE_LIMIT_WAIT_MS": "1000",
}

# Loads the secrets file (when readable) into the environment, then becomes claude, so
# no shell that read the secrets outlives the session and no value reaches a command line.
_EXEC_WITH_SECRETS = 'if [ -r "$0" ]; then set -a; . "$0"; set +a; fi; exec "$@"'

# `_EXEC_WITH_SECRETS`, run only after one line arrives on stdin. `_start` starts an
# orchestrator session this way to learn its pid, writes the run record and the claim
# under that pid, commits them, and then writes the line; so claude never runs before its
# project is claimed. Claude's own stdin is /dev/null. When the pipe closes with no line
# (`_start` failed, or its process died), the wrapper exits 1 without running claude.
_EXEC_WHEN_RECORDED = "read -r go || exit 1; exec </dev/null; " + _EXEC_WITH_SECRETS

# The signals that end a wake while it waits on its session (sys-w106): `_wait` ends the
# session and every process it started, and records the run's end. SIGKILL cannot be
# caught; each session's watcher (`_WATCH`) covers it.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Stopped(BaseException):
    """Raised in the main thread when `autonomouse run` gets one of `STOP_SIGNALS`. A
    `BaseException`, as `KeyboardInterrupt` is, so no `except Exception` on the way
    swallows it."""

    def __init__(self, signum: int) -> None:
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


@contextlib.contextmanager
def _stop_on_signals() -> Iterator[None]:
    """While the wake runs, each of `STOP_SIGNALS` raises `Stopped`; the handlers before
    are put back after. A signal ignored when the wake starts (`nohup autonomouse run`
    ignores SIGHUP) stays ignored. Off the main thread, where Python lets no handler be
    set (tests run wakes in threads), nothing changes."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def stop(signum: int, _frame: object) -> None:
        raise Stopped(signum)

    before = {
        sig: signal.signal(sig, stop)
        for sig in STOP_SIGNALS
        if signal.getsignal(sig) is not signal.SIG_IGN
    }
    try:
        yield
    finally:
        for sig, handler in before.items():
            signal.signal(sig, handler)


# Each session's watcher (sys-w106), started by `_start` with its stdin a pipe whose other
# end only `autonomouse run` holds. It reads that pipe to its end. `_unwatch` writes a
# line first, so the watcher exits doing nothing. When `autonomouse run` is gone without
# writing it (SIGKILL, or any other death), the kernel closes the pipe, the read returns
# nothing, and the watcher ends the session and every process it started
# (`_end_orphaned`). It imports autonomouse only then, so waiting costs one bare Python.
_WATCH = (
    "import sys\n"
    "if not sys.stdin.buffer.read():\n"
    "    from autonomouse.controller import _end_orphaned\n"
    "    _end_orphaned(*sys.argv[1:])\n"
)

# The Playwright MCP servers `_pool_mcp_config` points at the pool's slots
# (`docs/config.md`, `browser.pool`, `browser.slots`): one per slot, `pool-1`..`pool-<slots>`,
# each server n's `--cdp-endpoint` at port `POOL_BASE_PORT + n` of the pool's machine. Not
# this repository's own choice: the 930N ports are fixed by browsermaze, which owns the
# pool's server side.
POOL_BASE_PORT = 9300


def _pool_mcp_config(pool: str, slots: int, keep: Mapping[str, Any] | None = None) -> str:
    """The `--mcp-config` JSON for the `slots` pool servers: paired with
    `--strict-mcp-config` in `_start`, this is the session's only browser access for the
    whole wake, with no local Playwright or Chrome DevTools MCP tools and no fallback to
    them (the human's rule, 2026-09-28). `keep` holds the user's own servers the session
    keeps beside them, as they are registered: the linked tracker's MCP server
    (`_tracker_mcp_servers`), without which `--strict-mcp-config` would drop it.

    Each server runs the same `node` and Playwright MCP command `setup/install.sh`
    registers for the local `playwright` server at user scope, found on this process's
    own PATH (`shutil.which`, the way `autonomouse timer install` finds its tools) — only
    the browser it drives differs, one of the pool's leased Chromes over CDP in place of
    a local headless one. Raises `FileNotFoundError` naming the missing tool when `node`
    or `playwright-mcp` is not on PATH.
    """
    node = shutil.which("node")
    if node is None:
        raise FileNotFoundError(
            "browser.pool is set, but node is not on PATH; run setup/install.sh"
        )
    playwright_mcp = shutil.which("playwright-mcp")
    if playwright_mcp is None:
        raise FileNotFoundError(
            "browser.pool is set, but playwright-mcp is not on PATH; run setup/install.sh"
        )
    servers = {
        f"pool-{n}": {
            "type": "stdio",
            "command": node,
            "args": [
                playwright_mcp,
                "--cdp-endpoint",
                f"http://{browser.host(pool)}:{POOL_BASE_PORT + n}",
            ],
            "env": {},
        }
        for n in range(1, slots + 1)
    }
    return json.dumps({"mcpServers": {**(keep or {}), **servers}})


def _tracker_mcp_servers(project: Project) -> dict[str, Any]:
    """The MCP servers in the user's Claude Code config (`$CLAUDE_CONFIG_DIR/.claude.json`,
    by default `~/.claude.json`, its top-level `mcpServers`) that reach `project`'s linked
    tracker (`trackers.mcp_servers`), e.g. `linear` at `https://mcp.linear.app/mcp`. Empty
    for a project with no link, or when no config file exists. Agents reach a tracker only
    through this server (the owner's rule, 2026-09-29); whether it is signed in is the
    setup check's to find out, not this code's."""
    if project.tracker is None:
        return {}
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    path = (Path(base) if base else Path.home()) / ".claude.json"
    if not path.exists():
        return {}
    registered = json.loads(path.read_text()).get("mcpServers") or {}
    return trackers.mcp_servers(project.tracker, registered)


def default_state_dir() -> Path:
    """Where each run's printed result and stderr log go: `$AUTONOMOUSE_STATE`, else
    `~/.local/state/autonomouse/runs`."""
    env = os.environ.get("AUTONOMOUSE_STATE")
    if env and env.strip():
        return Path(env)
    return Path.home() / ".local" / "state" / "autonomouse" / "runs"


def default_pr_state(pr_url: str) -> str | None:
    """`pr_url`'s state (`MERGED`, `OPEN`, `CLOSED`) via `gh pr view`, or `None` when
    the check itself fails — no network, `gh` missing or not authenticated, the PR
    gone. `cleanup_worktrees` treats `None` the same as an open PR: leave the working
    folder for a later run to check again."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    state = result.stdout.strip()
    return state or None


# The dead claim states a wake picks first (`run status` reports them for a dead run);
# `resume_choice` decides whether that run's session is resumed.
RESUMABLE = (Liveness.STOPPED, Liveness.CRASHED)


@dataclass
class Pick:
    """A project and its work: the cards to resume, every ready card, and the ceiling that
    cuts the batch (`ceilings.workers_per_wake`, the number of workers alive at once)."""

    project: Project
    resume: list[Card]  # in_progress under a dead claim
    ready: list[Card]  # todo, every depends_on done, in pick order
    cap: int
    stopped_run: Run | None = None  # the stopped or crashed run holding the project's claim
    # the sweep's findings, asked only when the project has no resumable or ready card
    findings: list[SweepLine] = field(default_factory=list)

    @property
    def batch(self) -> list[Card]:
        """The cards the wake starts at once: the resumed cards, then the first ready ones,
        `cap` of them at most (the run record's `cards`)."""
        return (self.resume + self.ready)[: self.cap]

    @property
    def started(self) -> list[Card]:
        """The ready cards in the batch."""
        return self.batch[len(self.resume):]

    @property
    def waiting(self) -> list[Card]:
        """The ready cards after the batch, in pick order: the orchestrator starts each
        whenever a slot is free."""
        return self.ready[len(self.started):]


def live_claims(
    root: Path, *, projects_dir: Path | None = None, now: float | None = None
) -> dict[str, str]:
    """Each project held by an alive or stalled run, mapped to a line naming it."""
    held = {}
    for project in project_list(root):
        report, _ = claim_liveness(root, project, projects_dir=projects_dir, now=now)
        if report is not None and report.status in (Liveness.ALIVE, Liveness.STALLED):
            held[project.slug] = (
                f"project {project.slug!r} held by live run {project.claim.run_id!r} "
                f"({report.status.value}: {report.reason})"
            )
    return held


def stopped_claims(
    root: Path, *, projects_dir: Path | None = None, now: float | None = None
) -> dict[str, Run]:
    """Each project whose claim is held by a stopped or crashed run with a session id and
    a model, mapped to that run: the projects a wake picks first, and the sessions
    `resume_choice` may resume."""
    found = {}
    for project in project_list(root):
        report, run = claim_liveness(root, project, projects_dir=projects_dir, now=now)
        if (
            run is not None
            and report.status in RESUMABLE
            and run.session_id
            and run.model
        ):
            found[project.slug] = run
    return found


def session_tokens(session_id: str, projects_dir: Path | None = None) -> int | None:
    """How many tokens the session holds: its last API call's input (uncached, cache
    written and cache read) plus that call's output, from the `usage` of the last
    assistant line in its own transcript (`<projects_dir>/<cwd-slug>/<session_id>.jsonl`;
    sub-agents' transcripts do not count). A resume re-reads this much on every turn.
    `None` when no transcript or no usage is found."""
    projects_dir = projects_dir or claude_projects_dir()
    if not projects_dir.is_dir():
        return None
    usage = None
    for path in projects_dir.glob(f"*/{session_id}.jsonl"):
        for line in path.read_text(errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage = message["usage"]
    if usage is None:
        return None
    return sum(
        usage.get(key) or 0
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
    )


def resume_choice(
    run: Run,
    *,
    resume_tokens: int,
    projects_dir: Path | None = None,
    now: float | None = None,
) -> tuple[bool, str]:
    """Whether a wake resumes `run`'s session, and the `resumed=` line that says why.

    Only a crashed run (pid gone, no end recorded) whose session holds fewer than
    `resume_tokens` tokens (`session_tokens`) is resumed. A run that ended cleanly, a
    session at or over the limit, and a session whose count is unknown get a new
    session; the board's handoff notes carry the cards. A stalled run's claim is never
    picked (`live_claims`), so it never reaches this."""
    report = run_liveness(run, projects_dir=projects_dir, now=now)
    fresh = f"no (new session; run {run.id} "
    if report.status != Liveness.CRASHED:
        return False, fresh + f"is {report.status.value}: {report.reason})"
    tokens = session_tokens(run.session_id, projects_dir)
    if tokens is None:
        return False, fresh + (
            f"crashed; its session's token count is unknown, which counts as over "
            f"the {resume_tokens} limit)"
        )
    if tokens >= resume_tokens:
        return False, fresh + (
            f"crashed; its session holds {tokens} tokens, at or over the "
            f"{resume_tokens} limit)"
        )
    return True, (
        f"session {run.session_id} of run {run.id} (crashed; its session holds "
        f"{tokens} tokens, under the {resume_tokens} limit)"
    )


def pick(
    root: Path,
    cap: int,
    *,
    only: str | None = None,
    projects_dir: Path | None = None,
    now: float | None = None,
    sweep: Callable[[Path, str], list[SweepLine]] | None = None,
    out: Callable[[str], None] = lambda line: print(line, flush=True),
) -> Pick | None:
    """The first project with reclaimable or ready cards: reclaimable first, `cap`
    (`ceilings.workers_per_wake`) of them at most, then every ready card, each in
    `position` order; `cap` cuts the pick's `batch`, never its `ready` list. A reclaimable
    card past the cap stays under its dead claim for a later wake, as before. Projects are
    taken by `priority`, the smallest first; among projects of equal priority, one whose
    claim is held by a stopped or crashed run comes first. A project of a smaller priority
    number is picked before a stopped one of a larger number. A done project is never
    picked; with `only`, no other project is.

    A project with neither is asked `sweep(root, slug)` when one is given
    (`autonomouse.sweep.sweep`, `sys-s8`): a line that is a finding — an open pull request
    autonomouse opened needs work — picks the project with no cards, and the pick's
    `findings` hold every such line for Jev's `go` (`sys-s22`). A sweep that raises is
    printed (`sweep=<slug>: failed …`) and counts as no finding."""
    cards = card_list(root)
    stopped = stopped_claims(root, projects_dir=projects_dir, now=now)
    projects = [
        p
        for p in project_list(root)
        if p.done_at is None and (only is None or p.slug == only)
    ]
    projects.sort(key=lambda p: (p.priority, p.slug not in stopped))  # stable: then slug
    for project in projects:
        own = sorted(
            (c for c in cards if c.project == project.slug), key=lambda c: (c.position, c.id)
        )
        resume = [
            c
            for c in own
            if c.stack == Stack.IN_PROGRESS
            and liveness(
                session_id=c.assigned.session_id,
                pid=c.assigned.pid,
                recorded_end=False,
                projects_dir=projects_dir,
                now=now,
            ).status
            in TAKEOVER_ALLOWED
        ]
        ready = situation.ready(cards, project.slug)
        findings = [] if resume or ready else _findings(sweep, root, project.slug, out=out)
        if resume or ready or findings:
            return Pick(
                project=project,
                resume=resume[:cap],
                ready=ready,
                cap=cap,
                stopped_run=stopped.get(project.slug),
                findings=findings,
            )
    return None


def _findings(
    sweep: Callable[[Path, str], list[SweepLine]] | None,
    root: Path,
    slug: str,
    *,
    out: Callable[[str], None],
) -> list[SweepLine]:
    """The lines of `sweep` on `slug`'s open pull requests that are findings; none with no
    sweep. A raise is printed and counts as no finding: the sweep never stops a wake."""
    if sweep is None:
        return []
    try:
        found = [line for line in sweep(root, slug) if line.finding]
    except Exception as e:  # noqa: BLE001 — a gh failure must never stop the wake
        out(f"sweep={slug}: failed, no finding counted: {e}")
        return []
    out(f"sweep={slug}: {'finding' if found else 'no finding'}")
    return found


def _ids(cards: list[Card]) -> str:
    return ", ".join(c.id for c in cards) or "none"


def prompt(
    run_id: str,
    chosen: Pick,
    home: Home,
    plan: jev_wake.WakePlan | None = None,
    situation_text: str = "",
    *,
    wake_log: Path,
    brief: str = "",
    instructions: str = "",
) -> str:
    """The message a new session gets; it names the wake log (`write_wake_log`), carries
    the project's brief (`brief`, `brief_prompt`) and the owner's instructions
    (`instructions`, `instructions_prompt`) and ends with the Situation block
    (`situation_text`, `autonomouse.situation.Situation.text`)."""
    return (
        f"Read {home.charter('orchestrator.md')} and run one wake. REPO is {home.root}; "
        f"the board commands are `{home.cli}`. Your run id is {run_id}."
        + wake_log_prompt(wake_log)
        + f" The run controller has written your run record and claimed project "
        f"{chosen.project.slug} for it. Resume these in_progress cards: "
        f"{_ids(chosen.resume)}. Start these ready cards: {_ids(chosen.started)}."
        + waiting_prompt(chosen)
        + jev_prompt(chosen, plan)
        + brief
        + instructions
        + _situation_tail(situation_text)
    )


def resume_prompt(
    run_id: str,
    chosen: Pick,
    home: Home,
    plan: jev_wake.WakePlan | None = None,
    situation_text: str = "",
    *,
    wake_log: Path,
    brief: str = "",
    instructions: str = "",
) -> str:
    """The message a resumed session gets: its new run id, its cards, its new run's wake
    log, the project's brief (`brief_prompt`) and the owner's instructions
    (`instructions_prompt`); it ends with the Situation block."""
    old = chosen.stopped_run.id if chosen.stopped_run else "unknown"
    return (
        f"Your session stopped before its wake ended (run {old}), and the run controller "
        f"has resumed it as run {run_id}. Your run id is now {run_id}: pass it on every "
        f"write."
        + wake_log_prompt(wake_log)
        + f" The run controller has written the new run record and claimed project "
        f"{chosen.project.slug} for it. The workers you spawned before have stopped. "
        f"Continue your cards from their notes, as {home.charter('orchestrator.md')} "
        f"describes. "
        f"Resume these in_progress cards: {_ids(chosen.resume)}. "
        f"Start these ready cards: {_ids(chosen.started)}."
        + waiting_prompt(chosen)
        + jev_prompt(chosen, plan)
        + brief
        + instructions
        + _situation_tail(situation_text)
    )


def _situation_tail(situation_text: str) -> str:
    return f"\n\n{situation_text}" if situation_text else ""


def brief_prompt(brief: Brief | None) -> str:
    """The project's brief in the orchestrator's prompt (sys-t6), empty for a project with
    no brief. Under the heading "The project's brief" come first the lines each worker's
    prompt carries after its card's goal (`docs/orchestrator.md`, "A wake", step 5): the
    goal sentence and the intent sentence as `load_brief` reads them, each only when the
    brief has it, the goal sentence keeping any quotes the brief writes around it. Then
    the whole `brief.md` as written, between `<brief>` tags, so that its last section
    ends where the tag does and not at the Situation block after it."""
    if brief is None:
        return ""
    lines = [
        f"The project's {name}: {sentence}"
        for name, sentence in (("goal", brief.goal), ("intent", brief.intent))
        if sentence
    ]
    worker = (
        "Each worker's prompt carries these lines after its card's goal, as written "
        "(your charter, \"A wake\", step 5):\n" + "\n".join(lines) + "\n\n"
        if lines
        else ""
    )
    return f"\n\n# The project's brief\n\n{worker}<brief>\n{brief.text.strip()}\n</brief>"


def instructions_prompt(project: Project) -> str:
    """The owner's instructions for `project` in the orchestrator's prompt, empty when it
    has none: under the heading "The owner's instructions", those for the orchestrator
    (`orchestrator_instructions`), then those each worker's prompt carries
    (`worker_instructions`, `docs/orchestrator.md`, "A wake", step 5), one `- ` line each,
    each part only when its list has one."""
    parts = []
    if project.orchestrator_instructions:
        parts.append(
            "For you:\n" + "\n".join(f"- {i}" for i in project.orchestrator_instructions)
        )
    if project.worker_instructions:
        parts.append(
            "Each worker's prompt carries these, as written (your charter, \"A wake\", "
            "step 5):\n" + "\n".join(f"- {i}" for i in project.worker_instructions)
        )
    if not parts:
        return ""
    return (
        "\n\n# The owner's instructions\n\nThe owner wrote these for this project; where "
        "one differs from a charter, the instruction holds.\n\n" + "\n\n".join(parts)
    )


def wake_log_prompt(wake_log: Path) -> str:
    """The sentence that names the session's wake log (`write_wake_log`)."""
    return f" Your wake log is {wake_log}: append to it as the wake goes."


def write_wake_log(run_id: str, chosen: Pick) -> Path:
    """Create run `run_id`'s wake log, `wake_logs_root()/<run id>.md`, with its header: the
    run id, the project, the ready cards (started or waiting) and the launch time. The
    orchestrator appends its observations to it as the wake goes (`docs/orchestrator.md`,
    "Your wake log"); no code reads it. Returns its path."""
    folder = wake_logs_root()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_id}.md"
    path.write_text(
        f"# Wake log of run {run_id}\n\n"
        f"- Run id: {run_id}\n"
        f"- Project: {chosen.project.slug}\n"
        f"- Ready cards: {_ids(chosen.ready)}\n"
        f"- Launched: {now_iso()}\n"
    )
    return path


def waiting_prompt(chosen: Pick) -> str:
    """The ready cards the ceiling left waiting for a free slot, in pick order, for the
    orchestrator; empty when there are none."""
    if not chosen.waiting:
        return ""
    return f" These ready cards wait for a free slot, in pick order: {_ids(chosen.waiting)}."


def jev_prompt(chosen: Pick, plan: jev_wake.WakePlan | None) -> str:
    """What Jev decided for each ready card, started or waiting, for the orchestrator;
    empty when Jev is off."""
    if plan is None:
        return ""
    lines = [
        (
            " Jev was consulted for this wake, and the run record's jev list holds every "
            "answer. These sizes are Jev's advice, which docs/orchestrator/tier-rubric.md "
            "lets you override, with the reason recorded by card edit --note."
        )
    ]
    for card in chosen.ready:
        planned = plan.cards[card.id]
        if planned.split:
            lines.append(
                f"Split {card.id}: Jev sized it larger than m. Size its children by your "
                f"tier rubric."
            )
        else:
            lines.append(
                f"Start {card.id} with tier {planned.tier} (size {planned.size}): card start "
                f"--model {planned.model} --effort {planned.effort} --agent-type "
                f"{planned.agent_type}, and spawn its worker with Agent model {planned.model} "
                f"and subagent_type {planned.agent_type}."
            )
    return " ".join(lines)


@dataclass(frozen=True)
class Jev:
    """How a wake consults Jev; `wake` gets one only when `jev.enabled` is true.

    `config` is the effective config (its ceilings are read and sent to Jev); `ask` posts
    the questions (`autonomouse.jev.client.ask`).
    """

    config: Mapping[str, Any]
    ask: Callable[..., Mapping[str, Any]] = field(default=jev_ask)


def all_runs(root: Path) -> list[dict[str, Any]]:
    """Every wake's run record on the board, as JSON dicts. A planner's or another agent
    session's record (`kind` `planner` or `session`) is not a wake, so Jev's state and
    `_attempts` never see one."""
    folder = root / "runs"
    if not folder.is_dir():
        return []
    runs = (load_run(root, p.stem) for p in sorted(folder.glob("*.json")))
    return [run.to_dict() for run in runs if run.kind is RunKind.WAKE]


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse `KEY=VALUE` lines from `path` (blanks and `#` comments ignored).

    `{}` when the file is missing or unreadable. Never logs or raises on its content --
    this is the same file `_EXEC_WITH_SECRETS` sources into claude's environment."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip()] = value.strip()
    return found


@contextlib.contextmanager
def _secrets_in_environ(secrets: Path) -> Iterator[None]:
    """`secrets`' `KEY=VALUE` pairs in `os.environ` for the duration of the block, restored
    exactly after -- so the Jev client (which reads `TYPESAFE_API_KEY` from its own
    process's environment) sees the key without it ever reaching a command line or a
    child process's argv."""
    added = _read_env_file(secrets)
    previous = {key: os.environ.get(key) for key in added}
    os.environ.update(added)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def consult_jev(root: Path, chosen: Pick, jev: Jev, secrets: Path) -> jev_wake.WakePlan:
    """Jev's one request for this wake: go, and size per ready card, started or waiting.
    A pick with no card but a finding hands Jev the findings as its work (`sys-s22`).

    Loads `secrets` (the same file `_start` loads for claude's session) into this
    process's environment for the request only, so the Jev client sees `TYPESAFE_API_KEY`.

    Raises `BoardValidationError` when a run record under `runs/` fails validation
    (`all_runs`); `wake` catches it and skips Jev for the wake (sys-t17)."""
    with _secrets_in_environ(secrets):
        return jev_wake.consult(
            now=datetime.now(UTC),
            config=effective_config(jev.config, root),
            cards=[c.to_dict() for c in card_list(root)],
            runs=all_runs(root),
            resume=[c.id for c in chosen.resume],
            ready=[c.to_dict() for c in chosen.ready],
            findings=[line.to_dict() for line in chosen.findings],
            ask=jev.ask,
        )


def _jev_line(plan: jev_wake.WakePlan) -> str:
    go = next(e for e in plan.entries if e["question"] == "go")
    parts = [f"go {go['answer'] or 'none'} (advice)", f"width {plan.width}"]
    parts += [
        f"{c.card} {c.size}" + ("" if c.split else f" {c.tier}") for c in plan.cards.values()
    ]
    return "jev=" + ", ".join(parts)


@dataclass
class Started:
    run_id: str
    session_id: str
    resumed: Run | None  # the run whose session this one resumed
    proc: subprocess.Popen
    result_path: Path
    stderr_path: Path
    wake_log: Path
    # `browser.pool` as it stood for this session; "" when unset. `_release_leased_browsers`
    # skips its ssh work when this is "": no pool, no lease could exist.
    browser_pool: str = ""
    # How many processes the session left alive that `_wait` ended; None until it exits.
    leftovers: int | None = None
    # The session's watcher (`_WATCH`); None in a test that builds a `Started` by hand.
    watcher: subprocess.Popen | None = None
    paused: bool = False


def _follow_up_prs(
    root: Path,
    chosen: Pick,
    *,
    run_id: str,
    follow_up: Callable[..., followup.FollowUp],
    out: Callable[[str], None],
) -> None:
    """Run the PR follow-up on each resumed card that has a PR; print one
    `follow_up=<id>: …` line per card."""
    for card in chosen.resume:
        if not card.pr:
            continue
        try:
            result = follow_up(root, card, chosen.project, run_id=run_id)
        except (github.GitHubError, CommandError) as e:
            out(f"follow_up={card.id}: failed, left as it was: {e}")
            continue
        out(f"follow_up={card.id}: {result.summary}")


def _follow_up_done_prs(
    root: Path,
    *,
    only: str | None,
    follow_up_done: Callable[..., followup.DoneFollowUp],
    out: Callable[[str], None],
) -> list[str]:
    """Run the done-card follow-up (`followup.follow_up_done`, `sys-t13`) on every done
    card with a PR whose recorded base is not its project's default branch, in every
    project that is not done and, with `only`, in that project alone; print one `follow_up=<id>: …` line per card and
    return the ids of the cards it changed. A failure is printed and never stops the
    wake."""
    changed = []
    projects = {p.slug: p for p in project_list(root)}
    for card in card_list(root, stack=Stack.DONE.value):
        project = projects.get(card.project)
        if (
            project is None
            or project.done_at is not None
            or (only is not None and card.project != only)
            or not card.pr
            or card.base_branch in (None, project.default_branch)
        ):
            continue
        try:
            result = follow_up_done(root, card, project, run_id="follow-up")
        except (github.GitHubError, CommandError, followup.MergeError) as e:
            out(f"follow_up={card.id}: failed, left as it was: {e}")
            continue
        out(f"follow_up={card.id}: {result.summary}")
        if result.retargeted_to:
            changed.append(card.id)
    return changed


def _all_done(timers_off: Callable[[], list[str]] | None, *, out: Callable[[str], None]) -> int:
    """The wake's end when every project is done: say so and turn the timers off
    (`timers_off`, the CLI's `autonomouse.timer.remove`: all three crontab lines). New
    todo cards re-arm them (`autonomouse card new`). Returns 0, or 1 when the timers could
    not be removed, since the next fire would find the same board and try again."""
    out("all_done: every project is done; nothing is left to wake")
    if timers_off is None:
        return 0
    try:
        removed = timers_off()
    except Exception as e:  # noqa: BLE001 — crontab is outside the board; printed, exit 1
        out(f"timers_off=failed: {type(e).__name__}: {e}")
        return 1
    out(
        f"timers_off: removed {len(removed)} autonomouse crontab lines"
        if removed
        else "timers_off: the crontab holds no autonomouse lines"
    )
    return 0


def _run_plan_step(plan_step: Callable[..., int], out: Callable[[str], None]) -> None:
    """Run the planner on every thread waiting for it. Its lines are printed with a
    `plan_step: ` prefix; a non-zero exit or any exception is printed as `plan_step=failed`
    and swallowed, because a planner problem must never stop the wake's own work."""
    try:
        code = plan_step(out=lambda line: out(f"plan_step: {line}"))
    except Exception as e:  # noqa: BLE001 — the wake goes on whatever the planner did
        out(f"plan_step=failed: {type(e).__name__}: {e}")
        return
    out("plan_step=ok" if code == 0 else f"plan_step=failed: exit {code}")


@_stop_on_signals()
def wake(
    root: Path,
    *,
    model: str,
    effort: str,
    workers_per_wake: int,
    pause: Path,
    resume_tokens: int = DEFAULTS["ceilings"]["resume_tokens"],
    interval_hours: int = DEFAULTS["wake"]["interval_hours"],
    state_dir: Path,
    secrets: Path,
    claude: str = "claude",
    home: Home | None = None,
    max_budget_usd: float | None = None,
    project: str | None = None,
    browser_pool: str = "",
    browser_slots: int = 4,
    jev: Jev | None = None,
    pr_state: Callable[[str], str | None] = default_pr_state,
    merged_into: MergedInto = github.pr_merged_into,
    branch_merged: BranchMerged = github.branch_pr_merged,
    follow_up: Callable[..., followup.FollowUp] = followup.follow_up,
    follow_up_done: Callable[..., followup.DoneFollowUp] = followup.follow_up_done,
    sweep: Callable[[Path, str], list[SweepLine]] | None = None,
    plan_step: Callable[..., int] | None = None,
    timers_off: Callable[[], list[str]] | None = None,
    out: Callable[[str], None] = lambda line: print(line, flush=True),
) -> int:
    """Run one wake on the highest-priority project with work, or on `project` when given;
    return the exit code. One wake per machine: a wake that starts while another wake is
    alive or stalled starts nothing (exit 3). Prints `key=value` lines as it goes. A usage limit
    in force (`usage_limit_in_force`) starts no session and writes a run record saying so.
    With `jev` (config `jev.enabled`), Jev answers `go`, recorded as advice and never
    applied, and each ready card's size first (the width is counted from the picked cards,
    and each card's tier from its size), and `recover` after a failed session.

    Before picking, takes back the working folder of every `done` card whose PR has merged, and of every `cancelled` card whose project no alive
    or stalled run holds
    (`cleanup_worktrees`, `docs/board-schema.md`'s worktree lifecycle, `sys-e6`) —
    every wake does this, not only the ones that go on to start work, so a project
    with nothing left to pick still sheds its finished cards' worktrees. At the end, after
    the last session's end is recorded, does it again and removes every worktree the wake
    left that is no card's (`_clean_worktrees_at_end`, sys-u5).
    Skipped along with everything else while paused; a paused wake writes a run record
    whose `refusals` names the PAUSE file (`_record_pause`).

    Before the session starts, follows up on the PR of every in_progress card it
    resumes (`follow_up`, `autonomouse.followup`, `sys-d8`): retargets the PR when
    its base merged, and hands the card back for the next iteration when a check
    failed or a comment is unanswered. The notes it writes ride in the run's start
    commit; a `gh` failure is printed and never stops the wake.

    Before picking, and after the cleanup, follows up on the stacked PR of every done
    card in a project no alive or stalled run holds (`follow_up_done`,
    `_follow_up_done_prs`, `sys-t13`): once the PR's base has merged, retargets it to
    the default branch and merges that branch into it, and reopens the card to `todo`
    when the merge conflicts, so the pick that follows starts it in this wake. What it
    changed is committed to the board at once.

    Before all of that, runs `plan_step` (the CLI passes `autonomouse.planner.step` with
    everything but `out` bound) when given; see `_run_plan_step`.

    `sweep` (the CLI passes `autonomouse.sweep.sweep`; None asks nothing) lets `pick`
    treat a project with no resumable or ready card as having work when an open pull
    request autonomouse opened needs it (`sys-s8`); with `jev`, those findings are the
    work Jev's `go` reads (`sys-s22`).

    Once it knows no other wake is alive, it marks finished projects done
    (`autonomouse.board.done.mark_finished`, reading pull requests with `merged_into` and
    `branch_merged`), commits them, and picks only among the
    projects not done. When every project is done there is nothing to pick: it calls
    `timers_off` (the CLI passes `autonomouse.timer.remove`; None removes nothing), says
    so and returns 0 (`_all_done`). `interval_hours` (config `wake.interval_hours`) is
    what the Situation's next fire is counted from.

    `browser_pool` and `browser_slots` (config `browser.pool` and `browser.slots`) are
    passed to every `_start()` this wake makes, read once here rather than per session, so
    every project's session of this wake sees the same pool, set or unset.
    """
    if pause.exists():
        out(f"paused: {pause} exists; no run started")
        _record_pause(root, pause, model=model, effort=effort, out=out)
        return EXIT_NOT_STARTED
    model, effort = usage_limit.fallback_tier(root, model, effort)
    home = home or locate()
    if not home.charter("orchestrator.md").is_file():
        raise FileNotFoundError(f"{home.charter('orchestrator.md')} not found")

    if project is not None:
        project_show(root, project)
    control.resume_at_wake(project)
    if plan_step is not None:
        _run_plan_step(plan_step, out)
    state_dir.mkdir(parents=True, exist_ok=True)
    launch = {
        "model": model,
        "effort": effort,
        "state_dir": state_dir,
        "secrets": secrets,
        "claude": claude,
        "home": home,
        "max_budget_usd": max_budget_usd,
        "out": out,
        "interval_hours": interval_hours,
        "browser_pool": browser_pool,
        "browser_slots": browser_slots,
    }
    with open(state_dir / "run.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cleaned = cleanup_worktrees(root, pr_state=pr_state, out=out)
        if cleaned:
            commit_board(root, f"worktree cleanup: removed {', '.join(cleaned)}")
        if project is not None:
            project_show(root, project)  # an unknown slug fails loudly here
        held = live_claims(root)
        if held:
            out(
                "not started: another wake is alive or stalled, and one machine works "
                "one project at a time; " + "; ".join(held.values())
            )
            return EXIT_NOT_STARTED
        finished = mark_finished(
            root, merged_into=merged_into, branch_merged=branch_merged, out=out
        )
        if finished:
            commit_board(root, f"project done: {', '.join(finished)}")
        if every_project_done(root):
            return _all_done(timers_off, out=out)
        followed = _follow_up_done_prs(
            root, only=project, follow_up_done=follow_up_done, out=out
        )
        if followed:
            commit_board(root, f"done-card follow-up: {', '.join(followed)}")
        chosen = pick(root, workers_per_wake, only=project, sweep=sweep, out=out)
        if chosen is None:
            if project is not None and project_show(root, project).done_at is not None:
                out(f"nothing to do: project {project!r} is done")
            else:
                where = f"project {project!r} has no" if project else "no project has"
                out(f"nothing to do: {where} ready or reclaimable cards")
            return 0
        before = _worktrees_at_start(chosen.project, out=out)
        plan = None
        limit_reset = usage_limit_in_force(root, datetime.now(UTC), out=out)
        if limit_reset is not None:
            why = f"not started: a usage limit is in force until {limit_reset}"
            out(why)
            run_id = _record_no_start(root, chosen, why)
        else:
            if jev is not None:
                try:
                    plan = consult_jev(root, chosen, jev, secrets)
                except BoardValidationError as e:
                    out(f"run_record_unreadable={e}")
                else:
                    out(_jev_line(plan))
                    for reason in dict.fromkeys(
                        f"{e['question']}: {e['reason']}"
                        for e in plan.entries
                        if e["source"] == "fallback"
                    ):
                        out(f"jev_fallback={reason}")
            run_id = new_run_id()
            _follow_up_prs(root, chosen, run_id=run_id, follow_up=follow_up, out=out)
            resume, why = (
                resume_choice(chosen.stopped_run, resume_tokens=resume_tokens)
                if chosen.stopped_run is not None
                else (False, NEW_SESSION)
            )
            if chosen.stopped_run is not None:
                previous_model = chosen.stopped_run.model or ""
                if (usage_limit.fallback_tier(root, previous_model, effort)[0] != previous_model
                        or ("fable" in model.lower() and "opus" in previous_model.lower())):
                    resume, why = False, "no: model changed after a usage-limit fallback"
            started = _start(
                root,
                chosen,
                resume=resume,
                why=why,
                plan=plan,
                run_id=run_id,
                **launch,
            )

    if limit_reset is not None:
        return _no_start(root, run_id, out=out)

    # Every run this wake starts: the wake's report counts the cards each one finished.
    run_ids = [started.run_id]
    code = _wait(root, started, out)

    if (started.resumed is not None and not started.paused and code != 0 and _result(started) is None
            and not usage_limit.session_limit(started.session_id, "")):
        reason = _first_line(started.stderr_path)
        out(
            f"resume_failed=session {started.session_id} exited {code} without a "
            f"result ({reason}); starting a new session from the notes"
        )
        _record_end(
            root,
            started,
            code=code,
            summary=f"resume of session {started.session_id} failed (exit {code}: "
            f"{reason}); a new session took over from the notes",
            out=out,
        )
        commit_board(root, f"run {started.run_id}: resume failed (exit {code})")
        with open(state_dir / "run.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started = _start(root, chosen, resume=False, plan=plan, **launch)
        run_ids.append(started.run_id)
        code = _wait(root, started, out)

    # Under the decision lock from the recorded end to the retry's start, so no other wake
    # takes the project's claim while it reads as stopped.
    retries = 0
    while True:
        with open(state_dir / "run.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started.paused = (started.paused or control.pause_requested(started.run_id)
                              or control.project_paused(chosen.project.slug))
            result = _record_end(
                root,
                started,
                code=code,
                summary=f"orchestrator exited {code} without writing a summary",
                out=out,
            )
            if (
                jev is None
                or started.paused
                or _has_usage_limit(root, started.run_id)
                or not _failed(code, result)
                or _recover(
                    root,
                    started,
                    project=chosen.project.slug,
                    code=code,
                    result=result,
                    retries=retries,
                    jev=jev,
                    secrets=secrets,
                    out=out,
                )
                != "retry"
            ):
                break
            retries += 1
            commit_board(
                root, f"run {started.run_id}: failed (exit {code}); recover: retry"
            )
            started = _start(root, chosen, resume=False, plan=plan, **launch)
            run_ids.append(started.run_id)
        code = _wait(root, started, out)

    with open(state_dir / "run.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _clean_worktrees_at_end(
                root, started, chosen.project, before, pr_state=pr_state, out=out
            )
        except Exception as e:  # noqa: BLE001 — printed; the wake still ends as before
            out(f"worktree_end_cleanup=failed: {type(e).__name__}: {e}")

    try:
        _wake_report(root, chosen.project, run_ids, secrets=secrets, out=out)
    except Exception as e:  # noqa: BLE001 — printed; the report never fails the wake
        out(f"wake_report=failed: {type(e).__name__}: {e}")

    return _finish(
        root,
        started,
        project=chosen.project.slug,
        code=code,
        result=result,
        out=out,
    )


# The `resumed=` line of a session started with no dead run's session to resume.
NEW_SESSION = "no (new session)"


def _start(
    root: Path,
    chosen: Pick,
    *,
    resume: bool,
    plan: jev_wake.WakePlan | None,
    model: str,
    effort: str,
    state_dir: Path,
    secrets: Path,
    claude: str,
    home: Home,
    max_budget_usd: float | None,
    out: Callable[[str], None],
    interval_hours: int = DEFAULTS["wake"]["interval_hours"],
    run_id: str | None = None,
    why: str | None = None,
    browser_pool: str = "",
    browser_slots: int = 4,
) -> Started:
    """Start claude — resuming `chosen.stopped_run`'s session with its model and effort
    (`effort` when its record predates the field) when `resume`, else a new session —
    held before it runs anything (`_EXEC_WHEN_RECORDED`); write its run record, claim the
    project for it and commit the board; then let claude run. When any of those writes
    fails, the held process is killed and claude never ran. `run_id` is the id to use, when the caller already chose one; else a new one. `why` is
    the `resumed=` line when the caller chose it (`resume_choice`). The caller holds the
    lock.

    First it creates the run's wake log (`write_wake_log`), which every prompt names; a
    failure there raises before anything is started or written to the board.

    Every prompt carries the project's brief (`brief_prompt`, empty when it has none) and
    the owner's instructions (`instructions_prompt`, empty when it has none) and ends
    with the project's Situation (`autonomouse.situation.build`).

    `browser_pool` and `browser_slots` are `browser.pool` and `browser.slots` as this wake
    read them; when the pool is set, the session's argv gets `--mcp-config` naming one pool
    server per slot and `--strict-mcp-config`
    (`_pool_mcp_config`), so this is the only browser access the session, and every
    worker it spawns, has for its whole run — no local Playwright or Chrome DevTools MCP
    tools, and no fallback to them. For a project linked to a tracker the same config
    keeps the user's MCP server for that tracker (`_tracker_mcp_servers`), and a
    `tracker_mcp=<server names, or none>` line says which. Empty keeps today's argv,
    unchanged, where the session loads every server the user registered."""
    run_id = run_id or new_run_id()
    resumed = chosen.stopped_run if resume else None
    wake_log = write_wake_log(run_id, chosen)
    facts = situation.build(
        root,
        chosen.project,
        interval_hours=interval_hours,
        now=datetime.now().astimezone(),
        out=out,
    ).text()
    brief = brief_prompt(load_brief(root, chosen.project.slug))
    instructions = instructions_prompt(chosen.project)
    if usage_limit.fallback_tier(root, "fable", "medium")[0] == "opus":
        instructions += (
            "\nFable usage is exhausted until its recorded reset. Use opus at xhigh for "
            "every worker that would use Fable, including reclaimed cards whose assigned "
            "tier still says Fable. Read `autonomouse config show` for effective tiers.\n"
        )
    if resumed is not None:
        session_id = resumed.session_id
        model = resumed.model
        effort = resumed.effort or effort
        text = resume_prompt(
            run_id,
            chosen,
            home,
            plan,
            facts,
            wake_log=wake_log,
            brief=brief,
            instructions=instructions,
        )
        argv = [claude, "-p", text, "--resume", session_id]
    else:
        session_id = str(uuid.uuid4())
        text = prompt(
            run_id,
            chosen,
            home,
            plan,
            facts,
            wake_log=wake_log,
            brief=brief,
            instructions=instructions,
        )
        argv = [claude, "-p", text, "--session-id", session_id]
    argv += [
        "--model",
        model,
        "--effort",
        effort,
        "--dangerously-skip-permissions",
        "--output-format",
        "json",
        "--settings",
        session_end_hook.settings(),
    ]
    if max_budget_usd is not None:
        argv += ["--max-budget-usd", str(max_budget_usd)]
    if browser_pool:
        keep = _tracker_mcp_servers(chosen.project)
        if chosen.project.tracker is not None:
            out(f"tracker_mcp={','.join(sorted(keep)) or 'none'}")
        argv += ["--mcp-config", _pool_mcp_config(browser_pool, browser_slots, keep), "--strict-mcp-config"]
    result_path = state_dir / f"{run_id}.result.json"
    stderr_path = state_dir / f"{run_id}.stderr.log"
    # AUTONOMOUSE_RUN_ID reaches the session's own environment (and so the SessionEnd
    # hook's, sys-d5, registered above with --settings), so the hook knows which run
    # record its end reason belongs to without guessing from the session id, which a
    # resumed session can share with an earlier run.
    #
    # CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0: unattended mode (`-p`) otherwise gives up
    # on background workers (an Agent tool call with run_in_background) 600000ms after
    # the orchestrator's last turn and prints "Background tasks still running after
    # 600s; terminating" -- killing whatever card a worker was mid-test on. 0 means no
    # ceiling: the session waits for every worker.
    env = {
        **os.environ,
        **WATCHDOG_ENV,
        "AUTONOMOUSE_RUN_ID": run_id,
        "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
    }
    with open(result_path, "w") as stdout, open(stderr_path, "w") as stderr:
        proc = subprocess.Popen(
            ["bash", "-c", _EXEC_WHEN_RECORDED, str(secrets), *argv],
            cwd=home.root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,  # a process group of its own: `_leftovers`
        )
    watcher = None
    try:
        watcher = _watch(run_id, proc.pid, wake_log)
        run = run_start(
            root,
            run_id=run_id,
            session_id=session_id,
            pid=proc.pid,
            model=model,
            effort=effort,
            kind=RunKind.WAKE,
        )
        if plan is not None:
            run.jev = list(plan.entries)
        run.cards = [c.id for c in chosen.batch]
        save_run(root, run)
        try:
            outcome = project_claim(root, chosen.project.slug, run_id=run_id)
        except CommandError as e:
            _record_refusal(root, run_id, "project claim", str(e), out=out)
            raise
        commit_board(
            root,
            f"run {run_id}: started"
            + (f", resuming session {session_id}" if resumed else "")
            + f"; claimed {chosen.project.slug}"
            + (f" (took over {outcome.took_over})" if outcome.took_over else "")
            + f"; cards {', '.join(run.cards)}",
        )
    except BaseException:  # a `Stopped` too: the held child never outlives this
        proc.kill()  # the exact child this run started, still held; nothing else
        proc.wait()
        proc.stdin.close()
        if watcher is not None:
            _unwatch(watcher)
        raise
    proc.stdin.write(b"go\n")
    proc.stdin.close()  # claude runs from here on

    out(f"run_id={run_id}")
    out(f"pid={proc.pid}")
    out(f"session_id={session_id}")
    out(f"model={model}")
    out(f"effort={effort}")
    out("resumed=" + (why or NEW_SESSION))
    out(f"project={chosen.project.slug}")
    out(f"resume={','.join(c.id for c in chosen.resume)}")
    out(f"ready={','.join(c.id for c in chosen.started)}")
    out(f"waiting={','.join(c.id for c in chosen.waiting)}")
    out(f"secrets={secrets} ({'loaded' if os.access(secrets, os.R_OK) else 'absent'})")
    out(f"result={result_path}")
    out(f"stderr={stderr_path}")
    out(f"wake_log={wake_log}")
    return Started(
        run_id=run_id,
        session_id=session_id,
        resumed=resumed,
        proc=proc,
        result_path=result_path,
        stderr_path=stderr_path,
        wake_log=wake_log,
        browser_pool=browser_pool,
        watcher=watcher,
    )


# How long the processes a session left alive get to exit after TERM, before KILL.
LEFTOVER_GRACE_S = 5.0


def _wait(root: Path, started: Started, out: Callable[[str], None]) -> int:
    """Wait for the session to exit and print `exit=<code>`; then end every process it
    left alive (`_end_leftovers`), keep their count on `started` and stop its watcher.
    Returns the session's exit code.

    When the wait itself is cut off, the session and everything it started are ended
    before the error goes on: the session runs in a process group of its own, so a
    terminal's signal no longer reaches it. When a signal cut it off (`Stopped`: SIGINT,
    SIGTERM or SIGHUP, sys-w106), the run's end is recorded too, with exit code 128 plus
    the signal's number, and the board committed. A terminal that hung up (SIGHUP) takes
    no more lines, so on this path a line that cannot be printed is dropped, never
    raised."""
    try:
        try:
            run = load_run(root, started.run_id)
        except (CommandError, BoardValidationError):
            run = None
        while True:
            try:
                code = started.proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                if (control.pause_requested(started.run_id)
                        or (run is not None and run.project and control.project_paused(run.project))):
                    started.paused = True
                    out("paused: ending this wake and its workers; next wake may start again")
                    started.leftovers = _end_leftovers(started, out)
                    code = started.proc.wait()
                    break
                hit = usage_limit.session_limit(
                    started.session_id, run.model or "" if run else "",
                    since=datetime.fromisoformat(run.started_at) if run else None,
                )
                if hit:
                    out(f"usage_limit=ending wake: {hit['text']}")
                    started.leftovers = _end_leftovers(started, out)
                    code = started.proc.wait()
                    break
    except BaseException as e:

        def said(line: str) -> None:
            with contextlib.suppress(OSError):
                out(line)

        started.leftovers = _end_leftovers(started, said)
        _release_leased_browsers(started, said)
        if isinstance(e, Stopped):
            _record_stop(root, started, e, said)
        if started.watcher is not None:
            _unwatch(started.watcher)
        raise
    out(f"exit={code}")
    started.leftovers = _end_leftovers(started, out)
    _release_leased_browsers(started, out)
    if started.watcher is not None:
        _unwatch(started.watcher)
    return code


def _record_stop(
    root: Path, started: Started, stopped: Stopped, out: Callable[[str], None]
) -> None:
    """Record the end of `started`'s run, which `stopped`'s signal cut off after `_wait`
    ended its session and processes, and commit the board. The claim stays with the run,
    as a crashed run's does, so the next wake picks the project first; the run reads as
    stopped, so that wake starts a new session from the cards' notes."""
    name = signal.Signals(stopped.signum).name
    code = 128 + stopped.signum
    started.proc.wait()  # the session, ended above: reaped, never a zombie
    out(f"stopped={name}: the session and every process it started are ended")
    _record_end(
        root,
        started,
        code=code,
        summary=f"autonomouse run got {name}; it ended the session and every process "
        f"the session started ({started.leftovers} in all)",
        out=out,
    )
    commit_board(root, f"run {started.run_id}: ended by {name} (exit {code})")


def _leftovers(run_id: str, group: int) -> dict[int, str]:
    """Every live process run `run_id`'s session left, pid to command line: those still in
    the session's process group `group` (`_start` gives it one of its own), and those whose
    environment carries `AUTONOMOUSE_RUN_ID=<run_id>`, which `_start` sets on the session
    and every process it starts inherits. The second finds what the group misses: Claude
    Code's Bash tool runs each command in a session and process group of its own (sys-u4).
    A zombie is already dead and is left to its parent; a process this user cannot read
    is not this run's."""
    marker = f"AUTONOMOUSE_RUN_ID={run_id}".encode()
    found: dict[int, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # /proc/<pid>/stat: `pid (comm) state ppid pgrp ...`; comm may hold spaces.
            state, _, pgrp = (entry / "stat").read_text().rsplit(")", 1)[1].split()[:3]
            if state == "Z":
                continue
            if int(pgrp) != group and marker not in (
                (entry / "environ").read_bytes().split(b"\0")
            ):
                continue
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except (OSError, ValueError):
            continue  # it exited while being read, or it is another user's
        found[int(entry.name)] = cmd.decode(errors="replace").strip()
    return found


def _signal(pids: Iterable[int], sig: signal.Signals) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


def _end_processes(run_id: str, group: int) -> dict[int, str]:
    """End every live process of run `run_id` (`_leftovers`): TERM, up to
    `LEFTOVER_GRACE_S` seconds for them to exit, then KILL to any still there. Each is
    found again before it is sent KILL, so a pid another process took meanwhile is never
    touched. Returns the ended ones, pid to command line."""
    ended = _leftovers(run_id, group)
    if ended:
        _signal(ended, signal.SIGTERM)
        deadline = time.monotonic() + LEFTOVER_GRACE_S
        while (left := _leftovers(run_id, group)) and time.monotonic() < deadline:
            time.sleep(0.2)
        _signal(left, signal.SIGKILL)
        ended.update(left)
    return ended


def _end_leftovers(started: Started, out: Callable[[str], None]) -> int:
    """End every process `started`'s session left alive (`_end_processes`). Appends one
    line per ended process (pid and command) to the wake log and prints
    `leftover_processes=<count>`; returns the count."""
    ended = _end_processes(started.run_id, started.proc.pid)
    if ended:
        with started.wake_log.open("a") as log:
            for pid, cmd in ended.items():
                log.write(
                    f"- {now_iso()} autonomouse run ended process {pid}, which the "
                    f"session left alive: {cmd[:300]}\n"
                )
    out(f"leftover_processes={len(ended)}")
    return len(ended)


def _watch(run_id: str, group: int, wake_log: Path) -> subprocess.Popen:
    """Start the watcher (`_WATCH`) of run `run_id`'s session, process group `group`. It
    runs in a session of its own, so no terminal signal and no `_leftovers` of the run
    reaches it; its errors go to the wake log."""
    with wake_log.open("a") as log:
        return subprocess.Popen(
            [sys.executable, "-c", _WATCH, run_id, str(group), str(wake_log)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=log,
            start_new_session=True,
        )


def _unwatch(watcher: subprocess.Popen) -> None:
    """Tell the watcher `autonomouse run` is still here and wait for it to exit."""
    with contextlib.suppress(BrokenPipeError):
        watcher.stdin.write(b"ended\n")
        watcher.stdin.close()
    watcher.wait()


def _end_orphaned(run_id: str, group: str, wake_log: str) -> None:
    """The watcher's work once `autonomouse run` is gone: end every process of run
    `run_id` (`_end_processes`) and append one line per ended process to the wake log.
    The run record keeps no end; the next wake finds the run crashed, as before."""
    ended = _end_processes(run_id, int(group))
    with open(wake_log, "a") as log:
        log.writelines(
            f"- {now_iso()} autonomouse run was gone; its watcher ended process {pid}"
            f" of the session: {cmd[:300]}\n"
            for pid, cmd in ended.items()
        )


def _release_leased_browsers(started: Started, out: Callable[[str], None]) -> None:
    """Release every `.14` pool slot still recorded as leased for this run — the backstop
    for a `card` report whose `autonomouse browser release` never ran, because the session
    crashed or ended before step 6 (`docs/orchestrator.md`). Reads the run's lease record
    under the state dir (`browser.leased_slots`, the same folder `result_path` and
    `stderr_path` live in) and releases each slot it names over ssh (`browser.release`);
    prints `leased_browsers_released=<n>`. Prints nothing when `started.browser_pool` is
    ""; no pool, no lease could exist. A release ssh cannot reach the pool for is printed
    and left for the operator — it never fails the wake."""
    if not started.browser_pool:
        return
    state_dir = started.result_path.parent
    leased = browser.leased_slots(state_dir, started.run_id)
    released = 0
    for slot in leased:
        try:
            browser.release(started.browser_pool, started.run_id, slot, state_dir=state_dir)
            released += 1
        except browser.BrowserPoolError as e:
            out(f"leased_browser_release_failed=slot {slot}: {e}")
    out(f"leased_browsers_released={released}")


def usage_limit_in_force(
    root: Path, now: datetime, out: Callable[[str], None] = print
) -> str | None:
    """The reset time of a usage limit still in force at `now`, as recorded (ISO, UTC), or
    None: the latest `usage_limit.reset_at` on the board's run records that is still ahead
    (`autonomouse.situation.usage_limit_reset`, the rule the Situation block reports). A
    limit recorded with no reset time does not hold a wake back.

    Runs before the Jev consult (`consult_jev`) and reads every run record the same way
    (`all_runs`); a record that fails validation (`BoardValidationError`: a bad file under
    `runs/`) is printed with `out` naming the record and its error and left out of the
    scan, rather than raising before this wake's claim, the way it did before sys-t17. The
    remaining, valid records still answer the question, so a real limit recorded elsewhere
    still holds the wake back."""
    folder = root / "runs"
    if not folder.is_dir():
        return None
    runs = []
    for path in sorted(folder.glob("*.json")):
        try:
            run = load_run(root, path.stem)
            limited_model = (run.usage_limit or {}).get("model") or run.model or ""
            if "fable" not in limited_model.lower():
                runs.append(run)
        except BoardValidationError as e:
            out(f"run_record_unreadable={e}")
    return situation.usage_limit_reset(runs, now)


def _record_no_start(root: Path, chosen: Pick, summary: str) -> str:
    """The run record of a wake that starts no session (a usage limit in force): ended at
    once, no session, no cards, no claim, `summary` saying why. The caller holds the lock.
    Returns the run id."""
    run_id = new_run_id()
    at = now_iso()
    save_run(
        root,
        Run(
            id=run_id,
            project=chosen.project.slug,
            session_id=None,
            pid=None,
            model=None,
            effort=None,
            started_at=at,
            ended_at=at,
            cards=[],
            jev=[],
            notify_failures=[],
            result=None,
            end_reason=None,
            status=RunStatus.STOPPED,
            summary=summary,
            version=__version__,
        ),
    )
    return run_id


def _record_pause(
    root: Path, pause: Path, *, model: str, effort: str, out: Callable[[str], None]
) -> None:
    """The PAUSE file's refusal: a run record with no session whose `refusals` names the
    file (`run_refused`: `refused: PAUSE - <path> exists`), committed. Never raises: the
    kill switch stops the wake even when the board cannot take the record, which is
    printed as `refusal_record=failed:`."""
    try:
        run = run_refused(
            root, command="PAUSE", reason=f"{pause} exists", model=model, effort=effort
        )
        commit_board(root, f"run {run.id}: {run.summary}")
    except Exception as e:  # noqa: BLE001 — printed; a paused wake still starts nothing
        out(f"refusal_record=failed: {type(e).__name__}: {e}")
        return
    out(f"refusal_record={root / 'runs' / f'{run.id}.json'} ({run.summary})")


def _record_refusal(
    root: Path, run_id: str, command: str, reason: str, *, out: Callable[[str], None]
) -> None:
    """Append `{at, command, reason}` to the run's `refusals` (`record_refusal`, sys-j5)
    and print `refusal=<command>: <reason>`. A record that cannot take it is printed as
    `refusal_record=failed:`; the caller goes on as it would have."""
    try:
        record_refusal(root, run_id, command, reason)
    except (CommandError, BoardValidationError, OSError) as e:
        out(f"refusal_record=failed: {type(e).__name__}: {e}")
        return
    out(f"refusal={command}: {reason}")


# claude's result when a session stops on `--max-budget-usd` (claude 2.1.283): its
# `subtype`, and the `terminal_reason` beside it.
BUDGET_SUBTYPE = "error_max_budget_usd"
BUDGET_TERMINAL_REASON = "budget_exhausted"


def _budget_refusal(result: dict | None) -> str | None:
    """Why the session ended on `--max-budget-usd`, from claude's printed result, or None
    when it did not. The reason quotes each of the result's `errors` that names the budget
    (claude writes `Reached maximum budget ($5)`); when none does, it says what the result
    holds: its `subtype`, `terminal_reason`, `errors` and `total_cost_usd`."""
    if not isinstance(result, dict) or not (
        result.get("subtype") == BUDGET_SUBTYPE
        or result.get("terminal_reason") == BUDGET_TERMINAL_REASON
    ):
        return None
    errors = result.get("errors")
    named = [
        e for e in (errors if isinstance(errors, list) else [])
        if isinstance(e, str) and "budget" in e.lower()
    ]
    if named:
        return "the session ended on --max-budget-usd: " + "; ".join(named)
    held = ", ".join(
        f"{key} {result[key]!r}"
        for key in ("subtype", "terminal_reason", "errors", "total_cost_usd")
        if key in result
    )
    return f"the session ended on --max-budget-usd; its result names no budget: {held}"


def _no_start(root: Path, run_id: str, *, out: Callable[[str], None]) -> int:
    """After a wake that started no session (a usage limit in force): print the run
    record's outcome and commit the board."""
    run = load_run(root, run_id)
    out(f"run_id={run_id}")
    out(f"record={root / 'runs' / f'{run_id}.json'} ({run.summary})")
    out(_behaviour_log(root, run_id))
    commit_board(root, f"run {run_id}: {run.summary}")
    return 0


def _result(started: Started) -> dict | None:
    """The JSON object claude printed, or None."""
    try:
        parsed = json.loads(started.result_path.read_text())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_line(path: Path) -> str:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    return lines[0] if lines else "no stderr"


def _has_usage_limit(root: Path, run_id: str) -> bool:
    try:
        return load_run(root, run_id).usage_limit is not None
    except BoardValidationError:
        return False


def _record_end(
    root: Path,
    started: Started,
    *,
    code: int,
    summary: str,
    out: Callable[[str], None],
) -> dict | None:
    """Copy claude's result into the run record and end it, with the usage limit when one
    was hit and the process's own `code` (`proc.wait()`); `summary` only when the
    orchestrator wrote none. Returns the result.

    A record this version of autonomouse refuses (`BoardValidationError`: a merge under the
    live wake changed a field's shape, sys-d31) is ended with `run_end_unchecked` instead,
    which writes the same end into its JSON; the refusal is printed as `record_invalid=`.
    So the result always reaches the record and the wake goes on to release its claim."""
    if started.paused:
        summary = "paused by command; work may resume at the next wake"
    result = _result(started)
    hit = usage_limit.detect(
        _result_text(result) + "\n" + started.stderr_path.read_text(errors="replace"),
        hit_at=datetime.now(UTC),
    )
    try:
        run_before = load_run(root, started.run_id)
    except BoardValidationError:
        run_before = None
    limit = usage_limit.session_limit(
        started.session_id, run_before.model or "" if run_before else "",
        since=datetime.fromisoformat(run_before.started_at) if run_before else None,
    ) or (run_before.usage_limit if run_before else None) or (hit.to_dict() if hit is not None else None)
    try:
        if load_run(root, started.run_id).summary is not None:
            summary = None
        run = run_end(
            root,
            started.run_id,
            result=result,
            summary=summary,
            exit_code=code,
            leftover_processes=started.leftovers,
        )
        if limit is not None:
            run.usage_limit = limit
            save_run(root, run)
    except BoardValidationError as e:
        out(f"record_invalid={e}; the end is written into the record's JSON as it is")
        run_end_unchecked(
            root,
            started.run_id,
            result=result,
            summary=summary,
            usage_limit=limit,
            exit_code=code,
            leftover_processes=started.leftovers,
        )
    out(
        f"record={root / 'runs' / f'{started.run_id}.json'} (result "
        + (
            "recorded)"
            if result is not None
            else f"none: no JSON printed; see {started.stderr_path})"
        )
    )
    if limit is not None:
        out(
            f"usage_limit=hit at {limit['hit_at']}, resets "
            f"{limit['reset_at'] or 'unknown'} ({limit['text']})"
        )
    if started.paused:
        control.acknowledge(started.run_id)
    if (budget := _budget_refusal(result)) is not None:
        _record_refusal(root, started.run_id, "claude --max-budget-usd", budget, out=out)
    out(_behaviour_log(root, started.run_id))
    return result


def _behaviour_log(root: Path, run_id: str) -> str:
    """Derive and write the run's behaviour log; the `key=value` line saying where it
    went, or why it failed. Never raises: the log must not fail the wake."""
    try:
        path = behaviour_log.write(root, run_id)
        reason = behaviour_log.read_summary(root, run_id).get("reason")
    except Exception as e:  # noqa: BLE001 — any failure is reported, never raised
        return f"behaviour_log=failed: {type(e).__name__}: {e}"
    return f"behaviour_log={path}" + (f" ({reason})" if reason else "")


def _result_text(result: dict | None) -> str:
    """The final text of claude's printed result, or empty."""
    text = (result or {}).get("result")
    return text if isinstance(text, str) else ""


def _failed(code: int, result: dict | None) -> bool:
    """A session failed: it exited non-zero, printed no result, or its result is an error."""
    return code != 0 or result is None or result.get("is_error") is True


def _failure_text(started: Started, code: int, result: dict | None) -> str:
    """The error `recover` reads: the exit, and the tail of the result or else of stderr."""
    head = f"session exited {code}" + ("; printed no result" if result is None else "")
    if result is not None and result.get("is_error") is True:
        head += "; result is_error true"
    tail = _result_text(result) or started.stderr_path.read_text(errors="replace")
    tail = tail.strip()[-RESULT_TEXT_TAIL:]
    return f"{head}: {tail}" if tail else head


def _attempts(runs: list[dict[str, Any]], project: str) -> int:
    """How many of `project`'s sessions failed in a row, newest first (at least 1: the
    caller asks after a failure). Run records with no session (a wake Jev skipped) are not
    attempts."""
    sessions = sorted(
        (r for r in runs if r.get("project") == project and r.get("session_id")),
        key=lambda r: r.get("started_at") or "",
        reverse=True,
    )
    count = 0
    for run in sessions:
        result = run.get("result")
        if not (result is None or result.get("is_error") is True):
            break
        count += 1
    return max(count, 1)


def _recover(
    root: Path,
    started: Started,
    *,
    project: str,
    code: int,
    result: dict | None,
    retries: int,
    jev: Jev,
    secrets: Path,
    out: Callable[[str], None],
) -> str:
    """Ask Jev `recover` about the failed session `started`, record the answer in its run
    record's `jev` list, notify the human on `raise`, and return the applied option.

    The state is the wake's base state plus the failure: the error text, how many of the
    project's sessions failed in a row (never fewer than this wake's own failed sessions:
    `_attempts` reads a session's failure from its result's `is_error`, not its own
    `exit_code`, so a nonzero exit with no error in the printed result would not count),
    and the usage limit's reset time when one was hit.
    An answer below `recover`'s floor, or a failed request, is `retry`: a new session from
    the notes. A run record this version refuses (`BoardValidationError`, sys-d31) is
    `next_wake`: the wake goes on to release its claim, and nothing is recorded on the
    record. A `retry` after `ceilings.retries_per_wake` retries this wake (the effective
    config's, `jev.config`) is applied as `next_wake`, with the reason on the run record
    and no notify; only a usable `raise` from Jev notifies the human."""
    try:
        runs = all_runs(root)
        limit = load_run(root, started.run_id).usage_limit
    except BoardValidationError as e:
        out(f"recover=next_wake (a run record cannot be read: {e})")
        return "next_wake"
    state = recover_state(
        base_state(
            now=datetime.now(UTC),
            config=jev.config,
            cards=[c.to_dict() for c in card_list(root)],
            runs=runs,
        ),
        error=_failure_text(started, code, result),
        attempts=max(_attempts(runs, project), retries + 1),
        reset_at=limit["reset_at"] if limit else None,
    )
    try:
        with _secrets_in_environ(secrets):
            answers = jev.ask(state, questions_body([RECOVER]), timeout=30.0)
        verdict = RECOVER.decide(answers.get(RECOVER.key))
        applied, reason = verdict.applied, verdict.fallback_reason
        pick, confidence = verdict.pick, verdict.confidence
        probabilities = verdict.probabilities
    except (JevError, OSError, ValueError, KeyError) as exc:
        applied, reason = "retry", f"Jev request failed: {exc!r}"
        pick, confidence, probabilities = None, None, None
    cap = jev.config["ceilings"]["retries_per_wake"]
    if applied == "retry" and retries >= cap:
        applied = "next_wake"
        reason = "; ".join(
            r
            for r in (
                reason,
                f"{retries} retries this wake already (ceilings.retries_per_wake is {cap})",
            )
            if r
        )
    source = "jev" if reason is None else "fallback"
    run = load_run(root, started.run_id)
    run.jev.append(
        {
            "question": "recover",
            "card": None,
            "source": source,
            "answer": pick,
            "confidence": confidence,
            "probabilities": None if probabilities is None else dict(probabilities),
            "applied": applied,
            "reason": reason,
        }
    )
    save_run(root, run)
    out(
        f"recover={applied} (attempts {state['failure']['attempts']}, "
        f"reset_at {state['failure']['reset_at'] or 'none'}, source {source}"
        + (f": {reason})" if reason else ")")
    )
    if applied == "raise":
        with _secrets_in_environ(secrets):
            notify(
                human_messages.wake_failed(
                    project, state["failure"]["error"][:400], state["failure"]["attempts"]
                ),
                root=root,
                run_id=started.run_id,
                config=jev.config,
                html=True,
            )
    return applied


def _worktrees_at_start(project: Project, *, out: Callable[[str], None]) -> set[Path] | None:
    """The worktrees registered in `project`'s repository as the wake starts, resolved: the
    wake's end never removes one of them, since any may be the human's (sys-u5). `None`,
    printed as `worktrees_at_start=failed`, when git cannot list them; the wake's end then
    removes none."""
    try:
        return {path.resolve() for path in registered(Path(project.repo_path))}
    except WorktreeError as e:
        out(f"worktrees_at_start=failed, none is removed at the wake's end: {e}")
        return None


def _clean_worktrees_at_end(
    root: Path,
    started: Started,
    project: Project,
    before: set[Path] | None,
    *,
    pr_state: Callable[[str], str | None],
    out: Callable[[str], None],
) -> None:
    """The wake's end leaves no worktree it did not mean to keep (sys-u5). First it runs
    `cleanup_worktrees` again, so a card that finished with its PR merged, or was
    cancelled, during this wake sheds its worktree now. Then it removes with `git worktree
    remove --force` every worktree registered in `project`'s repository that is not in
    `before` (`_worktrees_at_start`) and is no card's `worktree`, such as a review fork's
    checkout of a PR, keeping its branch. Each removal, or a removal git refuses, is printed
    and written as one line to the wake log."""
    cleaned = cleanup_worktrees(root, pr_state=pr_state, run_id=started.run_id, out=out)
    if cleaned:
        out(f"worktree_end_cleanup=removed {', '.join(cleaned)}")
    if before is None:
        return
    repo = Path(project.repo_path)
    cards = {Path(c.worktree).resolve() for c in card_list(root) if c.worktree}
    for path, branch in registered(repo).items():
        if path.resolve() in before or path.resolve() in cards:
            continue
        try:
            remove_registered(repo, path)
        except WorktreeError as e:
            out(f"worktree_remove_failed path={path} error={e}")
            entry = f"could not remove worktree {path}, which no card owns: {e}"
        else:
            out(f"worktree_removed path={path} branch={branch or 'detached'}")
            kept = f"its branch {branch} is kept" if branch else "it had no branch"
            entry = (
                f"removed worktree {path}: it was registered during the wake and no card "
                f"owns it; {kept}"
            )
        with started.wake_log.open("a") as log:
            log.write(f"\n- {now_iso()} The run controller {entry}.\n")


def _open_prs(repo: str) -> list[dict[str, Any]]:
    """The open pull requests of `repo` (`<owner>/<name>`) from `gh pr list` (up to 200),
    each a dict with `headRefName`, `baseRefName` and `isCrossRepository`. Raises
    `github.GitHubError` when `gh` fails or prints something else."""
    args = [
        "pr", "list", "--repo", repo, "--state", "open", "--limit", "200",
        "--json", "headRefName,baseRefName,isCrossRepository",
    ]
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise github.GitHubError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise github.GitHubError(f"gh {' '.join(args)} returned non-JSON: {e}") from e
    if not isinstance(prs, list):
        raise github.GitHubError(f"gh {' '.join(args)} returned no list: {result.stdout!r}")
    return prs


def _waiting_prs(root: Path, project: Project, prs: list[dict[str, Any]]) -> int:
    """How many of `prs` (`_open_prs`) wait for the owner to merge: autonomouse's own into
    `project`'s default branch or stacked on a card's branch. The sweep decides what is
    autonomouse's own (`autonomouse.sweep.is_own_branch`): a head branch in the project's
    repository itself, never a fork's, that a card of the project records or that starts
    with `card/` or `wake/`. A stacked base is a card's branch by the same reading of
    cards: a branch a card records or one starting with `card/`. A card's PR into the
    wake's shared branch (`wake/`) is the worker's to merge, not the owner's."""
    recorded = recorded_branches(root, project.slug)
    return sum(
        1
        for pr in prs
        if is_own_branch(pr["headRefName"], recorded)
        and not pr["isCrossRepository"]
        and (
            pr["baseRefName"] == project.default_branch
            or pr["baseRefName"] in recorded
            or pr["baseRefName"].startswith("card/")
        )
    )


def _cards_finished(root: Path, project: str, run_ids: list[str]) -> int:
    """How many of `project`'s cards are `done` and were moved there by one of `run_ids`,
    the runs of this wake (each card counted once)."""
    return sum(
        1
        for card in card_list(root, project=project, stack=Stack.DONE.value)
        if any(h.to == Stack.DONE.value and h.run_id in run_ids for h in card.history)
    )


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _wake_report(
    root: Path,
    project: Project,
    run_ids: list[str],
    *,
    secrets: Path,
    out: Callable[[str], None],
) -> None:
    """Send the owner the wake's one report (sys-w23): the project by its name, how many
    cards this wake's runs finished, and how many pull requests wait for the owner to
    merge (`_waiting_prs`). A wake that failed sends it too, with the counts it reached,
    after its failure notice. When GitHub cannot be read, the message says so in place of
    the pull request count and the error is printed as `wake_report_prs=failed:`. A send
    that fails is recorded on the last run's `notify_failures` (`notify`) and never fails
    the wake."""
    finished = _count(_cards_finished(root, project.slug, run_ids), "card")
    try:
        waiting = _waiting_prs(root, project, _open_prs(github.repo_slug(project.repo)))
    except github.GitHubError as e:
        out(f"wake_report_prs=failed: {e}")
        prs = "not counted, GitHub could not be read"
    else:
        prs = _count(waiting, "pull request")
    text = human_messages.wake_report(project.name, finished, prs)
    out(f"wake_report={' / '.join(text.splitlines())}")
    with _secrets_in_environ(secrets):
        notify(text, root=root, run_id=run_ids[-1], html=True)


def _finish(
    root: Path,
    started: Started,
    *,
    project: str,
    code: int,
    result: dict | None,
    out: Callable[[str], None],
) -> int:
    """After the last session's end is recorded: claim released and board committed.
    Returns the run's exit code: the orchestrator session's own, or 1 when it left no
    result.

    Each of the two steps runs even when the one before it failed, and a failure is
    printed (`claim=failed:`, `commit=failed:`) and never changes the exit code either:
    a wake that raised here would leave the claim held and exit 1, whatever the session
    did (sys-d31)."""
    run_id = started.run_id

    try:
        released = project_show(root, project).claim.run_id == run_id
        if released:
            project_release(root, project, run_id=run_id)
        out(f"claim={'released' if released else 'already released'}")
    except Exception as e:  # noqa: BLE001 — printed; the wake still ends with the session's code
        released = False
        out(f"claim=failed: {type(e).__name__}: {e}")

    try:
        commit_board(
            root,
            f"run {run_id}: ended (exit {code}); result "
            + ("recorded" if result is not None else "missing")
            + ("; claim released" if released else ""),
        )
    except Exception as e:  # noqa: BLE001 — printed; the wake still ends with the session's code
        out(f"commit=failed: {type(e).__name__}: {e}")

    if started.paused:
        return 0
    if code != 0:
        return code
    return 1 if result is None else 0
