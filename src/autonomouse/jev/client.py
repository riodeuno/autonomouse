"""One function that asks Typesafe AI's Jev model typed questions about state.

Posts ``state`` and ``questions`` to ``POST https://api.typesafe.ai/v1/systemone``
(see https://docs.typesafe.ai/api.md) and returns one typed answer per question:

- a ``choice`` question returns a :class:`ChoiceAnswer` (``pick``, ``probabilities``,
  ``confidence``)
- a ``noul`` question returns a :class:`NoulAnswer` (``probability``)
- a ``score`` question returns a :class:`ScoreAnswer` (``value``)

The API key comes from the ``TYPESAFE_API_KEY`` environment variable. Per
``docs/rules.md``, secrets reach a session only through its environment: this
module never reads a secrets file itself. Locally, load the secrets
file into the environment before running, e.g.
``uv run --env-file ~/.config/autonomouse/.env``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from autonomouse import __version__

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY_ENV_VAR = "TYPESAFE_API_KEY"
# Cloudflare in front of the API answers urllib's default "Python-urllib/3.x"
# with 403 "error code: 1010"; a product User-Agent passes.
USER_AGENT = f"autonomouse/{__version__}"

_MISSING_KEY_MESSAGE = (
    f"{KEY_ENV_VAR} is not set. Put the key in ~/.config/autonomouse/.env (mode 600), "
    f"one line: printf 'TYPESAFE_API_KEY=<key>\\n' >> ~/.config/autonomouse/.env "
    f"&& chmod 600 ~/.config/autonomouse/.env -- then run with its environment loaded, "
    f"e.g. `uv run --env-file ~/.config/autonomouse/.env ...` "
    f"(docs/rules.md #7: secrets reach a session only through its environment)."
)


class JevError(Exception):
    """Base class for every error this module raises."""


class JevConfigError(JevError):
    """Raised when the client cannot find what it needs to make a request."""


class JevRequestError(JevError):
    """An HTTP call to Jev failed. Carries the exact status and body."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"Jev request failed: HTTP {status}: {body}")


@dataclass(frozen=True)
class ChoiceQuestion:
    """Choose one option from up to 255 named ``criteria``."""

    instructions: object
    criteria: dict[str, object]
    type: str = "choice"


@dataclass(frozen=True)
class NoulQuestion:
    """A yes/no question; ``criteria`` (a ``{"true": ..., "false": ...}`` pair) is optional."""

    instructions: object
    criteria: dict[str, object] | None = None
    type: str = "noul"


@dataclass(frozen=True)
class ScoreQuestion:
    """Rate the state against 2-10 ordered level descriptions in ``criteria``."""

    instructions: object
    criteria: list[object]
    type: str = "score"


Question = ChoiceQuestion | NoulQuestion | ScoreQuestion


@dataclass(frozen=True)
class ChoiceAnswer:
    pick: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class NoulAnswer:
    probability: float


@dataclass(frozen=True)
class ScoreAnswer:
    value: float


Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer


def ask(
    state: object,
    questions: dict[str, Question],
    *,
    timeout: float = 30.0,
) -> dict[str, Answer]:
    """Post ``state`` and ``questions`` to Jev and return one typed answer per question.

    Raises :class:`JevConfigError` if ``TYPESAFE_API_KEY`` is not set, and
    :class:`JevRequestError` (carrying the HTTP status and body) on any error response.
    """
    key = os.environ.get(KEY_ENV_VAR)
    if not key:
        raise JevConfigError(_MISSING_KEY_MESSAGE)

    payload = {
        "state": state,
        "model": MODEL,
        "questions": {name: _question_body(q) for name, q in questions.items()},
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise JevRequestError(exc.code, exc.read().decode(errors="replace")) from exc

    parsed = json.loads(body)
    return {name: _parse_answer(raw) for name, raw in parsed["answers"].items()}


def _question_body(question: Question) -> dict[str, object]:
    body: dict[str, object] = {
        "type": question.type,
        "instructions": question.instructions,
    }
    if question.criteria is not None:
        body["criteria"] = question.criteria
    return body


def _parse_answer(raw: dict[str, object]) -> Answer:
    kind = raw["type"]
    if kind == "choice":
        return ChoiceAnswer(
            pick=raw["choice"],
            probabilities=raw["probabilities"],
            confidence=raw["confidence"],
        )
    if kind == "noul":
        return NoulAnswer(probability=raw["noul"])
    if kind == "score":
        return ScoreAnswer(value=raw["score"])
    raise JevError(f"unknown answer type {kind!r} in Jev response: {raw!r}")
