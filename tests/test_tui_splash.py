"""The splash: opening a project's board plays the logo animation centred in the
terminal for three seconds, then shows the board; a key or a click shows the board at
once. A terminal narrower or shorter than the art shows the centre of each frame, never
a wrapped line. The dark theme draws the art white on dark, the light theme dark on
light (`autonomouse.tui.splash`)."""

import itertools
import json
import time

import pytest
from rich.color import Color
from rich.text import Text
from textual.widgets import DataTable, Static

from autonomouse.board import commands as board_commands
from autonomouse.tui import splash as splash_module
from autonomouse.tui.app import LIGHT_THEME, AutonomouseApp
from autonomouse.tui.board import BoardScreen, Stacks
from autonomouse.tui.logo import FRAMES, WHITE, read_art
from autonomouse.tui.splash import SPLASH_SECONDS, SplashScreen

ART_WIDTH = 120
ART_HEIGHT = 27


@pytest.fixture
def board(tmp_path, monkeypatch):
    """One project with one card in todo, and the splash turned on."""
    root = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(root))
    board_commands.project_new(
        root,
        slug="system",
        name="Autonomouse system",
        goal="This project builds the system itself.",
        repo="https://github.com/test/system",
        repo_path="/home/test/system",
        default_branch="main",
        priority=1,
    )
    board_commands.card_new(
        root, card_id="sys-a1", project="system", title="a card", goal="g", test="t"
    )
    monkeypatch.setattr(AutonomouseApp, "SPLASH", True)
    return root


@pytest.fixture
def long_splash(monkeypatch):
    """A splash that plays for a minute unless a key, a click or the test ends it: the
    board a test sees is then the key's doing, and a test that reads several frames is
    not cut short by the three seconds running out on a loaded machine."""
    monkeypatch.setattr(splash_module, "SPLASH_SECONDS", 60.0)


async def _next_frame(pilot, splash: SplashScreen, frame: int) -> None:
    """Wait until the splash shows a frame other than `frame`."""
    deadline = time.monotonic() + 10
    while splash.frame == frame:
        assert time.monotonic() < deadline, "the splash stopped on one frame"
        await pilot.pause(0.05)


def _frame_lines(index: int) -> list[str]:
    """Frame `index`'s plain lines, each padded to the art's full width."""
    lines = Text.from_ansi(read_art(FRAMES[index])).plain.split("\n")
    return [line.ljust(ART_WIDTH) for line in lines]


def _art(screen: SplashScreen) -> Text:
    content = screen.query_one("#splash-art", Static).content
    assert isinstance(content, Text)
    return content


def _own_time() -> float:
    """The viewer's own clock: the monotonic clock less the time this thread, which runs
    the viewer, waited for a CPU while other processes held them all (the second field
    of `/proc/thread-self/schedstat`, in nanoseconds). It moves while the viewer works
    and while it idles, and stands still while the machine's other processes keep it
    from running, so a gap on it is the viewer's own and not the machine's load."""
    with open("/proc/thread-self/schedstat") as schedstat:
        waited = int(schedstat.read().split()[1])
    return time.monotonic() - waited / 1e9


async def _open_board(app, pilot, clock=time.monotonic) -> float:
    """Press enter on the project; the time on `clock` just before the press."""
    await pilot.pause()
    table = app.query_one(DataTable)
    table.move_cursor(row=table.get_row_index("system"))
    pressed = clock()
    await pilot.press("enter")
    await pilot.pause()
    return pressed


class _WatchedSplash(SplashScreen):
    """The splash, noting what a test cannot see between two of its pauses on a loaded
    machine: when it went on screen, what lay under it, each frame it drew and when,
    when it handed the screen back, and the cards the board under it held then."""

    def _clock(self) -> float:
        return time.monotonic()

    def on_mount(self) -> None:
        # Textual then calls `SplashScreen.on_mount` itself, as it calls the handler of
        # every class; calling it here too started a second frame timer
        self.mounted_at = self._clock()
        self.under = self.app.screen_stack[-2]
        self.frames_drawn: set[int] = set()
        self.draws: list[tuple[float, int]] = []

    def _draw(self) -> None:
        self.frames_drawn.add(self.frame)
        self.draws.append((self._clock(), self.frame))
        super()._draw()

    def dismiss(self, result=None):
        self.dismissed_at = self._clock()
        stacks = self.under.query_one(Stacks)
        self.board_at_dismiss = (
            stacks.display,
            [item.card.id for items in stacks.grid for item in items if item.card],
        )
        return super().dismiss(result)


