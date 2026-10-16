"""CI runs on every push to a wake's interim branch `wake/<run id>`, as it does on main, so a
red interim branch shows in that branch's own runs (`docs/orchestrator/red-main.md`).

GitHub reads the push trigger from `on.push.branches` in .github/workflows/ci.yml. The
project has no YAML library, so `_push_branches` reads that one list from the file's
indented blocks and fails loudly when the shape it expects is not there."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CI_YML = REPO / ".github" / "workflows" / "ci.yml"
README = REPO / "README.md"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _child(lines: list[str], key: str) -> tuple[str, list[str]]:
    """Find `key:` at the outermost indent of `lines`; return the text after its colon and
    the lines indented beneath it."""
    body = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    outer = min((_indent(line) for line in body), default=0)
    for i, line in enumerate(body):
        if _indent(line) == outer and line.strip().startswith(f"{key}:"):
            nested = []
            for deeper in body[i + 1 :]:
                if _indent(deeper) <= outer:
                    break
                nested.append(deeper)
            return line.split(":", 1)[1].strip(), nested
    raise AssertionError(f"ci.yml has no `{key}:` where the push trigger's branches should be")


def _push_branches() -> list[str]:
    _, on = _child(CI_YML.read_text().splitlines(), "on")
    _, push = _child(on, "push")
    value, items = _child(push, "branches")
    if value:  # a flow list: branches: [main, "wake/**"]
        entries = value.removeprefix("[").removesuffix("]").split(",")
    else:  # a block list: one "- <branch>" line each
        entries = [item.strip().removeprefix("-") for item in items]
    return [entry.strip().strip("'\"") for entry in entries]


def test_ci_runs_on_push_to_main_and_every_wake_branch():
    branches = _push_branches()
    assert "main" in branches, branches
    assert "wake/**" in branches, branches


def _ci_commands() -> list[str]:
    """The `run:` command of each step in ci.yml, in order."""
    return [
        line.split("run:", 1)[1].strip()
        for line in CI_YML.read_text().splitlines()
        if line.strip().removeprefix("- ").startswith("run:")
    ]


def _readme_commands() -> list[str]:
    """The commands in the fenced block under README's `## Run the tests`."""
    section = README.read_text().split("## Run the tests", 1)[1]
    block = section.split("```", 2)[1]
    return [line.strip() for line in block.splitlines() if line.strip()]


def test_ci_and_readme_run_the_suite_in_parallel_with_the_same_commands():
    """sys-v10: the suite runs across the machine's cores under pytest-xdist, and a worker's
    local check (docs/worker.md: the checks the README or CI names) is CI's own invocation."""
    ci = _ci_commands()
    assert "uv run pytest -q -n auto" in ci, ci
    assert _readme_commands() == ci, (_readme_commands(), ci)
