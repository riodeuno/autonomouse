"""A card's size fixes its worker's tier (sys-i18, the human's decision of 2026-09-23).

Jev answers `size` (xs, s, m, or split: larger than m) for each ready card, never `tier`. `autonomouse run` applies
Jev's size whenever it names one of the four sizes, whatever its confidence (sys-j6), and the
card's own size when the answer is missing or malformed, maps the
size to `model/effort` through the config's `tiers` table, records both in the run record's
`size` entry, and passes the tier to the orchestrator's prompt. A fake Jev stands in for the
real one; the board and claude fakes come from test_run_controller.
"""

import copy

import pytest

from autonomouse.board.commands import card_edit
from autonomouse.board.runs import load_run
from autonomouse.board.store import commit_board
from autonomouse.config import DEFAULTS, format_config, load_config, worker_tiers
from autonomouse.jev.client import ChoiceAnswer, NoulAnswer
from autonomouse.jev.questions import size
from test_run_controller import _autonomouse_run, _prompt, _value, env  # noqa: F401
from test_run_jev import _entry, _run, jev  # noqa: F401  (jev is a fixture)


def _size_card(env, card_id: str, card_size: str) -> None:  # noqa: F811
    card_edit(env.root, card_id, size=card_size, note="n")
    commit_board(env.root, f"{card_id} sized {card_size}")


def _answers(**sizes: ChoiceAnswer) -> dict:
    """`go` go; `sizes` holds Jev's size answers, by card id."""
    answers = {"go": ChoiceAnswer("go", {}, 0.9)}
    answers.update({f"size:{card_id}": a for card_id, a in sizes.items()})
    return answers


# ---------------------------------------------------------------------------- the map


def test_the_default_map_is_the_humans():
    assert DEFAULTS["tiers"] == {"xs": "sonnet/xhigh", "s": "opus/xhigh", "m": "fable/high"}
    assert worker_tiers(DEFAULTS) == {
        "xs": ("sonnet", "xhigh"),
        "s": ("opus", "xhigh"),
        "m": ("fable", "high"),
    }


def test_config_show_prints_the_map(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    lines = format_config(config).splitlines()
    assert "tiers.xs = sonnet/xhigh" in lines
    assert "tiers.s = opus/xhigh" in lines
    assert "tiers.m = fable/high" in lines


@pytest.mark.parametrize(
    ("table", "words"),
    [
        ({"xs": "sonnet/high"}, "tiers.xs is 'sonnet/high', below the sonnet/xhigh floor"),
        ({"s": "opus/medium"}, "tiers.s must be <model>/<effort>"),
        ({"m": "gpt/high"}, "tiers.m must be <model>/<effort>"),
        ({"m": "fable"}, "tiers.m must be <model>/<effort>"),
        ({"l": "fable/xhigh"}, "tiers.l is not a card size"),
    ],
)
def test_a_map_below_the_floor_or_off_the_sizes_is_refused(table, words):
    config = copy.deepcopy(DEFAULTS)
    config["tiers"].update(table)
    with pytest.raises(ValueError, match="tiers") as refused:
        worker_tiers(config)
    assert words in str(refused.value)


# ---------------------------------------------------------------------------- the question


@pytest.mark.parametrize("confidence", [0.8, 0.3, 0.2])
def test_jevs_size_is_applied_whatever_its_confidence(confidence):
    question = size("sys-b4", "xs")
    assert question.floor is None
    verdict = question.decide(ChoiceAnswer("m", {"m": 0.8}, confidence))
    assert (verdict.applied, verdict.fallback_reason) == ("m", None)


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (None, "no answer"),
        (ChoiceAnswer("l", {"l": 0.9}, 0.9), "pick 'l' is not one of"),
        (NoulAnswer(0.9), "expected a choice answer"),
    ],
)
def test_an_unusable_size_answer_falls_back_to_the_cards_own_size(answer, reason):
    verdict = size("sys-b4", "xs").decide(answer)
    assert verdict.applied == "xs"
    assert reason in verdict.fallback_reason


# ---------------------------------------------------------------------------- the wake


