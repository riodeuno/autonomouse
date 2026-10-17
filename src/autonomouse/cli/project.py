"""`autonomouse project`: create and read projects."""

import json
import sys
from pathlib import Path

from autonomouse.board import commands as board_commands
from autonomouse.board import runs as board_runs
from autonomouse.board import trackers
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError, Project
from autonomouse.board.store import GitCommitError
from autonomouse.cli.shared import _fail


def register(subparsers) -> None:
    project_parser = subparsers.add_parser("project", help="create and read projects")
    project_sub = project_parser.add_subparsers(dest="project_command")

    new_parser = project_sub.add_parser(
        "new",
        help="create a project from its GitHub URL: board, clone, timers, Telegram check",
        description=(
            "Creates a project from a GitHub repository URL, asking each question its flag "
            "does not answer, with gum when stdin is a terminal, else on stdin: the "
            "project's name, the repository URL, the name and email the agents commit with "
            "and the branch their pull requests go into (Enter keeps each default), and the "
            "Linear project or issue the project is linked to (blank for none); no question "
            "asks the goal. It creates the board when there is none, clones the repository "
            "under ~/projects/autonomouse/repos/<slug>, proves GitHub access and any Linear "
            "link (a small agent creates and removes a test issue there), writes the "
            "project, commits the board, installs the timers and checks that Telegram is "
            "set up. On a terminal it ends in a live Claude session (Fable 5.1, high "
            "effort) in the project's clone, which writes the project's goal and brief "
            "with you; with no terminal, the planner asks about them on Telegram. Run it "
            "again for each further project. On a terminal with "
            "no name given, it first offers every project whose setup did not finish (until "
            "its brief is written), plus start a new project, and continues the one you "
            "choose from the step it stopped at. With --repo-path the project is recorded "
            "from that existing checkout and nothing else runs."
        ),
    )
    new_parser.add_argument(
        "name", nargs="?", help="the project's name, as the board shows it; asked when absent"
    )
    new_parser.add_argument("--name", dest="name_flag", help="the same as the positional name")
    new_parser.add_argument("--slug", help="default: made from the name")
    new_parser.add_argument(
        "--goal",
        help="one paragraph, what the project is for; no question asks it, and planning "
        "writes it when this is absent",
    )
    new_parser.add_argument(
        "--repo", help="https://github.com/<owner>/<name> or git@github.com:<owner>/<name>.git"
    )
    new_parser.add_argument(
        "--repo-path", help="an existing checkout to record instead of cloning"
    )
    new_parser.add_argument(
        "--default-branch",
        help=(
            "the branch the agents' pull requests go into, which must exist on origin; "
            "skips that question, whose default is the clone's origin/HEAD (main on an "
            "empty repository); with --repo-path, default that checkout's origin/HEAD"
        ),
    )
    new_parser.add_argument(
        "--git-name",
        help="the name the agents commit with; skips that question, whose default is the "
        "GitHub account's name, else its login",
    )
    new_parser.add_argument(
        "--git-email",
        help="the email the agents commit with; skips that question, whose default is "
        "the GitHub account's no-reply address",
    )
    new_parser.add_argument(
        "--priority", type=int, help="default: one more than the highest on the board"
    )
    new_parser.add_argument(
        "--tracker",
        metavar="URL",
        help=(
            "a Linear project or issue URL to link the project to; skips that question, "
            "which a blank answer leaves unlinked; with --repo-path, the only way to link"
        ),
    )
    new_parser.add_argument("--json", action="store_true")

    show_parser = project_sub.add_parser("show", help="show a project")
    show_parser.add_argument("slug")
    show_parser.add_argument("--json", action="store_true")

    list_parser = project_sub.add_parser("list", help="list every project")
    list_parser.add_argument("--json", action="store_true")

    edit_parser = project_sub.add_parser(
        "edit",
        help=(
            "edit a project's name, goal, priority, test_temp_env, merge_method, "
            "charter_files, instructions and tracker"
        ),
    )
    edit_parser.add_argument("slug")
    edit_parser.add_argument(
        "--name", help="the display name the board screen shows; the slug stays"
    )
    edit_parser.add_argument("--goal")
    edit_parser.add_argument("--priority", type=int)
    edit_parser.add_argument(
        "--test-temp-env",
        action="append",
        metavar="NAME",
        help=(
            "an environment variable every test run points at a fresh temporary folder; "
            "repeats, one name each, and replaces the list"
        ),
    )
    edit_parser.add_argument(
        "--merge-method",
        help="squash, merge or rebase: how a green PR merges into the wake's interim branch",
    )
    edit_parser.add_argument(
        "--charter-files",
        action="append",
        metavar="GLOB",
        help=(
            "a path glob whose change makes a card at least size s; repeats, one glob "
            "each, and replaces the list"
        ),
    )
    edit_parser.add_argument(
        "--orchestrator-instruction",
        action="append",
        metavar="TEXT",
        help=(
            "your instruction to the project's orchestrator, which its every wake gets "
            "word for word; repeats, one instruction each, and replaces the list; '' "
            "clears it"
        ),
    )
    edit_parser.add_argument(
        "--worker-instruction",
        action="append",
        metavar="TEXT",
        help=(
            "your instruction to every worker on the project, which each worker's prompt "
            "gets word for word; repeats, one instruction each, and replaces the list; "
            "'' clears it"
        ),
    )
    edit_parser.add_argument(
        "--tracker",
        metavar="URL",
        help=(
            "a Linear project or issue URL to link the project to, replacing any link, "
            "once a small agent has proved through the Linear MCP that it can create and "
            "remove a test issue there; none clears the link"
        ),
    )
    edit_parser.add_argument("--json", action="store_true")

    done_parser = project_sub.add_parser(
        "done",
        help="mark a project done: no more wakes, sweeps or follow-ups on it",
        description=(
            "Marks the project done. A done project is never picked by a wake, swept "
            "for pull requests or followed up; the board shows DONE for it and lists "
            "it last. A wake already running on it goes on to its end. A wake also "
            "marks a project done by itself once every card is done or cancelled and "
            "every done card's work has reached the default branch (its pull request "
            "merged into it, or into a branch whose own pull request merged). A new "
            "todo card on a done project reopens it."
        ),
    )
    done_parser.add_argument("slug")
    done_parser.add_argument("--json", action="store_true")

    claim_parser = project_sub.add_parser(
        "claim",
        help="write a run on the project, taking over a stopped or crashed run's claim",
    )
    claim_parser.add_argument("slug")
    claim_parser.add_argument("--run-id", required=True)

    release_parser = project_sub.add_parser(
        "release", help="clear the project's claim held by this run"
    )
    release_parser.add_argument("slug")
    release_parser.add_argument("--run-id", required=True)

    project_parser.set_defaults(_parser=project_parser)


