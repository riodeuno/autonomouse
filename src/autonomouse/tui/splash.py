"""The splash: opening a project's board first plays the autonomouse animation in the
middle of the terminal for three seconds, then the board appears.

`SplashScreen` plays the eight animation frames (`autonomouse.tui.logo.FRAMES`), one
every `FRAME_SECONDS`, in a loop, and dismisses itself after `SPLASH_SECONDS`. Any key
or click dismisses it at once. The app pushes it on top of the board screen it has just
pushed (`AutonomouseApp.open_board`), so the board reads its cards while the splash
plays and is there the moment the splash goes.

The art is 120 columns wide and 27 rows tall. A terminal smaller than that shows the
centre of each frame, cut to the terminal's width and height (`crop_centre`), so no line
of the art ever wraps. The frames' lines end at their last stroke, so each is padded to
the 120 columns first: the crop takes the same columns of every frame, and the ground
and the mice stay where they are.

The colours follow `autonomouse.tui.logo.art_text`: white strokes on the dark theme, the theme's dark foreground in their place on a light theme. The frames
end each run of a background colour with the terminal's "default background" code, which
Textual draws in the terminal's own background colour rather than the theme's: on a
light theme in a dark terminal that striped the art with dark bands. So the splash
drops that code (`on_theme_background`) and those cells show the theme's background.
"""

from __future__ import annotations

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from autonomouse.tui.logo import FRAMES, art_text, read_art

SPLASH_SECONDS = 3.0
FRAME_SECONDS = 0.3


# the art's canvas (`assets/README.md`); each frame's lines stop at their last stroke
ART_WIDTH = 120


def on_theme_background(art: Text) -> Text:
    """`art` with every "default background" colour removed, so those cells take the
    background of the screen they sit on. The art's styles carry colours only."""
    for index, span in enumerate(art.spans):
        style = span.style
        if (
            isinstance(style, Style)
            and style.bgcolor is not None
            and style.bgcolor.is_default
        ):
            art.spans[index] = span._replace(style=Style.from_color(style.color))
    return art


def crop_centre(art: Text, width: int, height: int) -> Text:
    """The centre `width` columns and `height` rows of `art`, its lines first padded on
    the right to `ART_WIDTH`. Every art character is one cell wide (Unicode quadrant
    blocks and spaces), so a column is a character."""
    lines = art.split("\n", allow_blank=True)
    top = max(0, (len(lines) - height) // 2)
    left = max(0, (ART_WIDTH - width) // 2)
    cropped = []
    for line in lines[top : top + height]:
        line = line.copy()
        line.pad_right(ART_WIDTH - cell_len(line.plain))
        cropped.append(line[left : left + width])
    return Text("\n").join(cropped)


class SplashScreen(Screen[None]):
    """The animation, centred, until three seconds pass or a key or click ends it."""

    DEFAULT_CSS = """
    SplashScreen {
        align: center middle;
    }
    SplashScreen #splash-art {
        width: auto;
        height: auto;
        text-wrap: nowrap;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._frames = tuple(read_art(name) for name in FRAMES)
        self.frame = 0  # the index into `FRAMES` of the frame on screen
        self._ended = False

    def compose(self) -> ComposeResult:
        yield Static(id="splash-art")

    def on_mount(self) -> None:
        self.set_interval(FRAME_SECONDS, self._next_frame)
        self.set_timer(SPLASH_SECONDS, self._end)
        # the signal, not a watch on `App.theme`: a watch runs before the new
        # theme's colours are worked out
        self.app.theme_changed_signal.subscribe(self, lambda _theme: self._draw())

    def on_resize(self, event: events.Resize) -> None:
        self._draw()

    def _next_frame(self) -> None:
        self.frame = (self.frame + 1) % len(self._frames)
        self._draw()

    def _draw(self) -> None:
        art = art_text(
            self._frames[self.frame],
            dark=self.app.current_theme.dark,
            ink=self.app.theme_variables["foreground"],
        )
        art = on_theme_background(art)
        width, height = self.size
        self.query_one("#splash-art", Static).update(crop_centre(art, width, height))

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        self._end()

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self._end()

    def _end(self) -> None:
        """Show the board: the timer and a key can both arrive, and only the first
        dismisses."""
        if not self._ended:
            self._ended = True
            self.dismiss()
