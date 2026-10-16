"""The viewer's framed footer (`autonomouse.tui.footer.AppFooter`, sys-w112): the same key
hints as Textual's stock `Footer`, the command palette key after them, and the slug and
the autonomouse version at the far right, inside sys-w110's frame. No screen uses it
yet; each screen switches to it in its own card, so the legend helpers in
`tui_wait.py` must read both footers alike."""

import io
from typing import ClassVar

import pytest
from rich.console import Console
from textual.app import App
from textual.binding import Binding, BindingType
from textual.color import Color
from textual.widgets import Footer, Static
from textual.widgets._footer import FooterKey, FooterLabel

import autonomouse
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME
from autonomouse.tui.footer import AppFooter
from autonomouse.tui.look import FRAME
from test_tui_icons import private_use
from tui_wait import legend, legend_shows, pause_until

VERSION = f"autonomouse v{autonomouse.__version__}"
PALETTE = "ctrl+p"
# The keys a stock footer shows for `KeysApp`: every bound key but `a`, whose action
# `check_action` refuses, and the palette key. `d`, which `check_action` greys out
# (returns None), is shown, dim.
# `left` and `right` share one legend entry, as the board's arrow keys do.
SHOWN = frozenset({"left", "right", "q", "o", "d", "r", PALETTE})
MOVE = Binding.Group("Move", compact=True)


class KeysApp(App):
    """A small app with key bindings, two in a group, one refused and one greyed out,
    and a footer."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("left", "mark('left')", "Left", key_display="←", group=MOVE),
        Binding("right", "mark('right')", "Right", key_display="→", group=MOVE),
        Binding("q", "quit", "Quit"),
        Binding("o", "open", "Open card"),
        Binding("a", "approve", "Approve"),
        Binding("d", "details", "Details"),
        Binding("r", "mark('refresh')", "Refresh"),
        Binding("x", "mark('hidden')", "Hidden", show=False),
    ]

    def __init__(self, footer, theme: str = DARK_THEME):
        super().__init__()
        self.make_footer = footer
        self.start_theme = theme
        self.ran: list[str] = []

    def compose(self):
        yield Static("body")
        yield self.make_footer()

    def on_mount(self) -> None:
        self.theme = self.start_theme

    def check_action(self, action: str, parameters) -> bool | None:
        if action == "approve":
            return False
        if action == "details":
            return None
        return True

    def action_open(self) -> None:
        self.ran.append("open")

    def action_mark(self, what: str) -> None:
        self.ran.append(what)


def _screen_lines(app: App) -> list[str]:
    """The screen as the terminal shows it, one string per row."""
    width, height = app.size
    console = Console(
        width=width,
        height=height,
        file=io.StringIO(),
        record=True,
        legacy_windows=False,
    )
    console.print(app.screen._compositor.render_update(full=True))
    return console.export_text().splitlines()


async def _keys(pilot) -> dict[str, FooterKey]:
    """The footer's key hints by key, once the legend has drawn."""
    await legend_shows(pilot, lambda keys: keys == SHOWN)
    return {key.key: key for key in pilot.app.screen.query(FooterKey)}


@pytest.mark.parametrize("make_footer", [Footer, AppFooter], ids=["stock", "app"])
async def test_the_legend_helpers_read_both_footers_alike(make_footer):
    app = KeysApp(make_footer)
    async with app.run_test(size=(140, 12)) as pilot:
        keys = await legend_shows(pilot, lambda keys: keys == SHOWN)
        assert keys == SHOWN
        assert legend(app.screen) == (SHOWN, ())


async def test_the_footer_shows_the_stock_footers_keys_the_same_way():
    shown = {}
    for make_footer in (Footer, AppFooter):
        app = KeysApp(make_footer)
        async with app.run_test(size=(140, 12)) as pilot:
            keys = await _keys(pilot)
            shown[make_footer] = (
                {
                    key: (hint.description, hint.has_class("-disabled"))
                    for key, hint in keys.items()
                },
                [str(label.content) for label in app.screen.query(FooterLabel)],
            )
    assert shown[AppFooter] == shown[Footer]
    hints, groups = shown[AppFooter]
    assert "a" not in hints  # check_action refused it
    assert hints["d"] == ("Details", True)  # greyed out, still shown
    assert groups == ["Move"]