def run(args) -> None:
    root = board_root()
    try:
        if args.project_command == "new":
            _run_project_new(root, args)
        elif args.project_command == "show":
            project = board_commands.project_show(root, args.slug)
            _print_project(project, as_json=args.json)
        elif args.project_command == "list":
            projects = board_commands.project_list(root)
            if args.json:
                print(json.dumps([p.to_dict() for p in projects], indent=2))
            else:
                print(_project_list_plain(projects))
        elif args.project_command == "edit":
            _check_tracker_link(root, args.slug, args.tracker)
            project = board_commands.project_edit(
                root,
                args.slug,
                name=args.name,
                goal=args.goal,
                priority=args.priority,
                test_temp_env=args.test_temp_env,
                merge_method=args.merge_method,
                charter_files=args.charter_files,
                orchestrator_instructions=args.orchestrator_instruction,
                worker_instructions=args.worker_instruction,
                tracker=args.tracker,
            )
            _print_project(project, as_json=args.json)
        elif args.project_command == "done":
            project = board_commands.project_done(root, args.slug)
            _print_project(project, as_json=args.json)
        elif args.project_command == "claim":
            outcome = board_runs.project_claim(root, args.slug, run_id=args.run_id)
            if outcome.took_over:
                print(
                    f"claimed {outcome.project} for {outcome.run_id}; took over "
                    f"{outcome.took_over} ({outcome.previous.status.value}: "
                    f"{outcome.previous.reason}), now taken_over"
                )
            else:
                print(f"claimed {outcome.project} for {outcome.run_id}")
        elif args.project_command == "release":
            board_runs.project_release(root, args.slug, run_id=args.run_id)
            print(f"released {args.slug}")
        else:
            args._parser.error(
                "choose a project command: new, show, list, edit, done, claim, release"
            )
    except (board_commands.CommandError, BoardValidationError) as e:
        _fail(str(e))


