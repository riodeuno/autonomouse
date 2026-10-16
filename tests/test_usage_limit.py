"""autonomouse.usage_limit: the limit line is found and its reset time read."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from autonomouse.usage_limit import UsageLimit, detect

# Monday 2026-09-21, 14:10 UTC.
HIT = datetime(2026, 9, 21, 14, 10, 5, tzinfo=UTC)


def _reset(text, local_tz=UTC, hit_at=HIT):
    found = detect(text, hit_at=hit_at, local_tz=local_tz)
    assert found is not None
    return found.to_dict()["reset_at"]


@pytest.mark.parametrize(
    ("text", "reset_at"),
    [
        ("You've hit your session limit · resets 3:45pm", "2026-09-21T15:45:00Z"),
        ("You've hit your session limit · resets 3pm", "2026-09-21T15:00:00Z"),
        # Earlier in the day than the hit: tomorrow.
        ("You've hit your Opus limit · resets 9:30am", "2026-09-22T09:30:00Z"),
        ("You've hit your weekly limit · resets Mon 12:00am", "2026-09-28T00:00:00Z"),
        ("You've hit your Sonnet limit · resets Wed 9am", "2026-09-23T09:00:00Z"),
        ("You've hit your weekly limit · resets Oct 2, 5pm", "2026-10-02T17:00:00Z"),
        # The hit's own minute is still the hit's day.
        ("You've hit your session limit · resets 2:10pm", "2026-09-21T14:10:00Z"),
        # A named zone wins over the local clock.
        ("You've hit your session limit · resets 4pm (Europe/London)", "2026-09-21T15:00:00Z"),
        ("Claude AI usage limit reached|1790000000", "2026-09-21T14:13:20Z"),
        (
            "Claude usage limit reached. Your limit will reset at 2026-09-22T17:00:00Z.",
            "2026-09-22T17:00:00Z",
        ),
        ("Claude usage limit reached. Your limit will reset at 18:30.", "2026-09-21T18:30:00Z"),
    ],
)
def test_the_reset_time_is_read(text, reset_at):
    assert _reset(text) == reset_at


def test_a_bare_clock_time_is_read_on_the_local_clock():
    berlin = ZoneInfo("Europe/Berlin")  # UTC+2 in September: the hit is 16:10 there
    assert _reset("You've hit your session limit · resets 5pm", berlin) == "2026-09-21T15:00:00Z"
    assert _reset("You've hit your session limit · resets 4pm", berlin) == "2026-09-22T14:00:00Z"
    assert _reset("You've hit your session limit · resets 5pm", timezone(timedelta(hours=-7))) == (
        "2026-09-22T00:00:00Z"
    )


def test_the_line_is_found_inside_longer_output_and_recorded_whole():
    text = "Working on sys-d4...\nAPI Error\nYou've hit your session limit · resets 3:45pm\nbye"
    found = detect(text, hit_at=HIT, local_tz=UTC)
    assert found == UsageLimit(
        hit_at=HIT,
        reset_at=datetime(2026, 9, 21, 15, 45, tzinfo=UTC),
        text="You've hit your session limit · resets 3:45pm",
    )
    assert found.to_dict() == {
        "hit_at": "2026-09-21T14:10:05Z",
        "reset_at": "2026-09-21T15:45:00Z",
        "text": "You've hit your session limit · resets 3:45pm",
    }


@pytest.mark.parametrize(
    "text",
    [
        "You've hit your session limit",
        "You've hit your session limit · resets soon",
        "Claude usage limit reached.",
    ],
)
def test_a_hit_whose_reset_cannot_be_read_is_still_a_hit(text):
    found = detect(text, hit_at=HIT, local_tz=UTC)
    assert found is not None and found.reset_at is None and found.text == text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "All cards finished; PRs opened.",
        "You've used 85% of your session limit · resets 3:45pm",
        'API Error: 429 {"type":"rate_limit_error"}',
    ],
)
def test_no_hit(text):
    assert detect(text, hit_at=HIT, local_tz=UTC) is None


def test_a_naive_hit_time_is_refused():
    with pytest.raises(ValueError):
        detect("You've hit your session limit", hit_at=datetime(2026, 9, 21))  # noqa: DTZ001