class _SplashOnOwnTime(_WatchedSplash):
    """The watched splash timed on the viewer's own clock (`_own_time`): it notes its
    draws and its end on that clock, and ends itself once `SPLASH_SECONDS` have passed on
    it, with its own wall-clock timer set to a minute (`long_splash`). The frames it
    shows and the gaps between them then count the viewer's own work and idling, and no
    time the machine's other processes took from it."""

    def _clock(self) -> float:
        return _own_time()

    def on_mount(self) -> None:
        # Textual then calls `_WatchedSplash.on_mount`, which notes `mounted_at`
        self.set_interval(0.1, self._end_on_own_time)

    def _end_on_own_time(self) -> None:
        if self._clock() - self.mounted_at >= SPLASH_SECONDS:
            self._end()


def _watch_splashes(monkeypatch, kind=_WatchedSplash) -> list[_WatchedSplash]:
    """Every splash the app opens from now on, watched as a `kind`."""
    splashes: list[_WatchedSplash] = []

    def watched() -> _WatchedSplash:
        splashes.append(kind())
        return splashes[-1]

    monkeypatch.setattr("autonomouse.tui.app.SplashScreen", watched)
    return splashes


async def test_opening_a_board_plays_the_splash_for_three_seconds_then_shows_it(
    board, monkeypatch
):
    splashes = _watch_splashes(monkeypatch)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        opened = await _open_board(app, pilot)
        deadline = opened + SPLASH_SECONDS + 30
        while not isinstance(app.screen, BoardScreen):
            assert time.monotonic() < deadline, "the board never showed"
            await pilot.pause(0.05)

        (splash,) = splashes
        # the splash went on screen over the board, which read its cards meanwhile
        assert isinstance(splash.under, BoardScreen)
        assert app.screen is splash.under
        assert splash.dismissed_at - splash.mounted_at >= SPLASH_SECONDS
        assert len(splash.frames_drawn) > 1, "the splash never moved past a frame"


# the system board on 2026-09-30, when its splash stood still: 424 cards, 367 done
BIG_BOARD = {"done": 367, "cancelled": 30, "backlog": 15, "todo": 8, "in_progress": 4}

# the longest time on the viewer's own clock (`_own_time`) the splash may show one
# frame on a big board: half the splash. On this repository's 4-core test machine the
# splash on this test's board shows a frame for at most about half a second of it,
# alone or beside sixteen busy processes, and with the board of before sys-w109 stood
# still for about two seconds of it. On the wall clock, beside the other workers' test
# runs, the same board stood still for up to 9 s (sys-w123).
STALL_SECONDS = SPLASH_SECONDS / 2


@pytest.fixture
def big_board(board):
    """The one-card board grown to the system board's size (`BIG_BOARD`), its one
    card counted among the todo cards."""
    cards = board / "projects" / "system" / "cards"
    template = json.loads((cards / "sys-a1.json").read_text())
    ids = ["sys-a1"]
    for stack, count in BIG_BOARD.items():
        for position in range(count - (stack == "todo")):
            card_id = f"sys-t{len(ids)}"
            card = dict(
                template,
                id=card_id,
                # as long as the system board's titles (91 characters on average),
                # which wrap in a column
                title=f"card {len(ids)}: the splash animation plays its frames on "
                "a board with hundreds of cards in its stacks",
                position=position + (stack == "todo"),
                stack=stack,
                phase="writing" if stack == "in_progress" else None,
            )
            (cards / f"{card_id}.json").write_text(json.dumps(card))
            ids.append(card_id)
    assert len(ids) == sum(BIG_BOARD.values())
    return set(ids)


async def test_on_a_board_of_424_cards_the_splash_keeps_moving_then_shows_the_board(
    big_board, long_splash, monkeypatch
):
    """Before sys-w109 the board mounted every card while the splash played, and the
    splash drew its first frame, then nothing for about two seconds. The test times the
    splash on the viewer's own clock (`_SplashOnOwnTime`), so it counts that blocking
    and none of the time the machine's other processes take."""
    splashes = _watch_splashes(monkeypatch, _SplashOnOwnTime)
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        opened = await _open_board(app, pilot, _own_time)
        # on the wall clock, which a loaded machine stretches
        deadline = time.monotonic() + 60
        while not isinstance(app.screen, BoardScreen):
            assert time.monotonic() < deadline, "the board never showed"
            await pilot.pause(0.05)

        (splash,) = splashes
        assert app.screen is splash.under

    # every change of frame, from the key that opened the board to the splash's end
    changes = [
        at
        for (_, before), (at, frame) in itertools.pairwise(splash.draws)
        if frame != before
    ]
    times = [opened, splash.draws[0][0], *changes, splash.dismissed_at]
    shown = ", ".join(f"+{at - opened:.2f}s" for at in times)
    assert len(changes) >= 3, (
        f"the splash changed its frame {len(changes)} times: {shown}"
    )
    stall = max(later - earlier for earlier, later in itertools.pairwise(times))
    assert stall < STALL_SECONDS, (
        f"the splash stood still for {stall:.2f}s of the viewer's own time: {shown}"
    )

    # the board was there, whole, the moment the splash ended
    displayed, ids = splash.board_at_dismiss
    assert displayed
    assert set(ids) == big_board


