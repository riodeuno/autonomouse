"""The create-project wizard, `autonomouse project new [<name>]`: from a GitHub URL to a
project autonomouse works on, asking every per-project question its flags do not answer, so
a second project is set up the same way as the first (sys-w20). When stdin is a terminal,
each question is a gum prompt, as `setup/install.sh` asks its own (`autonomouse.gum`,
sys-w101): a line of text, a secret without echo, or a choice from a list (`init.Choice`).
With no terminal, each is a line of stdin, and a script or an agent answers by flags and
piped lines.

In order:

0. ask the project's name, as the board shows it, when neither `<name>` nor `--name`
   gives it; a name with no letter or digit to make a slug from is asked again;
1. create the board when none exists (`make_folder` and `make_repository` from
   `autonomouse.init`), so this is the first command a fresh machine needs after the
   installer;
2. ask the repository URL, `https://github.com/<owner>/<name>` or
   `git@github.com:<owner>/<name>.git`; a URL outside github.com is asked again. The
   record's `repo` is always the https form;
3. clone it into `repos_root()/<slug>` (`$AUTONOMOUSE_REPOS`, else
   `~/projects/autonomouse/repos`), the project's `repo_path`, first making the clone
   root and the worktree root (`worktrees_root()`, `$AUTONOMOUSE_WORKTREES`, else
   `~/projects/autonomouse/worktrees`) when they are missing. A clone already there
   whose origin names the same repository is reused, so a run that failed later
   continues from it;
4. ask three questions (sys-w9), each with a default that Enter keeps, and each skipped
   by its flag: the name the agents commit with (`--git-name`; default the GitHub
   account's name, else its login) and their email (`--git-email`; default the account's
   no-reply address), both set with `git config` on the clone only; then the branch their
   pull requests go into (`--default-branch`; default the clone's `origin/HEAD`), recorded
   as the project's `default_branch`. It asks them even when git already resolves an
   author. A branch that does not exist on origin is refused and asked again; given by
   flag, it stops the wizard. Stdin that has ended answers each of the three as Enter
   does, so a caller with no terminal gets the defaults. An empty repository, one with no
   branch yet as GitHub makes it, has no `origin/HEAD`: the branch question offers
   `main`, and the wizard pushes one commit with no files to the chosen branch, which
   GitHub then makes the default branch, and checks the clone out on it
   (`_first_commit`). That commit and branch are the only things it adds, and it changes
   nothing that was there;
5. ask the Linear project or issue URL the project is linked to (`--tracker`; sys-w14),
   whose blank answer, also what ended stdin gives, links none. A
   URL that is no Linear project or issue is refused and asked again; given by flag, it
   stops the wizard before step 1. Before a link is stored in the project's `tracker`
   (`autonomouse.board.trackers`), `check_tracker` proves the link with a small agent
   (sys-w15), which creates one test issue there through the Linear MCP and removes it;
   when it cannot, an answer is refused with the fix and asked again, and a flag stops
   the wizard before `project.json` with the fix;
6. write `project.json` (slug from the name, priority one more than the highest on the
   board, 1 on an empty one) and commit the board. Its `goal` is `--goal`'s, else empty:
   no question asks it (the human's item 6 of 2026-09-30), since planning writes it;
7. write the crontab lines (`autonomouse.timer.install`), so the project is worked from
   the next wake on;
8. check, by key name only (`docs/rules.md`, item 7), that `TELEGRAM_BOT_TOKEN` is in the
   secrets file and `notify.telegram_chat_id` is set, and print the three steps that
   set them up when either is missing; with Telegram off (`notify.telegram = false`) it
   prints one line saying you answer on the board, and no steps;
9. plan the project: its goal and its brief are written with the human before it gets
   any card (`docs/planner.md`, "The brief"). On a terminal, the wizard ends in a live
   Claude session in the project's repository, `repo_path` (`_plan_live`, sys-w103): Fable
   5.1 at high effort with `--dangerously-skip-permissions`, on an instruction of two
   lines, which writes the goal, the brief and the human's instructions for the project
   with the human on the project's brief thread. With no terminal, it opens the brief thread and runs the planner on it once
   (`autonomouse.planner.brief`), prints the planner's first question, which asks what the
   project is for, and commits the board; the planner asks the rest on Telegram. No
   question asks whether to plan now.

On a terminal, with no name, `--slug` or `--repo-path` given, the first question, before
step 0, is a choice of every project whose setup did not finish, plus start a new project
(sys-w102). A setup has not finished until its project's brief is written: it stopped at a
question (esc, ctrl-c, stdin that ended, quit at the access check), at a step that failed,
or at planning, which the live session or the planner on Telegram may not have ended yet.
`autonomouse.setup_record` keeps, on this machine, each setup's accepted answers and ended
steps.
Choosing one continues it from the step it stopped at: a saved answer is used wherever no
flag gives one, and an ended step is skipped. A clone already made skips the GitHub login
too, and the access check reads how the clone pushes from its own origin. A setup that
stopped at planning goes straight to the live session, on its brief thread. With
no terminal, or with a name given, a setup starts from its first question and its record
starts over, so a script's flags keep deciding. With no unfinished setup, nothing is
offered and the name is the first question.

Stdin that has ended answers each question that has a default, so any caller with no
terminal (`project new <name> --repo <url> [--goal <goal>] </dev/null`) gets a project,
no live session, and the planner's questions on Telegram. `setup/install.sh` runs
`project new` with no flags on the terminal (`</dev/tty`), so the human answers every
question there, with gum, and ends in the live session. A question with no default (the
name, the URL) that stdin cannot answer stops the wizard and names the question. Each
project gets its own clone under `repos_root()`, with its own commit identity and branch;
the crontab lines are one set for every project, rewritten in place by each run of step 7.

A step that fails prints its fix and exits non-zero before `project.json` is written;
the crontab lines, the Telegram check and planning come after it, so their failure leaves
the project in place and names the command that finishes the job. The GitHub access check
is the one step that waits instead (sys-w100): on a failure it prints the fix and asks
whether to re-check, which runs the check again, or to quit; only quit, or stdin that has
ended, stops the wizard there.

With `--repo-path`, the project is recorded from that existing checkout and nothing else
runs: no board creation, no clone, no question of step 4, no crontab lines, no Telegram
check, no planning, no tracker question (`--tracker` links one, after `check_tracker`
proves it) — the path every caller that already has a checkout uses (the planner, the
tests), with the flags it always passed. Its default branch is the checkout's
`origin/HEAD` unless `--default-branch` names one; `--git-name` and `--git-email`, when
given, are set on that checkout as in step 4.

Before step 0, the cloning form checks that Claude Code is logged in (`claude auth
status`), since the GitHub access check and the planner both run as Claude sessions; when
it is not, the wizard stops before its first question and names `claude auth login`, as
`setup/install.sh` does.

Between steps 2 and 5, `autonomouse.github_access` (sys-s3) makes sure autonomouse can do
on GitHub what its workers do: right after the URL question it logs gh in when it is not
(a token, or an SSH key the clone then uses); after the clone it offers the account's name
and email for step 4's questions and sets the answers on the clone; then a small agent
checks access from the chosen branch (`docs/probes/github.md`): on a throwaway branch and
draft pull request against that branch, it pushes, opens and closes the pull request,
reads its CI status, review comments and merge conflicts, and cancels the repository's
latest finished Actions run, saying each step in plain words as it goes. When an ability
the workers need is not proven, `_check_access` prints the fix and asks re-check or quit;
stdin that has ended (a caller with no terminal) stops the wizard before `project.json`
with the fix. Nothing here writes a credential.
"""

