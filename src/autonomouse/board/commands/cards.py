"""The `card` commands: create, read, move, edit and delete cards, and ask, answer,
progress, retarget and finish them.

Per `docs/board-schema.md`, a card's `stack`, `assigned`, `worktree`, `branch`,
`base_branch`, `pr`, `phase`, `decisions` and (after creation) `history` are set
by commands (`move`, `cancel`, `ask`, `answer`, `progress`, `finish` here;
`start` lives in `board.runs` instead, next to `project_claim`, for the
circular-import reason its own docstring gives); `card_new` here only ever
produces a fresh card sitting in `todo` or `backlog`, untouched by any of those.

Card ids look like `<prefix>-<letter><n>` (e.g. `sys-b3`); the letter groups
related cards, which is a planning judgment no formula can reproduce, so `card_new`
takes the id as given rather than inventing one — see the id validation below.
"""

from __future__ import annotations

from pathlib import Path

from autonomouse import human_messages
from autonomouse.board import trackers
from autonomouse.board.commands.common import (
    _CARD_ID_RE,
    _ORCHESTRATOR_RUN_RE,
    RULES_LIMIT,
    SCREEN_RUN_ID,
    TELEGRAM_RUN_ID,
    CommandError,
    _board_card_ids,
    _card_path,
    _cards_dir,
    _check_depends_on,
    _check_files,
    _find_card_path,
    _now,
    _require_project,
)
from autonomouse.board.commands.projects import project_reopen
from autonomouse.board.history import append_history, now_iso, progress_note
from autonomouse.board.overlaps import overlaps
from autonomouse.board.schema import (
    OTHER_OPTION,
    Access,
    Assigned,
    Card,
    Decision,
    Handoff,
    HistoryEntry,
    Phase,
    Size,
    Stack,
)
from autonomouse.board.store import load_card, save_card
from autonomouse.notify import notify


def _parse_access(values: list[str], *, card_id: str) -> list[Access]:
    """Read each `--access` value, `<name>=<check>`, into an `Access` entry, in order.

    The name is the text before the first `=`, so a check may itself hold `=`. A value
    with no `=`, an empty name or an empty check is refused."""
    entries = []
    for value in values:
        name, sep, check = value.partition("=")
        if not sep or not name.strip() or not check.strip():
            raise CommandError(
                f"{card_id}: access value {value!r} is not <name>=<check> — --access takes "
                "a tool or service name, '=', and a shell command that exits 0 when it is "
                "present, and repeats: --access 'gh=gh auth status' "
                "--access 'stripe=stripe whoami'"
            )
        entries.append(Access(name=name.strip(), check=check.strip()))
    return entries


