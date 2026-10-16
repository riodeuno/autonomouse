"""`autonomouse browser lease`: what it prints and the status it exits with when the pool
has a slot, is still full after its wait, or fails (`src/autonomouse/cli/browser.py`)."""

from types import SimpleNamespace

import pytest

from autonomouse import browser
from autonomouse.cli import browser as cli_browser

LEASE = SimpleNamespace(browser_command="lease")


@pytest.fixture(autouse=True)
def pool_and_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_browser, "_pool", lambda: "deploy@192.0.2.14")
    monkeypatch.setattr(cli_browser, "_run_id", lambda: "run-1")
    monkeypatch.setattr(cli_browser.controller, "default_state_dir", lambda: tmp_path)


def test_lease_prints_the_slot_and_url(monkeypatch, capsys):
    monkeypatch.setattr(
        browser, "lease", lambda *a, **k: browser.Lease(slot=2, url="http://192.0.2.14:9302")
    )
    cli_browser.run(LEASE)
    assert capsys.readouterr().out == "2 http://192.0.2.14:9302\n"


def test_lease_exits_75_when_the_pool_is_still_full_after_its_wait(monkeypatch, capsys):
    def full(*a, **k):
        raise browser.PoolFullError("ssh ... failed: FAILED: no free slot - all 4 are leased", 4)

    monkeypatch.setattr(browser, "lease", full)
    with pytest.raises(SystemExit) as exit_:
        cli_browser.run(LEASE)
    assert exit_.value.code == cli_browser.STILL_FULL == 75
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "all 4 slots are still leased" in captured.err
    assert "autonomouse browser lease" in captured.err


def test_lease_exits_1_naming_why_on_any_other_failure(monkeypatch, capsys):
    def unreachable(*a, **k):
        raise browser.BrowserPoolError("ssh ... failed: Connection timed out")

    monkeypatch.setattr(browser, "lease", unreachable)
    with pytest.raises(SystemExit) as exit_:
        cli_browser.run(LEASE)
    assert exit_.value.code == 1
    assert "Connection timed out" in capsys.readouterr().err
