"""Thin SSH client over a Chrome pool, `browsermaze`
(https://github.com/riodeuno/browsermaze, which owns the server side).

The pool runs several logged-in Chromes behind a golden profile; a slot is one of them,
leased to one run at a time so its login never serves two cards at once
(`docs/config.md`, `browser.pool`). `pool` is what `browser.pool` holds, `user@host`: every
call here SSHes to it as given (so the user's own ssh config applies; no key or user
override) and runs one `browsermaze` subcommand, mirroring `github.py`'s `gh` wrapper: a
plain function per operation, a `BrowserPoolError` naming the shell command and what it
printed on failure.

`lease` and `release` also keep the run's own record of which slots it holds, under
the run's state dir (`controller.default_state_dir()`, the same folder `result.json`
and `stderr.log` live in) — `controller._release_leased_browsers` reads it as the
crash-safety backstop for a `browser release` that never ran.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# ssh's own connect bound, so an unreachable pool fails fast rather than hanging the
# orchestrator's turn on a dropped connection.
_CONNECT_TIMEOUT_S = 10

# How long one `lease` call waits for a slot to free, and how often it asks. The orchestrator
# runs `autonomouse browser lease` through Claude Code's Bash tool, which moves a call
# still running after its 120 s default timeout to the background (`docs/orchestrator.md`,
# "A wake", step 7): a lease that finishes there holds a slot the orchestrator never saw.
# An ask starts no later than 80 s in and takes at most `timeout` (30 s), so a call ends by
# 110 s with no `timeout` argument to remember. A bounded call also hands the turn back, so the
# orchestrator can handle a worker's report, which releases a slot, before it asks again
# (`cli/browser.py`).
_WAIT_S = 80.0
_POLL_S = 10.0

# The pool's answer to `lease` when every slot is taken (`browsermaze`'s `cmd_lease`).
_FULL = re.compile(r"no free slot - all (\d+) are leased")


class BrowserPoolError(Exception):
    """An `ssh <pool> browsermaze ...` call failed, or its output could not be read;
    the message names the command and what it printed, safe to print as is."""


class PoolFullError(BrowserPoolError):
    """`browsermaze lease` answered "no free slot - all N are leased": the pool is
    reachable and every slot is held. `leased` is N. The one failure `lease` waits out."""

    def __init__(self, message: str, leased: int):
        super().__init__(message)
        self.leased = leased


@dataclass(frozen=True)
class Lease:
    """One slot leased from the pool: `slot` is `browsermaze release`'s argument, `url`
    the Playwright MCP server's `--cdp-endpoint` (`http://<host>:930<slot>`)."""

    slot: int
    url: str


def host(pool: str) -> str:
    """The machine of a `user@host` pool, which the slots' CDP ports are on."""
    return pool.rpartition("@")[2]


def _run_ssh(pool: str, *args: str, timeout: float) -> str:
    command = f"ssh {pool} browsermaze {' '.join(args)}"
    try:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                f"ConnectTimeout={_CONNECT_TIMEOUT_S}",
                pool,
                "browsermaze",
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise BrowserPoolError(f"{command} timed out after {timeout}s") from e
    except OSError as e:
        raise BrowserPoolError(f"could not run ssh: {e}") from e
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        full = _FULL.search(reason)
        if full:
            raise PoolFullError(f"{command} failed: {reason}", int(full.group(1)))
        raise BrowserPoolError(f"{command} failed: {reason}")
    return result.stdout


