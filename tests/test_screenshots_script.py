"""`scripts/screenshots.py --fixture every-view` shoots every view in the light theme with
`--light`, and with the PAUSE file present in the fixture's own config folder with
`--paused` (sys-w119). The flags combine with each other, with `--nerd-font` and with
`--crashed-wake`.

Each run of the script is a subprocess of its own, so the environment it sets and the
fixture it builds never reach this test process. Its `HOME` is an empty temporary folder
and `AUTONOMOUSE_CONFIG` is not set, so a script that wrote the PAUSE file where the real
one lives would leave it in that folder.

`_claim` builds the every-view fixture in this test process, to read the claim it
leaves. The fixture opens its planning threads with fixed ids and then puts back the
real `new_thread_id` (sys-w120): before, it left the spent ids behind, and every later
test on the same pytest worker that opened a thread failed with StopIteration.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from textual.theme import BUILTIN_THEMES

from autonomouse.board import plans as board_plans
from autonomouse.tui.app import DARK_THEME, LIGHT_THEME

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "screenshots.py"
VIEWS = 14  # the views of the every-view fixture; sys-w105 adds more
BOARD = "02-board.svg"


def _background(theme: str) -> str:
    """The colour a theme paints its screens with, as an SVG rect's `fill` gives it."""
    colour = BUILTIN_THEMES[theme].to_color_system().generate()["background"].lower()
    return f'fill="{colour}"'


def _shoot(tmp_path: Path, *flags: str) -> dict[str, str]:
    """Run the every-view fixture with `flags`, and return each SVG's text by file name.

    The files are `NN-<view>.svg`, numbered from 01 with no gap, and the script prints
    their paths in that order. The real PAUSE file's place stays empty."""
    home = tmp_path / "home"
    home.mkdir()
    out = tmp_path / "out"
    env = {**os.environ, "HOME": str(home), "AUTONOMOUSE_HOME": str(tmp_path / "board")}
    env.pop("AUTONOMOUSE_CONFIG", None)
    run = subprocess.run(
        [sys.executable, str(SCRIPT), str(out), "--fixture", "every-view", *flags],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    files = sorted(out.glob("*.svg"))
    assert len(files) >= VIEWS
    assert [f.name[:2] for f in files] == [f"{n:02d}" for n in range(1, len(files) + 1)]
    assert run.stdout.split() == [str(f) for f in files]
    assert not (home / ".config" / "autonomouse" / "PAUSE").exists()
    return {f.name: f.read_text(encoding="utf-8") for f in files}


def _has_nerd_font_glyph(svg: str) -> bool:
    """A Nerd Font icon is a character of the Supplementary Private Use Area
    (`autonomouse.tui.icons`); the plain icons have none."""
    return any(0xF0000 <= ord(c) <= 0xFFFFD for c in svg)


def test_a_plain_run_is_dark_and_not_paused(tmp_path):
    shots = _shoot(tmp_path)

    assert _background(LIGHT_THEME) not in shots[BOARD]
    assert all(_background(DARK_THEME) in svg for svg in shots.values())
    assert "paused" not in shots[BOARD].lower()
    assert not _has_nerd_font_glyph(shots[BOARD])


def test_light_shoots_every_view_in_the_light_theme(tmp_path):
    shots = _shoot(tmp_path, "--light")

    assert all(_background(LIGHT_THEME) in svg for svg in shots.values())
    assert not any(_background(DARK_THEME) in svg for svg in shots.values())


def test_paused_shoots_the_board_of_a_paused_project(tmp_path):
    shots = _shoot(tmp_path, "--paused")

    assert "paused" in shots[BOARD].lower()
    assert _background(DARK_THEME) in shots[BOARD]


def test_all_four_flags_combine(tmp_path):
    shots = _shoot(tmp_path, "--light", "--paused", "--nerd-font", "--crashed-wake")

    board = shots[BOARD]
    assert all(_background(LIGHT_THEME) in svg for svg in shots.values())
    assert _has_nerd_font_glyph(board)
    assert "stopped" in board  # the crashed wake's in_progress cards


def test_light_and_paused_are_refused_without_the_every_view_fixture(tmp_path):
    for flags in (["--light"], ["--paused"], ["--fixture", "status-strip", "--paused"]):
        run = subprocess.run(
            [sys.executable, str(SCRIPT), str(tmp_path / "out"), *flags],
            env={**os.environ, "HOME": str(tmp_path), "AUTONOMOUSE_HOME": str(tmp_path / "b")},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert run.returncode == 2, run.stdout
        assert "--fixture every-view" in run.stderr
        assert not (tmp_path / "out").exists()


def _claim(root: Path, **flags) -> dict:
    """The project claim of the every-view fixture, built in `root` with `flags`."""
    spec = importlib.util.spec_from_file_location("screenshots_script", SCRIPT)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    script._build_every_view_fixture(root, "system", **flags)
    return json.loads((root / "projects" / "system" / "project.json").read_text())["claim"]


def test_a_paused_project_has_no_wake_unless_it_crashed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    live = _claim(tmp_path / "live")
    paused = _claim(tmp_path / "paused", paused=True)
    crashed = _claim(tmp_path / "crashed", paused=True, crashed_wake=True)

    assert live["pid"] == os.getpid()
    assert paused["run_id"] is None
    assert crashed["run_id"] is not None and crashed["pid"] != os.getpid()


def test_building_the_fixture_leaves_the_new_thread_id_it_found(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(tmp_path / "board"))
    found = board_plans.new_thread_id

    _claim(tmp_path / "board")

    assert board_plans.new_thread_id is found
