"""The viewer's looks: colour always comes with a symbol and a word, and empty stacks
say what their emptiness means; every state a pill shows has its pill, which reads in
both themes and both icon forms; and the frame style reads in both themes. The project
header has its own tests (`test_tui_board_header.py`). The frame's focus colour differs
clearly from every stack's colour in both themes, so the chosen card stands out in every
column."""

from collections.abc import Callable

import pytest
from textual.app import App
from textual.color import Color, rgb_to_lab
from textual.content import Content
from textual.theme import BUILTIN_THEMES
from textual.widgets import Static

from autonomouse.board.plans import ThreadStatus
from autonomouse.board.schema import Size, Stack
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import STACK_ORDER, BoardItem
from autonomouse.tui.icons import NERD, PLAIN, Icons
from autonomouse.tui.look import (
    DECISION,
    FRAME,
    NEUTRAL,
    PROJECT_STATUS_COLOUR,
    QUESTION_COLOUR,
    STACK_LOOK,
    THREAD_COLOUR,
    THREAD_WORD,
    ProjectStatus,
    project_status_pill,
    question_pill,
    size_badge,
    stack_label,
    stack_pill,
    theme_variables,
    thread_pill,
)
from test_tui_board import CARDS, _open_system_board, board  # noqa: F401
from test_tui_icons import private_use


def test_every_stack_has_a_distinct_symbol_and_colour():
    assert set(STACK_LOOK) == set(Stack)
    for icons in (PLAIN, NERD):
        assert set(icons.stack) == set(Stack)
        assert len(set(icons.stack.values())) == len(Stack)
    assert len({look.colour for look in STACK_LOOK.values()}) == len(Stack)


async def test_titles_and_cards_carry_their_stacks_symbol_and_word(board):  # noqa: F811
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        for stack in STACK_ORDER:
            column = screen.query_one(f"#stack-{stack.value}")
            title = str(column.query_one(".stack-title", Static).content)
            assert (
                title == f"{PLAIN.stack[stack]} {stack_label(stack)} {len(CARDS[stack.value])}"
            )
            for item in column.query(BoardItem):
                if item.card is not None:
                    assert str(item.border_title) == f"{PLAIN.stack[stack]} {item.card.id}"


async def test_an_empty_stack_says_what_its_emptiness_means(board):  # noqa: F811
    app = AutonomouseApp()
    async with app.run_test(size=(240, 40)) as pilot:
        screen = await _open_system_board(pilot)
        empty = screen.query_one("#stack-cancelled BoardItem.empty", BoardItem)
        title = empty.query_one("#card-title", Static)
        assert str(title.content) == STACK_LOOK[Stack.CANCELLED].empty


# ------------------------------------------------------------------------ pills --

THEMES = [DARK_THEME, LIGHT_THEME]
FORMS = {"plain": PLAIN, "nerd": NERD}


def _pills() -> list[
    tuple[str, Callable[[Icons], Content], Callable[[Icons], str], str, str]
]:
    """Every state a pill shows, and a size: its name, its pill, its icon, its word and
    its colour."""
    pills = []
    for stack in Stack:
        pills.append(
            (
                f"stack {stack.value}",
                lambda i, s=stack: stack_pill(s, icons=i),
                lambda i, s=stack: i.stack[s],
                stack_label(stack),
                STACK_LOOK[stack].colour,
            )
        )
    status_icon = {
        ProjectStatus.ACTIVE: lambda i: i.wake,
        ProjectStatus.PAUSED: lambda i: i.paused,
        ProjectStatus.STOPPED: lambda i: i.stopped,
        ProjectStatus.DONE: lambda i: i.stack[Stack.DONE],
        ProjectStatus.WAITING: lambda i: i.clock,
    }
    for status in ProjectStatus:
        pills.append(
            (
                f"project {status.value}",
                lambda i, s=status: project_status_pill(s, icons=i),
                status_icon[status],
                status.value,
                PROJECT_STATUS_COLOUR[status],
            )
        )
    for answered, word, icon in (
        (False, "open", "waiting"),
        (True, "answered", "passed"),
    ):
        pills.append(
            (
                f"question {word}",
                lambda i, a=answered: question_pill(answered=a, icons=i),
                lambda i, n=icon: getattr(i, n),
                word,
                QUESTION_COLOUR[answered],
            )
        )
    for status in ThreadStatus:
        pills.append(
            (
                f"thread {status.value}",
                lambda i, s=status: thread_pill(s, icons=i),
                lambda i, s=status: i.thread_status[s],
                THREAD_WORD[status],
                THREAD_COLOUR[status],
            )
        )
    for size in Size:
        pills.append(
            (
                f"size {size.value}",
                lambda i, s=size: size_badge(s, icons=i),
                lambda i: i.size,
                size.value,
                NEUTRAL,
            )
        )
    return pills


