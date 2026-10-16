"""Pricing an agent from the token usage of its own transcript messages."""

import pytest

from autonomouse.behaviour.price import cost_by_model


def agent_cost(records):
    """The total cost, None when any model with tokens has no rates, and those models."""
    costs = cost_by_model(records)
    unpriced = [model for model, usd in costs.items() if usd is None]
    return (None if unpriced else sum(costs.values(), 0.0)), unpriced


def _assistant(msg_id, model, usage):
    return {"type": "assistant", "message": {"id": msg_id, "model": model, "usage": usage}}


def test_a_streamed_message_counts_once_with_its_last_usage():
    records = [
        _assistant("m1", "claude-sonnet-5", {"output_tokens": 3}),
        _assistant("m1", "claude-sonnet-5", {"output_tokens": 100_000}),
        {"type": "user", "message": {"content": "x", "usage": {"output_tokens": 9}}},
    ]
    assert agent_cost(records) == (pytest.approx(1.0), [])  # 100k at $10/MTok


def test_cache_writes_are_priced_by_their_duration():
    usage = {
        "cache_creation_input_tokens": 300_000,
        "cache_creation": {"ephemeral_1h_input_tokens": 100_000},
    }
    cost, _ = agent_cost([_assistant("m1", "claude-opus-5", usage)])
    assert cost == pytest.approx(0.2 * 6.25 + 0.1 * 10.0)


def test_models_with_a_context_or_date_suffix_use_their_base_rates():
    records = [
        _assistant("m1", "claude-opus-5[1m]", {"input_tokens": 1_000_000}),
        _assistant("m2", "claude-sonnet-5-20260401", {"input_tokens": 1_000_000}),
    ]
    assert agent_cost(records) == (pytest.approx(7.0), [])


def test_a_model_without_rates_leaves_the_cost_unknown_unless_it_used_no_tokens():
    synthetic = _assistant("m1", "<synthetic>", {"input_tokens": 0})
    assert agent_cost([synthetic]) == (0.0, [])
    unknown = _assistant("m2", "claude-new-9", {"output_tokens": 1})
    assert agent_cost([synthetic, unknown]) == (None, ["claude-new-9"])


def test_fable_messages_are_priced():
    usage = {"input_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000}
    assert agent_cost([_assistant("m1", "claude-fable-5", usage)]) == (pytest.approx(11.0), [])
    assert agent_cost([_assistant("m1", "claude-fable-5-1", usage)]) == (
        pytest.approx(10.25),
        [],
    )


def test_claude_haiku_4_5_messages_are_priced_as_claude_printed_them():
    """Run 20260927-023843-e5a3's result: claude printed costUSD 1.0002582 for these
    claude-haiku-4-5-20251001 tokens, all cache writes 5-minute ones. A 1-hour write
    costs twice the input rate, as on every other model."""
    usage = {
        "input_tokens": 596,
        "output_tokens": 81699,
        "cache_read_input_tokens": 2245072,
        "cache_creation_input_tokens": 293328,
        "cache_creation": {"ephemeral_5m_input_tokens": 293328, "ephemeral_1h_input_tokens": 0},
    }
    assert agent_cost([_assistant("m1", "claude-haiku-4-5-20251001", usage)]) == (
        pytest.approx(1.0002582),
        [],
    )
    one_hour = {"cache_creation_input_tokens": 1_000_000, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}
    assert agent_cost([_assistant("m1", "claude-haiku-4-5", one_hour)]) == (pytest.approx(2.0), [])


def test_claude_opus_5_5_messages_are_priced_as_claude_printed_them():
    """Run 20260923-092038-e31f's record: claude printed costUSD 0.6625358 for these
    claude-opus-5-5[1m] tokens, all cache writes 5-minute ones."""
    usage = {
        "input_tokens": 28,
        "output_tokens": 7045,
        "cache_read_input_tokens": 667694,
        "cache_creation_input_tokens": 77597,
        "cache_creation": {"ephemeral_5m_input_tokens": 77597, "ephemeral_1h_input_tokens": 0},
    }
    assert agent_cost([_assistant("m1", "claude-opus-5-5[1m]", usage)]) == (
        pytest.approx(0.6625358),
        [],
    )
    one_hour = {"cache_creation_input_tokens": 1_000_000, "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000}}
    assert agent_cost([_assistant("m1", "claude-opus-5-5", one_hour)]) == (pytest.approx(8.0), [])
