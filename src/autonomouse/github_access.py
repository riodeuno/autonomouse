"""GitHub access for `autonomouse project new` (sys-s3): log gh in when it is not, set
the name and email the agents commit with on the clone, and prove that the login can do on
the repository what the workers do — before the project is recorded.

Three steps, each a function the wizard (`autonomouse.project_wizard`) calls in order:

1. `login`, right after the URL question and before the clone (a private repository
   cannot be cloned first): `gh auth status --hostname github.com`. When gh is logged
   in, nothing is asked. Otherwise the wizard asks how: (1) a personal access token,
   read without echo and passed to `gh auth login --with-token` on stdin, then `gh auth
   setup-git` so git over https uses gh; or (2) an SSH key path: the clone then uses
   `git@github.com:<owner>/<name>.git` with `core.sshCommand` set on the clone to that
   key, so every worktree pushes with it, and gh's API login is still asked, as a token
   or an interactive `gh auth login`.
2. after the clone, the wizard asks the name and email the agents commit with (sys-w9):
   `account_identity` gives the defaults from `gh api user` (the account's name or login;
   `<id>+<login>@users.noreply.github.com`), and `set_identity` sets the answers as the
   clone's own `user.name` and `user.email`, so every worktree made from it commits with
   them.
3. `check_access`: a small agent (`autonomouse.probe`, the charter `docs/probes/github.md`)
   works in the clone on the throwaway branch `probe/autonomouse` (`PROBE_BRANCH`, always
   that name, sys-w100) made from `origin/<the project's branch>`; a leftover of a check
   that stopped before its end is reset. It does on a pull request what the workers do
   there: it pushes an empty commit, opens a draft pull request against that branch, reads
   its CI status (any one of Actions runs, check runs and commit statuses is enough: a
   fine-grained token never reads check runs), its review comments and whether it has
   merge conflicts, cancels the repository's latest finished Actions run (GitHub answers
   409 and changes nothing), closes the pull request and deletes the branch, confirming
   before every write that it touches only its own branch and pull request, and at the
   end that the open pull requests and the branches are as they were. It says each step
   in one plain line as it goes, and its report lists each ability as proven, missing with
   its fix, or unchecked with why.

A failed login step raises `AccessError` naming the step, git's or gh's error and the
exact fix. `check_access` raises it when an ability the workers need (push, open a pull
request, read CI status, review comments and merge conflicts) is not proven, naming each
one's fix, or when the agent gave no report; the wizard then asks the human to re-check or
quit, and stops before `project.json` only on quit or when nobody can answer. A step that
only cancels or cleans up never stops setup (sys-s71, sys-w100): cancelling a run, closing
the test pull request and deleting the branch, when refused, print a warning (for the
cleanup steps, what is left behind on GitHub) and setup goes on. Nothing here
writes a credential: gh stores its own login, and the token is never in an argv, a file,
the agent's prompt or a printed line.

Every git and gh call goes through `subprocess.run`, as `autonomouse.github` does, so a
test fakes them.
"""

from __future__ import annotations

import getpass
import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autonomouse import github, probe
from autonomouse.init import Ask, Choice, Say, answer

TOKEN_NEEDS = (
    "Contents read and write, Pull requests read and write, Actions read and write and "
    "Commit statuses read on the repository; a classic token also read:org, which gh "
    "requires"
)
TOKEN_SETTINGS = "github.com > Settings > Developer settings > Personal access tokens"
SSH_KEYS_SETTINGS = "github.com > Settings > SSH and GPG keys"

# The email `account_identity` offers: the account's GitHub no-reply address.
NOREPLY = "{id}+{login}@users.noreply.github.com"

# The access check's throwaway branch: one name, so a check that stopped before its end
# leaves at most this one branch, which the next check resets (sys-w100).
PROBE_BRANCH = "probe/autonomouse"


class AccessError(Exception):
    """A step failed; the message names the step, the error and the fix, and is safe
    to print (it never holds the token)."""


