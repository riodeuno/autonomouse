"""Modal screens for answering and cancelling a `decision_needed` card.

Neither modal writes a card file. Each is a plain result-producing dialog: it
gathers an answer (and whether the card needs more work or is already finished) or
a cancel reason from the human and `dismiss()`es with it (or with `None` on
escape). `BoardScreen` is the only thing that reads that result and
calls `board_commands.card_answer` / `card_cancel` — the sanctioned, atomic write
path (`board/store.py`). Keeping the write out of the modal means the modal's own
tests can drive it to completion without touching a board at all, and the "the
screen never writes a file itself" limit stays true by construction: there is
nowhere in this file a write could hide.

`AnswerModal` shows the decision as a document, in the reading view every screen that
shows text to read uses (`autonomouse.tui.reading.ReadingView`): the question first,
because it is what the human has to answer, as the question heads the same decision on
Telegram (`docs/rules.md` item 8, "How a message to the human is laid out"); then the
asking agent's brief, which item 8 asks of everything a `decision_needed` card shows,
and the recommendation, each under its own heading (`format_decision`). The options
sit right below the document, next to the recommendation.

The icons (`autonomouse.tui.icons`, in the form the app holds) mark the decision's
title as waiting on the human, the brief and the recommendation, each place the card
can go by its stack, and the cancel prompt by the cancelled stack. Above the document
the question's pill (`autonomouse.tui.look.question_pill`) says `OPEN`, and `ANSWERED`
once the human has given the answer and is asked where the card goes.

Both dialogs sit in the frame every panel of the viewer has (`autonomouse.tui.look.
FRAME`), in its focus colour: a dialog holds the focus for as long as it is open.
"""

from __future__ import annotations

from typing import ClassVar, NamedTuple

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from autonomouse.board.schema import OTHER_OPTION, Decision, Stack
from autonomouse.tui.icons import PLAIN, Icons
from autonomouse.tui.look import FRAME, question_pill
from autonomouse.tui.reading import COLUMN_WIDTH, ReadingView, plain

NO_BRIEF = "No brief: this question was asked before every question carried one."


def format_decision(card_id: str, decision: Decision, *, icons: Icons = PLAIN) -> str:
    """The decision as one Markdown text: a title naming the card, the question, the
    brief and the recommendation when there is one, the last two after their icons.
    The brief is Markdown an agent wrote and goes in as it is; the rest goes through
    `plain`, so it shows as written."""
    brief = decision.brief.strip() if decision.brief else ""
    blocks = [
        f"# {icons.waiting} {plain(card_id)} needs your decision",
        "## Question",
        plain(decision.question),
        f"## {icons.brief} Brief",
        brief if brief else f"*{plain(NO_BRIEF)}*",
    ]
    if decision.recommendation:
        blocks += [
            f"## {icons.recommendation} Recommendation",
            plain(decision.recommendation),
        ]
    return "\n\n".join(blocks) + "\n"


class Answer(NamedTuple):
    """What `AnswerModal` dismisses with: the answer, and whether the work it
    answers is already finished — `board_commands.card_answer`'s `done`, which
    sends the card to `done` instead of the top of `todo`."""

    text: str
    done: bool


