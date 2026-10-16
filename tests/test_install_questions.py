"""sys-w2, sys-w21, sys-w101: after its steps, `setup/install.sh` asks its setup questions on
the terminal with gum, in order: the GitHub token, the Telegram bot token, the browser pool's
address and when the first wake runs, which belong to the machine; last (sys-w103), it runs
`autonomouse project new` on the terminal, which asks the project's own questions, the same
ones it asks for any later project (the name, the repository URL, the name and email the
agents commit with, the branch their pull requests go into, and the Linear project or issue
it is linked to) and ends in the live planning session, the install's last step. A second
run asks nothing already answered.

The test runs the installer's questions section (from `# --- the questions` to `# ---
checklist`, with the layout lines it reads) the way `curl ... | bash` runs it: its text on
bash's stdin, on a pseudo-terminal that `tests/install_stubs.py`'s `drive` types the
answers into. Every program it calls is a stand-in, except `autonomouse` itself, git, jq and
date:

- `gum`, `gh` and `claude` are `tests/fixtures/install/`'s fakes; the fake gum refuses a
  prompt whose stdin is not the terminal, so a question that read the pipe would fail, and
  logs each prompt with its kind; `project new` asks its questions with gum too, since its
  stdin is the terminal, and with Python's `input()` only when it is not;
- `crontab` is `tests/test_timer.py`'s fake, which stores the crontab in a file;
- GitHub is local bare repositories: a global git config rewrites `https://github.com/` to
  their folder; one holds branches, a tag and a setting, one is empty;
- the Telegram Bot API is `install_stubs.serve_telegram`, which `AUTONOMOUSE_TELEGRAM_API`
  points autonomouse at.

`setup/test-install-questions.sh` runs the whole installer the same way in a clean Ubuntu
24.04 container, with the real gum.
"""

import json
import os
import re
import subprocess
import sys
import threading
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from autonomouse.board.brief import HEADINGS, load_brief
from install_stubs import CHAT_ID, drive, serve_telegram
from test_timer import FAKE_CRONTAB

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "setup" / "install.sh"
FIXTURES = Path(__file__).parent / "fixtures" / "install"
BOT_TOKEN = "123456:fake-bot-token"
FULL_URL = "https://github.com/owner/full"
EMPTY_URL = "https://github.com/owner/empty"
TZ = "Asia/Kolkata"  # a zone with a half-hour offset, so the wake's time keeps its offset

# The installer's own prompts, as the fake gum logs them (`<kind>: <header>`), by their
# opening words.
GITHUB_TOKEN = "password: GitHub personal access token for gh ("
TELEGRAM_TOKEN = "password: Telegram bot token, from @BotFather"
BROWSERS = "input: The browser pool as user@host"
FIRST_WAKE = "input: When does the first wake run?"

# The gum prompts `autonomouse project new` asks on the terminal (sys-w101), as the fake gum
# logs them, by their opening words, in the order it asks them.
PROJECT_PROMPTS = [
    "input: Project name, as the board shows it",
    "input: GitHub repository URL, https://github.com/<owner>/<name>",
    "input: Name the agents commit with",
    "input: Email the agents commit with",
    "input: Branch the agents' pull requests go into",
    "input: Linear project or issue URL for this project (blank for none)",
]
PROJECT_QUESTIONS = [prompt.split(": ", 1)[1] for prompt in PROJECT_PROMPTS]
LINEAR_PROJECT = "https://linear.app/acme/project/widgets-5b1a2c3d4e5f"
# The words each asked with before sys-w21, when the installer asked them itself.
OLD_INSTALLER_QUESTIONS = ("The GitHub repository autonomouse works on", "The first project's name")


def _questions_script(path: Path) -> Path:
    """install.sh's shell options, its layout lines for the state folder, the answered
    record and the secrets file, and its questions section, then the call that asks them."""
    text = INSTALL_SH.read_text()
    layout = [
        line for line in text.splitlines() if line.startswith(("STATE=", "ANSWERED=", "SECRETS="))
    ]
    questions = text[text.index("# --- the questions") : text.index("# --- checklist")]
    path.write_text(
        "set -euo pipefail\nshopt -s inherit_errexit\n"
        + "\n".join(layout)
        + '\nmkdir -p "$STATE"\nSTEPS_FAILED=0\n'
        + questions
        + "ask_the_questions\n"
    )
    return path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _bare_repositories(github: Path) -> None:
    """owner/full: main with a file, a branch, a tag and a repository setting; owner/empty:
    no branch at all, as GitHub makes a new repository."""
    source = github.parent / "source"
    _git(github.parent, "init", "-q", "-b", "main", str(source))
    (source / "README.md").write_text("widgets\n")
    ident = ("-c", "user.name=t", "-c", "user.email=t@example.com")
    _git(source, "add", "README.md")
    _git(source, *ident, "commit", "-q", "-m", "first")
    _git(source, "branch", "feature")
    _git(source, *ident, "tag", "-a", "v1", "-m", "v1")
    full = github / "owner" / "full"
    _git(github.parent, "init", "-q", "--bare", "-b", "main", str(full))
    _git(full, "config", "receive.denyDeletes", "false")
    _git(source, "push", "-q", str(full), "main", "feature", "v1")
    _git(github.parent, "init", "-q", "--bare", "-b", "main", str(github / "owner" / "empty"))


