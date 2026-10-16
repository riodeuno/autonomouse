"""The non-convergence replan (`docs/orchestrator/splitting-and-replanning.md`),
run through the shell exactly as the orchestrator runs it: a card forced to
iteration 5 goes back to todo with a note naming the cause, then is either split
into two smaller todo cards or rewritten in place with its attempts reset.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

CAUSE = "iteration 5; the review keeps asking for a schema change the goal forbids"


def _run(*args, env):
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result


def _show(card_id: str, env) -> dict:
    return json.loads(_run("card", "show", card_id, "--json", env=env).stdout)


def _card_at_the_limit(tmp_path: Path) -> dict:
    """A project with `wid-a1` in_progress at iteration 5, and `wid-a2` depending on it."""
    env = dict(os.environ, AUTONOMOUSE_HOME=str(tmp_path / "board"))
    _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )
    _run(
        "card", "new", "--id", "wid-a0", "--project", "widgets", "--title", "base",
        "--goal", "g", "--test", "t", env=env,
    )
    _run(
        "card", "new", "--id", "wid-a1", "--project", "widgets", "--title", "big",
        "--goal", "build the parser and the renderer", "--test", "t",
        "--position", "3", "--limit", "no new dependencies",
        "--depends-on", "wid-a0", env=env,
    )
    _run(
        "card", "new", "--id", "wid-a2", "--project", "widgets", "--title", "after",
        "--goal", "g", "--test", "t", "--depends-on", "wid-a1", env=env,
    )

    # `card start` needs a live claimed session (its own tests cover it); force the
    # card in_progress the way the other CLI tests do, then let the worker's own
    # command carry it to the attempt limit.
    card_path = tmp_path / "board" / "projects" / "widgets" / "cards" / "wid-a1.json"
    data = json.loads(card_path.read_text())
    data["stack"] = "in_progress"
    card_path.write_text(json.dumps(data))
    _run(
        "card", "progress", "wid-a1", "--phase", "review", "--iteration", "5",
        "--last-step", f"stopped at the limit: {CAUSE}",
        "--next-step", "change the schema as the reviewer asks", env=env,
    )
    assert _show("wid-a1", env)["iteration"] == 5
    return env


def test_card_at_iteration_5_is_split_into_two_todo_cards(tmp_path: Path) -> None:
    env = _card_at_the_limit(tmp_path)
    run = ["--run-id", "20260922-100000-0001"]

    _run("card", "move", "wid-a1", "--to", "todo", "--note", f"replanned: {CAUSE}",
         *run, env=env)
    parent = _show("wid-a1", env)
    children = [("wid-a3", "build the parser"), ("wid-a4", "build the renderer")]
    for child_id, goal in children:
        _run(
            "card", "new", "--id", child_id, "--project", "widgets", "--title", goal,
            "--goal", goal, "--test", "t", "--size", "xs",
            "--position", str(parent["position"]),
            *[a for dep in parent["depends_on"] for a in ("--depends-on", dep)],
            *[a for lim in parent["limits"] for a in ("--limit", lim)],
            "--note", f"split from wid-a1: {CAUSE}", *run, env=env,
        )
    _run("card", "edit", "wid-a2", "--depends-on", "wid-a3", "--depends-on", "wid-a4",
         *run, env=env)
    _run("card", "cancel", "wid-a1", "--reason", f"split into wid-a3, wid-a4: {CAUSE}",
         *run, env=env)

    parent = _show("wid-a1", env)
    assert parent["stack"] == "cancelled"
    notes = [(h["from"], h["to"], h["note"]) for h in parent["history"]]
    assert ("in_progress", "todo", f"replanned: {CAUSE}") in notes
    assert notes[-1] == ("todo", "cancelled", f"split into wid-a3, wid-a4: {CAUSE}")

    todo = {
        c["id"]: c
        for c in json.loads(
            _run("card", "list", "--project", "widgets", "--stack", "todo", "--json",
                 env=env).stdout
        )
    }
    assert sorted(todo) == ["wid-a0", "wid-a2", "wid-a3", "wid-a4"]
    for child in (todo["wid-a3"], todo["wid-a4"]):
        assert child["position"] == 3
        assert child["depends_on"] == ["wid-a0"]
        assert "no new dependencies" in child["limits"]
        assert child["iteration"] == 0
        # `card new` puts the child in todo, and its note is the creation row's: no move.
        [created] = child["history"]
        assert (created["from"], created["to"]) == (None, "todo")
        assert created["note"] == f"created via card new; why: split from wid-a1: {CAUSE}"
    assert _show("wid-a2", env)["depends_on"] == ["wid-a3", "wid-a4"]


def test_card_at_iteration_5_rewritten_in_place_starts_its_attempts_over(
    tmp_path: Path,
) -> None:
    env = _card_at_the_limit(tmp_path)
    run = ["--run-id", "20260922-100000-0001"]

    _run("card", "move", "wid-a1", "--to", "todo", "--note", f"replanned: {CAUSE}",
         *run, env=env)
    # The charter's rewrite: limits, test or notes, never the goal (rules, item 6).
    _run("card", "edit", "wid-a1", "--limit", "no new dependencies",
         "--limit", "the schema stays as it is", "--test", "parser test passes",
         "--note", CAUSE, *run, env=env)

    card = _show("wid-a1", env)
    assert card["stack"] == "todo"
    assert card["goal"] == "build the parser and the renderer"
    assert "the schema stays as it is" in card["limits"]
    assert card["test"] == "parser test passes"
    assert card["iteration"] == 0
    assert card["phase"] is None
    assert card["handoff"]["next_step"] is None
    assert card["handoff"]["last_step"] == f"stopped at the limit: {CAUSE}"
    notes = [h["note"] for h in card["history"]]
    assert f"replanned: {CAUSE}" in notes
    assert notes[-1] == (
        "edited: limits, test; attempts reset (iteration 0, phase and next_step cleared); "
        f"why: {CAUSE}"
    )


def test_a_new_limit_alone_starts_the_attempts_over(tmp_path: Path) -> None:
    # docs/orchestrator/splitting-and-replanning.md: a loosened limit (rules, item 10) is a
    # new plan too, so the card's next worker does not stop at iteration 5.
    env = _card_at_the_limit(tmp_path)
    _run("card", "edit", "wid-a1", "--limit", "the schema stays as it is",
         "--note", CAUSE, env=env)
    card = _show("wid-a1", env)
    assert card["limits"] == ["docs/rules.md applies", "the schema stays as it is"]
    assert card["test"] == "t"
    assert card["iteration"] == 0
    assert card["phase"] is None
    assert card["handoff"]["next_step"] is None
    assert card["handoff"]["last_step"] == f"stopped at the limit: {CAUSE}"
    assert card["history"][-1]["note"] == (
        "edited: limits; attempts reset (iteration 0, phase and next_step cleared); "
        f"why: {CAUSE}"
    )


def test_a_new_title_keeps_the_attempts(tmp_path: Path) -> None:
    # a title names the card; it is not the plan, so the attempts stand
    env = _card_at_the_limit(tmp_path)
    _run("card", "edit", "wid-a1", "--title", "parser and renderer", env=env)
    card = _show("wid-a1", env)
    assert card["title"] == "parser and renderer"
    assert card["iteration"] == 5
    assert card["phase"] == "review"
    assert card["handoff"]["next_step"] == "change the schema as the reviewer asks"
    assert card["history"][-1]["note"] == "edited: title"


def test_edit_without_goal_limits_or_test_keeps_the_attempts(tmp_path: Path) -> None:
    env = _card_at_the_limit(tmp_path)
    _run("card", "edit", "wid-a1", "--size", "m", "--note", CAUSE, env=env)
    card = _show("wid-a1", env)
    assert card["iteration"] == 5
    assert card["handoff"]["next_step"] == "change the schema as the reviewer asks"
