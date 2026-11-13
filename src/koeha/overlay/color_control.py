"""Compact HEX field and nonblocking native Qt color dialog."""
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget

from koeha.core.i18n import tr


class ColorControl(QWidget):
    colorChanged = Signal(str)

    def __init__(self, name, parent=None):
        super().__init__(parent)
        self._color = '#FFFFFF'
        self._dialog = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.swatch = QPushButton()
        self.swatch.setFixedSize(30, 30)
        self.swatch.setAccessibleName(name + ' color picker')
        self.swatch.clicked.connect(self._pick)
        self.edit = QLineEdit()
        self.edit.setMaxLength(9)
        self.edit.setAccessibleName(name + ' HEX color')
        self.edit.setPlaceholderText('#RRGGBB')
        self.edit.setToolTip('HEX: #RRGGBB or #AARRGGBB')
        self.edit.editingFinished.connect(self._commit)
        row.addWidget(self.swatch)
        row.addWidget(self.edit, 1)
        self.set_color(self._color)

    def set_color(self, value):
        color = QColor(value)
        value = color.name(QColor.NameFormat.HexArgb if color.alpha() != 255 else QColor.NameFormat.HexRgb).upper()
        if self.edit.text() != value and not self.edit.hasFocus():
            self.edit.setText(value)
        if value != self._color or not self.swatch.styleSheet():
            self._color = value
            self.swatch.setStyleSheet(f'background:{value}; border:1px solid #667080; border-radius:5px;')

    def _commit(self):
        text = self.edit.text().strip()
        color = QColor(text)
        if len(text) not in (7, 9) or not text.startswith('#') or not color.isValid():
            self.edit.setText(self._color)
            return
        previous = self._color
        self.set_color(text)
        self.edit.setText(self._color)
        if self._color != previous:
            self.colorChanged.emit(self._color)

    def _pick(self):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        original = self._color
        dialog = self._dialog = QColorDialog(QColor(original), self)
        dialog.setWindowTitle(tr(self.swatch.accessibleName()))
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel)
        def preview(color):
            self.set_color(color.name(QColor.NameFormat.HexArgb))
            self.colorChanged.emit(self._color)
        dialog.currentColorChanged.connect(preview)
        def finished(result):
            if not result:
                preview(QColor(original))
            self._dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()