def lease(
    pool: str,
    run_id: str,
    *,
    state_dir: Path,
    timeout: float = 30.0,
    wait: float = _WAIT_S,
    poll: float = _POLL_S,
) -> Lease:
    """Lease a free slot from `pool` for `run_id`, and record it under `state_dir` so the
    crash backstop can release it even after this process exits.

    When every slot is leased, asks again every `poll` seconds for `wait` seconds, saying
    on stderr that it waits and how many slots are leased, and leases the slot that frees.
    Raises `PoolFullError` when the pool is still full after `wait`: the caller calls again
    to keep waiting. Every other failure raises `BrowserPoolError` at once, naming why: ssh's
    own when `pool` is unreachable, or a pool answer that is not a slot.
    """
    deadline = time.monotonic() + wait
    while True:
        try:
            printed = _run_ssh(pool, "lease", timeout=timeout).strip()
            break
        except PoolFullError as full:
            if time.monotonic() + poll > deadline:
                raise
            print(
                f"waiting for a free browser slot: all {full.leased} are leased;"
                f" asking again in {poll:g}s",
                file=sys.stderr,
            )
            time.sleep(poll)
    parts = printed.split()
    if len(parts) != 2 or not parts[0].isdigit():
        raise BrowserPoolError(f"browsermaze lease printed an unexpected line: {printed!r}")
    leased = Lease(slot=int(parts[0]), url=parts[1])
    _add_lease(state_dir, run_id, leased.slot)
    return leased


def release(pool: str, run_id: str, slot: int, *, state_dir: Path, timeout: float = 30.0) -> None:
    """Release `slot` back to `pool`'s free list, and drop it from `run_id`'s lease
    record. Raises `BrowserPoolError` the same way `lease` does."""
    _run_ssh(pool, "release", str(slot), timeout=timeout)
    _remove_lease(state_dir, run_id, slot)


def logins(pool: str, *, timeout: float = 30.0) -> list[str]:
    """Every site `pool`'s golden Chrome profile is logged in to: the cookie host of
    each session cookie, deduplicated and sorted (`browsermaze logins`'s own contract,
    built in browsermaze)."""
    printed = _run_ssh(pool, "logins", timeout=timeout)
    return [line.strip() for line in printed.splitlines() if line.strip()]


def _covers(site: str, domain: str) -> bool:
    """`site` is `domain` itself, or a parent domain of it (`has_login`'s rule,
    `docs/orchestrator/access-checks.md`): `github.com` covers `gist.github.com`."""
    return domain == site or domain.endswith(f".{site}")


def has_login(pool: str, domain: str, *, timeout: float = 30.0) -> tuple[bool, str]:
    """Whether `domain`, or a parent domain of it, is among `pool`'s logins (`logins`),
    and the line that says why — meant as a card's `access` check
    (`docs/orchestrator/access-checks.md`): `autonomouse browser has-login <domain>`
    exits 0 only when this is true."""
    sites = logins(pool, timeout=timeout)
    match = next((site for site in sites if _covers(site, domain)), None)
    if match is not None:
        via = "" if match == domain else f", the parent domain of {domain}"
        return True, f"{pool} is logged in to {match}{via}"
    return False, (
        f"{pool} is not logged in to {domain} or any parent domain of it; its logins: "
        + (", ".join(sites) if sites else "none")
    )


def _lease_record_path(state_dir: Path, run_id: str) -> Path:
    return state_dir / f"{run_id}.leases.json"


def leased_slots(state_dir: Path, run_id: str) -> list[int]:
    """The slots `run_id`'s lease record still names, sorted; `[]` when there is none, or
    it cannot be read — nothing to release either way (`controller._release_leased_browsers`
    reads this as its crash-safety backstop)."""
    try:
        slots = json.loads(_lease_record_path(state_dir, run_id).read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return sorted(slot for slot in slots if isinstance(slot, int))


def _add_lease(state_dir: Path, run_id: str, slot: int) -> None:
    slots = set(leased_slots(state_dir, run_id))
    slots.add(slot)
    _write_leases(state_dir, run_id, slots)


def _remove_lease(state_dir: Path, run_id: str, slot: int) -> None:
    slots = set(leased_slots(state_dir, run_id))
    slots.discard(slot)
    _write_leases(state_dir, run_id, slots)


def _write_leases(state_dir: Path, run_id: str, slots: set[int]) -> None:
    path = _lease_record_path(state_dir, run_id)
    if not slots:
        path.unlink(missing_ok=True)
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(slots)))
