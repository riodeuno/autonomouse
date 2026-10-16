"""Waits for the viewer's tests: pause until the screen shows the state a test asserts.

Textual's `pilot.pause()` with no delay stops waiting once the process looks idle: its
CPU time stops keeping pace with the wall clock (`textual._wait.wait_for_idle`). On a
machine loaded past its cores the test process is starved of CPU, looks idle while it
still has work queued, and `pause()` returns before the key legend has redrawn, the
layout has placed a widget, or the live-refresh poll (`autonomouse.tui.live`) has read a
change. A fixed `pause(seconds)` guesses how long that takes, and a loaded machine
outruns the guess. `pause_until` waits for the state itself, so a test that still fails
after its timeout has found a screen that never reached that state.
"""

import time
from collections.abc import Callable
from typing import Any

from textual.widgets import Footer
from textual.widgets._footer import FooterKey

from autonomouse.tui.board import BoardItem, BoardScreen
from autonomouse.tui.card_detail import CardDetail

TIMEOUT = 10.0  # seconds; a screen that has not settled by then is a defect


async def pause_until(
    pilot,
    read: Callable[[], Any],
    ok: Callable[[Any], bool],
    *,
    timeout: float = TIMEOUT,
    step: float = 0.05,
) -> Any:
    """Pause in short steps, reading with `read()` after each, until `ok(value)` is
    true, for up to `timeout` seconds. Returns the value that held; raises
    `AssertionError` with the last value `read()` saw when none did."""
    deadline = time.monotonic() + timeout
    value = read()
    while not ok(value):
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"condition never held within {timeout}s; last saw: {value!r}"
            )
        await pilot.pause(step)
        value = read()
    return value


def legend(screen) -> tuple[frozenset[str], tuple[str, ...]]:
    """The keys the screen's legend (its `Footer`: Textual's stock one, or the viewer's
    framed `AppFooter`, which is a `Footer`) shows, and what is wrong with how it
    shows them: a key cut off a narrow terminal (no width, or outside the screen) or
    drawn over another key, such as the stock footer's palette key docked at the
    right. A screen just pushed may not have composed its `Footer` yet."""
    footers = screen.query(Footer)
    if not footers:
        return frozenset(), ("no legend yet",)
    keys = list(footers.first().query(FooterKey))
    problems = []
    for key in keys:
        if key.region.width == 0:
            problems.append(f"{key.key} is cut off the legend")
        elif not screen.region.contains_region(key.region):
            problems.append(f"{key.key} sits outside the screen")
        problems.extend(
            f"{key.key} overlaps {other.key}"
            for other in keys
            if other is not key and key.region.overlaps(other.region)
        )
    return frozenset(key.key for key in keys), tuple(problems)


async def legend_shows(pilot, ok: Callable[[frozenset[str]], bool]) -> frozenset[str]:
    """Pause until the current screen's legend is drawn with every key in view and its
    keys satisfy `ok`; returns those keys.

    The legend redraws after the refresh that follows a change of screen or focus
    (`Footer.bindings_changed`), so right after a key press it can still show the keys
    of the last focus, or no key at all while it rebuilds. Compare with `==` to an
    exact set where the test asserts a key is absent: an empty legend has no key."""
    keys, _ = await pause_until(
        pilot,
        lambda: legend(pilot.app.screen),
        lambda state: not state[1] and ok(state[0]),
    )
    return keys


def focus_of(screen) -> tuple[str | None, int, int] | None:
    """The focused board item as (its card's id, column, row), the id `None` on a
    stack's empty marker; `None` while no board item holds focus.

    The board moves focus on a later turn of the app's message loop than the key that
    asked for it (`Widget.focus` defers the change), so a key pressed before the last
    one's focus has landed moves from the old focus: press one key, wait for its focus,
    then press the next."""
    item = screen.focused
    if not isinstance(item, BoardItem):
        return None
    return (item.card.id if item.card else None), item.column, item.row


def board_settled(screen) -> bool:
    """A board screen whose focus sits on a board item and whose detail panel shows
    that item's card: the panel follows focus one message later."""
    if not isinstance(screen, BoardScreen) or focus_of(screen) is None:
        return False
    shown = screen.query_one(CardDetail).card
    return (shown.id if shown else None) == focus_of(screen)[0]


async def focus_lands(
    pilot, card_id: str | None, column: int | None = None
) -> tuple[str | None, int, int]:
    """Pause until focus sits on `card_id` (`None`: an empty marker), in `column` when
    given, and the detail panel shows it; returns `focus_of` the screen."""

    def ok(focus) -> bool:
        return (
            focus is not None
            and focus[0] == card_id
            and (column is None or focus[1] == column)
            and board_settled(pilot.app.screen)
        )

    return await pause_until(pilot, lambda: focus_of(pilot.app.screen), ok)