def _snapshot(bare: Path) -> dict[str, str]:
    """What the owner has in a repository: every ref and what it points at, HEAD, the config."""
    return {
        "refs": _git(bare, "for-each-ref", "--format=%(refname) %(objectname)"),
        "HEAD": (bare / "HEAD").read_text(),
        "config": (bare / "config").read_text(),
    }


@dataclass
class Machine:
    tmp: Path
    env: dict[str, str]
    fake: Path  # the fakes' state and logs (FAKE_DIR)
    github: Path
    telegram_log: Path
    script: Path

    def install(self, steps: list[tuple[str, str]], name: str) -> tuple[int, str]:
        """The questions, as `curl ... | bash` runs them, on a terminal that types `steps`."""
        return drive(
            ["bash", "-c", 'cat "$0" | bash', str(self.script)], steps,
            env=self.env, transcript=self.tmp / f"{name}.txt", timeout=60,
        )  # fmt: skip

    def asked(self) -> list[str]:
        log = self.fake / "gum.log"
        return log.read_text().splitlines() if log.exists() else []

    def cli(self, *args: str) -> str:
        return subprocess.run(
            [sys.executable, "-m", "autonomouse.cli", *args],
            env=self.env, capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip

    def config(self) -> dict:
        return tomllib.loads((Path(self.env["AUTONOMOUSE_CONFIG"]) / "config.toml").read_text())

    def secrets(self) -> Path:
        return Path(self.env["AUTONOMOUSE_CONFIG"]) / ".env"

    def answered(self) -> list[str]:
        path = Path(self.env["XDG_STATE_HOME"]) / "autonomouse-install" / "answered"
        return path.read_text().splitlines() if path.exists() else []

    def wake_line(self) -> str:
        crontab = (self.fake / "crontab.txt").read_text().splitlines()
        [wake] = [line for line in crontab if line.endswith("# autonomouse wake")]
        return wake

    def telegram(self) -> list[str]:
        return self.telegram_log.read_text().splitlines() if self.telegram_log.exists() else []

    def date(self, *args: str) -> str:
        return subprocess.run(
            ["date", *args], env=self.env, capture_output=True, text=True, check=True
        ).stdout.strip()


@pytest.fixture
def machine(tmp_path: Path) -> Iterator[Machine]:
    fake = tmp_path / "fake"
    bin_dir = tmp_path / "bin"
    fake.mkdir()
    bin_dir.mkdir()
    for name in ("gum", "gh", "claude"):
        (bin_dir / name).symlink_to(FIXTURES / name)
    (bin_dir / "crontab").write_text(FAKE_CRONTAB.format(store=fake / "crontab.txt"))
    (bin_dir / "autonomouse").write_text(f'#!/bin/sh\nexec {sys.executable} -m autonomouse.cli "$@"\n')
    for name in ("crontab", "autonomouse"):
        (bin_dir / name).chmod(0o755)
    github = tmp_path / "github"
    _bare_repositories(github)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f'[url "file://{github}/"]\n\tinsteadOf = https://github.com/\n')
    telegram_log = tmp_path / "telegram.log"
    server = serve_telegram(BOT_TOKEN, 1, telegram_log)  # the owner's message: the 2nd read
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / "home").mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GH_TOKEN", "GITHUB_TOKEN", "TELEGRAM_BOT_TOKEN", "XDG_CONFIG_HOME")
    }
    env.update(
        HOME=str(tmp_path / "home"),
        PATH=f"{bin_dir}:{os.environ['PATH']}",
        TZ=TZ,
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
        AUTONOMOUSE_HOME=str(tmp_path / "board"),
        AUTONOMOUSE_REPOS=str(tmp_path / "repos"),
        AUTONOMOUSE_WORKTREES=str(tmp_path / "worktrees"),
        AUTONOMOUSE_CONFIG=str(tmp_path / "config"),
        AUTONOMOUSE_STATE=str(tmp_path / "state"),
        AUTONOMOUSE_TELEGRAM_API=f"http://127.0.0.1:{server.server_address[1]}",
        XDG_STATE_HOME=str(tmp_path / "xdg-state"),
        FAKE_DIR=str(fake),
    )
    (fake / "claude-logged-in").touch()
    try:
        yield Machine(tmp_path, env, fake, github, telegram_log,
                      _questions_script(tmp_path / "questions.sh"))  # fmt: skip
    finally:
        server.shutdown()
        server.server_close()


