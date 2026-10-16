"""The questions the run asks Jev, the state each one reads, and what code does with the answer.

Wake-level question: ``go(interval_hours)`` (go / wait_30m / skip), which names the wake's own
cadence (``wake.interval_hours``) instead of a fixed number, so a project that changes it gets a
question built on its own schedule. One question per ready card:
``size(card_id, own_size)`` (xs / s / m / split), the card's effort and complexity. The size
fixes the worker's tier, its model and reasoning effort, through the config's ``tiers`` map
(:func:`autonomouse.config.worker_tiers`); Jev is never asked for a tier. ``split`` means larger
than ``m``: the goal needs more than one worker turn, so the orchestrator splits the card. After
a failed session: ``RECOVER`` (retry / next_wake / raise). Before ``autonomouse plan step`` starts
or resumes a planner session: ``PLANNER_SESSION`` (complex / simple / collect-user-intent), which
fixes the session's model and reasoning effort through the config's ``planner`` table
(:func:`autonomouse.config.planner_tiers`) and falls back to ``complex``.

Whether two cards conflict, and which ready cards start at once, are the orchestrator's
decisions, never Jev's (the human's rule, 2026-09-23): :mod:`autonomouse.jev.wake` counts the
wake's width from the cards, every resumed and ready card, at most ``ceilings.workers_per_wake``
of them alive at once, and the orchestrator starts each of the rest whenever a slot is free.

Every question is a Jev ``choice`` question with a rubric per option.
:meth:`CatalogueQuestion.decide` turns Jev's answer into the option code applies: Jev's pick when
the answer names one of the options (and, for a question with a confidence floor, is at or above
it), otherwise the question's fallback, with the reason recorded on the :class:`Verdict`. Of the
wake's questions only ``recover`` has a floor; below it, or with no usable answer, it falls back
to ``retry``, a new session from the notes. ``planner_session`` has its own floor, below which it
falls back to ``complex``. ``size`` has no floor: any size Jev names applies, whatever its
confidence, and only a missing or malformed answer keeps the card's own size. ``go`` has no
floor and no fallback: :mod:`autonomouse.jev.wake` records its answer as advice and never
applies it, since a wake starts whenever a card is ready, no PAUSE file exists and no usage
limit is in force (the controller's own checks, :mod:`autonomouse.controller`). The caller
applies the config ceiling (at most ``workers_per_wake`` workers alive at once) after
``decide``.

State builders:

- :func:`base_state` -- what every question reads: time and weekday, config ceilings,
  per-project stack counts, ready cards, the last five run records that started a session,
  each with its :func:`run_outcome`, and how many wakes after the newest of them started none.
- :func:`card_state` -- the base plus the whole of each judged card; ``size`` reads this.
- :func:`go_state` -- the base plus ``sweep_findings``, the open pull requests autonomouse
  opened that need work; ``go`` reads this and counts a finding as work, as it counts a ready
  card (sys-s22).
- :func:`recover_state` -- the base plus the failure: error, attempts, reset time.
- :func:`planner_state` -- one planning thread and the messages its next session answers;
  ``planner_session`` reads this, and nothing of the base.

Questions point at state paths in backticks, as the Jev docs recommend, so several per-card
questions can share one request and one state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from autonomouse.jev.client import Answer, ChoiceAnswer, ChoiceQuestion

STACKS = ("backlog", "todo", "in_progress", "decision_needed", "done", "cancelled")
LAST_RUNS = 5
RESULT_TEXT_TAIL = 600

# The human's rule for every tier (2026-09-22), word for word. Jev's size question quotes it, and
# so do docs/orchestrator/tier-rubric.md and docs/planner.md; decision B retired the
# suite's check that the docs quote this exact text, so only test_jev_questions.py checks
# Jev's own question against this constant.
TIER_RULE = (
    "delegate to capable orchestrators and workers; do not cheap out on their capabilities, do "
    "not underestimate the tasks, do not overestimate what the agents can do, and do not "
    "overspend."
)

# A card's size is its effort and complexity, judged by the hardest decision inside its goal
# (the human's decision, 2026-09-23); the config's `tiers` maps each size to the worker's model
# and reasoning effort. A card larger than `m` is `split`: the orchestrator splits it (sys-i29).
SIZE_DESCRIPTIONS = {
    "xs": (
        "Really small and straightforward: the goal names the exact edit, a few lines, fully "
        "specified, with nothing to decide and nothing to find out -- a typo, a changed "
        "constant, a rename, a moved file. Never new behaviour: a new command, check or refusal "
        "is at least `s`."
    ),
    "s": (
        "Most work: decisions inside a clear goal. New behaviour -- a command, a validation "
        "rule, a function or a whole module -- whose edge cases, error handling, refusals or "
        "output shape the worker decides; a change across several files with design choices; "
        "or a bounded search, a fix or a question whose cause or answer lies in one place or "
        "among a few named candidates."
    ),
    "m": (
        "The hardest decision is hard: the goal must be interpreted, an architecture chosen "
        "between real alternatives, or a cause found that could lie in any of several parts "
        "of the system -- an intermittent failure, a race, a problem nobody can yet explain -- "
        "before anything is built. Work that needs the largest model, and still one "
        "deliverable that one worker finishes in one turn."
    ),
    "split": (
        "Larger than `m`: the goal needs more than one worker turn, however easy or hard each "
        "part is. It bundles several independent deliverables -- different commands, screens, "
        "installers or subsystems that could each be their own pull request -- or rewrites "
        "many parts of the system at once, or its `test` needs several separate setups. The "
        "orchestrator splits such a card into children and sizes each of them."
    ),
}


# ---------------------------------------------------------------------------- state


# Ceilings no Jev question depends on, left out of the state: `resume_tokens` bounds
# which dead session `autonomouse run` resumes, and `retries_per_wake` how many times one
# wake starts a new session after `recover` answered `retry`; both are choices made in code,
# never by Jev.
NOT_FOR_JEV_CEILINGS = frozenset({"resume_tokens", "retries_per_wake"})


def base_state(
    *,
    now: datetime,
    config: Mapping[str, Any],
    cards: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """The facts every question reads.

    ``now`` must be timezone-aware; the state carries it, and its weekday, in UTC. ``cards`` is
    every card on the board (card JSON dicts), across projects, so dependencies in other
    projects resolve. ``runs`` is any number of run records. ``last_runs`` keeps the five
    most recently started that started a session, newest first, trimmed by
    :func:`_run_summary`, each with its :func:`run_outcome` read against ``cards``; a wake
    refused before it started a session (sys-j5) or skipped by Jev is not one of them, so a
    string of such wakes never pushes the last session that ran out of Jev's view.
    ``wakes_since_last_session`` counts those wakes started after the newest session in
    ``last_runs`` (every run, when none started a session).
    """
    if now.tzinfo is None:
        raise ValueError("base_state needs a timezone-aware `now`")
    cards = list(cards)
    now = now.astimezone(UTC)
    newest_first = sorted(runs, key=_started_at, reverse=True)
    sessions = [r for r in newest_first if r.get("session_id")]
    since = _started_at(sessions[0]) if sessions else ""
    return {
        "now": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weekday": now.strftime("%A"),
        "ceilings": {
            k: v for k, v in config["ceilings"].items() if k not in NOT_FOR_JEV_CEILINGS
        },
        "stack_counts": _stack_counts(cards),
        "ready_cards": _ready_cards(cards),
        "last_runs": [_run_summary(r, cards) for r in sessions[:LAST_RUNS]],
        "wakes_since_last_session": sum(
            1 for r in newest_first if not r.get("session_id") and _started_at(r) > since
        ),
    }


def card_state(base: Mapping[str, Any], judged: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """``base`` plus each judged card whole, keyed by id.

    The size descriptions stay in the ``size`` question's own rubric, not in this shared state:
    recorded live (sys-i18), Jev's confidence on a one-module card sized ``m`` fell from 0.7
    to 0.39 when the state carried them."""
    return {**base, "cards": {card["id"]: dict(card) for card in judged}}


def go_state(base: Mapping[str, Any], findings: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """``base`` plus ``sweep_findings``: for each open pull request autonomouse opened that
    needs work, the ``card`` whose pull request it is (``-`` when no card names it), the
    ``pr`` URL and what is ``wrong`` with it, in words; empty when there is none.

    ``findings`` are the lines of ``autonomouse github sweep`` that are findings, as
    ``autonomouse.sweep.Line.to_dict`` writes them: ``autonomouse run`` passes them when its pick
    found no card and the sweep found a finding (sys-s8). ``go`` counts a finding as work, as
    it counts a ready card, so such a wake can start and its orchestrator works the findings
    (sys-s22)."""
    return {
        **base,
        "sweep_findings": [
            {"card": line["card_id"], "pr": line["url"], "wrong": _wrong(line)}
            for line in findings
        ],
    }


def _wrong(line: Mapping[str, Any]) -> str:
    """What is wrong with one sweep finding, in words: a conflict with its base branch or
    the default branch, failed runs, unanswered review comments, or several of them."""
    wrong = []
    if line["mergeable"] == "CONFLICTING":
        wrong.append("it conflicts with its base branch")
    if line["conflicts"]:
        wrong.append(f"it conflicts with the default branch in {', '.join(line['conflicts'])}")
    if line["runs"] == "fail":
        wrong.append("its runs failed")
    if line["unanswered"]:
        wrong.append(f"{line['unanswered']} review comment(s) unanswered")
    return ", ".join(wrong)


def recover_state(
    base: Mapping[str, Any],
    *,
    error: str,
    attempts: int,
    reset_at: str | None,
) -> dict[str, Any]:
    """``base`` plus the failure being recovered from.

    ``error`` is the error text as seen (result text, exception, HTTP body); ``attempts`` is how
    many times this work has been tried and failed, this failure included; ``reset_at`` is the
    ISO time a usage limit resets, or ``None`` when no limit is involved.
    """
    return {
        **base,
        "failure": {"error": error, "attempts": attempts, "reset_at": reset_at},
    }


def _stack_counts(cards: list[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for card in cards:
        per_project = counts.setdefault(card["project"], dict.fromkeys(STACKS, 0))
        per_project[card["stack"]] += 1
    return counts


def _ready_cards(cards: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Todo cards whose every dependency is done, in pick order within each project."""
    stack_of = {card["id"]: card["stack"] for card in cards}
    ready = [
        card
        for card in cards
        if card["stack"] == "todo"
        and all(stack_of.get(dep) == "done" for dep in card.get("depends_on", []))
    ]
    ready.sort(key=lambda c: (c["project"], c["position"]))
    return [
        {
            "id": c["id"],
            "project": c["project"],
            "title": c["title"],
            "size": c["size"],
            "goal": c["goal"],
        }
        for c in ready
    ]