from __future__ import annotations

import getpass
import re
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

from autonomouse import github, github_access, planner, probe, setup_record, timer
from autonomouse.board import commands as board_commands
from autonomouse.board import trackers
from autonomouse.board.brief import brief_path
from autonomouse.board.paths import repos_root, worktrees_root
from autonomouse.board.plans import Author, thread_open
from autonomouse.board.schema import Project
from autonomouse.board.store import commit_board
from autonomouse.config import load_config, secrets_path
from autonomouse.controller import default_state_dir
from autonomouse.home import locate
from autonomouse.init import (
    Ask,
    Choice,
    InitError,
    Say,
    answer,
    make_folder,
    make_repository,
)
from autonomouse.notify import telegram_on

TELEGRAM_STEPS = (
    "put TELEGRAM_BOT_TOKEN=<the bot's token, from @BotFather> in {secrets} (mode 600)",
    "message the bot once on Telegram, so it knows your chat",
    "run: autonomouse notify setup",
)


class WizardError(Exception):
    """A step failed; the message names the step, what went wrong and the fix."""


def run_project_new(
    root: Path,
    *,
    name: str | None,
    slug: str | None,
    repo: str | None,
    goal: str | None,
    repo_path: str | None,
    default_branch: str | None,
    priority: int | None,
    ask: Ask,
    say: Say,
    read_secret: Callable[[str], str] = getpass.getpass,
    git_name: str | None = None,
    git_email: str | None = None,
    tracker: str | None = None,
    install: Callable[[], object] = timer.install,
    terminal: bool = False,
) -> Project:
    """Create the project `name` on the board at `root` (module docstring).

    Each field but the goal comes from its keyword when not None, else from a question
    through `ask`; the goal comes from `goal` or is empty. A GitHub token is read with
    `read_secret`, without echo. On a terminal the CLI passes `autonomouse.gum`'s two, else
    `init.ask_stdin` and `getpass.getpass`. `install` is what step 7 calls; a test passes
    a stub. `terminal`, which the CLI sets when stdin is a terminal, makes step 9 the live
    session (`_plan_live`), and, with no name, slug or checkout given, makes the first
    question offer every setup that did not finish (`_choose_setup`); without it, the
    planner asks on Telegram. Raises `WizardError`, `InitError` (board creation),
    `CommandError` (the project record), `GitCommitError` (the board commit) or
    `TimerError` (the timers), each with a message safe to print. Returns the project as
    the board holds it at the end, with the goal the live session wrote.
    """
    if repo_path is None:
        _claude_logged_in()
    setup = None
    if terminal and repo_path is None and name is None and slug is None:
        setup = _choose_setup(root, ask)
    if setup is not None:
        name, slug = setup.name, setup.slug
    if name is None:
        name = answer(ask, PROJECT_NAME_QUESTION, None, None if slug else _check_name)
    slug = slug or _slug_from_name(name)
    if tracker is not None:
        try:
            trackers.project_link(tracker)
        except trackers.TrackerError as e:
            raise WizardError(f"--tracker {e}") from None
    if repo_path is not None:
        checkout = Path(repo_path).expanduser()
        branch = default_branch or _default_branch(checkout)
        if git_name is not None or git_email is not None:
            try:
                github_access.set_identity(checkout, say, name=git_name, email=git_email)
            except github_access.AccessError as e:
                raise WizardError(str(e)) from e
        repo = repo or answer(ask, "GitHub repository URL the pull requests are opened against")
        if tracker is not None:
            check_tracker(tracker, checkout=checkout, say=say)
        return _record(
            root,
            slug=slug,
            name=name,
            goal=goal or "",
            repo=repo,
            repo_path=str(checkout),
            default_branch=branch,
            priority=priority,
            tracker=tracker,
        )

    make_folder(root, say)
    make_repository(root, say)
    if setup is None:
        setup = setup_record.Setup(slug=slug, name=name)
    else:
        say(f"setup: continuing {name} ({slug}), which {setup.stopped_at()}")
    project_file = root / "projects" / slug / "project.json"
    if not setup.project and project_file.exists():
        left = setup_record.load(slug)
        raise WizardError(
            f"project {slug!r} is already on the board ({project_file}); pick another name, "
            "or edit it with: autonomouse project edit"
            + (
                "; its setup did not finish: run `autonomouse project new` with no name on "
                "a terminal to continue it"
                if left is not None and left.project
                else ""
            )
        )
    setup.save()
    if setup.project:
        project = board_commands.project_show(root, slug)
    else:
        project = _make_project(
            root, setup, repo=repo, goal=goal, default_branch=default_branch,
            priority=priority, git_name=git_name, git_email=git_email, tracker=tracker,
            ask=ask, say=say, read_secret=read_secret,
        )  # fmt: skip
    if not setup.timers:
        try:
            install()
        except timer.TimerError as e:
            raise WizardError(
                f"timers: {e}\nfix: the project is on the board; run `autonomouse timer "
                "install` once cron is installed"
            ) from e
        say(
            "timers: three crontab lines written (the wake, the morning review, plan step "
            "every five minutes); `autonomouse timer status` shows them"
        )
        setup.timers = True
        setup.save()
    if setup.thread is None:
        _telegram_check(say)
        if not terminal:
            _brief(root, setup, say)
    if terminal:
        _plan_live(root, setup, project, say)
    else:
        say(
            f"planning: the planner asks its questions {_asks_where()} (planning thread "
            f"{setup.thread}); join it live any time with: autonomouse plan join "
            f"{setup.thread}"
        )
    if brief_path(root, slug).is_file():
        setup.remove()
    return board_commands.project_show(root, slug)


