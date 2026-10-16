"""`autonomouse card`: create and read cards."""

import json

from autonomouse import controller, followup, github, overview
from autonomouse.board import commands as board_commands
from autonomouse.board import runs as board_runs
from autonomouse.board import stuck as board_stuck
from autonomouse.board.paths import board_root
from autonomouse.board.schema import BoardValidationError, Card, Stack
from autonomouse.cli.shared import _env_default, _fail
from autonomouse.config import secrets_path


def register(subparsers) -> None:
    card_parser = subparsers.add_parser("card", help="create and read cards")
    card_sub = card_parser.add_subparsers(dest="card_command")

    new_parser = card_sub.add_parser(
        "new", help="create a card in todo, or with --backlog in backlog"
    )
    new_parser.add_argument("--id", dest="card_id", required=True)
    new_parser.add_argument("--project", required=True)
    new_parser.add_argument("--title", required=True)
    new_parser.add_argument("--goal", required=True)
    new_parser.add_argument("--test", required=True)
    new_parser.add_argument("--size", default="s")
    new_parser.add_argument("--position", type=int, default=0)
    new_parser.add_argument("--limit", dest="limits", action="append", default=[])
    new_parser.add_argument(
        "--depends-on", dest="depends_on", action="append", default=[]
    )
    new_parser.add_argument(
        "--files",
        action="append",
        default=[],
        help="a file the card's PR is expected to change; repeat once per file",
    )
    new_parser.add_argument(
        "--access",
        action="append",
        default=[],
        metavar="NAME=CHECK",
        help="a login or tool the card's test needs, and a shell command that exits 0 "
        "when it is present, e.g. 'gh=gh auth status'; repeat once per entry",
    )
    new_parser.add_argument(
        "--backlog",
        action="store_true",
        help="put the card in backlog, set aside for the human to decide later; "
        "without it the card goes to todo, ready for the next wake",
    )
    new_parser.add_argument(
        "--note",
        help="why the card was made; its first history row reads "
        "'created via card new; why: <note>' (default: created via card new)",
    )
    new_parser.add_argument("--run-id", default="manual")
    new_parser.add_argument("--json", action="store_true")

    show_parser = card_sub.add_parser("show", help="show a card")
    show_parser.add_argument("id")
    show_parser.add_argument("--project")
    show_parser.add_argument("--json", action="store_true")

    list_parser = card_sub.add_parser("list", help="list cards")
    list_parser.add_argument("--project")
    list_parser.add_argument("--stack")
    list_parser.add_argument("--json", action="store_true")

    overlaps_parser = card_sub.add_parser(
        "overlaps",
        help="list the project's cards not yet done or cancelled that would change one of "
        "these files, by their files list or, when it is empty, by the files their goal "
        "names (docs/planner.md, \"Cards that share a file\")",
    )
    overlaps_parser.add_argument("--project", required=True)
    overlaps_parser.add_argument(
        "--files",
        action="append",
        required=True,
        help="a file the planned card would change; repeat once per file",
    )
    overlaps_parser.add_argument("--json", action="store_true")

    move_parser = card_sub.add_parser(
        "move",
        help="move a card: backlog -> todo, todo -> backlog (the human set it aside; "
        "needs --note), in_progress -> todo, or done -> todo "
        "(a reopened card; needs --note and --next-step)",
    )
    move_parser.add_argument("id")
    move_parser.add_argument("--to", required=True)
    move_parser.add_argument("--project")
    move_parser.add_argument("--note")
    move_parser.add_argument(
        "--next-step", help="done -> todo only: the reopened card's handoff.next_step"
    )
    move_parser.add_argument("--run-id", default="manual")
    move_parser.add_argument("--json", action="store_true")

    cancel_parser = card_sub.add_parser("cancel", help="cancel a card from any stack")
    cancel_parser.add_argument("id")
    cancel_parser.add_argument("--reason", required=True)
    cancel_parser.add_argument("--project")
    cancel_parser.add_argument("--run-id", default="manual")
    cancel_parser.add_argument("--json", action="store_true")

    start_parser = card_sub.add_parser(
        "start",
        help="todo -> in_progress, or resume an in_progress card whose claim died",
    )
    start_parser.add_argument("id")
    start_parser.add_argument("--branch", required=True)
    start_parser.add_argument("--base-branch", required=True)
    start_parser.add_argument(
        "--session-id", help="default: $CLAUDE_CODE_SESSION_ID of this shell"
    )
    start_parser.add_argument(
        "--pid", type=int, help="default: $CLAUDE_PID of this shell"
    )
    start_parser.add_argument("--model", required=True)
    start_parser.add_argument("--effort", required=True)
    start_parser.add_argument("--agent-type", required=True)
    start_parser.add_argument("--run-id", required=True)
    start_parser.add_argument("--project")
    start_parser.add_argument("--json", action="store_true")

    ask_parser = card_sub.add_parser(
        "ask", help="ask a decision only the human can make; moves to decision_needed"
    )
    ask_parser.add_argument("id")
    ask_parser.add_argument(
        "--brief",
        required=True,
        help="plain words for someone who has not read the code, in at most three "
        "labelled parts with a blank line between them: 'What happened:', 'Why it needs "
        "you:' (the card's goal sentence, word for word) and 'What each option costs:'; "
        "no board id, and no options or recommendation, which --option and "
        "--recommendation carry "
        "(docs/rules/08-questions-for-the-human.md, 'How a message to the human is laid "
        "out')",
    )
    ask_parser.add_argument("--question", required=True)
    ask_parser.add_argument(
        "--option", dest="options", action="append", default=[], required=True
    )
    ask_parser.add_argument("--recommendation", required=True)
    ask_parser.add_argument("--project")
    ask_parser.add_argument("--run-id", default="manual")
    ask_parser.add_argument("--json", action="store_true")

    answer_parser = card_sub.add_parser(
        "answer", help="answer the open decision; returns the card to todo (or done)"
    )
    answer_parser.add_argument("id")
    answer_parser.add_argument("--answer", required=True)
    answer_parser.add_argument(
        "--done",
        action="store_true",
        help="the work was already finished; go straight to done",
    )
    answer_parser.add_argument("--project")
    answer_parser.add_argument("--run-id", default="manual")
    answer_parser.add_argument("--json", action="store_true")

    progress_parser = card_sub.add_parser(
        "progress",
        help="record one step: phase, iteration, last/next step, pr, attachments; "
        "stays in_progress",
    )
    progress_parser.add_argument("id")
    progress_parser.add_argument("--phase")
    progress_parser.add_argument("--iteration", type=int)
    progress_parser.add_argument("--last-step")
    progress_parser.add_argument("--next-step")
    progress_parser.add_argument("--pr")
    progress_parser.add_argument(
        "--attach",
        action="append",
        help="a file path or URL to attach to the card; repeatable",
    )
    progress_parser.add_argument("--project")
    progress_parser.add_argument("--run-id", default="manual")
    progress_parser.add_argument("--json", action="store_true")

    finish_parser = card_sub.add_parser(
        "finish", help="in_progress -> done; needs a verification note and a PR link"
    )
    finish_parser.add_argument("id")
    finish_parser.add_argument(
        "--last-step", required=True, help="the verification note, or a no-code card's findings"
    )
    finish_parser.add_argument("--pr")
    finish_parser.add_argument(
        "--no-code",
        action="store_true",
        help="the card's goal produces no code; finish without --pr",
    )
    finish_parser.add_argument("--project")
    finish_parser.add_argument("--run-id", default="manual")
    finish_parser.add_argument("--json", action="store_true")

    edit_parser = card_sub.add_parser(
        "edit",
        help="edit title/goal/limits/test/size/position/depends_on/files/access/"
        "tracker_issue",
    )
    edit_parser.add_argument("id")
    edit_parser.add_argument("--title")
    edit_parser.add_argument("--goal")
    edit_parser.add_argument("--limit", dest="limits", action="append", default=None)
    edit_parser.add_argument("--test")
    edit_parser.add_argument("--size")
    edit_parser.add_argument("--position", type=int)
    edit_parser.add_argument(
        "--depends-on", dest="depends_on", action="append", default=None
    )
    edit_parser.add_argument(
        "--clear-depends-on",
        action="store_true",
        help="set depends_on to [] in one step, for a dependency that is "
        "cancelled or will otherwise never turn done; refused together with "
        "--depends-on",
    )
    edit_parser.add_argument(
        "--files",
        action="append",
        default=None,
        help="a file the card's PR is expected to change; repeat once per file; "
        "replaces the list",
    )
    edit_parser.add_argument(
        "--access",
        action="append",
        default=None,
        metavar="NAME=CHECK",
        help="a login or tool the card's test needs, and a shell command that exits 0 "
        "when it is present, e.g. 'gh=gh auth status'; repeat once per entry; "
        "replaces the list",
    )
    edit_parser.add_argument(
        "--tracker-issue",
        metavar="URL",
        help="the URL of the card's issue in its project's tracker, e.g. "
        "https://linear.app/<workspace>/issue/ABC-123; the agent that files the issue "
        "records it here; replaces the one recorded",
    )
    edit_parser.add_argument(
        "--note",
        help="why the edit was made; ends the history row as 'why: <note>', and is "
        "required with --goal, --limit, --test or --size. "
        "Given with no other field, appends a history row of its own text, "
        "changing nothing else",
    )
    edit_parser.add_argument("--project")
    edit_parser.add_argument("--run-id", default="manual")
    edit_parser.add_argument("--json", action="store_true")

    delete_parser = card_sub.add_parser(
        "delete", help="delete a card's file (history kept in git)"
    )
    delete_parser.add_argument("id")
    delete_parser.add_argument("--project")

    stuck_parser = card_sub.add_parser(
        "stuck",
        help="print the first stuck signal that fires on a running card's worker "
        "(docs/orchestrator/while-workers-run.md), or nothing",
    )
    stuck_parser.add_argument("id")
    stuck_parser.add_argument(
        "--agent-id",
        help="the worker's agentId, from the Agent tool's result; counts the sub-agents "
        "it started since its last note",
    )
    stuck_parser.add_argument("--project")

    follow_up_parser = card_sub.add_parser(
        "follow-up",
        help="run the PR follow-up a normal wake runs before it resumes a card, on one "
        "in_progress card with a PR: retarget the PR when its base merged, hand the card "
        "back when a check failed or a comment is unanswered; prints one line",
    )
    follow_up_parser.add_argument("id")
    follow_up_parser.add_argument("--project")
    follow_up_parser.add_argument("--run-id", required=True)

    card_parser.set_defaults(_parser=card_parser)


