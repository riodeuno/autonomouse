"""Send every hand-built situation to Jev once and save the request and reply.

Run with the Jev key in the environment:

    uv run --env-file ~/.config/autonomouse/.env python tests/record_jev_situations.py [name ...]

With names, only those situations are re-recorded; the others keep their saved replies. The
output, `tests/fixtures/jev/situations.json`, holds for each situation the exact request body
sent, the exact reply body, the expected option, and what the catalogue decided. The script
prints one line per situation and exits 1 if any decision differs from the expected option,
so a miss is seen at recording time, not only in the replay.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from autonomouse.jev import client
from autonomouse.jev.questions import questions_body
from jev_situations import SITUATIONS

FIXTURE = Path(__file__).parent / "fixtures" / "jev" / "situations.json"


def _record_one(situation) -> dict:
    captured: dict = {}
    real_urlopen = urllib.request.urlopen

    def capturing_urlopen(request, timeout):
        captured["request"] = json.loads(request.data)
        with real_urlopen(request, timeout=timeout) as response:
            body = response.read()
        captured["response"] = json.loads(body)

        class _Replay:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return body

        return _Replay()

    client.urllib.request.urlopen = capturing_urlopen
    try:
        answers = client.ask(situation.state, questions_body(situation.entries))
    finally:
        client.urllib.request.urlopen = real_urlopen
    verdict = situation.entry.decide(answers.get(situation.entry.key))
    decided = {
        "decided": verdict.applied,
        "pick": verdict.pick,
        "confidence": verdict.confidence,
        "fallback_reason": verdict.fallback_reason,
    }
    return {
        "expected": situation.expected,
        **decided,
        "recorded": f"live call to {client.API_URL} at "
        f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}, request and reply verbatim",
        "request": captured["request"],
        "response": captured["response"],
    }


def main(names: list[str]) -> int:
    known = {s.name for s in SITUATIONS}
    saved = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else {}
    # A situation that no longer exists loses its saved reply.
    saved = {name: record for name, record in saved.items() if name in known}
    unknown = set(names) - known
    if unknown:
        print(f"unknown situations: {sorted(unknown)}", file=sys.stderr)
        return 2
    misses = 0
    for situation in SITUATIONS:
        if names and situation.name not in names:
            continue
        record = _record_one(situation)
        saved[situation.name] = record
        expected = situation.expected
        ok = record["decided"] == expected and record["fallback_reason"] is None
        misses += not ok
        probabilities = [
            record["response"]["answers"][entry.key]["probabilities"]
            for entry in situation.entries
        ]
        print(
            f"{'ok  ' if ok else 'MISS'} {situation.name}: expected {situation.expected}, "
            f"Jev {record['pick']} at {record['confidence']}, decided {record['decided']}"
            f"{' (' + record['fallback_reason'] + ')' if record['fallback_reason'] else ''} "
            f"{probabilities}"
        )
    FIXTURE.write_text(json.dumps(saved, indent=2, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}; {misses} miss(es)")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