def _started_at(run: Mapping[str, Any]) -> str:
    return run.get("started_at") or ""


def _run_summary(run: Mapping[str, Any], cards: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """A run record without its Jev log, with only the telling parts of the session result,
    and its :func:`run_outcome`.

    The Jev log is dropped: it is Jev's own past output, not a fact about the world. The
    ``result`` keeps its status fields and the tail of its final text, where a usage-limit
    message appears.
    """
    summary = {k: v for k, v in run.items() if k not in ("jev", "result")}
    result = run.get("result")
    if isinstance(result, Mapping):
        kept = {
            k: result[k]
            for k in ("is_error", "stop_reason", "num_turns", "total_cost_usd")
            if k in result
        }
        text = result.get("result")
        if isinstance(text, str):
            kept["result_tail"] = text[-RESULT_TEXT_TAIL:]
        summary["result"] = kept
    else:
        summary["result"] = result
    summary.update(run_outcome(run, cards))
    return summary


# ---------------------------------------------------------------------------- run outcomes
#
# What happened in a run, read from the session's own facts only: whether it started a session,
# the session's printed result, its status and end reason. The run's summary text and the
# controller's own work after the session (committing the board, sending
# notifications) are not part of the outcome.


def session_end(run: Mapping[str, Any]) -> tuple[str, str | None]:
    """How the run's session ended: ``("none", None)`` when it started no session,
    ``("running", None)``, ``("failed", detail)`` or ``("clean", None)``.

    A session failed when it printed no result once it ended (crashed, taken over, or exited
    without JSON) or printed one with ``is_error``. A session with no result whose own
    ``SessionEnd`` hook recorded ``end_reason: completed`` (its last turn finished) ended
    cleanly: the result was lost after the session, not by it. Nothing the controller did after
    the session counts.
    """
    if not run.get("session_id"):
        return "none", None
    result = run.get("result")
    if not isinstance(result, Mapping):
        if run.get("status") == "alive" and not run.get("ended_at"):
            return "running", None
        if run.get("end_reason") == "completed":
            return "clean", None
        detail = f"no result; status {run.get('status')}"
        if run.get("end_reason"):
            detail += f", end_reason {run['end_reason']}"
        return "failed", detail
    if result.get("is_error"):
        detail = f"is_error; stop_reason {result.get('stop_reason')}"
        if run.get("end_reason"):
            detail += f", end_reason {run['end_reason']}"
        return "failed", detail
    return "clean", None


def needed_human(
    run: Mapping[str, Any], cards: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The decisions cards asked the human for during the run, each with its ``answered_at``.

    A card needed the human in this run when its history moves it to ``decision_needed`` under
    the run's id; the decisions are the card's own, asked between the run's start and that
    move. ``decision`` is ``None`` when the move names no decision the card holds.
    """
    run_id, started = run.get("id"), _started_at(run)
    found: list[dict[str, Any]] = []
    for card in cards:
        for move in card.get("history") or []:
            if move.get("run_id") != run_id or move.get("to") != "decision_needed":
                continue
            asked = [
                d
                for d in card.get("decisions") or []
                if started <= (d.get("asked_at") or "") <= (move.get("at") or "")
            ]
            found += [
                {
                    "card": card["id"],
                    "decision": d["id"],
                    "asked_at": d.get("asked_at"),
                    "answered_at": d.get("answered_at"),
                }
                for d in asked
            ] or [{"card": card["id"], "decision": None, "asked_at": None, "answered_at": None}]
    return found


def run_outcome(run: Mapping[str, Any], cards: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The run's ``outcome`` in words, and ``needed_human`` when a card asked the human.

    A session run: ``session ended cleanly``, ``session failed (<detail>)`` or ``session still
    running`` (:func:`session_end`); when a card needed the human in it, the outcome says so
    and whether every decision asked has since been answered, and ``needed_human`` lists
    them with their ``answered_at`` (:func:`needed_human`). :func:`base_state` passes only runs
    that started a session; a wake that started none is counted in
    ``wakes_since_last_session`` instead (sys-t40).
    """
    state, detail = session_end(run)
    outcome = {
        "running": "session still running",
        "failed": f"session failed ({detail})",
        "clean": "session ended cleanly",
    }[state]
    asked = needed_human(run, cards)
    if not asked:
        return {"outcome": outcome}
    if all(d["answered_at"] for d in asked):
        outcome += "; a card needed the human, and the decision has since been answered"
    else:
        outcome += "; a card needed the human, and a decision is still unanswered"
    return {"outcome": outcome, "needed_human": asked}


# ---------------------------------------------------------------------------- deciding


@dataclass(frozen=True)
class Verdict:
    """What code does with one answer.

    ``pick`` and ``confidence`` are Jev's (``None`` when there was no usable answer);
    ``applied`` is the option to act on (``None`` when the entry has no fallback of its own and
    Jev's answer was unusable); ``fallback_reason`` is ``None`` when Jev's pick was
    applied, otherwise why the fallback was. ``probabilities`` is Jev's probability per option,
    kept whenever Jev gave a choice answer (a pick below a floor included), ``None`` otherwise,
    so a low-confidence answer can be read afterwards in the run record.
    """

    key: str
    pick: str | None
    confidence: float | None
    applied: str | None
    fallback_reason: str | None
    probabilities: Mapping[str, float] | None = None


@dataclass(frozen=True)
class CatalogueQuestion:
    """One catalogue entry: the Jev question under ``key``, its confidence floor, and its
    fallback.

    ``floor`` is ``None`` for a question with no floor: any pick that names one of its options
    applies, whatever its confidence (``go``, ``size``). ``fallback`` maps Jev's pick (or
    ``None`` when there is no usable pick) to the option applied instead. ``None`` means the
    entry has no fallback: an unusable answer gives a verdict whose ``applied`` is ``None``
    (``go``, whose answer is advice the wake never applies).
    """

    key: str
    question: ChoiceQuestion
    floor: float | None
    fallback: Callable[[str | None], str] | None

    @property
    def options(self) -> tuple[str, ...]:
        return tuple(self.question.criteria)

    def decide(self, answer: Answer | None) -> Verdict:
        if answer is None:
            return self._fall_back(None, None, "no answer")
        if not isinstance(answer, ChoiceAnswer):
            return self._fall_back(None, None, f"expected a choice answer, got {answer!r}")
        probabilities = dict(answer.probabilities)
        if answer.pick not in self.options:
            return self._fall_back(
                None,
                answer.confidence,
                f"pick {answer.pick!r} is not one of {self.options}",
                probabilities,
            )
        if self.floor is not None and answer.confidence < self.floor:
            return self._fall_back(
                answer.pick,
                answer.confidence,
                f"confidence {answer.confidence:.2f} below floor {self.floor:.2f}",
                probabilities,
            )
        return Verdict(
            self.key, answer.pick, answer.confidence, answer.pick, None, probabilities
        )

    def _fall_back(
        self,
        pick: str | None,
        confidence: float | None,
        reason: str,
        probabilities: Mapping[str, float] | None = None,
    ) -> Verdict:
        applied = self.fallback(pick) if self.fallback else None
        return Verdict(self.key, pick, confidence, applied, reason, probabilities)


def questions_body(entries: Iterable[CatalogueQuestion]) -> dict[str, ChoiceQuestion]:
    """The ``questions`` argument for :func:`autonomouse.jev.client.ask`."""
    return {entry.key: entry.question for entry in entries}


def decide_all(
    entries: Iterable[CatalogueQuestion], answers: Mapping[str, Answer]
) -> dict[str, Verdict]:
    """One verdict per entry; an entry missing from ``answers`` falls back."""
    return {entry.key: entry.decide(answers.get(entry.key)) for entry in entries}


def _always(option: str) -> Callable[[str | None], str]:
    return lambda _pick: option


# ---------------------------------------------------------------------------- the catalogue
#
# Of the wake's questions only `recover` has a floor, which follows the Jev confidence guide
# (actions that start spend sit at 0.6); `planner_session`, below, has its own. `go` is advice
# and `size` is Jev's answer whatever its confidence, which the orchestrator may override
# (docs/orchestrator/tier-rubric.md). Each rubric names the facts in the state it rests on.

_WAKE_FACTS = (
    "The facts are in the state: `now` and `weekday`, `ceilings`, `stack_counts` per project, "
    "`ready_cards` (todo cards whose dependencies are all done), `sweep_findings` (pull "
    "requests the program opened that need work: each names its `card`, its `pr` and what is "
    "`wrong` -- a conflict with its base branch, failed runs or unanswered review comments), "
    "and `last_runs` (the five most recent runs that started a session, newest first). A "
    "finding is work, as a ready card is: the wake has work when `ready_cards` or "
    "`sweep_findings` is not empty. `wakes_since_last_session` counts the wakes after the "
    "newest of those runs that started no session, refused before they started or skipped by "
    "Jev on purpose; such a wake is not a failure. Each run's `outcome` says what happened: "
    "`session ended cleanly`, `session failed (...)` or `session still running` describe a "
    "session; when a card needed the human in that session, `needed_human` lists its "
    "decisions, and a non-null `answered_at` means the human has answered since, so that "
    "blocker is resolved. Each run also has `status`, `summary` and the session `result`."
)

# `wake.interval_hours` is a whole number from 1 to 24 (`autonomouse.timer`, `TimerError`
# otherwise), so every valid value has a word here; an out-of-range value (a config `install`
# has not yet checked) falls back to its numeral rather than raising.
_HOUR_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight",
    9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
    15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
    20: "twenty", 21: "twenty-one", 22: "twenty-two", 23: "twenty-three", 24: "twenty-four",
}


def go(interval_hours: int) -> CatalogueQuestion:
    """The wake-level question: start now, wait 30 minutes, or skip? Reads
    :func:`go_state`. ``interval_hours`` is the project's own ``wake.interval_hours``
    (:data:`autonomouse.timer.INTERVAL_KEY`), the hours between scheduled wakes, so the
    question describes the cadence this project actually runs on."""
    hours = _HOUR_WORDS.get(interval_hours, str(interval_hours))
    return CatalogueQuestion(
        key="go",
        question=ChoiceQuestion(
            instructions=(
                f"An unattended program wakes every {hours} hours to work cards on a to-do "
                "board, and to fix the pull requests it opened, with Claude Code sessions. "
                "Should this wake start work now, wait thirty minutes and try again, or skip "
                "until the next scheduled wake? " + _WAKE_FACTS
            ),
            criteria={
                "go": (
                    "There is work -- at least one card in `ready_cards` or at least one pull "
                    "request in `sweep_findings` -- and nothing in `last_runs` says a session "
                    "started now would fail: the latest run that started a session ended "
                    "cleanly, or failed for a reason that is gone; runs skipped by Jev and "
                    "blockers whose decision has been answered do not count against starting; "
                    "and no usage limit is still in force at `now`."
                ),
                "wait_30m": (
                    "There is work, but a short condition makes starting now likely to fail, "
                    "and it clears within about thirty minutes: the newest session hit a usage "
                    "limit whose reset time is less than thirty minutes after `now`, or it "
                    "failed moments ago on a transient error such as a network failure or a "
                    "rate limit."
                ),
                "skip": (
                    "There is nothing to do or starting would fail for longer than thirty "
                    "minutes: `ready_cards` and `sweep_findings` are both empty, a usage limit "
                    "resets more than thirty minutes after `now`, or the last several sessions "
                    "failed the same way, which needs a human rather than another try."
                ),
            },
        ),
        # Advice: recorded on the run record, never applied (autonomouse.jev.wake).
        floor=None,
        fallback=None,
    )

def size(card_id: str, own_size: str) -> CatalogueQuestion:
    """How much effort and complexity is in the card: ``xs``, ``s``, ``m``, or ``split``
    (larger than ``m``: more than one worker turn, so the orchestrator splits it)? Reads
    :func:`card_state`. Any size Jev names applies, whatever its confidence; a missing or
    malformed answer falls back to ``own_size``, the size the card already carries."""
    card = f"`cards.{card_id}`"
    return CatalogueQuestion(
        key=f"size:{card_id}",
        question=ChoiceQuestion(
            instructions=(
                f"How much effort and complexity is in {card}: `xs`, `s`, `m`, or `split`? "
                f"The size fixes the worker: the model and reasoning effort that work the card "
                f"follow from it. The human's rule for every tier: \"{TIER_RULE}\" Size "
                f"measures the hardest decision inside the goal and how clear the goal is, not "
                f"how big the change is: a one-line fix whose cause must first be found is `s` "
                f"or `m`. `split` is not a degree of difficulty: it means the card is larger "
                f"than `m`, more than one worker finishes in one turn (one Claude Code session "
                f"in one worktree, one pull request), so the orchestrator splits it first; a "
                f"card that is one deliverable, however hard, is `xs`, `s` or `m`. Read its "
                f"`goal`, `limits` and `test`."
            ),
            criteria=dict(SIZE_DESCRIPTIONS),
        ),
        floor=None,
        fallback=_always(own_size),
    )


RECOVER = CatalogueQuestion(
    key="recover",
    question=ChoiceQuestion(
        instructions=(
            "A Claude Code session started by an unattended program failed. Should the program "
            "retry now, leave the work for the next scheduled wake in a few hours, or raise it "
            "to the human? `failure` holds the `error` text, the number of `attempts` so far "
            "(this failure included) and `reset_at`, the time a usage limit resets (null when "
            "no limit is involved); compare `reset_at` with `now`. `last_runs` shows what "
            "happened before."
        ),
        criteria={
            "retry": (
                "The failure is transient and already over: a network error, a timeout, a rate "
                "limit or a crash with no sign of a lasting cause; `attempts` is 1 or 2; any "
                "`reset_at` is already before `now`."
            ),
            "next_wake": (
                "The failure clears by itself but not soon: a usage limit whose `reset_at` is "
                "still after `now`, or a transient failure that keeps repeating; the next "
                "scheduled wake can try again without a human."
            ),
            "raise": (
                "The failure needs a human: an authentication or permission error, a missing "
                "credential, tool or file, the same error across three or more `attempts`, or "
                "any error that trying again cannot fix."
            ),
        },
    ),
    floor=0.6,
    # Below the floor or with no usable answer: a new session from the notes. A usable
    # `raise` still reaches the human (autonomouse.controller).
    fallback=_always("retry"),
)


# ---------------------------------------------------------------------------- the planner
#
# `autonomouse plan step` asks this before it starts or resumes a planner session on a thread
# (`autonomouse.planner`, the human's decision, 2026-09-28): a planner is started every time
# there is a decision to be made, and the kind of session fixes its model and reasoning effort
# through the config's `planner` table (:func:`autonomouse.config.planner_tiers`). Jev only
# judges the kind; it is never asked for a tier.

PLANNER_SESSION_DESCRIPTIONS = {
    "complex": (
        "The next session makes a hard decision: it must interpret a request whose goal is "
        "unclear or disputed, choose a design between real alternatives, split the work into "
        "several cards that depend on each other, find out from the project's code or board "
        "what the request touches before it can plan, or work out what a correction from the "
        "human changes in cards already planned."
    ),
    "simple": (
        "The next session makes a decision inside a clear goal: the human has already "
        "confirmed what they want and answered what was open, and the session writes the one "
        "or few cards that answer implies, or takes a short, direct answer and asks the next "
        "question the conversation already points to, or closes a thread the human asked to "
        "close."
    ),
    "collect-user-intent": (
        "The next session only collects the human's intent: the request is new, vague or "
        "changed, and the session says the goal back to the human in one sentence for a yes, "
        "or asks the one question that fills a gap in what the human wants. It writes no card "
        "and decides no design."
    ),
}


def planner_state(thread: Mapping[str, Any], new_messages: Iterable[str]) -> dict[str, Any]:
    """What :data:`PLANNER_SESSION` reads: the planning ``thread`` (its JSON, as
    ``autonomouse plan show --json`` prints it) without its session and tier bookkeeping, the
    ``session`` the planner runs next (``new`` when no planner has answered the thread yet,
    ``resume`` otherwise), and ``new_messages``, the texts of the human's messages that
    session answers (the thread's ``unanswered``, :mod:`autonomouse.board.plans`)."""
    return {
        "thread": {
            key: thread[key] for key in ("id", "project", "status", "messages", "cards_written")
        },
        "session": "new" if thread.get("session_id") is None else "resume",
        "new_messages": list(new_messages),
    }


PLANNER_SESSION = CatalogueQuestion(
    key="planner_session",
    question=ChoiceQuestion(
        instructions=(
            "A planner, a Claude Code session, turns a human's request into cards on a to-do "
            "board through a conversation on a planning thread. A planner session is started "
            "every time the thread has a decision to make: it answers the human's newest "
            "messages and then asks the human one question or writes cards and closes the "
            "thread. What kind of work is the next planner session: `complex`, `simple`, or "
            "`collect-user-intent`? The kind fixes the model and reasoning effort the session "
            f"runs on. The human's rule for every tier: \"{TIER_RULE}\" `thread.messages` holds "
            "the conversation so far, oldest first; `new_messages` the human's messages the "
            "next session answers; `thread.cards_written` the cards already written; "
            "`session` is `new` when no planner has answered the thread yet."
        ),
        criteria=dict(PLANNER_SESSION_DESCRIPTIONS),
    ),
    floor=0.5,
    fallback=_always("complex"),
)
