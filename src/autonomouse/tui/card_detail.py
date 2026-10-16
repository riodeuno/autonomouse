"""The card details pane: everything about the selected card the board's cramped
card face has no room for, shown as a document in the shared reading view
(`autonomouse.tui.reading.ReadingView`).

`BoardScreen` hands the currently focused item's card (or `None`, for the
empty-stack marker) to `CardDetail.show` whenever focus moves. `card_line` and
`format_card` are pure functions, kept separate from the widget, so what they produce
can be checked without driving a live screen.

The pane opens with one line (`card_line`, drawn by `CardLine`), as the sample the
human picked on 2026-09-30 does: the card's icon, id and title; its stack as a pill
(`autonomouse.tui.look`), which for an `in_progress` card says its phase in the word
`autonomouse.overview.phase_word` gives it (`stopped` while no wake works the card's
project, sys-w108, which `CardDetail.working` carries); its size as a badge; and at the
right how long ago the card was created and last updated. A title too long for one row
flows on to the next, and a pill is never broken. The two times move on once a minute
while the card stays shown. The line is a widget of its own, not Markdown, so its pills
keep their filled colour.

Under it `format_card`'s one Markdown text holds the fields in four groups, each under
a heading with its icon (`autonomouse.tui.icons`), always in this order, so the eye
learns where each one is:

- Progress: where the work stands, first because it is what changes while the human
  watches. Its facts (the phase and iteration of a card a worker had, the pull request
  and branch, the worker's model, reasoning and time since pickup while it is
  `in_progress`), then the last step, the next step and the attachments. A fact or
  field with nothing in it is left out, and a card with none says "Not started." in
  one line, so a todo card's goal is two lines from the top.
- Goal: the goal, then the limits (when the card has any besides the rules) and the
  test.
- Questions, when the card has any: the heading says how many are open, after the
  waiting icon, or that all are answered, after the passed icon.
- History.

Each field is a section with its own heading, under its group's, and each heading
and fact has its icon: last step, next step, attachments, limits, test, brief,
recommendation, model, iteration and time since pickup.

The icons (`autonomouse.tui.icons`, in the form the app holds) mark state and kind
too: the phase beside its word, the pull request, each history row's stacks, and the
answer a decision waits for. Each question, and the decision a `decision_needed` card
waits on, opens with its `OPEN` or `ANSWERED` pill. A pill in Markdown is a paragraph
of the pill's words alone (`_question_pill`); the pane draws such a paragraph as the
filled pill (`_PillParagraph`), so the source stays plain text a reader can copy.

Fields that agents write in prose (goal, limits, test, notes, questions, history
notes, a decision's brief, question, options, recommendation and answer) go in as
the Markdown they are, so a list in a goal shows as a list. Every other piece of
board text (the id, the title, a branch, a URL, an attachment's path) goes through
`reading.plain`, so it shows as written.

A `decision_needed` card is shown to a human who has not read the code
(`docs/rules.md` item 8). Its pane opens with the open decision alone, under one
heading that says it waits on the human: the question first, because it is what the
human has to answer, as the question heads the same decision on Telegram
(`docs/rules.md` item 8, "How a message to the human is laid out"); then the asking
agent's brief, the options with "other: tell me" last, the recommendation, then the
answer. Everything a worker writes — goal, limits, test, phase, iteration, handoff
notes, branch, PR link, history — sits behind the details toggle (`d` on the board),
off by default, because those fields are not a brief and never stand in for one.

The limit "docs/rules.md applies" is never shown: the rules apply to every card.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.content import Content
from textual.geometry import Size
from textual.widget import Widget
from textual.widgets import Markdown
from textual.widgets.markdown import MarkdownBlock

from autonomouse.board.schema import (
    OTHER_OPTION,
    Card,
    Decision,
    HistoryEntry,
    Stack,
)
from autonomouse.overview import STOPPED, phase_word
from autonomouse.tui.format import elapsed
from autonomouse.tui.icons import NERD, PLAIN, Icons
from autonomouse.tui.look import (
    STACK_LOOK,
    pill,
    question_pill,
    size_badge,
    stack_label,
    stack_pill,
)
from autonomouse.tui.reading import COLUMN_WIDTH, ReadingView, plain

NO_SELECTION = "(no card selected)"
DETAILS_HINT = "press d for the rest of the card: its progress, goal, questions, history"
NO_BRIEF = "(none: this question was asked before every question carried a brief)"
ANSWER_HINT = "press a to answer"
NONE = "None."
NOT_STARTED = "Not started."
# every card carries this limit, so it tells the reader nothing
RULES_LIMIT = "docs/rules.md applies"


def _field(text: str) -> str:
    """A field an agent wrote, as the Markdown it is; blank as `NONE`."""
    return text.strip() or NONE


def _item(text: str, marker: str = "- ") -> str:
    """`text` as one list item: its later lines indented under the marker, so a
    field with several lines stays inside its item."""
    first, *rest = text.strip().splitlines() or [""]
    pad = " " * len(marker)
    return "\n".join([marker + first, *(pad + line if line else "" for line in rest)])


def _list(items: list[str]) -> str:
    return "\n".join(_item(item) for item in items) or NONE


def _facts(facts: list[tuple[str, str]]) -> str:
    """`Name: value` pairs as a list, the name bold and the value as written."""
    return "\n".join(f"- **{name}:** {plain(value)}" for name, value in facts)


def _heading(icon: str, words: str) -> str:
    """A group's heading: its icon, then its words."""
    return f"## {icon} {words}"


