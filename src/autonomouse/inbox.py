"""`autonomouse notify poll`: the human's Telegram messages, routed onto the board.

Reads the bot's unconfirmed updates (`autonomouse.notify.updates`), keeps only messages
from `notify.telegram_chat_id` (any other chat is ignored and counted), and routes each.
Every message autonomouse sends about a card or a planning thread opens with a two-digit
reference number and the project's name (`07 Widgets`), and the board's reference
register (`autonomouse.board.references`) holds which card or thread, and which Telegram
message, each number belongs to. The poll reads the register as it was when the poll
started, so a number means what the human saw.

1. **What the message is about**, first match wins:
   - **a reply to a bot message** is about the card or thread the register holds for the
     replied message's id, which it holds for as long as the message's question is open
     (`references.open_messages`); a reply to an older message the register no longer holds is
     about what its `[<id>]` tag names (`autonomouse.notify.tag`), on its first line or
     under its number line, when it carries one;
   - **a message whose first word is a two-digit number**, with or without a leading
     `#` (`07 2`, `#07 2`), is about the card or thread the register holds for that
     number, and the rest of the message is the text. A number the register does not
     hold is not routed (below);
   - **a message that starts with an id** — a card id or a planning thread id, any case,
     optionally followed by `:` — is about that id, and the rest of the message is the
     text;
   - **any other message that is not a reply** answers the one open question — an
     unanswered decision, or a thread waiting on the human — when exactly one is open
     across the board, unless its first word is a project's slug, which asks for a new
     thread on that project. A reply to a message the register cannot place is about
     nothing, as before the register: it points at one message, never at whichever
     question happens to be open.
2. **About a card:** the text answers its open decision with `card answer` — an option
   number, or text equal to an option's in any case and ignoring surrounding spaces,
   picks that option's text (picking "other: tell me" is refused: the human's own words
   are the answer); `done`, when no option is named `done`, answers with `--done`;
   anything else is the answer as written. When the card's last decision is already
   answered — the human replies to the line that says it was answered from a shell
   (`board.commands._follow_up`), or answers the question late — the text is recorded
   on that decision as its `reply`, as written; the answer stays, no thread opens, and
   the card stays where it is unless the human set it aside: when the answer was
   `leave this card for now` and the card is in backlog or todo, a reply that starts
   with `go ahead`, in any case, moves a backlog card to todo, or keeps a todo card
   there, either way ready again, and the acknowledgement of any other reply to a
   backlog card tells the human to reply `go ahead`. When the card was cancelled while
   its decision was still open — the human replies to the line that says the decision
   is no longer needed (`board.commands._follow_up`, from `card_cancel`) — the text is
   recorded the same way, on the decision that was never answered; the card stays
   cancelled, and the acknowledgement says so and that a message which is not a reply
   asks for new work.
3. **About a planning thread:** the text is appended to the thread as a human message
   (`plan reply`). A closed thread refuses replies, so a reply to one opens a new thread
   on the same project instead.
4. **About nothing** — none or several questions open, or a project named first — opens a
   new planning thread on the project whose slug is the message's first word, or else on
   the project of the most recent thread message or decision on the board. Board times
   are whole seconds, so a poll's own writes share a second with each other and with what
   was written just before the poll; the order the poll wrote them in decides, and the
   thread message this poll wrote last is the most recent.

Each routed message gets a short acknowledgement back on Telegram, numbered and naming
the project like every message about a card or thread, that says in plain words what was
recorded and where it went. When every message is handled, the updates are confirmed so
the next poll never reads them again. A message that could not be routed (a card that
never asked a decision, a number no option has) is printed, acknowledged with the reason
and "Nothing was recorded", and confirmed too: sending it again is the fix. When it named
no open question — a number the register does not hold, a card with no question, a card
or thread no longer on the board — the acknowledgement lists the open questions by number
and project.

`autonomouse plan step` runs this first (`autonomouse.cli.shared._plan_step`), so a reply
reaches the board at the start of every wake and in the morning review. With Telegram off
(`notify.telegram = false`) it runs nothing there; run by hand, this says Telegram is off.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from autonomouse import notify as telegram
from autonomouse.board import plans as board_plans
from autonomouse.board import references
from autonomouse.board.commands import (
    TELEGRAM_RUN_ID,
    CommandError,
    _find_card_path,
    _one_line,
    card_answer,
    card_list,
    project_list,
    project_reopen,
)
from autonomouse.board.history import append_history, now_iso
from autonomouse.board.schema import (
    OTHER_OPTION,
    BoardValidationError,
    Card,
    Decision,
    Stack,
)
from autonomouse.board.store import load_card, save_card

RUN_ID = TELEGRAM_RUN_ID
# A `[<id>]` tag opening a message, or opening its second line under a reference number
# line (`07 Widgets`), as the brief `card ask` sends carries it.
_TAG_RE = re.compile(r"^\s*(?:\d{2} [^\n]*\n)?\[([a-z0-9-]+)\]")
_NUMBER_RE = re.compile(r"^\s*#?(\d{2})(?:\s+|$)(.*)$", re.DOTALL)
_LEAD_RE = re.compile(r"^\s*([A-Za-z0-9-]+):?(?:\s+|$)(.*)$", re.DOTALL)
# The option an access question offers to set a card aside
# (docs/orchestrator/access-checks.md), and the reply that makes a card so answered,
# in backlog or still in todo, ready again.
SET_ASIDE = "leave this card for now"
_GO_AHEAD_RE = re.compile(r"go\s+ahead\b", re.IGNORECASE)


@dataclass
class Counts:
    answered: int = 0
    recorded: int = 0
    cancel_replies: int = 0
    replied: int = 0
    opened: int = 0
    other_chats: int = 0
    not_routed: int = 0

    def line(self) -> str:
        return (
            f"poll: {self.answered} decisions answered, {self.recorded} replies to "
            f"answered decisions recorded, {self.cancel_replies} replies to cancelled "
            f"decisions recorded, {self.replied} thread replies, "
            f"{self.opened} threads opened, {self.not_routed} not routed, "
            f"{self.other_chats} from other chats ignored"
        )


class NotOpen(CommandError):
    """The message names no question that is open; its acknowledgement lists the open
    ones by number and project."""


def _open_questions(root: Path) -> list[tuple[str, str]]:
    """(card or thread id, project slug) of every question waiting on the human: a card's
    unanswered decision, or a thread whose planner waits for the human."""
    waiting = [
        (card.id, card.project)
        for card in card_list(root)
        if card.stack == Stack.DECISION_NEEDED
        and card.decisions
        and card.decisions[-1].answer is None
    ]
    waiting += [
        (thread.id, thread.project)
        for thread in board_plans.thread_list(root)
        if thread.status is board_plans.ThreadStatus.WAITING_HUMAN
    ]
    return waiting


def _open_list(root: Path) -> str:
    """The open questions in plain words, by number and project, for an acknowledgement."""
    waiting = _open_questions(root)
    if not waiting:
        return "No question is waiting for you."
    entries = references.load(root)
    named = []
    for about, project in waiting:
        name = references.project_name(root, project)
        ref = references.number_of(entries, about)
        named.append(f"{ref.label} {name}" if ref else f"one on {name} with no number")
    return (
        f"The questions waiting for you: {', '.join(sorted(named))}. Reply to one of "
        "those messages, or start your message with its number."
    )


def _names_project(root: Path, text: str) -> str | None:
    """The project whose slug is the message's first word, if any."""
    lead = _LEAD_RE.match(text)
    word = lead.group(1).lower() if lead else None
    return word if word in {p.slug for p in project_list(root)} else None


