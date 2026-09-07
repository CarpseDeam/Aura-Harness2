"""Own project command selection, editing, and its terminal projection."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from aura.gui.project_command_controls import ProjectCommandControls, ProjectCommandDialog
from aura.gui.project_command_runner import ProjectCommandRunner
from aura.gui.terminal_window import TerminalWindow
from aura.project_commands import (
    ProjectCommand,
    ProjectCommandsState,
    ProjectCommandStore,
    ProjectCommandStoreError,
    discover_project_commands,
)


class ProjectCommandController(QObject):
    """One project selection and one user-started process, independent of chat."""

    def __init__(
        self,
        controls: ProjectCommandControls,
        terminal: TerminalWindow,
        parent_widget: QWidget,
        *,
        runner: ProjectCommandRunner | None = None,
        state_root: Path | None = None,
    ) -> None:
        super().__init__(parent_widget)
        self._controls = controls
        self._terminal = terminal
        self._parent_widget = parent_widget
        self._runner = runner or ProjectCommandRunner(self)
        self._state_root = state_root
        self._root: Path | None = None
        self._store: ProjectCommandStore | None = None
        self._settings = ProjectCommandsState()
        self._load_error = ""
        self._run_root: Path | None = None
        self._run_cwd = ""
        self._has_output = False
        self._closed = False
        controls.primary_requested.connect(self._primary)
        controls.selected.connect(self._select)
        controls.add_requested.connect(self._add)
        controls.edit_requested.connect(self._edit)
        controls.remove_requested.connect(self._remove)
        controls.restart_requested.connect(self._runner.restart)
        controls.output_requested.connect(self.show_output)
        self._runner.started.connect(self._started)
        self._runner.output.connect(self._terminal.append_output)
        self._runner.finished.connect(self._finished)
        self._runner.state_changed.connect(self._render)
        self._terminal.terminal_cleared.connect(self._restore_running_transcript)

    def set_workspace_root(self, root: Path | None) -> None:
        resolved = Path(root).resolve() if root is not None else None
        if resolved != self._root:
            self._runner.stop()
        self._root = resolved
        self._settings = ProjectCommandsState()
        self._load_error = ""
        self._store = ProjectCommandStore(resolved, state_root=self._state_root) if resolved else None
        if self._store:
            try:
                self._settings = self._store.load()
            except ProjectCommandStoreError as exc:
                self._load_error = f"{exc}\n\n{self._store.path}"
        self._render()

    def _render(self, _state: str = "") -> None:
        same_command = self._run_root == self._root and self._settings.selected == self._runner.current_command
        state = self._runner.state if same_command or self._runner.active else "idle"
        self._controls.render(
            self._settings,
            available=self._root is not None and not self._closed,
            state=state,
            running_command=self._runner.current_command,
            has_output=self._has_output,
            error=self._load_error,
        )

    def _primary(self) -> None:
        if self._runner.active:
            self._runner.stop()
        elif self._load_error:
            QMessageBox.warning(self._parent_widget, "Project commands", self._load_error)
        elif self._settings.selected:
            self._start_selected()
        else:
            self._add()

    def _start_selected(self) -> None:
        if self._root is not None and self._settings.selected and not self._load_error:
            self._runner.start(self._root, self._settings.selected)

    def _select(self, command_id: str) -> None:
        if self._runner.active or not any(c.id == command_id for c in self._settings.commands):
            return
        settings = replace(self._settings, selected_id=command_id)
        if self._save(settings):
            self._settings = settings
            self._render()

    def _add(self) -> None:
        self._edit_command(None)

    def _edit(self) -> None:
        self._edit_command(self._settings.selected)

    def _edit_command(self, command: ProjectCommand | None) -> None:
        if self._root is None or self._runner.active or self._load_error:
            return
        suggestions = [] if command else discover_project_commands(self._root)
        dialog = ProjectCommandDialog(
            self._root, command=command, suggestions=suggestions, parent=self._parent_widget,
        )
        try:
            while dialog.exec() == QDialog.DialogCode.Accepted:
                saved = dialog.saved_command
                if saved is None:
                    return
                commands = list(self._settings.commands)
                existing = next((i for i, item in enumerate(commands) if item.id == saved.id), None)
                if existing is None:
                    commands.append(saved)
                else:
                    commands[existing] = saved
                settings = ProjectCommandsState(commands=tuple(commands), selected_id=saved.id)
                if not self._save(settings):
                    continue  # Keep the entered values available after a failed save.
                self._settings = settings
                self._render()
                if dialog.run_after_save:
                    self._start_selected()
                return
        finally:
            dialog.deleteLater()

    def _remove(self) -> None:
        selected = self._settings.selected
        if selected is None or self._runner.active:
            return
        remaining = tuple(c for c in self._settings.commands if c.id != selected.id)
        settings = ProjectCommandsState(commands=remaining)
        if self._save(settings):
            self._settings = settings
            self._render()

    def _save(self, settings: ProjectCommandsState) -> bool:
        if self._store is None or self._load_error:
            return False
        try:
            self._store.save(settings)
        except (ProjectCommandStoreError, ValueError) as exc:
            QMessageBox.warning(self._parent_widget, "Could not save command", str(exc))
            return False
        return True

    def _started(self, run_id: str, command: ProjectCommand, cwd: str) -> None:
        self._run_root = self._root
        self._run_cwd = cwd
        self._has_output = True
        self._terminal.set_command(run_id, command.command, cwd)
        self._render()
        self.show_output()

    def _finished(self, run_id: str, exit_code: int | None) -> None:
        self._terminal.set_result(run_id, exit_code, stopped=self._runner.state == "stopped")
        self._render()

    def _restore_running_transcript(self) -> None:
        # New Chat / loading a conversation reset the shared transcript. The
        # project command lives independently and must keep its output route.
        command = self._runner.current_command
        run_id = self._runner.run_id
        self._has_output = self._runner.active
        if self._runner.active and command and not self._terminal.has_command(run_id):
            self._terminal.set_command(run_id, command.command, self._run_cwd)
            self._terminal.append_output(run_id, self._runner.output_tail)
        self._render()

    def show_output(self) -> None:
        self._terminal.show()
        self._terminal.raise_()
        self._terminal.activateWindow()

    def shutdown(self) -> None:
        self._closed = True
        self._runner.shutdown()
