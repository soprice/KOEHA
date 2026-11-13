"""Category navigation that collapses to a selector in narrow desktop windows."""
from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from koeha.core.i18n import tr
from koeha.ui.widgets import ElidedComboBox


class SettingsSections(QWidget):
    def __init__(self):
        super().__init__()
        self.titles = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.selector = ElidedComboBox()
        self.selector.setAccessibleName(tr("Settings category"))
        layout.addWidget(self.selector)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.menu = QListWidget()
        self.menu.setObjectName("settingsMenu")
        self.menu.setAccessibleName(tr("Settings category"))
        self.menu.setFixedWidth(172)
        self.menu.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        row.addWidget(self.menu)
        self.pages = QStackedWidget()
        row.addWidget(self.pages, 1)
        layout.addLayout(row, 1)
        self.menu.currentRowChanged.connect(self.select)
        self.selector.currentIndexChanged.connect(self.select)

    def add_section(self, title, widget, *, footer=None):
        self.titles.append(title)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        if footer is None:
            self.pages.addWidget(area)
        else:
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            layout.addWidget(area, 1)
            layout.addWidget(footer)
            self.pages.addWidget(panel)
        self.menu.addItem(tr(title))
        self.menu.item(self.menu.count() - 1).setToolTip(tr(title))
        self.selector.addItem(tr(title))
        if self.pages.count() == 1:
            self.select(0)

    def select(self, index):
        if index < 0:
            return
        with QSignalBlocker(self.menu), QSignalBlocker(self.selector):
            self.menu.setCurrentRow(index)
            self.selector.setCurrentIndex(index)
        self.pages.setCurrentIndex(index)

    def select_title(self, title):
        self.select(self.titles.index(title))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact = self.width() < 900
        self.menu.setVisible(not compact)
        self.selector.setVisible(compact)
