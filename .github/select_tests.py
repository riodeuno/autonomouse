"""Limit release-only changes to their tests; otherwise keep the full suite."""

import os
import subprocess
from pathlib import Path

RELEASE_FILES = {
    ".github/workflows/ci.yml",
    ".github/select_tests.py",
    "src/autonomouse/release.py",
    "src/autonomouse/cli/release.py",
    "tests/test_release.py",
    "tests/test_ci_workflow.py",
    "tests/test_ci_selection.py",
    "CLAUDE.md",
    "docs/setup.md",
    "docs/design/DESIGN.md",
}


def selected_tests(changed: set[str]) -> list[str]:
    if changed and changed <= RELEASE_FILES:
        return [
            "tests/test_release.py",
            "tests/test_ci_workflow.py",
            "tests/test_ci_selection.py",
        ]
    return []


def main() -> None:
    base = os.environ.get("BASE_COMMIT", "")
    diff = subprocess.run(
        ["git", "diff", "--name-only", "-z", base, "HEAD"],
        capture_output=True, check=False,
    ) if base and set(base) != {"0"} else None
    changed = set(diff.stdout.decode().strip("\0").split("\0")) if diff and diff.returncode == 0 else set()
    targets = selected_tests(changed)
    if targets:
        with Path(os.environ["GITHUB_ENV"]).open("a") as env:
            env.write("PYTEST_ADDOPTS=" + " ".join(targets) + "\n")
        print("Affected tests: " + " ".join(targets))
    else:
        print("Changes extend beyond release automation; running the full suite.")


if __name__ == "__main__":
    main()
