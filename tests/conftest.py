"""Fixtures every test gets."""

import pytest


@pytest.fixture(autouse=True)
def _errors_log(tmp_path_factory, monkeypatch):
    """Point the error log (`autonomouse.errors_log`) at a temp file, so a test whose
    command or viewer raises never adds an entry to the human's real
    `~/.local/state/autonomouse/errors.log`."""
    log = tmp_path_factory.mktemp("errors-log") / "errors.log"
    monkeypatch.setenv("AUTONOMOUSE_ERRORS_LOG", str(log))


@pytest.fixture(autouse=True)
def _no_caller_config(monkeypatch):
    """Remove the caller's `AUTONOMOUSE_CONFIG`, so `config_dir()` never points at the
    human's real config folder: a test that needs a config sets its own (the variable, or
    a temporary HOME), and every `dict(os.environ, ...)` a test hands a subprocess
    inherits the removal. Without it a caller's config makes tests fail falsely."""
    monkeypatch.delenv("AUTONOMOUSE_CONFIG", raising=False)


@pytest.fixture(autouse=True)
def _no_splash(monkeypatch):
    """Open a board without the three-second splash (`autonomouse.tui.splash`), so a
    viewer test sees the board as soon as it presses enter; the splash's own tests turn
    it back on."""
    from autonomouse.tui.app import AutonomouseApp

    monkeypatch.setattr(AutonomouseApp, "SPLASH", False)
