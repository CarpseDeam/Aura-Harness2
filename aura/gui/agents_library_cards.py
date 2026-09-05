"""Paint compact library cards; inputs are presentation data, never storage."""

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from aura.gui.theme import ACCENT, BG_ALT, BG_RAISED, BORDER, FG, FG_DIM, FG_MUTED

CARD_ROLE = Qt.ItemDataRole.UserRole + 2


class LibraryCardDelegate(QStyledItemDelegate):
    @staticmethod
    def _check_rect(option):
        return QRect(option.rect.left() + 16, option.rect.center().y() - 8, 16, 16)

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and index.data(Qt.ItemDataRole.CheckStateRole) is not None:
            if (event.button() == Qt.MouseButton.LeftButton
                    and index.flags() & Qt.ItemFlag.ItemIsUserCheckable
                    and self._check_rect(option).contains(event.position().toPoint())):
                checked = index.data(Qt.ItemDataRole.CheckStateRole)
                return model.setData(index, 0 if checked else 2, Qt.ItemDataRole.CheckStateRole)
            return False
        return super().editorEvent(event, model, option, index)

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(280, 112 if (index.data(CARD_ROLE) or {}).get("team") else 98)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        data = index.data(CARD_ROLE) or {}
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(option.rect).adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setBrush(QColor(BG_RAISED if selected else BG_ALT))
        painter.setPen(QPen(QColor(ACCENT if selected else BORDER), 1))
        painter.drawRoundedRect(rect, 8, 8)
        preview = data.get("preview")
        preview_width = 190 if preview and rect.width() > 560 else 0
        left = rect.left() + 14
        if index.data(Qt.ItemDataRole.CheckStateRole) is not None:
            check = self._check_rect(option)
            # Match editorEvent's hit area and retain native check-state data
            # for keyboard navigation and accessibility.
            painter.setPen(QPen(QColor(ACCENT if index.data(Qt.ItemDataRole.CheckStateRole) else FG_DIM), 1))
            painter.setBrush(QColor(BG_RAISED))
            painter.drawRoundedRect(QRectF(check), 3, 3)
            if index.data(Qt.ItemDataRole.CheckStateRole):
                painter.fillRect(check.adjusted(4, 4, -4, -4), QColor(ACCENT))
            left = max(left, check.right() + 12)
        width = rect.right() - left - preview_width - 16
        for text, top, size, bold, color in (
            (data.get("name", ""), 10, 14, True, FG),
            (data.get("purpose") or "No purpose added yet", 35, 12, False, FG_DIM),
            (data.get("detail", ""), 61, 12, False, FG_DIM),
        ):
            font = QFont(option.font)
            font.setPixelSize(size)
            font.setBold(bold)
            painter.setFont(font)
            painter.setPen(QColor(color))
            label = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, int(max(0, width)))
            painter.drawText(QRectF(left, rect.top() + top, width, 22), Qt.AlignmentFlag.AlignVCenter, label)
        if preview_width:
            self._preview(painter, QRectF(rect.right() - 186, rect.top() + 12, 172, rect.height() - 24), preview)
        painter.restore()

    @staticmethod
    def _preview(painter, rect, preview):
        points, edges = preview
        if not points:
            return
        x0 = min(p[0] for p in points.values())
        y0 = min(p[1] for p in points.values())
        width = max(p[0] for p in points.values()) - x0 + 196
        height = max(p[1] for p in points.values()) - y0 + 82
        scale = min(rect.width() / width, rect.height() / height)
        painter.save()
        painter.translate(rect.center().x() - width * scale / 2, rect.center().y() - height * scale / 2)
        painter.scale(scale, scale)
        for source, target, helper in edges:
            if source not in points or target not in points:
                continue
            a, b = points[source], points[target]
            painter.setPen(QPen(QColor(FG_MUTED), 5, Qt.PenStyle.DashLine if helper else Qt.PenStyle.SolidLine))
            painter.drawLine(int(a[0] - x0 + 98), int(a[1] - y0 + 34), int(b[0] - x0 + 98), int(b[1] - y0 + 34))
        painter.setPen(QPen(QColor(ACCENT), 4))
        painter.setBrush(QColor(BG_RAISED))
        for x, y in points.values():
            painter.drawRoundedRect(QRectF(x - x0, y - y0, 196, 68), 10, 10)
        painter.restore()
