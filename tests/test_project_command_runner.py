"""Exercise real shell processes without requiring a visible desktop."""

from __future__ import annotations

import json
import os
import shlex
import signal
import sys
import threading
import time
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from aura.gui.project_command_runner import MAX_OUTPUT_CHARS, ProjectCommandRunner
from aura.project_commands import ProjectCommand


@pytest.fixture(scope="session")
def core_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runner(core_app):
    instance = ProjectCommandRunner()
    yield instance
    instance.shutdown()
    assert not instance.active
    assert not any(t.name == "aura-project-command" for t in threading.enumerate())


def spin_until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.005)
    assert predicate()


def python_command(tmp_path, code, **kwargs):
    script = tmp_path / "command.py"
    script.write_text(code, encoding="utf-8")
    if os.name == "nt":
        executable = sys.executable.replace("'", "''")
        script_arg = str(script).replace("'", "''")
        command = f"& '{executable}' '{script_arg}'"
    else:
        command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
    return ProjectCommand("Launch app", command, **kwargs)


def test_streams_output_and_preserves_utf8_before_process_exits(runner, tmp_path):
    command = python_command(tmp_path, """
import os, time
os.write(1, b'prefix ' + bytes([0xf0, 0x9f]))
time.sleep(0.05)
os.write(1, bytes([0x8c, 0xb8]) + b'\\n')
print('stderr', file=__import__('sys').stderr)
print('unbuffered stdout')
time.sleep(0.8)
""")
    states, chunks, completed = [], [], []
    runner.state_changed.connect(states.append)
    runner.output.connect(lambda run_id, text: chunks.append((run_id, text)))
    runner.finished.connect(lambda run_id, code: completed.append((run_id, code)))
    assert runner.start(tmp_path, command)
    spin_until(lambda: "unbuffered stdout" in runner.output_tail)
    assert runner.active
    assert "prefix 🌸" in runner.output_tail
    assert "stderr" in runner.output_tail
    spin_until(lambda: not runner.active)
    assert states == ["starting", "running", "succeeded"]
    assert completed == [(runner.run_id, 0)]
    assert {run_id for run_id, _ in chunks} == {runner.run_id}


def test_exit_failure_and_launch_failure_are_distinct(runner, tmp_path, monkeypatch):
    command = python_command(tmp_path, "raise SystemExit(7)")
    assert runner.start(tmp_path, command)
    spin_until(lambda: not runner.active)
    assert runner.state == "failed"
    assert runner.exit_code == 7

    def unavailable(_):
        raise FileNotFoundError("shell executable is unavailable")

    monkeypatch.setattr("aura.gui.project_command_runner._shell_args", unavailable)
    assert runner.start(tmp_path, command)
    spin_until(lambda: not runner.active)
    assert runner.state == "failed"
    assert runner.exit_code is None
    assert "shell executable is unavailable" in runner.output_tail


def test_uses_chosen_directory_and_environment(runner, tmp_path):
    cwd = tmp_path / "app"
    cwd.mkdir()
    command = python_command(tmp_path, """
import json, os
print(json.dumps({'cwd': os.getcwd(), 'value': os.environ['AURA_COMMAND_TEST']}))
""", cwd="app", env={"AURA_COMMAND_TEST": "value with spaces & symbols"})
    assert runner.start(tmp_path, command)
    spin_until(lambda: not runner.active)
    result = json.loads(runner.output_tail)
    assert Path(result["cwd"]) == cwd
    assert result["value"] == "value with spaces & symbols"


def test_uses_project_venv_and_preserves_shell_syntax(runner, tmp_path):
    venv_root = tmp_path / ".venv"
    venv.EnvBuilder(with_pip=False).create(venv_root)
    script = tmp_path / "environment.py"
    script.write_text("import sys, os; print(sys.prefix); print(os.environ['VIRTUAL_ENV'])", encoding="utf-8")
    command = "python environment.py; echo shell-syntax" if os.name == "nt" else "python environment.py && echo shell-syntax"
    assert runner.start(tmp_path, ProjectCommand("Test", command))
    spin_until(lambda: not runner.active)
    assert runner.state == "succeeded"
    lines = runner.output_tail.splitlines()
    assert Path(lines[0]) == venv_root
    assert Path(lines[1]) == venv_root
    assert lines[2] == "shell-syntax"


def test_invalid_cwd_fails_without_spawning_and_shutdown_is_final(runner, tmp_path):
    assert runner.start(tmp_path, ProjectCommand("Run", "echo should-not-launch", cwd="missing"))
    assert runner.state == "failed"
    assert not runner.active
    assert runner.exit_code is None
    runner.shutdown()
    assert not runner.start(tmp_path, ProjectCommand("Run", "echo nope"))


