"""format_card: the Markdown text the details pane renders, for every field it names;
and card_line: the pane's first line (sys-w115)."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from autonomouse.board.schema import (
    Assigned,
    Card,
    Decision,
    Handoff,
    HistoryEntry,
    Phase,
    Size,
    Stack,
)
from autonomouse.tui.card_detail import (
    ANSWER_HINT,
    DETAILS_HINT,
    NO_BRIEF,
    NO_SELECTION,
    NOT_STARTED,
    card_line,
    format_card,
)
from autonomouse.tui.icons import NERD, PLAIN
from autonomouse.tui.look import question_pill, size_badge, stack_pill

NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _card(**overrides) -> Card:
    base = {
        "id": "sys-x1",
        "project": "system",
        "title": "a title",
        "stack": Stack.TODO,
        "position": 0,
        "size": Size.S,
        "goal": "the goal text",
        "limits": ["docs/rules.md applies", "no touching the schema"],
        "test": "the test text",
        "depends_on": [],
        "assigned": Assigned(
            model=None,
            effort=None,
            agent_type=None,
            session_id=None,
            pid=None,
            picked_up_at=None,
        ),
        "worktree": None,
        "branch": None,
        "base_branch": None,
        "pr": None,
        "phase": None,
        "iteration": 0,
        "handoff": Handoff(
            last_step=None, next_step=None, updated_at=None, run_id=None
        ),
        "decisions": [],
        "history": [
            HistoryEntry(
                at="2026-09-22T06:00:00Z",
                run_id="seed",
                from_=None,
                to="todo",
                note="seeded",
            )
        ],
        "created_at": "2026-09-22T06:00:00Z",
        "updated_at": "2026-09-22T06:00:00Z",
    }
    base.update(overrides)
    return Card(**base)


def test_none_renders_the_placeholder():
    assert format_card(None) == NO_SELECTION


def test_a_fresh_cards_fields_are_all_present():
    card = _card()
    (row,) = card_line(card, width=120, now=NOW)
    assert card.id in row.plain
    assert card.title in row.plain
    text = format_card(card, now=NOW)
    assert card.goal in text
    assert "no touching the schema" in text
    assert card.test in text
    assert card.history[0].note in text
    assert card.history[0].to in text
    # a card no worker has touched says so once, in its progress group (sys-w105)
    assert NOT_STARTED in text


def test_notes_and_questions_show_their_content():
    card = _card(
        handoff=Handoff(
            last_step="wrote the panel",
            next_step="run the tests",
            updated_at="2026-09-22T07:00:00Z",
            run_id="run-1",
        ),
        decisions=[
            Decision(
                id="d1",
                question="which layout?",
                options=["side panel", "bottom panel"],
                recommendation="bottom panel",
                asked_at="2026-09-22T07:00:00Z",
                answer="bottom panel",
                answered_at="2026-09-22T07:05:00Z",
            )
        ],
    )
    text = format_card(card, now=NOW)
    assert "wrote the panel" in text
    assert "run the tests" in text
    assert "which layout?" in text
    assert "side panel" in text
    assert "bottom panel" in text


def test_empty_fields_get_no_heading():
    # an empty field is left out rather than shown as "None." (sys-w105)
    text = format_card(_card(), now=NOW)
    for heading in ("Attachments", "Last step", "Next step", "Questions"):
        assert heading not in text
    assert "None." not in text
    # the rules limit is never shown, so a card with only that one has no limits heading
    assert "Limits" not in format_card(_card(limits=["docs/rules.md applies"]), now=NOW)


def test_attachments_show_their_paths_and_urls():
    card = _card(attachments=["docs/screenshot.svg", "https://example.com/log.txt"])
    text = format_card(card, now=NOW)
    assert "docs/screenshot.svg" in text
    assert "https://example.com/log.txt" in text


def test_todo_card_shows_none_of_the_in_progress_facts():
    card = _card(stack=Stack.TODO)
    text = format_card(card, now=NOW)
    assert "**Model:**" not in text
    assert "**Reasoning:**" not in text
    assert "**Phase:**" not in text
    assert "**Iteration:**" not in text
    assert "**Picked up:**" not in text


def test_in_progress_card_shows_the_five_extra_facts():
    card = _card(
        stack=Stack.IN_PROGRESS,
        phase=Phase.TESTING,
        iteration=2,
        assigned=Assigned(
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            session_id="s1",
            pid=123,
            picked_up_at="2026-09-22T10:00:00Z",
        ),
    )
    text = format_card(card, now=NOW)
    assert f"**{PLAIN.model} Model:** sonnet" in text
    assert "**Reasoning:** high" in text
    assert f"**Phase:** {PLAIN.phase[Phase.TESTING]} testing" in text
    assert f"**{PLAIN.iteration} Iteration:** 2" in text
    # picked up 2 hours before NOW
    assert f"**{PLAIN.elapsed} Picked up:** 2h 0m ago" in text


def test_elapsed_time_since_pickup_is_unknown_when_not_yet_picked_up():
    card = _card(stack=Stack.IN_PROGRESS)
    text = format_card(card, now=NOW)
    assert f"**{PLAIN.elapsed} Picked up:** unknown" in text


def _decision_card(**decision_fields) -> Card:
    decision = {
        "id": "d1",
        "question": "which layout?",
        "options": ["side panel", "bottom panel", "other: tell me"],
        "recommendation": "bottom panel",
        "asked_at": "2026-09-22T07:00:00Z",
        "answer": None,
        "answered_at": None,
        "brief": "This card moves the detail panel. It is stuck on where to put it.",
    }
    decision.update(decision_fields)
    return _card(
        stack=Stack.DECISION_NEEDED,
        phase=Phase.REVIEW,
        iteration=2,
        branch="card/sys-x1",
        pr="https://github.com/test/system/pull/9",
        handoff=Handoff(
            last_step="a worker note",
            next_step="another worker note",
            updated_at=None,
            run_id=None,
        ),
        decisions=[Decision(**decision)],
    )


def test_a_decision_card_shows_its_decision_in_reading_order():
    text = format_card(_decision_card(), now=NOW)
    # the question first, as on Telegram (docs/rules.md rule 8), then the brief (sys-w105)
    order = [
        text.index(f"## {PLAIN.waiting} Your decision"),
        text.index("### Question"),
        text.index("which layout?"),
        text.index(f"### {PLAIN.brief} Brief"),
        text.index("This card moves the detail panel."),
        text.index("### Options"),
        text.index("1. side panel"),
        text.index("2. bottom panel"),
        text.index("3. other: tell me"),
        text.index(f"### {PLAIN.recommendation} Recommendation"),
        text.index("### Answer"),
        text.index(ANSWER_HINT),
        text.index(DETAILS_HINT),
    ]
    assert order == sorted(order)


def test_a_decision_card_hides_worker_fields_until_details():
    card = _decision_card()
    hidden = [
        "no touching the schema",
        "a worker note",
        "another worker note",
        "card/sys-x1",
        "pull/9",
        "Iteration",
        "the goal text",
    ]
    brief = format_card(card, now=NOW)
    for field in hidden:
        assert field not in brief
    full = format_card(card, now=NOW, details=True)
    for field in hidden:
        assert field in full
    assert DETAILS_HINT not in full
    assert full.index("This card moves the detail panel.") < full.index(
        f"## {PLAIN.goal} Goal"
    )


def test_other_is_listed_last_even_when_the_decision_lacks_it():
    card = _decision_card(options=["side panel"])
    text = format_card(card, now=NOW)
    assert "1. side panel" in text
    assert "2. other: tell me" in text


def test_a_decision_without_a_brief_says_so():
    text = format_card(_decision_card(brief=None), now=NOW)
    assert NO_BRIEF in text
    assert "a worker note" not in text


def test_the_rules_limit_is_never_shown():
    for card in (_card(), _decision_card()):
        for details in (False, True):
            text = format_card(card, now=NOW, details=details)
            assert "docs/rules.md applies" not in text
    assert "no touching the schema" in format_card(_card(), now=NOW)


# ------------------------------------------------------------ sys-w115 --

FORMS = {"plain": PLAIN, "nerd": NERD}


def _worked_card(**overrides) -> Card:
    """An in_progress card with every field the pane gives an icon to."""
    fields = {
        "stack": Stack.IN_PROGRESS,
        "phase": Phase.TESTING,
        "iteration": 2,
        "assigned": Assigned(
            model="sonnet",
            effort="high",
            agent_type="worker-high",
            session_id="s1",
            pid=123,
            picked_up_at="2026-09-22T10:00:00Z",
        ),
        "handoff": Handoff(
            last_step="wrote the panel",
            next_step="run the tests",
            updated_at="2026-09-22T07:00:00Z",
            run_id="run-1",
        ),
        "attachments": ["docs/screenshot.svg"],
    }
    fields.update(overrides)
    return _card(**fields)


@pytest.mark.parametrize("form", FORMS)
def test_each_heading_and_fact_carries_its_icon(form):
    icons = FORMS[form]
    text = format_card(_worked_card(), now=NOW, icons=icons)
    for line in (
        f"### {icons.limits} Limits",
        f"### {icons.test} Test",
        f"### {icons.attachment} Attachments",
        f"### {icons.step} Last step",
        f"### {icons.step} Next step",
        f"**{icons.model} Model:** sonnet",
        f"**{icons.iteration} Iteration:** 2",
        f"**{icons.elapsed} Picked up:** 2h 0m ago",
    ):
        assert line in text.splitlines() or f"- {line}" in text.splitlines(), line
    decision = format_card(_decision_card(), now=NOW, icons=icons)
    assert f"### {icons.brief} Brief" in decision.splitlines()
    assert f"### {icons.recommendation} Recommendation" in decision.splitlines()


@pytest.mark.parametrize("form", FORMS)
def test_every_question_opens_with_its_open_or_answered_pill(form):
    icons = FORMS[form]
    open_pill = question_pill(answered=False, icons=icons).plain.strip()
    answered_pill = question_pill(answered=True, icons=icons).plain.strip()
    answered = Decision(
        id="d0",
        question="which colour?",
        options=["red", "blue"],
        recommendation="red",
        asked_at="2026-09-22T06:30:00Z",
        answer="blue",
        answered_at="2026-09-22T06:40:00Z",
    )
    card = _decision_card()
    card = replace(card, decisions=[answered, *card.decisions])
    text = format_card(card, now=NOW, icons=icons, details=True)
    lines = text.splitlines()
    # the decision the card waits on opens with OPEN, under its heading
    assert lines.index(open_pill) == lines.index(f"## {icons.waiting} Your decision") + 2
    # in the questions group each question's item opens with its own pill
    assert f"- {answered_pill}" in lines
    assert f"- {open_pill}" in lines
    assert text.index(f"- {answered_pill}") < text.index("which colour?")
    assert "**Answer:** blue" in text


@pytest.mark.parametrize(
    ("stack", "extra", "word"),
    [
        (Stack.TODO, {}, "TODO"),
        (Stack.IN_PROGRESS, {"phase": Phase.TESTING}, "TESTING"),
        (Stack.IN_PROGRESS, {}, "STARTING"),
        (Stack.DONE, {}, "DONE"),
        (Stack.DECISION_NEEDED, {}, "DECISION NEEDED"),
    ],
)
@pytest.mark.parametrize("form", FORMS)
def test_the_first_line_holds_id_title_state_pill_size_badge_and_both_times(
    form, stack, extra, word
):
    icons = FORMS[form]
    card = _card(
        stack=stack,
        size=Size.M,
        created_at="2026-09-22T11:57:00Z",
        updated_at="2026-09-22T10:30:00Z",
        **extra,
    )
    (row,) = card_line(card, width=100, now=NOW, icons=icons)
    text = row.plain
    assert row.cell_length == 100
    assert text.startswith(f"{icons.card} sys-x1  a title  ")
    badge = size_badge(Size.M, icons=icons).plain
    assert f" {word} " in text
    assert text.index(f" {word} ") < text.index(badge)
    # the times close the row, at its right
    assert text.endswith(f"  {icons.elapsed} created 3m ago | updated 1h 30m ago")
    if word in ("TODO", "DONE", "DECISION NEEDED"):
        assert stack_pill(stack, icons=icons).plain in text


def test_an_in_progress_card_with_no_wake_says_stopped_in_its_pill():
    card = _card(stack=Stack.IN_PROGRESS, phase=Phase.TESTING)
    (row,) = card_line(card, width=100, now=NOW, working=False)
    assert f" {PLAIN.stopped} STOPPED " in row.plain
    assert "TESTING" not in row.plain


LONG = (
    "The card details pane and the decision dialogs show pills, a size badge and an "
    "icon on every heading"
)


@pytest.mark.parametrize("form", FORMS)
def test_a_long_title_flows_on_and_no_pill_is_broken(form):
    icons = FORMS[form]
    card = _card(title=LONG, stack=Stack.DONE)
    rows = card_line(card, width=84, now=NOW, icons=icons)
    assert len(rows) > 1
    assert all(r.cell_length <= 84 for r in rows)
    texts = [r.plain for r in rows]
    # the first row starts with the id and ends with the times
    assert texts[0].startswith(f"{icons.card} sys-x1  The card")
    assert texts[0].endswith("updated 6h 0m ago")
    # the title reads whole across the rows, and the pill and the badge sit together
    state = f"{stack_pill(Stack.DONE, icons=icons).plain} {size_badge(Size.S, icons=icons).plain}"
    assert sum(state in t for t in texts) == 1
    times = f"{icons.elapsed} created 6h 0m ago | updated 6h 0m ago"
    words = " ".join(texts).replace(state, "").replace(times, "").split()
    assert " ".join(words[2:]) == LONG


def test_a_narrow_pane_puts_the_times_on_a_row_of_their_own():
    rows = card_line(_card(), width=50, now=NOW)
    assert "created" not in rows[0].plain
    assert rows[-1].plain.strip() == f"{PLAIN.elapsed} created 6h 0m ago | updated 6h 0m ago"
    assert rows[-1].cell_length == 50  # at the right
