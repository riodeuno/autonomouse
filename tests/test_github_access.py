"""Tests for `autonomouse.github_access` (sys-s3): `subprocess.run` is faked to record every
git and gh call and answer from a queue, as `_FakeGh` in tests/test_github.py does, so no
test here reaches GitHub. The access check (sys-w26) runs a stub `claude`, `FAKE_CLAUDE`
below. The one live run of the check, against riodeuno/autonomouse, is the worker's and
goes in the card's verification note."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from autonomouse import github_access, project_wizard
from autonomouse.github_access import AccessError, Login

HTTPS = "https://github.com/test/widgets"
SSH = "git@github.com:test/widgets.git"
KEY_OPTIONS = "-o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
USER = json.dumps({"login": "tess", "id": 7, "name": "Tess Tester"})
NOT_LOGGED_IN = (1, "", "You are not logged into any GitHub hosts. To log in, run: gh auth login")
OK = (0, "", "")


@dataclass
class _Call:
    args: list[str]
    cwd: str | None
    input: str | None

    @property
    def head(self) -> list[str]:
        return self.args[:3]


class _FakeRun:
    """Replaces `subprocess.run`: `responses` is a queue of `(returncode, stdout, stderr)`
    consumed in call order; every call is recorded in `.calls` with its cwd and stdin."""

    def __init__(self, responses: list[tuple[int, str, str]]):
        self.responses = list(responses)
        self.calls: list[_Call] = []

    def __call__(self, args, *, cwd=None, input=None, capture_output=False, text=False,
                 check=False):  # fmt: skip
        self.calls.append(_Call(args=list(args), cwd=cwd, input=input))
        if not self.responses:
            raise AssertionError(f"no fake response queued for {args}")
        returncode, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


class _Ask:
    """Scripted answers; `AssertionError` when asked more than scripted."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.questions: list[str] = []

    def __call__(self, question: str, default: str | None, *, choice=None) -> str:
        self.questions.append(question)
        if not self.answers:
            raise AssertionError(f"unexpected question: {question!r}")
        return self.answers.pop(0)


@pytest.fixture
def fake(monkeypatch):
    def install(responses):
        run = _FakeRun(responses)
        monkeypatch.setattr(github_access.subprocess, "run", run)
        return run

    return install


# ------------------------------------------------------------------- login --


def test_a_logged_in_gh_asks_nothing(fake) -> None:
    run = fake([OK])
    said: list[str] = []

    login = github_access.login(_Ask(), said.append)

    assert [c.args for c in run.calls] == [["gh", "auth", "status", "--hostname", "github.com"]]
    assert login == Login(method="existing")
    assert login.clone_url(SSH) == SSH
    assert login.clone_config() == []
    assert said == ["github: gh is logged in to github.com"]