@pytest.mark.parametrize("key", ["space", "q", "escape", "enter"])
async def test_a_key_during_the_splash_shows_the_board_at_once(board, long_splash, key):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_board(app, pilot)
        assert isinstance(app.screen, SplashScreen)
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, BoardScreen)
        # the key only ended the splash: `q` did not quit, escape did not leave the board
        assert app.is_running


async def test_a_click_during_the_splash_shows_the_board_at_once(board, long_splash):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_board(app, pilot)
        assert isinstance(app.screen, SplashScreen)
        assert await pilot.click("#splash-art")
        await pilot.pause()
        assert isinstance(app.screen, BoardScreen)


async def test_at_full_size_the_splash_shows_each_frame_whole_and_centred(
    board, long_splash
):
    app = AutonomouseApp()
    async with app.run_test(size=(130, 40)) as pilot:
        await _open_board(app, pilot)
        splash = app.screen
        assert isinstance(splash, SplashScreen)
        art = splash.query_one("#splash-art", Static)
        assert _art(splash).plain.split("\n") == _frame_lines(splash.frame)
        assert art.region.width == ART_WIDTH and art.region.height == ART_HEIGHT
        assert art.region.x == (130 - ART_WIDTH) // 2
        assert art.region.y == (40 - ART_HEIGHT) // 2


@pytest.mark.parametrize("size", [(80, 24), (60, 40), (120, 20)])
async def test_a_small_terminal_shows_the_centre_of_the_frame_with_no_wrapped_line(
    board, long_splash, size
):
    width, height = size
    app = AutonomouseApp()
    async with app.run_test(size=size) as pilot:
        await _open_board(app, pilot)
        splash = app.screen
        assert isinstance(splash, SplashScreen)
        for _ in range(3):  # this frame and the next two, as the animation plays
            frame = splash.frame
            shown = _art(splash).plain.split("\n")
            rows = min(height, ART_HEIGHT)
            columns = min(width, ART_WIDTH)
            top = (ART_HEIGHT - rows) // 2
            left = (ART_WIDTH - columns) // 2
            assert shown == [
                line[left : left + columns]
                for line in _frame_lines(frame)[top : top + rows]
            ]
            # what the terminal draws: one screen line per art line, none wider
            art = splash.query_one("#splash-art", Static)
            assert art.region.height == rows
            assert art.region.width == columns
            strips = art.render_lines(art.region.reset_offset)
            assert [strip.text for strip in strips] == shown
            await _next_frame(pilot, splash, frame)


def _stroke_colours(text: Text) -> set[tuple[int, int, int]]:
    found = set()
    for span in text.spans:
        style = span.style
        for colour in (style.color, style.bgcolor):
            if colour is not None and colour.triplet is not None:
                found.add(tuple(colour.triplet))
    return found


async def test_the_dark_theme_draws_white_strokes_and_the_light_theme_dark_ones(
    board, long_splash
):
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _open_board(app, pilot)
        splash = app.screen
        assert isinstance(splash, SplashScreen)
        assert app.current_theme.dark
        assert WHITE in _stroke_colours(_art(splash))

        app.theme = LIGHT_THEME
        await pilot.pause()
        assert not app.current_theme.dark
        colours = _stroke_colours(_art(splash))
        assert WHITE not in colours
        ink = Color.parse(app.theme_variables["foreground"]).triplet
        assert tuple(ink) in colours
        # the ink is dark: the strokes stay readable on the light background
        assert sum(ink) < 3 * 128


def _backgrounds_drawn(splash: SplashScreen) -> set[tuple[int, int, int]]:
    """Every background colour the terminal draws in the splash's art, as RGB."""
    art = splash.query_one("#splash-art", Static)
    found = set()
    for strip in art.render_lines(art.region.reset_offset):
        for segment in strip:
            bgcolor = segment.style.bgcolor if segment.style else None
            if bgcolor is not None:
                found.add(tuple(bgcolor.get_truecolor()))
    return found


@pytest.mark.parametrize("light", [False, True])
async def test_the_art_has_no_background_but_its_own_and_the_themes(
    board, long_splash, light
):
    """The frames reset the background with the terminal's "default background" code;
    drawn as it is, that shows the terminal's own background in bands across the art,
    which on the light theme is a colour the theme does not have."""
    app = AutonomouseApp()
    async with app.run_test(size=(120, 40)) as pilot:
        if light:
            app.theme = LIGHT_THEME
        await _open_board(app, pilot)
        splash = app.screen
        assert isinstance(splash, SplashScreen)
        drawn = _backgrounds_drawn(splash)
        art_backgrounds = {
            tuple(span.style.bgcolor.triplet)
            for span in _art(splash).spans
            if span.style.bgcolor is not None and span.style.bgcolor.triplet
        }
        screen_background = splash.styles.background.rgb
        assert drawn - art_backgrounds == {screen_background}