@dataclass(frozen=True)
class Login:
    """How this machine reaches GitHub, as `login` found or set it up.

    `method` is `existing` (gh was logged in already; the clone URL is used as given),
    `token` (gh now holds a token and serves git over https; the clone uses the https
    form) or `ssh` (the clone uses the ssh form with `core.sshCommand` set to `ssh_key`).
    """

    method: str
    ssh_key: Path | None = None

    def clone_url(self, url: str) -> str:
        """The URL to clone: the ssh form under an SSH key, the https form under a token
        set up just now, else `url` as the human gave it."""
        slug = github.repo_slug(url)
        if self.method == "ssh":
            return f"git@github.com:{slug}.git"
        if self.method == "token":
            return f"https://github.com/{slug}"
        return url

    def clone_config(self) -> list[str]:
        """`key=value` settings for the clone's own config: `core.sshCommand` under an
        SSH key, so every worktree made from the clone pushes with it; nothing else."""
        if self.ssh_key is None:
            return []
        return [
            (
                f"core.sshCommand=ssh -i {self.ssh_key} -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=accept-new"
            )
        ]


# ------------------------------------------------------------------- login --


LOGIN_HOW = Choice("Log in with", {"1": "a personal access token", "2": "an SSH key path"})
API_LOGIN = Choice(
    "gh still needs an API login:",
    {"1": "a personal access token", "2": "run `gh auth login` interactively now"},
)


def login(
    ask: Ask, say: Say, *, read_secret: Callable[[str], str] = getpass.getpass
) -> Login:
    """Step 1 (module docstring). `read_secret` reads the token without echo; a test
    passes a stub. Raises `AccessError` when gh refuses the token or the key path is not
    a file, and `InitError` when stdin ends on a question."""
    status = _run(["gh", "auth", "status", "--hostname", "github.com"])
    if status.returncode == 0:
        say("github: gh is logged in to github.com")
        return Login(method="existing")
    say("github: gh is not logged in to github.com")
    how = answer(ask, LOGIN_HOW, "1", _one_or_two)
    if how == "1":
        _login_with_token(say, read_secret)
        _gh_or_fail(
            ["gh", "auth", "setup-git", "--hostname", "github.com"],
            step="gh auth setup-git",
            fix="run `gh auth setup-git` by hand, then run this command again",
        )
        say("github: gh logged in with the token; git over https now uses gh")
        return Login(method="token")
    key = Path(
        answer(ask, "Path of the SSH private key that can push to the repository")
    ).expanduser()
    if not key.is_file():
        raise AccessError(
            f"ssh key: {key} is not a file\nfix: give the path of the private key (the one "
            f"whose .pub is on {SSH_KEYS_SETTINGS}), then run this command again"
        )
    say(f"github: the clone will push over ssh with {key}")
    api = answer(ask, API_LOGIN, "1", _one_or_two)
    if api == "1":
        _login_with_token(say, read_secret)
    else:
        interactive = subprocess.run(
            ["gh", "auth", "login", "--hostname", "github.com"], check=False
        )
        if interactive.returncode != 0:
            raise AccessError(
                f"gh auth login: exited {interactive.returncode}\nfix: finish `gh auth "
                "login` by hand, then run this command again"
            )
    say("github: gh logged in")
    return Login(method="ssh", ssh_key=key)


def _one_or_two(reply: str) -> None:
    if reply not in ("1", "2"):
        raise ValueError("answer 1 or 2")