def test_the_token_reaches_gh_on_stdin_and_nowhere_else(fake, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    run = fake([NOT_LOGGED_IN, OK, OK])
    token = "github_pat_11AAAAAAA0secretsecretsecret"
    said: list[str] = []
    ask = _Ask("1")

    login = github_access.login(ask, said.append, read_secret=lambda prompt: token + "\n")

    assert login == Login(method="token")
    assert login.clone_url(SSH) == HTTPS  # git over https, served by gh
    assert [c.args for c in run.calls] == [
        ["gh", "auth", "status", "--hostname", "github.com"],
        ["gh", "auth", "login", "--hostname", "github.com", "--with-token"],
        ["gh", "auth", "setup-git", "--hostname", "github.com"],
    ]
    assert run.calls[1].input == token
    assert all(token not in arg for c in run.calls for arg in c.args)
    assert all(token not in line for line in said)
    assert all(token not in q for q in ask.questions)
    assert all(token not in f.read_text(errors="replace") for f in tmp_path.rglob("*") if f.is_file())
    # The question names what the token needs on the repository.
    needs = "\n".join(said)
    for permission in ("Contents read and write", "Pull requests read and write", "Actions read and write",
                       "Commit statuses read", "read:org"):  # fmt: skip
        assert permission in needs


def test_a_refused_token_names_the_fix_and_keeps_the_token_out_of_the_error(fake) -> None:
    run = fake([NOT_LOGGED_IN, (1, "", "error validating token: HTTP 401")])

    with pytest.raises(AccessError) as e:
        github_access.login(_Ask("1"), lambda _: None, read_secret=lambda _: "ghp_x1")

    assert "HTTP 401" in str(e.value)
    assert "Contents read and write" in str(e.value)
    assert "ghp_x1" not in str(e.value)
    assert run.calls[-1].input == "ghp_x1"


def test_the_ssh_answer_sets_core_sshcommand_and_the_ssh_origin(fake, tmp_path) -> None:
    key = tmp_path / "id_ed25519"
    key.write_text("not a real key")
    run = fake([NOT_LOGGED_IN, OK, OK])  # auth status; the token login for gh's API; clone
    said: list[str] = []

    login = github_access.login(
        _Ask("2", str(key), "1"), said.append, read_secret=lambda _: "ghp_api"
    )

    assert login == Login(method="ssh", ssh_key=key)
    assert login.clone_url(HTTPS) == SSH
    assert login.clone_config() == [f"core.sshCommand=ssh -i {key} {KEY_OPTIONS}"]
    assert run.calls[1].args[:3] == ["gh", "auth", "login"] and run.calls[1].input == "ghp_api"
    assert all("setup-git" not in c.args for c in run.calls)  # git does not go through gh

    dest = tmp_path / "repos" / "widgets"
    project_wizard._clone(login.clone_url(HTTPS), dest, said.append, config=login.clone_config())

    assert run.calls[-1].args == [
        "git", "clone", "-c", f"core.sshCommand=ssh -i {key} {KEY_OPTIONS}", SSH, str(dest),
    ]  # fmt: skip


def test_a_reused_clone_gets_core_sshcommand_by_git_config(fake, tmp_path) -> None:
    dest = tmp_path / "widgets"
    dest.mkdir()
    run = fake([(0, SSH + "\n", ""), OK])
    login = Login(method="ssh", ssh_key=Path("/keys/k"))

    project_wizard._clone(SSH, dest, lambda _: None, config=login.clone_config())

    assert run.calls[-1].args == [
        "git", "config", "core.sshCommand", f"ssh -i /keys/k {KEY_OPTIONS}",
    ]  # fmt: skip
    assert run.calls[-1].cwd == dest


def test_a_key_path_that_is_not_a_file_stops_with_the_fix(fake, tmp_path) -> None:
    fake([NOT_LOGGED_IN])

    with pytest.raises(AccessError, match="is not a file"):
        github_access.login(_Ask("2", str(tmp_path / "missing")), lambda _: None)


# ---------------------------------------------------------------- identity --


def test_the_account_identity_is_its_name_and_its_noreply_address(fake) -> None:
    run = fake([(0, USER, "")])

    assert github_access.account_identity() == ("Tess Tester", "7+tess@users.noreply.github.com")
    assert [c.args for c in run.calls] == [["gh", "api", "user"]]


def test_an_account_without_a_name_offers_its_login(fake) -> None:
    fake([(0, json.dumps({"login": "tess", "id": 7, "name": None}), "")])

    assert github_access.account_identity()[0] == "tess"


def test_set_identity_sets_both_on_the_clone_only(fake, tmp_path) -> None:
    """sys-w9: `git config` without --global, run in the clone, writes the clone's own
    config; nothing asks whether git already resolves an author."""
    run = fake([OK, OK])
    said: list[str] = []

    github_access.set_identity(tmp_path, said.append, name="Ada", email="ada@example.com")

    assert [c.args for c in run.calls] == [
        ["git", "config", "user.name", "Ada"],
        ["git", "config", "user.email", "ada@example.com"],
    ]
    assert {c.cwd for c in run.calls} == {str(tmp_path)}
    assert said == [
        (
            "identity: the agents commit with user.name 'Ada' and user.email "
            f"'ada@example.com', set on {tmp_path}"
        )
    ]


# ------------------------------------------------------------------- check --

# The access check (sys-w26) is a small agent: `check_access` runs the probe charter
# docs/probes/github.md through `autonomouse.probe`. The stub claude below records its argv
# and working folder in $FAKE_DIR/call.json and prints $FAKE_DIR/events, one stream-json
# event per line, as `claude -p --output-format stream-json` does.
FAKE_CLAUDE = f"""#!{sys.executable}
import json, os, sys
d = os.environ["FAKE_DIR"]
calls = os.path.join(d, "calls")
os.makedirs(calls, exist_ok=True)
with open(os.path.join(calls, f"{{len(os.listdir(calls)) + 1}}.json"), "w") as f:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd()}}, f)
print(open(os.path.join(d, "events")).read(), end="", flush=True)
"""

_STEPS = (
    (
        "I am creating a throwaway pull request to check the agents can open pull requests; "
        "it will be removed"
    ),
    "Checking that CI status can be read",
    "Checking that review comments can be read",
    "Checking whether GitHub says the pull request has merge conflicts",
    "Closing the pull request",
    "Removed the branch; nothing changed on trunk",
)
_CONTENTS_FIX = (
    "give the token Contents read and write on test/widgets (github.com > Settings > "
    "Developer settings > Personal access tokens)"
)
_ACTIONS_WRITE_FIX = (
    "give the gh login Actions read and write on test/widgets (github.com > Settings > "
    "Developer settings > Personal access tokens)"
)
_ALL_OK_LINE = (
    "access: the agents can push, open pull requests, and read CI status, review comments "
    "and merge conflicts on test/widgets"
)


def _perm(name: str, required: bool, status: str = "ok", fix: str = "") -> dict:
    return {"name": name, "required": required, "status": status, "fix": fix}


def _all_ok(**changed: dict) -> list[dict]:
    """The eight abilities of docs/probes/github.md, each ok unless `changed` names it."""
    eight = [
        _perm("push", True), _perm("pull_requests", True), _perm("ci_status", True),
        _perm("review_comments", True), _perm("merge_conflicts", True),
        _perm("cancel_run", False), _perm("close_pull_request", False),
        _perm("delete_branch", False),
    ]  # fmt: skip
    return [changed.get(p["name"], p) for p in eight]


def _events(permissions: list[dict], steps=_STEPS) -> str:
    report = json.dumps({"permissions": permissions})
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        *(
            json.dumps(
                {"type": "assistant",
                 "message": {"content": [{"type": "text", "text": f"STEP: {step}"}]}}
            )
            for step in steps
        ),
        json.dumps({"type": "result", "is_error": False, "result": report}),
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture
def stub(tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    fake.mkdir()
    claude = fake / "claude"
    claude.write_text(FAKE_CLAUDE)
    claude.chmod(0o755)
    monkeypatch.setenv("FAKE_DIR", str(fake))
    clone = tmp_path / "clone"
    clone.mkdir()

    class Stub:
        def __init__(self):
            self.clone, self.said = clone, []

        def check(self, permissions: list[dict], *, origin_url: str = HTTPS) -> None:
            (fake / "events").write_text(_events(permissions))
            github_access.check_access(
                clone, slug="test/widgets", branch="trunk", origin_url=origin_url,
                say=self.said.append, claude=str(claude),
            )  # fmt: skip

        def calls(self) -> list[dict]:
            paths = sorted((fake / "calls").glob("*.json"), key=lambda p: int(p.stem))
            return [json.loads(p.read_text()) for p in paths]

    return Stub()


def _prompt(call: dict) -> str:
    return call["argv"][call["argv"].index("-p") + 1]


def test_the_check_runs_the_github_probe_charter_in_the_clone_with_the_facts(stub) -> None:
    stub.check(_all_ok())

    [call] = stub.calls()
    prompt = _prompt(call)
    charter = (Path(__file__).resolve().parent.parent / "docs" / "probes" / "github.md")
    assert charter.read_text().strip() in prompt
    assert Path(call["cwd"]) == stub.clone
    assert "- repository: test/widgets" in prompt
    assert "- branch: trunk" in prompt
    assert "- push over: https" in prompt
    assert re.findall(r"^- probe branch: (\S+)$", prompt, re.MULTILINE) == ["probe/autonomouse"]
    argv = call["argv"]
    assert (argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]) == ("sonnet", "high")


def test_each_step_line_of_the_agent_is_said_in_order_and_all_ok_says_so(stub) -> None:
    stub.check(_all_ok())

    assert stub.said == [
        "access: a small agent checks what the agents can do on test/widgets; each step follows",
        *(f"access: {step}" for step in _STEPS),
        _ALL_OK_LINE,
    ]


def test_an_ssh_clone_tells_the_agent_that_git_pushes_over_ssh(stub) -> None:
    stub.check(_all_ok(), origin_url=SSH)

    assert "- push over: ssh" in _prompt(stub.calls()[0])


def test_every_check_uses_the_one_probe_branch_probe_autonomouse(stub) -> None:
    """sys-w100, the owner's item 4 of 2026-09-30: the check always uses the branch
    probe/autonomouse, so a check that stopped leaves at most that branch, which the next
    check resets. Fails on main, whose branch was autonomouse-access-<timestamp>-<random>."""
    stub.check(_all_ok())
    stub.check(_all_ok())

    branches = [
        re.search(r"^- probe branch: (\S+)$", _prompt(c), re.MULTILINE).group(1)
        for c in stub.calls()
    ]
    assert branches == [github_access.PROBE_BRANCH] * 2 == ["probe/autonomouse"] * 2


def test_a_missing_required_permission_stops_with_its_fix(stub) -> None:
    push = _perm("push", True, "missing", f"{_CONTENTS_FIX}; git said: permission denied")

    with pytest.raises(AccessError) as e:
        stub.check(_all_ok(push=push))

    message = str(e.value)
    assert message.startswith("access: the agents cannot work on test/widgets until these are fixed:\n")
    assert message.endswith(f"push is missing\nfix: {_CONTENTS_FIX}; git said: permission denied")
    # The next step is the wizard's to say: re-check or quit, or run again with no terminal.
    assert "run this command again" not in message
    assert "pull_requests" not in message
    assert not any("the agents can push" in line for line in stub.said)


def test_ci_status_missing_stops_with_its_fix(stub) -> None:
    """The agents cannot tell whether their pull request's CI passed: setup stops."""
    fix = "give the gh login Actions read on test/widgets (github.com > Settings); gh said: HTTP 403"
    with pytest.raises(AccessError) as e:
        stub.check(_all_ok(ci_status=_perm("ci_status", True, "missing", fix)))

    assert f"ci_status is missing\nfix: {fix}" in str(e.value)


def test_ci_status_read_through_actions_runs_with_check_runs_denied_is_ok_with_no_warning(
    stub,
) -> None:
    """The owner's answer of 2026-09-29 (sys-w26 d1): the check proves the workers can do
    their jobs. A fine-grained token is refused check runs and reads Actions runs; the
    charter has the agent report `ci_status` ok and say nothing of the refused way, and
    setup prints no warning."""
    charter = (Path(__file__).resolve().parent.parent / "docs" / "probes" / "github.md").read_text()
    assert "Any one of them answering proves `ci_status`." in charter
    assert "A way GitHub refuses while another answers\n   is not reported at all" in charter

    stub.check(_all_ok())

    assert not any("warning" in line or "check run" in line for line in stub.said)
    assert stub.said[-1] == _ALL_OK_LINE


def test_a_missing_cancel_warns_with_its_fix_and_goes_on(stub) -> None:
    fix = f"{_ACTIONS_WRITE_FIX}; gh said: HTTP 403"
    stub.check(_all_ok(cancel_run=_perm("cancel_run", False, "missing", fix)))

    assert f"access: warning: cancel_run is missing; setup goes on without it\nfix: {fix}" in stub.said
    assert stub.said[-1] == _ALL_OK_LINE


def test_an_unchecked_cancel_is_said_and_goes_on(stub) -> None:
    why = "the repository has no finished Actions run to try a cancel on"
    stub.check(_all_ok(cancel_run=_perm("cancel_run", False, "unchecked", why)))

    assert f"access: cancel_run is unchecked: {why}" in stub.said


def test_a_probe_that_gives_no_report_stops_with_the_branch_to_look_for(stub) -> None:
    (stub.clone.parent / "fake" / "events").write_text(
        json.dumps({"type": "result", "is_error": False, "result": "I could not finish."}) + "\n"
    )

    with pytest.raises(AccessError) as e:
        github_access.check_access(
            stub.clone, slug="test/widgets", branch="trunk", origin_url=HTTPS,
            say=stub.said.append, claude=str(stub.clone.parent / "fake" / "claude"),
        )  # fmt: skip

    assert "github probe ended without a report" in str(e.value)
    assert "a branch probe/autonomouse this check left on test/widgets is reset by the next one" in str(e.value)


def test_the_charter_names_the_eight_abilities_and_which_are_required() -> None:
    """sys-w100, the owner's item 8 of 2026-09-30: setup blocks only on what the workers
    need. Closing the test pull request and deleting the branch only clean up, so each is
    its own ability, not required, whose fix names what is left behind; opening a pull
    request no longer covers closing it."""
    charter = (Path(__file__).resolve().parent.parent / "docs" / "probes" / "github.md").read_text()
    rows = re.findall(r"^\| `(\w+)` \| (true|false) \| (.*) \|$", charter, re.MULTILINE)
    assert [(name, required) for name, required, _ in rows] == [
        ("push", "true"), ("pull_requests", "true"), ("ci_status", "true"),
        ("review_comments", "true"), ("merge_conflicts", "true"),
        ("cancel_run", "false"), ("close_pull_request", "false"), ("delete_branch", "false"),
    ]  # fmt: skip
    fixes = {name: fix for name, _, fix in rows}
    assert "cannot open a pull request |" in f"{fixes['pull_requests']} |"
    assert "close" not in fixes["pull_requests"]
    assert fixes["close_pull_request"].startswith("left behind: the draft pull request")
    assert "gh pr close <number> --repo <repository>" in fixes["close_pull_request"]
    assert fixes["delete_branch"].startswith("left behind: the branch `probe/autonomouse`")
    assert "git push origin --delete probe/autonomouse" in fixes["delete_branch"]
    assert "never `pull_requests` or `push`" in charter


def test_the_charter_uses_probe_autonomouse_and_resets_a_leftover() -> None:
    """sys-w100, the owner's item 4 of 2026-09-30: one branch name, and a leftover of a
    check that stopped is reset (a forced push) and its open pull request closed first."""
    charter = (Path(__file__).resolve().parent.parent / "docs" / "probes" / "github.md").read_text()
    assert "autonomouse-access-" not in charter
    assert "always `probe/autonomouse`" in charter
    assert "`git branch -f <probe branch> <sha>`" in charter
    assert "confirm that `<probe branch>` is exactly `probe/autonomouse`" in charter
    assert "push --force origin <probe branch>`" in charter
    assert "an earlier check left it: close it first" in charter


def test_the_charter_keeps_every_scratch_file_in_one_mktemp_folder_and_removes_it() -> None:
    """sys-w50: the agent that follows the charter picked fixed paths under the system's
    temporary folder for its before-and-after lists (sys-w26's live run), so two setups at
    once overwrote each other's lists and every setup left the files behind."""
    charter = (Path(__file__).resolve().parent.parent / "docs" / "probes" / "github.md").read_text()

    made = charter.index("mktemp -d")
    removed = charter.index("rm -rf <scratch>")
    assert made < removed
    for name in ("prs_before", "heads_before", "prs_after", "heads_after"):
        assert re.search(rf"<scratch>/{name}\.\w+", charter), name
    assert "always run steps 9 to 12" in charter
    # No absolute or home-relative path: the folder is the only place a file may go.
    assert re.findall(r"(?<![\w<>.:/$~-])[/~][\w/.-]*", charter) == []
    # Every redirect in the steps writes into the scratch folder.
    assert re.findall(r"[a-z] > (?!<scratch>/)\S", charter.split("## Permissions")[0]) == []