def _make_project(
    root: Path,
    setup: setup_record.Setup,
    *,
    repo: str | None,
    goal: str | None,
    default_branch: str | None,
    priority: int | None,
    git_name: str | None,
    git_email: str | None,
    tracker: str | None,
    ask: Ask,
    say: Say,
    read_secret: Callable[[str], str],
) -> Project:
    """Steps 2 to 6: from the repository URL to `project.json` and the board commit. Each
    step `setup` records as done is skipped, and each answer it holds is used where no flag
    gives one; each answer and step is saved in `setup` as it is accepted or ends."""
    url = _repo_url(repo or setup.repo, ask)
    setup.repo = url
    setup.save()
    repo_slug = github.repo_slug(url)
    https_url = f"https://github.com/{repo_slug}"
    try:
        if setup.clone is None:
            login = github_access.login(ask, say, read_secret=read_secret)
            origin_url = login.clone_url(url)
            for folder in (repos_root(), worktrees_root()):
                folder.mkdir(parents=True, exist_ok=True)
            clone = _clone(origin_url, repos_root() / setup.slug, say, config=login.clone_config())
            setup.clone = str(clone)
            setup.save()
        else:
            # The login and the clone are done: the clone's own origin says how it pushes.
            clone = Path(setup.clone)
            origin_url = _git_read(clone, "config", "--get", "remote.origin.url") or url
            say(f"clone: {clone} (cloned before, origin {origin_url})")
        setup.git_name, setup.git_email = _ask_identity(
            clone, ask, say, name=git_name or setup.git_name, email=git_email or setup.git_email
        )
        setup.save()
        empty = _is_empty(clone)
        branch = _ask_branch(clone, ask, flag=default_branch or setup.branch, empty=empty)
        if empty:
            _first_commit(clone, branch, say, origin_url=origin_url, slug=repo_slug)
        say(f"branch: the agents' pull requests go into {branch}")
        setup.branch = branch
        setup.save()
        if not setup.access:
            _check_access(
                clone, slug=repo_slug, branch=branch, origin_url=origin_url, ask=ask, say=say
            )
            setup.access = True
            setup.save()
    except github_access.AccessError as e:
        raise WizardError(str(e)) from e
    setup.goal = goal or setup.goal
    setup.save()
    if tracker is not None:
        check_tracker(tracker, checkout=clone, say=say)
    elif setup.tracker is not None:
        tracker = setup.tracker  # proven when it was answered
    else:
        tracker = answer(
            _enter_on_ended_stdin(ask), TRACKER_QUESTION, "", _tracker_answer(clone, say)
        )
    setup.tracker = tracker
    setup.save()
    project = _record(
        root,
        slug=setup.slug,
        name=setup.name,
        goal=setup.goal or "",
        repo=https_url,
        repo_path=str(clone),
        default_branch=branch,
        priority=priority,
        tracker=tracker or None,
    )
    setup.project = True
    setup.save()
    sha = commit_board(root, f"autonomouse project new {setup.slug}")
    say(
        f"project: created {setup.slug} ({setup.name}), priority {project.priority}, tracker "
        f"{trackers.describe(project.tracker)}; board commit {sha}"
    )
    return project


