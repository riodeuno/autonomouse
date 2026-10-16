"""Unit tests for autonomouse.jev.client against recorded Jev responses.

tests/fixtures/jev/success.json and error_401.json are verbatim captures of live
calls to POST https://api.typesafe.ai/v1/systemone (see each fixture's "recorded"
field). error_429.json is hand-built from the documented error shape, since
deliberately exceeding the real rate limit to record one would burn quota on the
shared key for no benefit; see its "recorded" field.

One live test (test_live_call_returns_a_choice) makes a real network call and is
skipped when TYPESAFE_API_KEY is not in the environment.
"""

from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomouse.jev.client import (
    KEY_ENV_VAR,
    ChoiceAnswer,
    ChoiceQuestion,
    JevConfigError,
    JevRequestError,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    ask,
)

FIXTURES = Path(__file__).parent / "fixtures" / "jev"

QUESTIONS = {
    "sky_color": ChoiceQuestion(
        instructions="What color is the sky in this scene?",
        criteria={"blue": None, "red": None, "green": None},
    ),
    "grass_is_green": NoulQuestion(instructions="Is the grass green in this scene?"),
    "sunniness": ScoreQuestion(
        instructions="Rate how sunny this scene sounds.",
        criteria=["not sunny", "somewhat sunny", "very sunny"],
    ),
}
STATE = "The sky is blue and the grass is green under bright sunshine."


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


def _http_error(status: int, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.typesafe.ai/v1/systemone",
        code=status,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body.encode()),
    )


@pytest.fixture
def _has_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV_VAR, "sk-test-key")


def test_choice_answer_from_recorded_response(_has_key):
    fixture = _load("success.json")
    body = json.dumps(fixture["body"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        answers = ask(STATE, QUESTIONS)
    assert answers["sky_color"] == ChoiceAnswer(
        pick="blue", probabilities={"green": 0.0, "red": 0.0, "blue": 1.0}, confidence=1.0
    )


def test_noul_answer_from_recorded_response(_has_key):
    fixture = _load("success.json")
    body = json.dumps(fixture["body"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        answers = ask(STATE, QUESTIONS)
    assert answers["grass_is_green"] == NoulAnswer(probability=0.99)


def test_score_answer_from_recorded_response(_has_key):
    fixture = _load("success.json")
    body = json.dumps(fixture["body"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        answers = ask(STATE, QUESTIONS)
    assert answers["sunniness"] == ScoreAnswer(value=2.0)


def test_request_shape_matches_the_documented_api(_has_key):
    fixture = _load("success.json")
    body = json.dumps(fixture["body"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)) as mock_urlopen:
        ask(STATE, QUESTIONS)
    sent = mock_urlopen.call_args[0][0]
    assert sent.full_url == "https://api.typesafe.ai/v1/systemone"
    assert sent.get_method() == "POST"
    assert sent.get_header("Authorization") == "Bearer sk-test-key"
    assert sent.get_header("Content-type") == "application/json"
    assert sent.get_header("User-agent").startswith("autonomouse/")
    payload = json.loads(sent.data)
    assert payload["state"] == STATE
    assert payload["model"] == "jev-latest"
    assert payload["questions"]["sky_color"] == {
        "type": "choice",
        "instructions": "What color is the sky in this scene?",
        "criteria": {"blue": None, "red": None, "green": None},
    }
    assert payload["questions"]["grass_is_green"] == {
        "type": "noul",
        "instructions": "Is the grass green in this scene?",
    }


def test_401_fails_loudly_with_status_and_body(_has_key):
    fixture = _load("error_401.json")
    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error(fixture["status"], fixture["body"]),
    ), pytest.raises(JevRequestError) as excinfo:
        ask(STATE, QUESTIONS)
    assert excinfo.value.status == 401
    assert "authentication_error" in excinfo.value.body
    assert "401" in str(excinfo.value)


def test_429_fails_loudly_with_status_and_body(_has_key):
    fixture = _load("error_429.json")
    with patch(
        "urllib.request.urlopen",
        side_effect=_http_error(fixture["status"], fixture["body"]),
    ), pytest.raises(JevRequestError) as excinfo:
        ask(STATE, QUESTIONS)
    assert excinfo.value.status == 429
    assert "rate_limit_error" in excinfo.value.body


def test_missing_key_names_the_exact_command_to_fix_it(monkeypatch):
    monkeypatch.delenv(KEY_ENV_VAR, raising=False)
    with pytest.raises(JevConfigError) as excinfo:
        ask(STATE, QUESTIONS)
    message = str(excinfo.value)
    assert KEY_ENV_VAR in message
    assert "~/.config/autonomouse/.env" in message
    assert "uv run --env-file ~/.config/autonomouse/.env" in message


# A live test needs both the key and an explicit opt-in: the orchestrator session loads
# ~/.config/autonomouse/.env into its environment (src/autonomouse/controller.py), so the key
# alone is present in every worker's local suite even when nobody asked for a live call.
LIVE_ENV_VAR = "AUTONOMOUSE_LIVE_TESTS"


@pytest.mark.skipif(
    not (os.environ.get(KEY_ENV_VAR) and os.environ.get(LIVE_ENV_VAR) == "1"),
    reason=(
        f"{KEY_ENV_VAR} and {LIVE_ENV_VAR}=1 are not both in the environment; run with "
        f"`{LIVE_ENV_VAR}=1 uv run --env-file ~/.config/autonomouse/.env pytest`"
    ),
)
def test_live_call_returns_a_choice():
    answers = ask(
        STATE,
        {"sky_color": QUESTIONS["sky_color"]},
    )
    answer = answers["sky_color"]
    assert isinstance(answer, ChoiceAnswer)
    assert answer.pick in {"blue", "red", "green"}
    assert 0.0 <= answer.confidence <= 1.0