def _say(line: str) -> None:
    """Print one line of a setup step at once, so a probe's step lines reach the owner
    while its agent works, even when stdout is a pipe."""
    print(line, flush=True)


def _check_tracker_link(root: Path, slug: str, tracker: str | None) -> None:
    """`project edit --tracker <url>`: prove the link (`project_wizard.check_tracker`)
    before it is saved; `none` and a URL no tracker reads need no proof, and the edit
    refuses the latter."""
    if tracker is None or tracker.strip().lower() == board_commands.NO_TRACKER:
        return
    try:
        trackers.project_link(tracker)
    except trackers.TrackerError:
        return  # project_edit refuses it, with the message
    from autonomouse import project_wizard

    project = board_commands.project_show(root, slug)
    try:
        project_wizard.check_tracker(tracker, checkout=Path(project.repo_path), say=_say)
    except project_wizard.WizardError as e:
        _fail(str(e))


def _run_project_new(root: Path, args) -> None:
    """`autonomouse project new`: the create-project wizard (`autonomouse.project_wizard`).
    On a terminal it asks each question with gum (`autonomouse.gum`), offers to continue
    a setup that did not finish and ends in the live planning session; with none, it asks
    on stdin, offers nothing and leaves planning to Telegram."""
    import getpass

    from autonomouse import gum, init, project_wizard

    if args.name is not None and args.name_flag is not None and args.name != args.name_flag:
        args._parser.error(f"two names given: {args.name!r} and --name {args.name_flag!r}")
    name = args.name if args.name is not None else args.name_flag
    terminal = sys.stdin.isatty()
    try:
        project = project_wizard.run_project_new(
            root,
            name=name,
            slug=args.slug,
            repo=args.repo,
            goal=args.goal,
            repo_path=args.repo_path,
            default_branch=args.default_branch,
            priority=args.priority,
            git_name=args.git_name,
            git_email=args.git_email,
            tracker=args.tracker,
            ask=gum.ask if terminal else init.ask_stdin,
            read_secret=gum.read_secret if terminal else getpass.getpass,
            say=_say,
            terminal=terminal,
        )
    except (project_wizard.WizardError, init.InitError, GitCommitError, OSError) as e:
        _fail(str(e))
    _print_project(project, as_json=args.json)


def _project_plain(project: Project) -> str:
    claim = (
        "free" if project.claim.run_id is None else f"claimed by {project.claim.run_id}"
    )
    return "\n".join(
        [
            f"slug: {project.slug}",
            f"name: {project.name}",
            f"goal: {project.goal}",
            f"repo: {project.repo}",
            f"repo_path: {project.repo_path}",
            f"default_branch: {project.default_branch}",
            f"priority: {project.priority}",
            f"tracker: {trackers.describe(project.tracker)}",
            f"test_temp_env: {', '.join(project.test_temp_env) or '(none)'}",
            f"merge_method: {project.merge_method.value}",
            f"charter_files: {', '.join(project.charter_files) or '(none)'}",
            *_instructions_plain("orchestrator", project.orchestrator_instructions),
            *_instructions_plain("worker", project.worker_instructions),
            f"claim: {claim}",
            f"created_at: {project.created_at}",
            f"updated_at: {project.updated_at}",
            *([f"done_at: {project.done_at}"] if project.done_at else []),
        ]
    )


def _instructions_plain(whom: str, instructions: list[str]) -> list[str]:
    """`<whom>_instructions: (none)`, or that heading and one `- <instruction>` line
    each."""
    if not instructions:
        return [f"{whom}_instructions: (none)"]
    return [f"{whom}_instructions:", *(f"- {i}" for i in instructions)]


def _project_list_plain(projects: list[Project]) -> str:
    if not projects:
        return "no projects"
    widths = (max(len(p.slug) for p in projects), max(len(p.name) for p in projects))
    lines = []
    for project in projects:
        claim = "free" if project.claim.run_id is None else project.claim.run_id
        lines.append(
            f"{project.slug:<{widths[0]}}  {project.name:<{widths[1]}}  "
            f"priority={project.priority}  claim={claim}"
            + ("  done" if project.done_at else "")
        )
    return "\n".join(lines)


def _print_project(project: Project, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(project.to_dict(), indent=2))
    else:
        print(_project_plain(project))