UNFINISHED = "Continue a project whose setup did not finish, or start a new one"
NEW_PROJECT = "start a new project"


def _choose_setup(root: Path, ask: Ask) -> setup_record.Setup | None:
    """The first question on a terminal (sys-w102): every setup that did not finish
    (`setup_record.unfinished`), the most recent first, plus start a new project. Returns
    the setup chosen, or None for a new project; asks nothing, and returns None, when every
    setup finished."""
    setups = setup_record.unfinished(root)
    if not setups:
        return None
    labels = {str(i): f"{s.name}: {s.stopped_at()}" for i, s in enumerate(setups, 1)}
    labels["n"] = NEW_PROJECT

    def one_of(reply: str) -> None:
        if reply not in labels:
            raise ValueError(f"{reply!r}: answer one of {', '.join(labels)}")

    reply = answer(ask, Choice(UNFINISHED, labels), None, one_of)
    return None if reply == "n" else setups[int(reply) - 1]


RECHECK = Choice("Make the fix above, then", {"r": "re-check access", "q": "quit"})


def _recheck_or_quit(reply: str) -> None:
    if reply.lower() not in ("r", "re-check", "recheck", "q", "quit"):
        raise ValueError(f"{reply!r}: answer r to re-check access, or q to quit")


def _check_access(
    clone: Path, *, slug: str, branch: str, origin_url: str, ask: Ask, say: Say
) -> None:
    """Run the GitHub access check (`github_access.check_access`) until it passes. On a
    failure, print the fix and wait for the human's choice: re-check runs the check again,
    quit stops the wizard before `project.json` (sys-w100). Setup never leaves on its own
    while someone can answer; stdin that has ended (a script or an agent with flags and no
    keyboard) stops the wizard with the fix."""
    while True:
        try:
            github_access.check_access(
                clone, slug=slug, branch=branch, origin_url=origin_url, say=say
            )
            return
        except github_access.AccessError as e:
            failure = e
        say(str(failure))
        try:
            reply = answer(ask, RECHECK, None, _recheck_or_quit)
        except InitError:  # stdin ended (nobody is there to choose), or esc or ctrl-c
            raise WizardError(f"{failure}\nthen run this command again") from failure
        if reply.lower() in ("q", "quit"):
            raise WizardError(
                "access: quit at the access check; the project is not created\nfix: make "
                "the fix above, then run this command again"
            )


