"""Tests for `autonomouse.notify` and `autonomouse notify` / `autonomouse notify setup`.

Telegram fixtures live in tests/fixtures/telegram/, in the same wrapper shape as
tests/fixtures/jev/probe.json (`recorded`, `request`, `response`). `getUpdates_empty.json`
and `error_401.json` are verbatim live captures against a real bot,
made while building this card. `getUpdates_with_message.json` and `sendMessage_ok.json`
are not: no chat existed yet to record from (the human had not messaged the bot), so they
are hand-built to match the Telegram Bot API's documented shape exactly — each fixture
file's own `recorded` field says which kind it is. `test_live_*` below re-checks the real
API once TELEGRAM_BOT_TOKEN and a real chat id are both available, mirroring
tests/test_jev_probe.py's live/offline split.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from autonomouse import notify as notify_module
from autonomouse.board import runs as board_runs
from autonomouse.notify import NotifyError, notify, send, setup

FIXTURES = Path(__file__).parent / "fixtures" / "telegram"


def _fixture(name: str) -> dict:
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


def _replay(fixture_name: str):
    """`urllib.request.urlopen` returning the fixture's recorded response body."""
    body = json.dumps(_fixture(fixture_name)["response"]).encode()
    return patch("urllib.request.urlopen", return_value=_FakeResponse(body))


TELEGRAM_CONFIG = {"notify": {"channel": "telegram", "telegram_chat_id": "555000111"}}
TELEGRAM_ENV = {"TELEGRAM_BOT_TOKEN": "123456789:AAtest-token"}


# --------------------------------------------------------------------------- send() : telegram


def test_send_telegram_posts_to_sendmessage_and_reads_the_recorded_response() -> None:
    with _replay("sendMessage_ok.json") as fake_urlopen:
        send("hello from autonomouse", config=TELEGRAM_CONFIG, env=TELEGRAM_ENV)

    request = fake_urlopen.call_args[0][0]
    assert request.full_url == (
        "https://api.telegram.org/bot123456789:AAtest-token/sendMessage"
    )
    body = json.loads(request.data)
    assert body == {"chat_id": "555000111", "text": "hello from autonomouse"}


def test_send_telegram_missing_token_names_the_fix() -> None:
    with pytest.raises(NotifyError) as excinfo:
        send("hi", config=TELEGRAM_CONFIG, env={})

    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "~/.config/autonomouse/.env" in message


def test_send_telegram_missing_chat_id_names_setup() -> None:
    config = {"notify": {"channel": "telegram", "telegram_chat_id": ""}}
    with pytest.raises(NotifyError) as excinfo:
        send("hi", config=config, env=TELEGRAM_ENV)

    message = str(excinfo.value)
    assert "telegram_chat_id" in message
    assert "notify setup" in message


def test_send_telegram_rejection_is_loud() -> None:
    fixture = _fixture("error_401.json")
    body = json.dumps(fixture["response"]).encode()

    import urllib.error

    error = urllib.error.HTTPError(
        "https://api.telegram.org/bot.../sendMessage",
        fixture["http_status"],
        "Unauthorized",
        {},
        None,
    )
    with (
        patch.object(error, "read", return_value=body),
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(NotifyError) as excinfo,
    ):
        send("hi", config=TELEGRAM_CONFIG, env=TELEGRAM_ENV)

    message = str(excinfo.value)
    assert "401" in message
    assert "Unauthorized" in message


# ------------------------------------------------------------------------------- setup()


def test_setup_reads_the_chat_id_from_getupdates() -> None:
    with _replay("getUpdates_with_message.json"):
        chat_id = setup(env=TELEGRAM_ENV)

    assert chat_id == "555000111"


def test_setup_no_messages_yet_names_the_fix() -> None:
    with _replay("getUpdates_empty.json"), pytest.raises(NotifyError) as excinfo:
        setup(env=TELEGRAM_ENV)

    message = str(excinfo.value)
    assert "no messages yet" in message
    assert "notify setup" in message


def test_setup_missing_token_names_the_fix() -> None:
    with pytest.raises(NotifyError) as excinfo:
        setup(env={})

    assert "TELEGRAM_BOT_TOKEN" in str(excinfo.value)


# ---------------------------------------------------------------------------------- notify()


