"""The autonomouse art, and the header every screen of the viewer shows in place of
Textual's one-line title bar.

The art ships inside the package, in `assets/` next to this file, so a wheel install
has it: the eight animation frames the splash plays (`FRAMES`, frame 00 to 07). Every
file is ANSI text: Unicode quadrant blocks, each in a 24-bit colour, with the neutral
strokes pure white.

`AppHeader` is the header, and it shows no logo: the art belongs to the splash alone
(`autonomouse.tui.splash`). It is a box in the viewer's frame (`autonomouse.tui.look.FRAME`),
as in the look the human chose on 2026-09-30.
Its first line holds, at the left, what Textual's title bar showed: the app's title and
the screen's sub-title (the project's slug on a project's screens), in bold; it watches
both, as Textual's `Header` does, so a screen that sets its sub-title on mount shows it
at once. At the right of that line are the local date and time, to the minute, after
the clock icon. Under it come a status line and a description line, each only when the
screen hands in a function that returns it, and under those the screen's own widgets
(the board its project's name and wake status, the planning threads their title and
keys).

The header calls the status and description functions when it mounts, when the clock
moves on a minute, and on `refresh_status()`, so a time such as `3m ago` never goes
stale. A function that fails to read the board (`autonomouse.tui.live.READ_ERRORS`)
leaves its line as it was, and the app logs the error. The minute tick redraws the
header's own lines and lays the screen out again only when a line's height changes, so
an idle viewer stays near zero CPU (sys-w90).

The dark theme shows the art as it is, white strokes on the dark background. On a
light theme white strokes would vanish, so `art_text` draws them in the theme's own
foreground colour instead; the coloured strokes keep their colours in both.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from importlib.resources import files

from rich.color import Color
from rich.style import Style
from rich.text import Span, Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.dom import NoScreen
from textual.visual import VisualType
from textual.widget import Widget
from textual.widgets import Static

from autonomouse.tui.live import READ_ERRORS
from autonomouse.tui.look import FRAME

ASSETS = files("autonomouse.tui") / "assets"
FRAMES: tuple[str, ...] = tuple(f"animation/frame_{i:02d}.ans" for i in range(8))

WHITE = (255, 255, 255)


def read_art(name: str) -> str:
    """One art file's ANSI text, `name` relative to `assets/`, without its last newline."""
    return (ASSETS / name).read_text(encoding="utf-8").rstrip("\n")


def art_text(ansi: str, *, dark: bool, ink: str) -> Text:
    """The art as Rich text. With `dark` false, every pure white stroke or cell is drawn
    in `ink` (a colour Rich reads, such as `#1e1e1e`) so it stays visible on a light
    background."""
    text = Text.from_ansi(ansi)
    if dark:
        return text
    spans = []
    for span in text.spans:
        style = span.style if isinstance(span.style, Style) else Style.parse(span.style)
        if _is_white(style.color):
            style += Style(color=ink)
        if _is_white(style.bgcolor):
            style += Style(bgcolor=ink)
        spans.append(Span(span.start, span.end, style))
    text.spans = spans
    return text


def _is_white(colour: Color | None) -> bool:
    return (
        colour is not None
        and colour.triplet is not None
        and tuple(colour.triplet) == WHITE
    )


# a function with no argument that returns a header line (`AppHeader`'s status or
# description)
Line = Callable[[], VisualType]


def local_now() -> datetime:
    """The time on this machine's clock, in its own time zone."""
    return datetime.now().astimezone()


# how often the header asks its clock whether a new minute has begun, in seconds
CLOCK_CHECK_INTERVAL = 1.0


class AppHeader(Vertical):
    """The header of every screen: in the viewer's frame, the title at the left and the
    time at the right, then the `status` and `description` lines when the screen gives
    them, then whatever `side` widgets the screen hands in, such as the board's project
    name. `now` is the clock, local time (`local_now` when not given); a test
    replaces it."""

    DEFAULT_CSS = f"""
    AppHeader {{
        dock: top;
        width: 100%;
        height: auto;
        border: {FRAME.rest};
        padding: 0 1;
    }}
    AppHeader #header-top {{
        height: 1;
    }}
    AppHeader #header-title {{
        width: 1fr;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }}
    AppHeader #header-clock {{
        width: auto;
        padding-left: 2;
        color: $text-muted;
    }}
    AppHeader .header-line {{
        width: 1fr;
        display: none;
    }}
    """

    def __init__(
        self,
        *side: Widget,
        status: Line | None = None,
        description: Line | None = None,
        now: Callable[[], datetime] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._side = side
        self._status = status
        self._description = description
        self._now = now or local_now
        self._minute: datetime | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="header-top"):
            yield Static(id="header-title")
            # drawn now, so the first layout sizes it; the tick redraws it in place
            yield Static(self._clock_text(self._now()), id="header-clock")
        yield Static(id="header-status", classes="header-line")
        yield Static(id="header-description", classes="header-line")
        yield from self._side

    def on_mount(self) -> None:
        self.watch(self.app, "title", self._show_title)
        self.watch(self.app, "sub_title", self._show_title)
        self.watch(self.screen, "title", self._show_title)
        self.watch(self.screen, "sub_title", self._show_title)
        self._tick()  # the first status and description
        self.set_interval(CLOCK_CHECK_INTERVAL, self._tick)

    def refresh_status(self) -> None:
        """Call the status and description functions again and show what they return.
        A function that raises a board read error leaves its line as it was."""
        for line_id, line in (
            ("#header-status", self._status),
            ("#header-description", self._description),
        ):
            if line is None:
                continue
            try:
                content = line()
            except READ_ERRORS as error:
                self.app.log_board_read_error(error)  # type: ignore[attr-defined]
                continue
            _show_line(self.query_one(line_id, Static), content)

    def _tick(self) -> None:
        """Once a new minute has begun on the clock: the time, then the status and
        description, which may say how long ago something happened."""
        minute = self._now().replace(second=0, microsecond=0)
        if minute == self._minute:
            return
        self._minute = minute
        # the time's width never changes, so no layout
        self.query_one("#header-clock", Static).update(
            self._clock_text(minute), layout=False
        )
        self.refresh_status()

    def _clock_text(self, time: datetime) -> str:
        return f"{self.app.icons.clock} {time:%Y-%m-%d %H:%M}"  # type: ignore[attr-defined]

    def _show_title(self) -> None:
        """What Textual's title bar showed: the screen's title and sub-title, else the
        app's."""
        try:
            screen = self.screen
        except NoScreen:
            return
        title = screen.title if screen.title is not None else self.app.title
        sub_title = (
            screen.sub_title if screen.sub_title is not None else self.app.sub_title
        )
        self.query_one("#header-title", Static).update(
            self.app.format_title(title, sub_title)
        )


def _show_line(line: Static, content: VisualType) -> None:
    """Draw `content` on a header line, which wraps when wider than the header. The
    screen is laid out again only when the line appears or its height changes: a
    status line whose `3m ago` becomes `4m ago` redraws itself alone (sys-w90)."""
    if line.display and content == line.content:
        return
    height = line.content_size.height
    line.update(content, layout=False)
    if not line.display:
        line.display = True  # lays the screen out
        return
    width = line.content_size.width
    if not width or line.visual.get_height(line.styles, width) != height:
        line.refresh(layout=True)
