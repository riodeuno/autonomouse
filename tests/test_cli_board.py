"""CLI-level test for the card's own test: create a project and three cards,
list them by stack, show one as JSON, edit a project's priority, move and
cancel a card — all through the shell, `autonomouse project ...` /
`autonomouse card ...`.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(*args, env):
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def test_project_and_card_lifecycle_through_the_shell(tmp_path: Path) -> None:
    board_home = tmp_path / "board"
    env = dict(os.environ, AUTONOMOUSE_HOME=str(board_home))

    new_project = _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "Build widgets.",
        "--repo",
        "https://github.com/test/widgets",
        "--repo-path",
        "/home/test/widgets", "--default-branch", "main",
        "--priority",
        "3",
        env=env,
    )
    assert new_project.returncode == 0, new_project.stderr
    assert "slug: widgets" in new_project.stdout

    for i in range(1, 4):
        created = _run(
            "card",
            "new",
            "--id",
            f"wid-a{i}",
            "--project",
            "widgets",
            "--title",
            f"card {i}",
            "--goal",
            "goal text",
            "--test",
            "test text",
            "--position",
            str(i),
            env=env,
        )
        assert created.returncode == 0, created.stderr
        assert f"id: wid-a{i}" in created.stdout
        assert "stack: todo" in created.stdout

    listed = _run("card", "list", "--project", "widgets", "--stack", "todo", env=env)
    assert listed.returncode == 0, listed.stderr
    for i in range(1, 4):
        assert f"wid-a{i}" in listed.stdout

    listed_done = _run(
        "card", "list", "--project", "widgets", "--stack", "done", env=env
    )
    assert listed_done.returncode == 0
    assert "no cards" in listed_done.stdout

    shown = _run("card", "show", "wid-a2", "--json", env=env)
    assert shown.returncode == 0, shown.stderr
    card_json = json.loads(shown.stdout)
    assert card_json["id"] == "wid-a2"
    assert card_json["stack"] == "todo"
    assert card_json["limits"] == ["docs/rules.md applies"]

    edited = _run("project", "edit", "widgets", "--priority", "7", env=env)
    assert edited.returncode == 0, edited.stderr
    assert "priority: 7" in edited.stdout

    renamed = _run("project", "edit", "widgets", "--name", "Widget factory", env=env)
    assert renamed.returncode == 0, renamed.stderr

    reshown = json.loads(_run("project", "show", "widgets", "--json", env=env).stdout)
    assert (reshown["slug"], reshown["name"], reshown["priority"]) == (
        "widgets",
        "Widget factory",
        7,
    )


def test_project_list_plain_and_json(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project",
        "new",
        "--slug",
        "a",
        "--name",
        "A",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "2",
        env=env,
    )
    _run(
        "project",
        "new",
        "--slug",
        "b",
        "--name",
        "B",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )

    plain = _run("project", "list", env=env)
    assert plain.returncode == 0
    lines = plain.stdout.strip().splitlines()
    assert lines[0].startswith("b")
    assert lines[1].startswith("a")

    as_json = _run("project", "list", "--json", env=env)
    slugs = [p["slug"] for p in json.loads(as_json.stdout)]
    assert slugs == ["b", "a"]


def test_card_move_and_cancel_through_the_shell(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        "--backlog",
        env=env,
    )

    moved = _run("card", "move", "wid-a1", "--to", "todo", "--json", env=env)
    assert moved.returncode == 0, moved.stderr
    moved_json = json.loads(moved.stdout)
    assert moved_json["stack"] == "todo"
    assert moved_json["history"][-1] == {
        "at": moved_json["history"][-1]["at"],
        "run_id": "manual",
        "from": "backlog",
        "to": "todo",
        "note": None,
    }

    refused = _run("card", "move", "wid-a1", "--to", "bogus", env=env)
    assert refused.returncode != 0
    assert "no such stack" in refused.stderr

    cancelled = _run(
        "card", "cancel", "wid-a1", "--reason", "no longer needed", "--json", env=env
    )
    assert cancelled.returncode == 0, cancelled.stderr
    cancelled_json = json.loads(cancelled.stdout)
    assert cancelled_json["stack"] == "cancelled"
    assert cancelled_json["history"][-1]["note"] == "no longer needed"

    refused_reason = _run("card", "cancel", "wid-a1", "--reason", "", env=env)
    assert refused_reason.returncode != 0
    assert "reason" in refused_reason.stderr


def test_card_move_reopens_a_done_card_through_the_shell(tmp_path: Path) -> None:
    board_home = tmp_path / "board"
    env = dict(os.environ, AUTONOMOUSE_HOME=str(board_home))
    _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    _run(
        "card", "new", "--id", "wid-a1", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", env=env,
    )
    # No command finishes a card without a run and a PR; set the stack by hand.
    card_file = board_home / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_file.read_text())
    data["stack"] = "done"
    card_file.write_text(json.dumps(data))

    no_next = _run(
        "card", "move", "wid-a1", "--to", "todo", "--note", "reopened", env=env
    )
    assert no_next.returncode != 0
    assert "--next-step" in no_next.stderr

    reopened = _run(
        "card", "move", "wid-a1", "--to", "todo", "--note", "reopened: conflicts",
        "--next-step", "reopened: merge origin/main", "--run-id", "run-1", "--json",
        env=env,
    )
    assert reopened.returncode == 0, reopened.stderr
    card = json.loads(reopened.stdout)
    assert (card["stack"], card["position"], card["phase"]) == ("todo", 0, "writing")
    assert card["handoff"]["next_step"] == "reopened: merge origin/main"
    assert card["history"][-1]["from"] == "done"
    assert card["history"][-1]["note"] == "reopened: conflicts"


def test_card_ask_without_recommendation_is_a_usage_error(tmp_path: Path) -> None:
    # Rules item 8: every decision brief carries a recommendation. argparse refuses
    # the command before it reads the board, so no board or notify config is needed.
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    env["AUTONOMOUSE_CONFIG"] = str(tmp_path / "notify-config")
    env.pop("TELEGRAM_BOT_TOKEN", None)
    refused = _run(
        "card",
        "ask",
        "wid-a1",
        "--brief",
        "the brief",
        "--question",
        "q",
        "--option",
        "a",
        "--option",
        "b",
        env=env,
    )
    assert refused.returncode == 2
    assert "usage:" in refused.stderr
    assert "--recommendation" in refused.stderr


def test_card_ask_without_brief_is_a_usage_error(tmp_path: Path) -> None:
    # Rules item 8: every decision opens with a brief a human can read without
    # the code. argparse refuses the command before it reads the board.
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    env["AUTONOMOUSE_CONFIG"] = str(tmp_path / "notify-config")
    env.pop("TELEGRAM_BOT_TOKEN", None)
    refused = _run(
        "card",
        "ask",
        "wid-a1",
        "--question",
        "q",
        "--option",
        "a",
        "--recommendation",
        "a",
        env=env,
    )
    assert refused.returncode == 2
    assert "--brief" in refused.stderr


def test_card_ask_blank_recommendation_is_refused(tmp_path: Path) -> None:
    # sys-d28: argparse's own `required=True` (sys-d26) accepts an empty or
    # whitespace-only string, so a brief with a blank recommendation could still
    # reach the human. `card_ask` itself refuses it now (rules item 8).
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    env["AUTONOMOUSE_CONFIG"] = str(tmp_path / "notify-config")
    env.pop("TELEGRAM_BOT_TOKEN", None)
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        env=env,
    )
    card_path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))

    for blank in ("", "  "):
        refused = _run(
            "card",
            "ask",
            "wid-a1",
            "--brief",
            "the brief",
            "--question",
            "q",
            "--option",
            "a",
            "--option",
            "b",
            "--recommendation",
            blank,
            env=env,
        )
        assert refused.returncode != 0
        assert "--recommendation" in refused.stderr

        after = json.loads(card_path.read_text())
        assert after["decisions"] == []
        assert after["stack"] == "in_progress"


def test_card_ask_and_answer_through_the_shell(tmp_path: Path) -> None:
    # `card ask` sends a real notify; keep it off this machine's real config.toml
    # and real credentials (TELEGRAM_BOT_TOKEN, if this shell happens to export one)
    # so this test can never make a live send.
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    env["AUTONOMOUSE_CONFIG"] = str(tmp_path / "notify-config")
    env.pop("TELEGRAM_BOT_TOKEN", None)
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        env=env,
    )
    refused = _run(
        "card",
        "ask",
        "wid-a1",
        "--brief",
        "the brief",
        "--question",
        "which?",
        "--option",
        "a",
        "--recommendation",
        "a",
        env=env,
    )
    assert refused.returncode != 0
    assert "in_progress" in refused.stderr

    # `card start` (todo -> in_progress) is a later card; force the card
    # in_progress the same way the board's own tests do, then exercise ask/answer.
    card_path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))

    asked = _run(
        "card",
        "ask",
        "wid-a1",
        "--brief",
        "the brief",
        "--question",
        "which spend limit?",
        "--option",
        "$10",
        "--option",
        "$50",
        "--recommendation",
        "$10",
        "--json",
        env=env,
    )
    assert asked.returncode == 0, asked.stderr
    asked_json = json.loads(asked.stdout)
    assert asked_json["stack"] == "decision_needed"
    assert asked_json["decisions"][0]["question"] == "which spend limit?"
    assert asked_json["decisions"][0]["options"] == ["$10", "$50", "other: tell me"]
    assert asked_json["decisions"][0]["recommendation"] == "$10"
    assert asked_json["decisions"][0]["answer"] is None

    shown = _run("card", "show", "wid-a1", "--json", env=env)
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["decisions"][0]["brief"] == "the brief"

    answered = _run("card", "answer", "wid-a1", "--answer", "$10", "--json", env=env)
    assert answered.returncode == 0, answered.stderr
    answered_json = json.loads(answered.stdout)
    assert answered_json["stack"] == "todo"
    assert answered_json["position"] == 0
    assert answered_json["decisions"][0]["answer"] == "$10"
    assert answered_json["decisions"][0]["answered_at"]

    refused_again = _run("card", "answer", "wid-a1", "--answer", "$10", env=env)
    assert refused_again.returncode != 0
    assert "decision_needed" in refused_again.stderr


def test_card_answer_reaches_telegram_from_the_secrets_file_without_the_token_in_env(
    tmp_path: Path, monkeypatch
) -> None:
    """sys-s35: run from a plain shell whose own environment has no
    TELEGRAM_BOT_TOKEN, `card answer` must still reach Telegram by loading the
    token from the secrets file, the same way `autonomouse notify` does. Run
    in-process (unlike this file's other tests) so the Telegram send can be
    faked without a real network call; a subprocess could not be faked this way."""
    from autonomouse import cli
    from autonomouse import notify as notify_module

    board_home = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(board_home))
    secrets_folder = tmp_path / "config"
    secrets_folder.mkdir()
    token = "123456789:AAtest-token"
    (secrets_folder / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    (secrets_folder / "config.toml").write_text(
        '[notify]\ntelegram_chat_id = "555000111"\n'
    )
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(secrets_folder))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    calls: list[tuple[str, str, dict]] = []

    def fake_telegram(sent_token: str, method: str, payload: dict) -> dict:
        calls.append((sent_token, method, payload))
        return {"ok": True, "result": {"message_id": 7}}

    monkeypatch.setattr(notify_module, "_telegram_request", fake_telegram)

    def run_cli(*argv: str) -> None:
        monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
        cli.main()

    run_cli(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1",
    )
    run_cli(
        "card", "new", "--id", "wid-a1", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t",
    )
    card_path = board_home / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))

    run_cli(
        "card", "ask", "wid-a1", "--brief", "b", "--question", "q",
        "--option", "a", "--recommendation", "a",
    )
    # The ask's own send is exercised by test_card_ask_and_answer_through_the_shell;
    # only `card answer`'s send is what this test checks.
    calls.clear()

    run_cli("card", "answer", "wid-a1", "--answer", "a")

    assert calls == [
        (
            token,
            "sendMessage",
            {
                "chat_id": "555000111",
                "text": (
                    "<b>02 Widgets</b>\n<b>Answered: q</b>\nAnswer: a\n"
                    "Answered by: someone at the command line\n"
                    "The card is now in the top of todo."
                ),
                "parse_mode": "HTML",
                "reply_parameters": {
                    "message_id": 7,
                    "allow_sending_without_reply": True,
                },
            },
        )
    ]
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_card_cancel_reaches_telegram_from_the_secrets_file_without_the_token_in_env(
    tmp_path: Path, monkeypatch
) -> None:
    """sys-s42: `card cancel` of a card whose last decision is unanswered sends a
    Telegram follow-up the same way `card answer` does (sys-s35), and must reach it
    from the secrets file when a plain shell's own environment has no
    TELEGRAM_BOT_TOKEN. Run in-process so the Telegram send can be faked without a
    real network call; a subprocess could not be faked this way."""
    from autonomouse import cli
    from autonomouse import notify as notify_module

    board_home = tmp_path / "board"
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(board_home))
    secrets_folder = tmp_path / "config"
    secrets_folder.mkdir()
    token = "123456789:AAtest-token"
    (secrets_folder / ".env").write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    (secrets_folder / "config.toml").write_text(
        '[notify]\ntelegram_chat_id = "555000111"\n'
    )
    monkeypatch.setenv("AUTONOMOUSE_CONFIG", str(secrets_folder))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    calls: list[tuple[str, str, dict]] = []

    def fake_telegram(sent_token: str, method: str, payload: dict) -> dict:
        calls.append((sent_token, method, payload))
        return {"ok": True, "result": {"message_id": 7}}

    monkeypatch.setattr(notify_module, "_telegram_request", fake_telegram)

    def run_cli(*argv: str) -> None:
        monkeypatch.setattr(sys, "argv", ["autonomouse", *argv])
        cli.main()

    run_cli(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1",
    )
    run_cli(
        "card", "new", "--id", "wid-a1", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t",
    )
    card_path = board_home / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))

    run_cli(
        "card", "ask", "wid-a1", "--brief", "b", "--question", "q",
        "--option", "a", "--recommendation", "a",
    )
    # The ask's own send is exercised by test_card_ask_and_answer_through_the_shell;
    # only `card cancel`'s send is what this test checks.
    calls.clear()

    run_cli("card", "cancel", "wid-a1", "--reason", "no longer needed")

    assert calls == [
        (
            token,
            "sendMessage",
            {
                "chat_id": "555000111",
                "text": (
                    "<b>02 Widgets</b>\n<b>No longer needed: q</b>\n"
                    "The card was cancelled by someone at the command line."
                ),
                "parse_mode": "HTML",
                "reply_parameters": {
                    "message_id": 7,
                    "allow_sending_without_reply": True,
                },
            },
        )
    ]
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_card_edit_and_delete_through_the_shell(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        env=env,
    )

    edited = _run(
        "card",
        "edit",
        "wid-a1",
        "--goal",
        "new goal",
        "--size",
        "m",
        "--note",
        "n",
        "--json",
        env=env,
    )
    assert edited.returncode == 0, edited.stderr
    edited_json = json.loads(edited.stdout)
    assert edited_json["goal"] == "new goal"
    assert edited_json["size"] == "m"
    assert edited_json["test"] == "t"  # untouched

    retitled = _run("card", "edit", "wid-a1", "--title", "new", "--json", env=env)
    assert retitled.returncode == 0, retitled.stderr
    retitled_json = json.loads(retitled.stdout)
    assert retitled_json["title"] == "new"
    assert retitled_json["goal"] == "new goal"  # untouched
    assert len(retitled_json["history"]) == len(edited_json["history"]) + 1
    assert retitled_json["history"][-1]["note"] == "edited: title"

    refused_empty = _run("card", "edit", "wid-a1", env=env)
    assert refused_empty.returncode != 0
    assert "nothing to edit" in refused_empty.stderr

    deleted = _run("card", "delete", "wid-a1", env=env)
    assert deleted.returncode == 0, deleted.stderr
    assert "deleted wid-a1" in deleted.stdout
    assert not (
        tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    ).exists()

    refused_again = _run("card", "delete", "wid-a1", env=env)
    assert refused_again.returncode != 0
    assert "no card" in refused_again.stderr


def test_card_edit_clear_depends_on_through_the_shell(tmp_path: Path) -> None:
    # sys-t15: a card whose dependency is cancelled counts only `done` cards
    # (situation.ready()) and stays blocked forever; `--clear-depends-on` is
    # the command that empties `depends_on` instead of a hand edit of the JSON.
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    _run(
        "card", "new", "--id", "wid-d1", "--project", "widgets", "--title", "d1",
        "--goal", "g", "--test", "t", env=env,
    )
    _run(
        "card", "new", "--id", "wid-d2", "--project", "widgets", "--title", "d2",
        "--goal", "g", "--test", "t", env=env,
    )
    _run(
        "card", "new", "--id", "wid-a1", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", "--depends-on", "wid-d1", "--depends-on", "wid-d2",
        env=env,
    )

    cleared = _run(
        "card", "edit", "wid-a1", "--clear-depends-on", "--run-id", "run-1",
        "--json", env=env,
    )
    assert cleared.returncode == 0, cleared.stderr
    cleared_json = json.loads(cleared.stdout)
    assert cleared_json["depends_on"] == []
    row = cleared_json["history"][-1]
    assert "--clear-depends-on" in row["note"]
    assert row["run_id"] == "run-1"

    both = _run(
        "card", "edit", "wid-a1", "--clear-depends-on", "--depends-on", "wid-d1",
        env=env,
    )
    assert both.returncode != 0
    assert "cannot both be given" in both.stderr


def test_card_edit_note_through_the_shell(tmp_path: Path) -> None:
    # sys-d36: `card edit --note` puts the why into the history row the edit writes.
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    _run(
        "card", "new", "--id", "wid-a1", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", env=env,
    )

    edited = _run(
        "card", "edit", "wid-a1", "--test", "new test", "--note", "x",
        "--run-id", "run-1", "--json", env=env,
    )
    assert edited.returncode == 0, edited.stderr
    row = json.loads(edited.stdout)["history"][-1]
    assert row["note"] == "edited: test; why: x"
    assert row["run_id"] == "run-1"

    blank = _run("card", "edit", "wid-a1", "--test", "t2", "--note", " ", env=env)
    assert blank.returncode != 0
    assert "--note is blank" in blank.stderr


def test_board_export_through_the_shell(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    for i in range(1, 3):
        _run(
            "card",
            "new",
            "--id",
            f"wid-a{i}",
            "--project",
            "widgets",
            "--title",
            f"card {i}",
            "--goal",
            "g",
            "--test",
            "t",
            env=env,
        )

    exported = _run("board", "export", env=env)
    assert exported.returncode == 0, exported.stderr
    export_json = json.loads(exported.stdout)
    assert {c["id"] for c in export_json["cards"]} == {"wid-a1", "wid-a2"}
    assert {p["slug"] for p in export_json["projects"]} == {"widgets"}

    out_file = tmp_path / "export.json"
    to_file = _run("board", "export", "--out", str(out_file), env=env)
    assert to_file.returncode == 0, to_file.stderr
    assert "wrote 1 project(s), 2 card(s)" in to_file.stdout
    on_disk = json.loads(out_file.read_text())
    assert {c["id"] for c in on_disk["cards"]} == {"wid-a1", "wid-a2"}


def test_card_progress_and_finish_through_the_shell(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        env=env,
    )

    # `card start` (todo -> in_progress) is exercised by its own card's tests;
    # force the card in_progress the same way the other CLI tests do.
    card_path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))

    progressed = _run(
        "card",
        "progress",
        "wid-a1",
        "--phase",
        "writing",
        "--iteration",
        "0",
        "--last-step",
        "read the charter",
        "--next-step",
        "implement the change",
        "--json",
        env=env,
    )
    assert progressed.returncode == 0, progressed.stderr
    progressed_json = json.loads(progressed.stdout)
    assert progressed_json["stack"] == "in_progress"
    assert progressed_json["phase"] == "writing"
    assert progressed_json["handoff"]["last_step"] == "read the charter"
    assert progressed_json["handoff"]["next_step"] == "implement the change"

    second = _run(
        "card",
        "progress",
        "wid-a1",
        "--phase",
        "testing",
        "--last-step",
        "ran pytest",
        env=env,
    )
    assert second.returncode == 0, second.stderr

    rows = [
        h["note"]
        for h in json.loads(
            _run("card", "show", "wid-a1", "--json", env=env).stdout
        )["history"]
    ]
    assert rows[-2:] == [
        (
            'progress: phase=writing, iteration=0, last_step="read the charter", '
            "next_step: implement the change"
        ),
        'progress: phase=testing, last_step="ran pytest"',
    ]

    refused_no_pr = _run(
        "card", "finish", "wid-a1", "--last-step", "all green", env=env
    )
    assert refused_no_pr.returncode != 0
    assert "--pr" in refused_no_pr.stderr

    finished = _run(
        "card",
        "finish",
        "wid-a1",
        "--pr",
        "https://github.com/test/widgets/pull/1",
        "--last-step",
        "ran pytest and ruff; both clean",
        "--json",
        env=env,
    )
    assert finished.returncode == 0, finished.stderr
    finished_json = json.loads(finished.stdout)
    assert finished_json["stack"] == "done"
    assert finished_json["pr"] == "https://github.com/test/widgets/pull/1"
    assert finished_json["handoff"]["last_step"] == "ran pytest and ruff; both clean"


def test_card_progress_attach_shows_in_card_show(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project",
        "new",
        "--slug",
        "widgets",
        "--name",
        "Widgets",
        "--goal",
        "g",
        "--repo",
        "r",
        "--repo-path",
        "p", "--default-branch", "main",
        "--priority",
        "1",
        env=env,
    )
    _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        env=env,
    )
    card_path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))

    progressed = _run(
        "card",
        "progress",
        "wid-a1",
        "--attach",
        "docs/screenshot.svg",
        "--json",
        env=env,
    )
    assert progressed.returncode == 0, progressed.stderr
    assert json.loads(progressed.stdout)["attachments"] == ["docs/screenshot.svg"]

    shown = _run("card", "show", "wid-a1", env=env)
    assert shown.returncode == 0, shown.stderr
    assert "attachments: docs/screenshot.svg" in shown.stdout


def test_card_new_refused_without_project(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    result = _run(
        "card",
        "new",
        "--id",
        "wid-a1",
        "--project",
        "widgets",
        "--title",
        "t",
        "--goal",
        "g",
        "--test",
        "t",
        env=env,
    )
    assert result.returncode != 0
    assert "no project" in result.stderr


def _board_with_two_cards(home: Path) -> None:
    """A small board made through the shell: one project, two cards."""
    env = dict(os.environ, AUTONOMOUSE_HOME=str(home))
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "https://github.com/test/widgets", "--repo-path", "/home/test/widgets",
        "--default-branch", "main", "--priority", "1", env=env,
    )
    assert made.returncode == 0, made.stderr
    for card_id in ("wid-a1", "wid-a2"):
        made = _run(
            "card", "new", "--id", card_id, "--project", "widgets", "--title", "t",
            "--goal", "g", "--test", "t", env=env,
        )
        assert made.returncode == 0, made.stderr


def test_board_import_creates_everything_then_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _board_with_two_cards(source)
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))

    first = _run("board", "import", str(source), "--json", env=env)
    assert first.returncode == 0, first.stderr
    first_json = json.loads(first.stdout)
    assert first_json["projects_created"] == ["widgets"]
    assert first_json["projects_skipped"] == []
    assert sorted(first_json["cards_created"]) == ["wid-a1", "wid-a2"]
    assert first_json["cards_skipped"] == []
    assert (tmp_path / "board" / "projects" / "widgets" / "project.json").is_file()
    for card_id in ("wid-a1", "wid-a2"):
        assert (tmp_path / "board" / "projects" / "widgets" / "cards" / f"{card_id}.json").is_file()

    second = _run("board", "import", str(source), "--json", env=env)
    assert second.returncode == 0, second.stderr
    second_json = json.loads(second.stdout)
    assert second_json["projects_created"] == []
    assert second_json["projects_skipped"] == ["widgets"]
    assert second_json["cards_created"] == []
    assert sorted(second_json["cards_skipped"]) == ["wid-a1", "wid-a2"]


def test_board_import_refuses_dir_without_projects(tmp_path: Path) -> None:
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = _run("board", "import", str(empty_dir), env=env)
    assert result.returncode != 0
    assert "no projects/" in result.stderr


def _new_card(card_id: str, *depends_on: str, env) -> subprocess.CompletedProcess:
    deps = [a for dep in depends_on for a in ("--depends-on", dep)]
    return _run(
        "card", "new", "--id", card_id, "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", *deps, "--json", env=env,
    )


def test_card_new_and_edit_refuse_depends_on_that_names_no_card(tmp_path: Path) -> None:
    """sys-b14's test: `--depends-on sys-nope` exits non-zero naming it; `'a,b'`
    exits non-zero with the repeat hint; two real ids are stored."""
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    assert made.returncode == 0, made.stderr
    for card_id in ("wid-a1", "wid-a2"):
        assert _new_card(card_id, env=env).returncode == 0

    unknown = _new_card("wid-a3", "sys-nope", env=env)
    assert unknown.returncode != 0
    assert "sys-nope" in unknown.stderr
    assert not (tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a3.json").exists()

    joined = _new_card("wid-a3", "wid-a1,wid-a2", env=env)
    assert joined.returncode != 0
    assert "--depends-on <id> --depends-on <id>" in joined.stderr

    both = _new_card("wid-a3", "wid-a1", "wid-a2", env=env)
    assert both.returncode == 0, both.stderr
    assert json.loads(both.stdout)["depends_on"] == ["wid-a1", "wid-a2"]

    edit_unknown = _run("card", "edit", "wid-a3", "--depends-on", "sys-nope", env=env)
    assert edit_unknown.returncode != 0
    assert "sys-nope" in edit_unknown.stderr
    edit_joined = _run("card", "edit", "wid-a3", "--depends-on", "wid-a1,wid-a2", env=env)
    assert edit_joined.returncode != 0
    assert "--depends-on <id> --depends-on <id>" in edit_joined.stderr
    shown = _run("card", "show", "wid-a3", "--json", env=env)
    assert json.loads(shown.stdout)["depends_on"] == ["wid-a1", "wid-a2"]

    edited = _run("card", "edit", "wid-a3", "--depends-on", "wid-a2", env=env)
    assert edited.returncode == 0, edited.stderr
    shown = _run("card", "show", "wid-a3", "--json", env=env)
    assert json.loads(shown.stdout)["depends_on"] == ["wid-a2"]


def test_card_edit_refuses_a_card_that_depends_on_itself(tmp_path: Path) -> None:
    """sys-t19's test: `card edit wid-a3 --depends-on wid-a3` exits non-zero naming
    the card, in the same form as the unknown-id refusal."""
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    assert made.returncode == 0, made.stderr
    assert _new_card("wid-a3", env=env).returncode == 0

    self_dep = _run("card", "edit", "wid-a3", "--depends-on", "wid-a3", env=env)
    assert self_dep.returncode != 0
    assert "wid-a3" in self_dep.stderr
    shown = _run("card", "show", "wid-a3", "--json", env=env)
    assert json.loads(shown.stdout)["depends_on"] == []


# sys-j5: a command refused while it carries a run's `--run-id` writes the refusal on that
# run's record, and still prints the same error line and exits 1.

_RUN = "20260927-010000-0001"


def _board_with_run(tmp_path: Path) -> dict:
    """A board with project widgets, card wid-a1 and an alive run record `_RUN` whose
    pid is this test's own process; claude's transcripts folder is empty."""
    claude_dir = tmp_path / "claude"
    (claude_dir / "projects").mkdir(parents=True)
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        AUTONOMOUSE_CONFIG=str(tmp_path / "config"),
        CLAUDE_CONFIG_DIR=str(claude_dir),
    )
    env.pop("TELEGRAM_BOT_TOKEN", None)
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1",
        env=env,
    )
    assert made.returncode == 0, made.stderr
    assert _new_card("wid-a1", env=env).returncode == 0
    started = _run(
        "run", "start", _RUN, "--session-id", "s1", "--pid", str(os.getpid()),
        "--model", "opus", env=env,
    )
    assert started.returncode == 0, started.stderr
    return env


def _refusals(tmp_path: Path, run_id: str = _RUN) -> list[tuple[str, str]]:
    path = tmp_path / "board" / "runs" / f"{run_id}.json"
    return [(r["command"], r["reason"]) for r in json.loads(path.read_text())["refusals"]]


def test_a_card_ask_with_a_blank_brief_is_recorded_on_its_run(tmp_path: Path) -> None:
    env = _board_with_run(tmp_path)
    ask = (
        "card", "ask", "wid-a1", "--brief", " ", "--question", "q", "--option", "a",
        "--recommendation", "a",
    )
    refused = _run(*ask, "--run-id", _RUN, env=env)
    assert refused.returncode == 1
    assert refused.stderr == "error: ask needs --brief\n"
    assert _refusals(tmp_path) == [("card ask", "ask needs --brief")]

    # Run by hand (no --run-id, the default `manual`): the same line, nothing written.
    by_hand = _run(*ask, env=env)
    assert (by_hand.returncode, by_hand.stderr) == (1, "error: ask needs --brief\n")
    assert _refusals(tmp_path) == [("card ask", "ask needs --brief")]
    assert sorted(p.name for p in (tmp_path / "board" / "runs").iterdir()) == [
        f"{_RUN}.json"
    ]


def test_a_card_new_whose_depends_on_names_no_card_is_recorded_on_its_run(
    tmp_path: Path,
) -> None:
    env = _board_with_run(tmp_path)
    refused = _run(
        "card", "new", "--id", "wid-a2", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", "--depends-on", "sys-nope", "--run-id", _RUN,
        env=env,
    )
    assert refused.returncode == 1
    [(command, reason)] = _refusals(tmp_path)
    assert command == "card new" and "sys-nope" in reason
    assert refused.stderr == f"error: {reason}\n"


def test_a_project_claim_of_a_held_project_is_recorded_on_its_run(tmp_path: Path) -> None:
    env = _board_with_run(tmp_path)
    assert _run("project", "claim", "widgets", "--run-id", _RUN, env=env).returncode == 0
    second = "20260927-010000-0002"
    started = _run(
        "run", "start", second, "--session-id", "s2", "--pid", str(os.getpid()),
        "--model", "opus", env=env,
    )
    assert started.returncode == 0, started.stderr

    refused = _run("project", "claim", "widgets", "--run-id", second, env=env)
    assert refused.returncode == 1
    [(command, reason)] = _refusals(tmp_path, second)
    assert command == "project claim" and _RUN in reason and "stalled" in reason
    assert refused.stderr == f"error: {reason}\n"
    assert _refusals(tmp_path) == []


def test_a_card_file_that_fails_validation_is_recorded_on_its_run(tmp_path: Path) -> None:
    env = _board_with_run(tmp_path)
    card_path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["size"] = "huge"
    card_path.write_text(json.dumps(data))

    refused = _run("card", "move", "wid-a1", "--to", "backlog", "--run-id", _RUN, env=env)
    assert refused.returncode == 1
    [(command, reason)] = _refusals(tmp_path)
    assert command == "card move" and "size" in reason
    assert refused.stderr == f"error: {reason}\n"


def test_a_refusal_on_a_run_id_with_no_record_prints_as_before(tmp_path: Path) -> None:
    """A planning thread's id is no run record: the refusal prints and writes nothing."""
    env = _board_with_run(tmp_path)
    refused = _run(
        "card", "ask", "wid-a1", "--brief", "", "--question", "q", "--option", "a",
        "--recommendation", "a", "--run-id", "plan-20260927-010000-abcd", env=env,
    )
    assert (refused.returncode, refused.stderr) == (1, "error: ask needs --brief\n")
    assert _refusals(tmp_path) == []
    assert not (tmp_path / "board" / "runs" / "plan-20260927-010000-abcd.json").exists()


def test_a_refusal_whose_record_write_fails_still_prints_and_exits_1(
    tmp_path: Path,
) -> None:
    env = _board_with_run(tmp_path)
    run_path = tmp_path / "board" / "runs" / f"{_RUN}.json"
    run_path.write_text("{not json")
    refused = _run(
        "card", "ask", "wid-a1", "--brief", "", "--question", "q", "--option", "a",
        "--recommendation", "a", "--run-id", _RUN, env=env,
    )
    assert refused.returncode == 1
    first, second = refused.stderr.splitlines()
    assert first == "error: ask needs --brief"
    assert second.startswith(f"error: the refusal was not recorded on run {_RUN}:")


# sys-t39: a planner session runs its board commands with `--run-id <thread id>`
# (`docs/planner.md`), a shape no run record's own id ever has (`new_run_id` gives
# `<YYYYMMDD-HHMMSS>-<4 hex>`; a thread's is `plan-<YYYYMMDD-HHMMSS>-<4 hex>`). A refusal
# under that id lands on the alive planner record whose `thread` is that id.

_THREAD = "plan-20260927-010000-ab12"
_PLANNER_RUN = "20260927-010000-cd34"


def _board_with_planner_run(tmp_path: Path, *, pid: int) -> dict:
    """A board with project widgets, card wid-a1, and a run record `_PLANNER_RUN` of
    kind `planner` whose `thread` is `_THREAD`, with `pid` and a transcript so its
    liveness is `alive` when `pid` is this test's own process."""
    claude_dir = tmp_path / "claude"
    session_dir = claude_dir / "projects" / "-fake"
    session_dir.mkdir(parents=True)
    (session_dir / "planner-s1.jsonl").write_text('{"type":"user"}\n')
    env = dict(
        os.environ,
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        AUTONOMOUSE_CONFIG=str(tmp_path / "config"),
        CLAUDE_CONFIG_DIR=str(claude_dir),
    )
    env.pop("TELEGRAM_BOT_TOKEN", None)
    made = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1",
        env=env,
    )
    assert made.returncode == 0, made.stderr
    assert _new_card("wid-a1", env=env).returncode == 0
    from autonomouse.board.runs import RunKind, run_start

    run_start(
        tmp_path / "board",
        run_id=_PLANNER_RUN,
        session_id="planner-s1",
        pid=pid,
        model="opus",
        kind=RunKind.PLANNER,
        project="widgets",
        thread=_THREAD,
    )
    return env