def _in_order(asked: list[str], expected: list[str]) -> None:
    assert len(asked) == len(expected), "\n".join(asked)
    for line, start in zip(asked, expected, strict=True):
        assert line.startswith(start), f"{line!r} is not {start!r}"


def _project_new_asked(out: str) -> None:
    """`project new` asked each of its questions, once and in order, on the terminal, after
    the browser pool's question and the first wake's, and the installer asked none of
    them itself."""
    at = out.index("The browser pool as user@host")
    assert out.index("When does the first wake run?") > at
    at = out.index("When does the first wake run?")
    for question in PROJECT_QUESTIONS:
        assert out.count(question) == 1, (question, out[-3000:])
        assert out.index(question) > at, (question, out[-3000:])
        at = out.index(question)
    for words in OLD_INSTALLER_QUESTIONS:
        assert words not in out


def _project_steps(*answers: str) -> list[tuple[str, str]]:
    """The drive steps that answer `project new`'s questions, in PROJECT_QUESTIONS order."""
    return [(re.escape(q), a) for q, a in zip(PROJECT_QUESTIONS, answers, strict=True)]


def test_the_questions_set_up_a_fresh_machine_and_a_second_run_asks_nothing(
    machine: Machine,
) -> None:
    """The full path: a refused GitHub token and a Telegram token Telegram does not know are
    each asked again; the owner's message arrives while the installer waits; `project new`
    asks the project's questions on the terminal, and the repository already holds code, so
    the owner picks its other branch; the first wake is tomorrow at 09:00, asked before the
    project; setup ends in the live planning session."""
    before = _snapshot(machine.github / "owner" / "full")
    tomorrow_nine = machine.date("-d", "tomorrow 09:00", "+%Y%m%d%H%M")

    status, out = machine.install(
        [
            ("GitHub personal access token for gh", "ghp_refused\r"),
            ("GitHub personal access token for gh", "ghp_fake_token\r"),
            ("Telegram bot token, from @BotFather", "999:not-this-bot\r"),
            ("Telegram bot token, from @BotFather", f"{BOT_TOKEN}\r"),
            ("The browser pool as user@host", "deploy@192.0.2.14\r"),
            ("When does the first wake run", "\x15tomorrow 09:00\r"),
            *_project_steps(
                "Widget Works\r", f"{FULL_URL}\r", "Widget Bot\r", "bot@widgets.example\r",
                "feature\r", f"{LINEAR_PROJECT}\r",
            ),
        ],
        "first",
    )

    assert status == 0, out[-3000:]
    _in_order(machine.asked(), [GITHUB_TOKEN, GITHUB_TOKEN, TELEGRAM_TOKEN, TELEGRAM_TOKEN,
                                BROWSERS, FIRST_WAKE, *PROJECT_PROMPTS])  # fmt: skip
    _project_new_asked(out)
    # The defaults project new offers come from the GitHub account and the repository.
    assert "Name the agents commit with [Owner]" in out
    assert "Email the agents commit with [7+owner@users.noreply.github.com]" in out
    assert "Branch the agents' pull requests go into [main]" in out
    assert "gh did not take that token" in out
    assert "Telegram does not know that bot token; give it again" in out
    # 1: gh is logged in with the second token, and git uses its login.
    assert (machine.fake / "gh-token").read_text() == "ghp_fake_token\n"
    gh_calls = (machine.fake / "gh.calls").read_text().splitlines()
    assert gh_calls.count("auth login --hostname github.com --with-token") == 2
    assert "auth setup-git --hostname github.com" in gh_calls
    # 2: the token that works, alone, in a secrets file only its owner reads; the chat id
    # from the owner's message, read after one read found none yet.
    assert machine.secrets().read_text() == f"TELEGRAM_BOT_TOKEN={BOT_TOKEN}\n"
    assert machine.secrets().stat().st_mode & 0o777 == 0o600
    assert machine.telegram() == [
        "getUpdates read token=wrong", "getUpdates read token=ok",
        "getUpdates read token=ok", "getUpdates confirm token=ok",
    ]  # fmt: skip
    config = machine.config()
    assert config["notify"]["telegram_chat_id"] == str(CHAT_ID)
    # 3: the browser pool.
    assert config["browser"]["pool"] == "deploy@192.0.2.14"
    # 5: the project, made from the answers: its name, repository and branch, and the
    # commit name and email on its own clone. No question asked its goal.
    [project] = json.loads(machine.cli("project", "list", "--json"))
    assert (project["slug"], project["name"], project["repo"], project["default_branch"]) == (
        "widget-works", "Widget Works", FULL_URL, "feature",
    )  # fmt: skip
    assert project["goal"] == ""
    assert project["tracker"] == {"kind": "linear", "url": LINEAR_PROJECT, "link": "project"}
    clone = Path(project["repo_path"])
    assert _git(clone, "config", "--local", "user.name") == "Widget Bot"
    assert _git(clone, "config", "--local", "user.email") == "bot@widgets.example"
    assert "branch: the agents' pull requests go into feature" in out
    # Setup ended in the live planning session, on this terminal, in the project's clone,
    # after the first wake was set: the session is the install's last step.
    assert out.index("4. first wake: ") < out.index("planning live with claude-fable-5-1")
    live = (machine.fake / "claude-live").read_text().splitlines()
    assert live[0].startswith("argv: --session-id ")
    assert live[1] == "stdin: terminal"
    assert (machine.fake / "claude-live.cwd").read_text().strip() == project["repo_path"]
    # 4: wakes run from tomorrow 09:00, at minute 0 of 09:00 and every 4 hours round it.
    assert config["wake"]["start"] == f"{machine.date('-d', 'tomorrow', '+%Y-%m-%d')}T09:00+05:30"
    wake = machine.wake_line()
    assert wake.split()[:5] == ["0", "1,5,9,13,17,21", "*", "*", "*"]
    assert f"-ge {tomorrow_nine} ] && " in wake
    # The repository: nothing the owner had changed. The access check ran once, in the clone.
    assert (machine.fake / "probe.calls").read_text().splitlines() == [str(clone)]
    # The Linear link was proven once, in the clone, before it was saved (sys-w15).
    assert (machine.fake / "linear-probe.calls").read_text().splitlines() == [str(clone)]
    assert "linear: Deleted the test issue WID-57" in out
    assert _snapshot(machine.github / "owner" / "full") == before

    asked, crontab = machine.asked(), (machine.fake / "crontab.txt").read_text()
    status, again = machine.install([], "second")

    assert status == 0, again[-3000:]
    assert machine.asked() == asked
    for said in (
        "1. GitHub token: gh is already logged in",
        f"2. Telegram bot token: already in {machine.secrets()}",
        "Telegram chat: already saved",
        "3. browser pool: already answered",
        "4. first wake: already set",
        "5. project: a project is already on the board",
    ):
        assert said in again, again[-3000:]
    for question in PROJECT_QUESTIONS:
        assert question not in again
    assert len(machine.telegram()) == 4
    assert (machine.fake / "crontab.txt").read_text() == crontab
    assert _snapshot(machine.github / "owner" / "full") == before


def test_an_empty_repository_no_browser_pool_and_a_session_left_early(machine: Machine) -> None:
    """gh is logged in already, so the first question is Telegram's; the owner leaves the
    browser pool blank; `project new` gets an empty repository, whose every default the
    owner keeps with Enter, and the owner leaves the live planning session before the brief
    is written, so the planner asks the rest on Telegram; the first wake, asked before the
    project, is in 2 hours."""
    (machine.fake / "gh-token").write_text("ghp_from_before\n")
    earliest = datetime.now().astimezone() + timedelta(hours=2)

    status, out = machine.install(
        [
            ("Telegram bot token, from @BotFather", f"{BOT_TOKEN}\r"),
            ("The browser pool as user@host", "\r"),
            ("When does the first wake run", "\x15in 2 hours\r"),
            *_project_steps(
                "Empty One\r", f"{EMPTY_URL}\r", "\r", "\r", "\r", "\r"
            ),
        ],
        "empty",
    )

    assert status == 0, out[-3000:]
    _in_order(machine.asked(), [TELEGRAM_TOKEN, BROWSERS, FIRST_WAKE, *PROJECT_PROMPTS])
    _project_new_asked(out)
    assert "1. GitHub token: gh is already logged in" in out
    assert "auth login --hostname github.com --with-token" not in (machine.fake / "gh.calls").read_text()
    # The empty repository gets one commit with no files on main, and nothing else.
    empty = machine.github / "owner" / "empty"
    assert _git(empty, "for-each-ref", "--format=%(refname)") == "refs/heads/main"
    assert _git(empty, "log", "--format=%s", "main") == "Initial commit"
    assert _git(empty, "ls-tree", "main") == ""
    [project] = json.loads(machine.cli("project", "list", "--json"))
    assert (project["slug"], project["repo"], project["default_branch"]) == ("empty-one", EMPTY_URL, "main")
    assert project["tracker"] is None
    assert not (machine.fake / "linear-probe.calls").exists()
    clone = Path(project["repo_path"])
    assert _git(clone, "config", "--local", "user.name") == "Owner"
    assert _git(clone, "config", "--local", "user.email") == "7+owner@users.noreply.github.com"
    # The blank answer leaves browser.pool unset and is not asked again. The live session
    # ran in the clone and ended before the brief was written: its brief thread waits for
    # the planner, which asks the rest on Telegram.
    config = machine.config()
    assert config.get("browser", {}).get("pool", "") == ""
    assert machine.answered() == ["browser-pool"]
    assert (machine.fake / "claude-live.cwd").read_text().strip() == str(clone)
    [thread] = json.loads(machine.cli("plan", "list", "--json"))
    assert "before the brief was written; the planner asks the rest on Telegram (planning " \
        f"thread {thread['id']})" in " ".join(out.split())
    assert thread["status"] == "waiting_planner"
    left = thread["messages"][-1]
    assert left["from"] == "human" and "before the brief was written" in left["text"]
    # The first wake is the whole minute at or after 2 hours from now.
    start = datetime.fromisoformat(config["wake"]["start"])
    assert earliest - timedelta(minutes=1) <= start <= earliest + timedelta(minutes=2)
    assert machine.wake_line().split()[0] == str(start.minute)
    assert f"-ge {start:%Y%m%d%H%M} ] && " in machine.wake_line()

    status, again = machine.install([], "empty-again")
    assert status == 0, again[-3000:]
    assert "3. browser pool: already answered" in again
    assert "5. project: a project is already on the board" in again
    assert len(machine.asked()) == 3 + len(PROJECT_PROMPTS)


