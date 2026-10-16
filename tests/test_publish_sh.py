"""setup/publish.sh: a dry run changes nothing; --apply --dest puts a matched pair (the wheel
and the install.sh that pins its digest) in a local folder that curl can fetch. The script
runs from a copy of this repository committed and pushed to a scratch origin, so its gates
(clean tree, pushed HEAD) see a repository this test controls. No host is ever
contacted: the dry run does not contact one and every --apply here has --dest."""

import hashlib
import http.server
import re
import shutil
import subprocess
import threading
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [*GIT, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path):
    """This repository's files (tracked and new, not ignored), committed and pushed."""
    files = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.split("\0")
    repo = tmp_path / "repo"
    for name in filter(None, files):
        src = ROOT / name
        if src.is_file():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, repo / name)
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def publish(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(repo / "setup" / "publish.sh"), *args],
        cwd=repo, capture_output=True, text=True, check=False,
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pins(install_sh: str) -> dict[str, str]:
    return dict(re.findall(r'^(WHEEL_URL|WHEEL_SHA256)="(.*)"$', install_sh, re.MULTILINE))


def test_dry_run_prints_every_step_and_changes_nothing(repo):
    install_before = (repo / "setup" / "install.sh").read_bytes()
    run = publish(
        repo, "--url", "https://setup.example.com/autonomouse",
        "--host", "deploy@203.0.113.7", "--remote-dir", "/srv/autonomouse",
    )
    assert run.returncode == 0, run.stdout + run.stderr
    out = run.stdout
    wheel = re.search(r"built  (autonomouse-\S+\.whl)", out).group(1)
    digest = re.search(r"sha256 ([0-9a-f]{64})", out).group(1)
    for text in [
        "DRY RUN",
        "clean checkout of " + git(repo, "rev-parse", "HEAD").strip(),
        f'WHEEL_URL="https://setup.example.com/autonomouse/{wheel}"',
        f'WHEEL_SHA256="{digest}"',
        f"copy {wheel}, then install.sh",
        "/srv/autonomouse on deploy@203.0.113.7",
    ]:
        assert text in out
    assert "PUBLISH:" not in out
    assert (repo / "setup" / "install.sh").read_bytes() == install_before
    assert pins(install_before.decode()) == {"WHEEL_URL": "", "WHEEL_SHA256": ""}
    assert git(repo, "status", "--porcelain", "--ignored") == ""


def test_without_a_url_or_a_destination_it_refuses_and_says_what_to_pass(repo):
    run = publish(repo)
    assert run.returncode != 0
    assert "--url" in run.stderr and "--dest" in run.stderr


def test_apply_to_a_host_needs_the_host_and_the_folder(repo):
    run = publish(repo, "--apply", "--url", "https://setup.example.com/autonomouse")
    assert run.returncode != 0
    assert "--host" in run.stderr and "--remote-dir" in run.stderr


def test_dry_run_with_a_destination_does_not_create_it(repo, tmp_path):
    run = publish(repo, "--dest", str(tmp_path / "served"))
    assert run.returncode == 0, run.stdout + run.stderr
    assert f"the local folder {tmp_path / 'served'}" in run.stdout
    assert not (tmp_path / "served").exists()


@pytest.fixture
def http_folder(tmp_path):
    """A local http server over tmp_path/served, on a port the OS picks."""
    served = tmp_path / "served"
    served.mkdir()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(served))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield served, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()


def curl(url: str) -> bytes:
    return subprocess.run(["curl", "-fsSL", url], check=True, capture_output=True).stdout


def test_apply_puts_a_matched_pair_where_curl_finds_it(repo, http_folder):
    served, url = http_folder
    install_before = (repo / "setup" / "install.sh").read_bytes()
    run = publish(repo, "--apply", "--dest", str(served), "--url", url)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "PUBLISH: PASS" in run.stdout
    (wheel,) = served.glob("autonomouse-*-py3-none-any.whl")
    assert sorted(p.name for p in served.iterdir()) == sorted(["install.sh", wheel.name])

    install_sh = curl(f"{url}/install.sh")
    pinned = pins(install_sh.decode())
    assert pinned["WHEEL_URL"] == f"{url}/{wheel.name}"
    assert pinned["WHEEL_SHA256"] == sha256(curl(pinned["WHEEL_URL"]))
    assert pinned["WHEEL_SHA256"] == sha256(wheel.read_bytes())
    # Apart from the two pinned lines, the published install.sh is HEAD's.
    unpinned = re.sub(r'^(WHEEL_URL|WHEEL_SHA256)=".*"$', r'\1=""', install_sh.decode(), flags=re.MULTILINE)
    assert unpinned.encode() == install_before
    subprocess.run(["bash", "-n", str(served / "install.sh")], check=True)

    assert (repo / "setup" / "install.sh").read_bytes() == install_before
    assert git(repo, "status", "--porcelain") == ""


def test_apply_to_a_file_url_by_default(repo, tmp_path):
    served = tmp_path / "served"
    run = publish(repo, "--apply", "--dest", str(served))
    assert run.returncode == 0, run.stdout + run.stderr
    (wheel,) = served.glob("*.whl")
    pinned = pins((served / "install.sh").read_text())
    assert pinned["WHEEL_URL"] == f"file://{served}/{wheel.name}"
    assert pinned["WHEEL_SHA256"] == sha256(curl(pinned["WHEEL_URL"]))


def test_refuses_a_version_already_served(repo, tmp_path):
    """A served folder that already holds pyproject's version, republished, is refused: two
    commits would then answer to one wheel name, and an install caught mid-swap would fail
    its checksum. The refusal names `autonomouse release`, the command that bumps the
    version, and leaves the served folder untouched — in a dry run too."""
    served = tmp_path / "served"
    first = publish(repo, "--apply", "--dest", str(served))
    assert first.returncode == 0, first.stdout + first.stderr
    before = {p.name: p.read_bytes() for p in served.iterdir()}

    run = publish(repo, "--apply", "--dest", str(served))
    assert run.returncode != 0
    assert "autonomouse release" in run.stdout + run.stderr
    assert {p.name: p.read_bytes() for p in served.iterdir()} == before

    dry = publish(repo, "--dest", str(served))
    assert dry.returncode != 0
    assert "autonomouse release" in dry.stdout + dry.stderr
    assert {p.name: p.read_bytes() for p in served.iterdir()} == before


def test_refuses_when_the_destination_is_unreachable(repo, tmp_path):
    """A curl failure that is not "nothing served" (a 404, or a missing file for --dest)
    must not be read as "nothing served": the answer is unknown, and an unknown answer must
    not wave a publish through that a version already served would have refused."""
    served = tmp_path / "served"
    run = publish(repo, "--apply", "--dest", str(served), "--url", "http://127.0.0.1:1")
    assert run.returncode != 0
    assert "cannot tell what" in run.stdout + run.stderr
    assert not served.exists()


def test_refuses_a_dirty_tree(repo, tmp_path):
    (repo / "README.md").write_text("changed\n")
    run = publish(repo, "--apply", "--dest", str(tmp_path / "served"))
    assert run.returncode != 0
    assert "working tree is dirty" in run.stderr
    assert not (tmp_path / "served").exists()


def test_refuses_a_head_origin_does_not_have(repo, tmp_path):
    git(repo, "commit", "-q", "--allow-empty", "-m", "unpushed")
    run = publish(repo, "--apply", "--dest", str(tmp_path / "served"))
    assert run.returncode != 0
    assert "on no branch at origin" in run.stderr
    assert not (tmp_path / "served").exists()