def run(args) -> None:
    root = board_root()
    try:
        if args.card_command == "new":
            card = board_commands.card_new(
                root,
                card_id=args.card_id,
                project=args.project,
                title=args.title,
                goal=args.goal,
                test=args.test,
                size=args.size,
                position=args.position,
                limits=args.limits,
                depends_on=args.depends_on,
                files=args.files,
                access=args.access,
                run_id=args.run_id,
                stack="backlog" if args.backlog else "todo",
                note=args.note,
            )
            _print_card(card, as_json=args.json)
        elif args.card_command == "show":
            card = board_commands.card_show(root, args.id, project=args.project)
            _print_card(card, as_json=args.json)
        elif args.card_command == "list":
            cards = board_commands.card_list(
                root, project=args.project, stack=args.stack
            )
            if args.json:
                print(json.dumps([c.to_dict() for c in cards], indent=2))
            else:
                working = {
                    slug
                    for slug in {c.project for c in cards if c.stack == Stack.IN_PROGRESS}
                    if overview.project_working(
                        root, board_commands.project_show(root, slug)
                    )
                }
                print(_card_list_plain(cards, working=working))
        elif args.card_command == "overlaps":
            found = board_commands.card_overlaps(
                root, project=args.project, files=args.files
            )
            if args.json:
                rows = [
                    {"id": c.id, "stack": c.stack.value, "title": c.title,
                     "shared": shared, "from": source}
                    for c, shared, source in found
                ]  # fmt: skip
                print(json.dumps(rows, indent=2))
            else:
                print(_overlaps_plain(found))
        elif args.card_command == "move":
            card = board_commands.card_move(
                root,
                args.id,
                args.to,
                project=args.project,
                note=args.note,
                next_step=args.next_step,
                run_id=args.run_id,
            )
            _print_card(card, as_json=args.json)
        elif args.card_command == "cancel":
            # `card_cancel` sends a Telegram follow-up when the cancelled card's
            # last decision is unanswered (`_follow_up`); load the secrets file the
            # same way `card ask` and `card answer` do above (sys-s35, sys-s42).
            with controller._secrets_in_environ(secrets_path()):
                card = board_commands.card_cancel(
                    root,
                    args.id,
                    reason=args.reason,
                    project=args.project,
                    run_id=args.run_id,
                )
            _print_card(card, as_json=args.json)
        elif args.card_command == "start":
            session_id = _env_default(
                args.session_id, "CLAUDE_CODE_SESSION_ID", "--session-id"
            )
            pid = int(_env_default(args.pid, "CLAUDE_PID", "--pid"))
            card = board_runs.card_start(
                root,
                args.id,
                branch=args.branch,
                base_branch=args.base_branch,
                session_id=session_id,
                pid=pid,
                model=args.model,
                effort=args.effort,
                agent_type=args.agent_type,
                run_id=args.run_id,
                project=args.project,
            )
            _print_card(card, as_json=args.json)
        elif args.card_command == "ask":
            # `card_ask` sends the decision's brief to Telegram (`autonomouse.notify`),
            # which reads TELEGRAM_BOT_TOKEN from this process's environment; load the
            # secrets file the same way `_run_notify_command` does, so `card ask` run
            # from a plain shell reaches Telegram too (sys-s35).
            with controller._secrets_in_environ(secrets_path()):
                card = board_commands.card_ask(
                    root,
                    args.id,
                    question=args.question,
                    options=args.options,
                    brief=args.brief,
                    recommendation=args.recommendation,
                    project=args.project,
                    run_id=args.run_id,
                )
            _print_card(card, as_json=args.json)
        elif args.card_command == "answer":
            # Same as `card ask` above: `card_answer` sends a Telegram follow-up
            # (sys-s35).
            with controller._secrets_in_environ(secrets_path()):
                card = board_commands.card_answer(
                    root,
                    args.id,
                    answer=args.answer,
                    done=args.done,
                    project=args.project,
                    run_id=args.run_id,
                )
            _print_card(card, as_json=args.json)
        elif args.card_command == "progress":
            card = board_commands.card_progress(
                root,
                args.id,
                phase=args.phase,
                iteration=args.iteration,
                last_step=args.last_step,
                next_step=args.next_step,
                pr=args.pr,
                attach=args.attach,
                project=args.project,
                run_id=args.run_id,
            )
            _print_card(card, as_json=args.json)
        elif args.card_command == "finish":
            card = board_commands.card_finish(
                root,
                args.id,
                last_step=args.last_step,
                pr=args.pr,
                no_code=args.no_code,
                project=args.project,
                run_id=args.run_id,
            )
            _print_card(card, as_json=args.json)
        elif args.card_command == "edit":
            card = board_commands.card_edit(
                root,
                args.id,
                title=args.title,
                goal=args.goal,
                limits=args.limits,
                test=args.test,
                size=args.size,
                position=args.position,
                depends_on=args.depends_on,
                clear_depends_on=args.clear_depends_on,
                files=args.files,
                access=args.access,
                tracker_issue=args.tracker_issue,
                note=args.note,
                project=args.project,
                run_id=args.run_id,
            )
            _print_card(card, as_json=args.json)
        elif args.card_command == "delete":
            path = board_commands.card_delete(root, args.id, project=args.project)
            print(f"deleted {args.id} ({path})")
        elif args.card_command == "stuck":
            card = board_commands.card_show(root, args.id, project=args.project)
            subagents = None
            if args.agent_id:
                subagents = board_stuck.subagents_since(
                    card, args.agent_id, board_runs.claude_projects_dir()
                )
            project = board_commands.project_show(root, card.project)
            signal = board_stuck.stuck(
                card, default_branch=project.default_branch, subagents=subagents
            )
            if signal is not None:
                print(signal)
        elif args.card_command == "follow-up":
            card = board_commands.card_show(root, args.id, project=args.project)
            if card.stack != Stack.IN_PROGRESS:
                raise board_commands.CommandError(
                    f"cannot follow up from {card.stack.value!r} — a card must be "
                    "in_progress to follow up"
                )
            if not card.pr:
                raise board_commands.CommandError(f"{card.id} has no PR to follow up")
            project = board_commands.project_show(root, card.project)
            result = followup.follow_up(root, card, project, run_id=args.run_id)
            print(f"follow_up={card.id}: {result.summary}")
        else:
            args._parser.error(
                "choose a card command: new, show, list, overlaps, move, cancel, "
                "start, ask, answer, progress, finish, edit, delete, stuck, follow-up"
            )
    except (board_commands.CommandError, BoardValidationError, github.GitHubError) as e:
        _fail(str(e))


