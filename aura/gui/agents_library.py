"""One searchable Agent and Team library, with reusable-identity drag payloads.

Cards are presentation only. Scope remains part of every source key; names
never identify, combine, or delete definitions. Storage decisions leave as
signals for the established controllers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aura.agents.local_state import AgentPermission
from aura.gui.agents_library_cards import CARD_ROLE, LibraryCardDelegate
from aura.gui.agents_workflow_bar import WorkflowRow
from aura.gui.theme import BG_ALT, BORDER, FG

#: What a dragged library row carries: ``<scope>:<agent id>``. Names are
#: labels and change; the id is the agent.
AGENT_MIME = "application/x-aura-agent"

_ID_ROLE = Qt.ItemDataRole.UserRole
_AGENT_ID_ROLE = Qt.ItemDataRole.UserRole + 1

#: Group keys, in the order the library lists them.
SCOPE_ORDER: tuple[str, ...] = ("project", "personal")

SCOPE_LABELS: dict[str, str] = {"project": "Project", "personal": "Personal"}


@dataclass(frozen=True)
class AgentRow:
    """One agent as the list shows it.

    ``available`` and ``permission`` come from this user's private local
    state, never from the definition — a project definition has no say in
    either. ``model_label`` is already provider-qualified when the definition
    selects a provider, so the tooltip never hides a mixed-model target.
    """

    agent_id: str
    scope: str
    name: str
    description: str
    model_label: str
    thinking_label: str
    available: bool
    permission: AgentPermission
    valid: bool = True
    errors: tuple[str, ...] = ()

    @property
    def scope_label(self) -> str:
        return SCOPE_LABELS.get(self.scope, self.scope.title())

    @property
    def source_key(self) -> str:
        return source_key(self.scope, self.agent_id)


class AgentLibraryTree(QTreeWidget):
    """The rows themselves, and the one thing they know how to be: a drag."""

    def mimeData(  # noqa: N802 - Qt naming
        self, items: Sequence[QTreeWidgetItem]
    ) -> QMimeData:
        payload = QMimeData()
        for item in items:
            raw = item.data(0, _ID_ROLE)
            if raw:
                payload.setData(AGENT_MIME, str(raw).encode("utf-8"))
                payload.setText(item.text(0).splitlines()[0])
                break
        return payload


class AgentLibrary(QWidget):
    """Search and type views over the complete library."""

    team_open_requested = Signal(str)
    team_create_requested = Signal(str)
    create_requested = Signal(str)  # scope key
    current_row_changed = Signal(str)  # source key
    availability_changed = Signal(str, bool)  # agent id, available

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[AgentRow, ...] = ()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._current_source_key: str = ""
        self._current_id: str = ""
        self._mutations_enabled = True
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.tabs = QTabBar()
        self.tabs.addTab("Agents")
        self.tabs.addTab("Teams")
        self.tabs.setExpanding(False)
        layout.addWidget(self.tabs)
        buttons = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search names, purposes, models, or members…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        buttons.addWidget(self.search, 1)
        self.new_button = QToolButton()
        self.new_button.setText("New Agent")
        self.new_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.new_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.creation_menu = QMenu(self.new_button)
        self.creation_menu.addAction("Create in this project", lambda: self._create_in("project"))
        self.creation_menu.addAction("Create for this computer", lambda: self._create_in("personal"))
        self.new_button.setMenu(self.creation_menu)
        self.new_button.clicked.connect(self._create_current)
        self.new_button.setToolTip("Create in this project. Use the arrow for storage options.")
        buttons.addWidget(self.new_button)
        layout.addLayout(buttons)
        # Retain the established controller/test surface; storage choices are
        # secondary actions, no longer separate sections or primary buttons.
        self.new_project_button = QPushButton(self)
        self.new_personal_button = QPushButton(self)
        for button, scope in ((self.new_project_button, "project"), (self.new_personal_button, "personal")):
            button.hide()
            button.clicked.connect(lambda _checked=False, scope=scope: self._request_create(scope))
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.empty = QLabel("No matches. Try another search or create an Agent.")
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.tree = AgentLibraryTree()
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)
        self.tree.setItemDelegate(LibraryCardDelegate(self.tree))
        self.tree.setUniformRowHeights(False)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.DragOnly)
        self.tree.setToolTip("Drag an agent onto the canvas to place it in a workflow.")
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background: {BG_ALT}; color: {FG}; "
            f"border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}"
        )
        self.tree.currentItemChanged.connect(lambda _cur, _prev: self.sync_current())
        self.tree.itemChanged.connect(self._on_item_changed)
        self.stack.addWidget(self.tree)
        self.teams = QTreeWidget()
        self.teams.setHeaderHidden(True)
        self.teams.setRootIsDecorated(False)
        self.teams.setIndentation(0)
        self.teams.setItemDelegate(LibraryCardDelegate(self.teams))
        self.teams.setStyleSheet(self.tree.styleSheet())
        self.teams.itemClicked.connect(lambda item, _column: self.team_open_requested.emit(str(item.data(0, _ID_ROLE))))
        self.teams.itemActivated.connect(lambda item, _column: self.team_open_requested.emit(str(item.data(0, _ID_ROLE))))
        self.stack.addWidget(self.teams)
        self.tabs.currentChanged.connect(self._view_changed)
        self._team_rows = ()
        self._filter()

    def _view_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.new_button.setText("New Team" if index else "New Agent")
        self._filter()

    def _create_current(self) -> None:
        self._create_in("project")

    def _create_in(self, scope: str) -> None:
        if self._mutations_enabled:
            if self.tabs.currentIndex():
                self.team_create_requested.emit(scope)
            else:
                self._request_create(scope)

    def set_team_rows(self, rows: tuple[WorkflowRow, ...], current_id: str) -> None:
        self._team_rows = rows
        self.teams.clear()
        for row in sorted(rows, key=lambda row: (row.name.casefold(), row.graph_id)):
            item = QTreeWidgetItem(self.teams)
            item.setData(0, _ID_ROLE, row.graph_id)
            detail = "Members: " + (", ".join(row.members) or "No members yet")
            if not row.valid:
                detail = "Could not be loaded · " + "; ".join(row.errors)
            item.setText(0, "\n".join((row.name, row.description, detail)))
            item.setData(0, CARD_ROLE, dict(team=True, name=row.name, purpose=row.description,
                                          detail=detail, preview=row.preview))
            item.setToolTip(0, "\n".join((row.name, row.description, detail, f"{row.scope_label} · {row.graph_id}")))
            if row.graph_id == current_id:
                self.teams.setCurrentItem(item)
        self.tabs.setTabText(1, f"Teams ({len(rows)})")
        self._filter()

    def _filter(self, _text: str = "") -> None:
        query = self.search.text().strip().casefold()
        for tree in (self.tree, self.teams):
            for index in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(index)
                item.setHidden(query not in item.text(0).casefold())
        tree = self.teams if self.tabs.currentIndex() else self.tree
        count = sum(not tree.topLevelItem(i).isHidden() for i in range(tree.topLevelItemCount()))
        self.empty.setText("No matches. Try another search." if query else
                           ("No Teams yet. Create one here or ask Aura to build a Team in chat." if self.tabs.currentIndex()
                            else "No Agents yet. Create an Agent or ask Aura to build a Team in chat."))
        self.empty.setVisible(count == 0)

    # ---- what the page asks for --------------------------------------------

    @property
    def items(self) -> dict[str, QTreeWidgetItem]:
        return self._items

    @property
    def rows(self) -> tuple[AgentRow, ...]:
        return self._rows

    def current_source_key(self) -> str:
        return self._current_source_key

    def current_agent_id(self) -> str:
        return self._current_id

    def set_rows(self, rows: tuple[AgentRow, ...]) -> None:
        """Replace the whole roster, keeping the current row when it survives."""
        self._rows = tuple(rows)
        self.rebuild()

    def set_mutations_enabled(self, enabled: bool) -> None:
        self._mutations_enabled = bool(enabled)
        self.new_button.setEnabled(self._mutations_enabled)
        self.new_project_button.setEnabled(self._mutations_enabled)
        self.new_personal_button.setEnabled(self._mutations_enabled)
        self.rebuild()

    def row(self, key: str) -> AgentRow | None:
        return next((row for row in self._rows if row.source_key == key), None)

    def select_agent(self, agent_id: str, scope: str = "") -> bool:
        matches = [
            row
            for row in self._rows
            if row.agent_id == agent_id and (not scope or row.scope == scope)
        ]
        if len(matches) != 1:
            return False
        item = self._items.get(matches[0].source_key)
        if item is None:
            return False
        self.tree.setCurrentItem(item)
        return True

    def visible_agent_ids(self) -> dict[str, tuple[str, ...]]:
        """Visible agent ids per scope, in rendered order."""
        return {
            scope: tuple(row.agent_id for row in sorted(self._rows, key=lambda row: row.name.casefold())
                         if row.scope == scope and not self._items[row.source_key].isHidden())
            for scope in SCOPE_ORDER
        }

    def apply_local_state(
        self, agent_id: str, *, available: bool, permission: AgentPermission
    ) -> tuple[AgentRow, ...]:
        """Re-render one agent's rows in place, without rebuilding the list.

        Availability arrives from the row's own check box, so Qt is still
        inside that item's signal. Rebuilding here would destroy the very
        item mid-emit, so this updates the text and check state instead.
        """
        matching = [row for row in self._rows if row.agent_id == agent_id]
        if not matching:
            return ()
        updated_by_key = {
            row.source_key: replace(row, available=available, permission=permission)
            for row in matching
        }
        self._rows = tuple(
            updated_by_key.get(candidate.source_key, candidate)
            for candidate in self._rows
        )
        self._loading = True
        try:
            for key, updated in updated_by_key.items():
                item = self._items.get(key)
                if item is None:
                    continue
                item.setText(0, _row_text(updated))
                item.setData(0, CARD_ROLE, _card_data(updated))
                item.setToolTip(0, _row_tooltip(updated))
                item.setCheckState(
                    0, Qt.CheckState.Checked if available else Qt.CheckState.Unchecked
                )
        finally:
            self._loading = False
        return tuple(updated_by_key.values())

    # ---- rendering ---------------------------------------------------------

    def rebuild(self) -> None:
        previous = self._current_source_key
        self._loading = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self._items = {}
        self._groups = {}

        for row in sorted(self._rows, key=lambda row: (row.name.casefold(), row.source_key)):
            item = QTreeWidgetItem(self.tree)
            item.setData(0, _ID_ROLE, row.source_key)
            item.setData(0, _AGENT_ID_ROLE, row.agent_id)
            item.setData(0, CARD_ROLE, _card_data(row))
            item.setText(0, _row_text(row))
            item.setToolTip(0, _row_tooltip(row))
            item.setFlags(_item_flags(row, self._mutations_enabled))
            item.setCheckState(0, Qt.CheckState.Checked if row.available else Qt.CheckState.Unchecked)
            self._items[row.source_key] = item
        self.tabs.setTabText(0, f"Agents ({len(self._rows)})")
        self._filter()

        self.tree.setCurrentItem(self._items.get(previous) or self._first_item())
        self.tree.blockSignals(False)
        self._loading = False
        self.sync_current()

    def _first_item(self) -> QTreeWidgetItem | None:
        return self.tree.topLevelItem(0)

    def sync_current(self) -> None:
        item = self.tree.currentItem()
        raw = item.data(0, _ID_ROLE) if item is not None else None
        current = str(raw) if raw else ""
        row = self.row(current)
        changed = current != self._current_source_key
        self._current_source_key = current
        self._current_id = row.agent_id if row is not None else ""
        if changed or not current:
            self.current_row_changed.emit(current)

    # ---- user intent -------------------------------------------------------

    def _request_create(self, scope: str) -> None:
        if self._mutations_enabled:
            self.create_requested.emit(scope)

    def _on_item_changed(self, item: QTreeWidgetItem, _column: int) -> None:
        if self._loading or not self._mutations_enabled:
            return
        raw = item.data(0, _ID_ROLE)
        if not raw:
            return
        row = self.row(str(raw))
        if row is None:
            return
        available = item.checkState(0) == Qt.CheckState.Checked
        if row.available == available:
            return
        self.availability_changed.emit(row.agent_id, available)


def _item_flags(row: AgentRow, mutations_enabled: bool) -> Qt.ItemFlag:
    """A broken definition cannot be made available, and a running turn freezes all."""
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if row.valid:
        flags |= Qt.ItemFlag.ItemIsDragEnabled
    if row.valid and mutations_enabled:
        flags |= Qt.ItemFlag.ItemIsUserCheckable
    return flags


def _row_text(row: AgentRow) -> str:
    if not row.valid:
        return f"{row.name}   ·   could not be loaded"
    return "\n".join((row.name, row.description, row.model_label))


def _card_data(row: AgentRow) -> dict:
    return dict(name=row.name, purpose=row.description,
                detail=row.model_label if row.valid else "Could not be loaded")


def _row_tooltip(row: AgentRow) -> str:
    if not row.valid:
        return "\n".join(row.errors) or "This definition could not be loaded."
    parts = [row.name, row.description, row.model_label, f"Thinking: {row.thinking_label}",
             f"{row.scope_label} · {row.agent_id}", row.permission.label,
             "Checked: available to Aura. Select to edit settings."]
    return "\n".join(part for part in parts if part)


def source_key(scope: str, agent_id: str) -> str:
    return f"{scope}:{agent_id}"


def _small_button(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(tooltip)
    return button


__all__ = [
    "AGENT_MIME",
    "SCOPE_LABELS",
    "SCOPE_ORDER",
    "AgentLibrary",
    "AgentLibraryTree",
    "AgentRow",
    "source_key",
]