def test_a_card_new_refused_under_a_thread_run_id_is_recorded_on_the_alive_planner_run(
    tmp_path: Path,
) -> None:
    env = _board_with_planner_run(tmp_path, pid=os.getpid())
    refused = _run(
        "card", "new", "--id", "wid-a2", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", "--depends-on", "sys-nope", "--run-id", _THREAD,
        env=env,
    )
    assert refused.returncode == 1
    [(command, reason)] = _refusals(tmp_path, _PLANNER_RUN)
    assert command == "card new" and "sys-nope" in reason
    assert refused.stderr == f"error: {reason}\n"
    # nothing was written under the thread id itself — no run record has that shape
    assert not (tmp_path / "board" / "runs" / f"{_THREAD}.json").exists()


def test_a_card_new_refused_under_a_thread_run_id_with_no_alive_planner_writes_nothing(
    tmp_path: Path,
) -> None:
    """A planner record for the thread that is not alive (its claude process ended) is
    no target either: the refusal prints and nothing is written, same as a thread with no
    record at all."""
    env = _board_with_planner_run(tmp_path, pid=_dead_pid())
    refused = _run(
        "card", "new", "--id", "wid-a2", "--project", "widgets", "--title", "t",
        "--goal", "g", "--test", "t", "--depends-on", "sys-nope", "--run-id", _THREAD,
        env=env,
    )
    assert refused.returncode == 1
    assert _refusals(tmp_path, _PLANNER_RUN) == []


def _dead_pid() -> int:
    """A pid that existed and is gone: a child that exited and was reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid
