"""Cut a release of autonomouse: `autonomouse release <major|minor|patch>`.

A release is a commit on `main` that bumps the semantic version in `pyproject.toml` (and
the matching line in `uv.lock`), prepends a `CHANGELOG.md` section listing the titles of
the pull requests merged since the previous tag, and carries an annotated tag
`v<version>`. The commit and the tag are pushed together (`git push --atomic`), so origin
never has one without the other.

The pull request titles come from `git log` on main's first-parent line since the
previous tag: a squash merge's subject is the title with ` (#N)` on the end, and a merge
commit's first body line is the title. Commits that are neither (a release commit, a
direct push) are not pull requests and are left out.

A repository's first release has no previous tag to count from; its tag is made by hand
once, on the commit whose `pyproject.toml` first says that version.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

PARTS = ("major", "minor", "patch")
RELEASE_BRANCH = "main"
_VERSION_RE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)
_SQUASH_RE = re.compile(r"\(#\d+\)$")
_MERGE_RE = re.compile(r"^Merge pull request (#\d+) from ")
_CHANGELOG_HEAD = "# Changelog\n"


class ReleaseError(Exception):
    """A release the command refuses, or a git step that failed."""


@dataclass
class Release:
    previous: str
    version: str
    tag: str
    commit: str
    titles: list[str]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise ReleaseError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def bump(version: str, part: str) -> str:
    """`0.1.0` bumped by `patch` is `0.1.1`; `minor` is `0.2.0`; `major` is `1.0.0`."""
    major, minor, patch = (int(n) for n in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"part must be one of {', '.join(PARTS)} — got {part!r}")


def read_version(pyproject: str) -> str:
    """The `version = "X.Y.Z"` line of `[project]`; the only such line in the file."""
    matches = _VERSION_RE.findall(pyproject)
    if len(matches) != 1:
        raise ReleaseError(
            f'pyproject.toml must have exactly one line `version = "X.Y.Z"` — '
            f"found {len(matches)}"
        )
    return ".".join(matches[0])


def merged_pr_titles(repo: Path, since_tag: str) -> list[str]:
    """Titles of the pull requests merged on main since `since_tag`, oldest first."""
    log = _git(
        repo, "log", "--first-parent", "--reverse", "--format=%s%x1f%b%x1e",
        f"{since_tag}..HEAD",
    )
    titles = []
    for entry in log.split("\x1e"):
        entry = entry.strip("\n")
        if not entry:
            continue
        subject, _, body = entry.partition("\x1f")
        merge = _MERGE_RE.match(subject)
        if merge:
            first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
            titles.append(f"{first or subject} ({merge.group(1)})")
        elif _SQUASH_RE.search(subject):
            titles.append(subject)
    return titles


def changelog_with(existing: str | None, tag: str, day: str, titles: list[str]) -> str:
    """`existing` with a new section for `tag` on top, under the `# Changelog` heading."""
    lines = [f"- {t}" for t in titles] or ["- No pull requests merged."]
    section = f"## {tag} — {day}\n\n" + "\n".join(lines) + "\n"
    rest = existing or _CHANGELOG_HEAD
    if rest.startswith(_CHANGELOG_HEAD):
        rest = rest[len(_CHANGELOG_HEAD):].lstrip("\n")
    return _CHANGELOG_HEAD + "\n" + section + ("\n" + rest if rest else "")


def _bump_lock(lock: str, old: str, new: str) -> str:
    """uv.lock's own entry for the project carries the version too; `uv run` would rewrite
    it after the bump and leave the checkout dirty, so the release commit carries it."""
    before = f'name = "autonomouse"\nversion = "{old}"\n'
    if lock.count(before) != 1:
        raise ReleaseError(f"uv.lock has no single autonomouse entry at version {old}")
    return lock.replace(before, f'name = "autonomouse"\nversion = "{new}"\n')


def release(repo: Path, part: str, *, remote: str = "origin", today: str | None = None) -> Release:
    """Bump, write the changelog, commit, tag and push. Refuses a checkout that is not on
    main, has uncommitted changes, or has no previous release tag."""
    if part not in PARTS:
        raise ReleaseError(f"part must be one of {', '.join(PARTS)} — got {part!r}")
    repo = Path(_git(repo, "rev-parse", "--show-toplevel").strip())
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != RELEASE_BRANCH:
        raise ReleaseError(
            f"a release is cut only from {RELEASE_BRANCH}; this checkout is on {branch!r}"
        )
    dirty = _git(repo, "status", "--porcelain").strip()
    if dirty:
        raise ReleaseError(
            "the checkout has uncommitted changes; commit or remove them first:\n" + dirty
        )

    pyproject_path = repo / "pyproject.toml"
    pyproject = pyproject_path.read_text()
    if not re.search(r'^name = "autonomouse"$', pyproject, re.MULTILINE):
        raise ReleaseError(f"{pyproject_path} is not the autonomouse project")
    current = read_version(pyproject)
    previous = f"v{current}"
    if not _git(repo, "tag", "--list", previous).strip():
        raise ReleaseError(
            f"pyproject.toml says {current} but there is no tag {previous} to count from; "
            f"tag the commit that released {current} first"
        )
    version = bump(current, part)
    tag = f"v{version}"
    if _git(repo, "tag", "--list", tag).strip():
        raise ReleaseError(f"tag {tag} already exists")

    titles = merged_pr_titles(repo, previous)
    day = today or datetime.now(UTC).date().isoformat()

    pyproject_path.write_text(
        _VERSION_RE.sub(f'version = "{version}"', pyproject, count=1)
    )
    paths = ["pyproject.toml", "CHANGELOG.md"]
    lock_path = repo / "uv.lock"
    if lock_path.is_file():
        lock_path.write_text(_bump_lock(lock_path.read_text(), current, version))
        paths.append("uv.lock")
    changelog_path = repo / "CHANGELOG.md"
    existing = changelog_path.read_text() if changelog_path.is_file() else None
    changelog_path.write_text(changelog_with(existing, tag, day, titles))

    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"release {tag}")
    _git(repo, "tag", "-a", tag, "-m", f"autonomouse {version}")
    commit = _git(repo, "rev-parse", "HEAD").strip()
    try:
        _git(repo, "push", "--atomic", remote, RELEASE_BRANCH, f"refs/tags/{tag}")
    except ReleaseError as e:
        raise ReleaseError(
            f"{e}\nthe release commit {commit[:12]} and tag {tag} exist locally; "
            f"push them with: git push --atomic {remote} {RELEASE_BRANCH} refs/tags/{tag}"
        ) from e
    return Release(previous=previous, version=version, tag=tag, commit=commit, titles=titles)


def automatic_release(repo: Path, tested_commit: str) -> Release | None:
    """Release a tested main push once. A newer push's CI owns its release instead.

    GitHub's built-in token does not trigger another workflow for the release push.
    The tag check also makes manual reruns safe.
    """
    head = _git(repo, "rev-parse", "HEAD").strip()
    if head != tested_commit:
        return None
    current = read_version((repo / "pyproject.toml").read_text())
    tagged = _git(repo, "rev-parse", f"refs/tags/v{current}^{{commit}}").strip()
    if tagged == head:
        return None
    return release(repo, "patch")
