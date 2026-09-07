"""Saved commands through the real controls, controller, process, and terminal."""
from __future__ import annotations

import os
import shlex
import sys
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from aura.gui.main_window_terminal import MainWindowTerminalController
from aura.gui.project_command_controller import ProjectCommandController
from aura.gui.project_command_controls import ProjectCommandControls, ProjectCommandDialog
from aura.gui.terminal_window import TerminalWindow
from aura.project_commands import ProjectCommand, ProjectCommandsState, ProjectCommandStore


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def workspace_ui(qapp, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    host = QWidget()
    controls = ProjectCommandControls(host)
    terminal = TerminalWindow(host)
    controller = ProjectCommandController(controls, terminal, host, state_root=tmp_path / "settings")
    controller.set_workspace_root(root)
    host.show()
    yield root, controls, terminal, controller
    controller.shutdown()
    host.close()
    host.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def spin_until(predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


def python_command(root, name, source):
    script = root / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    if os.name == "nt":
        executable = sys.executable.replace("'", "''")
        script_path = str(script).replace("'", "''")
        command = f"& '{executable}' -u '{script_path}'"
    else:
        command = f"{shlex.quote(sys.executable)} -u {shlex.quote(str(script))}"
    return ProjectCommand(name, command)


def save_commands(controller, *commands):
    controller._store.save(ProjectCommandsState(commands))
    controller.set_workspace_root(controller._root)


def test_first_setup_saves_without_execution_then_primary_runs(workspace_ui, monkeypatch):
    root, controls, terminal, controller = workspace_ui
    command = python_command(root, "Launch app", "from pathlib import Path\nPath('ran').write_text('yes')\nprint('app complete')")
    monkeypatch.setattr("aura.gui.project_command_controller.discover_project_commands", lambda _: [command])
    original_exec = ProjectCommandDialog.exec

    def edit_and_save(dialog):
        assert dialog.command_edit.text() == command.command
        def click_save():
            button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Save")
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        QTimer.singleShot(0, click_save)
        return original_exec(dialog)

    monkeypatch.setattr(ProjectCommandDialog, "exec", edit_and_save)
    QTest.mouseClick(controls.run_button, Qt.MouseButton.LeftButton)
    assert not controller._runner.active
    assert not (root / "ran").exists()
    assert controls.run_button.text() == "Launch app"
    assert controller._store.load().selected.command == command.command

    QTest.mouseClick(controls.run_button, Qt.MouseButton.LeftButton)
    spin_until(lambda: not controller._runner.active)
    assert (root / "ran").read_text() == "yes"
    assert controls.status.text() == "Succeeded"
    assert "app complete\n✓ exited 0" in terminal.transcript_text()


def test_failed_command_selection_and_output_survive_reopening(workspace_ui):
    root, controls, terminal, controller = workspace_ui
    failure = python_command(root, "Run tests", "print('test failed')\nraise SystemExit(7)")
    other = ProjectCommand("Build", "echo build")
    save_commands(controller, failure, other)
    controls.run_button.click()
    spin_until(lambda: not controller._runner.active)
    assert controls.status.text() == "Failed"
    assert "test failed\n✗ exited 7" in terminal.transcript_text()
    terminal.hide()
    controls.output_button.click()
    assert terminal.isVisible()
    next(action for action in controls.menu.actions() if action.text() == "Build").trigger()
    assert controls.run_button.text() == "Build"
    assert controls.status.text() == ""
    controller.set_workspace_root(root)
    assert controller._settings.selected == other
    assert controls.run_button.text() == "Build"
    terminal.reset()
    assert controls.output_button.isHidden()
    assert not any(action.text() == "View output" for action in controls.menu.actions())


def test_server_survives_chat_reset_and_stops_on_project_change(workspace_ui):
    root, controls, terminal, controller = workspace_ui
    server = python_command(root, "Start server", "import time\nprint('ready', flush=True)\nwhile True: time.sleep(0.1)")
    save_commands(controller, server)
    controls.run_button.click()
    spin_until(lambda: "ready" in controller._runner.output_tail)
    run_id = controller._runner.run_id
    terminal.reset()  # The same reset used by New Chat and conversation loading.
    assert controller._runner.active and terminal.has_command(run_id)
    assert terminal.transcript_text().count("ready") == 1
    terminal.clear_display()
    assert terminal.has_command(run_id)
    assert terminal.transcript_text() == ""

    next_root = root.parent / "other project"
    next_root.mkdir()
    controller._runner.restart()
    controller.set_workspace_root(next_root)
    spin_until(lambda: not controller._runner.active)
    assert controller._runner.run_id == run_id  # Workspace change cancels pending Restart.
    assert controls.run_button.text() == "Set up command"
    assert controls.status.text() == ""
    assert "stopped" in terminal.transcript_text()
    assert "exited 0" not in terminal.transcript_text()
    assert not terminal.has_active_commands


def test_invalid_saved_folder_reports_no_exit_code(workspace_ui):
    root, controls, terminal, controller = workspace_ui
    command = ProjectCommand("Start server", "echo should not run", cwd="gone")
    save_commands(controller, command)
    controls.run_button.click()
    assert not controller._runner.active
    assert controls.status.text() == "Failed"
    text = terminal.transcript_text()
    assert "Working directory does not exist" in text and "could not start" in text
    assert "exited" not in text


def test_corrupt_settings_are_visible_and_never_replaced(workspace_ui, monkeypatch):
    root, controls, _, controller = workspace_ui
    store = ProjectCommandStore(root, state_root=controller._state_root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("broken")
    controller.set_workspace_root(root)
    warnings = []
    monkeypatch.setattr("aura.gui.project_command_controller.QMessageBox.warning", lambda *args: warnings.append(args[-1]))
    controls.run_button.click()
    assert warnings and controls.status.text() == "Settings error"
    assert not controller._runner.active and store.path.read_text() == "broken"


def test_editor_validates_folder_env_and_preserves_edit_identity(qapp, tmp_path):
    existing = ProjectCommand("Tests", "python -m pytest")
    dialog = ProjectCommandDialog(tmp_path, command=existing)
    dialog.cwd_edit.setText("../outside")
    dialog._save(False)
    assert dialog.saved_command is None and dialog.error_label.text()
    dialog.cwd_edit.setText(".")
    dialog.env_edit.setPlainText("PORT=3000\nPORT=4000")
    dialog._save(False)
    assert "more than once" in dialog.error_label.text()
    dialog.env_edit.setPlainText("PORT=3000\nVALUE=a=b")
    dialog._save(True)
    assert dialog.saved_command.id == existing.id
    assert dict(dialog.saved_command.env) == {"PORT": "3000", "VALUE": "a=b"}
    assert dialog.run_after_save
    dialog.deleteLater()


def test_terminal_rail_stays_running_when_another_command_finishes(workspace_ui):
    _, _, terminal, _ = workspace_ui
    rail = SimpleNamespace(state="dim")
    rail.set_state = lambda state: setattr(rail, "state", state)
    window = SimpleNamespace(_edge_rail=rail, _playground=SimpleNamespace(terminal_window=lambda: terminal))
    controller = MainWindowTerminalController(window)
    terminal.terminal_started.connect(controller._on_terminal_started)
    terminal.terminal_finished.connect(controller._on_terminal_finished)
    terminal.terminal_stopped.connect(controller._on_terminal_stopped)
    terminal.set_command("server", "start server")
    terminal.set_command("agent", "run tests")
    terminal.set_result("agent", 0)
    assert rail.state == "running"
    terminal.set_result("server", 0, stopped=True)
    assert rail.state == "dim"
    assert "■ stopped" in terminal.transcript_text()
    terminal.set_command("missing", "missing program")
    terminal.set_result("missing", None)
    assert rail.state == "failure"
