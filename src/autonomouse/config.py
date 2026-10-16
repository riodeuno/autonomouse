"""Load, default, and mask `~/.config/autonomouse/config.toml`
(`$AUTONOMOUSE_CONFIG/config.toml` when that is set).

A missing file yields the defaults below untouched; a present file overrides
only the keys it sets. `mask_secrets` hides any value that could carry a
credential before the config reaches a terminal: a key name that reads as a
secret (`token`, `password`, `secret`, `credential`, anything ending `key`).
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autonomouse.board.schema import Size


def config_dir() -> Path:
    """`$AUTONOMOUSE_CONFIG` when set, else `~/.config/autonomouse`: holds `config.toml`
    and the `PAUSE` file."""
    env = os.environ.get("AUTONOMOUSE_CONFIG")
    if env and env.strip():
        return Path(env)
    return Path.home() / ".config" / "autonomouse"


def pause_path() -> Path:
    """The kill switch: while this file exists, `autonomouse run` starts nothing."""
    return config_dir() / "PAUSE"


def secrets_path() -> Path:
    """The secrets file (`KEY=VALUE` lines, mode 600): `.env` in `config_dir()`, so an
    install from the wheel finds it without a repository checkout. Every wake and planner
    session loads it into the environment of what it starts (`docs/rules.md`, item 7)."""
    return config_dir() / ".env"


def config_path() -> Path:
    """Where `config.toml` itself lives, honoring `$AUTONOMOUSE_CONFIG`."""
    return config_dir() / "config.toml"


DEFAULTS: dict[str, Any] = {
    # The wake's cadence: `autonomouse timer install` writes the wake's crontab line from
    # these (`autonomouse.timer`): every `interval_hours` (a whole number of hours from 1 to
    # 24), at 00:00 and every N hours after while `start` is unset, else from `start`, an
    # ISO-8601 time, which is also when the first scheduled wake runs.
    "wake": {
        "interval_hours": 4,
        "start": "",
    },
    "ceilings": {
        "workers_per_wake": 8,
        "resume_tokens": 140000,
        # How many new sessions one wake starts after Jev's `recover` answered `retry`
        # (autonomouse.controller); a `retry` past it ends the wake as `next_wake`.
        "retries_per_wake": 2,
    },
    "jev": {
        "enabled": False,
    },
    # `telegram` is whether autonomouse messages the human on Telegram at all. `false` is a
    # choice (`install.sh`'s blank answer to its Telegram question writes it): nothing is
    # sent, nothing is polled, and the human answers on the board (`autonomouse.notify`).
    "notify": {
        "telegram": True,
        "telegram_chat_id": "",
    },
    # The Chrome pool's login, `user@host` (browsermaze, https://github.com/riodeuno/browsermaze;
    # `src/autonomouse/browser.py`), and how many slots it runs; unset means every session of
    # every wake keeps today's local Playwright/Chrome DevTools MCP servers, with no
    # runtime fallback either way (the human's rule, 2026-09-28).
    "browser": {
        "pool": "",
        "slots": 4,
    },
    # Each planner session's tier, `model/effort`, by the kind of session Jev judges it to
    # be (the human's decision, 2026-09-28): `complex`, `simple` or `collect-user-intent`,
    # a session that only collects the human's intent. Read through `planner_tiers`.
    "planner": {
        "complex": "fable/high",
        "simple": "opus/xhigh",
        "collect-user-intent": "opus/medium",
    },
    "orchestrator": {
        "model": "fable",
        "effort": "medium",
    },
    # A card's size fixes its worker's tier (the human's decision, 2026-09-23): the model
    # and reasoning effort, as `model/effort`, for each size. Read through `worker_tiers`.
    "tiers": {
        "xs": "sonnet/xhigh",
        "s": "opus/xhigh",
        "m": "fable/high",
    },
    # `autonomouse review improve` (`autonomouse.review.improve`): the runs a pattern must
    # show in, and the board project its proposal cards go to.
    "improve": {
        "min_runs": 2,
        "project": "system",
    },
    # The board viewer (`autonomouse` with no arguments): whether the terminal the human
    # views it from has a Nerd Font, so the viewer shows Nerd Font icons rather than plain
    # Unicode symbols (`autonomouse.tui.icons`). Read through `viewer_nerd_font`.
    "viewer": {
        "nerd_font": False,
    },
}

# The reasoning efforts the orchestrator session may run at. The human's rule
# (2026-09-22) was Fable at high or Opus at xhigh, never lower; the human's rule
# (2026-09-28) replaces it: every wake's orchestrator runs on Fable at medium effort.
ORCHESTRATOR_EFFORTS = ("medium", "high", "xhigh", "max")

# What a `tiers` value may name. The human's floor (2026-09-22) is Sonnet at extra-high
# reasoning: no model runs at `low` or `medium`, and Sonnet runs only at `xhigh`.
WORKER_MODELS = ("sonnet", "opus", "fable")
WORKER_EFFORTS = ("high", "xhigh")

_SECRET_KEY = re.compile(r"token|secret|password|credential|key$", re.IGNORECASE)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Return the config: `config.toml` merged over the defaults.

    A missing file yields exactly `DEFAULTS`.
    """
    config = _deep_copy(DEFAULTS)
    file_path = path if path is not None else config_path()
    if file_path.is_file():
        with file_path.open("rb") as f:
            on_disk = tomllib.load(f)
        _deep_merge(config, on_disk)
    return config


