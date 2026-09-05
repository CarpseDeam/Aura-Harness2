"""Visibility of the existing workspace pane, without rebuilding its contents."""

from PySide6.QtCore import QEvent, QObject, QTimer


class WorkspaceVisibilityController(QObject):
    """Coordinate the rail with splitter collapse, hide/show, and width restore."""

    def __init__(self, splitter, pane, rail, parent=None):
        super().__init__(parent)
        self._splitter = splitter
        self._pane = pane
        self._rail = rail
        self._index = splitter.indexOf(pane)
        self._last_width = max(420, splitter.sizes()[self._index])
        self._queued = False
        self._dragging = False
        pane.installEventFilter(self)
        self._handle = splitter.handle(self._index)
        self._handle.installEventFilter(self)
        splitter.splitterMoved.connect(self.sync)
        rail.workspaceRequested.connect(self.toggle)
        self.sync()

    def is_visible(self) -> bool:
        return not self._pane.isHidden() and self._splitter.sizes()[self._index] > 0

    def sync(self, *_args) -> None:
        visible = self.is_visible()
        width = self._splitter.sizes()[self._index]
        if visible and width >= 240 and not self._dragging:
            self._last_width = width
        self._rail.set_workspace_visible(visible)

    def toggle(self) -> None:
        if self.is_visible():
            self.sync()
            self._pane.hide()
        else:
            self._pane.show()
            sizes = self._splitter.sizes()
            total = sum(sizes)
            # Keep the project list and a usable chat column. The same pane,
            # file tabs, selection, and execution widgets remain alive.
            available = max(0, total - sizes[0])
            width = min(self._last_width, max(240, available - 280))
            sizes[self._index] = width
            sizes[1] = max(0, available - width)
            self._splitter.setSizes(sizes)
        self.sync()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._handle:
            if event.type() == QEvent.Type.MouseButtonPress:
                self.sync()
                self._dragging = True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._dragging = False
                QTimer.singleShot(0, self.sync)
        if obj is self._pane and event.type() in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Resize):
            # setSizes and direct widget visibility changes have no dedicated
            # splitter signal. Observe after Qt settles its child geometry.
            if not self._queued:
                self._queued = True
                QTimer.singleShot(0, self._settled)
        return False

    def _settled(self) -> None:
        self._queued = False
        self.sync()