def _target(
    root: Path, message: dict, entries: list[references.Reference]
) -> tuple[str | None, str]:
    """What the message is about (a card or thread id, or None) and its text.

    `entries` is the reference register as the poll found it.

    Raises:
        NotOpen: the message's first word is a number the register does not hold.
    """
    text = message.get("text", "").strip()
    reply = message.get("reply_to_message")
    if reply is not None:
        ref = references.by_message(entries, reply.get("message_id"))
        if ref is not None:
            return ref.about, text
        found = _TAG_RE.match(reply.get("text", ""))
        if found and references.is_about(found.group(1)):
            return found.group(1), text
    number = _NUMBER_RE.match(text)
    if number:
        ref = references.by_number(entries, int(number.group(1)))
        if ref is None:
            raise NotOpen(f"no message carries the number {number.group(1)} now")
        return ref.about, number.group(2).strip()
    lead = _LEAD_RE.match(text)
    if lead and references.is_about(lead.group(1).lower()):
        return lead.group(1).lower(), lead.group(2).strip()
    # A reply points at one message: one the register cannot place never answers
    # whichever question happens to be open.
    if reply is None and _names_project(root, text) is None:
        waiting = _open_questions(root)
        if len(waiting) == 1:
            return waiting[0][0], text
    return None, text


