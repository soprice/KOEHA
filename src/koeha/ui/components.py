"""Small Qt building blocks shared by every page."""
import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
)

from koeha.core.i18n import tr
from koeha.ui.widgets import ElidedComboBox


def label(text, kind=None):
    widget = QLabel(tr(text))
    if kind:
        widget.setObjectName(kind)
    widget.setWordWrap(True)
    return widget


def button(text, callback, kind=None):
    widget = QPushButton(tr(text))
    if kind:
        widget.setObjectName(kind)
    widget.clicked.connect(callback)
    return widget


def card(kind="card"):
    widget = QFrame()
    widget.setObjectName(kind)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(7)
    return widget, layout


def choices(items, selected=None):
    combo = ElidedComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(10)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    for title, value in items:
        combo.addItem(tr(title), value)
    index = combo.findData(selected)
    if index >= 0:
        combo.setCurrentIndex(index)
    combo.setToolTip(combo.currentText())
    combo.currentTextChanged.connect(combo.setToolTip)
    return combo


def format_clock(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{seconds:02}"


def empty_html(title, hint=""):
    return (f"<div style='margin-top:48px' align='center'>"
            f"<span style='font-size:17px;color:#d5deea'>{html.escape(tr(title))}</span><br>"
            f"<span style='font-size:14px;color:#8796a8'>{html.escape(tr(hint))}</span></div>")


class ElidedLabel(QLabel):
    """Single line that ends in "…" when space runs out; the full text stays in the tooltip."""

    def __init__(self):
        super().__init__()
        self.full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text):
        self.full_text = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, self.width()))


class Banner(QFrame):
    """Error message with a way out: selectable text plus a dismiss button."""

    def __init__(self):
        super().__init__()
        self.setObjectName("banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 8, 8)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        close = QPushButton("✕")
        close.setObjectName("bannerClose")
        close.setFixedSize(28, 28)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAccessibleName(tr("Dismiss message"))
        close.clicked.connect(self.hide)
        row.addWidget(self.message, 1)
        row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def setText(self, text):
        self.message.setText(text)

    def text(self):
        return self.message.text()


def scroll(page):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(page)
    return area
