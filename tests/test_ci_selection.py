"""The CI selector narrows known release changes and falls back for other code."""

import runpy
from pathlib import Path

select = runpy.run_path(str(Path(__file__).resolve().parents[1] / ".github/select_tests.py"))["selected_tests"]


def test_release_changes_select_all_affected_test_modules():
    assert select({"src/autonomouse/release.py", ".github/workflows/ci.yml"}) == [
        "tests/test_release.py", "tests/test_ci_workflow.py", "tests/test_ci_selection.py",
    ]


def test_other_code_keeps_the_full_suite():
    assert select({"src/autonomouse/controller.py", "src/autonomouse/release.py"}) == []


def test_unknown_diff_keeps_the_full_suite():
    assert select(set()) == []
