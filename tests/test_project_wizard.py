"""`autonomouse project new <name>`, the create-project wizard (sys-s2), run through the
shell the way a human on a fresh machine runs it: answers on stdin, or flags.

GitHub is a local bare repository: a temporary global git config rewrites both URL forms,
`https://github.com/test/widgets` and `git@github.com:test/widgets.git`, to its file URL
(`url.<file URL>.insteadOf`), so `git clone` never leaves the machine. `AUTONOMOUSE_HOME`,
`AUTONOMOUSE_REPOS`, `AUTONOMOUSE_WORKTREES` and `AUTONOMOUSE_CONFIG` point at empty
temporary folders; `HOME` at another, so the crontab lines `timer.install()` writes carry its paths; and `crontab` on
`PATH` is a fake that stores its input in a file, so the lines are written to it and never
to the machine's crontab. `gh` on `PATH` is a fake too (sys-s3): logged in, one account, a
pull request it "opens" and "closes", an empty `workflow_runs` list; it records its calls.
`claude` on `PATH` is the fake below: logged in, the GitHub access check (sys-w26) and the
planner (sys-t5), which `project new` runs on the project's brief thread;
`AUTONOMOUSE_STATE`, where the planner's result files go, is one more temporary folder.
"""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autonomouse import github
from autonomouse.board.brief import load_brief
from autonomouse.board.paths import repos_root, worktrees_root
from autonomouse.timer import MARKER
from test_timer import FAKE_CRONTAB

HTTPS = "https://github.com/test/widgets"
SSH = "git@github.com:test/widgets.git"
# Enter on the three questions after the clone (sys-w9): the name and the email the agents
# commit with, and the branch their pull requests go into.
ENTER_THREE = "\n\n\n"

# A fake `claude`. `auth status` succeeds unless $FAKE_DIR/claude-logged-out exists. A `-p`
# whose prompt is a probe (it holds the probe runner's "## How you report") is the GitHub
# access check (sys-w26): it records its arguments and working folder in probe-calls/<n>.json,
# prints two step lines and a report, stream-json as the runner reads it, with every
# permission ok unless $FAKE_DIR/probe-report.json holds another report. A probe whose
# prompt holds "# Probe: Linear" is the Linear link's access check (sys-w15) instead: it
# records the same in linear-probe-calls/<n>.json and prints one step line and a report
# with every permission ok and a note, unless $FAKE_DIR/linear-report.json holds another. Every other call
# stands for the planner on a project's brief thread (docs/planner.md, "The brief"); with
# $FAKE_DIR/planner-fails it prints "not logged in" and exits 1. It records its arguments in planner-calls/<n>.json; called without `-p`, as
# a live session (`plan join`) calls it, it exits 0 there. Otherwise it answers only through
# the CLI, after reading the thread: with no planner message yet it asks what the project is
# for; after the human's answer it says that answer back as the goal sentence and asks for a
# yes; after the yes it writes the brief, with the seven headings of
# `autonomouse.board.brief.HEADINGS` and the confirmed goal sentence under `## Goal`, to a
# draft file, then runs `plan brief <thread-id> <draft>` as the request names, and closes
# the thread.
FAKE_PLANNER = f"""#!{sys.executable}
import json, os, re, subprocess, sys
from autonomouse.board.brief import HEADINGS

d = os.environ["FAKE_DIR"]
if sys.argv[1:3] == ["auth", "status"]:
    logged_in = not os.path.exists(os.path.join(d, "claude-logged-out"))
    print(json.dumps({{"loggedIn": logged_in}}))
    sys.exit(0 if logged_in else 1)
prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else ""
if "# Probe: Linear" in prompt and "## How you report" in prompt:
    probes = os.path.join(d, "linear-probe-calls")
    os.makedirs(probes, exist_ok=True)
    with open(os.path.join(probes, f"{{len(os.listdir(probes)) + 1}}.json"), "w") as f:
        json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, f)
    path = os.path.join(d, "linear-report.json")
    names = ["linear_mcp", "read_link", "create_issue", "remove_test_issue"]
    report = open(path).read() if os.path.exists(path) else json.dumps({{"permissions": [
        {{"name": n, "required": True, "status": "ok", "fix": ""}} for n in names
    ], "notes": ["Deleted the test issue WID-57"]}})
    print(json.dumps({{"type": "assistant", "message": {{"content": [
        {{"type": "text", "text": "STEP: Creating a test issue; it will be removed"}}]}}}}))
    print(json.dumps({{"type": "result", "is_error": False, "result": report}}))
    sys.exit(0)
if "## How you report" in prompt:
    probes = os.path.join(d, "probe-calls")
    os.makedirs(probes, exist_ok=True)
    with open(os.path.join(probes, f"{{len(os.listdir(probes)) + 1}}.json"), "w") as f:
        json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, f)
    path = os.path.join(d, "probe-report.json")
    names = ["push", "pull_requests", "ci_status", "review_comments",
             "merge_conflicts", "cancel_run", "close_pull_request", "delete_branch"]
    report = open(path).read() if os.path.exists(path) else json.dumps({{"permissions": [
        {{"name": n, "required": i < 5, "status": "ok", "fix": ""}} for i, n in enumerate(names)
    ]}})
    for step in ("Checking that CI status can be read", "Removed the branch; nothing changed"):
        print(json.dumps({{"type": "assistant", "message": {{"content": [
            {{"type": "text", "text": "STEP: " + step}}]}}}}))
    print(json.dumps({{"type": "result", "is_error": False, "result": report}}))
    sys.exit(0)
if os.path.exists(os.path.join(d, "planner-fails")):
    print("not logged in", file=sys.stderr)
    sys.exit(1)
calls = os.path.join(d, "planner-calls")
os.makedirs(calls, exist_ok=True)
with open(os.path.join(calls, f"{{len(os.listdir(calls)) + 1}}.json"), "w") as f:
    json.dump(sys.argv[1:], f)
if "-p" not in sys.argv:
    sys.exit(0)

def cli(*a):
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *a],
        check=True, capture_output=True, text=True,
    ).stdout

thread_id = re.search(r"plan-\\d{{8}}-\\d{{6}}-[0-9a-f]{{4}}", prompt).group(0)
thread = json.loads(cli("plan", "show", thread_id, "--json"))
said = [m["text"] for m in thread["messages"] if m["from"] == "planner"]
answer = thread["messages"][-1]["text"]
if not said:
    cli("plan", "say", thread_id, f"What is {{thread['project']}} for: why does it exist?")
elif len(said) == 1:
    cli("plan", "say", thread_id, f"Goal sentence: {{answer}} Is that right?")
else:
    goal = said[1].removeprefix("Goal sentence: ").removesuffix(" Is that right?")
    body = {{"Goal": goal, "Intent": "The creator wants widgets sold."}}
    draft = os.path.join(os.environ["FAKE_DIR"], "draft.md")
    with open(draft, "w") as f:
        f.write("\\n".join(f"## {{h}}\\n\\n{{body.get(h, h)}}\\n" for h in HEADINGS))
    cli("plan", "brief", thread_id, draft)
    cli("plan", "close", thread_id, "The brief is written.")
print(json.dumps({{"type": "result", "is_error": False, "result": "ok"}}))
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    source = tmp_path / "source"
    _git(tmp_path, "init", "-q", "-b", "trunk", str(source))
    _git(source, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "first")  # fmt: skip
    remote = tmp_path / "github.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "trunk", str(remote))
    _git(source, "push", "-q", str(remote), "trunk")
    global_git = tmp_path / "gitconfig"
    global_git.write_text(
        f'[url "file://{remote}"]\n\tinsteadOf = {HTTPS}\n\tinsteadOf = {SSH}\n'
    )
    stub = tmp_path / "stub"
    stub.mkdir()
    crontab = stub / "crontab"
    crontab.write_text(FAKE_CRONTAB.format(store=tmp_path / "crontab.txt"))
    crontab.chmod(0o755)
    gh = stub / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{tmp_path}/gh.calls"\n'
        'case "$1 $2" in\n'
        '  "auth status") exit 0 ;;\n'
        '  "api user") echo \'{"login":"tess","id":7,"name":"Tess Tester"}\' ;;\n'
        '  "api repos/"*) echo \'{"workflow_runs":[]}\' ;;\n'
        '  "pr create") echo "https://github.com/test/widgets/pull/1" ;;\n'
        '  "pr close") exit 0 ;;\n'
        '  *) echo "fake gh: unexpected: $*" >&2; exit 1 ;;\n'
        "esac\n"
    )
    gh.chmod(0o755)
    claude = stub / "claude"
    claude.write_text(FAKE_PLANNER)
    claude.chmod(0o755)
    (tmp_path / "home").mkdir()
    return dict(
        os.environ,
        HOME=str(tmp_path / "home"),
        PATH=f"{stub}:{os.environ['PATH']}",
        GIT_CONFIG_GLOBAL=str(global_git),
        GIT_CONFIG_NOSYSTEM="1",
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        AUTONOMOUSE_REPOS=str(tmp_path / "repos"),
        AUTONOMOUSE_WORKTREES=str(tmp_path / "worktrees"),
        AUTONOMOUSE_CONFIG=str(tmp_path / "config"),
        AUTONOMOUSE_STATE=str(tmp_path / "state"),
        FAKE_DIR=str(tmp_path),
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _run(*args: str, env: dict[str, str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _projects(env: dict[str, str]) -> list[dict]:
    listed = _run("project", "list", "--json", env=env)
    assert listed.returncode == 0, listed.stderr
    return json.loads(listed.stdout)


def _probe_calls(env: dict[str, str]) -> list[dict]:
    """Each run of the GitHub access check: the fake claude's argv and working folder."""
    calls = Path(env["FAKE_DIR"]) / "probe-calls"
    paths = sorted(calls.glob("*.json"), key=lambda p: int(p.stem)) if calls.exists() else []
    return [json.loads(p.read_text()) for p in paths]


