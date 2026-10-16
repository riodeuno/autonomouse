"""An agent's cost, priced from the token usage of its own transcript messages.

Each assistant message in a transcript carries `message.usage`: input, output, cache-read
and cache-write tokens, the cache writes split into 5-minute and 1-hour ones. Claude Code
writes one record per content block of a streamed message, all with the same `message.id`
and a usage that grows until the message ends, so a message counts once, with the usage of
its last record.

A message is priced by its own `message.model` at the list rates below, in US dollars per
million tokens. They reproduce the `costUSD` that `claude` prints per model in
`result.modelUsage`. A message with no tokens costs nothing whatever its model (Claude
Code's `<synthetic>` messages). A message with tokens on a model not in the table has no
price: its agent's cost is unknown, and the model is named so the table can be extended.
Web search requests are not priced; the run's total cost still holds them.
"""

from __future__ import annotations

from typing import Any

# USD per million tokens: input, output, cache read, cache write (5 min), cache write (1 h).
RATES: dict[str, dict[str, float]] = {
    "claude-opus-5": {
        "input": 5.0,
        "output": 25.0,
        "cache_read": 0.5,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
    },
    "claude-opus-5-5": {
        "input": 4.0,
        "output": 20.0,
        "cache_read": 0.2,
        "cache_write_5m": 5.0,
        "cache_write_1h": 8.0,
    },
    "claude-sonnet-5": {
        "input": 2.0,
        "output": 10.0,
        "cache_read": 0.2,
        "cache_write_5m": 2.5,
        "cache_write_1h": 4.0,
    },
    "claude-fable-5": {
        "input": 10.0,
        "output": 50.0,
        "cache_read": 1.0,
        "cache_write_5m": 12.5,
        "cache_write_1h": 20.0,
    },
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_read": 0.1,
        "cache_write_5m": 1.25,
        "cache_write_1h": 2.0,
    },
    "claude-fable-5-1": {
        "input": 10.0,
        "output": 50.0,
        "cache_read": 0.25,
        "cache_write_5m": 12.5,
        "cache_write_1h": 20.0,
    },
}


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def tokens(usage: dict[str, Any]) -> dict[str, int]:
    """A message's usage as the token counts the rates price. A cache write that the
    usage does not split into 5-minute and 1-hour is priced as a 5-minute one."""
    split = usage.get("cache_creation")
    split = split if isinstance(split, dict) else {}
    written = _int(usage.get("cache_creation_input_tokens"))
    one_hour = _int(split.get("ephemeral_1h_input_tokens"))
    return {
        "input": _int(usage.get("input_tokens")),
        "output": _int(usage.get("output_tokens")),
        "cache_read": _int(usage.get("cache_read_input_tokens")),
        "cache_write_5m": max(written - one_hour, 0),
        "cache_write_1h": one_hour,
    }


def _base_model(model: str) -> str:
    """`claude-sonnet-5-20260401` -> `claude-sonnet-5`; `claude-opus-5[1m]` ->
    `claude-opus-5`; `claude-fable-5-1` stays itself."""
    bare = model.split("[", 1)[0]
    head, _, tail = bare.rpartition("-")
    return head if tail.isdigit() and len(tail) == 8 else bare


def rates_of(model: str) -> dict[str, float] | None:
    """The rates a model's messages are priced at; None for a model not in the table."""
    return RATES.get(_base_model(model))


def messages_usage(records: list[dict[str, Any]]) -> list[tuple[str, dict[str, int]]]:
    """Each assistant message's model and token counts, once per message: the usage of
    its last record."""
    last: dict[str, tuple[str, dict[str, Any]]] = {}
    for i, record in enumerate(records):
        message = record.get("message") or {}
        usage = message.get("usage")
        if record.get("type") != "assistant" or not isinstance(usage, dict):
            continue
        key = message.get("id") or record.get("requestId") or record.get("uuid")
        last[key or f"#{i}"] = (message.get("model") or "", usage)
    return [(model, tokens(usage)) for model, usage in last.values()]


def cost_by_model(records: list[dict[str, Any]]) -> dict[str, float | None]:
    """The cost of the messages in `records` in USD, per model, in the order the models
    first cost something; None for a model with tokens and no rates."""
    costs: dict[str, float | None] = {}
    for model, counts in messages_usage(records):
        if not any(counts.values()):
            continue
        name = model or "(no model)"
        rates = rates_of(model)
        if rates is None:
            costs[name] = None
        elif costs.get(name, 0.0) is not None:
            usd = sum(counts[k] * rates[k] for k in counts) / 1_000_000
            costs[name] = (costs.get(name) or 0.0) + usd
    return costs