def _claude_logged_in() -> None:
    """Stop before any question when Claude Code is not logged in: the GitHub access check
    and the planner both run as Claude sessions."""
    try:
        status = subprocess.run(
            ["claude", "auth", "status"], capture_output=True, text=True, check=False
        )
    except OSError as e:
        raise WizardError(
            f"claude: Claude Code is not installed on this machine ({e})\nfix: run the "
            "install command, then log in with `claude auth login`, then run this command again"
        ) from e
    if status.returncode != 0:
        raise WizardError(
            "claude: Claude Code is not logged in on this machine, and the GitHub access check "
            "and the planner run as Claude sessions\nfix: run `claude auth login`, then run "
            "this command again"
        )


def _brief(root: Path, setup: setup_record.Setup, say: Say) -> None:
    """Step 9: the brief thread, with the planner settings `plan step` uses. Its id is
    saved in `setup` as soon as the thread is open, so a planner that fails leaves the
    setup to continue at planning."""
    slug = setup.slug
    say("brief: the planner reads its charter and writes its first question about the brief")
    state_dir = default_state_dir() / "plans"
    thread, ok = planner.brief(
        root,
        slug,
        config=load_config(),
        claude="claude",
        home=locate(),
        secrets=secrets_path(),
        state_dir=state_dir,
        out=say,
    )
    setup.thread = thread.id
    setup.save()
    sha = commit_board(root, f"autonomouse project new {slug}: brief thread {thread.id}")
    say(f"brief: planning thread {thread.id}" + (f"; board commit {sha}" if sha else ""))
    if not ok:
        raise WizardError(
            f"brief: the planner's first turn on planning thread {thread.id} failed: its exit "
            f"code is above, its stderr log in {state_dir}\nfix: the project and its brief "
            "thread are on the board; `autonomouse plan step` runs the planner again, as the "
            "timer does every five minutes"
        )
    asked = [m.text for m in thread.messages if m.from_ is Author.PLANNER]
    if asked:
        say(f"brief: the planner's first question:\n{asked[0]}")
    else:
        say(
            "brief: no question yet; the next plan step, which the timer runs every five "
            f"minutes, runs the planner on the thread and sends its first question "
            f"{_asks_where()}"
        )


def _plan_live(root: Path, setup: setup_record.Setup, project: Project, say: Say) -> None:
    """Step 9 on a terminal (the human's item 6 of 2026-09-30): the live session
    (`planner.setup_session`) in the project's repository, `repo_path`, on the project's
    brief thread, which is opened under the plan-step lock when the setup has none yet and
    saved in `setup` at once. Then the board is committed. A session that ends before the
    brief is written leaves the thread to the planner on Telegram, and the setup to
    continue at planning."""

    def claim() -> str:
        if setup.thread is None:
            setup.thread = thread_open(root, setup.slug, planner.BRIEF_TEXT).id
            setup.save()
        return setup.thread

    say(
        "planning: a live Claude session starts in the project's repository; plan the "
        "project with it, and it writes the goal and the brief when you agree"
    )
    # Claude asks first about a folder it has not been told to trust, with "No, exit"
    # under the cursor, so a quick Enter would end the session before it starts.
    say(
        "planning: if Claude first asks whether you trust the folder, choose "
        '"Yes, I trust this folder"'
    )
    try:
        code = planner.setup_session(
            root, claim, name=setup.name, cwd=Path(project.repo_path), claude="claude",
            home=locate(), secrets=secrets_path(), state_dir=default_state_dir() / "plans",
            out=say,
        )  # fmt: skip
    except board_commands.CommandError as e:
        raise WizardError(
            f"planning: {e}\nfix: the project is on the board; run `autonomouse project "
            "new` on a terminal again to continue its setup at planning"
        ) from e
    sha = commit_board(root, f"autonomouse project new {setup.slug}: brief thread {setup.thread}")
    say(f"planning: board commit {sha}" if sha else "planning: nothing new to commit")
    if brief_path(root, setup.slug).is_file():
        say(f"planning: the brief is written; read it with: autonomouse show {setup.slug}")
        return
    say(
        f"planning: the session ended (exit code {code}) before the brief was written; the "
        f"planner asks the rest {_asks_where()} (planning thread {setup.thread}); to plan "
        "here instead, run `autonomouse project new` on a terminal again"
    )