def _login_with_token(say: Say, read_secret: Callable[[str], str]) -> None:
    say(f"the token needs: {TOKEN_NEEDS} ({TOKEN_SETTINGS})")
    token = read_secret("Personal access token (not shown): ").strip()
    if not token:
        raise AccessError("gh auth login: no token given\nfix: run this command again")
    result = subprocess.run(
        ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
        input=token,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AccessError(
            f"gh auth login --with-token failed:\n{result.stderr.strip()}\n"
            f"fix: make a token with {TOKEN_NEEDS} ({TOKEN_SETTINGS}), then run this "
            "command again"
        )


# ---------------------------------------------------------------- identity --


def account_identity() -> tuple[str, str]:
    """The name and email the GitHub account gh is logged in as commits with: its public
    name, else its login, and its no-reply address (`NOREPLY`). The wizard offers them as
    the defaults of its two identity questions."""
    out = _gh_or_fail(
        ["gh", "api", "user"],
        step="gh api user",
        fix="check `gh auth status`, then run this command again",
    )
    try:
        user = json.loads(out)
        return user.get("name") or user["login"], NOREPLY.format(id=user["id"], login=user["login"])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise AccessError(
            f"gh api user: returned no usable account: {out!r}\nfix: check `gh auth "
            "status`, then run this command again"
        ) from e


def set_identity(checkout: Path, say: Say, *, name: str | None, email: str | None) -> None:
    """Step 2 (module docstring): set `name` and `email`, each when given, as `user.name`
    and `user.email` in `checkout`'s own git config, which its worktrees share."""
    for key, value in (("user.name", name), ("user.email", email)):
        if value is None:
            continue
        result = _run(["git", "config", key, value], cwd=checkout)
        if result.returncode != 0:
            raise AccessError(
                f"identity: git config {key} failed in {checkout}:\n{result.stderr.strip()}"
            )
    given = " and ".join(
        f"{key} {value!r}" for key, value in (("user.name", name), ("user.email", email)) if value
    )
    say(f"identity: the agents commit with {given}, set on {checkout}")


# ------------------------------------------------------------------- check --


def check_access(
    clone: Path,
    *,
    slug: str,
    branch: str,
    origin_url: str,
    say: Say,
    claude: str = "claude",
) -> None:
    """Step 3 (module docstring): run the GitHub probe in `clone`, say its step lines, warn
    on each optional ability it did not prove and raise `AccessError` on the required
    ones. `origin_url` is the URL the clone was made from; its form tells the agent whether
    git pushes with gh's token or an SSH key, which picks the fix for a refused push. The
    error names what to fix and no next step: the wizard asks the human to re-check or
    quit, and says "run this command again" only when nobody can answer."""
    facts = {
        "repository": slug,
        "branch": branch,
        "probe branch": PROBE_BRANCH,
        "push over": "ssh" if origin_url.startswith("git@") else "https",
    }
    say(f"access: a small agent checks what the agents can do on {slug}; each step follows")
    try:
        report = probe.run_probe(
            "github", cwd=clone, facts=facts, say=lambda line: say(f"access: {line}"),
            claude=claude,
        )  # fmt: skip
    except probe.ProbeError as e:
        raise AccessError(
            f"access check: {e}\nfix: check that `claude -p` runs on this machine (`claude "
            f"auth status`); a branch {PROBE_BRANCH} this check left on {slug} is reset by "
            "the next one"
        ) from e
    for p in report.warnings:
        if p.status == "missing":
            say(f"access: warning: {p.name} is missing; setup goes on without it\nfix: {p.fix}")
        else:
            say(f"access: {p.name} is unchecked: {p.fix}")
    if report.blocking:
        raise AccessError(
            f"access: the agents cannot work on {slug} until these are fixed:\n"
            + "\n".join(f"{p.name} is {p.status}\nfix: {p.fix}" for p in report.blocking)
        )
    say(
        f"access: the agents can push, open pull requests, and read CI status, review "
        f"comments and merge conflicts on {slug}"
    )


def _push_fix(origin_url: str, slug: str) -> str:
    if origin_url.startswith("git@"):
        return (
            f"add the key's .pub to your GitHub account ({SSH_KEYS_SETTINGS}) with write "
            f"access to {slug}"
        )
    return f"give the token Contents read and write on {slug} ({TOKEN_SETTINGS})"


# ------------------------------------------------------------------- calls --


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False
    )


def _gh_or_fail(args: list[str], *, step: str, fix: str, cwd: Path | None = None) -> str:
    result = _run(args, cwd=cwd)
    if result.returncode != 0:
        raise AccessError(
            f"{step}: {' '.join(args[:3])} failed:\n{result.stderr.strip()}\nfix: {fix}"
            + ("" if fix.endswith("again") else ", then run this command again")
        )
    return result.stdout
