"""Tests for autonomouse.release: `autonomouse release <major|minor|patch>`."""

import subprocess
from pathlib import Path

import pytest

from autonomouse.release import (
    ReleaseError,
    bump,
    changelog_with,
    merged_pr_titles,
    release,
)

PYPROJECT = '[project]\nname = "autonomouse"\nversion = "0.1.0"\n'
LOCK = 'version = 1\n\n[[package]]\nname = "autonomouse"\nversion = "0.1.0"\n'


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def commit(repo: Path, subject: str, body: str = "") -> None:
    (repo / "log.txt").write_text((repo / "log.txt").read_text() + subject + "\n")
    git(repo, "add", "log.txt")
    git(repo, "commit", "-q", "-m", subject + (f"\n\n{body}" if body else ""))


@pytest.fixture
def clone(tmp_path):
    """A checkout on main at 0.1.0, tagged v0.1.0, with a bare origin it pushes to."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(origin), str(repo))
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (repo / "uv.lock").write_text(LOCK)
    (repo / "log.txt").write_text("")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "sys-a1: the start (#1)")
    git(repo, "tag", "-a", "v0.1.0", "-m", "autonomouse 0.1.0")
    git(repo, "push", "-q", "origin", "main", "v0.1.0")
    return repo


def test_bump():
    assert bump("0.1.0", "patch") == "0.1.1"
    assert bump("0.1.3", "minor") == "0.2.0"
    assert bump("0.4.3", "major") == "1.0.0"


def test_patch_release_bumps_logs_commits_tags_and_pushes(clone):
    commit(clone, "sys-b2: squash-merged change (#2)")
    commit(clone, "a direct commit, not a pull request")
    git(clone, "checkout", "-q", "-b", "feature")
    commit(clone, "work on the feature")
    git(clone, "checkout", "-q", "main")
    git(
        clone, "merge", "-q", "--no-ff", "feature", "-m",
        "Merge pull request #3 from owner/feature\n\nsys-c3: merged with a merge commit",
    )

    cut = release(clone, "patch", today="2026-09-22")

    assert cut.tag == "v0.1.1" and cut.previous == "v0.1.0"
    assert 'version = "0.1.1"' in (clone / "pyproject.toml").read_text()
    assert 'name = "autonomouse"\nversion = "0.1.1"' in (clone / "uv.lock").read_text()
    changelog = (clone / "CHANGELOG.md").read_text()
    assert changelog == (
        "# Changelog\n\n## v0.1.1 — 2026-09-22\n\n"
        "- sys-b2: squash-merged change (#2)\n"
        "- sys-c3: merged with a merge commit (#3)\n"
    )
    head = git(clone, "rev-parse", "HEAD").strip()
    assert git(clone, "rev-list", "-n1", "v0.1.1").strip() == head
    assert git(clone, "status", "--porcelain") == ""
    origin = clone.parent / "origin.git"
    assert git(origin, "rev-parse", "main").strip() == head
    assert git(origin, "rev-list", "-n1", "v0.1.1").strip() == head


def test_second_release_counts_from_the_last_tag_and_keeps_old_sections(clone):
    commit(clone, "sys-b2: first change (#2)")
    release(clone, "patch", today="2026-09-22")
    commit(clone, "sys-b4: second change (#4)")

    cut = release(clone, "minor", today="2026-09-23")

    assert cut.tag == "v0.2.0" and cut.titles == ["sys-b4: second change (#4)"]
    changelog = (clone / "CHANGELOG.md").read_text()
    assert changelog.index("## v0.2.0") < changelog.index("## v0.1.1")
    assert changelog.count("sys-b2: first change (#2)") == 1


def test_refuses_a_dirty_checkout(clone):
    (clone / "log.txt").write_text("uncommitted\n")
    with pytest.raises(ReleaseError, match="uncommitted changes"):
        release(clone, "patch")
    assert 'version = "0.1.0"' in (clone / "pyproject.toml").read_text()


def test_refuses_a_checkout_not_on_main(clone):
    git(clone, "checkout", "-q", "-b", "other")
    with pytest.raises(ReleaseError, match="only from main"):
        release(clone, "patch")


def test_refuses_when_the_current_version_has_no_tag(clone):
    git(clone, "tag", "-d", "v0.1.0")
    with pytest.raises(ReleaseError, match="no tag v0.1.0"):
        release(clone, "patch")


def test_failed_push_says_how_to_retry(clone):
    git(clone, "remote", "set-url", "origin", str(clone.parent / "missing.git"))
    with pytest.raises(ReleaseError, match="git push --atomic origin main refs/tags/v0.1.1"):
        release(clone, "patch")


def test_merged_pr_titles_skips_non_pr_commits(clone):
    commit(clone, "release v9.9.9")
    assert merged_pr_titles(clone, "v0.1.0") == []


def test_changelog_keeps_a_hand_written_top():
    existing = "# Changelog\n\n## v0.1.0\n\nFirst release.\n"
    assert changelog_with(existing, "v0.1.1", "2026-09-22", []) == (
        "# Changelog\n\n## v0.1.1 — 2026-09-22\n\n- No pull requests merged.\n"
        "\n## v0.1.0\n\nFirst release.\n"
    )


def test_cli_refuses_a_dirty_checkout(clone, monkeypatch, capsys):
    from autonomouse import cli

    (clone / "log.txt").write_text("uncommitted\n")
    monkeypatch.chdir(clone)
    monkeypatch.setattr("sys.argv", ["autonomouse", "release", "patch"])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    assert exit_.value.code == 1
    assert "uncommitted changes" in capsys.readouterr().err


def test_automatic_release_skips_a_released_commit(clone):
    from autonomouse.release import automatic_release

    head = git(clone, "rev-parse", "HEAD").strip()
    assert automatic_release(clone, head) is None
    assert git(clone, "rev-parse", "HEAD").strip() == head


def test_automatic_release_skips_a_superseded_ci_run(clone):
    from autonomouse.release import automatic_release

    tested = git(clone, "rev-parse", "HEAD").strip()
    commit(clone, "a newer change (#2)")
    assert automatic_release(clone, tested) is None
    assert 'version = "0.1.0"' in (clone / "pyproject.toml").read_text()


def test_automatic_release_pushes_once_and_reruns_do_nothing(clone):
    from autonomouse.release import automatic_release

    commit(clone, "a merged change (#2)")
    tested = git(clone, "rev-parse", "HEAD").strip()
    cut = automatic_release(clone, tested)
    assert cut.tag == "v0.1.1"
    assert git(clone.parent / "origin.git", "rev-parse", "main").strip() == cut.commit
    assert automatic_release(clone, tested) is None
    assert automatic_release(clone, cut.commit) is None