def _about_card(root: Path, card_id: str, text: str, counts: Counts) -> str:
    """Answer the card's open decision, record the text on its answered one, or, for a
    card cancelled while its decision was still open, record the text as a reply to
    that never-answered decision; return the acknowledgement. A card that never asked a
    decision refuses the message."""
    if not any(root.glob(f"projects/*/cards/{card_id}.json")):
        raise NotOpen("that card is no longer on the board")
    path = _find_card_path(root, card_id, None)
    card = load_card(path)
    decision = card.decisions[-1] if card.decisions else None
    if decision is None:
        raise NotOpen("this card has no question waiting for an answer")
    if decision.answer is None and card.stack is Stack.DECISION_NEEDED:
        ack = _answer(root, card_id, decision, text)
        counts.answered += 1
    elif decision.answer is None:
        # The only other way a decision stays unanswered: `card_cancel` left the card
        # in `cancelled` with its open decision untouched (`docs/board-schema/card.md`,
        # the `decisions` row).
        ack = _record_cancelled_reply(path, card, decision, text)
        counts.cancel_replies += 1
    else:
        ack = _record_reply(root, path, card, decision, text)
        counts.recorded += 1
    return ack


def _record_cancelled_reply(path: Path, card: Card, decision: Decision, text: str) -> str:
    """Record the human's reply to a card cancelled while its decision was still open
    (`board.commands.card_cancel`'s follow-up); return the acknowledgement.

    The decision stays unanswered and the card stays cancelled: a reply here asks
    nothing more of it, the way `_record_reply` leaves an answered card where it is.
    """
    ts = now_iso()
    decision.reply = text
    decision.replied_at = ts
    append_history(
        card,
        run_id=RUN_ID,
        to=card.stack.value,
        from_=card.stack.value,
        note=f"reply to cancelled decision {decision.id}: {text}",
        at=ts,
    )
    save_card(path, card)
    return (
        f'recorded as a reply to the cancelled question "{_one_line(decision.question)}". '
        "The card is cancelled; a message that is not a reply asks for new work."
    )


