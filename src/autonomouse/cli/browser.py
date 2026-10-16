"""`autonomouse browser`: lease and release a slot on the Chrome pool, and read what
it is logged in to (`docs/config.md`, `browser.pool`; `src/autonomouse/browser.py`)."""

import os
import sys

from autonomouse import browser, controller
from autonomouse.cli.shared import _fail
from autonomouse.config import load_config

# `browser lease`'s exit status when the pool is reachable and still full after its wait:
# 75 is EX_TEMPFAIL, "try again". Every other failure exits 1.
STILL_FULL = 75


def register(subparsers) -> None:
    browser_parser = subparsers.add_parser(
        "browser",
        help="lease/release a slot on browser.pool, or read/check its logins",
        description=(
            "'browser lease' SSHes browser.pool and leases a free slot for this run "
            "(AUTONOMOUSE_RUN_ID from the environment), printing 'N http://<host>:930N'. "
            "When every slot is leased it waits, for under two minutes, for one to free, "
            "saying so on stderr, and exits 75 if the pool is still full: run it again "
            "to keep waiting. It fails at once, exit 1, naming why, when the pool is "
            "unreachable or answers anything else. "
            "'browser release <n>' releases slot n. 'browser logins' lists every site "
            "the pool's golden Chrome profile is logged in to. 'browser has-login "
            "<domain>' exits 0 when that domain, or a parent domain of it, is in that "
            "list and 1 otherwise, printing why — meant as a card's access check "
            "(docs/orchestrator/access-checks.md)."
        ),
    )
    browser_sub = browser_parser.add_subparsers(dest="browser_command")

    browser_sub.add_parser(
        "lease", help="lease a free slot for this run (AUTONOMOUSE_RUN_ID)"
    )

    release_parser = browser_sub.add_parser("release", help="release a leased slot")
    release_parser.add_argument("slot", type=int)

    browser_sub.add_parser(
        "logins", help="every site the pool's golden Chrome profile is logged in to"
    )

    has_login_parser = browser_sub.add_parser(
        "has-login",
        help="exit 0 when a domain (or a parent domain of it) is logged in, 1 otherwise",
    )
    has_login_parser.add_argument("domain")

    browser_parser.set_defaults(_parser=browser_parser)


def _pool() -> str:
    pool = load_config()["browser"]["pool"]
    if not pool:
        _fail("browser: browser.pool is not set in config.toml (docs/config.md)")
    return pool


def _run_id() -> str:
    run_id = os.environ.get("AUTONOMOUSE_RUN_ID")
    if not run_id:
        _fail("browser: AUTONOMOUSE_RUN_ID is not set in the environment")
    return run_id


def run(args) -> None:
    pool = _pool()
    state_dir = controller.default_state_dir()
    try:
        if args.browser_command == "lease":
            leased = browser.lease(pool, _run_id(), state_dir=state_dir)
            print(f"{leased.slot} {leased.url}")
        elif args.browser_command == "release":
            browser.release(pool, _run_id(), args.slot, state_dir=state_dir)
            print(f"released {args.slot}")
        elif args.browser_command == "logins":
            for site in browser.logins(pool):
                print(site)
        elif args.browser_command == "has-login":
            ok, why = browser.has_login(pool, args.domain)
            print(why)
            raise SystemExit(0 if ok else 1)
        else:
            args._parser.error(
                "choose a browser command: lease, release, logins, has-login"
            )
    except browser.PoolFullError as e:
        print(
            f"browser: all {e.leased} slots are still leased; run"
            " 'autonomouse browser lease' again to keep waiting",
            file=sys.stderr,
        )
        raise SystemExit(STILL_FULL)
    except browser.BrowserPoolError as e:
        _fail(str(e))