@pytest.mark.parametrize(
    "token_answers, telegram_log",
    [
        ([""], []),
        (["999:not-this-bot", ""], ["getUpdates read token=wrong"]),
    ],
    ids=["blank at once", "blank after a token Telegram refuses"],
)
def test_a_blank_telegram_answer_turns_telegram_off_and_a_second_run_asks_nothing(
    machine: Machine, token_answers: list[str], telegram_log: list[str]
) -> None:
    """The owner skips Telegram: the installer neither waits for a message to the bot nor
    reads any chat id, writes `notify.telegram = false` (the record of the answer), and a
    second run does not ask the Telegram question again."""
    (machine.fake / "gh-token").write_text("ghp_from_before\n")

    status, out = machine.install(
        [
            *[("Telegram bot token, from @BotFather", f"{answer}\r") for answer in token_answers],
            ("The browser pool as user@host", "\r"),
            ("When does the first wake run", "\x15in 2 hours\r"),
            *_project_steps("Empty One\r", f"{EMPTY_URL}\r", "\r", "\r", "\r", "\r"),
        ],
        "telegram-off",
    )

    assert status == 0, out[-3000:]
    _in_order(
        machine.asked(),
        [*[TELEGRAM_TOKEN] * len(token_answers), BROWSERS, FIRST_WAKE, *PROJECT_PROMPTS],
    )
    assert "2. Telegram: off; you answer on the board" in out
    assert "Waiting for your message to the bot" not in out
    assert machine.telegram() == telegram_log
    config = machine.config()
    assert config["notify"]["telegram"] is False
    assert "telegram_chat_id" not in config["notify"]
    assert machine.answered() == ["browser-pool"]  # Telegram's answer is the config key

    asked = machine.asked()
    status, again = machine.install([], "telegram-off-again")
    assert status == 0, again[-3000:]
    assert "2. Telegram: already off; you answer on the board" in again
    assert machine.asked() == asked
    assert machine.telegram() == telegram_log


def test_without_a_claude_login_the_installer_asks_nothing_and_names_claude_auth_login(
    machine: Machine,
) -> None:
    (machine.fake / "claude-logged-in").unlink()

    status, out = machine.install([], "no-claude")

    assert status == 1
    assert "claude auth login" in out
    assert machine.asked() == []
    assert not (machine.fake / "gh.calls").exists()


def test_with_no_terminal_the_installer_asks_nothing(machine: Machine) -> None:
    """`curl ... | bash` from a session with no terminal, such as cron or a CI runner: the
    checklist names each open item's fix instead."""
    result = subprocess.run(
        ["bash", "-c", 'cat "$0" | bash', str(machine.script)],
        env=machine.env, capture_output=True, text=True, check=False,
        start_new_session=True,  # no controlling terminal, so /dev/tty does not open
    )  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert "Setup questions: not asked, with no terminal to ask on" in result.stdout
    assert machine.asked() == []
    assert "project new" not in result.stdout
    assert not (machine.tmp / "repos").exists()


# The token path's and the SSH key path's gum prompts, as `project new` asks them when gh is
# not logged in (`github_access.login`), and the first access check fails.
TOKEN_LOGIN = ["choose: Log in with", "password: Personal access token (not shown)"]
SSH_LOGIN = [
    "choose: Log in with",
    "input: Path of the SSH private key that can push to the repository",
    "choose: gh still needs an API login:",
    "password: Personal access token (not shown)",
]


