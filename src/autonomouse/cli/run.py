"""`autonomouse run`: one wake, or the run records."""

import json
import sys
from pathlib import Path

from autonomouse import controller, sweep
from autonomouse.board import commands as board_commands
from autonomouse.board import runs as board_runs
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError
from autonomouse.board.store import GitCommitError
from autonomouse.cli.shared import _RECORD_ERRORS, _env_default, _fail, _plan_step
from autonomouse.config import (
    ORCHESTRATOR_EFFORTS,
    config_path,
    load_config,
    pause_path,
    secrets_path,
    worker_tiers,
)


def register(subparsers) -> None:
    run_parser = subparsers.add_parser(
        "run",
        help="run one wake (no subcommand), or read and write run records",
        description=(
            "Without a subcommand: one wake. Exit 3 if the PAUSE file exists. Run "
            "`plan step` (a failure is printed and never changes the exit code). Pick a "
            "free project with work (one whose claim a stopped or crashed run holds first, "
            "then by priority; --project forces one); when none is free and a project a "
            "live run holds has ready cards, start the orchestrator waiting for that "
            "project (run record with waiting_for, no claim; exit 3 when no held project "
            "has a ready card). Start the "
            "orchestrator with `claude -p` (resuming the dead run's session only when it "
            "crashed and holds fewer than ceilings.resume_tokens tokens, else a new "
            "session, also when that resume fails), wait for it, record its "
            "result, release the claim and back up the board."
        ),
    )
    run_parser.add_argument(
        "--model",
        dest="wake_model",
        metavar="MODEL",
        help="the orchestrator session's model (default: orchestrator.model in the "
        "config, fable)",
    )
    run_parser.add_argument(
        "--effort",
        dest="wake_effort",
        choices=ORCHESTRATOR_EFFORTS,
        help="the orchestrator session's reasoning effort (default: "
        "orchestrator.effort in the config, medium)",
    )
    run_parser.add_argument(
        "--project",
        dest="wake_project",
        metavar="SLUG",
        help="wake this project only (held by a live run: the orchestrator waits for it)",
    )
    run_parser.add_argument(
        "--max-budget-usd",
        type=float,
        help="passed to claude: the session stops once it has spent this much",
    )
    run_sub = run_parser.add_subparsers(dest="run_command")

    start_parser = run_sub.add_parser(
        "start", help="write runs/<id>.json with status alive"
    )
    start_parser.add_argument("id", nargs="?", help="default: a new run id")
    start_parser.add_argument(
        "--session-id", help="default: $CLAUDE_CODE_SESSION_ID of this shell"
    )
    start_parser.add_argument(
        "--pid", type=int, help="default: $CLAUDE_PID of this shell"
    )
    start_parser.add_argument("--model", required=True)
    start_parser.add_argument("--json", action="store_true")

    end_parser = run_sub.add_parser(
        "end", help="record the run's end, its captured result and summary"
    )
    end_parser.add_argument("id")
    end_parser.add_argument(
        "--result",
        help="file holding the JSON result `claude -p` printed; - reads stdin",
    )
    end_parser.add_argument("--summary")
    end_parser.add_argument("--json", action="store_true")

    status_parser = run_sub.add_parser(
        "status",
        help="alive / stalled / stopped / crashed / taken_over, with the evidence",
    )
    status_parser.add_argument("id")
    status_parser.add_argument("--json", action="store_true")

    note_parser = run_sub.add_parser(
        "note",
        help="append a card id and/or a jev entry to a run record, or set its interim PR",
    )
    note_parser.add_argument("id")
    note_parser.add_argument("--card", help="card id to append to `cards`")
    note_parser.add_argument(
        "--jev",
        help=(
            "JSON object to append to `jev`: "
            "{question, card, source, answer, confidence, applied}, "
            "with an optional `reason`"
        ),
    )
    note_parser.add_argument(
        "--interim-pr",
        help="URL of the final PR opened from the wake's interim branch (`interim_pr`)",
    )
    note_parser.add_argument("--json", action="store_true")

    interim_parser = run_sub.add_parser(
        "interim",
        help="make the wake's interim branch wake/<run id> and print its name",
        description=(
            "Push wake/<run id> to the origin of the run record's project from "
            "origin/<default_branch>, write it as the record's interim_branch and print "
            "the branch name. A branch already on origin and a record already naming it "
            "are left as they are, so a second run prints the same name and changes "
            "nothing. Refused, with nothing written, when the record does not exist or "
            "names no project, or when git fails. Commits nothing. The orchestrator runs "
            "it only after the three reshaping moves failed "
            "(docs/orchestrator/conflict-check.md)."
        ),
    )
    interim_parser.add_argument("id", help="the run id")

    run_parser.set_defaults(_parser=run_parser)


