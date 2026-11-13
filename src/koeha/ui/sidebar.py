"""Collapsible navigation rail.

Expanded it shows icon + label; collapsed it keeps icons only (labels move to tooltips) so the
workspace gets the width. A pointer click animates the width with a strong ease-out; the keyboard
shortcut and reduced-motion switch instantly, because repeated keyboard actions should never wait.
"""
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from koeha.core.i18n import tr
from koeha.ui import motion
from koeha.ui.icons import icon

EXPANDED = 144
COLLAPSED = 48
LABEL_SWITCH = 138  # reveal labels only when the narrower rail has room for them
GUTTER = 6
LOGO_SIZE = 22


class Sidebar(QFrame):
    navigated = Signal(int)
    collapsedChanged = Signal(bool)

    def __init__(self, items, bottom_items=(), collapsed=False, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.collapsed = collapsed
        self.items = [*items, *bottom_items]
        self.animation = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(GUTTER, 10, GUTTER, 8)
        layout.setSpacing(2)

        # Expanded: [logo  KOEHA  ⟨collapse⟩]. Collapsed: the logo, with the expand button beneath it.
        self.header = QHBoxLayout()
        self.header.setSpacing(4)
        self.mark = QLabel()
        self.mark.setPixmap(QIcon(str(Path(__file__).parent / "assets/koeha.svg")).pixmap(LOGO_SIZE, LOGO_SIZE))
        self.mark.setFixedSize(LOGO_SIZE, LOGO_SIZE)
        self.mark.setAccessibleName("KOEHA")
        self.brand = QWidget()
        self.brand.setToolTip(tr("Every voice, understood."))
        brand = QVBoxLayout(self.brand)
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(0)
        name = QLabel("<b>KOEHA</b>")
        name.setStyleSheet("font-size:15px;")
        brand.addWidget(name)
        self.toggle = self._toggle_button()
        self.expand = self._toggle_button()
        self.header.addWidget(self.mark)
        self.header.addWidget(self.brand, 1)
        self.header.addWidget(self.toggle)
        layout.addLayout(self.header)
        layout.addSpacing(2)
        layout.addWidget(self.expand, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)

        self.buttons = []
        for index, (title, icon_name) in enumerate(self.items):
            if index == len(items):
                layout.addStretch()
            nav = QPushButton()
            nav.setObjectName("nav")
            nav.setCheckable(True)
            nav.setIcon(icon(icon_name, "#b4c2d3"))
            nav.setIconSize(QSize(18, 18))
            nav.setFixedHeight(36)
            nav.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            nav.setCursor(Qt.CursorShape.PointingHandCursor)
            nav.setAccessibleName(tr(title))
            nav.clicked.connect(lambda checked=False, i=index: self.navigated.emit(i))
            layout.addWidget(nav)
            self.buttons.append(nav)
        if not bottom_items:
            layout.addStretch()
        self._apply_labels(not collapsed)
        self.setFixedWidth(COLLAPSED if collapsed else EXPANDED)

    def _toggle_button(self):
        toggle = QPushButton()
        toggle.setObjectName("ghost")
        toggle.setFixedSize(24, 24)
        toggle.setIconSize(QSize(16, 16))
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.clicked.connect(lambda: self.set_collapsed(not self.collapsed, animate=True))
        return toggle

    def select(self, index):
        for i, nav in enumerate(self.buttons):
            nav.setChecked(i == index)

    def set_collapsed(self, collapsed, animate=False):
        if collapsed == self.collapsed and self.animation is None:
            return
        self.collapsed = collapsed
        target = COLLAPSED if collapsed else EXPANDED
        if self.animation is not None:
            self.animation.stop()
            self.animation = None
        if not animate or not motion.enabled():
            self.setFixedWidth(target)
            self._apply_labels(not collapsed)
        else:
            # Start from the current on-screen width so a second click mid-flight reverses smoothly.
            start = self.width()
            group = QParallelAnimationGroup(self)
            for prop in (b"minimumWidth", b"maximumWidth"):
                animation = QPropertyAnimation(self, prop, group)
                animation.setStartValue(start)
                animation.setEndValue(target)
                animation.setDuration(170 if collapsed else 220)  # leaving is quicker than arriving
                animation.setEasingCurve(QEasingCurve.Type.OutQuint)
                group.addAnimation(animation)
            group.animationAt(0).valueChanged.connect(lambda width: self._apply_labels(width >= LABEL_SWITCH))
            def finished():
                self.setFixedWidth(target)
                self._apply_labels(not collapsed)
                self.animation = None
            group.finished.connect(finished)
            self.animation = group
            group.start()
        self.collapsedChanged.emit(collapsed)

    def _apply_labels(self, expanded):
        if getattr(self, "_expanded_labels", None) == expanded:
            return
        self._expanded_labels = expanded
        self.brand.setVisible(expanded)
        self.toggle.setVisible(expanded)
        self.expand.setVisible(not expanded)
        logo_inset = (COLLAPSED - 2 * GUTTER - LOGO_SIZE) // 2
        self.header.setContentsMargins(0 if expanded else logo_inset, 0, 0, 0)
        for toggle, name, tip in [(self.toggle, "panel_collapse", tr("Collapse sidebar")),
                                  (self.expand, "panel_expand", tr("Expand sidebar"))]:
            toggle.setIcon(icon(name, "#b4c2d3"))
            toggle.setToolTip(f"{tip}  (Ctrl+B)")
            toggle.setAccessibleName(tip)
        for nav, (title, _) in zip(self.buttons, self.items):
            nav.setText((" " + tr(title)) if expanded else "")
            nav.setToolTip("" if expanded else tr(title))
