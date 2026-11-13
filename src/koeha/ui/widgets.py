"""Shared compact desktop controls."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class ElidedComboBox(QComboBox):
    """Keep long selections inside the text area, clear of the drop-down arrow."""

    def paintEvent(self, event):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self)
        # The Fusion label adds a two-pixel inset on each side of the edit field.
        option.currentText = self.fontMetrics().elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, max(0, text_rect.width() - 4))
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