@pytest.mark.parametrize("theme", [DARK_THEME, LIGHT_THEME])
async def test_the_footer_reads_keys_then_palette_then_slug_and_version_in_a_frame(
    theme,
):
    app = KeysApp(lambda: AppFooter(slug="system"), theme)
    async with app.run_test(size=(140, 12)) as pilot:
        keys = await _keys(pilot)
        footer = app.screen.query_one(AppFooter)
        about = footer.query_one(".footer-about", Static)

        # in the frame: a heavy border all round, in the frame's resting colour
        rest = Color.parse(app.get_css_variables()[FRAME.colour.removeprefix("$")])
        for edge in ("top", "right", "bottom", "left"):
            kind, colour = getattr(footer.styles, f"border_{edge}")
            assert kind == FRAME.border
            assert colour == rest
        assert footer.region.height == 3
        lines = _screen_lines(app)
        assert lines[-3].startswith("┏") and lines[-3].endswith("┓")
        assert lines[-1].startswith("┗") and lines[-1].endswith("┛")

        # the palette key follows the other keys, then the label fills to the right
        palette = keys[PALETTE]
        others = [hint for key, hint in keys.items() if key != PALETTE]
        assert all(hint.region.right <= palette.region.x for hint in others)
        assert about.region.x >= palette.region.right
        assert about.region.right == footer.content_region.right

        row = lines[-2]
        assert row.rstrip(" ┃").endswith(f"system   {VERSION}")
        assert row.index("Quit") < row.index("palette") < row.index("system")
        assert not private_use("\n".join(lines))


async def test_with_no_slug_the_footer_shows_the_version_alone():
    app = KeysApp(AppFooter)
    async with app.run_test(size=(140, 12)) as pilot:
        await _keys(pilot)
        row = _screen_lines(app)[-2]
        assert row.rstrip(" ┃").endswith(VERSION)
        assert "system" not in row


@pytest.mark.parametrize("width", [80, 60, 40])
async def test_on_a_narrow_terminal_the_keys_keep_their_room(width):
    """The label is cut, never a key: each key hint is as wide as on a wide terminal
    until the terminal is too narrow for the keys themselves, where the label has no
    width at all and the keys scroll as the stock footer's do."""
    app = KeysApp(lambda: AppFooter(slug="system"))
    async with app.run_test(size=(140, 12)) as pilot:
        wide = {key: hint.region.width for key, hint in (await _keys(pilot)).items()}

    app = KeysApp(lambda: AppFooter(slug="system"))
    async with app.run_test(size=(width, 12)) as pilot:
        # the legend is laid out once every key has a width; `legend_shows` would
        # wait for every key to be in view, which a narrow terminal never gives
        hints = await pause_until(
            pilot,
            lambda: {key.key: key for key in app.screen.query(FooterKey)},
            lambda hints: (
                set(hints) == SHOWN
                and all(hint.region.width for hint in hints.values())
            ),
        )
        about = app.screen.query_one(".footer-about", Static)
        assert {key: hint.region.width for key, hint in hints.items()} == wide
        keys_end = max(hint.region.right for hint in hints.values())
        inside = app.screen.query_one(AppFooter).content_region
        # the label shows only in the room the keys leave, and nowhere on a terminal
        # too narrow for the keys
        in_view = about.region.intersection(inside).width
        assert in_view == max(0, inside.right - keys_end)
        row = _screen_lines(app)[-2]
        assert VERSION not in row  # the version is cut first, then the slug
        assert ("system" in row) == (width == 80)


@pytest.mark.parametrize("make_footer", [Footer, AppFooter], ids=["stock", "app"])
async def test_a_click_on_a_key_hint_runs_its_action(make_footer):
    app = KeysApp(make_footer)
    async with app.run_test(size=(140, 12)) as pilot:
        keys = await _keys(pilot)
        await pilot.click(keys["o"])
        await pause_until(pilot, lambda: list(app.ran), lambda ran: ran == ["open"])
        await pilot.click(keys["r"])
        await pause_until(
            pilot, lambda: list(app.ran), lambda ran: ran == ["open", "refresh"]
        )