def test_notify_swallows_a_failure_and_logs_it_on_the_run_record(tmp_path, capsys) -> None:
    board_runs.run_start(
        tmp_path, run_id="20260922-100000-aaaa", session_id="s", pid=1, model="m"
    )
    config = {"notify": {"channel": "telegram", "telegram_chat_id": ""}}

    notify(
        "[sys-x1] decision needed",
        root=tmp_path,
        run_id="20260922-100000-aaaa",
        config=config,
        env=TELEGRAM_ENV,
    )

    run = board_runs.load_run(tmp_path, "20260922-100000-aaaa")
    assert len(run.notify_failures) == 1
    entry = run.notify_failures[0]
    assert entry["text"] == "[sys-x1] decision needed"
    assert "telegram_chat_id" in entry["error"]
    assert "notify:" in capsys.readouterr().err


def test_notify_succeeding_logs_nothing() -> None:
    with _replay("sendMessage_ok.json"):
        notify(
            "hi",
            root=None,
            run_id=None,
            config=TELEGRAM_CONFIG,
            env=TELEGRAM_ENV,
        )
    # no run record touched, no exception: success needs no log


def test_notify_with_no_run_record_does_not_crash(tmp_path, capsys) -> None:
    config = {"notify": {"channel": "telegram", "telegram_chat_id": ""}}

    notify(
        "hi",
        root=tmp_path,
        run_id="20260922-100000-nonexistent",
        config=config,
        env=TELEGRAM_ENV,
    )

    assert "notify:" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


def test_notify_without_root_only_prints(capsys) -> None:
    config = {"notify": {"channel": "telegram", "telegram_chat_id": ""}}

    notify("hi", config=config, env=TELEGRAM_ENV)

    assert "notify:" in capsys.readouterr().err


# ------------------------------------------------------------ Telegram off (notify.telegram = false)

TELEGRAM_OFF = {"notify": {"telegram": False, "telegram_chat_id": ""}}


def test_send_while_telegram_is_off_says_so_and_how_to_turn_it_on() -> None:
    with patch("urllib.request.urlopen") as urlopen, pytest.raises(NotifyError) as excinfo:
        send("hi", config=TELEGRAM_OFF, env=TELEGRAM_ENV)

    message = str(excinfo.value)
    assert message.startswith("Telegram is off")
    assert "you answer on the board" in message
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "autonomouse notify setup" in message
    assert urlopen.call_count == 0


def test_notify_while_telegram_is_off_sends_nothing_and_logs_no_failure(
    tmp_path, capsys
) -> None:
    board_runs.run_start(
        tmp_path, run_id="20260922-100000-aaaa", session_id="s", pid=1, model="m"
    )

    with patch("urllib.request.urlopen") as urlopen:
        sent = notify(
            "[sys-x1] decision needed",
            root=tmp_path,
            run_id="20260922-100000-aaaa",
            config=TELEGRAM_OFF,
            env={},
        )

    assert sent is None
    assert urlopen.call_count == 0
    assert capsys.readouterr().err == ""
    assert board_runs.load_run(tmp_path, "20260922-100000-aaaa").notify_failures == []
    assert not (tmp_path / "telegram-references.json").exists()


def test_telegram_is_on_unless_the_config_turns_it_off() -> None:
    assert notify_module.telegram_on({})
    assert notify_module.telegram_on({"notify": {"telegram_chat_id": ""}})
    assert notify_module.telegram_on({"notify": {"telegram": True}})
    assert not notify_module.telegram_on({"notify": {"telegram": False}})


def test_a_token_with_no_chat_id_is_still_set_up_wrong_not_off() -> None:
    config = {"notify": {"telegram": True, "telegram_chat_id": ""}}
    with pytest.raises(NotifyError) as excinfo:
        send("hi", config=config, env=TELEGRAM_ENV)

    assert "telegram_chat_id" in str(excinfo.value)
    assert "Telegram is off" not in str(excinfo.value)


# --------------------------------------------------------------------------------------- CLI


def test_cli_notify_without_credential_fails_loudly_and_exits_nonzero(tmp_path: Path) -> None:
    env = dict(os.environ, HOME=str(tmp_path), AUTONOMOUSE_CONFIG=str(tmp_path / "config"))
    env.pop("TELEGRAM_BOT_TOKEN", None)
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "notify", "hello"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "TELEGRAM_BOT_TOKEN" in result.stderr


def test_cli_notify_setup_without_token_fails_loudly_and_exits_nonzero(
    tmp_path: Path,
) -> None:
    env = dict(os.environ, HOME=str(tmp_path), AUTONOMOUSE_CONFIG=str(tmp_path / "config"))
    env.pop("TELEGRAM_BOT_TOKEN", None)
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "notify", "setup"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "TELEGRAM_BOT_TOKEN" in result.stderr


# ------------------------------------------------------ CLI: the secrets file (sys-s4)
#
# A human who put TELEGRAM_BOT_TOKEN in the secrets file runs `notify setup` and
# `notify "<text>"` from a plain shell: nothing exported, the token only in the file.