def _card_plain(card: Card) -> str:
    return "\n".join(
        [
            f"id: {card.id}",
            f"project: {card.project}",
            f"title: {card.title}",
            f"stack: {card.stack.value}",
            f"position: {card.position}",
            f"size: {card.size.value}",
            f"goal: {card.goal}",
            f"limits: {', '.join(card.limits)}",
            f"test: {card.test}",
            f"depends_on: {', '.join(card.depends_on) if card.depends_on else '(none)'}",
            f"files: {', '.join(card.files) if card.files else '(none)'}",
            *(
                [f"access: {a.name}: {a.check}" for a in card.access]
                or ["access: (none)"]
            ),
            f"phase: {card.phase.value if card.phase else '(none)'}",
            f"iteration: {card.iteration}",
            f"pr: {card.pr or '(none)'}",
            f"tracker_issue: {_tracker_issue(card)}",
            f"attachments: {', '.join(card.attachments) if card.attachments else '(none)'}",
            (
                f"decisions: {len(card.decisions)} "
                f"({sum(1 for d in card.decisions if d.answer is None)} open)"
            ),
            f"created_at: {card.created_at}",
            f"updated_at: {card.updated_at}",
        ]
    )


def _card_list_plain(cards: list[Card], *, working: set[str]) -> str:
    """One line per card: id, stack, position, title, and for an in_progress card
    its phase as the board shows it (`overview.phase_word`): `stopped` unless its
    project is in `working`, the projects a wake works now."""
    if not cards:
        return "no cards"
    widths = (
        max(len(c.id) for c in cards),
        max(len(c.stack.value) for c in cards),
        max(len(c.title) for c in cards),
    )
    lines = []
    for card in cards:
        line = (
            f"{card.id:<{widths[0]}}  {card.stack.value:<{widths[1]}}  "
            f"pos={card.position:<3}  {card.title:<{widths[2]}}"
        )
        phase = overview.phase_word(card, working=card.project in working)
        lines.append(f"{line}  {phase}" if phase is not None else line)
    return "\n".join(lines)


def _overlaps_plain(found: list[tuple[Card, list[str], str]]) -> str:
    if not found:
        return "no open card changes these files"
    lines = []
    for card, shared, source in found:
        by = "" if source == "files" else " (read from its goal; it has no files list)"
        lines.append(f"{card.id}  {card.stack.value}  {', '.join(shared)}{by}  {card.title}")
    return "\n".join(lines)


def _print_card(card: Card, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(card.to_dict(), indent=2))
    else:
        print(_card_plain(card))


def _tracker_issue(card: Card) -> str:
    issue = card.tracker_issue
    return f"{issue['identifier']} {issue['url']}" if issue is not None else "(none)"