class AnswerModal(ModalScreen[Answer | None]):
    """Show a card's open decision and its options; dismiss with the answer and
    where the card goes next.

    Two steps. First the human picks an option — or, for `OTHER`
    (`docs/rules.md` item 8's fixed last option, "so the human can answer in
    their own words", always listed last even on a decision recorded without
    it), types the answer into a text field instead: the human's
    own words are the answer, not the option's placeholder text, and an empty
    submit holds the modal open rather than write a value the human never
    typed (`CancelModal` below has the same shape). Then the modal shows the
    answer and asks where the card goes, naming the stack in each choice before
    the human confirms: `MORE_WORK` (first, so enter alone takes it — the
    default) or `FINISHED`. Escape at either step dismisses with `None`, which
    the caller treats as "back out, write nothing".
    """

    OTHER = OTHER_OPTION
    MORE_WORK = "more work needed — the card goes to the top of todo"
    FINISHED = "the work is already finished — the card goes to done"

    DEFAULT_CSS = f"""
    AnswerModal {{
        align: center middle;
    }}
    AnswerModal > Vertical {{
        width: {COLUMN_WIDTH + 3};  /* the reading column, the border, the scroll bar */
        max-width: 95%;
        height: auto;
        max-height: 90%;
        border: {FRAME.focused};
        background: $surface;
        padding: 0 0 1 0;
    }}
    AnswerModal #state {{
        margin: 1 2 0 2;
    }}
    AnswerModal ReadingView {{
        height: auto;
        max-height: 60vh;
    }}
    AnswerModal OptionList {{
        height: auto;
        max-height: 10;
        margin: 1 2 0 2;
    }}
    AnswerModal Input, AnswerModal #chosen, AnswerModal .hint {{
        margin: 1 2 0 2;
    }}
    AnswerModal .hint {{
        color: $text-muted;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_none", "Back", key_display="esc"),
    ]

    def __init__(self, card_id: str, decision: Decision) -> None:
        super().__init__()
        self.card_id = card_id
        self.decision = decision
        # `card ask` appends OTHER to every decision; one recorded before it did may
        # lack it, and still gets the free-text answer here
        self.options = [o for o in decision.options if o != self.OTHER] + [self.OTHER]
        self.answer = ""  # set by `_show_landing`, the first step's end

    def compose(self) -> ComposeResult:
        icons = self.app.icons
        with Vertical():
            yield Static(question_pill(answered=False, icons=icons), id="state")
            yield ReadingView(
                format_decision(self.card_id, self.decision, icons=icons), id="question"
            )
            yield OptionList(*self.options, id="options")
            yield Input(placeholder="type your answer")
            # markup off: the answer is the human's own text, shown as typed
            yield Static("", id="chosen", markup=False)
            yield OptionList(
                f"{icons.stack[Stack.TODO]} {self.MORE_WORK}",
                f"{icons.stack[Stack.DONE]} {self.FINISHED}",
                id="landing",
            )
            yield Static("enter: choose   esc: back", classes="hint")

    def on_mount(self) -> None:
        self.query_one(Input).display = False
        self.query_one("#chosen", Static).display = False
        self.query_one("#landing", OptionList).display = False
        self.query_one("#options", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "landing":
            # index 1 is FINISHED, index 0 MORE_WORK
            self.dismiss(Answer(self.answer, done=event.option_index == 1))
            return
        # index, not `event.option.prompt`, so this doesn't depend on however
        # OptionList wraps a plain string into its internal `Option` type
        option = self.options[event.option_index]
        if option == self.OTHER:
            self._show_other_field()
            return
        self._show_landing(option)

    def _show_other_field(self) -> None:
        """Hide the option list, reveal the text field, and move focus to it —
        `on_input_submitted` below is the only way on from here."""
        self.query_one("#options", OptionList).display = False
        self.query_one(".hint", Static).update("enter: confirm   esc: back")
        text_field = self.query_one(Input)
        text_field.display = True
        text_field.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        answer = event.value.strip()
        if answer:
            self._show_landing(answer)
        # empty answer: stay open, `card_answer` would refuse it anyway (see
        # `CancelModal.on_input_submitted` below for the same shape)

    def _show_landing(self, answer: str) -> None:
        """Second step: keep the answer, mark the question answered, hide the first
        step's widgets, and ask where the card goes, with `MORE_WORK` highlighted."""
        self.answer = answer
        self.query_one("#state", Static).update(
            question_pill(answered=True, icons=self.app.icons)
        )
        self.query_one("#options", OptionList).display = False
        self.query_one(Input).display = False
        chosen = self.query_one("#chosen", Static)
        chosen.update(f"Answer: {answer}\nWhere does {self.card_id} go now?")
        chosen.display = True
        landing = self.query_one("#landing", OptionList)
        landing.display = True
        landing.highlighted = 0
        landing.focus()
        self.query_one(".hint", Static).update("enter: confirm   esc: back")

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class CancelModal(ModalScreen[str | None]):
    """Prompt for a cancel reason; dismiss with it once non-empty, or `None` on escape.

    `card_cancel` refuses an empty reason, so this holds the modal open on an empty
    submit rather than dismissing with one the caller would just have to reject.
    """

    DEFAULT_CSS = f"""
    CancelModal {{
        align: center middle;
    }}
    CancelModal > Vertical {{
        width: 60;
        height: auto;
        border: {FRAME.focused};
        background: $surface;
        padding: 1 2;
    }}
    CancelModal Input {{
        margin-top: 1;
    }}
    CancelModal .hint {{
        color: $text-muted;
        margin-top: 1;
    }}
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_none", "Back", key_display="esc"),
    ]

    def __init__(self, card_id: str) -> None:
        super().__init__()
        self.card_id = card_id

    def compose(self) -> ComposeResult:
        with Vertical():
            cancelled = self.app.icons.stack[Stack.CANCELLED]
            yield Static(f"{cancelled} Cancel {self.card_id} — reason:")
            yield Input(placeholder="reason")
            yield Static("enter: cancel   esc: back", classes="hint")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        reason = event.value.strip()
        if reason:
            self.dismiss(reason)
        # empty reason: stay open, `card_cancel` would refuse it anyway

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
