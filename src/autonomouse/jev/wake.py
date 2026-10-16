"""Jev in the run: one request per wake for `go` and each ready card's `size`; the width is
counted from the cards, and each card's tier from its size.

`autonomouse run` calls :func:`consult` when `jev.enabled` is true, after it has picked the
project and every one of its ready cards and before any session starts. :func:`consult`:

1. builds the state (:func:`~autonomouse.jev.questions.card_state` over the whole board, the
   run records and the ready cards, and :func:`~autonomouse.jev.questions.go_state` with the
   sweep's findings: a wake picked for a finding alone has no card, and a finding is its work,
   sys-s22),
2. asks every question in one request: `go`, and one `size` per ready card,
3. records `go` as advice and never applies it: the wake starts whatever Jev answered, since
   `autonomouse run` starts a wake whenever it has work, no PAUSE file exists and no usage
   limit is in force (the controller's own checks, before this runs). The `go` entry keeps
   Jev's answer, `applied` null and the reason :data:`GO_ADVICE`. Each `size` answer that
   names one of the four sizes applies, whatever its confidence; a missing or malformed
   answer, or a request that failed (bad or missing key, network, timeout, unparsable
   response), keeps the card's own size. A failed request also marks the width entry
   `fallback`.
   Each applied size maps to its worker's tier through the config's `tiers`
   (:func:`~autonomouse.config.worker_tiers`); a size of `split` (larger than `m`) maps to no
   tier: the orchestrator splits the card and sizes its children,
4. builds the batch in pick order: every resumed card, then the ready cards. It counts the
   width (:func:`width_rule`): every resumed and ready card, at most
   `ceilings.workers_per_wake`, the number of workers alive at once; the batch is the first
   `width` cards; the ready cards after them wait, and the orchestrator starts each
   whenever a slot is free (`docs/orchestrator.md`). Every ready card, started or
   waiting, gets its `size` answer. Whether two branches collide is the orchestrator's
   business while its workers run (its conflict check, `docs/orchestrator/conflict-check.md`),
   not Jev's,
5. returns a :class:`WakePlan` whose `entries` are the run record's `jev` list.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomouse.config import worker_tiers
from autonomouse.jev.client import JevError
from autonomouse.jev.client import ask as jev_ask
from autonomouse.jev.questions import (
    Verdict,
    base_state,
    card_state,
    decide_all,
    go,
    go_state,
    questions_body,
    size,
)

Ask = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class CardPlan:
    """What the run does with one ready card: the size applied (Jev's, or the card's own),
    and the worker's model and effort that size maps to in the config's `tiers`, both
    `None` when the size is `split` (larger than `m`: the orchestrator splits the card)."""

    card: str
    size: str
    model: str | None
    effort: str | None

    @property
    def split(self) -> bool:
        return self.size == SPLIT

    @property
    def tier(self) -> str | None:
        """`model/effort`, as the run record's `size` entry names it; `None` for a split."""
        return None if self.split else f"{self.model}/{self.effort}"

    @property
    def agent_type(self) -> str | None:
        """The Agent tool's `subagent_type` for this effort; `None` for a split."""
        return None if self.split else f"worker-{self.effort}"


@dataclass(frozen=True)
class WakePlan:
    """The applied answers for one wake, which always starts: Jev's `go` is advice.

    `width` is after the ceiling. `batch` is the ids of the cards this wake starts at once,
    resumed ones first, `width` of them at most; `waiting` the ids of the ready cards after
    them, in pick order, each of which the orchestrator starts whenever a slot is free.
    `cards` holds a plan for every ready card, started or waiting. `entries` is the run
    record's `jev` list: one entry for every answer.
    """

    width: int
    batch: list[str]
    waiting: list[str]
    cards: dict[str, CardPlan]
    entries: list[dict[str, Any]]


# The `size` answer that is larger than `m`, and the reason its run-record entry carries.
SPLIT = "split"
SPLIT_REASON = "larger than m: the orchestrator splits it"
WAITING_REASON = "waits for a free slot"
# The reason on every `go` entry: Jev's answer is recorded and never applied.
GO_ADVICE = "advice, never applied: the wake starts whenever it has work"