def worker_tiers(config: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """The config's `tiers` table as `{size: (model, effort)}`, one entry per card size.

    Raises `ValueError` naming the key when a key is not a card size (`xs`, `s`, `m`; a card
    bigger than `m` is split, never started), a size has no tier, or a value is not
    `<model>/<effort>` with a model in `WORKER_MODELS` and an effort in `WORKER_EFFORTS` at
    or above the `sonnet/xhigh` floor.
    """
    table = config["tiers"]
    sizes = [size.value for size in Size]
    for key in table:
        if key not in sizes:
            raise ValueError(
                f"tiers.{key} is not a card size; the sizes are {', '.join(sizes)}, and a "
                f"card bigger than m is split"
            )
    tiers = {}
    for size in sizes:
        value = table.get(size)
        model, _, effort = value.partition("/") if isinstance(value, str) else ("", "", "")
        if model not in WORKER_MODELS or effort not in WORKER_EFFORTS:
            raise ValueError(
                f"tiers.{size} must be <model>/<effort> with a model among "
                f"{', '.join(WORKER_MODELS)} and an effort among {', '.join(WORKER_EFFORTS)} "
                f"— got {value!r}"
            )
        if model == "sonnet" and effort != "xhigh":
            raise ValueError(
                f"tiers.{size} is {value!r}, below the sonnet/xhigh floor: Sonnet runs only "
                f"at xhigh"
            )
        tiers[size] = (model, effort)
    return tiers


# The kinds of planner session Jev tells apart (`autonomouse.jev.questions.planner_session`),
# each a key of the config's `planner` table. `complex` is also the tier of every session Jev
# does not judge: Jev off, failed or below its floor, and the live sessions of `plan join`
# and `plan new`.
PLANNER_SESSIONS = ("complex", "simple", "collect-user-intent")
PLANNER_EFFORTS = ("medium", "high", "xhigh", "max")


def planner_tiers(config: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    """The config's `planner` table as `{kind: (model, effort)}`, one entry per kind in
    `PLANNER_SESSIONS`.

    Raises `ValueError` naming the key when a kind's value is not `<model>/<effort>` with a
    model in `WORKER_MODELS` and an effort in `PLANNER_EFFORTS`. Other keys in the table are
    not read: `planner.model`, the planner's one model before this table, may still sit in
    a config file and changes nothing.
    """
    table = config["planner"]
    tiers = {}
    for kind in PLANNER_SESSIONS:
        value = table.get(kind)
        model, _, effort = value.partition("/") if isinstance(value, str) else ("", "", "")
        if model not in WORKER_MODELS or effort not in PLANNER_EFFORTS:
            raise ValueError(
                f"planner.{kind} must be <model>/<effort> with a model among "
                f"{', '.join(WORKER_MODELS)} and an effort among "
                f"{', '.join(PLANNER_EFFORTS)} — got {value!r}"
            )
        tiers[kind] = (model, effort)
    return tiers


def viewer_nerd_font(config: Mapping[str, Any]) -> bool:
    """The config's `viewer.nerd_font`. Raises `TypeError` naming the key when it is
    not `true` or `false`, as a hand edit could leave it."""
    value = config["viewer"]["nerd_font"]
    if not isinstance(value, bool):
        raise TypeError(f"viewer.nerd_font is true or false; got {value!r}")
    return value


def set_config_section(path: Path, section: str, values: dict[str, Any]) -> dict[str, Any]:
    """Merge `values` into `section` of the config file at `path`, creating the file
    and the section if either is missing. Every other section and key is read from
    the file as-is and written back untouched — this never round-trips through
    `DEFAULTS`, so a key the human never set stays unset.

    Returns the file's full on-disk content (not merged with `DEFAULTS`) after the
    write. Used by `autonomouse notify setup` to write the telegram chat id.
    """
    on_disk: dict[str, Any] = {}
    if path.is_file():
        with path.open("rb") as f:
            on_disk = tomllib.load(f)
    table = dict(on_disk.get(section, {}))
    table.update(values)
    on_disk[section] = table
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump_toml(on_disk))
    return on_disk


def set_key(path: Path, key: str, value: str) -> Any:
    """Write one key, `<section>.<name>` as `autonomouse config show` prints it, into the
    config file at `path` (`set_config_section`), as the type its default has: a whole
    number for an integer key, `true` or `false` for a boolean one, else the text as given.
    Returns the value written. Raises `ValueError` naming the key when it is not one of
    `DEFAULTS`, or when the value is not of its type."""
    section, _, name = key.partition(".")
    if name not in DEFAULTS.get(section, {}):
        raise ValueError(f"{key} is not a config key; `autonomouse config show` lists them")
    default = DEFAULTS[section][name]
    typed: Any = value
    if isinstance(default, bool):
        if value not in ("true", "false"):
            raise ValueError(f"{key} is true or false; got {value!r}")
        typed = value == "true"
    elif isinstance(default, int):
        try:
            typed = int(value)
        except ValueError:
            raise ValueError(f"{key} is a whole number; got {value!r}") from None
    set_config_section(path, section, {name: typed})
    return typed


def _dump_toml(data: dict[str, dict[str, Any]]) -> str:
    """Render `data` (tables of scalar values, `config.toml`'s own shape) as TOML."""
    lines = []
    for section, table in data.items():
        lines.append(f"[{section}]")
        for key, value in table.items():
            lines.append(f"{key} = {_toml_literal(value)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _toml_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise TypeError(f"config values must be str, int or bool — got {value!r}")


def mask_secrets(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `config` with secret-shaped values masked."""
    return {
        section: {key: _mask_value(key, value) for key, value in table.items()}
        for section, table in config.items()
    }


def format_config(config: dict[str, Any]) -> str:
    """Render `config` as one `section.key = value` line per key, sorted."""
    lines = []
    for section in sorted(config):
        for key in sorted(config[section]):
            lines.append(f"{section}.{key} = {_format_value(config[section][key])}")
    return "\n".join(lines)


def _mask_value(key: str, value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    return "***" if _SECRET_KEY.search(key) else value


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _deep_copy(table: dict) -> dict:
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in table.items()}


def _deep_merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