@pytest.mark.parametrize(
    ("card_size", "model", "effort"),
    [("xs", "sonnet", "xhigh"), ("s", "opus", "xhigh"), ("m", "fable", "high")],
)
def test_a_card_runs_at_the_tier_its_size_maps_to(env, jev, card_size, model, effort):  # noqa: F811
    # Jev gives no size answer, so each card keeps its own size.
    _size_card(env, "hi-a1", card_size)
    _, settings = jev(_answers())
    assert env.wake(jev=settings) == 0
    entry = _entry(_run(env), "size", "hi-a1")
    assert (entry["applied"], entry["tier"]) == (card_size, f"{model}/{effort}")
    assert entry["source"] == "fallback"
    assert entry["reason"] == f"no answer; the card's own size {card_size}"
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert (
        f"Start hi-a1 with tier {model}/{effort} (size {card_size}): card start --model "
        f"{model} --effort {effort} --agent-type worker-{effort}, and spawn its worker with "
        f"Agent model {model} and subagent_type worker-{effort}."
    ) in prompt


def test_a_jev_size_overrides_the_cards_size(env, jev):  # noqa: F811
    _size_card(env, "hi-a1", "xs")
    fake, settings = jev(_answers(**{"hi-a1": ChoiceAnswer("m", {"s": 0.2, "m": 0.8}, 0.8)}))
    assert env.wake(jev=settings) == 0
    _, questions = fake.calls[0]
    assert "tier" not in " ".join(questions)  # Jev is asked the size, never the tier
    assert _entry(_run(env), "size", "hi-a1") == {
        "question": "size", "card": "hi-a1", "source": "jev", "answer": "m",
        "confidence": 0.8, "probabilities": {"s": 0.2, "m": 0.8}, "applied": "m",
        "tier": "fable/high", "reason": None,
    }
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Start hi-a1 with tier fable/high (size m): card start --model fable" in prompt
    assert "hi-a1 m fable/high" in _value(env.lines, "jev")


def test_a_jev_size_at_confidence_0_2_is_applied(env, jev):  # noqa: F811
    """sys-j6: before, a size below the 0.5 floor kept the card's own size. Now any size Jev
    names applies; the orchestrator may still override it (docs/orchestrator/tier-rubric.md)."""
    _size_card(env, "hi-a1", "m")
    _, settings = jev(_answers(**{
        "hi-a1": ChoiceAnswer("xs", {"xs": 0.2}, 0.2),
        "hi-a2": ChoiceAnswer("s", {"s": 0.9}, 0.9),
    }))
    assert env.wake(jev=settings) == 0
    entry = _entry(_run(env), "size", "hi-a1")
    assert (entry["source"], entry["answer"], entry["applied"], entry["tier"]) == (
        "jev", "xs", "xs", "sonnet/xhigh"
    )
    assert entry["reason"] is None
    assert not [line for line in env.lines if line.startswith("jev_fallback=size")]
    prompt = _prompt((env.fake_dir / "args").read_text().splitlines())
    assert "Start hi-a1 with tier sonnet/xhigh (size xs)" in prompt


def test_the_human_changes_the_map_in_the_config(env, monkeypatch, capsys):  # noqa: F811
    # No Jev key: the request fails before any network call, and each card keeps its own
    # size, `s`, which the config maps to fable/xhigh instead of the default opus/xhigh.
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    _, lines = _autonomouse_run(
        env, monkeypatch, capsys, config='[jev]\nenabled = true\n\n[tiers]\ns = "fable/xhigh"\n'
    )
    run = load_run(env.root, _value(lines, "run_id"))
    assert _entry(run, "size", "hi-a1")["tier"] == "fable/xhigh"
    assert "Start hi-a1 with tier fable/xhigh (size s)" in _prompt(
        (env.fake_dir / "args").read_text().splitlines()
    )


def test_a_map_below_the_floor_fails_the_wake_before_it_starts(env, monkeypatch, capsys):  # noqa: F811
    from autonomouse import cli

    config_dir = env.tmp / "config-dir"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('[tiers]\nxs = "sonnet/high"\n')
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setattr("sys.argv", ["autonomouse", "run"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code != 0
    err = capsys.readouterr().err
    assert "tiers.xs is 'sonnet/high', below the sonnet/xhigh floor" in err
    assert str(config_dir / "config.toml") in err
    assert not (env.fake_dir / "args").exists()
    # sys-j5: the refusal has a wake record of its own, with no session, naming the key.
    [record] = [
        r for r in (load_run(env.root, p.stem) for p in (env.root / "runs").glob("*.json"))
        if r.refusals
    ]
    assert (record.kind.value, record.session_id, record.status.value) == (
        "wake", None, "stopped"
    )
    [refusal] = record.refusals
    assert refusal["command"] == "run" and "tiers.xs" in refusal["reason"]