@pytest.mark.parametrize("how", ["token", "ssh key"])
def test_project_new_on_a_terminal_asks_every_question_with_gum(machine: Machine, how: str) -> None:
    """sys-w101: `autonomouse project new` run by hand on a terminal, with gh logged out and
    an access check that fails once, asks every question it has as a gum prompt: text with
    gum input, the token with gum input --password, and how to log in and re-check or quit
    with gum choose. None reads stdin as a line."""
    (machine.fake / "probe-push-missing-once").touch()
    key = machine.tmp / "id_ed25519"
    key.write_text("not a real key")
    gitconfig = Path(machine.env["GIT_CONFIG_GLOBAL"])  # the SSH form reaches the same folder
    gitconfig.write_text(
        gitconfig.read_text()
        + f'[url "file://{machine.github}/owner/full"]\n\tinsteadOf = git@github.com:owner/full.git\n'
    )
    login = (
        [(r"Log in with", "a personal access token\r"), (r"Personal access token", "ghp_fake_token\r")]
        if how == "token"
        else [
            (r"Log in with", "an SSH key path\r"),
            (r"Path of the SSH private key", f"{key}\r"),
            (r"gh still needs an API login:", "a personal access token\r"),
            (r"Personal access token", "ghp_fake_token\r"),
        ]
    )

    status, out = drive(
        ["autonomouse", "project", "new"],
        [
            (r"Project name, as the board shows it", "Widget Works\r"),
            (r"GitHub repository URL", f"{FULL_URL}\r"),
            *login,
            (r"Name the agents commit with", "\r"),
            (r"Email the agents commit with", "\r"),
            (r"Branch the agents' pull requests go into", "\r"),
            (r"Make the fix above, then", "re-check access\r"),
            (r"Linear project or issue URL", "\r"),
        ],
        env=machine.env, transcript=machine.tmp / "project-new.txt", timeout=60,
    )  # fmt: skip

    assert status == 0, out[-3000:]
    _in_order(machine.asked(), [
        "input: Project name, as the board shows it",
        "input: GitHub repository URL, https://github.com/<owner>/<name> or git@github.com:",
        *(TOKEN_LOGIN if how == "token" else SSH_LOGIN),
        "input: Name the agents commit with [Owner]",
        "input: Email the agents commit with [7+owner@users.noreply.github.com]",
        "input: Branch the agents' pull requests go into [main]",
        "choose: Make the fix above, then",
        "input: Linear project or issue URL for this project (blank for none)",
    ])  # fmt: skip
    assert "push is missing\nfix: give the token Contents read and write" in out.replace("\r", "")
    [project] = json.loads(machine.cli("project", "list", "--json"))
    assert (project["slug"], project["default_branch"]) == ("widget-works", "main")
    assert (machine.fake / "gh-token").read_text() == "ghp_fake_token\n"
    assert len((machine.fake / "probe.calls").read_text().splitlines()) == 2  # the re-check
    assert (machine.fake / "claude-live").read_text().splitlines()[1] == "stdin: terminal"


GOAL = "Small shops sell widgets online."
# What the owner and the live session do in it: they agree, and the session runs the two
# commands its instruction names, as it names them after the autonomouse command line, with
# the goal and a brief draft in place of `<goal>` and `<file>`.
LIVE_SESSION = f"""#!{sys.executable}
import os, pathlib, re, shlex, subprocess, sys
from autonomouse.board.brief import HEADINGS
draft = pathlib.Path(os.environ["FAKE_DIR"]) / "brief-draft.md"
body = {{"Goal": {GOAL!r}, "Intent": "The creator wants widgets sold."}}
draft.write_text("\\n".join(f"## {{h}}\\n\\n{{body.get(h, h)}}\\n" for h in HEADINGS))
for command in re.findall(r"`([^`]+)`", sys.argv[-1]):
    words = shlex.split(command)
    at = max(i for i, w in enumerate(words) if w == "autonomouse" or w.endswith("/autonomouse"))
    fill = {{"<goal>": {GOAL!r}, "<file>": str(draft)}}
    args = [fill.get(w, w) for w in words[at + 1 :]]
    subprocess.run([sys.executable, "-m", "autonomouse.cli", *args], check=True)
"""


