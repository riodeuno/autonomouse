"""sys-s9: a card carries `access`, the logins and tools its test needs, each with a
shell command that exits 0 when that login or tool is present on this machine.

`card new --access '<name>=<check>'` (repeatable) writes the list, `card edit --access`
replaces it, `card show` prints one `access: <name>: <check>` line per entry, and
`--json` and `board export` carry the field. A card written before the field existed
loads with `access == []` and writes it back on its next save. Nothing runs the checks.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from autonomouse.board.store import load_card, save_card

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _run(*args: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _board(tmp_path: Path) -> tuple[dict, Path]:
    home = tmp_path / "board"
    env = dict(os.environ, AUTONOMOUSE_HOME=str(home))
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "https://github.com/test/widgets", "--repo-path", "/home/test/widgets",
        "--default-branch", "main",
        "--priority", "1",
        env=env,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    return env, home / "projects" / "widgets" / "cards"


def _new(env: dict, card_id: str, *extra: str) -> subprocess.CompletedProcess:
    return _run(
        "card", "new", "--id", card_id, "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", *extra,
        env=env,
    )  # fmt: skip


def test_card_new_writes_each_access_in_order_and_card_show_prints_them(tmp_path):
    env, cards = _board(tmp_path)

    made = _new(
        env, "wid-a1",
        "--access", "gh=gh auth status",
        "--access", "stripe=stripe whoami --key=live",
    )  # fmt: skip
    assert made.returncode == 0, made.stderr

    # The name is the text before the first `=`; the check keeps any `=` after it.
    on_disk = json.loads((cards / "wid-a1.json").read_text())
    assert on_disk["access"] == [
        {"name": "gh", "check": "gh auth status"},
        {"name": "stripe", "check": "stripe whoami --key=live"},
    ]

    shown = _run("card", "show", "wid-a1", env=env)
    assert shown.returncode == 0, shown.stderr
    lines = shown.stdout.splitlines()
    assert [line for line in lines if line.startswith("access:")] == [
        "access: gh: gh auth status",
        "access: stripe: stripe whoami --key=live",
    ]

    as_json = _run("card", "show", "wid-a1", "--json", env=env)
    assert json.loads(as_json.stdout)["access"] == on_disk["access"]

    bare = _new(env, "wid-a2")
    assert bare.returncode == 0, bare.stderr
    assert json.loads((cards / "wid-a2.json").read_text())["access"] == []
    shown_bare = _run("card", "show", "wid-a2", env=env).stdout.splitlines()
    assert [line for line in shown_bare if line.startswith("access:")] == [
        "access: (none)"
    ]


def test_card_edit_access_replaces_the_list(tmp_path):
    env, cards = _board(tmp_path)
    made = _new(
        env, "wid-a1", "--access", "gh=gh auth status", "--access", "stripe=stripe whoami"
    )
    assert made.returncode == 0, made.stderr

    edited = _run(
        "card", "edit", "wid-a1", "--access", "playwright-mcp=claude mcp get playwright",
        env=env,
    )  # fmt: skip
    assert edited.returncode == 0, edited.stderr

    card = json.loads((cards / "wid-a1.json").read_text())
    assert card["access"] == [
        {"name": "playwright-mcp", "check": "claude mcp get playwright"}
    ]
    assert card["history"][-1]["note"] == "edited: access"
    assert "access: playwright-mcp: claude mcp get playwright" in edited.stdout


def test_a_card_without_the_field_loads_empty_and_gains_it_on_save(tmp_path):
    env, cards = _board(tmp_path)
    assert _new(env, "wid-a1").returncode == 0
    path = cards / "wid-a1.json"
    old = json.loads(path.read_text())
    del old["access"]
    path.write_text(json.dumps(old, indent=2))

    card = load_card(path)
    assert card.access == []
    save_card(path, card)
    assert json.loads(path.read_text())["access"] == []

    # Through the command line too: an edit of another field writes the field back.
    path.write_text(json.dumps(old, indent=2))
    assert "access" not in json.loads(path.read_text())
    shown = _run("card", "show", "wid-a1", env=env)
    assert shown.returncode == 0, shown.stderr
    assert "access: (none)" in shown.stdout.splitlines()
    assert _run("card", "edit", "wid-a1", "--title", "t2", env=env).returncode == 0
    assert json.loads(path.read_text())["access"] == []


def test_a_malformed_access_value_is_refused_and_writes_nothing(tmp_path):
    env, cards = _board(tmp_path)

    for value in ("nocheck", "=cmd", "gh=", "  =gh auth status"):
        refused = _new(env, "wid-a1", "--access", "gh=gh auth status", "--access", value)
        assert refused.returncode != 0, value
        assert "<name>=<check>" in refused.stderr, refused.stderr
        assert "--access" in refused.stderr, refused.stderr
        assert not (cards / "wid-a1.json").exists(), value

    assert _new(env, "wid-a2", "--access", "gh=gh auth status").returncode == 0
    path = cards / "wid-a2.json"
    before = path.read_text()
    for value in ("nocheck", "=cmd"):
        refused = _run("card", "edit", "wid-a2", "--access", value, env=env)
        assert refused.returncode != 0, value
        assert "<name>=<check>" in refused.stderr, refused.stderr
        assert path.read_text() == before, value


def test_board_export_carries_the_field(tmp_path):
    env, _ = _board(tmp_path)
    assert _new(env, "wid-a1", "--access", "gh=gh auth status").returncode == 0
    assert _new(env, "wid-a2").returncode == 0

    exported = _run("board", "export", env=env)
    assert exported.returncode == 0, exported.stderr
    by_id = {c["id"]: c for c in json.loads(exported.stdout)["cards"]}
    assert by_id["wid-a1"]["access"] == [{"name": "gh", "check": "gh auth status"}]
    assert by_id["wid-a2"]["access"] == []


def test_the_board_schema_doc_has_the_access_row():
    schema = (DOCS / "board-schema" / "card.md").read_text().splitlines()
    row = next(line for line in schema if line.startswith("| access |"))
    assert "each `{name, check}`" in row
    assert "`check` a shell command that exits 0 when that login or tool is present" in row
    assert "The commands only record the list and never run a check." in row
    assert "`card edit --access` replaces the list" in row
    assert "one `access: <name>: <check>` line per entry, `access: (none)`" in row
    assert row.endswith("| `card new` / `card edit` |")