def _record_reply(root: Path, path: Path, card: Card, decision: Decision, text: str) -> str:
    """Record the human's reply on the card's answered decision; return the
    acknowledgement.

    The answer stays as it was. A later reply replaces the recorded one, and the card's
    history keeps a row for each. The card's stack stays too, except for a card the human
    set aside: in backlog or still in todo, answered `leave this card for now`. A reply
    to it that starts with `go ahead` moves a backlog card to todo, or keeps a todo card
    there, and the reply's row records the move (or the stay). A backlog card moved to
    todo reopens a done project.
    """
    ts = now_iso()
    decision.reply = text
    decision.replied_at = ts
    answer = decision.answer or ""
    was_backlog = card.stack == Stack.BACKLOG
    set_aside = (
        card.stack in (Stack.BACKLOG, Stack.TODO) and answer.strip().lower() == SET_ASIDE
    )
    go_ahead = set_aside and _GO_AHEAD_RE.match(text) is not None
    from_ = card.stack.value
    if go_ahead and was_backlog:
        card.stack = Stack.TODO
    append_history(
        card,
        run_id=RUN_ID,
        to=card.stack.value,
        from_=from_,
        note=f"reply to answered decision {decision.id}: {text}",
        at=ts,
    )
    save_card(path, card)
    if go_ahead and was_backlog:
        project_reopen(root, card.project)
    recorded = (
        f'recorded as a reply to the answered question "{_one_line(decision.question)}".'
    )
    if go_ahead:
        if was_backlog:
            return f"{recorded} The card you set aside is back in todo, ready for a wake."
        return f"{recorded} The card you set aside stays in todo, ready for a wake."
    kept = f'Its answer stays "{_one_line(answer)}"'
    if set_aside and was_backlog:
        return (
            f"{recorded} {kept} and the card stays in backlog; reply "
            '"go ahead" to make it ready again.'
        )
    return (
        f"{recorded} {kept} and the card does not move; to ask for more work, send a "
        f"message that is not a reply."
    )


def _answer(root: Path, card_id: str, decision: Decision, text: str) -> str:
    """Answer the card's open `decision`; return the acknowledgement.

    A number, or text equal to an option's, picks that option. Only text that picks no
    option can mean the work is finished: an access question offers an option named
    `done` (docs/orchestrator/access-checks.md), and typing it answers with it.
    """
    typed = text.strip().lower()
    names = [option.strip().lower() for option in decision.options]
    n = None
    if text.isdigit():
        n = int(text)
        if not 1 <= n <= len(decision.options):
            raise CommandError(
                f"this question has options 1 to {len(decision.options)}; got {n}"
            )
    elif typed in names:
        n = names.index(typed) + 1
    answer = text if n is None else decision.options[n - 1]
    if n is not None and answer == OTHER_OPTION:
        raise CommandError(
            f"option {n} is '{OTHER_OPTION}': reply with your answer in your own words"
        )
    done = n is None and typed == "done"
    card_answer(root, card_id, answer=answer, done=done, run_id=RUN_ID)
    where = "done" if done else "the top of todo"
    return (
        f'answer recorded on the question "{_one_line(decision.question)}": {answer}. '
        f"The card is now in {where}."
    )


def _latest_project(root: Path) -> str | None:
    """The project of the most recent thread message or decision on the board.

    Board times are whole seconds. Two moments on two projects in the same second have
    no recorded order, and the project whose slug sorts last wins.
    """
    moments: list[tuple[str, str]] = []
    for thread in board_plans.thread_list(root):
        last = thread.messages[-1].at if thread.messages else thread.opened_at
        moments.append((last, thread.project))
    for card in card_list(root):
        moments.extend((d.asked_at, card.project) for d in card.decisions)
    return max(moments)[1] if moments else None


def _open(root: Path, text: str, written: str | None) -> tuple[str, str, str]:
    """Open a new thread; return its id, the acknowledgement and its project.

    `written` is the project of the last thread message this poll wrote, which is the most
    recent moment on the board. Its time often shares a second with the moments before
    it, so only the poll knows it came last.
    """
    project = _names_project(root, text) or written or _latest_project(root)
    if project is None:
        raise CommandError(
            "no project named in the message and none on the board to default to; "
            "start the message with a project's name"
        )
    thread = board_plans.thread_open(root, project, text)
    ack = (
        f"your message opened a new planning conversation on "
        f"{references.project_name(root, project)}. The planner answers here when it "
        "has read it."
    )
    return thread.id, ack, project


