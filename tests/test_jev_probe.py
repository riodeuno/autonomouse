"""Tests for autonomouse.jev.probe and `autonomouse jev probe`.

tests/fixtures/jev/probe.json is a verbatim live capture of the probe's exact request and
reply (see its "recorded" field). The offline tests replay it; they also prove the card's
"a deliberately wrong stored answer exits 1" by monkeypatching the module's ``EXPECTED``
constant -- that constant *is* the stored expected answer the card names, so patching it is
the direct way to make it wrong. Two tests make real network calls and are skipped when
TYPESAFE_API_KEY is not in the environment: they prove the card's "run twice; same answer"
against the live API, in-process and through the shell exactly as the command is run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import autonomouse.jev.probe as probe_module
from autonomouse import cli
from autonomouse.jev.client import KEY_ENV_VAR
from autonomouse.jev.probe import EXPECTED, KEY, ProbeResult, probe

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "jev" / "probe.json").read_text())


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def _has_key(monkeypatch):
    monkeypatch.setenv(KEY_ENV_VAR, "sk-test-key")


# ---------------------------------------------------------------------------- probe(), offline


def test_request_matches_the_recorded_probe_question(_has_key):
    body = json.dumps(FIXTURE["response"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)) as urlopen:
        probe()
    sent = json.loads(urlopen.call_args[0][0].data)
    assert sent == FIXTURE["request"], "the probe question changed since recording; re-record it"


def test_probe_matches_the_stored_answer_from_the_recorded_reply(_has_key):
    body = json.dumps(FIXTURE["response"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        result = probe()
    assert result == ProbeResult(pick="blue", confidence=1.0, expected=EXPECTED, matches=True)


def test_a_deliberately_wrong_stored_answer_reports_a_mismatch(_has_key, monkeypatch):
    monkeypatch.setattr(probe_module, "EXPECTED", "red")
    body = json.dumps(FIXTURE["response"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        result = probe()
    assert result == ProbeResult(pick="blue", confidence=1.0, expected="red", matches=False)


def test_non_choice_reply_counts_as_a_mismatch(_has_key):
    body = json.dumps({"answers": {KEY: {"type": "noul", "noul": 0.9}}}).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        result = probe()
    assert result == ProbeResult(pick=None, confidence=None, expected=EXPECTED, matches=False)


# ---------------------------------------------------------------------------- CLI, offline (in-process)
#
# The mocked HTTP call and the swapped-in wrong `EXPECTED` both live in this process, so these
# run through the real `autonomouse jev probe` entry point (`cli.main`) without a subprocess
# boundary a monkeypatch or mock can't cross.


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
    cli.main()


def test_cli_prints_the_answer_confidence_and_match_and_exits_zero(_has_key, monkeypatch, capsys):
    body = json.dumps(FIXTURE["response"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
        _run_main(monkeypatch, ["jev", "probe"])
    out = capsys.readouterr().out
    assert "answer: blue" in out
    assert "confidence: 1.0" in out
    assert "matches: yes" in out


def test_cli_exits_1_when_the_stored_answer_is_wrong(_has_key, monkeypatch, capsys):
    monkeypatch.setattr(probe_module, "EXPECTED", "red")
    body = json.dumps(FIXTURE["response"]).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)), pytest.raises(
        SystemExit
    ) as excinfo:
        _run_main(monkeypatch, ["jev", "probe"])
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "answer: blue" in out
    assert "matches: no" in out


# ---------------------------------------------------------------------------- live, key-gated
#
# A live test needs both the key and an explicit opt-in: the orchestrator session loads
# ~/.config/autonomouse/.env into its environment (src/autonomouse/controller.py), so the key
# alone is present in every worker's local suite even when nobody asked for a live call.

LIVE_ENV_VAR = "AUTONOMOUSE_LIVE_TESTS"

LIVE = pytest.mark.skipif(
    not (os.environ.get(KEY_ENV_VAR) and os.environ.get(LIVE_ENV_VAR) == "1"),
    reason=(
        f"{KEY_ENV_VAR} and {LIVE_ENV_VAR}=1 are not both in the environment; run with "
        f"`{LIVE_ENV_VAR}=1 uv run --env-file ~/.config/autonomouse/.env pytest`"
    ),
)


@LIVE
def test_live_probe_gives_the_same_answer_twice_in_a_row():
    first = probe()
    second = probe()
    assert first.pick == second.pick == EXPECTED
    assert first.matches
    assert second.matches


@LIVE
def test_live_cli_probe_gives_the_same_answer_twice_in_a_row():
    env = dict(os.environ)
    first = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "jev", "probe"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "jev", "probe"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "answer: blue" in first.stdout
    assert first.stdout == second.stdout


# ---------------------------------------------------------------------------- the gate itself


def test_the_live_tests_skip_on_a_key_alone_with_no_opt_in():
    """The card's own check: a worker whose local suite has the key but never asked for a live
    run must not make one.

    Spawns pytest on both live-gated files with TYPESAFE_API_KEY set and AUTONOMOUSE_LIVE_TESTS
    unset -- exactly the shape of a worker's local suite, since the orchestrator session loads
    the key from .env into its environment (src/autonomouse/controller.py) whether or not a
    live run was asked for. Deselects itself so the nested run does not spawn one of its own.
    """
    env = dict(os.environ)
    env[KEY_ENV_VAR] = "dummy"
    env.pop(LIVE_ENV_VAR, None)
    repo_root = Path(__file__).parent.parent
    this_test = "tests/test_jev_probe.py::test_the_live_tests_skip_on_a_key_alone_with_no_opt_in"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "tests/test_jev_probe.py",
            "tests/test_jev_client.py",
            "--deselect",
            this_test,
        ],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 skipped" in result.stdout, result.stdout
    assert KEY_ENV_VAR in result.stdout, result.stdout
    assert LIVE_ENV_VAR in result.stdout, result.stdout