def test_project_new_on_a_terminal_asks_no_goal_and_ends_in_a_fable_session_in_the_repo(
    machine: Machine,
) -> None:
    """sys-w103, the owner's item 6 of 2026-09-30: `project new` on a terminal asks no goal
    question and no "start planning now", and ends by running claude on this terminal in
    the project's clone, `repos/<slug>`: Fable 5.1 (`claude-fable-5-1`), high effort and
    `--dangerously-skip-permissions`, on an instruction of at most two lines that names the
    commands writing the goal and the brief. The owner agrees in the session, which runs
    those two commands as named: the project's goal and brief are on the board, the brief
    thread is closed, the setup is finished and the board committed. No planner turn runs
    headless. Fails on main, which asks the goal and whether to plan now, and whose live
    session resumes the planner's in the charters' folder at the config's tier."""
    (machine.fake / "gh-token").write_text("ghp_from_before\n")  # gh is logged in
    session = machine.fake / "live-session"
    session.write_text(LIVE_SESSION)
    session.chmod(0o755)
    board = Path(machine.env["AUTONOMOUSE_HOME"])

    status, out = drive(
        ["autonomouse", "project", "new"],
        [
            (r"Project name, as the board shows it", "Widget Works\r"),
            (r"GitHub repository URL", f"{FULL_URL}\r"),
            (r"Name the agents commit with", "\r"),
            (r"Email the agents commit with", "\r"),
            (r"Branch the agents' pull requests go into", "\r"),
            (r"Linear project or issue URL", "\r"),
        ],
        env=machine.env, transcript=machine.tmp / "project-new.txt", timeout=60,
    )  # fmt: skip

    assert status == 0, out[-3000:]
    _in_order(machine.asked(), PROJECT_PROMPTS)
    assert not [q for q in machine.asked() if "goal" in q.lower() or q.startswith("confirm:")]
    assert "Project goal" not in out and "Start planning now" not in out
    # The live session: claude on this terminal, in the clone, at Fable 5.1 and high effort,
    # permissions skipped, on two lines that name the goal's and the brief's commands.
    repo = Path(machine.env["AUTONOMOUSE_REPOS"]) / "widget-works"
    [project] = json.loads(machine.cli("project", "list", "--json"))
    assert project["repo_path"] == str(repo)
    assert (machine.fake / "claude-live.cwd").read_text().strip() == str(repo)
    assert (machine.fake / "claude-live").read_text().splitlines()[1] == "stdin: terminal"
    argv = (machine.fake / "claude-live.argv").read_text().split("\0")[:-1]
    assert argv[argv.index("--model") + 1] == "claude-fable-5-1"
    assert argv[argv.index("--effort") + 1] == "high"
    assert "--dangerously-skip-permissions" in argv and "-p" not in argv
    [thread] = json.loads(machine.cli("plan", "list", "--json"))
    instruction = argv[-1]
    assert len(instruction.splitlines()) <= 2, instruction
    assert "You are planning the autonomouse project Widget Works" in instruction
    assert "project edit widget-works --goal" in instruction
    assert f"plan brief {thread['id']} <file>" in instruction
    assert not (machine.fake / "claude.calls").exists()  # no headless planner turn
    # What the session wrote with the owner, through those commands; then setup finished.
    assert project["goal"] == GOAL
    assert load_brief(board, "widget-works").goal == GOAL
    assert thread["status"] == "closed"
    assert "The brief was written with the human in the live session" in thread["messages"][-1]["text"]
    assert "planning: the brief is written; read it with: autonomouse show widget-works" in out
    assert list((Path(machine.env["AUTONOMOUSE_STATE"]) / "setup").glob("*.json")) == []
    assert _git(board, "log", "-1", "--format=%s") == (
        f"autonomouse project new widget-works: brief thread {thread['id']}"
    )
    assert _git(board, "status", "--porcelain") == ""


UNFINISHED = "choose: Continue a project whose setup did not finish, or start a new one"
WIDGETS_AT_ACCESS = "Widget Works: stopped at the GitHub access check"
WIDGETS_AT_PLANNING = "Widget Works: stopped at planning: its brief is not written yet"
GADGETS_AT_PLANNING = "Gadgets: stopped at planning: its brief is not written yet"


def _options(out: str) -> list[str]:
    """The options the fake gum drew for the last choice in `out`, in order."""
    text = out.replace("\r", "")
    options = []
    for line in text[text.rindex(UNFINISHED.split(": ", 1)[1]) :].split("\n")[1:]:
        if not line.startswith("  "):
            return options
        options.append(line[2:])
    return options