def _request(thread: board_plans.Thread) -> str:
    """The thread's first message, the human's request, on one line."""
    first = _one_line(thread.messages[0].text) if thread.messages else ""
    return first if len(first) <= 60 else first[:57] + "..."


def _route(
    root: Path, about: str | None, text: str, counts: Counts, written: str | None
) -> tuple[str, str, str | None]:
    """Route one message's `text` to `about`, a card or thread id, or to a new thread
    when None; return (what it went to, acknowledgement, the project of the thread
    message it wrote, or None when the message was about a card).

    `written` is the project of the last thread message this poll wrote, if any.
    """
    if not text:
        raise CommandError("the message has no text")
    if about is not None and references.CARD_ID_RE.fullmatch(about):
        return about, _about_card(root, about, text, counts), None
    if about is not None:
        try:
            thread = board_plans.load_thread(root, about)
        except CommandError as e:
            raise NotOpen("that planning conversation is no longer on the board") from e
        if thread.status is board_plans.ThreadStatus.CLOSED:
            opened = board_plans.thread_open(root, thread.project, text)
            counts.opened += 1
            ack = (
                f'the planning conversation "{_request(thread)}" is closed, so your '
                "message opened a new one on "
                f"{references.project_name(root, thread.project)}. The planner answers "
                "here when it has read it."
            )
            return opened.id, ack, thread.project
        board_plans.thread_reply(root, about, text)
        counts.replied += 1
        ack = (
            f'your reply is added to the planning conversation "{_request(thread)}". '
            "The planner answers here when it has read it."
        )
        return about, ack, thread.project
    thread_id, ack, project = _open(root, text, written)
    counts.opened += 1
    return thread_id, ack, project


def poll(
    root: Path,
    *,
    config: Mapping,
    env: Mapping[str, str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Route every unconfirmed message; exit code 0, or 1 when Telegram could not be read.

    Raises nothing: Telegram being off, a missing token, chat id or network failure is
    printed with its fix.
    """
    if not telegram.telegram_on(config):
        out(f"poll: {telegram.off_message()}")
        return 1
    chat_id = str(config.get("notify", {}).get("telegram_chat_id", ""))
    if not chat_id:
        out(
            "poll: no notify.telegram_chat_id is configured; run `autonomouse notify "
            "setup` first (docs/config.md)"
        )
        return 1
    try:
        pending = telegram.updates(env)
    except telegram.NotifyError as e:
        out(f"poll: {e}")
        return 1

    counts = Counts()
    written: str | None = None
    try:
        entries = references.load(root)
    except BoardValidationError as e:
        out(f"poll: {e}")
        return 1
    for update in pending:
        message = update.get("message")
        if message is None:
            continue
        if str(message.get("chat", {}).get("id")) != chat_id:
            counts.other_chats += 1
            continue
        target = None
        try:
            target, text = _target(root, message, entries)
            about, ack, project = _route(root, target, text, counts, written)
        except (CommandError, BoardValidationError) as e:
            counts.not_routed += 1
            out(f"poll: message {message.get('message_id')} not routed: {e}")
            ack = f"Nothing was recorded: {e}."
            if isinstance(e, NotOpen):
                try:
                    ack += " " + _open_list(root)
                except (CommandError, BoardValidationError) as listing:
                    out(f"poll: could not list the open questions: {listing}")
            telegram.notify(ack, root=root, about=target, config=config, env=env)
            continue
        written = project or written
        out(f"poll: message {message.get('message_id')} -> {about}: {ack}")
        telegram.notify(ack, root=root, about=about, config=config, env=env)

    if pending:
        try:
            telegram.confirm(pending[-1]["update_id"], env)
        except telegram.NotifyError as e:
            out(f"poll: routed, but could not confirm the updates: {e}")
            out(counts.line())
            return 1
    out(counts.line())
    return 0
