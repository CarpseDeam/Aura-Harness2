"""The rail follows splitter visibility while the original widgets stay alive."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QSplitter, QTextEdit, QVBoxLayout, QWidget

from aura.gui.edge_rails import EdgeTabRail
from aura.gui.workspace_visibility import WorkspaceVisibilityController


def test_workspace_toggle_collapse_and_external_hide_preserve_context():
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(1350, 800)
    layout = QVBoxLayout(host)
    splitter = QSplitter(Qt.Orientation.Horizontal)
    project, chat, workspace = QLineEdit("selected-project"), QTextEdit("Existing chat"), QTextEdit("Unsaved file contents")
    for widget in (project, chat, workspace):
        splitter.addWidget(widget)
    layout.addWidget(splitter)
    rail = EdgeTabRail(host)
    layout.addWidget(rail)
    splitter.setSizes([220, 530, 560])
    controller = WorkspaceVisibilityController(splitter, workspace, rail, host)
    host.show()
    app.processEvents()
    width = splitter.sizes()[2]
    assert rail.workspace_tab.isChecked()
    rail.workspace_tab.click()
    app.processEvents()
    assert not controller.is_visible() and not rail.workspace_tab.isChecked()
    assert rail.workspace_tab.isVisible()
    rail.workspace_tab.click()
    app.processEvents()
    assert abs(splitter.sizes()[2] - width) <= 3
    assert workspace.toPlainText() == "Unsaved file contents"
    assert chat.toPlainText() == "Existing chat" and project.text() == "selected-project"
    assert splitter.widget(2) is workspace
    sizes = splitter.sizes()
    splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
    app.processEvents()
    assert not rail.workspace_tab.isChecked()
    rail.workspace_tab.click()
    app.processEvents()
    assert rail.workspace_tab.isChecked()
    assert abs(splitter.sizes()[2] - width) <= 3
    from PySide6.QtTest import QTest

    # Intermediate widths during a collapse gesture must not replace the
    # usable width from before the drag started.
    handle = splitter.handle(2)
    QTest.mousePress(handle, Qt.MouseButton.LeftButton)
    splitter.setSizes([220, 800, 280])
    app.processEvents()
    splitter.setSizes([220, 1080, 0])
    app.processEvents()
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton)
    app.processEvents()
    rail.workspace_tab.click()
    app.processEvents()
    assert abs(splitter.sizes()[2] - width) <= 3
    workspace.hide()
    app.processEvents()
    assert not rail.workspace_tab.isChecked()
    workspace.show()
    app.processEvents()
    assert rail.workspace_tab.isChecked()
    host.close()
    host.deleteLater()
