"""Agents and Teams library with an on-demand graph editor and inspector.

The page renders presentation data and emits intent. Agent storage/grants and
Workflow sessions retain their existing owners; Teams are Workflow objects.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aura.agents.local_state import AgentPermission
from aura.gui.agents_editor import (
    AgentDetail,
    AgentDraft,
    AgentEditor,
    ModelChoices,
    ModelTargetChoice,
    catalog_choices,
)
from aura.gui.agents_library import (
    SCOPE_LABELS,
    SCOPE_ORDER,
    AgentLibrary,
    AgentRow,
)
from aura.gui.agents_workflow_bar import WorkflowBar, WorkflowRow
from aura.gui.agents_workflow_canvas import WorkflowScene, WorkflowView
from aura.gui.agents_workflow_inspector import (
    ConnectionInfo,
    OccurrenceInfo,
    WorkflowInfo,
    WorkflowInspector,
)
from aura.gui.theme import BG, BORDER, FG, FG_MUTED

_BUSY_NOTE = (
    "Aura is running a turn. You can read your agents; changes are available "
    "again when the turn finishes."
)


class AgentsPage(QDialog):
    """Modeless Agents window: library, canvas, inspector, and the local grants."""

    team_opened = Signal()
    arrange_requested = Signal()
    visibility_changed = Signal(bool)
    current_row_changed = Signal(str)
    author_with_aura_requested = Signal()
    create_requested = Signal(str)  # scope key
    save_requested = Signal(object)  # AgentDraft
    delete_requested = Signal(str, str)  # scope key, agent id
    availability_changed = Signal(str, bool)
    permission_changed = Signal(str, str)  # agent id, AgentPermission value

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        choices: ModelChoices | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("agentsPage")
        self.setWindowTitle("Agents & Teams")
        self.setModal(False)
        self.setMinimumSize(940, 560)
        self.resize(1350, 800)
        self.setStyleSheet(
            f"QDialog#agentsPage {{ background: {BG}; border: 1px solid {BORDER}; }}"
        )

        self._choices = choices or catalog_choices()
        self._detail: AgentDetail | None = None
        self._mutations_enabled = True
        self._editing_team = False
        self._team_info = None
        self._pending_inspector = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        self._header_row = self._build_header()
        layout.addLayout(self._header_row)

        self.workflow_bar = WorkflowBar()
        layout.addWidget(self.workflow_bar)
        self.author_button = QPushButton("Ask Aura to build a Team")
        self.author_button.setToolTip("Describe a Team that implements, tests, and reviews a change in chat.")
        self.author_button.clicked.connect(self.author_with_aura_requested)
        self._header_row.addWidget(self.author_button)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(5)
        self.splitter.addWidget(self._build_library())
        self.splitter.addWidget(self._build_canvas())
        self._inspector_panel = self._build_inspector()
        self.splitter.addWidget(self._inspector_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setCollapsible(2, False)
        layout.addWidget(self.splitter, 1)
        self.show_library()
        layout.addLayout(self._build_footer())

    # ---- construction ------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        self.back_button = QPushButton("← Library")
        self.back_button.clicked.connect(self.show_library)
        row.addWidget(self.back_button)
        self.title = QLabel("Agents & Teams")
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPixelSize(17)
        self.title.setFont(title_font)
        self.title.setStyleSheet(f"color: {FG}; font-size: 17px; font-weight: 600; background: transparent;")
        row.addWidget(self.title, 1)
        self.library_button = QPushButton("Library")
        self.library_button.setCheckable(True)
        self.library_button.toggled.connect(self._toggle_library)
        row.addWidget(self.library_button)
        self.arrange_button = QPushButton("Arrange")
        self.arrange_button.setToolTip("Arrange this Team to use the canvas. Undo restores its previous positions and routes.")
        self.arrange_button.clicked.connect(self.arrange_requested)
        row.addWidget(self.arrange_button)
        self.fit_button = QPushButton("Fit")
        self.fit_button.setToolTip("Show the complete graph (F). Ctrl+0 restores 100% zoom.")
        self.fit_button.clicked.connect(lambda: self.view.fit_to_content())
        row.addWidget(self.fit_button)
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(lambda: self.view.undo_requested.emit())
        row.addWidget(self.undo_button)
        self.redo_button = QPushButton("Redo")
        self.redo_button.clicked.connect(lambda: self.view.redo_requested.emit())
        row.addWidget(self.redo_button)
        self.inspector_button = QPushButton("Settings")
        self.inspector_button.setCheckable(True)
        self.inspector_button.toggled.connect(self._toggle_inspector)
        row.addWidget(self.inspector_button)
        return row

    def _build_library(self) -> QWidget:
        self._library = AgentLibrary()
        self._library.create_requested.connect(self._request_create)
        self._library.current_row_changed.connect(self.current_row_changed)
        self._library.availability_changed.connect(self.availability_changed)
        self._library.tree.itemClicked.connect(lambda *_: self._choose_library_agent())
        self._library.tree.itemActivated.connect(lambda *_: self._choose_library_agent())
        self._library.tree.currentItemChanged.connect(
            lambda *_: self._choose_library_agent()
            if self._library.tree.hasFocus() and not self._library._loading else None
        )
        self._library.team_open_requested.connect(self.open_team)
        self._library.team_create_requested.connect(self.workflow_bar.create_requested)
        return self._library

    def _build_canvas(self) -> QWidget:
        self.scene = WorkflowScene(self)
        self.view = WorkflowView(self.scene)
        self.view.interaction_finished.connect(self._finish_graph_selection)
        return self.view

    def _build_inspector(self) -> QWidget:
        self._editor = AgentEditor(self._choices)
        self._editor.save_requested.connect(self.save_requested)
        self._editor.delete_requested.connect(self.delete_requested)
        self._editor.permission_changed.connect(self.permission_changed)
        self.inspector = WorkflowInspector(self._editor)

        # Compatibility aliases for the page's established test/controller
        # surface. Ownership remains inside AgentEditor.
        self._name = self._editor.name
        self._description = self._editor.description
        self._instructions = self._editor.instructions
        self._model = self._editor.model
        self._thinking = self._editor.thinking
        self._permission = self._editor.permission
        self._save_btn = self._editor.save_button
        self._delete_btn = self._editor.delete_button

        scroller = QScrollArea()
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QScrollArea.Shape.NoFrame)
        scroller.setWidget(self.inspector)
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        self.selection_label = QLabel("Team settings")
        self.selection_label.setWordWrap(True)
        self.selection_label.setStyleSheet(f"color: {FG}; font-weight: 600; padding: 8px;")
        column.addWidget(self.selection_label)
        column.addWidget(scroller, 1)
        panel.setMinimumWidth(340)
        return panel

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"color: {FG_MUTED}; font-size: 11px; background: transparent;"
        )
        row.addWidget(self._status, 1)

        close = QPushButton("Close")
        close.clicked.connect(self.hide)
        row.addWidget(close)
        return row

    def show_library(self) -> None:
        self._editing_team = False
        self.title.setText("Agents & Teams")
        self.workflow_bar.hide()
        self.author_button.show()
        self.view.hide()
        self._library.show()
        self._library.setMaximumWidth(16777215)
        self._inspector_panel.hide()
        self.inspector_button.setChecked(False)
        self.inspector_button.hide()
        for button in (self.back_button, self.library_button, self.arrange_button,
                       self.fit_button, self.undo_button, self.redo_button):
            button.hide()

    def open_team(self, graph_id: str = "") -> None:
        self._editing_team = True
        self._library.hide()
        self._library.setMaximumWidth(350)
        self.library_button.setChecked(False)
        self._inspector_panel.hide()
        self.inspector_button.setChecked(False)
        self.inspector_button.show()
        self.view.show()
        self.author_button.hide()
        self.workflow_bar.show()
        for button in (self.back_button, self.library_button, self.arrange_button,
                       self.fit_button, self.undo_button, self.redo_button):
            button.show()
        if graph_id:
            self.workflow_bar.workflow_selected.emit(graph_id)
        self.team_opened.emit()
        if self._team_info:
            self.title.setText(self._team_info.name)
        self.scene.clearSelection()
        self.inspector.set_context("team")
        self.selection_label.setText("Team settings")
        self.view.setFocus()
        QTimer.singleShot(0, self.view.fit_to_content)

    def _toggle_library(self, visible: bool) -> None:
        if not self._editing_team:
            return
        self._library.setVisible(visible)
        if visible:
            # One auxiliary panel at a time keeps a useful canvas on laptops.
            self.inspector_button.setChecked(False)
            self.splitter.setSizes([320, max(500, self.width() - 360), 0])

    def _toggle_inspector(self, visible: bool) -> None:
        self._inspector_panel.setVisible(visible)
        if visible:
            if self._editing_team:
                self.library_button.setChecked(False)
            self.splitter.setSizes([max(400, self.width() - 440), max(500, self.width() - 440), 390])

    def _choose_library_agent(self) -> None:
        # A deliberate library click edits the definition, including when it
        # happens to be the same Agent as the selected graph occurrence.
        self.scene.clearSelection()
        self.current_row_changed.emit(self.current_source_key())
        self.reveal_agent()

    def reveal_agent(self) -> None:
        if self._detail is None:
            return
        self.inspector.set_context("agent")
        self.selection_label.setText(f"Agent · {self._detail.name}")
        self.inspector_button.show()
        self.inspector_button.setChecked(True)

    def reveal_selection(self, kind: str) -> None:
        occurrence = self.inspector.occurrence
        self.inspector.set_context("team" if not kind or (kind == "node" and occurrence is None) else kind)
        self.selection_label.setText(
            f"Assignment · {occurrence.agent_name}" if kind == "node" and occurrence else
            "Connection settings" if kind == "connection" else "Team settings"
        )
        if kind and QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            # Revealing a dock during a node's mouse press changes scene
            # coordinates under the gesture and can turn a click into a move.
            self._pending_inspector = True
        elif kind:
            self.inspector_button.setChecked(True)

    def _finish_graph_selection(self) -> None:
        if self._pending_inspector:
            self._pending_inspector = False
            self.inspector_button.setChecked(True)
            nodes, _edges = self.scene.selected_ids()
            if len(nodes) == 1:
                item = self.scene.node_items.get(nodes[0])
                if item is not None:
                    self.view.ensureVisible(item, 50, 50)

    def set_history_actions(self, can_undo: bool, can_redo: bool) -> None:
        self.undo_button.setEnabled(self._mutations_enabled and can_undo)
        self.redo_button.setEnabled(self._mutations_enabled and can_redo)

    # ---- the library's established surface ---------------------------------

    @property
    def _items(self) -> dict[str, QTreeWidgetItem]:
        return self._library.items

    @property
    def _tree(self):
        return self._library.tree

    @property
    def _new_project_btn(self) -> QPushButton:
        return self._library.new_project_button

    @property
    def _new_personal_btn(self) -> QPushButton:
        return self._library.new_personal_button

    # ---- controller-facing API ---------------------------------------------

    def set_rows(self, rows: tuple[AgentRow, ...]) -> None:
        """Replace the whole roster, keeping the current row when it survives."""
        self._library.set_rows(rows)

    def set_detail(self, detail: AgentDetail | None) -> None:
        """Load the editor with the current agent, or clear it."""
        self._detail = detail
        self._editor.set_detail(detail)
        if not self._inspector_panel.isHidden() and self.inspector.context == "agent":
            self.selection_label.setText(f"Agent · {detail.name}" if detail else "Agent settings")

    def apply_local_state(
        self, agent_id: str, *, available: bool, permission: AgentPermission
    ) -> None:
        """Re-render one row after a local decision, without rebuilding the list."""
        if not self._library.apply_local_state(
            agent_id, available=available, permission=permission
        ):
            return
        if self._detail is not None and self._detail.agent_id == agent_id:
            self._detail = replace(
                self._detail, available=available, permission=permission
            )
            self._editor.apply_local_state(available=available, permission=permission)

    def set_mutations_enabled(self, enabled: bool) -> None:
        """Allow or forbid every change without hiding anything.

        Browsing stays live during a turn: definitions, the roster, permissions,
        and every workflow are all frozen, because a running turn may already
        be acting on the answers they gave.
        """
        self._mutations_enabled = bool(enabled)
        self.author_button.setEnabled(self._mutations_enabled)
        self.arrange_button.setEnabled(self._mutations_enabled and self._team_info is not None)
        if not self._mutations_enabled:
            self.set_history_actions(False, False)
        self._status.setText("" if self._mutations_enabled else _BUSY_NOTE)
        self._library.set_mutations_enabled(self._mutations_enabled)
        self.inspector.set_mutations_enabled(self._mutations_enabled)
        self.workflow_bar.set_mutations_enabled(self._mutations_enabled)
        self.scene.set_editable(self._mutations_enabled)

    def mutations_enabled(self) -> bool:
        return self._mutations_enabled

    def is_open(self) -> bool:
        return self.isVisible()

    def current_agent_id(self) -> str:
        return self._library.current_agent_id()

    def current_source_key(self) -> str:
        return self._library.current_source_key()

    def select_agent(self, agent_id: str, scope: str = "") -> bool:
        return self._library.select_agent(agent_id, scope)

    def visible_agent_ids(self) -> dict[str, tuple[str, ...]]:
        """Visible agent ids per scope, in rendered order."""
        return self._library.visible_agent_ids()

    def draft(self) -> AgentDraft | None:
        """What Save would send right now, or None with nothing loaded."""
        return self._editor.draft()

    # ---- the workflow surface ----------------------------------------------

    def set_workflow_rows(self, rows: tuple[WorkflowRow, ...], current_id: str) -> None:
        self.workflow_bar.set_rows(rows, current_id)
        self._library.set_team_rows(rows, current_id)

    def set_workflow_runnable(self, runnable: bool) -> None:
        self.workflow_bar.set_runnable(runnable)

    def set_workflow_running(self, running: bool) -> None:
        self.workflow_bar.set_running(running)

    def set_run_states(self, nodes: dict, connections: dict | None = None) -> None:
        """Show which steps are running, finished, or never got to."""
        self.scene.set_run_states(nodes, connections)

    def set_model_choices(self, choices: ModelChoices) -> None:
        """Re-list the editor's qualified targets after catalogs change."""
        self._choices = choices
        self._editor.set_choices(choices)

    def set_workflow_info(self, info: WorkflowInfo | None) -> None:
        self._team_info = info
        self.inspector.set_workflow(info)
        self.arrange_button.setEnabled(self._mutations_enabled and info is not None)
        if self._editing_team:
            self.title.setText(info.name if info else "Team editor")

    def set_occurrence(self, occurrence: OccurrenceInfo | None) -> None:
        self.inspector.set_occurrence(occurrence)

    def set_connection(self, connection: ConnectionInfo | None) -> None:
        self.inspector.set_connection(connection)

    def current_workflow_id(self) -> str:
        return self.workflow_bar.current_graph_id()

    # ---- user intent -------------------------------------------------------

    def _request_create(self, scope: str) -> None:
        if self._mutations_enabled:
            self.create_requested.emit(scope)

    # ---- Qt lifecycle ------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt naming
        super().closeEvent(event)
        self.visibility_changed.emit(False)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self.visibility_changed.emit(False)


__all__ = [
    "SCOPE_LABELS",
    "SCOPE_ORDER",
    "AgentDetail",
    "AgentDraft",
    "AgentRow",
    "AgentsPage",
    "ConnectionInfo",
    "ModelChoices",
    "ModelTargetChoice",
    "OccurrenceInfo",
    "WorkflowInfo",
    "WorkflowRow",
    "catalog_choices",
]