def run(args) -> None:
    root = board_root()
    if args.run_command is None:
        _run_wake(root, args)
        return
    try:
        if args.run_command == "start":
            session_id = _env_default(
                args.session_id, "CLAUDE_CODE_SESSION_ID", "--session-id"
            )
            pid = int(_env_default(args.pid, "CLAUDE_PID", "--pid"))
            run = board_runs.run_start(
                root,
                run_id=args.id or controller.new_run_id(),
                session_id=session_id,
                pid=pid,
                model=args.model,
            )
            _print_run(run, as_json=args.json)
        elif args.run_command == "end":
            result = _read_result(args.result) if args.result else None
            run = board_runs.run_end(root, args.id, result=result, summary=args.summary)
            _print_run(run, as_json=args.json)
        elif args.run_command == "status":
            report = board_runs.run_status(root, args.id)
            if args.json:
                print(json.dumps(report.to_dict(), indent=2))
            else:
                print(f"{report.status.value}: {report.reason}")
        elif args.run_command == "note":
            jev_entry = None
            if args.jev is not None:
                try:
                    jev_entry = json.loads(args.jev)
                except json.JSONDecodeError as e:
                    _fail(f"--jev is not JSON — {e}")
            run = board_runs.run_note(
                root, args.id, card=args.card, jev=jev_entry, interim_pr=args.interim_pr
            )
            _print_run(run, as_json=args.json)
        elif args.run_command == "interim":
            print(board_runs.run_interim(root, args.id).interim_branch)
        else:
            args._parser.error("choose a run command: start, end, status, note, interim")
    except (board_commands.CommandError, BoardValidationError, OSError) as e:
        _fail(str(e))


def _run_wake(root: Path, args) -> None:
    config = load_config()
    model = args.wake_model or config["orchestrator"]["model"]
    effort = args.wake_effort or config["orchestrator"]["effort"]
    if effort not in ORCHESTRATOR_EFFORTS:
        _refuse_wake(
            root,
            f"orchestrator.effort in {config_path()} must be one of "
            f"{', '.join(ORCHESTRATOR_EFFORTS)} — got {effort!r}",
            model=model,
            effort=effort,
        )
    try:
        worker_tiers(config)  # a card's size picks its worker's tier from this table
    except ValueError as e:
        _refuse_wake(root, f"{e} (in {config_path()})", model=model, effort=effort)
    try:
        code = controller.wake(
            root,
            model=model,
            effort=effort,
            workers_per_wake=config["ceilings"]["workers_per_wake"],
            resume_tokens=config["ceilings"]["resume_tokens"],
            interval_hours=config["wake"]["interval_hours"],
            pause=pause_path(),
            state_dir=controller.default_state_dir(),
            secrets=secrets_path(),
            max_budget_usd=args.max_budget_usd,
            project=args.wake_project,
            browser_pool=config["browser"]["pool"],
            browser_slots=config["browser"]["slots"],
            jev=controller.Jev(config=config) if config["jev"]["enabled"] else None,
            plan_step=_plan_step(root, config),
            sweep=sweep.sweep,
        )
    except (
        board_commands.CommandError,
        BoardValidationError,
        GitCommitError,
        OSError,
    ) as e:
        _fail(str(e))
    except controller.Stopped as e:
        # SIGINT, SIGTERM or SIGHUP: the wake has ended its session and recorded its end.
        raise SystemExit(128 + e.signum) from None
    raise SystemExit(code)


def _refuse_wake(root: Path, message: str, *, model, effort) -> None:
    """`_fail` for a config value `autonomouse run` refuses before any session starts:
    the refusal also gets a run record of its own (`board_runs.run_refused`), since no
    run exists yet to carry it. A model or effort the config gives as something other than
    a string is recorded as null; the refusal's reason still quotes it."""
    print(f"error: {message}", file=sys.stderr)
    try:
        board_runs.run_refused(
            root,
            command="run",
            reason=message,
            model=model if isinstance(model, str) else None,
            effort=effort if isinstance(effort, str) else None,
        )
    except _RECORD_ERRORS as e:  # the refusal above stands either way
        print(
            f"error: the refusal was not recorded on a run record: {e}", file=sys.stderr
        )
    raise SystemExit(1)


def _read_result(source: str) -> dict:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        _fail(f"result {source} is not JSON — {e}")
    if not isinstance(result, dict):
        _fail(f"result {source} must be a JSON object")
    return result


def _print_run(run: board_runs.Run, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(run.to_dict(), indent=2))
        return
    print(
        f"run {run.id}: status={run.status.value} project={run.project} "
        f"pid={run.pid} session={run.session_id} ended_at={run.ended_at} "
        f"result={'recorded' if run.result is not None else 'none'} "
        f"version={run.version}"
    )