def _limits(limits: list[str]) -> list[str]:
    """The card's limits without `RULES_LIMIT`, which every card carries."""
    return [x for x in limits if x.strip().rstrip(".").lower() != RULES_LIMIT]


def state_pill(card: Card, *, icons: Icons = PLAIN, working: bool = True) -> Content:
    """The card's stack as a pill; for an `in_progress` card the word `phase_word`
    gives it (its phase, `starting` or `stopped`), in the in_progress colour."""
    word = phase_word(card, working=working)
    if word is None:
        return stack_pill(card.stack, icons=icons)
    if word == STOPPED:
        icon = icons.stopped
    elif card.phase is not None:
        icon = icons.phase[card.phase]
    else:  # starting: no phase written yet
        icon = icons.stack[Stack.IN_PROGRESS]
    return pill(icon, word, STACK_LOOK[Stack.IN_PROGRESS].colour, icons=icons)


def card_times(card: Card, *, now: datetime, icons: Icons = PLAIN) -> Content:
    """`created 3m ago | updated 3m ago`, after the elapsed-time icon, muted."""
    return Content.styled(
        f"{icons.elapsed} created {elapsed(card.created_at, now=now)}"
        f" | updated {elapsed(card.updated_at, now=now)}",
        "$text-muted",
    )


def card_line(
    card: Card,
    *,
    width: int,
    now: datetime,
    icons: Icons = PLAIN,
    working: bool = True,
) -> list[Content]:
    """The pane's opening line in rows `width` cells wide: the card icon and id, the
    title, the stack pill (`state_pill`) and the size badge, and `card_times` at the
    right of the first row. It is one row when all of it fits. A longer title flows on
    to the rows below, which take the whole width; the id and a word of the title are
    never broken, and the pill and the badge stay together. When the times leave the
    first row too little room they go on a row of their own at the end."""
    times = card_times(card, now=now, icons=icons)
    tokens: list[tuple[str, Content]] = [
        ("", Content.styled(f"{icons.card} {card.id}", "bold $text-primary"))
    ]
    words = card.title.split()
    tokens += [
        ("  " if n == 0 else " ", Content.styled(word, "bold"))
        for n, word in enumerate(words)
    ]
    # the pill and the badge stay together, on the row of the title's last word or
    # the start of the next
    state = Content.assemble(
        state_pill(card, icons=icons, working=working),
        " ",
        size_badge(card.size, icons=icons),
    )
    tokens.append(("  ", state))
    room = width - times.cell_length - TIMES_GAP
    if room < MIN_FIRST_ROW:
        rows = _flow(tokens, first=width, rest=width)
        return [*rows, times.pad_left(max(width - times.cell_length, 0))]
    rows = _flow(tokens, first=room, rest=width)
    rows[0] = rows[0].pad_right(width - rows[0].cell_length - times.cell_length) + times
    return rows


# the space between the first row's words and its times, and the least room the words
# keep beside the times before the times move to a row of their own
TIMES_GAP = 2
MIN_FIRST_ROW = 24