def card_new(
    root: Path,
    *,
    card_id: str,
    project: str,
    title: str,
    goal: str,
    test: str,
    size: str = "s",
    position: int = 0,
    limits: list[str] | None = None,
    depends_on: list[str] | None = None,
    files: list[str] | None = None,
    access: list[str] | None = None,
    run_id: str = "manual",
    stack: str = "backlog",
    note: str | None = None,
) -> Card:
    """Create a new card in `stack`: `todo`, ready for a wake, or `backlog`, set aside
    for the human to decide later. The default stays `backlog` for Python callers such as
    `review improve`, whose proposals the human judges first; `autonomouse card new`
    passes `todo` unless given `--backlog`.

    `limits` always carries `docs/rules.md applies`, even if the caller does not
    pass it. `files` are the files the card's PR is expected to change. `access`
    values are `<name>=<check>`, the logins and tools the card's test needs. `note` is
    why the card was made: the creation history row reads `created via card new; why:
    <note>`, and `created via card new` when it is not given (a split's child names its
    parent and the cause there). Refused if `card_id` is not
    `<prefix>-<letter><n>`, the project does not exist, the size is not `xs`/`s`/`m`,
    `stack` is not `backlog` or `todo`, `note` is blank, the id is already taken, a
    `depends_on` value holds a comma or names no card on the board, a `files` value is
    blank or holds a comma, or an `access` value is not `<name>=<check>` with both parts
    non-empty. A card in `todo` reopens a done project (`project_reopen`).
    """
    if not _CARD_ID_RE.fullmatch(card_id):
        raise CommandError(
            f"card id must look like <prefix>-<letter><n>, e.g. sys-b3 — got {card_id!r}"
        )
    _require_project(root, project)
    try:
        size_enum = Size(size)
    except ValueError:
        raise CommandError(
            f"size must be one of {', '.join(s.value for s in Size)} — got {size!r}"
        )
    if stack not in (Stack.BACKLOG.value, Stack.TODO.value):
        raise CommandError(f"a new card goes to backlog or todo — got {stack!r}")
    if note is not None and not note.strip():
        raise CommandError("--note is blank — say why the card was made, or leave --note out")
    path = _card_path(root, project, card_id)
    if path.exists():
        raise CommandError(f"card {card_id!r} already exists at {path}")
    _check_depends_on(list(depends_on or []), _board_card_ids(root), card_id=card_id)
    _check_files(list(files or []), card_id=card_id)
    access_entries = _parse_access(list(access or []), card_id=card_id)

    merged_limits = [RULES_LIMIT]
    for limit in limits or []:
        if limit not in merged_limits:
            merged_limits.append(limit)

    ts = _now()
    card = Card(
        id=card_id,
        project=project,
        title=title,
        stack=Stack(stack),
        position=position,
        size=size_enum,
        goal=goal,
        limits=merged_limits,
        test=test,
        depends_on=list(depends_on or []),
        files=list(dict.fromkeys(files or [])),
        access=access_entries,
        assigned=Assigned(
            model=None,
            effort=None,
            agent_type=None,
            session_id=None,
            pid=None,
            picked_up_at=None,
        ),
        worktree=None,
        branch=None,
        base_branch=None,
        pr=None,
        phase=None,
        iteration=0,
        handoff=Handoff(last_step=None, next_step=None, updated_at=None, run_id=run_id),
        decisions=[],
        history=[
            HistoryEntry(
                at=ts,
                run_id=run_id,
                from_=None,
                to=stack,
                note=(
                    f"created via card new; why: {note}"
                    if note is not None
                    else "created via card new"
                ),
            )
        ],
        created_at=ts,
        updated_at=ts,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    save_card(path, card)
    if card.stack == Stack.TODO:
        project_reopen(root, project)
    return card


def card_show(root: Path, card_id: str, *, project: str | None = None) -> Card:
    """Load one card by id, searching every project unless `project` narrows it."""
    return load_card(_find_card_path(root, card_id, project))


def card_list(
    root: Path,
    *,
    project: str | None = None,
    stack: str | None = None,
) -> list[Card]:
    """Cards on the board, optionally filtered to one `project` and/or one `stack`.

    Ordered by project, then position, then id.
    """
    stack_enum = None
    if stack is not None:
        try:
            stack_enum = Stack(stack)
        except ValueError:
            raise CommandError(
                f"stack must be one of {', '.join(s.value for s in Stack)} — got {stack!r}"
            )

    projects_dir = root / "projects"
    if project is not None:
        _require_project(root, project)
        slugs = [project]
    elif projects_dir.is_dir():
        slugs = sorted(e.name for e in projects_dir.iterdir() if e.is_dir())
    else:
        slugs = []

    cards = []
    for slug in slugs:
        cards_dir = _cards_dir(root, slug)
        if not cards_dir.is_dir():
            continue
        for path in sorted(cards_dir.glob("*.json")):
            card = load_card(path)
            if stack_enum is not None and card.stack != stack_enum:
                continue
            cards.append(card)

    cards.sort(key=lambda c: (c.project, c.position, c.id))
    return cards


def card_overlaps(
    root: Path, *, project: str, files: list[str]
) -> list[tuple[Card, list[str], str]]:
    """The cards of `project` not yet `done` or `cancelled` that would change one of
    `files` (`board.overlaps.overlaps`): each card, the entries of `files` it shares,
    and `"files"` or `"goal"` for where the card's own names came from. Refused if the
    project does not exist or a `files` value is blank or holds a comma."""
    _check_files(files, card_id="card overlaps")
    closed = {Stack.DONE, Stack.CANCELLED}
    open_cards = [c for c in card_list(root, project=project) if c.stack not in closed]
    return overlaps(files, open_cards)


# `move` only ever makes these four transitions; every other stack change has its
# own command (`start`, `ask`, `answer`, `finish`), per the transition table in
# `docs/board-schema.md`.
_MOVE_TRANSITIONS = {
    (Stack.BACKLOG, Stack.TODO),
    (Stack.TODO, Stack.BACKLOG),
    (Stack.IN_PROGRESS, Stack.TODO),
    (Stack.DONE, Stack.TODO),
}


def card_move(
    root: Path,
    card_id: str,
    to: str,
    *,
    project: str | None = None,
    note: str | None = None,
    next_step: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Move a card along an allowed `move` transition: `backlog` -> `todo`,
    `todo` -> `backlog` (the human set the card aside), `in_progress` -> `todo`
    (a reclaimed or replanned card), or `done` -> `todo` (a done card reopened
    because its open PR conflicts with its base, `docs/orchestrator/conflict-check.md`).

    Reopening a done card puts it at position 0, sets `phase` to `writing` and
    `handoff.next_step` to `next_step`, so its next worker reads that it is
    bringing the same PR up to date (`docs/worker.md`, "Before the loop") rather
    than starting the card over. `iteration` stays: the conflict is not a failed
    attempt.

    Refused if `to` names a stack that does not exist, if the card's current stack
    has no `move` transition to it — `todo` -> `in_progress` needs `card start`, not
    `move` — if `note` is missing on a move out of `in_progress` or `done` or on
    `todo` -> `backlog` (`docs/board-schema.md` requires a note there), or if
    `next_step` is missing on a move out of `done` or given on any other move.
    A card moved into `todo` reopens a done project (`project_reopen`).
    """
    try:
        to_stack = Stack(to)
    except ValueError:
        raise CommandError(
            f"no such stack {to!r} — must be one of {', '.join(s.value for s in Stack)}"
        )

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    from_stack = card.stack

    if from_stack == Stack.TODO and to_stack == Stack.IN_PROGRESS:
        raise CommandError("todo -> in_progress needs `card start`, not `move`")
    if (from_stack, to_stack) not in _MOVE_TRANSITIONS:
        raise CommandError(
            f"cannot move a card from {from_stack.value!r} to {to_stack.value!r} — "
            "`move` only does backlog -> todo, todo -> backlog, in_progress -> todo "
            "and done -> todo"
        )
    if to_stack == Stack.BACKLOG and not (note and note.strip()):
        raise CommandError(
            "moving a card from todo to backlog needs --note "
            "(say the human set it aside, and their words)"
        )
    if from_stack == Stack.IN_PROGRESS and not (note and note.strip()):
        raise CommandError(
            "moving a card from in_progress back to todo needs --note "
            "(say it was reclaimed or replanned)"
        )
    reopen = from_stack == Stack.DONE
    if reopen and not (note and note.strip()):
        raise CommandError(
            "moving a done card back to todo needs --note "
            "(say why it was reopened, e.g. which merge made its PR conflict)"
        )
    if reopen and not (next_step and next_step.strip()):
        raise CommandError(
            "moving a done card back to todo needs --next-step "
            "(what its next worker does, e.g. merge the base into its branch)"
        )
    if not reopen and next_step is not None:
        raise CommandError("--next-step is only for moving a done card back to todo")

    ts = now_iso()
    card.stack = to_stack
    if reopen:
        card.position = 0
        card.phase = Phase.WRITING
        card.handoff.next_step = next_step
        card.handoff.run_id = run_id
        card.handoff.updated_at = ts
    append_history(
        card, run_id=run_id, to=to_stack.value, note=note, from_=from_stack.value, at=ts
    )
    save_card(path, card)
    if to_stack == Stack.TODO:
        project_reopen(root, card.project)
    return card


def card_cancel(
    root: Path,
    card_id: str,
    *,
    reason: str,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Cancel a card from any stack, recording `reason` as the history note.

    A card with an open decision — the human was sent it on Telegram — gets a
    follow-up there saying the decision is no longer needed and who cancelled the
    card (`_follow_up`).

    Refused if `reason` is empty.
    """
    if not (reason and reason.strip()):
        raise CommandError("cancel needs --reason")

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    from_stack = card.stack
    open_decision = _open_decision(card)

    card.stack = Stack.CANCELLED
    append_history(
        card,
        run_id=run_id,
        to=Stack.CANCELLED.value,
        note=reason,
        from_=from_stack.value,
    )
    save_card(path, card)
    if open_decision is not None:
        _follow_up(
            root,
            card.id,
            open_decision,
            run_id,
            human_messages.cancelled(open_decision, _answerer(root, run_id)),
        )
    return card


def card_edit(
    root: Path,
    card_id: str,
    *,
    title: str | None = None,
    goal: str | None = None,
    limits: list[str] | None = None,
    test: str | None = None,
    size: str | None = None,
    position: int | None = None,
    depends_on: list[str] | None = None,
    clear_depends_on: bool = False,
    files: list[str] | None = None,
    access: list[str] | None = None,
    tracker_issue: str | None = None,
    note: str | None = None,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Edit `title`, `goal`, `limits`, `test`, `size`, `position`, `depends_on`,
    `files`, `access` and/or `tracker_issue` — the fields `docs/board-schema/card.md`
    lists as settable after creation — or edit none of them and give `note` alone.
    `tracker_issue` is the URL of the card's issue in its project's tracker, which the
    agent that filed the issue records (`board.trackers`). A field
    left out keeps its current value; `files` and `access`, like `depends_on`,
    replace the whole list.

    `clear_depends_on` sets `depends_on` to `[]` in one step, for a dependency
    that will never turn `done` — cancelled, or split into children this card
    was not updated to depend on (`situation.ready()` counts only `done` cards,
    so a cancelled dependency blocks the card forever otherwise). Refused
    together with `depends_on`: pick one. The history row names the flag
    (`depends_on cleared via --clear-depends-on`), so a hand edit is never the
    only record of why the list emptied.

    `note` is why the edit was made. The history row ends with `why: <note>`, so
    the reason for a loosened test or limit sits in the card's history next to the
    change itself (rules, item 10), also when no `card move` goes with the edit. An
    edit of `goal`, `limits`, `test` or `size` is a judgment on the card and needs a
    `note`; one of `title`, `position`, `depends_on`, `files`, `access` or
    `tracker_issue` does not.

    Given alone, with no other field, `note` is instead the whole of the
    history row: `from` and `to` are the card's current stack, the row's note
    is `note` as written, no field changes, and the attempt state —
    `iteration`, `phase`, `handoff.next_step` — is left as it is. This is how a
    session records why a card stays where it is without moving it or
    replanning it (`docs/orchestrator.md`, "a `card edit --note` naming what is
    missing").

    `limits` replaces the card's whole list but is normalized the same way `card_new`
    normalizes it, so `docs/rules.md applies` is never edited away. Does not move the
    card's stack; the history row records the edit with `from`/`to` both the card's
    current stack, same as the schema's "from/to are stacks" for every other row.

    Changing `goal`, `limits` or `test` rewrites the card's plan, so it also resets
    the attempt state of the old plan: `iteration` back to 0, `phase` and
    `handoff.next_step` to null (`handoff.last_step` is kept). Without this, a card
    replanned in place at the attempt limit — a loosened limit included (rules, item
    10) — would keep `iteration` 5 and its next worker would stop at once. A new
    `title`, `size`, `position`, `depends_on`, `files`, `access` or `tracker_issue`
    leaves the plan as it is, and the attempt state with it.

    Refused if no field and no `note` are given, `note` is blank, the card
    does not exist, `size` is not `xs`/`s`/`m`, `goal`, `limits`, `test` or `size`
    is given without `note` (checked after the card lookup, so a missing card is
    reported first), both `depends_on` and `clear_depends_on` are given, a
    `depends_on` value holds a comma or names no card on the board, a `files`
    value is blank or holds a comma, an `access` value is not
    `<name>=<check>` with both parts non-empty, or `tracker_issue` is not the URL of an
    issue in a tracker autonomouse knows.
    """
    if (
        title is None
        and goal is None
        and limits is None
        and test is None
        and size is None
        and position is None
        and depends_on is None
        and not clear_depends_on
        and files is None
        and access is None
        and tracker_issue is None
        and note is None
    ):
        raise CommandError(
            "nothing to edit — pass --title, --goal, --limit (repeatable), --test, "
            "--size, --position, --depends-on (repeatable), --clear-depends-on, "
            "--files (repeatable), --access (repeatable), --tracker-issue and/or --note"
        )
    if note is not None and not note.strip():
        raise CommandError("--note is blank — say why, or leave --note out")
    if depends_on is not None and clear_depends_on:
        raise CommandError(
            "--depends-on and --clear-depends-on cannot both be given — pick one"
        )

    size_enum = None
    if size is not None:
        try:
            size_enum = Size(size)
        except ValueError:
            raise CommandError(
                f"size must be one of {', '.join(s.value for s in Size)} — got {size!r}"
            )

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    judged = [
        name
        for name, value in (
            ("goal", goal),
            ("limits", limits),
            ("test", test),
            ("size", size),
        )
        if value is not None
    ]
    if judged and note is None:
        raise CommandError(
            f"an edit of {', '.join(judged)} needs --note — say why the card changes"
        )
    if depends_on is not None:
        _check_depends_on(depends_on, _board_card_ids(root), card_id=card_id)
    if files is not None:
        _check_files(files, card_id=card_id)
    access_entries = (
        _parse_access(access, card_id=card_id) if access is not None else None
    )
    issue = None
    if tracker_issue is not None:
        try:
            issue = trackers.issue_link(tracker_issue)
        except trackers.TrackerError as e:
            raise CommandError(f"card {card_id}: tracker issue: {e}") from None

    changed = []
    if title is not None:
        card.title = title
        changed.append("title")
    if goal is not None:
        card.goal = goal
        changed.append("goal")
    if limits is not None:
        merged_limits = [RULES_LIMIT]
        for limit in limits:
            if limit not in merged_limits:
                merged_limits.append(limit)
        card.limits = merged_limits
        changed.append("limits")
    if test is not None:
        card.test = test
        changed.append("test")
    if size_enum is not None:
        card.size = size_enum
        changed.append("size")
    if position is not None:
        card.position = position
        changed.append("position")
    if depends_on is not None:
        card.depends_on = list(depends_on)
        changed.append("depends_on")
    if clear_depends_on:
        card.depends_on = []
        changed.append("depends_on cleared via --clear-depends-on")
    if files is not None:
        card.files = list(dict.fromkeys(files))
        changed.append("files")
    if access_entries is not None:
        card.access = access_entries
        changed.append("access")
    if issue is not None:
        card.tracker_issue = issue
        changed.append(f"tracker_issue {issue['identifier']}")

    if changed:
        # A new goal, limits or test is a new plan: its attempts start over, and
        # the old next step no longer applies. `last_step` stays so the next
        # worker can read what the previous plan's last attempt saw.
        row = f"edited: {', '.join(changed)}"
        rewritten = goal is not None or limits is not None or test is not None
        if rewritten and (
            card.iteration or card.phase is not None or card.handoff.next_step
        ):
            card.iteration = 0
            card.phase = None
            card.handoff.next_step = None
            row += "; attempts reset (iteration 0, phase and next_step cleared)"
        if note is not None:
            row += f"; why: {note}"
    else:
        # No field changed: `note` alone is the whole history row, as written.
        row = note

    append_history(
        card,
        run_id=run_id,
        to=card.stack.value,
        from_=card.stack.value,
        note=row,
    )
    save_card(path, card)
    return card


def card_delete(
    root: Path,
    card_id: str,
    *,
    project: str | None = None,
) -> Path:
    """Delete a card's file outright. Its history is kept in git (the board
    folder's own repository), not as a tombstone record — the file itself is
    gone, so the orchestrator's next commit of the board folder shows the
    removal.

    Refused if the card does not exist.
    """
    path = _find_card_path(root, card_id, project)
    path.unlink()
    return path


def _open_decision(card: Card) -> Decision | None:
    """The card's last decision when it is still unanswered, else None."""
    last = card.decisions[-1] if card.decisions else None
    return last if last is not None and last.answer is None else None


def _one_line(text: str) -> str:
    return " ".join(text.split())


# What a run record's `kind` says about who wrote a row, for `_answerer`.
_RUN_KIND_WHO = {
    "wake": "the orchestrator",
    "planner": "the planner",
    "session": "an agent session acting for you",
}


def _answerer(root: Path, run_id: str) -> str:
    """Who wrote an answer or a cancellation, in plain words, from the run id it
    carries: the board screen, `manual` (the command line without a run id), a
    planner's planning thread, or an orchestrator-shaped id, named by its record's
    `kind` when one loads (`wake` is "the orchestrator", `planner` is "the planner",
    `session` is "an agent session acting for you", the same phrase the retired run
    id `human` used to get) and "the orchestrator" — a wake's own id, the common
    case — when none does (a record written before the field existed is a `wake`
    too). Any other id is "an agent". It names no run or thread id: the human reads
    it on Telegram (`autonomouse.human_messages`)."""
    if run_id == SCREEN_RUN_ID:
        return "you, on the board screen"
    if run_id == "manual":
        return "someone at the command line"
    if _ORCHESTRATOR_RUN_RE.fullmatch(run_id):
        # Imported here: `board.runs` imports this module.
        from autonomouse.board.runs import load_run

        try:
            kind = load_run(root, run_id).to_dict().get("kind", "wake")
        except CommandError:  # no record with this id — a wake's own, most often
            kind = "wake"
        return _RUN_KIND_WHO.get(kind, "the orchestrator")
    if run_id.startswith("plan-"):
        return "the planner"
    return "an agent"


def _follow_up(
    root: Path, card_id: str, decision: Decision, run_id: str, text: str
) -> None:
    """Tell the Telegram chat that `decision` left the decision stack by a path other
    than the human's own reply there, so no message in the chat points at a
    decision that is gone. Sent as a reply to the decision's brief when its message
    id is stored; never sent for an answer that came from Telegram itself
    (`TELEGRAM_RUN_ID`), which `notify poll` already acknowledges. The card's id goes
    to `notify` as `about`, for the reference number, and never into the text."""
    if run_id == TELEGRAM_RUN_ID:
        return
    notify(
        text,
        root=root,
        run_id=run_id,
        reply_to=decision.message_id,
        about=card_id,
        html=True,
    )


def card_ask(
    root: Path,
    card_id: str,
    *,
    question: str,
    options: list[str],
    brief: str,
    recommendation: str | None = None,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Ask a decision only the human can make: record it and move the card to
    `decision_needed` (the `in_progress -> decision_needed` transition).

    Stores a new `Decision` — `brief`, `question`, `options`, `recommendation`,
    `asked_at` — unanswered, with `OTHER_OPTION` ("other: tell me") appended as the last option
    (`docs/rules.md` item 8), so no question reaches the human without a way to answer
    in their own words; a caller that already listed it gets it once, at the end. Then
    it sends the brief, question, options and recommendation with
    `autonomouse.notify.notify` so a human sees it without polling the board
    (`autonomouse.human_messages.decision_brief`; the card's id goes as `about`, for
    the reference number, never into the text), and stores the sent message's
    Telegram id on the decision as `message_id`. A
    failed send is logged on this run's record, never raised back here — asking
    the decision must succeed even when the notify channel cannot be reached.

    `brief` is the asking agent's plain-words text for a human who has not read
    the code: what has been done, what is stuck and why, the goal sentence, and what
    happens under each option, in the labelled parts rules item 8 lays out; the
    options and the recommendation come from their own arguments. The code only
    requires it and shows it, its paragraphs as written, under the question; its
    content is the asker's.

    Refused unless the card is `in_progress`, `question` or `brief` is empty,
    `options` is empty (a decision needs at least one option to choose between), or
    `recommendation` is given but blank or whitespace-only (rules item 8: every
    brief carries a recommendation). The CLI always supplies `--recommendation`;
    an internal caller may still omit it and get `None` — only a blank string is
    refused, so decisions recorded before this check, which may hold a null
    recommendation, keep loading.
    """
    if not (question and question.strip()):
        raise CommandError("ask needs --question")
    if not (brief and brief.strip()):
        raise CommandError("ask needs --brief")
    if not options:
        raise CommandError("ask needs at least one --option")
    if recommendation is not None and not recommendation.strip():
        raise CommandError("ask needs --recommendation")

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    from_stack = card.stack

    if from_stack != Stack.IN_PROGRESS:
        raise CommandError(
            f"cannot ask from {from_stack.value!r} — a card must be in_progress "
            "to ask (only `card start` gets it there)"
        )

    ts = now_iso()
    decision = Decision(
        id=f"d{len(card.decisions) + 1}",
        question=question,
        options=[o for o in options if o != OTHER_OPTION] + [OTHER_OPTION],
        recommendation=recommendation,
        asked_at=ts,
        answer=None,
        answered_at=None,
        brief=brief,
    )
    card.decisions.append(decision)
    card.stack = Stack.DECISION_NEEDED
    append_history(
        card,
        run_id=run_id,
        to=Stack.DECISION_NEEDED.value,
        note=f"asked: {question}",
        from_=from_stack.value,
        at=ts,
    )
    save_card(path, card)
    message_id = notify(
        human_messages.decision_brief(decision),
        root=root,
        run_id=run_id,
        about=card.id,
        html=True,
    )
    if message_id is not None:
        # Kept so a follow-up (`_follow_up`) can reply to this exact message.
        decision.message_id = message_id
        save_card(path, card)
    return card


def card_answer(
    root: Path,
    card_id: str,
    *,
    answer: str,
    done: bool = False,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Answer the card's open decision and return it from `decision_needed`.

    Fills in the last decision's `answer` and `answered_at`. The answer is any text:
    one of the options, or the human's own words (`OTHER_OPTION`). Goes to the top of
    `todo` (`position` 0) unless `done` is set, in which case the card goes
    straight to `done` — the work was already finished and the answer needs no
    more of it.

    Unless the answer came from Telegram (`run_id` `telegram`, the human's own
    reply), sends a one-line follow-up there naming the question, who answered and
    the answer, as a reply to the decision's brief (`_follow_up`).

    A card that goes to `todo` reopens a done project (`project_reopen`).

    Refused unless the card is `decision_needed`, it has an unanswered decision,
    or `answer` is empty.
    """
    if not (answer and answer.strip()):
        raise CommandError("answer needs --answer")

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    from_stack = card.stack

    if from_stack != Stack.DECISION_NEEDED:
        raise CommandError(
            f"cannot answer from {from_stack.value!r} — a card must be "
            "decision_needed to answer (only `card ask` gets it there)"
        )
    open_decision = card.decisions[-1] if card.decisions else None
    if open_decision is None or open_decision.answer is not None:
        raise CommandError(f"card {card_id!r} has no open decision to answer")

    ts = now_iso()
    open_decision.answer = answer
    open_decision.answered_at = ts

    to_stack = Stack.DONE if done else Stack.TODO
    card.stack = to_stack
    if not done:
        card.position = 0
    append_history(
        card,
        run_id=run_id,
        to=to_stack.value,
        note=f"answered: {answer}",
        from_=from_stack.value,
        at=ts,
    )
    save_card(path, card)
    if not done:
        project_reopen(root, card.project)
    where = "done" if done else "the top of todo"
    _follow_up(
        root,
        card.id,
        open_decision,
        run_id,
        human_messages.answered(open_decision, _answerer(root, run_id), where),
    )
    return card


def card_progress(
    root: Path,
    card_id: str,
    *,
    phase: str | None = None,
    iteration: int | None = None,
    last_step: str | None = None,
    next_step: str | None = None,
    pr: str | None = None,
    attach: list[str] | None = None,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Record one step of an in_progress card's work.

    Sets any of `phase`, `iteration`, `handoff.last_step`, `handoff.next_step`,
    `pr` and `attachments` — exactly the fields `docs/rules.md` lets a worker write
    on its own card (`phase`, `iteration`, `handoff`, `pr`, `attachments`) —
    leaving every field not passed as it was. `attach` is a list of file paths or
    URLs, appended in order to the card's existing `attachments`. The card stays
    `in_progress`; `progress` never moves it. Each call appends one history row,
    so calling it after every step (`docs/worker.md`'s loop) leaves the rows in the
    order the steps happened. The row's note names the fields it set and carries the
    texts of `last_step` and `next_step` (`board.history.progress_note`), so the
    history keeps every note a worker wrote; `board.stuck` compares the next steps.

    Refused unless the card is `in_progress`, `phase` (when given) is one of
    `writing`/`testing`/`ci`/`review`, `iteration` (when given) is negative, or
    nothing was passed to set.
    """
    if (
        phase is None
        and iteration is None
        and last_step is None
        and next_step is None
        and pr is None
        and not attach
    ):
        raise CommandError(
            "nothing to progress — pass --phase, --iteration, --last-step, "
            "--next-step, --pr and/or --attach"
        )

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    from_stack = card.stack
    if from_stack != Stack.IN_PROGRESS:
        raise CommandError(
            f"cannot progress from {from_stack.value!r} — a card must be "
            "in_progress to progress"
        )

    phase_enum = card.phase
    if phase is not None:
        try:
            phase_enum = Phase(phase)
        except ValueError:
            raise CommandError(
                f"phase must be one of {', '.join(p.value for p in Phase)} — "
                f"got {phase!r}"
            )
    if iteration is not None and iteration < 0:
        raise CommandError(f"iteration must not be negative — got {iteration}")

    ts = now_iso()
    changes = []
    if phase is not None:
        card.phase = phase_enum
        changes.append(f"phase={phase_enum.value}")
    if iteration is not None:
        card.iteration = iteration
        changes.append(f"iteration={iteration}")
    if last_step is not None:
        card.handoff.last_step = last_step
        changes.append("last_step")
    if next_step is not None:
        card.handoff.next_step = next_step
    if pr is not None:
        card.pr = pr
        changes.append(f"pr={pr}")
    for path_or_url in attach or []:
        card.attachments.append(path_or_url)
        changes.append(f"attach={path_or_url}")
    card.handoff.updated_at = ts
    card.handoff.run_id = run_id

    append_history(
        card,
        run_id=run_id,
        to=from_stack.value,
        from_=from_stack.value,
        note=progress_note(changes, next_step, last_step),
        at=ts,
    )
    save_card(path, card)
    return card


def card_retarget(
    root: Path,
    card_id: str,
    *,
    base_branch: str,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Record that an in_progress or done card's PR now targets `base_branch`: the
    wake's PR follow-up (`autonomouse.followup`) calls this after it retargeted the PR
    because the branch it targeted has merged. Only `base_branch` changes, with one
    history row; refused unless the card is `in_progress` or `done`."""
    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    if card.stack not in (Stack.IN_PROGRESS, Stack.DONE):
        raise CommandError(
            f"cannot retarget from {card.stack.value!r} — a card must be in_progress or done"
        )
    old = card.base_branch
    card.base_branch = base_branch
    append_history(
        card,
        run_id=run_id,
        to=card.stack.value,
        from_=card.stack.value,
        note=f"retargeted: base_branch {old} -> {base_branch} (its base merged)",
    )
    save_card(path, card)
    return card


def card_finish(
    root: Path,
    card_id: str,
    *,
    last_step: str | None = None,
    pr: str | None = None,
    no_code: bool = False,
    project: str | None = None,
    run_id: str = "manual",
) -> Card:
    """Finish a card: `in_progress` -> `done`.

    Always needs `last_step` — the verification note (what was run and what was
    seen), or, for a card whose goal produces no code, its findings — recorded in
    `handoff.last_step` per `docs/board-schema.md`. Also needs `pr`, the PR link,
    unless the caller passes `no_code`: `docs/rules.md` says a card whose goal
    produces no code (a spike, a judgment for the human) ends with its findings in
    its notes and no PR, so that is the one case `finish` accepts without one.

    Refused unless the card is `in_progress`, it has an unanswered decision
    (`docs/board-schema.md`'s `finish` transition needs none open), `last_step` is
    empty, or `pr` is missing and `no_code` was not passed.
    """
    if not (last_step and last_step.strip()):
        raise CommandError(
            "finish needs --last-step (the verification note, or a no-code "
            "card's findings)"
        )
    if not no_code and not (pr and pr.strip()):
        raise CommandError(
            "finish needs --pr (or --no-code for a card whose goal produces no code)"
        )

    path = _find_card_path(root, card_id, project)
    card = load_card(path)
    from_stack = card.stack
    if from_stack != Stack.IN_PROGRESS:
        raise CommandError(
            f"cannot finish from {from_stack.value!r} — a card must be "
            "in_progress to finish"
        )
    open_decision = next((d for d in card.decisions if d.answer is None), None)
    if open_decision is not None:
        raise CommandError(
            f"card {card_id!r} has an open decision ({open_decision.question!r}) "
            "— answer it first"
        )

    ts = now_iso()
    if pr is not None:
        card.pr = pr
    card.handoff.last_step = last_step
    card.handoff.updated_at = ts
    card.handoff.run_id = run_id
    card.stack = Stack.DONE
    append_history(
        card,
        run_id=run_id,
        to=Stack.DONE.value,
        from_=from_stack.value,
        note="finished" if card.pr is None else f"finished: {card.pr}",
        at=ts,
    )
    save_card(path, card)
    return card
