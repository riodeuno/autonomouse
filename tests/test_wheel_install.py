"""An autonomouse installed from the wheel needs no repository checkout: the agents'
charters (`docs/`) ship inside the package, every claude session autonomouse starts runs in
the folder that holds those charters (`autonomouse.home`), and the secrets file every wake
and planner session loads sits in the config folder (`~/.config/autonomouse/.env`), not next
to the source. The schedule is three crontab lines `autonomouse timer install` writes from
`autonomouse.timer` (sys-s6): the wheel carries no unit file."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from autonomouse import controller, home

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The wheel `uv build` makes from this checkout, built once for the tests below."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    out = tmp_path_factory.mktemp("wheel")
    subprocess.run(["uv", "build", "--wheel", "-q", "-o", str(out), str(REPO)], check=True)
    (built,) = out.glob("autonomouse-*.whl")
    return built


def test_the_built_wheel_contains_every_charter(wheel: Path) -> None:
    charters = sorted((REPO / "docs").rglob("*.md"))
    assert REPO / "docs" / "orchestrator.md" in charters
    with zipfile.ZipFile(wheel) as zf:
        for path in charters:
            member = f"autonomouse/docs/{path.relative_to(REPO / 'docs')}"
            assert zf.read(member) == path.read_bytes(), member


def test_the_built_wheel_contains_the_viewer_art(wheel: Path) -> None:
    """The board viewer's animation frames (`autonomouse.tui.logo`) ship inside the
    package, byte for byte."""
    from autonomouse.tui.logo import FRAMES

    assets = REPO / "src" / "autonomouse" / "tui" / "assets"
    with zipfile.ZipFile(wheel) as zf:
        for name in FRAMES:
            member = f"autonomouse/tui/assets/{name}"
            assert zf.read(member) == (assets / name).read_bytes(), member


def test_from_a_checkout_sessions_run_in_the_checkout_through_uv() -> None:
    found = home.locate()
    assert found.root == REPO
    assert found.charter("orchestrator.md").is_file()
    assert found.cli == f"uv run -q --project {REPO} autonomouse"


def test_from_an_installed_wheel_sessions_run_in_the_package_through_its_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # site-packages/autonomouse, two folders under lib/python3.x: no pyproject.toml there.
    package = tmp_path / "lib" / "python3.13" / "site-packages" / "autonomouse"
    monkeypatch.setattr(home, "PACKAGE", package)
    monkeypatch.setattr(home, "CHECKOUT", package.parents[1])
    monkeypatch.setattr(sys, "executable", str(tmp_path / "tool env" / "bin" / "python"))

    found = home.locate()

    assert found.root == package
    assert found.charter("worker.md") == package / "docs" / "worker.md"
    assert found.cli == f"'{tmp_path}/tool env/bin/autonomouse'"


def _cli_main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from autonomouse import cli

    monkeypatch.setattr("sys.argv", ["autonomouse", *argv])
    with pytest.raises(SystemExit):
        cli.main()


def test_run_and_its_plan_step_load_the_secrets_file_from_the_config_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {}
    monkeypatch.setattr(controller, "wake", lambda root, **kw: seen.update(kw) or 0)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))

    _cli_main(["run"], monkeypatch)

    assert seen["secrets"] == config_dir / ".env"
    assert seen["plan_step"].keywords["secrets"] == config_dir / ".env"
    assert seen["plan_step"].keywords["home"] == home.locate()


@pytest.mark.parametrize(
    "argv", [["plan", "join", "thread-1"], ["plan", "new", "system"]]
)
def test_plan_join_and_new_load_the_secrets_file_from_the_config_folder(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sys-i11: `plan join` and `plan new` used to pass the repository's own `.env`
    (`controller.REPO / ".env"`), so on a machine installed from the wheel — no
    repository checkout, no `.env` there — a live planner session started with no
    secrets. They now build the secrets path the same way `plan step` does."""
    from autonomouse import planner

    seen = {}
    monkeypatch.setattr(planner, "join", lambda root, thread_id, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(planner, "new", lambda root, project, **kw: seen.update(kw) or 0)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / ".env").write_text("SOME_TOKEN=x\n")
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(config_dir))
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))

    _cli_main(argv, monkeypatch)

    assert seen["secrets"] == config_dir / ".env"
    assert seen["home"] == home.locate()
