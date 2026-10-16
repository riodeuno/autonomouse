"""sys-w101: `autonomouse.gum`, how `project new` asks on a terminal. The pty tests in
tests/test_install_questions.py drive whole runs through a fake gum; these check what the
real gum is handed and how its answer comes back, with `subprocess.run` stubbed."""

import subprocess

import pytest

from autonomouse import gum
from autonomouse.init import Choice, InitError
from autonomouse.project_wizard import RECHECK, UNFINISHED


class _Gum:
    """`subprocess.run` for gum: records each argv and answers with (exit code, stdout)."""

    def __init__(self, *answers: tuple[int, str]):
        self.answers = list(answers)
        self.argv: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.argv.append(argv)
        code, out = self.answers.pop(0)
        return subprocess.CompletedProcess(argv, code, stdout=out)


def test_a_long_question_is_folded_to_the_terminal_so_gum_does_not_cut_it_off(monkeypatch) -> None:
    """gum cuts a header off at the terminal's edge (seen at 80 columns: a question lost its
    last words), so the header comes folded."""
    monkeypatch.setenv("COLUMNS", "40")
    run = _Gum((0, "start a new project\n"))
    monkeypatch.setattr(gum.subprocess, "run", run)
    choice = Choice(UNFINISHED, {"1": "start a new project"})

    assert gum.ask(choice.text(), None, choice=choice) == "1"

    [argv] = run.argv
    assert argv[:3] == ["gum", "choose", "--header"]
    prompt = argv[3]
    assert all(len(line) <= 38 for line in prompt.splitlines())
    assert " ".join(prompt.splitlines()) == UNFINISHED


def test_a_choice_comes_back_as_its_answer_and_a_text_answer_has_no_length_cap(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    run = _Gum((0, "quit\n"), (0, "x" * 450 + "\n"))
    monkeypatch.setattr(gum.subprocess, "run", run)

    assert gum.ask(RECHECK.text(), None, choice=RECHECK) == "q"
    assert gum.ask("Project goal", None) == "x" * 450

    assert run.argv[0] == ["gum", "choose", "--header", RECHECK.header, "re-check access", "quit"]
    assert run.argv[1][run.argv[1].index("--char-limit") + 1] == "0"


@pytest.mark.parametrize(("code", "why"), [(1, "esc"), (130, "ctrl-c")])
def test_esc_or_ctrl_c_stops_with_the_question_and_the_fix(monkeypatch, code, why) -> None:
    monkeypatch.setattr(gum.subprocess, "run", _Gum((code, "")))

    with pytest.raises(InitError) as e:
        gum.ask("Project name, as the board shows it", None)

    assert f"stopped at 'Project name, as the board shows it': gum exited {code} ({why}" in str(e.value)
    assert "fix: run this command again" in str(e.value)
