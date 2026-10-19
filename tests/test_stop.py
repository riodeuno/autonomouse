import sys
import threading

import pytest

from autonomouse import cli, controller, planner, timer
from autonomouse.board.commands import project_show
from autonomouse.config import DEFAULTS, pause_path
from test_project_pause import wait_for
from test_run_controller import env  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_control_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTONOMOUSE_STATE", str(tmp_path / "state"))


def fake_cron(env, monkeypatch, *, fail=False):  # noqa: F811
    state = env.tmp / "crontab.txt"
    unrelated = "7 1 * * * echo keep-this-job\n"
    state.write_text(unrelated + "0 */4 * * * autonomouse run # autonomouse wake\n" +
                     "0 6 * * * autonomouse review daily # autonomouse review\n" +
                     "*/5 * * * * autonomouse plan step # autonomouse plan\n")
    executable = env.tmp / "crontab"
    executable.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                          f"p = Path({str(state)!r})\n"
                          "if sys.argv[1] == '-l': print(p.read_text(), end='')\n"
                          "else: p.write_text(sys.stdin.read())\n")
    executable.chmod(0o755)
    original = timer.remove

    def remove():
        if fail:
            raise timer.TimerError("crontab refused the write")
        return original(crontab=str(executable))

    monkeypatch.setattr(timer, "remove", remove)
    return state, unrelated


@pytest.mark.parametrize("cron_fails", [False, True])
def test_stop_ends_agents_removes_only_own_cron_and_blocks_new_work(env, monkeypatch, cron_fails):  # noqa: F811
    monkeypatch.setenv("AUTONOMOUSE_HOME", str(env.root))
    monkeypatch.setenv("FAKE_WAIT", str(env.fake_dir / "never"))
    child = env.fake_dir / "worker.pid"
    monkeypatch.setenv("FAKE_ON_NEW", f"sleep 300 & echo $! > '{child}'")
    state, unrelated = fake_cron(env, monkeypatch, fail=cron_fails)
    codes = []
    runner = threading.Thread(target=lambda: codes.append(env.wake()))
    runner.start()
    try:
        wait_for(lambda: child.exists() and child.read_text().strip())
        monkeypatch.setattr(sys, "argv", ["autonomouse", "stop"])
        if cron_fails:
            with pytest.raises(SystemExit) as error:
                cli.main()
            assert error.value.code == 1
        else:
            cli.main()
            assert state.read_text() == unrelated
            cli.main()  # idempotent with no runs and no managed cron entries
        runner.join(timeout=15)
        assert not runner.is_alive()
        assert codes == [0]
        assert project_show(env.root, "high").claim.run_id is None
        assert pause_path().exists()
        assert env.wake(pause=pause_path()) == controller.EXIT_NOT_STARTED
        assert planner.step(env.root, config=DEFAULTS, claude="must-not-start",
                            home=None, secrets=env.tmp / "absent", state_dir=env.tmp / "plans") == 0
        assert timer.rearm() is None
    finally:
        (env.fake_dir / "never").touch()
        runner.join(timeout=15)
