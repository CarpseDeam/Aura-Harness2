"""Movable project command controls and the small command editor."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from aura.config import media_path
from aura.gui.theme import ACCENT, BG, DANGER, FG, FG_DIM, SUCCESS, WARN
from aura.project_commands import ProjectCommand, ProjectCommandsState

_ACTIVE_STATES = {"starting", "running", "stopping"}


class ProjectCommandControls(QWidget):
    """Presentation only: placement does not own settings or processes."""

    primary_requested = Signal()
    selected = Signal(str)
    add_requested = Signal()
    edit_requested = Signal()
    remove_requested = Signal()
    restart_requested = Signal()
    output_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._compact = False
        self._button_label = "Run…"
        self._active = False
        self._has_output = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.run_button = QToolButton(self)
        self.run_button.setObjectName("projectCommandRun")
        self.run_button.setIconSize(QSize(14, 14))
        self.run_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.run_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.run_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.run_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.run_button.clicked.connect(self.primary_requested.emit)
        self.menu = QMenu(self)
        self.run_button.setMenu(self.menu)
        layout.addWidget(self.run_button)

        self.status = QLabel(self)
        self.status.setObjectName("projectCommandStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)

        self.output_button = QToolButton(self)
        self.output_button.setObjectName("projectCommandOutput")
        self.output_button.setIconSize(QSize(14, 14))
        self.output_button.setIcon(QIcon(str(media_path("terminal_2_24dp.svg"))))
        self.output_button.setToolTip("View command output")
        self.output_button.setAccessibleName("View command output")
        self.output_button.clicked.connect(self.output_requested.emit)
        layout.addWidget(self.output_button)
        self.render(ProjectCommandsState(), available=False)

    def render(
        self,
        settings: ProjectCommandsState,
        *,
        available: bool = True,
        state: str = "idle",
        running_command: ProjectCommand | None = None,
        has_output: bool = False,
        error: str = "",
    ) -> None:
        active = state in _ACTIVE_STATES
        command = running_command if active else settings.selected
        name = command.name if command else "Run…"
        label = "Stop" if active else name
        self.run_button.setIcon(QIcon(str(media_path("project_stop.svg" if active else "project_play.svg"))))
        self._button_label, self._active, self._has_output = label, active, has_output
        self.run_button.setAccessibleName(f"Stop {name}" if active else command.name if command else "Set up project command")
        detail = f"{command.name}\n{command.command}\nFolder: {command.cwd}" if command else "Choose a command to run in this project"
        if state != "idle":
            detail = f"{state.capitalize()}: {detail}"
        self.run_button.setToolTip(error or detail if available else "Open a project to set up commands")
        self.run_button.setEnabled(available and state != "stopping")
        color = {"failed": DANGER, "succeeded": SUCCESS, "running": ACCENT, "stopping": WARN}.get(state, FG_DIM)
        status = "Settings error" if error else state.capitalize()
        if state == "idle" and not error:
            status = ""
        self.status.setText(status)
        self.status.setStyleSheet(f"color: {DANGER if error else color}; font-size: 11px;")
        self.status.setToolTip(error or detail)
        self._fit_labels()

        self.menu.clear()
        if state != "idle":
            self.menu.addSection(f"{state.capitalize()}: {name}")
        if settings.commands:
            self.menu.addSection("Project commands")
            for saved in settings.commands:
                action = self.menu.addAction(saved.name)
                action.setCheckable(True)
                action.setChecked(saved.id == getattr(settings.selected, "id", None))
                action.setToolTip(saved.command)
                action.setEnabled(not active and not error)
                action.triggered.connect(lambda _checked=False, key=saved.id: self.selected.emit(key))
            self.menu.addSeparator()
        if active:
            self.menu.addAction("Restart", self.restart_requested.emit)
        if has_output:
            self.menu.addAction("View output", self.output_requested.emit)
            self.menu.addSeparator()
        self.menu.addAction("Add command…", self.add_requested.emit).setEnabled(not active and not error)
        if settings.selected:
            self.menu.addAction("Edit command…", self.edit_requested.emit).setEnabled(not active and not error)
            self.menu.addAction("Remove command", self.remove_requested.emit).setEnabled(not active and not error)

    def set_compact(self, compact: bool) -> None:
        """Keep the primary action readable when the workspace pane narrows."""
        self._compact = compact
        self._fit_labels()

    def _fit_labels(self) -> None:
        label = "Run" if self._compact and self._button_label != "Run…" and not self._active else self._button_label
        self.run_button.setText(self.run_button.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 155))
        self.status.setVisible(bool(self.status.text()) and not self._compact)
        self.output_button.setVisible(self._has_output and not self._compact)


class ProjectCommandDialog(QDialog):
    """Review a detected command or save a custom one; saving alone never runs it."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        command: ProjectCommand | None = None,
        suggestions: list[ProjectCommand] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(f"QDialog {{ background: {BG}; color: {FG}; }}")
        self.setWindowTitle("Edit command" if command else "Add project command")
        self.setMinimumWidth(480)
        self._root = workspace_root
        self._original = command
        self.saved_command: ProjectCommand | None = None
        self.run_after_save = False
        layout = QVBoxLayout(self)
        intro = QLabel(f"Commands for {workspace_root.name}", self)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(intro)
        if not command:
            self.suggestions = QComboBox(self)
            self.suggestions.addItem("Custom command", None)
            for suggestion in suggestions or []:
                self.suggestions.addItem(f"{suggestion.name} — {suggestion.command}", suggestion)
            self.suggestions.currentIndexChanged.connect(self._choose_suggestion)
            layout.addWidget(self.suggestions)

        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText("Start server, Run tests, Launch app…")
        self.command_edit = QLineEdit(self)
        self.command_edit.setPlaceholderText("For example: npm run dev or python -m my_app")
        form.addRow("Name", self.name_edit)
        form.addRow("Command", self.command_edit)
        layout.addLayout(form)

        advanced_button = QToolButton(self)
        advanced_button.setText("Folder and environment")
        advanced_button.setCheckable(True)
        advanced_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        advanced_button.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(advanced_button)
        advanced = QWidget(self)
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        self.cwd_edit = QLineEdit(".", self)
        self.cwd_edit.setPlaceholderText(". = project folder; or a subfolder such as frontend")
        self.env_edit = QPlainTextEdit(self)
        self.env_edit.setPlaceholderText("Optional overrides, one per line:\nPORT=3000\nDEBUG=1")
        self.env_edit.setMaximumHeight(105)
        advanced_form.addRow("Folder", self.cwd_edit)
        advanced_form.addRow("Environment", self.env_edit)
        layout.addWidget(advanced)
        advanced.hide()

        def toggle_advanced(checked: bool) -> None:
            advanced.setVisible(checked)
            advanced_button.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            self.adjustSize()

        advanced_button.toggled.connect(toggle_advanced)
        note = QLabel("Saved for this project on this computer. Uses the project's Python environment when available.", self)
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {FG_DIM}; font-size: 11px;")
        layout.addWidget(note)
        self.error_label = QLabel(self)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {DANGER};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        save_button = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        run_button = buttons.addButton("Save and run", QDialogButtonBox.ButtonRole.ActionRole)
        save_button.setDefault(True)
        save_button.clicked.connect(lambda: self._save(False))
        run_button.clicked.connect(lambda: self._save(True))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if command:
            self._fill(command)
            advanced_button.setChecked(command.cwd not in ("", ".") or bool(command.env))
        elif suggestions:
            self.suggestions.setCurrentIndex(1)

    def _fill(self, command: ProjectCommand) -> None:
        self.name_edit.setText(command.name)
        self.command_edit.setText(command.command)
        self.cwd_edit.setText(command.cwd)
        self.env_edit.setPlainText("\n".join(f"{name}={value}" for name, value in command.env.items()))

    def _choose_suggestion(self, index: int) -> None:
        command = self.suggestions.itemData(index)
        if command is not None:
            self._fill(command)

    def _save(self, run: bool) -> None:
        try:
            env: dict[str, str] = {}
            for line in self.env_edit.toPlainText().splitlines():
                if not line.strip():
                    continue
                key, separator, value = line.partition("=")
                if not separator:
                    raise ValueError("Use NAME=value for each environment override.")
                if key.strip() in env:
                    raise ValueError(f"Environment variable {key.strip()} appears more than once.")
                env[key.strip()] = value
            kwargs = {"id": self._original.id} if self._original else {}
            command = ProjectCommand(
                name=self.name_edit.text().strip(), command=self.command_edit.text().strip(),
                cwd=self.cwd_edit.text().strip() or ".", env=env, **kwargs,
            )
            command.working_directory(self._root)
        except (OSError, ValueError) as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        self.saved_command = command
        self.run_after_save = run
        self.accept()