def consult(
    *,
    now: datetime,
    config: Mapping[str, Any],
    cards: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
    resume: list[str],
    ready: list[Mapping[str, Any]],
    findings: Iterable[Mapping[str, Any]] = (),
    ask: Ask = jev_ask,
    timeout: float = 30.0,
) -> WakePlan:
    """Ask Jev `go`, and `size` for each card in `ready`, in one request; count the width
    from the cards (:func:`width_rule`) and map each applied size to its tier.

    `now` is timezone-aware. `config` is the effective config (its `ceilings`, `tiers` and
    `wake.interval_hours` are read, the last naming the wake's own cadence in the `go`
    question); `cards` every card on the board and `runs` every run record, as JSON dicts;
    `resume` the ids of the in_progress cards the wake resumes (their tier is already on the
    card, so they get no per-card question); `ready` every ready card of the project, in
    pick order; `findings` the sweep's findings on the project's open pull requests
    (`autonomouse.sweep.Line.to_dict`), which the pick passes only when it found no card:
    `go` counts each as work (:func:`~autonomouse.jev.questions.go_state`, sys-s22).
    """
    runs = list(runs)
    ceilings = config["ceilings"]
    tiers = worker_tiers(config)
    state = card_state(
        go_state(base_state(now=now, config=config, cards=cards, runs=runs), findings), ready
    )
    per_card = {card["id"]: size(card["id"], card["size"]) for card in ready}
    go_entry = go(config["wake"]["interval_hours"])
    entries_asked = [go_entry] + list(per_card.values())
    try:
        answers = ask(state, questions_body(entries_asked), timeout=timeout)
        failure = None
    except (JevError, OSError, ValueError, KeyError) as exc:
        answers, failure = {}, f"Jev request failed: {exc!r}"
    verdicts = decide_all(entries_asked, answers)
    if failure is not None:
        # The request itself failed, not one answer: every size keeps the card's own size,
        # which is also the no-Jev answer, and `go` has no answer to record.
        verdicts = {
            k: Verdict(v.key, None, None, v.applied, failure) for k, v in verdicts.items()
        }

    picked = [card["id"] for card in ready]
    width_applied, width_source, width_reason = width_rule(
        resume=resume,
        ready=picked,
        cap=ceilings["workers_per_wake"],
        failure=failure,
    )
    batch = (resume + picked)[:width_applied]
    waiting = [card_id for card_id in picked if card_id not in batch]

    entries = [
        _go_entry(verdicts[go_entry.key]),
        {"question": "width", "card": None, "source": width_source, "answer": None,
         "confidence": None, "probabilities": None, "applied": str(width_applied),
         "reason": width_reason},
    ]
    plans: dict[str, CardPlan] = {}
    for card_id, size_q in per_card.items():
        sized = verdicts[size_q.key]
        own = None if sized.fallback_reason is None else f"the card's own size {sized.applied}"
        waits = WAITING_REASON if card_id in waiting else None
        if sized.applied == SPLIT:
            entries.append(_size_entry(card_id, sized, SPLIT, None,
                                       _join(own, SPLIT_REASON, waits)))
            plans[card_id] = CardPlan(card_id, SPLIT, None, None)
            continue
        model, effort = tiers[sized.applied]
        entries.append(
            _size_entry(card_id, sized, sized.applied, f"{model}/{effort}", _join(own, waits))
        )
        plans[card_id] = CardPlan(card_id, sized.applied, model, effort)

    return WakePlan(
        width=width_applied, batch=batch, waiting=waiting, cards=plans,
        entries=entries,
    )


def width_rule(
    *,
    resume: list[str],
    ready: list[str],
    cap: int,
    failure: str | None,
) -> tuple[int, str, str]:
    """The wake's width, the `source` of its run-record entry, and the count in words.

    Width = every resumed card plus every ready card, at most `cap`
    (`ceilings.workers_per_wake`, the number of workers alive at once); the ready cards past
    it wait for a free slot. `source` is `jev`, or `fallback` when the request itself failed
    (`failure`)."""
    counted = len(resume) + len(ready)
    width = min(counted, cap)
    parts = [f"every resumed and ready card, {width} of {counted}"]
    if counted > cap:
        parts.append(f"ceilings.workers_per_wake is {cap}")
    if failure is not None:
        return width, "fallback", _join(failure, "; ".join(parts))
    return width, "jev", "; ".join(parts)


def _go_entry(verdict: Verdict) -> dict[str, Any]:
    """The `go` entry: Jev's answer as advice, `applied` null, `source` `jev` whether or not
    the answer was usable (`go` has no fallback); `reason` says why an answer is missing or
    unusable, then :data:`GO_ADVICE`."""
    return {**_entry("go", None, verdict, None, GO_ADVICE), "source": "jev"}


def _entry(
    question: str, card: str | None, verdict: Verdict, applied: str | None, note: str | None
) -> dict[str, Any]:
    return {
        "question": question,
        "card": card,
        "source": "jev" if verdict.fallback_reason is None else "fallback",
        "answer": verdict.pick,
        "confidence": verdict.confidence,
        "probabilities": None if verdict.probabilities is None else dict(verdict.probabilities),
        "applied": applied,
        "reason": _join(verdict.fallback_reason, note),
    }


def _size_entry(
    card: str, verdict: Verdict, applied: str | None, tier: str | None, note: str | None
) -> dict[str, Any]:
    """A `size` entry: `applied` is the size applied and `tier` the `model/effort` it maps to
    in the config's `tiers`, both null when nothing is done with the card this wake."""
    return {**_entry("size", card, verdict, applied, note), "tier": tier}


def _join(*reasons: str | None) -> str | None:
    kept = [r for r in reasons if r]
    return "; ".join(kept) if kept else None