def test_server_is_live_until_stopped_and_restart_uses_same_snapshot(runner, tmp_path):
    command = python_command(tmp_path, """
import os, time
print('ready', os.getpid())
while True:
    time.sleep(1)
""")
    starts, states = [], []
    runner.started.connect(lambda run_id, command, cwd: starts.append((run_id, command, cwd)))
    runner.state_changed.connect(states.append)
    assert runner.start(tmp_path, command)
    spin_until(lambda: "ready" in runner.output_tail)
    first_id = runner.run_id
    assert not runner.start(tmp_path, ProjectCommand("Other", "echo other"))
    assert runner.restart()
    spin_until(lambda: runner.run_id != first_id and "ready" in runner.output_tail)
    assert runner.active
    assert len(starts) == 2
    assert starts[0][1:] == starts[1][1:]
    assert "stopping" in states
    assert "stopped" in states
    runner.stop()
    spin_until(lambda: not runner.active)
    assert runner.state == "stopped"


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group and signal verification")
def test_stop_kills_child_even_when_shell_exits_and_child_ignores_term(runner, tmp_path):
    command = python_command(tmp_path, """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c',
    'import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print(os.getpid(), flush=True); time.sleep(60)'])
time.sleep(60)
""")
    assert runner.start(tmp_path, command)
    spin_until(lambda: runner.output_tail.strip().isdigit())
    child_pid = int(runner.output_tail.strip())
    runner.stop()
    spin_until(lambda: not runner.active)
    assert runner.state == "stopped"

    def child_stopped():
        try:
            # In minimal containers PID 1 may not reap an orphaned zombie;
            # zombies have exited and cannot retain the port or execute work.
            state = Path(f"/proc/{child_pid}/stat")
            if state.exists() and state.read_text().split()[2] == "Z":
                return True
            os.kill(child_pid, 0)
        except ProcessLookupError:
            return True
        return False

    spin_until(child_stopped)


def test_stop_ends_descendant_work_on_each_platform(runner, tmp_path):
    child_script = tmp_path / "child.py"
    child_script.write_text("""
import os, pathlib, time
pathlib.Path('child-pid').write_text(str(os.getpid()))
while True:
    pathlib.Path('heartbeat').write_text(str(time.monotonic_ns()))
    time.sleep(0.02)
""", encoding="utf-8")
    command = python_command(tmp_path, """
import subprocess, sys, time
subprocess.Popen([sys.executable, 'child.py'])
time.sleep(60)
""")
    assert runner.start(tmp_path, command)
    heartbeat = tmp_path / "heartbeat"
    spin_until(heartbeat.exists)
    child_pid = int((tmp_path / "child-pid").read_text())
    try:
        runner.stop()
        spin_until(lambda: not runner.active)
        assert runner.state == "stopped"
        last = heartbeat.read_text()
        time.sleep(0.12)
        assert heartbeat.read_text() == last
    finally:
        # If termination regresses, the failed test still cleans up its child.
        try:
            os.kill(child_pid, signal.SIGTERM)
        except OSError:
            pass


def test_output_is_bounded_and_shutdown_does_not_need_gui_drain(runner, tmp_path):
    command = python_command(tmp_path, """
import os
while True:
    os.write(1, b'x' * 8192)
""")
    assert runner.start(tmp_path, command)
    spin_until(lambda: len(runner.output_tail) == MAX_OUTPUT_CHARS)
    # Deliberately freeze GUI delivery long enough to fill the bounded queue.
    time.sleep(0.15)
    started = time.monotonic()
    runner.shutdown()
    assert time.monotonic() - started < 4
    assert not runner.active
    assert len(runner.output_tail) <= MAX_OUTPUT_CHARS
    assert runner.state == "stopped"


def test_stop_at_launch_and_shutdown_from_started_callback(runner, tmp_path):
    command = python_command(tmp_path, "import time; time.sleep(60)")
    runner.started.connect(lambda *_: runner.shutdown())
    assert runner.start(tmp_path, command)
    assert not runner.active
    assert runner.state == "stopped"


@pytest.mark.parametrize("stop_on_finish", [False, True])
def test_ordinary_stop_cancels_a_pending_restart(runner, tmp_path, stop_on_finish):
    command = python_command(tmp_path, "import time; print('ready'); time.sleep(60)")
    starts = []
    runner.started.connect(lambda *args: starts.append(args))
    assert runner.start(tmp_path, command)
    spin_until(lambda: "ready" in runner.output_tail)
    if stop_on_finish:
        runner.finished.connect(lambda *_: runner.stop())
    assert runner.restart()
    if not stop_on_finish:
        runner.stop()
    spin_until(lambda: not runner.active)
    assert len(starts) == 1
    assert runner.state == "stopped"


def test_windows_without_job_kills_tree_before_wrapper_can_exit(monkeypatch):
    from aura.gui import project_command_runner as runtime

    class Wrapper:
        exited = False
        children_running = True

        def poll(self):
            return 0 if self.exited else None

        def send_signal(self, _):
            self.exited = True  # Simulate CTRL_BREAK leaving its child alive.

        def wait(self, **_):
            return 0

    process = Wrapper()

    def tree_kill(wrapper, job, readers):
        assert not wrapper.exited, "taskkill needs the live wrapper's lineage"
        assert job is None and readers == []
        wrapper.exited = True
        wrapper.children_running = False

    monkeypatch.setattr(runtime, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(runtime, "signal", SimpleNamespace(CTRL_BREAK_EVENT=1))
    monkeypatch.setattr(runtime, "terminate_process_tree", tree_kill)
    runtime._finish_tree(process, None, stopping=True)
    assert process.exited
    assert not process.children_running
