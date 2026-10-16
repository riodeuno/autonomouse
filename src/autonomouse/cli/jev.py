"""`autonomouse jev`: Typesafe AI's Jev model."""

import json

from autonomouse.cli.shared import _fail
from autonomouse.jev.client import JevError
from autonomouse.jev.probe import probe


def register(subparsers) -> None:
    jev_parser = subparsers.add_parser("jev", help="Typesafe AI's Jev model")
    jev_sub = jev_parser.add_subparsers(dest="jev_command")

    probe_parser = jev_sub.add_parser(
        "probe",
        help="ask one fixed question with a known answer; exit 1 if Jev's pick has drifted",
    )
    probe_parser.add_argument("--json", action="store_true")

    jev_parser.set_defaults(_parser=jev_parser)


def run(args) -> None:
    try:
        if args.jev_command == "probe":
            result = probe()
            if args.json:
                print(
                    json.dumps(
                        {
                            "pick": result.pick,
                            "confidence": result.confidence,
                            "expected": result.expected,
                            "matches": result.matches,
                        },
                        indent=2,
                    )
                )
            else:
                print(f"answer: {result.pick}")
                print(f"confidence: {result.confidence}")
                print(f"expected: {result.expected}")
                print(f"matches: {'yes' if result.matches else 'no'}")
            if not result.matches:
                raise SystemExit(1)
        else:
            args._parser.error("choose a jev command: probe")
    except JevError as e:
        _fail(str(e))