def _probe_facts(env: dict[str, str]) -> dict[str, str]:
    """The facts the one access check of this test was given, from its prompt."""
    [call] = _probe_calls(env)
    prompt = call["argv"][call["argv"].index("-p") + 1]
    facts = prompt.split("## Facts\n", 1)[1].split("\n\n## ", 1)[0]
    return dict(line[2:].split(": ", 1) for line in facts.strip().splitlines())


def _cron_jobs(env: dict[str, str]) -> list[str]:
    """The jobs of the autonomouse lines in the fake crontab, from their marker comments."""
    store = Path(env["HOME"]).parent / "crontab.txt"
    lines = store.read_text().splitlines() if store.exists() else []
    return [line.rsplit(f"{MARKER} ", 1)[1] for line in lines if MARKER in line]


def _gh_calls(env: dict[str, str]) -> list[str]:
    log = Path(env["HOME"]).parent / "gh.calls"
    return log.read_text().splitlines() if log.exists() else []


def _threads(env: dict[str, str]) -> list[dict]:
    listed = _run("plan", "list", "--json", env=env)
    assert listed.returncode == 0, listed.stderr
    return json.loads(listed.stdout)


def _cards(env: dict[str, str]) -> list[dict]:
    listed = _run("card", "list", "--json", env=env)
    assert listed.returncode == 0, listed.stderr
    return json.loads(listed.stdout)


def _planner_calls(env: dict[str, str]) -> list[list[str]]:
    calls = Path(env["FAKE_DIR"]) / "planner-calls"
    paths = sorted(calls.glob("*.json"), key=lambda p: int(p.stem)) if calls.exists() else []
    return [json.loads(p.read_text()) for p in paths]


def _telegram_steps(text: str) -> list[str]:
    return [
        step
        for step in ("TELEGRAM_BOT_TOKEN=", "message the bot once", "autonomouse notify setup")
        if step in text
    ]


