"""Where the agents' charters live, and how an agent runs this autonomouse's commands.

Every claude session autonomouse starts — the wake's orchestrator and the planner — runs
in this folder and reads its charter from `docs/` inside it. The `worker-*` agent types it
spawns are in `~/.claude/agents/` (`setup/install.sh` writes them from `setup/agents/` when they are missing). Two installs exist:

- a repository checkout (`uv sync`, the crontab lines' `uv run --project`): the folder is
  the checkout, and an agent runs `uv run -q --project <checkout> autonomouse`;
- the wheel (`setup/install.sh`, `uv tool install`), which ships `docs/` inside the package
  (`pyproject.toml`, `force-include`): the folder is the installed package, and an agent
  runs the `autonomouse` script beside the Python that runs this code.

The checkout is recognised by its `pyproject.toml` two folders above this file.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
CHECKOUT = PACKAGE.parents[1]


@dataclass(frozen=True)
class Home:
    root: Path  # holds docs/; the working folder of every session autonomouse starts
    cli: str  # the command line an agent runs autonomouse with, from any folder

    def charter(self, name: str) -> Path:
        return self.root / "docs" / name


def locate() -> Home:
    """The checkout this code runs from, else the installed package."""
    if (CHECKOUT / "pyproject.toml").is_file():
        return Home(CHECKOUT, f"uv run -q --project {shlex.quote(str(CHECKOUT))} autonomouse")
    return Home(PACKAGE, shlex.quote(str(Path(sys.executable).parent / "autonomouse")))
