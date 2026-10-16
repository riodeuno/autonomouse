"""Tests for `src/autonomouse/browser.py`, the thin SSH client over a Chrome pool named
by `user@host` (browsermaze), against a fake `ssh` on PATH (never a real pool)."""

import os
from pathlib import Path

import pytest

from autonomouse import browser

FAKE_SSH = """#!/usr/bin/env bash
# Records its argv (minus the "ssh" argv[0], which "$@" never carries) to
# $FAKE_SSH_DIR/argv and counts the call in $FAKE_SSH_DIR/calls. For its first
# $FAKE_SSH_FULL_CALLS calls it answers as a full pool does, then it prints
# $FAKE_SSH_STDOUT/$FAKE_SSH_STDERR, sleeps $FAKE_SSH_SLEEP seconds when set, and
# exits $FAKE_SSH_EXIT (default 0).
printf '%s\\n' "$@" > "$FAKE_SSH_DIR/argv"
calls=$(( $(cat "$FAKE_SSH_DIR/calls" 2>/dev/null || echo 0) + 1 ))
echo "$calls" > "$FAKE_SSH_DIR/calls"
if [ "$calls" -le "${FAKE_SSH_FULL_CALLS:-0}" ]; then
  printf 'FAILED: no free slot - all 4 are leased\\n' >&2
  exit 1
fi
if [ -n "${FAKE_SSH_SLEEP:-}" ]; then sleep "$FAKE_SSH_SLEEP"; fi
if [ -n "${FAKE_SSH_STDOUT:-}" ]; then printf '%s' "$FAKE_SSH_STDOUT"; fi
if [ -n "${FAKE_SSH_STDERR:-}" ]; then printf '%s' "$FAKE_SSH_STDERR" >&2; fi
exit "${FAKE_SSH_EXIT:-0}"
"""


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    """A fake `ssh` first on PATH; returns the directory its calls are recorded under."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(0o755)
    ssh_dir = tmp_path / "ssh-calls"
    ssh_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_DIR", str(ssh_dir))
    for name in (
        "FAKE_SSH_STDOUT",
        "FAKE_SSH_STDERR",
        "FAKE_SSH_EXIT",
        "FAKE_SSH_SLEEP",
        "FAKE_SSH_FULL_CALLS",
    ):
        monkeypatch.delenv(name, raising=False)
    return ssh_dir


def _argv(ssh_dir: Path) -> list[str]:
    return (ssh_dir / "argv").read_text().splitlines()


def _calls(ssh_dir: Path) -> int:
    return int((ssh_dir / "calls").read_text())


def test_lease_returns_the_slot_and_url_and_records_it(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "3 http://192.0.2.14:9303\n")
    state_dir = tmp_path / "state"
    leased = browser.lease("deploy@192.0.2.14", "run-1", state_dir=state_dir)
    assert leased == browser.Lease(slot=3, url="http://192.0.2.14:9303")
    assert _argv(fake_ssh) == [
        "-o", "ConnectTimeout=10", "deploy@192.0.2.14", "browsermaze", "lease",
    ]
    assert browser.leased_slots(state_dir, "run-1") == [3]


def test_the_pool_is_handed_to_ssh_as_given_so_a_bare_host_uses_ssh_defaults(
    tmp_path, fake_ssh, monkeypatch
):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "1 http://192.0.2.14:9301\n")
    browser.lease("192.0.2.14", "run-1", state_dir=tmp_path / "state")
    assert _argv(fake_ssh) == ["-o", "ConnectTimeout=10", "192.0.2.14", "browsermaze", "lease"]


def test_host_is_the_part_of_the_pool_after_the_user():
    assert browser.host("deploy@192.0.2.14") == "192.0.2.14"
    assert browser.host("192.0.2.14") == "192.0.2.14"
    assert browser.host("pool.example.com") == "pool.example.com"


def test_lease_a_full_pool_that_frees_after_a_few_asks_leases_the_slot(
    tmp_path, fake_ssh, monkeypatch, capsys
):
    monkeypatch.setenv("FAKE_SSH_FULL_CALLS", "3")
    monkeypatch.setenv("FAKE_SSH_STDOUT", "2 http://192.0.2.14:9302\n")
    state_dir = tmp_path / "state"
    leased = browser.lease("deploy@192.0.2.14", "run-1", state_dir=state_dir, wait=5, poll=0.01)
    assert leased == browser.Lease(slot=2, url="http://192.0.2.14:9302")
    assert _calls(fake_ssh) == 4
    assert browser.leased_slots(state_dir, "run-1") == [2]
    err = capsys.readouterr().err
    assert err.count("waiting for a free browser slot: all 4 are leased") == 3


def test_lease_a_pool_still_full_after_the_wait_raises_pool_full(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_FULL_CALLS", "1000")
    with pytest.raises(browser.PoolFullError, match="no free slot - all 4 are leased") as full:
        browser.lease(
            "deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state", wait=0.3, poll=0.01
        )
    assert full.value.leased == 4
    assert _calls(fake_ssh) > 1
    assert browser.leased_slots(tmp_path / "state", "run-1") == []


def test_lease_with_no_wait_asks_once_and_raises_pool_full(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_FULL_CALLS", "1000")
    with pytest.raises(browser.PoolFullError):
        browser.lease("deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state", wait=0)
    assert _calls(fake_ssh) == 1


def test_lease_any_other_pool_failure_raises_at_once_without_waiting(
    tmp_path, fake_ssh, monkeypatch
):
    monkeypatch.setenv("FAKE_SSH_EXIT", "1")
    monkeypatch.setenv("FAKE_SSH_STDERR", "FAILED: golden Chrome is running - stop it first\n")
    with pytest.raises(browser.BrowserPoolError, match="golden Chrome is running") as failed:
        browser.lease(
            "deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state", wait=5, poll=0.01
        )
    assert not isinstance(failed.value, browser.PoolFullError)
    assert _calls(fake_ssh) == 1


def test_lease_unreachable_pool_raises_with_sshs_own_message(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_EXIT", "255")
    monkeypatch.setenv(
        "FAKE_SSH_STDERR",
        "ssh: connect to host 192.0.2.14 port 22: Connection timed out\n",
    )
    with pytest.raises(browser.BrowserPoolError, match="Connection timed out") as failed:
        browser.lease(
            "deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state", wait=5, poll=0.01
        )
    assert not isinstance(failed.value, browser.PoolFullError)
    assert _calls(fake_ssh) == 1


def test_lease_gives_up_when_ssh_hangs_past_its_timeout(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_SLEEP", "2")
    with pytest.raises(browser.BrowserPoolError, match=r"timed out after 0\.1s"):
        browser.lease("deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state", timeout=0.1)


def test_lease_when_ssh_itself_is_not_on_path(tmp_path, monkeypatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(browser.BrowserPoolError, match="could not run ssh"):
        browser.lease("deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state")


def test_lease_rejects_a_reply_it_does_not_understand(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "not what we expected\n")
    with pytest.raises(browser.BrowserPoolError, match="unexpected line"):
        browser.lease(
            "deploy@192.0.2.14", "run-1", state_dir=tmp_path / "state", wait=5, poll=0.01
        )
    assert _calls(fake_ssh) == 1


def test_release_drops_the_slot_from_the_record(tmp_path, fake_ssh, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FAKE_SSH_STDOUT", "3 http://192.0.2.14:9303\n")
    browser.lease("deploy@192.0.2.14", "run-1", state_dir=state_dir)
    monkeypatch.setenv("FAKE_SSH_STDOUT", "released 3\n")
    browser.release("deploy@192.0.2.14", "run-1", 3, state_dir=state_dir)
    assert _argv(fake_ssh) == [
        "-o", "ConnectTimeout=10", "deploy@192.0.2.14", "browsermaze", "release", "3",
    ]
    assert browser.leased_slots(state_dir, "run-1") == []
    assert not (state_dir / "run-1.leases.json").exists()


def test_release_keeps_the_record_when_ssh_fails(tmp_path, fake_ssh, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("FAKE_SSH_STDOUT", "3 http://192.0.2.14:9303\n")
    browser.lease("deploy@192.0.2.14", "run-1", state_dir=state_dir)
    monkeypatch.setenv("FAKE_SSH_EXIT", "1")
    monkeypatch.setenv("FAKE_SSH_STDERR", "boom\n")
    with pytest.raises(browser.BrowserPoolError):
        browser.release("deploy@192.0.2.14", "run-1", 3, state_dir=state_dir)
    assert browser.leased_slots(state_dir, "run-1") == [3]


def test_logins_lists_every_site_stripped(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "github.com\nhackernews.com\n")
    assert browser.logins("deploy@192.0.2.14") == ["github.com", "hackernews.com"]
    assert _argv(fake_ssh) == [
        "-o", "ConnectTimeout=10", "deploy@192.0.2.14", "browsermaze", "logins",
    ]


def test_has_login_matches_the_domain_itself(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "github.com\n")
    ok, why = browser.has_login("deploy@192.0.2.14", "github.com")
    assert ok
    assert why == "deploy@192.0.2.14 is logged in to github.com"


def test_has_login_matches_a_parent_domain(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "github.com\n")
    ok, why = browser.has_login("deploy@192.0.2.14", "gist.github.com")
    assert ok
    assert why == (
        "deploy@192.0.2.14 is logged in to github.com, the parent domain of gist.github.com"
    )


def test_has_login_does_not_match_a_look_alike_domain(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "github.com\n")
    ok, why = browser.has_login("deploy@192.0.2.14", "notgithub.com")
    assert not ok
    assert "notgithub.com" in why


def test_has_login_false_lists_what_is_logged_in(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "github.com\nhackernews.com\n")
    ok, why = browser.has_login("deploy@192.0.2.14", "example.com")
    assert not ok
    assert why == (
        "deploy@192.0.2.14 is not logged in to example.com or any parent domain of it; "
        "its logins: github.com, hackernews.com"
    )


def test_has_login_false_with_no_logins_at_all(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_STDOUT", "")
    ok, why = browser.has_login("deploy@192.0.2.14", "example.com")
    assert not ok
    assert why.endswith("its logins: none")


def test_leased_slots_with_no_record_is_empty(tmp_path):
    assert browser.leased_slots(tmp_path / "state", "run-1") == []


def test_leased_slots_ignores_a_corrupt_record(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "run-1.leases.json").write_text("not json")
    assert browser.leased_slots(state_dir, "run-1") == []
