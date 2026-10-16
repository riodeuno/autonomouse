"""The text of the Telegram messages the board's commands send the human.

The human reads these on a phone and has not read the code, so none of them writes a
card's or a planning thread's board id, and none names a command to type. The card or
thread a message is about travels beside the text, as `notify`'s `about`
(`autonomouse.notify`), which puts the reference number and the project's name on line
one and registers them, so the human's reply finds its way back.

Every message is Telegram HTML (`autonomouse.notify`, `html`), laid out for a phone
(`docs/rules/08-questions-for-the-human.md`, "How a message to the human is laid out"):
a bold one-line heading first, then short lines, and blank lines between the parts. A
decision's brief (`card_ask`) opens with its question in bold, then the asking agent's
brief with its own paragraphs and line breaks as sent, then the options as a numbered
list with the recommendation, then how to answer. The follow-ups (`card_answer`,
`card_cancel`) and the wake's own messages (the wake-failure notice
and the wake-end report, `autonomouse.controller`) open with a bold heading line, then
one labelled line per fact. Every message the planner sends with `notify --about` is the
planner's own text, its question in bold (`with_bold`).
"""

from __future__ import annotations

import html

from autonomouse.board.schema import Decision


class NotInText(ValueError):
    """The text to put in bold is not in the message."""


def _html(text: str) -> str:
    return html.escape(text, quote=False)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _paragraphs(text: str) -> str:
    """`text` with its line breaks and single blank lines kept as written: each line's
    trailing spaces dropped, a run of blank lines made one, and the ends trimmed."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    kept = [line for n, line in enumerate(lines) if line or lines[n - 1]]
    return "\n".join(kept)


def _heading(text: str) -> str:
    return f"<b>{_html(_one_line(text))}</b>"


def decision_brief(decision: Decision) -> str:
    """The message `card_ask` sends, as Telegram HTML: the question in bold as its
    heading line, the asking agent's brief with its own paragraphs and line breaks, the
    options numbered one per line with the recommendation, then how to answer
    (`docs/rules.md` item 8's shape), so the human can decide from the message alone. A
    reply naming an option's number, or `done`, is read by `autonomouse notify poll`
    (`autonomouse.inbox`): `done` picks the option named `done` when there is one, so
    only a question without one says `done` finishes the work."""
    options = [f"{n}. {_html(option)}" for n, option in enumerate(decision.options, 1)]
    if decision.recommendation:
        options.append(f"Recommendation: {_html(decision.recommendation)}")
    names = [option.strip().lower() for option in decision.options]
    if "done" in names:
        on_done = f"a reply of 'done' picks option {names.index('done') + 1}"
    else:
        on_done = "a reply of 'done' means the work is finished and needs nothing more"
    closing = (
        "Reply to this message with an option number or your own answer, or answer on "
        f"the board screen; {on_done}."
    )
    parts = [
        _heading(decision.question),
        _html(_paragraphs(decision.brief or "")),
        "\n".join(options),
        closing,
    ]
    return "\n\n".join(part for part in parts if part.strip())


def answered(decision: Decision, who: str, where: str) -> str:
    """The follow-up `card_answer` sends when the decision was answered off Telegram."""
    return "\n".join(
        [
            _heading(f"Answered: {decision.question}"),
            f"Answer: {_html(_one_line(decision.answer or ''))}",
            f"Answered by: {_html(who)}",
            f"The card is now in {_html(where)}.",
        ]
    )


def cancelled(decision: Decision, who: str) -> str:
    """The follow-up `card_cancel` sends for a card whose decision was still open. The
    canceller's reason stays on the board: it is written for the board and can name
    other cards by their ids."""
    return "\n".join(
        [
            _heading(f"No longer needed: {decision.question}"),
            f"The card was cancelled by {_html(who)}.",
        ]
    )


def _times(n: int) -> str:
    return "1 time" if n == 1 else f"{n} times"


def wake_failed(project: str, error: str, attempts: int) -> str:
    """The notice `autonomouse run` sends when a wake failed and Jev's answer is to tell
    the human (`autonomouse.controller`, `_recover`)."""
    return "\n".join(
        [
            _heading(f"The wake on {project} failed and needs you"),
            f"What went wrong: {_html(_one_line(error))}",
            f"It has failed {_times(attempts)} in a row.",
        ]
    )


def wake_report(project: str, finished: str, waiting: str) -> str:
    """The one report a wake ends with (sys-w23, `autonomouse.controller`,
    `_wake_report`): `finished` and `waiting` are the counts in words."""
    return "\n".join(
        [
            _heading(f"The wake on {project} has ended"),
            f"Finished: {_html(finished)}",
            f"Waiting for you to merge: {_html(waiting)}",
        ]
    )


def with_bold(text: str, bold: str | None) -> str:
    """`text` as Telegram HTML, its first `bold` in bold: how `notify --about` sends the
    planner's plain text with its question in bold.

    Raises:
        NotInText: `bold` is given and is not part of `text`.
    """
    if not bold or not bold.strip():
        return _html(text)
    # Found in the plain text, so a bold `amp` never lands inside the `&amp;` of an `&`.
    before, found, after = text.partition(bold.strip())
    if not found:
        raise NotInText(f"the text to put in bold is not in the message: {bold!r}")
    return f"{_html(before)}<b>{_html(found)}</b>{_html(after)}"