def _flow(tokens: list[tuple[str, Content]], *, first: int, rest: int) -> list[Content]:
    """`tokens`, each after its gap, in rows at most `first` cells wide, then `rest`;
    a token starts a new row when it does not fit, and a row's first token drops its
    gap. A token wider than its row is left whole: the widget crops it."""
    rows: list[Content] = []
    row, limit = Content(""), first
    for gap, token in tokens:
        if row.cell_length and row.cell_length + len(gap) + token.cell_length > limit:
            rows.append(row)
            row, limit = Content(""), rest
        row = row + gap + token if row.cell_length else token
    return [*rows, row]


class CardLine(Widget):
    """The pane's opening line (`card_line`), laid out for the width it has."""

    DEFAULT_CSS = """
    CardLine {
        height: auto;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.card: Card | None = None
        self.now = datetime.now(UTC)
        self.working = True

    def show(self, card: Card, *, now: datetime, working: bool) -> None:
        self.card, self.now, self.working = card, now, working
        self.refresh(layout=True)

    def rows(self, width: int) -> list[Content]:
        if self.card is None or width <= 0:
            return []
        return card_line(
            self.card,
            width=width,
            now=self.now,
            icons=self.app.icons,
            working=self.working,
        )

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self.rows(width))

    def render(self) -> Content:
        return Content("\n").join(self.rows(self.size.width))


def _question_pill(*, answered: bool, icons: Icons) -> str:
    """A question's `OPEN` or `ANSWERED` pill as a Markdown paragraph: the pill's words
    alone, which `_PillParagraph` draws as the pill."""
    return question_pill(answered=answered, icons=icons).plain.strip()


# the pill each such paragraph stands for, in both icon forms
_PILLS: dict[str, Content] = {
    _question_pill(answered=answered, icons=icons): question_pill(
        answered=answered, icons=icons
    )
    for answered in (False, True)
    for icons in (PLAIN, NERD)
}


# Textual's own paragraph block, which it does not export by name
_Paragraph = Markdown.BLOCKS["paragraph_open"]


class _PillParagraph(_Paragraph):
    """A paragraph that draws the words of a question pill (`_question_pill`) as the
    filled pill, and every other paragraph as Markdown does."""

    def set_content(self, content: Content) -> None:
        super().set_content(_PILLS.get(content.plain.strip(), content))


_BLOCKS: dict[str, type[MarkdownBlock]] = {
    **Markdown.BLOCKS,
    "paragraph_open": _PillParagraph,
}


def _questions_heading(decisions: list[Decision], *, icons: Icons) -> str:
    """How many questions wait on the human, after the waiting icon, or that none do,
    after the passed icon."""
    open_ = sum(1 for d in decisions if not d.answer)
    if open_:
        return _heading(icons.waiting, f"Questions: {open_} open")
    return _heading(icons.passed, "Questions: all answered")


def _questions(decisions: list[Decision], *, icons: Icons) -> str:
    """Each question as one list item that opens with its `OPEN` or `ANSWERED` pill."""
    items = []
    for decision in decisions:
        text = _question_pill(answered=bool(decision.answer), icons=icons)
        text += f"\n\n{decision.question.strip()}"
        text += f"\n\n*Options:* {'; '.join(o.strip() for o in decision.options)}"
        if decision.recommendation and not decision.answer:
            text += f"\n\n*Recommended:* {decision.recommendation.strip()}"
        if decision.answer:
            text += f"\n\n**Answer:** {decision.answer.strip()}"
        items.append(text)
    return _list(items)


def _when(at: str) -> str:
    """`2026-09-22T08:55:16Z` -> `2026-09-22 08:55`; anything else unchanged."""
    return at[:16].replace("T", " ") if len(at) >= 16 else at


def _stack_word(stack: str, *, icons: Icons) -> str:
    """A stack as a history row shows it: its icon and its word. The card file's
    schema takes any text there, so a row that names no stack shows its word alone."""
    icon = icons.stack.get(stack)
    word = plain(stack_label(stack))
    return f"{icon} {word}" if icon else word


def _history(entries: list[HistoryEntry], *, icons: Icons) -> str:
    items = []
    for entry in entries:
        from_ = _stack_word(entry.from_, icons=icons) if entry.from_ else "new"
        text = f"*{plain(_when(entry.at))}* {from_} → {_stack_word(entry.to, icons=icons)}"
        if entry.note:
            text += f": {entry.note.strip()}"
        items.append(text)
    return _list(items)


def _progress_facts(
    card: Card, *, now: datetime, icons: Icons, working: bool = True
) -> list[tuple[str, str]]:
    """The facts of the progress group, the phase first: an `in_progress` card's phase,
    iteration and worker (model, reasoning, time since pickup), a `decision_needed`
    card's phase and iteration where its work stopped, then any card's pull request and
    branch. A fact the card does not have is left out. An `in_progress` card's phase
    reads `stopped` while no wake works its project (`working`)."""
    facts: list[tuple[str, str]] = []
    if card.stack in (Stack.IN_PROGRESS, Stack.DECISION_NEEDED):
        phase = (
            f"{icons.phase[card.phase]} {card.phase.value}"
            if card.phase is not None
            else "—"
        )
        if card.stack == Stack.IN_PROGRESS and not working:
            phase = STOPPED
        facts += [
            ("Phase", phase),
            (f"{icons.iteration} Iteration", str(card.iteration)),
        ]
    if card.pr:
        facts.append((f"{icons.pr} Pull request", card.pr))
    if card.branch:
        facts.append(("Branch", card.branch))
    if card.stack == Stack.IN_PROGRESS:
        facts += [
            (f"{icons.model} Model", card.assigned.model or "—"),
            ("Reasoning", card.assigned.effort or "—"),
            (
                f"{icons.elapsed} Picked up",
                elapsed(card.assigned.picked_up_at, now=now),
            ),
        ]
    return facts


def _progress(
    card: Card, *, now: datetime, icons: Icons, working: bool = True
) -> list[str]:
    """The progress group: its heading, its facts, then the last step, the next step and
    the attachments, each only when it has something; "Not started." when none does."""
    blocks = [_heading(icons.progress, "Progress")]
    facts = _progress_facts(card, now=now, icons=icons, working=working)
    if facts:
        blocks.append(_facts(facts))
    for title, text in (
        ("Last step", card.handoff.last_step),
        ("Next step", card.handoff.next_step),
    ):
        if text and text.strip():
            blocks += [f"### {icons.step} {title}", _field(text)]
    if card.attachments:
        blocks += [
            f"### {icons.attachment} Attachments",
            _list([plain(a) for a in card.attachments]),
        ]
    if len(blocks) == 1:
        blocks.append(NOT_STARTED)
    return blocks


def _goal(card: Card, *, icons: Icons) -> list[str]:
    """The goal group: the goal, the limits besides the rules when there are any, and
    the test."""
    blocks = [_heading(icons.goal, "Goal"), _field(card.goal)]
    limits = _limits(card.limits)
    if limits:
        blocks += [f"### {icons.limits} Limits", _list(limits)]
    return blocks + [f"### {icons.test} Test", _field(card.test)]


def _decision(decision: Decision, *, icons: Icons) -> list[str]:
    """The decision a `decision_needed` card waits on, under one heading that says it
    waits on the human, in reading order: its `OPEN` or `ANSWERED` pill, the
    question, then the brief, the options ("other: tell me" last, even on a decision
    recorded before `card ask` appended it), the recommendation and the answer."""
    options = [o for o in decision.options if o != OTHER_OPTION] + [OTHER_OPTION]
    brief = _field(decision.brief) if decision.brief else f"*{plain(NO_BRIEF)}*"
    answer = (
        _field(decision.answer)
        if decision.answer
        else f"{icons.waiting} *{plain(ANSWER_HINT)}*"
    )
    return [
        _heading(icons.waiting, "Your decision"),
        _question_pill(answered=bool(decision.answer), icons=icons),
        "### Question",
        _field(decision.question),
        f"### {icons.brief} Brief",
        brief,
        "### Options",
        "\n".join(_item(o, f"{n}. ") for n, o in enumerate(options, 1)),
        f"### {icons.recommendation} Recommendation",
        _field(decision.recommendation or ""),
        "### Answer",
        answer,
    ]


def format_card(
    card: Card | None,
    *,
    now: datetime | None = None,
    details: bool = False,
    icons: Icons = PLAIN,
    working: bool = True,
) -> str:
    """One card's details as one Markdown text, the part of the pane under its first
    line (`card_line`): the groups of fields (module docstring): progress, goal,
    questions when it has any, history.
    `working` says whether a wake works the card's project now (`_progress_facts`).

    A `decision_needed` card with a decision shows that decision first
    (`_decision`), then `DETAILS_HINT` unless `details` is set, in which case the
    groups follow it. `None` (the empty-stack marker) renders a placeholder instead
    of a card.
    """
    if card is None:
        return plain(NO_SELECTION)
    now = now or datetime.now(UTC)
    blocks: list[str] = []
    if card.stack == Stack.DECISION_NEEDED and card.decisions:
        # the open one; the last one if a hand edit left none open
        waiting = next((d for d in card.decisions if d.answer is None), None)
        blocks += _decision(waiting or card.decisions[-1], icons=icons)
        if not details:
            blocks.append(f"*{plain(DETAILS_HINT)}*")
            return "\n\n".join(blocks) + "\n"
    blocks += _progress(card, now=now, icons=icons, working=working)
    blocks += _goal(card, icons=icons)
    if card.decisions:
        blocks += [
            _questions_heading(card.decisions, icons=icons),
            _questions(card.decisions, icons=icons),
        ]
    blocks += [_heading(icons.history, "History"), _history(card.history, icons=icons)]
    return "\n\n".join(blocks) + "\n"


class CardDetail(ReadingView):
    """The scrolling pane showing the currently focused card's details.

    Holds the details toggle: off for every card newly shown, kept while the same
    card is shown again (a live refresh redraws it with its fresh fields).

    Reached from the stacks by `Tab` and left by `Shift+Tab` or `Escape` — both
    call `BoardScreen`'s pane-focus toggle, so either one returns focus to
    whichever card or empty marker it left, not always the first. A heavy
    border shows while it holds focus (`board.py`'s `#detail:focus` rule), the
    same way a focused card's border does. `ReadingView` already scrolls it
    on the arrow keys, `PageUp`/`PageDown` and `Home`/`End`; `j`/`k` below add
    the vim-style pair for the same up/down scroll.

    Also carries its own `a`, bound here rather than only on the stacks'
    `BoardItem`: without it, a `decision_needed` card's open decision could be
    read from this pane but never answered from it, once `Tab` had moved focus
    here away from the card. `BoardScreen.action_answer_decision` and its
    `check_action` gate read `self.card` the same way whichever widget holds
    focus, so the dialog this opens, and the footer showing the key at all,
    are the same ones pressing `a` on the card itself would give.
    """

    DEFAULT_CSS = f"""
    CardDetail > CardLine {{
        width: 1fr;
        max-width: {COLUMN_WIDTH - 4};  /* the reading column's text, inside its padding */
        margin: 0 2;
        border-bottom: heavy $primary;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("a", "screen.answer_decision", "Answer", key_display="a"),
        Binding("escape", "screen.focus_pane_backward", "Board", key_display="esc"),
    ]

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(format_card(None), id=id, classes=classes)
        self.card: Card | None = None
        self.details = False
        # whether a wake works the shown card's project now; `BoardScreen` sets it
        self.working = True

    def compose(self) -> ComposeResult:
        yield CardLine(id="card-line")
        for widget in super().compose():
            if isinstance(widget, Markdown):
                widget.BLOCKS = _BLOCKS  # draws a question pill's paragraph as the pill
            yield widget

    def on_mount(self) -> None:
        self.query_one(CardLine).display = False
        self.set_interval(60, self._tick)

    def _tick(self) -> None:
        """Move the first line's times on by the minute gone by; nothing else redraws."""
        line = self.query_one(CardLine)
        if line.card is not None:
            line.show(line.card, now=datetime.now(UTC), working=line.working)

    def show(self, card: Card | None, *, now: datetime | None = None) -> None:
        """Render `card`'s first line and details, or the placeholder for `None`."""
        if card is None or self.card is None or card.id != self.card.id:
            self.details = False
        self.card = card
        now = now or datetime.now(UTC)
        icons = self.app.icons
        line = self.query_one(CardLine)
        line.display = card is not None
        if card is not None:
            line.show(card, now=now, working=self.working)
        self.update(
            format_card(
                card,
                now=now,
                details=self.details,
                icons=icons,
                working=self.working,
            )
        )

    def toggle_details(self) -> None:
        """Show or hide the worker fields behind a `decision_needed` card's
        decision."""
        self.details = not self.details
        self.show(self.card)