def test_project_new_offers_every_unfinished_setup_and_continues_the_one_chosen(
    machine: Machine,
) -> None:
    """sys-w102, the owner's item 3 of 2026-09-30: `project new` run by hand on a terminal
    first offers every project whose setup did not finish, plus start a new project, and
    continues the one chosen from the step it stopped at, asking nothing already answered.
    Widget Works stops at the GitHub access check (the fake check fails once and the owner
    quits); Gadgets, made by a script with flags and no terminal, stops at planning,
    which goes to Telegram. Continuing Widget Works runs the access check again, asks
    only what comes after it and ends in the live session, which the owner leaves before
    the brief; continuing Gadgets starts the live session on its brief thread at once. Once
    both briefs are written, nothing is offered and the name is the first question. Fails
    on main, where `project new` asks the name first every time."""
    (machine.fake / "gh-token").write_text("ghp_from_before\n")  # gh is logged in
    (machine.fake / "probe-push-missing-once").touch()
    records = Path(machine.env["AUTONOMOUSE_STATE"]) / "setup"

    def project_new(steps: list[tuple[str, str]], name: str) -> tuple[list[str], int, str]:
        before = len(machine.asked())
        status, out = drive(
            ["autonomouse", "project", "new"], steps, env=machine.env,
            transcript=machine.tmp / f"{name}.txt", timeout=60,
        )  # fmt: skip
        return machine.asked()[before:], status, out

    # 1. Widget Works stops at the access check: the owner quits there.
    asked, status, out = project_new([
        (r"Project name, as the board shows it", "Widget Works\r"),
        (r"GitHub repository URL", f"{FULL_URL}\r"),
        (r"Name the agents commit with", "Widget Bot\r"),
        (r"Email the agents commit with", "bot@widgets.example\r"),
        (r"Branch the agents' pull requests go into", "feature\r"),
        (r"Make the fix above, then", "quit\r"),
    ], "stop-at-access")  # fmt: skip
    assert status == 1, out[-3000:]
    assert UNFINISHED not in asked  # no setup had stopped before this one
    assert "access: quit at the access check; the project is not created" in out
    assert json.loads(machine.cli("project", "list", "--json")) == []

    # 2. Gadgets: a script with flags and no terminal; planning goes to Telegram, so the
    # project is made and its brief is never written.
    script = subprocess.run(
        [sys.executable, "-m", "autonomouse.cli", "project", "new", "Gadgets",
         "--repo", EMPTY_URL, "--goal", "Gadgets for everyone."],
        env=machine.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert script.returncode == 0, script.stdout + script.stderr
    assert "planning: the planner asks its questions on Telegram" in script.stdout
    assert UNFINISHED.split(": ", 1)[1] not in script.stdout  # nothing offered with no terminal

    # 3. The list: both, the most recent first, plus start a new project. Continuing Widget
    # Works starts at the access check, which passes now, then asks what comes after it,
    # and ends in the live session, which the fake leaves at once.
    asked, status, out = project_new([
        (r"Continue a project whose setup did not finish", f"{WIDGETS_AT_ACCESS}\r"),
        (r"Linear project or issue URL", "\r"),
    ], "continue-widgets")  # fmt: skip
    assert status == 0, out[-3000:]
    assert _options(out) == [GADGETS_AT_PLANNING, WIDGETS_AT_ACCESS, "start a new project"]
    _in_order(asked, [
        UNFINISHED,
        "input: Linear project or issue URL for this project (blank for none)",
    ])  # fmt: skip
    assert "setup: continuing Widget Works (widget-works), which stopped at the GitHub access check" in out
    projects = {p["slug"]: p for p in json.loads(machine.cli("project", "list", "--json"))}
    widgets, gadgets = projects["widget-works"], projects["gadgets"]
    assert (widgets["slug"], widgets["name"], widgets["repo"], widgets["default_branch"]) == (
        "widget-works", "Widget Works", FULL_URL, "feature",
    )  # fmt: skip
    clone = Path(widgets["repo_path"])
    assert (machine.fake / "claude-live.cwd").read_text().strip() == str(clone)
    assert _git(clone, "config", "--local", "user.name") == "Widget Bot"
    assert _git(clone, "config", "--local", "user.email") == "bot@widgets.example"
    # The access check ran for Widget Works (failed), for Gadgets, then for Widget Works again.
    probed = (machine.fake / "probe.calls").read_text().splitlines()
    assert probed == [str(clone), gadgets["repo_path"], str(clone)]

    # 4. Continuing Gadgets, which stopped at planning, starts the live session at once, in
    # its clone, on the brief thread the planner on Telegram has.
    asked, status, out = project_new([
        (r"Continue a project whose setup did not finish", f"{GADGETS_AT_PLANNING}\r"),
    ], "continue-gadgets")  # fmt: skip
    assert status == 0, out[-3000:]
    assert _options(out) == [WIDGETS_AT_PLANNING, GADGETS_AT_PLANNING, "start a new project"]
    assert asked == [UNFINISHED]
    threads = {t["project"]: t for t in json.loads(machine.cli("plan", "list", "--json"))}
    assert (machine.fake / "claude-live.cwd").read_text().strip() == gadgets["repo_path"]
    prompt = (machine.fake / "claude-live.argv").read_text().split("\0")[-2]
    assert f"plan brief {threads['gadgets']['id']} <file>" in prompt
    assert len(json.loads(machine.cli("project", "list", "--json"))) == 2

    # 5. Both briefs written, as the planner writes them: every setup finished, so nothing
    # is offered and the name is the first question (the owner leaves it with ctrl-d).
    for slug, thread in threads.items():
        draft = machine.tmp / f"{slug}-brief.md"
        body = {"Goal": f"{slug} ship.", "Intent": f"The creator wants {slug}."}
        draft.write_text("\n".join(f"## {h}\n\n{body.get(h, h)}\n" for h in HEADINGS))
        machine.cli("plan", "brief", thread["id"], str(draft))
    asked, status, out = project_new(
        [(r"Project name, as the board shows it", "\x04")], "all-finished"
    )
    assert status == 1, out[-3000:]
    assert asked == ["input: Project name, as the board shows it"]
    assert list(records.glob("*.json")) == []
