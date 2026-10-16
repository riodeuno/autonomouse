"""Tests for config.toml loading, defaulting, masking, and `autonomouse config show`."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from autonomouse.config import (
    DEFAULTS,
    format_config,
    load_config,
    mask_secrets,
    planner_tiers,
    set_config_section,
)


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing" / "config.toml") == DEFAULTS


def test_no_board_path_setting(tmp_path: Path) -> None:
    """sys-t18: `board_root()` (`autonomouse.board.paths`) reads only `AUTONOMOUSE_HOME`
    and nothing reads `config["board"]`, so the key is removed rather than wired up."""
    assert "board" not in load_config(tmp_path / "missing" / "config.toml")

    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "config", "show"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "board.path" not in result.stdout


def test_file_overrides_only_the_keys_it_sets(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
        [ceilings]
        workers_per_wake = 3

        [notify]
        channel = "telegram"
        """
    )

    config = load_config(config_path)

    assert config["ceilings"]["workers_per_wake"] == 3
    assert config["ceilings"]["resume_tokens"] == DEFAULTS["ceilings"]["resume_tokens"]
    assert config["notify"]["channel"] == "telegram"


def test_format_config_prints_every_key() -> None:
    lines = format_config(load_config(Path("/nonexistent/config.toml"))).splitlines()
    keys = {line.split(" = ")[0] for line in lines}

    assert keys == {
        "wake.interval_hours",
        "wake.start",
        "ceilings.workers_per_wake",
        "ceilings.resume_tokens",
        "ceilings.retries_per_wake",
        "jev.enabled",
        "notify.telegram_chat_id",
        "browser.pool",
        "browser.slots",
        "planner.complex",
        "planner.simple",
        "planner.collect-user-intent",
        "orchestrator.model",
        "orchestrator.effort",
        "tiers.xs",
        "tiers.s",
        "tiers.m",
        "improve.min_runs",
        "improve.project",
        "viewer.nerd_font",
    }


def test_set_config_section_creates_the_file_and_section(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    set_config_section(path, "notify", {"telegram_chat_id": "123456"})

    assert load_config(path)["notify"]["telegram_chat_id"] == "123456"


def test_set_config_section_leaves_other_keys_and_sections_untouched(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[notify]\nchannel = "telegram"\n\n[ceilings]\nworkers_per_wake = 3\n')

    set_config_section(path, "notify", {"telegram_chat_id": "123456"})

    config = load_config(path)
    assert config["notify"]["channel"] == "telegram"
    assert config["notify"]["telegram_chat_id"] == "123456"
    assert config["ceilings"]["workers_per_wake"] == 3


def test_mask_secrets_masks_secret_shaped_keys() -> None:
    config = load_config(Path("/nonexistent/config.toml"))
    config["notify"]["api_key"] = "abc123"

    masked = mask_secrets(config)

    assert masked["notify"]["api_key"] == "***"


def test_mask_secrets_leaves_ordinary_values_alone() -> None:
    config = load_config(Path("/nonexistent/config.toml"))
    masked = mask_secrets(config)

    assert masked["ceilings"]["workers_per_wake"] == 8
    assert masked["jev"]["enabled"] is False


def test_cli_config_show_prints_defaults_when_file_is_missing(tmp_path: Path) -> None:
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "config", "show"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "ceilings.retries_per_wake = 2" in result.stdout
    assert "jev.enabled = false" in result.stdout
    assert "notify.telegram_chat_id = " in result.stdout
    # sys-s15: no deferred wake exists and the scheduler owns the cadence, so neither ceiling
    # that counted wakes is left. sys-j6: no code reads a card's attempt cap (five attempts
    # is a charter default, sys-j4), so it leaves the config too.
    assert "wakes_per_day" not in result.stdout
    assert "deferrals_per_day" not in result.stdout
    assert "iterations_per_card" not in result.stdout


def test_the_planner_table_maps_each_kind_of_session_to_its_tier() -> None:
    config = load_config(Path("/nonexistent/config.toml"))

    assert planner_tiers(config) == {
        "complex": ("fable", "high"),
        "simple": ("opus", "xhigh"),
        "collect-user-intent": ("opus", "medium"),
    }


def test_a_planner_model_left_in_the_file_changes_nothing(tmp_path: Path) -> None:
    """`planner.model`, the planner's one model before the table, may still sit in a
    human's config file; the table decides every session's tier."""
    path = tmp_path / "config.toml"
    path.write_text('[planner]\nmodel = "fable"\nsimple = "fable/xhigh"\n')

    assert planner_tiers(load_config(path)) == {
        "complex": ("fable", "high"),
        "simple": ("fable", "xhigh"),
        "collect-user-intent": ("opus", "medium"),
    }


@pytest.mark.parametrize(
    "value", ["opus", "haiku/high", "opus/low", "", 3], ids=repr
)
def test_a_bad_planner_tier_is_refused_naming_its_key(tmp_path: Path, value) -> None:
    config = load_config(tmp_path / "missing.toml")
    config["planner"]["collect-user-intent"] = value

    with pytest.raises(ValueError, match="planner.collect-user-intent must be"):
        planner_tiers(config)


# sys-w2: the installer writes the browser pool's address and the first wake's time with
# `autonomouse config set`, which keeps every other key in the file as it is.


def _config_set(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(tmp_path), AUTONOMOUSE_CONFIG=str(tmp_path / "config"))
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "config", "set", *args],
        env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


def test_cli_config_set_writes_the_key_as_its_type_and_keeps_every_other_key(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config" / "config.toml"
    path.parent.mkdir()
    path.write_text('[notify]\ntelegram_chat_id = "424242"\n')

    for key, value in (("browser.pool", "deploy@192.0.2.14"), ("browser.slots", "2"),
                       ("wake.interval_hours", "6"), ("jev.enabled", "true"),
                       ("wake.start", "2026-09-29T09:00+02:00")):  # fmt: skip
        result = _config_set(tmp_path, key, value)
        assert result.returncode == 0, result.stderr
        assert result.stdout == f"{key} saved in {path}\n"

    config = load_config(path)
    assert config["browser"] == {"pool": "deploy@192.0.2.14", "slots": 2}
    assert config["wake"] == {"interval_hours": 6, "start": "2026-09-29T09:00+02:00"}
    assert config["jev"]["enabled"] is True
    assert config["notify"]["telegram_chat_id"] == "424242"


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("browser.address", "x", "browser.address is not a config key; `autonomouse config show` lists them"),
        ("pool", "x", "pool is not a config key; `autonomouse config show` lists them"),
        ("wake.interval_hours", "four", "wake.interval_hours is a whole number; got 'four'"),
        ("jev.enabled", "yes", "jev.enabled is true or false; got 'yes'"),
    ],
)
def test_cli_config_set_refuses_an_unknown_key_or_a_value_of_the_wrong_type(
    tmp_path: Path, key: str, value: str, error: str
) -> None:
    result = _config_set(tmp_path, key, value)

    assert result.returncode == 1
    assert result.stderr == f"config set: {error}\n"
    assert not (tmp_path / "config" / "config.toml").exists()