PILLS = _pills()


def _luminance(colour: Color) -> float:
    """WCAG 2 relative luminance."""

    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.r)
        + 0.7152 * channel(colour.g)
        + 0.0722 * channel(colour.b)
    )


def contrast(a: Color, b: Color) -> float:
    """WCAG 2 contrast ratio of two opaque colours, 1 to 21."""
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def resolve(theme: str, variable: str, over: str = "background") -> Color:
    """The opaque colour `variable` (`$success`) is in `theme`, blended over the theme's
    `over` colour when it has an alpha, as Textual draws it. The viewer's own
    variables (`look.theme_variables`, such as `$frame-focus`) resolve too, as the app
    adds them."""
    built_in = BUILTIN_THEMES[theme]
    values = {
        **theme_variables(dark=built_in.dark),
        **built_in.to_color_system().generate(),
    }
    colour = Color.parse(values[variable.removeprefix("$")])
    return Color.parse(values[over]) + colour if colour.a < 1 else colour


class PillApp(App):
    def __init__(self, theme: str, icons: Icons):
        super().__init__()
        self.pill_theme, self.pill_icons = theme, icons

    def compose(self):
        for _name, build, *_ in PILLS:
            yield Static(build(self.pill_icons))

    def on_mount(self) -> None:
        self.theme = self.pill_theme


def test_contrast_follows_the_wcag_formula():
    assert contrast(Color(0, 0, 0), Color(255, 255, 255)) == pytest.approx(21)
    assert contrast(Color(118, 118, 118), Color(255, 255, 255)) == pytest.approx(
        4.54, abs=0.01
    )


