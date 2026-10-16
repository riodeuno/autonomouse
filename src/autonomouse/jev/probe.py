"""A fixed daily health check for the Jev integration.

``autonomouse.jev.questions`` asks Jev to judge board state and picks a fallback when the
answer is missing, off the menu, or under its confidence floor -- it is built to be right about
*this run*. That does not tell anyone whether Jev itself is still up, still authenticating, and
still answering plain questions the way it always has. This module is that separate check: one
fixed question, about a state that never changes, with the right answer stored in code. Run it
once a day; the same pick every time says Jev is healthy, and a pick that no longer matches --
or an HTTP failure -- says something changed and is worth a look.

The question and its expected answer are deliberately trivial and unambiguous (the color of a
sky described as blue), so any drift in the pick, not the difficulty of the question, is the
signal.
"""

from __future__ import annotations

from dataclasses import dataclass

from autonomouse.jev.client import ChoiceAnswer, ChoiceQuestion, ask

STATE = "The sky is a clear, cloudless blue on a sunny afternoon."
QUESTION = ChoiceQuestion(
    instructions="What color is the sky described in the state?",
    criteria={"blue": None, "red": None, "green": None},
)
KEY = "probe"
EXPECTED = "blue"


@dataclass(frozen=True)
class ProbeResult:
    """What Jev answered, and whether it was the stored expected answer.

    ``pick`` and ``confidence`` are ``None`` when Jev's reply was not a usable choice answer;
    that also counts as a mismatch.
    """

    pick: str | None
    confidence: float | None
    expected: str
    matches: bool


def probe() -> ProbeResult:
    """Ask the fixed question once and compare Jev's pick against the module's ``EXPECTED``.

    Reads ``EXPECTED`` at call time, not import time, so a test can swap in a deliberately
    wrong stored answer with ``monkeypatch.setattr``. Raises
    :class:`~autonomouse.jev.client.JevConfigError` or
    :class:`~autonomouse.jev.client.JevRequestError`, the same as
    :func:`autonomouse.jev.client.ask`, on a missing key or a failed request -- a probe that
    cannot ask is not a pass.
    """
    answer = ask(STATE, {KEY: QUESTION})[KEY]
    if not isinstance(answer, ChoiceAnswer):
        return ProbeResult(pick=None, confidence=None, expected=EXPECTED, matches=False)
    return ProbeResult(
        pick=answer.pick,
        confidence=answer.confidence,
        expected=EXPECTED,
        matches=answer.pick == EXPECTED,
    )