def _record(
    root: Path,
    *,
    slug: str,
    name: str,
    goal: str,
    repo: str,
    repo_path: str,
    default_branch: str,
    priority: int | None,
    tracker: str | None,
) -> Project:
    if priority is None:
        priority = max((p.priority for p in board_commands.project_list(root)), default=0) + 1
    return board_commands.project_new(
        root,
        slug=slug,
        name=name,
        goal=goal,
        repo=repo,
        repo_path=repo_path,
        default_branch=default_branch,
        priority=priority,
        tracker=tracker,
    )


TRACKER_QUESTION = "Linear project or issue URL for this project (blank for none)"


def _tracker_answer(checkout: Path, say: Say) -> Callable[[str], None]:
    """The tracker question's check: a blank answer links no tracker; any other must be a
    link a known tracker reads, and `check_tracker` must prove it. A refusal is printed
    and the question asked again, so the owner can fix access or answer blank."""

    def check(url: str) -> None:
        if url:
            trackers.project_link(url)
            try:
                check_tracker(
                    url, checkout=checkout, say=say,
                    then="give the link again, or answer blank to link none now",
                )  # fmt: skip
            except WizardError as e:
                raise ValueError(str(e)) from e

    return check


def check_tracker(
    url: str,
    *,
    checkout: Path,
    say: Say,
    then: str = "run this command again",
    claude: str = "claude",
) -> None:
    """Prove that the agents can reach the tracker link `url` before it is saved (sys-w15):
    a small agent (`autonomouse.probe`) runs the charter `docs/probes/<kind>.md` of the
    link's tracker in the project's `checkout`, reads the link, creates one test issue
    there titled `autonomouse access check <UTC timestamp>-<random>` and removes it,
    saying each step as it goes. For Linear it reaches Linear only through the Linear MCP.

    Raises `WizardError` with each fix when the agent could not prove a required
    permission (a missing Linear MCP, a refused create or removal) or gave no report, so
    the link is not saved; each message ends with `then`, what the owner does after the
    fix. Knows no tracker: a new tracker is its entry in
    `autonomouse.board.trackers` and its charter."""
    link = trackers.project_link(url)
    kind = link["kind"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    title = f"autonomouse access check {stamp}-{token_hex(4)}"
    facts = {"link": link["url"], "link points at": link["link"], "test issue title": title}
    say(
        f"{kind}: a small agent checks that the agents can create issues at {link['url']}: "
        "it creates one test issue there and removes it; each step follows"
    )
    try:
        report = probe.run_probe(
            kind, cwd=checkout, facts=facts, say=lambda line: say(f"{kind}: {line}"),
            claude=claude,
        )  # fmt: skip
    except OSError as e:
        raise WizardError(
            f"{kind}: the link is not saved: the access check could not start claude in "
            f"{checkout}: {e}\nfix: check that Claude Code is installed and that {checkout}, "
            f"the project's checkout, exists, then {then}"
        ) from e
    except probe.ProbeError as e:
        raise WizardError(
            f"{kind}: the link is not saved: {e}\nfix: check that `claude -p` runs on this "
            f"machine (`claude auth status`), remove any issue titled {title!r} left at "
            f"{link['url']}, then {then}"
        ) from e
    for note in report.notes:
        say(f"{kind}: {note}")
    for p in report.warnings:
        say(f"{kind}: warning: {p.name} is {p.status}: {p.fix}")
    if report.blocking:
        raise WizardError(
            f"{kind}: the link is not saved: the agents cannot reach {link['url']} until "
            f"these are fixed, then {then}:\n"
            + "\n".join(f"{p.name} is {p.status}\nfix: {p.fix}" for p in report.blocking)
        )
    say(f"{kind}: the agents can read {link['url']} and create issues there")


PROJECT_NAME_QUESTION = "Project name, as the board shows it"


def _check_name(name: str) -> None:
    if not re.search(r"[a-z0-9]", name.lower()):
        raise ValueError(f"{name!r} has no letter or digit to make a slug from; give another")


def _slug_from_name(name: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", name.lower()))
    if not slug:
        raise WizardError(
            f"name {name!r} has no letter or digit to make a slug from; give one with --slug"
        )
    return slug


def _check_github_url(url: str) -> None:
    try:
        github.repo_slug(url)
    except github.GitHubError:
        raise ValueError(
            f"{url!r} is not a github.com repository: give https://github.com/<owner>/<name> "
            "or git@github.com:<owner>/<name>.git"
        ) from None


def _repo_url(repo: str | None, ask: Ask) -> str:
    if repo is None:
        return answer(
            ask,
            "GitHub repository URL, https://github.com/<owner>/<name> or "
            "git@github.com:<owner>/<name>.git",
            None,
            _check_github_url,
        )
    try:
        _check_github_url(repo)
    except ValueError as e:
        raise WizardError(f"--repo {e}") from None
    return repo.strip()


def _clone(url: str, dest: Path, say: Say, *, config: Sequence[str] = ()) -> Path:
    """Clone `url` into `dest`, or reuse the clone already there. `config` holds
    `key=value` settings for the clone's own git config (`github_access.Login.clone_config`):
    `-c` on a fresh clone, so they apply from the first fetch; `git config` on a reused one."""
    wanted = github.repo_slug(url)
    if dest.exists():
        # The URL as configured: `remote get-url` would apply `url.*.insteadOf` rewrites.
        origin = _git_read(dest, "config", "--get", "remote.origin.url")
        try:
            found = github.repo_slug(origin) if origin is not None else None
        except github.GitHubError:
            found = None
        if found != wanted:
            raise WizardError(
                f"clone: {dest} exists and is not a clone of {wanted}"
                f"{f' (its origin is {origin})' if origin else ''}\n"
                "fix: move it away, or record that checkout with --repo-path"
            )
        say(f"clone: {dest} (already there, origin {wanted})")
        for setting in config:
            key, value = setting.split("=", 1)
            subprocess.run(
                ["git", "config", key, value], cwd=dest, capture_output=True, text=True,
                check=False,
            )  # fmt: skip
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"clone: {url} -> {dest}")
    flags = [flag for setting in config for flag in ("-c", setting)]
    result = subprocess.run(
        ["git", "clone", *flags, url, str(dest)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise WizardError(
            f"clone: git clone {url} failed:\n{result.stderr.strip()}\n"
            "fix: check the URL and that this machine can reach the repository (gh auth "
            "status), then run this command again"
        )
    return dest


# The branch the first commit of an empty repository goes to, as GitHub names a new
# repository's default branch: the branch question's default there.
EMPTY_REPOSITORY_BRANCH = "main"

NAME_QUESTION = "Name the agents commit with"
EMAIL_QUESTION = "Email the agents commit with"
BRANCH_QUESTION = "Branch the agents' pull requests go into"


def _enter_on_ended_stdin(ask: Ask) -> Ask:
    """`ask` for one question of step 4 or 5: when stdin has ended, the first ask answers
    as Enter does, taking the default, so a caller with no terminal (`project new
    </dev/null`) gets the defaults; `setup/install.sh` runs `project new </dev/tty`, so
    the human answers there. A second ask, after the default was refused, ends the wizard
    as any question stdin cannot answer does."""
    ended = False

    def ask_once(question: str, default: str | None, *, choice: Choice | None = None) -> str:
        nonlocal ended
        try:
            return ask(question, default, choice=choice)
        except EOFError:
            if ended:
                raise
            ended = True
            return ""

    return ask_once


def _ask_identity(
    clone: Path, ask: Ask, say: Say, *, name: str | None, email: str | None
) -> tuple[str, str]:
    """Step 4's first two questions, each skipped by its flag or saved answer; the
    defaults come from `gh api user`, read only when a question is asked. Returns the
    name and email set on the clone."""
    if name is None or email is None:
        account_name, account_email = github_access.account_identity()
        if name is None:
            name = answer(_enter_on_ended_stdin(ask), NAME_QUESTION, account_name)
        if email is None:
            email = answer(_enter_on_ended_stdin(ask), EMAIL_QUESTION, account_email)
    github_access.set_identity(clone, say, name=name, email=email)
    return name, email


def _ask_branch(clone: Path, ask: Ask, *, flag: str | None, empty: bool) -> str:
    """Step 4's third question: the branch the agents' pull requests go into. On an empty
    repository no branch exists yet, and the chosen one is made by `_first_commit`;
    otherwise it must exist on origin."""
    if empty:
        return flag or answer(_enter_on_ended_stdin(ask), BRANCH_QUESTION, EMPTY_REPOSITORY_BRANCH)
    if flag is not None:
        if not _on_origin(clone, flag):
            raise WizardError(
                f"branch: --default-branch {flag} does not exist on origin\nfix: give a "
                "branch the repository has, or push that branch to GitHub first, then run "
                "this command again"
            )
        return flag

    def exists(branch: str) -> None:
        if not _on_origin(clone, branch):
            raise ValueError(
                f"{branch} does not exist on origin; give a branch the repository has"
            )

    return answer(_enter_on_ended_stdin(ask), BRANCH_QUESTION, _default_branch(clone), exists)


def _on_origin(clone: Path, branch: str) -> bool:
    """Whether origin has the branch, as origin itself answers (`git ls-remote`); when it
    does, fetch it, so that `origin/<branch>`, which the access check starts from, is
    current even in a clone that a stopped run left before the branch was pushed."""
    listed = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=clone, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if listed.returncode == 2:  # --exit-code: origin answered and has no such branch
        return False
    fetched = listed if listed.returncode != 0 else subprocess.run(
        ["git", "fetch", "--quiet", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
        cwd=clone, capture_output=True, text=True, check=False,
    )  # fmt: skip
    if fetched.returncode != 0:
        raise WizardError(
            f"branch: git {fetched.args[1]} origin {branch} failed:\n"
            f"{fetched.stderr.strip()}\nfix: check that this machine can reach the "
            "repository (gh auth status), then run this command again"
        )
    return True


def _is_empty(clone: Path) -> bool:
    """Whether the repository has no branch at all, as a new GitHub repository has before
    its first push: `git ls-remote --heads origin` answers and lists none."""
    return _git_read(clone, "ls-remote", "--heads", "origin") == ""


def _first_commit(clone: Path, branch: str, say: Say, *, origin_url: str, slug: str) -> None:
    """Give an empty repository its first branch: push one commit with no files to `branch`
    (GitHub makes the first branch pushed to a repository its default branch), then fetch
    it, point the clone's `origin/HEAD` at it and check the clone out on it. The commit is
    made with `git commit-tree` from the empty tree, so it holds no file."""

    def git(step: str, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=clone, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            fix = (
                github_access._push_fix(origin_url, slug)
                if step == "push"
                else f"check the clone at {clone}"
            )
            raise WizardError(
                f"empty repository: {step}: git {' '.join(args[:2])} failed:\n"
                f"{result.stderr.strip()}\nfix: {fix}, then run this command again"
            )
        return result.stdout.strip()

    tree = git("empty tree", "hash-object", "-t", "tree", "-w", "/dev/null")
    sha = git("first commit", "commit-tree", tree, "-m", "Initial commit")
    git("push", "push", "origin", f"{sha}:refs/heads/{branch}")
    git("fetch", "fetch", "origin")
    git("origin/HEAD", "remote", "set-head", "origin", branch)
    git("checkout", "switch", "-C", branch, "--track", f"origin/{branch}")
    say(
        f"empty repository: pushed a first commit with no files to {branch} "
        f"({sha[:7]}), which GitHub makes the default branch"
    )


def _default_branch(checkout: Path) -> str:
    """The repository's default branch, from `checkout`'s `origin/HEAD`: the clone of
    step 3, or the checkout `--repo-path` names."""
    ref = _git_read(checkout, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref is None:
        raise WizardError(
            f"default branch: {checkout} has no origin/HEAD, so git does not know the "
            f"repository's default branch\nfix: run `git -C {checkout} remote set-head origin "
            "--auto` (an empty repository has none: push a first commit first), then run this "
            "command again"
        )
    return ref.removeprefix("origin/")


def _git_read(cwd: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _secret_keys(secrets: Path) -> set[str]:
    """The key names in the secrets file; its values are never read into a name
    (`docs/rules.md`, item 7)."""
    if not secrets.is_file():
        return set()
    keys = set()
    for line in secrets.read_text().splitlines():
        line = line.strip().removeprefix("export ").lstrip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].strip())
    return keys


def _asks_where() -> str:
    """Where the planner's questions reach you: Telegram, or the board with Telegram off."""
    return "on Telegram" if telegram_on() else "on the board"


def _telegram_check(say: Say) -> None:
    if not telegram_on():
        say("telegram: off (notify.telegram = false); you answer on the board")
        return
    secrets = secrets_path()
    missing = []
    if "TELEGRAM_BOT_TOKEN" not in _secret_keys(secrets):
        missing.append(f"TELEGRAM_BOT_TOKEN is not in {secrets}")
    if not load_config()["notify"]["telegram_chat_id"]:
        missing.append("notify.telegram_chat_id is not set")
    if not missing:
        say("telegram: set up (TELEGRAM_BOT_TOKEN present, notify.telegram_chat_id set)")
        return
    say(f"telegram: not set up yet ({'; '.join(missing)}); autonomouse reaches you there, so:")
    for i, step in enumerate(TELEGRAM_STEPS, 1):
        say(f"  {i}. {step.format(secrets=secrets)}")