def test_every_state_named_has_a_pill_and_waiting_on_you_is_the_decision_colour():
    assert DECISION == STACK_LOOK[Stack.DECISION_NEEDED].colour
    assert QUESTION_COLOUR[False] == DECISION
    assert THREAD_COLOUR[ThreadStatus.WAITING_HUMAN] == DECISION
    assert {s.value for s in ProjectStatus} == {
        "active",
        "stopped",
        "paused",
        "done",
        "waiting",
    }
    assert len(set(PROJECT_STATUS_COLOUR.values())) == len(ProjectStatus)
    assert set(THREAD_WORD.values()) == {
        "waiting on you",
        "planner working",
        "live",
        "closed",
    }
    turns = {THREAD_WORD[s]: THREAD_COLOUR[s] for s in ThreadStatus}
    assert len(set(turns.values())) == len(turns)


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("theme", THEMES)
async def test_each_pill_holds_its_icon_and_word_in_capitals_on_its_colour_and_reads(
    theme, form, tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    icons = FORMS[form]
    left, right = icons.pill_ends
    app = PillApp(theme, icons)
    async with app.run_test(size=(60, len(PILLS) + 2)) as pilot:
        await pilot.pause()
        assert app.theme == theme
        for static, (name, _build, icon, word, colour) in zip(
            app.query(Static), PILLS, strict=True
        ):
            cells = [
                (char, segment.style)
                for segment in static.render_line(0)
                for char in segment.text
            ]
            text = "".join(char for char, _ in cells)
            body = f" {icon(icons)} {word.upper()} "
            assert text.startswith(left + body + right), (name, text)
            fill = resolve(theme, colour)
            for char, style in cells[len(left) : len(left) + len(body)]:
                background = Color.from_rich_color(style.bgcolor)
                assert background == fill, (name, char, background, fill)
                ink = Color.from_rich_color(style.color)
                assert contrast(ink, fill) >= 4.5, (name, theme, ink.hex, fill.hex)
            if left:  # the rounded ends are drawn in the pill's own colour
                for _char, style in (cells[0], cells[len(left) + len(body)]):
                    assert Color.from_rich_color(style.color) == fill, name
            if icons is PLAIN:
                assert not private_use(text), name
            else:
                assert private_use(left + right) == sorted(left + right), name


# ----------------------------------------------------------------------- frames --


@pytest.mark.parametrize("theme", THEMES)
def test_the_frame_is_heavy_and_its_rest_and_focus_colours_read_in_both_themes(theme):
    assert FRAME.border == "heavy"
    assert FRAME.rest == f"heavy {FRAME.colour}"
    assert FRAME.focused == f"heavy {FRAME.focus}"
    assert FRAME.colour != FRAME.focus
    for under in ("background", "surface", "panel"):
        ground = resolve(theme, f"${under}")
        for colour in (FRAME.colour, FRAME.focus):
            # WCAG's 3:1 for the parts of a screen that are not text
            assert contrast(resolve(theme, colour, over=under), ground) >= 3, (
                under,
                colour,
            )


def colour_distance(a: Color, b: Color) -> float:
    """CIE76 colour difference (Delta E): the straight-line distance of two opaque
    colours in CIELAB, where about 2.3 is just noticeable, 0 is the same colour and 100
    is black against white."""
    la, lb = rgb_to_lab(a), rgb_to_lab(b)
    return ((la.L - lb.L) ** 2 + (la.a - lb.a) ** 2 + (la.b - lb.b) ** 2) ** 0.5


# How far apart the focus colour and each stack's colour must be. Main's focus colour
# before sys-w125, `$text-primary`, stood 26 from the in_progress blue on the dark theme
# and 14 on the light one: a lighter shade of the same blue. 40 asks for a colour of its
# own, not a shade of a stack's.
MIN_FOCUS_DISTANCE = 40


def test_colour_distance_is_cie76():
    assert colour_distance(Color(0, 0, 0), Color(255, 255, 255)) == pytest.approx(
        100, abs=0.1
    )
    assert colour_distance(Color(1, 120, 212), Color(1, 120, 212)) == 0


@pytest.mark.parametrize("theme", THEMES)
def test_the_focus_colour_differs_clearly_from_every_stacks_colour(theme):
    focus = resolve(theme, FRAME.focus)
    for stack, look in STACK_LOOK.items():
        colour = resolve(theme, look.colour)
        distance = colour_distance(focus, colour)
        assert distance >= MIN_FOCUS_DISTANCE, (
            theme,
            stack.value,
            focus.hex,
            colour.hex,
            round(distance, 1),
        )


@pytest.mark.parametrize("theme", THEMES)
async def test_the_app_draws_the_focus_colour_of_the_theme_on_screen(
    theme, tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path))
    app = AutonomouseApp()
    async with app.run_test(size=(120, 30)) as pilot:
        # the app opens dark; a switch of theme re-reads the viewer's variables
        app.theme = LIGHT_THEME
        await pilot.pause()
        app.theme = theme
        await pilot.pause()
        variable = FRAME.focus.removeprefix("$")
        assert Color.parse(app.theme_variables[variable]) == resolve(theme, FRAME.focus)