FILE_TOKEN = "123456:fake-token-only-in-the-secrets-file"
CHAT = 555000111


def _message_update(update_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id - 99,
            "chat": {"id": CHAT, "type": "private"},
            "date": 1758534000,
            "text": text,
        },
    }


def _secrets_only(tmp_path: Path, monkeypatch, *, chat_id: str | None) -> Path:
    """AUTONOMOUSE_CONFIG a temporary folder whose .env holds a fake token, and no token
    in the process environment; config.toml holds `chat_id` when one is given."""
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / ".env").write_text(f"TELEGRAM_BOT_TOKEN={FILE_TOKEN}\n")
    if chat_id is not None:
        (folder / "config.toml").write_text(
            f'[notify]\ntelegram_chat_id = "{chat_id}"\n'
        )
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(folder))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    return folder


def _fake_telegram(monkeypatch, updates: list[dict]) -> list[tuple[str, str, dict]]:
    """The Telegram request function faked: each call is recorded as (token, method,
    payload); getUpdates returns `updates`, sendMessage a sent message."""
    calls: list[tuple[str, str, dict]] = []

    def fake(token: str, method: str, payload: dict) -> dict:
        calls.append((token, method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": updates}
        return {"ok": True, "result": {"message_id": 7}}

    monkeypatch.setattr(notify_module, "_telegram_request", fake)
    return calls


def _spy_confirm(monkeypatch) -> list[int]:
    """`notify.confirm` still runs, and each last update id it is called with is kept."""
    confirmed: list[int] = []
    real = notify_module.confirm

    def spy(last_update_id: int, env=None) -> None:
        confirmed.append(last_update_id)
        real(last_update_id, env)

    monkeypatch.setattr(notify_module, "confirm", spy)
    return confirmed


def _cli(monkeypatch, *argv: str) -> None:
    from autonomouse import cli

    monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
    cli.main()


def test_cli_notify_setup_reads_the_token_from_the_secrets_file_and_confirms_what_it_read(
    tmp_path: Path, monkeypatch
) -> None:
    folder = _secrets_only(tmp_path, monkeypatch, chat_id=None)
    calls = _fake_telegram(
        monkeypatch, [_message_update(100, "hi"), _message_update(101, "hello?")]
    )
    confirmed = _spy_confirm(monkeypatch)

    _cli(monkeypatch, "notify", "setup")

    config = tomllib.loads((folder / "config.toml").read_text())
    assert config["notify"]["telegram_chat_id"] == str(CHAT)
    assert {token for token, _, _ in calls} == {FILE_TOKEN}
    # No chat id was configured, so the message sent to find the chat is confirmed:
    # the next `notify poll` never opens a planning thread from it.
    assert confirmed == [101]
    assert calls[-1][1:] == ("getUpdates", {"offset": 102, "limit": 1, "timeout": 0})
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_cli_notify_setup_with_a_chat_id_configured_confirms_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    folder = _secrets_only(tmp_path, monkeypatch, chat_id="999")
    calls = _fake_telegram(
        monkeypatch, [_message_update(100, "a reply poll has not read")]
    )
    confirmed = _spy_confirm(monkeypatch)

    _cli(monkeypatch, "notify", "setup")

    config = tomllib.loads((folder / "config.toml").read_text())
    assert config["notify"]["telegram_chat_id"] == str(CHAT)
    assert confirmed == []
    assert [method for _, method, _ in calls] == ["getUpdates"]


def test_cli_notify_text_reads_the_token_from_the_secrets_file_and_sends(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _secrets_only(tmp_path, monkeypatch, chat_id=str(CHAT))
    calls = _fake_telegram(monkeypatch, [])

    _cli(monkeypatch, "notify", "hi")

    assert calls == [(FILE_TOKEN, "sendMessage", {"chat_id": str(CHAT), "text": "hi"})]
    assert capsys.readouterr().out == "sent.\n"
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_cli_notify_text_while_telegram_is_off_fails_saying_so(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = _secrets_only(tmp_path, monkeypatch, chat_id=None)
    (folder / "config.toml").write_text("[notify]\ntelegram = false\n")
    calls = _fake_telegram(monkeypatch, [])

    with pytest.raises(SystemExit) as excinfo:
        _cli(monkeypatch, "notify", "hi")

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("notify: Telegram is off")
    assert "autonomouse notify setup" in err
    assert calls == []


def test_cli_notify_setup_turns_telegram_on(tmp_path: Path, monkeypatch) -> None:
    folder = _secrets_only(tmp_path, monkeypatch, chat_id=None)
    (folder / "config.toml").write_text("[notify]\ntelegram = false\n")
    _fake_telegram(monkeypatch, [_message_update(100, "hi")])

    _cli(monkeypatch, "notify", "setup")

    config = tomllib.loads((folder / "config.toml").read_text())
    assert config["notify"] == {"telegram": True, "telegram_chat_id": str(CHAT)}


def test_cli_notify_poll_while_telegram_is_off_says_so_and_reads_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    folder = _secrets_only(tmp_path, monkeypatch, chat_id=None)
    (folder / "config.toml").write_text("[notify]\ntelegram = false\n")
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    calls = _fake_telegram(monkeypatch, [])

    with pytest.raises(SystemExit) as excinfo:
        _cli(monkeypatch, "notify", "poll")

    assert excinfo.value.code == 1
    assert capsys.readouterr().out.startswith("poll: Telegram is off")
    assert calls == []


@pytest.mark.parametrize(
    "call",
    [
        lambda: send("hi", config=TELEGRAM_CONFIG, env={}),
        lambda: setup(env={}),
    ],
    ids=["send", "setup"],
)
def test_missing_token_error_names_the_config_doc_path(call) -> None:
    with pytest.raises(NotifyError) as excinfo:
        call()

    message = str(excinfo.value)
    assert "docs/config.md" in message
    assert "{" not in message


def test_config_doc_says_notify_setup_loads_secrets_and_confirms_once() -> None:
    doc = (Path(__file__).resolve().parent.parent / "docs" / "config.md").read_text()
    start = doc.index("### `autonomouse notify setup`")
    section = " ".join(doc[start : doc.index("\n### ", start + 1)].split())

    assert "loads the secrets file into its own environment for `TELEGRAM_BOT_TOKEN`" in (
        section
    )
    assert (
        "when no chat id is configured yet, confirms every update it read, so `notify "
        "poll` never turns the message you sent to find the chat into a new planning "
        "thread; with a chat id already configured it confirms nothing"
    ) in section


# --------------------------------------------------------------------------- live, key-gated

LIVE = pytest.mark.skipif(
    not os.environ.get("TELEGRAM_BOT_TOKEN"),
    reason="no TELEGRAM_BOT_TOKEN in the environment",
)
LIVE_WITH_CHAT = pytest.mark.skipif(
    not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("_LIVE_TELEGRAM_CHAT_ID")),
    reason=(
        "no TELEGRAM_BOT_TOKEN, or the bot has no chat id yet — message it once on "
        "Telegram, run `autonomouse notify setup`, then set _LIVE_TELEGRAM_CHAT_ID to "
        "run this against the real API"
    ),
)


@LIVE
def test_live_setup_matches_the_recorded_shape_or_explains_why_not() -> None:
    """Proves the real API's `getUpdates` still matches what `setup()` parses.

    Passes whichever way is true right now: a message is waiting (returns its chat
    id) or none is (raises naming the fix) — both are `setup()` doing its job
    against the real endpoint, not a recorded stand-in.
    """
    try:
        chat_id = setup()
    except NotifyError as e:
        assert "no messages yet" in str(e)
    else:
        assert chat_id


@LIVE_WITH_CHAT
def test_live_send_reaches_the_real_chat() -> None:
    config = {
        "notify": {
            "channel": "telegram",
            "telegram_chat_id": os.environ["_LIVE_TELEGRAM_CHAT_ID"],
        }
    }
    send("hello from autonomouse (test_live_send_reaches_the_real_chat)", config=config)


def test_setup_asks_the_api_that_autonomouse_telegram_api_names(monkeypatch) -> None:
    """sys-w2: the installer's container test stands a stub in for Telegram through
    `AUTONOMOUSE_TELEGRAM_API`; unset, every request goes to api.telegram.org."""
    urls: list[str] = []
    body = json.dumps(_fixture("getUpdates_with_message.json")["response"]).encode()

    def fake_urlopen(request, timeout):
        urls.append(request.full_url)
        return _FakeResponse(body)

    monkeypatch.setattr(notify_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("AUTONOMOUSE_TELEGRAM_API", raising=False)
    setup({"TELEGRAM_BOT_TOKEN": "123:abc"})
    monkeypatch.setenv("AUTONOMOUSE_TELEGRAM_API", "http://127.0.0.1:8081/")
    setup({"TELEGRAM_BOT_TOKEN": "123:abc"})

    assert urls == [
        "https://api.telegram.org/bot123:abc/getUpdates",
        "http://127.0.0.1:8081/bot123:abc/getUpdates",
    ]