def test_answers_on_stdin_make_the_board_the_clone_the_project_and_the_timers(env) -> None:
    board = Path(env["AUTONOMOUSE_HOME"])
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widget-works"

    result = _run("project", "new", "Widget Works", env=env, stdin=f"{HTTPS}\n{ENTER_THREE}")

    assert result.returncode == 0, result.stderr
    assert _git(board, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    [thread] = _threads(env)
    assert _git(board, "log", "--format=%s").splitlines() == [
        f"autonomouse project new widget-works: brief thread {thread['id']}",
        "autonomouse project new widget-works",
    ]
    assert _git(board, "status", "--porcelain") == ""
    assert _git(clone, "remote", "get-url", "origin") == HTTPS
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "trunk"
    # Enter kept the three defaults (sys-w9): the fake gh account's name and no-reply
    # address, set on the clone, and the clone's origin/HEAD.
    assert "Name the agents commit with [Tess Tester]: " in result.stdout
    assert "Email the agents commit with [7+tess@users.noreply.github.com]: " in result.stdout
    assert "Branch the agents' pull requests go into [trunk]: " in result.stdout
    assert _git(clone, "config", "--local", "user.name") == "Tess Tester"
    assert _git(clone, "config", "--local", "user.email") == "7+tess@users.noreply.github.com"
    # The access check (sys-w26): the GitHub probe agent ran once, in the clone, on the
    # repository and the chosen branch, and its step lines were printed as it went.
    [probe] = _probe_calls(env)
    assert Path(probe["cwd"]) == clone
    facts = _probe_facts(env)
    assert (facts["repository"], facts["branch"], facts["push over"]) == ("test/widgets", "trunk", "https")
    assert facts["probe branch"] == "probe/autonomouse"
    assert "access: Checking that CI status can be read\naccess: Removed the branch" in result.stdout
    assert ALL_OK_LINE in result.stdout
    assert [c.split()[:2] for c in _gh_calls(env)] == [["auth", "status"], ["api", "user"]]
    [project] = _projects(env)
    assert project["slug"] == "widget-works"
    assert project["name"] == "Widget Works"
    assert project["goal"] == ""  # no question asks it; planning writes it (sys-w103)
    assert project["repo"] == HTTPS
    assert project["repo_path"] == str(clone)
    assert project["default_branch"] == "trunk"
    assert project["priority"] == 1
    # install() ran once: the three marked lines are in the fake crontab (sys-s6).
    assert _cron_jobs(env) == ["wake", "review", "plan"]
    # No .env: the output names the three Telegram steps.
    assert _telegram_steps(result.stdout) == [
        "TELEGRAM_BOT_TOKEN=", "message the bot once", "autonomouse notify setup",
    ]  # fmt: skip


def test_a_default_branch_flag_is_recorded_instead_of_the_clones_origin_head(env, tmp_path) -> None:
    """sys-s97: the env fixture's remote has origin/HEAD trunk. `--default-branch develop`
    names another branch that exists on that remote, and it is what gets recorded, not
    origin/HEAD; fails on card/sys-s89, whose clone path called `_default_branch(clone)`
    unconditionally and dropped the flag."""
    source = tmp_path / "source"
    remote = tmp_path / "github.git"
    _git(source, "checkout", "-q", "-b", "develop")
    _git(source, "push", "-q", str(remote), "develop")

    result = _run(
        "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g",
        "--default-branch", "develop", env=env,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"
    # The clone itself still checks out origin/HEAD; only the recorded default_branch changes.
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "trunk"
    [project] = _projects(env)
    assert project["default_branch"] == "develop"


def _main_and_develop(tmp_path: Path) -> Path:
    """Make the fixture's remote a repository with branches main (its default, origin/HEAD)
    and develop, a commit ahead of main, and no trunk; return the remote."""
    source, remote = tmp_path / "source", tmp_path / "github.git"
    _git(source, "push", "-q", str(remote), "trunk:main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(remote, "update-ref", "-d", "refs/heads/trunk")
    _git(source, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "on develop")  # fmt: skip
    _git(source, "push", "-q", str(remote), "trunk:develop")
    return remote


def test_typed_answers_set_the_clones_identity_and_the_branch_the_check_uses(env, tmp_path) -> None:
    """sys-w9: after the clone, `project new` asks the name and email the agents commit with
    and the branch their pull requests go into. The typed answers are the clone's own
    user.name and user.email and the project's default_branch, and the access check is given
    develop, the branch it starts from and opens its draft pull request against. Fails on
    main, whose wizard asked none of the three and recorded origin/HEAD."""
    _main_and_develop(tmp_path)
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"

    result = _run(
        "project", "new", "Widgets", env=env,
        stdin=f"{HTTPS}\nAda Lovelace\nada@example.com\ndevelop\n",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(clone, "config", "--local", "user.name") == "Ada Lovelace"
    assert _git(clone, "config", "--local", "user.email") == "ada@example.com"
    assert "Branch the agents' pull requests go into [main]: " in result.stdout
    [project] = _projects(env)
    assert (project["default_branch"], project["goal"]) == ("develop", "")
    assert _probe_facts(env)["branch"] == "develop"
    assert _git(clone, "var", "GIT_AUTHOR_IDENT").startswith("Ada Lovelace <ada@example.com>")


def test_a_clone_whose_git_already_resolves_an_author_is_still_asked(env, tmp_path) -> None:
    """sys-w9, the owner's choice of 2026-09-29: the questions are asked even when git
    resolves an author (here from the global config), and the answer, not that author, is
    what the clone commits with. Fails on main, which asked nothing when git resolved one."""
    global_git = Path(env["GIT_CONFIG_GLOBAL"])
    global_git.write_text(global_git.read_text() + "[user]\n\tname = Old Name\n\temail = old@example.com\n")
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"

    result = _run(
        "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env,
        stdin="New Name\nnew@example.com\n\n",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Name the agents commit with [Tess Tester]: " in result.stdout
    assert _git(clone, "config", "--local", "user.name") == "New Name"
    assert _git(clone, "config", "--local", "user.email") == "new@example.com"
    assert _git(clone, "var", "GIT_AUTHOR_IDENT").startswith("New Name <new@example.com>")


def test_a_branch_missing_on_origin_is_refused_and_asked_again(env) -> None:
    """sys-w9: a branch origin does not have is refused with a plain message and the
    question is asked again; Enter then keeps origin/HEAD."""
    result = _run(
        "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env,
        stdin="\n\nnope\n\n",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert "nope does not exist on origin; give a branch the repository has" in result.stdout
    assert result.stdout.count("Branch the agents' pull requests go into [trunk]: ") == 2
    assert _projects(env)[0]["default_branch"] == "trunk"


def test_the_three_flags_skip_their_questions(env, tmp_path) -> None:
    """sys-w9: --git-name, --git-email and --default-branch answer the three questions, so
    none is asked and gh's account is not read."""
    _main_and_develop(tmp_path)
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"

    result = _run(
        "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", "--git-name", "Ada",
        "--git-email", "ada@example.com", "--default-branch", "develop", env=env,
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    for question in ("Name the agents commit with", "Email the agents commit with",
                     "Branch the agents' pull requests go into"):  # fmt: skip
        assert question not in result.stdout
    assert "api user" not in _gh_calls(env)
    assert _git(clone, "config", "--local", "user.name") == "Ada"
    assert _git(clone, "config", "--local", "user.email") == "ada@example.com"
    assert _projects(env)[0]["default_branch"] == "develop"


def test_a_default_branch_flag_missing_on_origin_stops_with_a_plain_message(env) -> None:
    """sys-w9: a --default-branch that origin does not have stops the wizard before the
    project is written, naming the branch and the fix."""
    result = _run(
        "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g",
        "--default-branch", "nope", env=env,
    )  # fmt: skip

    assert result.returncode == 1
    assert "branch: --default-branch nope does not exist on origin" in result.stderr
    assert "push that branch to GitHub first" in result.stderr
    assert _projects(env) == []


def test_ended_stdin_takes_the_three_defaults(env) -> None:
    """sys-w9: a caller with no terminal (the installer runs `project new </dev/null`) gets
    each question's default, as Enter gives it, and the project is made."""
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Name the agents commit with [Tess Tester]: " in result.stdout
    assert _git(clone, "config", "--local", "user.name") == "Tess Tester"
    assert _git(clone, "config", "--local", "user.email") == "7+tess@users.noreply.github.com"
    assert _projects(env)[0]["default_branch"] == "trunk"


def test_repo_path_sets_the_identity_flags_on_that_checkout(env, tmp_path) -> None:
    """sys-w9: with --repo-path nothing is asked; --git-name and --git-email, when given,
    are set on that checkout."""
    checkout = tmp_path / "checkout"
    _git(tmp_path, "init", "-q", str(checkout))

    result = _run(
        "project", "new", "Widgets", "--goal", "g", "--repo", HTTPS, "--repo-path",
        str(checkout), "--default-branch", "main", "--git-name", "Ada", "--git-email",
        "ada@example.com", env=env,
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(checkout, "config", "--local", "user.name") == "Ada"
    assert _git(checkout, "config", "--local", "user.email") == "ada@example.com"
    assert "api user" not in _gh_calls(env)


def test_an_empty_repository_gets_its_first_commit_on_the_chosen_branch(env, tmp_path) -> None:
    """sys-w9: on an empty repository the branch question offers main; the branch typed
    there is where the first commit goes and what is recorded."""
    remote = tmp_path / "github.git"
    _git(remote, "update-ref", "-d", "refs/heads/trunk")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    result = _run(
        "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env,
        stdin="\n\ndevelop\n",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Branch the agents' pull requests go into [main]: " in result.stdout
    assert "empty repository: pushed a first commit with no files to develop" in result.stdout
    branches = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
    assert [b for b in branches if b != "refs/heads/probe/autonomouse"] == ["refs/heads/develop"]
    assert _projects(env)[0]["default_branch"] == "develop"


def test_an_empty_repository_gets_one_first_commit_on_main_and_nothing_else(env, tmp_path) -> None:
    """sys-w2: a new GitHub repository has no branch until its first push, so no pull
    request could be based on it. `project new` pushes one commit with no files to `main`,
    records `main`, and leaves the clone on it; the only other branch the remote gets is the
    access check's throwaway branch, which the check deletes again. Fails on
    card/sys-w1, whose wizard stopped with "no origin/HEAD"."""
    remote = tmp_path / "github.git"
    _git(remote, "update-ref", "-d", "refs/heads/trunk")  # no branch: an empty repository
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")  # as GitHub makes a new one

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "empty repository: pushed a first commit with no files to main" in result.stdout
    branches = _git(remote, "for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
    assert [b for b in branches if b != "refs/heads/probe/autonomouse"] == ["refs/heads/main"]
    assert _git(remote, "log", "--format=%s|%P", "main").splitlines() == ["Initial commit|"]
    assert _git(remote, "ls-tree", "-r", "main") == ""
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(clone, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main"
    assert _git(clone, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") == "origin/main"
    assert _git(clone, "status", "--porcelain") == ""
    [project] = _projects(env)
    assert project["default_branch"] == "main"
    # The access check ran on the new branch, as on any repository.
    assert _probe_facts(env)["branch"] == "main"


def test_project_new_ends_with_the_brief_thread_and_no_card_comes_before_the_brief(env) -> None:
    """sys-t5: after the clone, the access check and the project, `project new` opens the
    project's brief thread and runs the planner (the fake) on it once, so the thread's first
    planner message, which it prints, asks what the project is for. The board holds no card
    and no brief then, and none while the human answers (fixtures fed with `plan reply`,
    each followed by the `plan step` the timer runs): the planner says the goal sentence
    back, writes the brief on the yes, and the brief thread closes with no card written."""
    board = Path(env["AUTONOMOUSE_HOME"])
    brief_file = board / "projects" / "widgets" / "brief.md"

    result = _run("project", "new", "Widgets", env=env, stdin=f"{HTTPS}\n{ENTER_THREE}")

    assert result.returncode == 0, result.stderr
    [thread] = _threads(env)
    assert (thread["project"], thread["status"]) == ("widgets", "waiting_human")
    request, question = thread["messages"]
    assert request["from"] == "human"
    assert '"The brief"' in request["text"]
    assert request["text"].endswith(
        "write it with `autonomouse plan brief <thread-id> <file>`."
    )
    assert question == {
        "at": question["at"], "from": "planner", "text": "What is widgets for: why does it exist?",
    }  # fmt: skip
    assert f"brief: the planner's first question:\n{question['text']}\n" in result.stdout
    [call] = _planner_calls(env)
    assert call[call.index("--session-id") + 1] == thread["session_id"]
    prompt = call[call.index("-p") + 1]
    assert "docs/planner.md" in prompt and request["text"] in prompt
    assert f"plan brief {thread['id']} <file>" in prompt
    assert _git(board, "status", "--porcelain") == ""
    assert _cards(env) == [] and not brief_file.exists()

    for answer in ("Small shops sell widgets online.", "yes"):
        assert _cards(env) == [] and not brief_file.exists()
        assert _run("plan", "reply", thread["id"], answer, env=env).returncode == 0
        stepped = _run("plan", "step", env=env)
        assert stepped.returncode == 0, stepped.stdout + stepped.stderr

    [thread] = _threads(env)
    assert [m["text"] for m in thread["messages"] if m["from"] == "planner"] == [
        "What is widgets for: why does it exist?",
        "Goal sentence: Small shops sell widgets online. Is that right?",
        "The brief is written.",
    ]
    assert thread["status"] == "closed" and thread["cards_written"] == []
    brief = load_brief(board, "widgets")
    assert (brief.goal, brief.intent) == (
        "Small shops sell widgets online.", "The creator wants widgets sold.",
    )  # fmt: skip
    assert _cards(env) == []


def test_repo_path_opens_no_brief_thread(env) -> None:
    """sys-t5: `--repo-path` runs nothing but the record, the brief thread included."""
    result = _run(
        "project", "new", "Widgets", "--goal", "g", "--repo", "r", "--repo-path", "p",
        "--default-branch", "main", env=env,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert _threads(env) == []
    assert _planner_calls(env) == []


def test_a_planner_that_fails_leaves_the_thread_and_prints_the_fix(env) -> None:
    """sys-t5: when the planner's first turn fails, the project and its brief thread stay on
    the board, committed, and the wizard exits 1 naming `plan step`, which runs it again."""
    (Path(env["FAKE_DIR"]) / "planner-fails").touch()
    board = Path(env["AUTONOMOUSE_HOME"])

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 1
    [thread] = _threads(env)
    assert f"brief: the planner's first turn on planning thread {thread['id']} failed" in (
        result.stderr
    )
    assert "`autonomouse plan step` runs the planner again" in result.stderr
    assert [m["from"] for m in thread["messages"]] == ["human"]
    assert _projects(env)[0]["slug"] == "widgets"
    assert _git(board, "status", "--porcelain") == ""


def test_with_no_overrides_the_clone_and_worktree_roots_are_under_projects_autonomouse(
    env, monkeypatch
) -> None:
    """sys-s59: with HOME at a temporary folder and neither `AUTONOMOUSE_REPOS` nor
    `AUTONOMOUSE_WORKTREES` set, the clone root is HOME/projects/autonomouse/repos and the
    worktree root HOME/projects/autonomouse/worktrees; `project new` makes both when they
    are missing and clones the repository under the first."""
    home = Path(env["HOME"])
    env = {k: v for k, v in env.items() if k not in ("AUTONOMOUSE_REPOS", "AUTONOMOUSE_WORKTREES")}
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("AUTONOMOUSE_REPOS", raising=False)
    monkeypatch.delenv("AUTONOMOUSE_WORKTREES", raising=False)
    folders = home / "projects" / "autonomouse"
    assert repos_root() == folders / "repos"
    assert worktrees_root() == folders / "worktrees"
    assert not folders.exists()

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stderr
    clone = folders / "repos" / "widgets"
    assert _git(clone, "remote", "get-url", "origin") == HTTPS
    assert _projects(env)[0]["repo_path"] == str(clone)
    assert (folders / "worktrees").is_dir()


def test_the_ssh_form_records_the_https_repo(env) -> None:
    result = _run("project", "new", "Widgets", "--goal", "g", env=env, stdin=SSH + "\n")

    assert result.returncode == 0, result.stderr
    [project] = _projects(env)
    assert project["repo"] == HTTPS
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"
    assert _git(clone, "remote", "get-url", "origin") == SSH
    assert _probe_facts(env)["push over"] == "ssh"


def test_a_second_project_gets_priority_two(env) -> None:
    first = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)
    assert first.returncode == 0, first.stderr

    second = _run("project", "new", "Gadgets", "--repo", SSH, "--goal", "g", env=env)

    assert second.returncode == 0, second.stderr
    assert [(p["slug"], p["priority"]) for p in _projects(env)] == [
        ("widgets", 1), ("gadgets", 2),
    ]  # fmt: skip


def test_a_set_up_telegram_names_no_step(env) -> None:
    config = Path(env["AUTONOMOUSE_CONFIG"])
    config.mkdir()
    (config / ".env").write_text("TELEGRAM_BOT_TOKEN=not-a-real-token\n")
    (config / "config.toml").write_text('[notify]\ntelegram_chat_id = "12345"\n')

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stderr
    assert _telegram_steps(result.stdout) == []
    assert "not-a-real-token" not in result.stdout + result.stderr


def test_telegram_off_says_so_in_one_line_and_names_no_step(env) -> None:
    config = Path(env["AUTONOMOUSE_CONFIG"])
    config.mkdir()
    (config / "config.toml").write_text("[notify]\ntelegram = false\n")

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stderr
    assert _telegram_steps(result.stdout) == []
    assert "telegram: not set up" not in result.stdout
    assert result.stdout.count("telegram: off (notify.telegram = false); you answer on the board\n") == 1
    assert "planning: the planner asks its questions on the board (planning thread " in result.stdout
    assert "Telegram" not in result.stdout


def test_a_url_outside_github_is_asked_again(env) -> None:
    answers = "https://gitlab.com/test/widgets\n" + HTTPS + "\n" + ENTER_THREE

    result = _run("project", "new", "Widgets", env=env, stdin=answers)

    assert result.returncode == 0, result.stderr
    assert "not a github.com repository" in result.stdout
    assert result.stdout.count("GitHub repository URL") == 2
    assert _projects(env)[0]["repo"] == HTTPS


def test_a_clone_that_fails_prints_gits_error_and_writes_no_project(env) -> None:
    result = _run(
        "project", "new", "Widgets", "--repo", "https://github.com/test/missing",
        "--goal", "g", env=env,
    )  # fmt: skip

    assert result.returncode == 1
    assert "git clone https://github.com/test/missing failed" in result.stderr
    assert "fatal:" in result.stderr  # git's own words
    assert _projects(env) == []
    assert _cron_jobs(env) == []


ALL_OK_LINE = (
    "access: the agents can push, open pull requests, and read CI status, review comments "
    "and merge conflicts on test/widgets"
)


def _probe_report(env: dict[str, str], **changed: dict) -> None:
    """Make the fake access check report `changed` in place of the named permissions, until
    the test removes probe-report.json (the owner "grants" the permission)."""
    names = ["push", "pull_requests", "ci_status", "review_comments",
             "merge_conflicts", "cancel_run", "close_pull_request", "delete_branch"]  # fmt: skip
    permissions = [
        changed.get(n, {"name": n, "required": i < 5, "status": "ok", "fix": ""})
        for i, n in enumerate(names)
    ]
    (Path(env["FAKE_DIR"]) / "probe-report.json").write_text(json.dumps({"permissions": permissions}))


CONTENTS_FIX = "give the token Contents read and write on test/widgets (github.com > Settings)"


PUSH_MISSING = {"name": "push", "required": True, "status": "missing", "fix": CONTENTS_FIX}


def test_a_missing_push_permission_stops_before_the_project_with_its_fix(env) -> None:
    """sys-w26: the access check's agent reports push missing, a required permission, so
    setup stops before the project is written and prints the fix the agent gave. With stdin
    ended, as a script or an agent with flags and no keyboard runs it, nobody can choose
    re-check (sys-w100), so it stops as before and says to run the command again."""
    _probe_report(env, push=PUSH_MISSING)
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 1
    assert "access: the agents cannot work on test/widgets until these are fixed" in result.stderr
    assert f"push is missing\nfix: {CONTENTS_FIX}\nthen run this command again" in result.stderr
    assert len(_probe_calls(env)) == 1
    assert _projects(env) == []
    assert _cron_jobs(env) == []
    assert _planner_calls(env) == []
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "trunk"


def test_a_missing_cancel_warns_with_its_fix_and_the_project_is_made(env) -> None:
    """sys-w26: cancelling a CI run is optional; the agent reports it missing, and setup
    prints its fix and goes on to make the project."""
    fix = "give the gh login Actions read and write on test/widgets (github.com > Settings)"
    _probe_report(env, cancel_run={"name": "cancel_run", "required": False, "status": "missing", "fix": fix})

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"access: warning: cancel_run is missing; setup goes on without it\nfix: {fix}" in result.stdout
    assert _projects(env)[0]["slug"] == "widgets"


def test_a_refused_close_and_delete_warn_naming_what_is_left_behind_and_the_project_is_made(
    env,
) -> None:
    """sys-w100, the owner's item 8 of 2026-09-30: closing the test pull request and
    deleting the branch only clean up. The agent reports both refused, as the charter's
    rows `close_pull_request` and `delete_branch` say; setup warns with what is left behind
    and goes on to make the project."""
    close_fix = (
        "left behind: the draft pull request #1 from `probe/autonomouse` is still open on "
        "test/widgets; close it with `gh pr close 1 --repo test/widgets`, or leave it for the "
        "next check to close; GitHub said: HTTP 403"
    )
    delete_fix = (
        "left behind: the branch `probe/autonomouse` is still on test/widgets; delete it with "
        "`git push origin --delete probe/autonomouse`, or leave it for the next check to "
        "reset; git said: protected branch"
    )
    _probe_report(
        env,
        close_pull_request={"name": "close_pull_request", "required": False, "status": "missing", "fix": close_fix},
        delete_branch={"name": "delete_branch", "required": False, "status": "missing", "fix": delete_fix},
    )

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f"access: warning: close_pull_request is missing; setup goes on without it\nfix: {close_fix}"
    ) in result.stdout
    assert (
        f"access: warning: delete_branch is missing; setup goes on without it\nfix: {delete_fix}"
    ) in result.stdout
    assert ALL_OK_LINE in result.stdout
    assert RECHECK_QUESTION not in result.stdout
    assert len(_probe_calls(env)) == 1
    assert _projects(env)[0]["slug"] == "widgets"


RECHECK_QUESTION = "Make the fix above, then (r) re-check access, or (q) quit: "
# Every question before the access check answered by a flag, so the first line stdin gives
# is the answer to the re-check question.
ALL_FLAGS = (
    "project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", "--git-name", "Tess",
    "--git-email", "tess@example.com", "--default-branch", "trunk",
)  # fmt: skip


def _start(env: dict[str, str], tmp_path: Path, *args: str) -> subprocess.Popen:
    """`autonomouse <args>` with stdin a pipe the test writes to, as a terminal would be
    typed into; stderr goes to a file, read at the end."""
    return subprocess.Popen(
        [sys.executable, "-m", "autonomouse.cli", *args], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=(tmp_path / "stderr.txt").open("wb"),
    )  # fmt: skip


def _read_until(proc: subprocess.Popen, marker: str, *, seen: str = "", timeout: float = 60) -> str:
    """Stdout read so far, from `seen` on, once it holds `marker` one more time than `seen`
    does; fails when the process ends or `timeout` seconds pass first."""
    out, want = seen, seen.count(marker) + 1
    deadline = time.monotonic() + timeout
    fd = proc.stdout.fileno()
    while out.count(marker) < want:
        left = deadline - time.monotonic()
        assert left > 0, f"no {marker!r} within {timeout}s; stdout:\n{out}"
        if select.select([fd], [], [], left)[0]:
            chunk = os.read(fd, 65536)
            assert chunk, f"stdout ended before {marker!r}; stdout:\n{out}"
            out += chunk.decode()
    return out


def _still_waiting(proc: subprocess.Popen) -> bool:
    """The process neither ended nor ends within a second: it waits on its question."""
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return True
    return False


def test_a_missing_push_prints_the_fix_waits_and_a_recheck_after_the_grant_goes_on(
    env, tmp_path
) -> None:
    """sys-w100, the owner's item 5 of 2026-09-30: when access fails, setup shows the fix
    and waits until the owner chooses re-check or quit. The owner grants push while it
    waits, then chooses re-check: the check runs again, passes, and setup goes on to make
    the project. Fails on main, where setup exits at the first failure."""
    _probe_report(env, push=PUSH_MISSING)
    proc = _start(env, tmp_path, *ALL_FLAGS)
    try:
        out = _read_until(proc, RECHECK_QUESTION)
        assert _still_waiting(proc)
        assert len(_probe_calls(env)) == 1
        assert (
            "access: the agents cannot work on test/widgets until these are fixed:\n"
            f"push is missing\nfix: {CONTENTS_FIX}\n{RECHECK_QUESTION}"
        ) in out
        assert _projects(env) == []

        (Path(env["FAKE_DIR"]) / "probe-report.json").unlink()  # the owner grants push
        # Re-check, then stdin ends: later questions take their defaults.
        out += proc.communicate(b"r\n", timeout=60)[0].decode()
        assert proc.returncode == 0, out + (tmp_path / "stderr.txt").read_text()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert len(_probe_calls(env)) == 2
    assert out.index(RECHECK_QUESTION) < out.index(ALL_OK_LINE)
    assert out.count(RECHECK_QUESTION) == 1
    [project] = _projects(env)
    assert (project["slug"], project["default_branch"]) == ("widgets", "trunk")
    assert _cron_jobs(env) == ["wake", "review", "plan"]


def test_a_recheck_that_still_fails_waits_again_and_quit_exits_with_no_project(
    env, tmp_path
) -> None:
    """sys-w100: re-check with push still missing runs the check again, prints the fix and
    waits again; quit then ends setup before the project is written."""
    _probe_report(env, push=PUSH_MISSING)
    proc = _start(env, tmp_path, *ALL_FLAGS)
    try:
        out = _read_until(proc, RECHECK_QUESTION)
        proc.stdin.write(b"r\n")
        proc.stdin.flush()
        out = _read_until(proc, RECHECK_QUESTION, seen=out)
        assert _still_waiting(proc)
        assert len(_probe_calls(env)) == 2
        assert out.count(f"push is missing\nfix: {CONTENTS_FIX}\n{RECHECK_QUESTION}") == 2

        out += proc.communicate(b"q\n", timeout=60)[0].decode()
        code = proc.returncode
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    stderr = (tmp_path / "stderr.txt").read_text()
    assert code == 1, out + stderr
    assert "access: quit at the access check; the project is not created" in stderr
    assert len(_probe_calls(env)) == 2
    assert ALL_OK_LINE not in out
    assert _projects(env) == []
    assert _cron_jobs(env) == []
    assert _planner_calls(env) == []


def test_without_a_claude_login_project_new_stops_before_its_first_question(env) -> None:
    """sys-w26: every access check is an agent, so with Claude Code logged out `project new`
    asks nothing, touches nothing, and names `claude auth login`."""
    (Path(env["FAKE_DIR"]) / "claude-logged-out").touch()

    result = _run("project", "new", env=env, stdin=f"Widgets\n{HTTPS}\n{ENTER_THREE}")

    assert result.returncode == 1
    assert "Claude Code is not logged in" in result.stderr
    assert "fix: run `claude auth login`, then run this command again" in result.stderr
    assert "Project name" not in result.stdout
    assert result.stdout == ""
    assert _gh_calls(env) == []
    assert not Path(env["AUTONOMOUSE_HOME"]).exists()
    assert _probe_calls(env) == [] and _planner_calls(env) == []


def test_a_rerun_reuses_the_clone_that_is_already_there(env) -> None:
    board = Path(env["AUTONOMOUSE_HOME"])
    _probe_report(env, push=PUSH_MISSING)  # the first run stops at the access check
    stopped = _run("project", "new", "Widgets", env=env, stdin=HTTPS + "\n")
    assert stopped.returncode == 1
    assert "then run this command again" in stopped.stderr
    assert _projects(env) == []
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"
    assert clone.is_dir()
    (Path(env["FAKE_DIR"]) / "probe-report.json").unlink()

    result = _run("project", "new", "Widgets", "--repo", SSH, "--goal", "g", env=env)

    assert result.returncode == 0, result.stderr
    assert f"clone: {clone} (already there, origin test/widgets)" in result.stdout
    assert _projects(env)[0]["repo_path"] == str(clone)
    [thread] = _threads(env)
    assert _git(board, "log", "--format=%s").splitlines() == [
        f"autonomouse project new widgets: brief thread {thread['id']}",
        "autonomouse project new widgets",
    ]


def test_a_folder_that_is_another_repository_is_refused(env) -> None:
    clone = Path(env["AUTONOMOUSE_REPOS"]) / "widgets"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    _git(clone, "remote", "add", "origin", "https://github.com/test/other")

    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)

    assert result.returncode == 1
    assert "is not a clone of test/widgets" in result.stderr
    assert "--repo-path" in result.stderr
    assert _projects(env) == []


def test_repo_path_records_the_checkout_and_runs_nothing_else(env, tmp_path) -> None:
    result = _run(
        "project", "new", "--slug", "widgets", "--name", "Widgets", "--goal", "g",
        "--repo", "r", "--repo-path", "p", "--default-branch", "main", "--priority", "1", env=env,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    [project] = _projects(env)
    assert (project["slug"], project["repo"], project["repo_path"]) == ("widgets", "r", "p")
    assert project["default_branch"] == "main"
    assert not (Path(env["AUTONOMOUSE_HOME"]) / ".git").exists()
    assert not Path(env["AUTONOMOUSE_REPOS"]).exists()
    assert _cron_jobs(env) == []
    assert _telegram_steps(result.stdout) == []


def test_repo_path_without_a_priority_takes_the_next_one(env) -> None:
    first = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g", env=env)
    assert first.returncode == 0, first.stderr

    second = _run(
        "project", "new", "Gadgets", "--goal", "g", "--repo", "r", "--repo-path", "p",
        "--default-branch", "main", env=env
    )

    assert second.returncode == 0, second.stderr
    assert [(p["slug"], p["priority"]) for p in _projects(env)] == [
        ("widgets", 1), ("gadgets", 2),
    ]  # fmt: skip


def test_repo_path_records_the_default_branch_the_checkouts_origin_head_names(
    env, tmp_path
) -> None:
    """sys-s89: a repository whose default branch is develop. `--repo-path` without
    `--default-branch` records the branch the checkout's origin/HEAD names, as the clone
    path does, never main."""
    source = tmp_path / "develop-source"
    _git(tmp_path, "init", "-q", "-b", "develop", str(source))
    _git(source, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "first")  # fmt: skip
    remote = tmp_path / "develop.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "develop", str(remote))
    _git(source, "push", "-q", str(remote), "develop")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", f"file://{remote}", str(checkout))
    assert _git(checkout, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") == "origin/develop"

    result = _run(
        "project", "new", "Widgets", "--goal", "g", "--repo", HTTPS,
        "--repo-path", str(checkout), env=env,
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    [project] = _projects(env)
    assert (project["repo_path"], project["default_branch"]) == (str(checkout), "develop")


def test_repo_path_to_a_folder_without_origin_head_prints_the_fix(env, tmp_path) -> None:
    folder = tmp_path / "no-remote"
    _git(tmp_path, "init", "-q", str(folder))

    result = _run(
        "project", "new", "Widgets", "--goal", "g", "--repo", HTTPS,
        "--repo-path", str(folder), env=env,
    )  # fmt: skip

    assert result.returncode == 1
    assert f"default branch: {folder} has no origin/HEAD" in result.stderr
    assert f"fix: run `git -C {folder} remote set-head origin --auto`" in result.stderr
    assert _projects(env) == []


def test_a_name_is_required_once(env) -> None:
    """sys-w20: with no name given, the name is a question; stdin that has ended cannot
    answer it, so the wizard stops and names it."""
    none = _run("project", "new", "--repo", HTTPS, "--goal", "g", env=env)
    assert none.returncode == 1
    assert "no answer to 'Project name, as the board shows it': stdin ended" in none.stderr

    two = _run("project", "new", "Widgets", "--name", "Gadgets", "--repo", HTTPS, env=env)
    assert two.returncode == 2
    assert "two names" in two.stderr
    assert _projects(env) == []


GADGETS = "https://github.com/test/gadgets"
PLANNING_QUESTION = "Start planning now? "
TRACKER_QUESTION = "Linear project or issue URL for this project (blank for none): "
LINEAR_PROJECT = "https://linear.app/acme/project/widgets-launch-5b1a2c3d4e5f"
LINEAR_ISSUE = "https://linear.app/acme/issue/WID-42/ship-the-widget-page"


@pytest.mark.parametrize(
    ("typed", "tracker"),
    [
        (LINEAR_PROJECT, {"kind": "linear", "url": LINEAR_PROJECT, "link": "project"}),
        (LINEAR_ISSUE, {"kind": "linear", "url": LINEAR_ISSUE, "link": "issue"}),
        ("", None),
    ],
    ids=["linear-project", "linear-issue", "blank"],
)
def test_the_tracker_answer_links_the_project_to_a_linear_project_an_issue_or_nothing(
    env, typed, tracker
) -> None:
    """sys-w14: after the goal, `project new` asks for the Linear project or issue the
    project is linked to. A project URL and an issue URL are stored in the project's
    `tracker` with what they point at; a blank answer links nothing. Fails on card/sys-w21,
    which asks no such question and always writes a null tracker."""
    result = _run(
        "project", "new", "Widgets", env=env,
        stdin=f"{HTTPS}\n{ENTER_THREE}{typed}\n",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(TRACKER_QUESTION) == 1
    assert _projects(env)[0]["tracker"] == tracker
    # sys-w15: a link is proven once, in the clone, before it is saved; blank runs no check.
    calls = _linear_probe_calls(env)
    assert [Path(c["cwd"]) for c in calls] == ([Path(_projects(env)[0]["repo_path"])] if typed else [])


def _linear_probe_calls(env: dict[str, str]) -> list[dict]:
    calls = Path(env["FAKE_DIR"]) / "linear-probe-calls"
    paths = sorted(calls.glob("*.json"), key=lambda p: int(p.stem)) if calls.exists() else []
    return [json.loads(p.read_text()) for p in paths]


MCP_FIX = "connect Linear in Claude Code: run `claude`, type `/mcp`"


def _linear_mcp_missing(env: dict[str, str]) -> None:
    (Path(env["FAKE_DIR"]) / "linear-report.json").write_text(json.dumps({"permissions": [
        {"name": "linear_mcp", "required": True, "status": "missing", "fix": MCP_FIX},
        *({"name": n, "required": True, "status": "unchecked",
           "fix": "the Linear MCP is not connected"}
          for n in ("read_link", "create_issue", "remove_test_issue")),
    ]}))  # fmt: skip


def test_a_linear_answer_the_access_check_refuses_is_asked_again_with_its_fix(env) -> None:
    """sys-w15: when the Linear access check finds no Linear MCP, the answer is refused
    with the fix and the question asked again; a blank answer then links nothing."""
    _linear_mcp_missing(env)
    result = _run(
        "project", "new", "Widgets", env=env,
        stdin=f"{HTTPS}\n{ENTER_THREE}{LINEAR_PROJECT}\n\n",
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(TRACKER_QUESTION) == 2
    assert "linear: the link is not saved" in result.stdout
    assert f"fix: {MCP_FIX}" in result.stdout
    assert "then give the link again, or answer blank to link none now" in result.stdout
    assert _projects(env)[0]["tracker"] is None
    assert len(_linear_probe_calls(env)) == 1


def test_a_tracker_flag_the_access_check_refuses_stops_before_the_project_is_written(
    env,
) -> None:
    """sys-w15: `--tracker` whose access check fails stops the wizard with the fix, and no
    project is written."""
    _linear_mcp_missing(env)
    result = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g",
                  "--tracker", LINEAR_ISSUE, env=env)  # fmt: skip

    assert result.returncode != 0
    assert f"fix: {MCP_FIX}" in result.stdout + result.stderr
    assert not (Path(env["AUTONOMOUSE_HOME"]) / "projects" / "widgets" / "project.json").exists()


def test_a_url_that_is_no_linear_project_or_issue_is_asked_again(env) -> None:
    """sys-w14: an answer on another host is refused with the trackers autonomouse knows,
    and a Linear URL that is neither a project nor an issue with the shapes it takes; each
    is asked again, and the next answer is stored."""
    result = _run(
        "project", "new", "Widgets", env=env,
        stdin=(
            f"{HTTPS}\n{ENTER_THREE}https://github.com/orgs/acme/projects/1\n"
            f"https://linear.app/acme/team/WID/active\n{LINEAR_PROJECT}\n"
        ),
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count(TRACKER_QUESTION) == 3
    assert "is not a tracker autonomouse knows; the trackers it knows are Linear (linear.app)" in result.stdout
    assert "is neither a Linear project nor a Linear issue" in result.stdout
    assert _projects(env)[0]["tracker"]["link"] == "project"


def test_the_tracker_flag_skips_the_question_and_a_bad_one_stops_before_anything(env, tmp_path) -> None:
    """sys-w14: `--tracker` answers the question; with --repo-path, where nothing is asked,
    it is the one way to link. A flag no tracker reads stops the wizard before the board
    or the clone is made."""
    bad = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g",
               "--tracker", "https://jira.example.com/browse/WID-1", env=env)  # fmt: skip
    assert bad.returncode != 0
    assert "--tracker 'https://jira.example.com/browse/WID-1' is not a tracker" in bad.stderr + bad.stdout
    assert not Path(env["AUTONOMOUSE_HOME"]).exists()
    assert not (Path(env["AUTONOMOUSE_REPOS"]) / "widgets").exists()

    flagged = _run("project", "new", "Widgets", "--repo", HTTPS, "--goal", "g",
                   "--tracker", LINEAR_ISSUE, env=env)  # fmt: skip
    assert flagged.returncode == 0, flagged.stdout + flagged.stderr
    assert TRACKER_QUESTION not in flagged.stdout
    assert _projects(env)[0]["tracker"] == {"kind": "linear", "url": LINEAR_ISSUE, "link": "issue"}

    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(tmp_path / "github.git"), str(checkout))
    recorded = _run("project", "new", "Gadgets", "--repo", GADGETS, "--goal", "g",
                    "--repo-path", str(checkout), "--tracker", LINEAR_PROJECT, env=env)  # fmt: skip
    assert recorded.returncode == 0, recorded.stdout + recorded.stderr
    assert TRACKER_QUESTION not in recorded.stdout
    assert _projects(env)[1]["tracker"] == {"kind": "linear", "url": LINEAR_PROJECT, "link": "project"}


def test_two_projects_from_stdin_answers_alone_get_their_own_clone_identity_and_branch(
    env, tmp_path
) -> None:
    """sys-w20: `project new` with no flag asks every per-project question, the name and
    the URL first and the Linear link last, so the owner sets up a second project
    after install the way the first was set up. Each project gets its own clone, commit
    identity and branch; the crontab holds one set of autonomouse lines. Fails on
    card/sys-w9, which refused `project new` with no name."""
    gadgets = tmp_path / "gadgets.git"
    _git(tmp_path, "clone", "-q", "--bare", str(tmp_path / "github.git"), str(gadgets))
    _git(gadgets, "branch", "develop", "trunk")
    global_git = Path(env["GIT_CONFIG_GLOBAL"])
    global_git.write_text(global_git.read_text() + f'[url "file://{gadgets}"]\n\tinsteadOf = {GADGETS}\n')
    repos = Path(env["AUTONOMOUSE_REPOS"])

    first = _run(
        "project", "new", env=env,
        stdin=f"Widgets\n{HTTPS}\nAda Lovelace\nada@example.com\n\n\n",
    )  # fmt: skip
    second = _run(
        "project", "new", env=env,
        stdin=f"Gadgets\n{GADGETS}\nGrace Hopper\ngrace@example.com\ndevelop\n{LINEAR_ISSUE}\n",
    )  # fmt: skip

    for result in (first, second):
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Project name, as the board shows it: " in result.stdout
        assert "GitHub repository URL" in result.stdout
        assert TRACKER_QUESTION in result.stdout
        assert "planning: the planner asks its questions on Telegram" in result.stdout
    assert [
        (p["slug"], p["name"], p["goal"], p["repo"], p["repo_path"], p["default_branch"])
        for p in _projects(env)
    ] == [
        ("widgets", "Widgets", "", HTTPS, str(repos / "widgets"), "trunk"),
        ("gadgets", "Gadgets", "", GADGETS, str(repos / "gadgets"), "develop"),
    ]  # fmt: skip
    assert [p["tracker"] for p in _projects(env)] == [
        None, {"kind": "linear", "url": LINEAR_ISSUE, "link": "issue"},
    ]  # fmt: skip
    assert _git(repos / "widgets", "config", "--local", "user.name") == "Ada Lovelace"
    assert _git(repos / "widgets", "config", "--local", "user.email") == "ada@example.com"
    assert _git(repos / "gadgets", "config", "--local", "user.name") == "Grace Hopper"
    assert _git(repos / "gadgets", "config", "--local", "user.email") == "grace@example.com"
    assert _git(repos / "gadgets", "remote", "get-url", "origin") == GADGETS
    assert _cron_jobs(env) == ["wake", "review", "plan"]
    assert sorted((t["project"], t["status"]) for t in _threads(env)) == [
        ("gadgets", "waiting_human"), ("widgets", "waiting_human"),
    ]  # fmt: skip


@pytest.mark.parametrize("goal", [None, "Build widgets."], ids=["no-goal", "goal-flag"])
def test_flags_and_no_keyboard_make_the_project_and_the_planner_asks_on_telegram(
    env, goal
) -> None:
    """sys-w103, the owner's item 7 of 2026-09-30: a script or an agent makes a project
    with flags and stdin ended (`</dev/null`). No question asks the goal or whether to plan
    now: `--goal`, when given, is the project's goal, else it is empty. No live session
    starts; the planner runs its one brief turn (`claude -p`), which writes its first
    question on the brief thread and sends it on Telegram. Fails on main, where the goal
    is a question stdin cannot answer and "start planning now" is asked."""
    flags = ["--goal", goal] if goal else []
    result = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "project", "new", "Widgets", "--repo", HTTPS,
         *flags],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip

    assert result.returncode == 0, result.stdout + result.stderr
    for question in ("Project name", "Project goal", PLANNING_QUESTION):
        assert question not in result.stdout
    [project] = _projects(env)
    assert (project["slug"], project["goal"]) == ("widgets", goal or "")
    [thread] = _threads(env)
    assert (
        f"planning: the planner asks its questions on Telegram (planning thread {thread['id']}); "
        f"join it live any time with: autonomouse plan join {thread['id']}"
    ) in result.stdout
    assert "planning live" not in result.stdout and "joined live" not in result.stdout
    [call] = _planner_calls(env)  # the one brief turn; a live session has no -p
    assert "-p" in call
    assert thread["status"] == "waiting_human"
    assert [m["text"] for m in thread["messages"] if m["from"] == "planner"] == [
        "What is widgets for: why does it exist?"
    ]


@pytest.mark.parametrize("url", [HTTPS, SSH, "git@github.com:test/widgets"])
def test_repo_slug_reads_both_url_forms(url) -> None:
    assert github.repo_slug(url) == "test/widgets"


def test_init_on_an_empty_board_makes_it_and_points_at_project_new(env) -> None:
    board = Path(env["AUTONOMOUSE_HOME"])

    result = _run("init", env=env)

    assert result.returncode == 0, result.stderr
    assert _git(board, "log", "--format=%s") == "autonomouse init"
    assert "create one with: autonomouse project new <name>" in result.stdout
    assert _projects(env) == []
    # init lost its project flags.
    flagged = _run("init", "--name", "Widgets", env=env)
    assert flagged.returncode == 2
