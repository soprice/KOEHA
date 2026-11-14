"""Dark theme built from a small set of tokens, so every surface, border and state stays consistent."""
from pathlib import Path

from PySide6.QtCore import QEvent, QLocale, QObject, Qt
from PySide6.QtWidgets import QWidget

TOKENS = {
    "bg": "#0d1012",            # window
    "rail": "#0f1316",          # sidebar, status bar
    "surface": "#151a1e",       # cards
    "raised": "#1a2026",        # cards inside cards
    "field": "#10161b",         # inputs
    "border": "#29313a",
    "border_strong": "#3a4450",
    "hover": "#1d252c",
    "selected": "#212c33",
    "text": "#eef3fa",
    "text_2": "#b4c2d3",
    "muted": "#8fa0b4",         # >= 4.5:1 on surface
    "accent": "#a5e5c6",
    "accent_hover": "#bdeed7",
    "accent_pressed": "#8fd6b4",
    "on_accent": "#092a24",
    "accent_soft": "#22342f",
    "accent_line": "#4b7f6c",
    "info": "#a7c9fa",
    "warning": "#f1c881",
    "danger_bg": "#3a2326",
    "danger_line": "#6b3a40",
    "danger_text": "#ffd9d9",
}

STYLE = """
QWidget { background:transparent; color:{text}; font-family:'Segoe UI Variable Text','Segoe UI'; font-size:15px; }
QMainWindow, QWidget#workspace { background:{bg}; }
QLabel { background:transparent; }
QLabel#muted { color:{muted}; }
QLabel#warning { color:{warning}; }
QLabel#caption { color:{muted}; font-size:13px; }
QLabel#heading { font-family:'Segoe UI Variable Display','Segoe UI'; font-size:26px; font-weight:600; letter-spacing:-0.3px; }
QLabel#section { color:{muted}; font-size:12px; font-weight:600; letter-spacing:0.8px; }
QLabel#panelTitle { color:{text_2}; font-size:15px; font-weight:600; }
QLabel#versionBadge { color:{text_2}; background:{raised}; border:1px solid {border}; border-radius:5px; padding:2px 6px; font-size:12px; }
QLabel#state { font-size:13px; font-weight:700; letter-spacing:0.8px; }
QLabel#timer { color:{muted}; font-family:'Cascadia Mono','Consolas'; font-size:13px; }

QFrame#sidebar { background:{rail}; border-right:1px solid {border}; }
QFrame#card { background:{surface}; border:1px solid {border}; border-radius:12px; }
QFrame#inputCard { background:{raised}; border:1px solid {border}; border-radius:10px; }
QFrame#pill { background:{field}; border:1px solid {border}; border-radius:15px; }
QFrame#banner { background:{danger_bg}; border-bottom:1px solid {danger_line}; }
QFrame#banner QLabel { color:{danger_text}; }

QPushButton { background:#1e252c; color:{text}; border:1px solid {border_strong}; border-radius:8px; padding:8px 14px; min-height:16px; }
QPushButton:hover { background:#262f38; border-color:#4a5562; }
QPushButton:pressed { background:#171d22; }
QPushButton:disabled { color:#6f7b89; background:#161b20; border-color:{border}; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus, QDoubleSpinBox:focus { border:2px solid {accent}; }
QPushButton:checked { background:{accent_soft}; border-color:{accent_line}; }

QPushButton#primary { background:{accent}; color:{on_accent}; font-weight:600; border:1px solid {accent_hover}; }
QPushButton#primary:hover { background:{accent_hover}; }
QPushButton#primary:pressed { background:{accent_pressed}; }
QPushButton#primary:disabled { background:#4d6b5e; color:#1c3a31; border-color:#4d6b5e; }
QPushButton#stop { background:{danger_bg}; color:{danger_text}; font-weight:600; border:1px solid {danger_line}; }
QPushButton#stop:hover { background:#47292d; }
QPushButton#stop:pressed { background:#2e1c1f; }

QPushButton#ghost { background:transparent; border:1px solid transparent; color:{text_2}; padding:6px 10px; }
QPushButton#ghost:hover { background:{hover}; color:{text}; }
QPushButton#ghost:pressed { background:#151b20; }
QPushButton#ghost:checked { background:{hover}; color:{text}; border-color:transparent; }
QPushButton#link { background:transparent; border:0; color:{info}; padding:4px 6px; }
QPushButton#link:hover { color:#c7dcfc; text-decoration:underline; }
QPushButton#bannerClose { background:transparent; border:0; color:{danger_text}; padding:0; border-radius:6px; }
QPushButton#bannerClose:hover { background:#4a2c30; }

QFrame#sidebar QPushButton#ghost { padding:0; }
QPushButton#nav { background:transparent; border:0; border-radius:6px; color:{text_2}; text-align:left; padding:0 8px; min-height:36px; max-height:36px; }
QPushButton#nav:hover { background:{hover}; color:{text}; }
QPushButton#nav:pressed { background:#151b20; }
QPushButton#nav:checked { background:{selected}; color:{text}; }

QPushButton#segment { background:{surface}; color:{text_2}; border:1px solid {border_strong}; border-radius:0; padding:6px 14px; }
QPushButton#segment[position="first"] { border-top-left-radius:8px; border-bottom-left-radius:8px; }
QPushButton#segment[position="middle"] { border-left:0; }
QPushButton#segment[position="last"] { border-left:0; border-top-right-radius:8px; border-bottom-right-radius:8px; }
QPushButton#segment:hover { background:{hover}; color:{text}; }
QPushButton#segment:checked { background:{accent_soft}; color:#c9f2de; }

QComboBox, QLineEdit, QDoubleSpinBox { background:{field}; border:1px solid {border_strong}; border-radius:8px; padding:6px 10px; min-height:18px; }
QComboBox:hover, QLineEdit:hover, QDoubleSpinBox:hover { border-color:#4a5562; }
QComboBox:disabled, QLineEdit:disabled, QDoubleSpinBox:disabled { color:#7a8796; background:#12171b; border-color:{border}; }
/* Qt reserves the drop-down width separately from the content padding. */
QComboBox { padding-right:6px; combobox-popup:0; }
QComboBox:on { border-color:{accent_line}; }
QComboBox::drop-down { subcontrol-origin:padding; subcontrol-position:center right; width:28px; border:0; background:transparent; }
QComboBox::down-arrow { image:url("{assets}/chevron-down.svg"); width:12px; height:12px; }
QComboBox::down-arrow:on { image:url("{assets}/chevron-up.svg"); }
QComboBoxPrivateContainer { background:transparent; border:0; }
QComboBox QAbstractItemView { background:{raised}; color:{text}; border:1px solid {border_strong}; border-radius:10px; padding:4px; outline:0; }
QComboBox QAbstractItemView::item { min-height:30px; padding:0 10px; border-radius:6px; }
QComboBox QAbstractItemView::item:hover { background:{hover}; }
QComboBox QAbstractItemView::item:selected { background:{accent_soft}; color:#c9f2de; }
QDoubleSpinBox { padding:4px 30px 4px 2px; }
QDoubleSpinBox#translationSampling { padding-bottom:5px; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { subcontrol-origin:border; width:24px; border:0; background:transparent; border-radius:6px; margin:2px 3px; }
QDoubleSpinBox::up-button { subcontrol-position:top right; }
QDoubleSpinBox::down-button { subcontrol-position:bottom right; }
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover { background:{hover}; }
QDoubleSpinBox::up-arrow { image:url("{assets}/chevron-up.svg"); width:10px; height:10px; }
QDoubleSpinBox::down-arrow { image:url("{assets}/chevron-down.svg"); width:10px; height:10px; }
QPlainTextEdit, QTableWidget, QListWidget { background:{surface}; border:1px solid {border}; border-radius:8px; padding:10px; }
QTextBrowser { background:transparent; border:0; padding:2px; }
QHeaderView::section { background:{raised}; color:{text_2}; border:0; padding:9px; }
QListWidget::item { padding:12px; border-bottom:1px solid {border}; border-radius:6px; }
QListWidget::item:hover { background:{hover}; }
QListWidget::item:selected { background:#24342f; color:{text}; }
QListWidget#settingsMenu { background:transparent; border:0; padding:0; font-size:14px; }
QListWidget#settingsMenu::item { padding:10px 8px; border:0; border-radius:6px; }
QSplitter::handle { background:transparent; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar::handle:vertical { background:#323b45; border-radius:3px; min-height:30px; }
QScrollBar::handle:vertical:hover { background:#46525f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }

QProgressBar { background:{raised}; border:0; border-radius:3px; min-height:4px; max-height:5px; color:transparent; }
QProgressBar::chunk { background:{accent}; border-radius:2px; }
QProgressBar#activity { background:{bg}; border-radius:0; min-height:3px; max-height:3px; }
QProgressBar#activity::chunk { background:{info}; border-radius:0; }
QProgressBar#meter { background:{field}; border:1px solid {border}; border-radius:4px; min-height:8px; max-height:8px; }
QProgressBar#meter::chunk { background:{accent}; border-radius:3px; }

QSlider::groove:horizontal { height:4px; background:{border_strong}; border-radius:2px; }
QSlider::sub-page:horizontal { background:{info}; border-radius:2px; }
QSlider::handle:horizontal { background:{text}; width:14px; margin:-5px 0; border-radius:7px; }
QSlider:focus { border:1px solid {accent}; }
QToolTip { background:#222c33; color:{text}; border:1px solid #4a5865; padding:6px 8px; border-radius:6px; }
QCheckBox { spacing:8px; background:transparent; }
QCheckBox::indicator { width:18px; height:18px; border:1px solid #728294; border-radius:5px; background:{field}; }
QCheckBox::indicator:hover { border-color:{accent}; }
QCheckBox::indicator:checked { background:{accent}; border-color:{accent}; }
QCheckBox::indicator:disabled { background:#20272e; border-color:{border_strong}; }
QStatusBar { background:{rail}; border-top:1px solid {border}; color:{muted}; font-size:12px; }
QStatusBar::item { border:0; }
"""
ASSETS = (Path(__file__).parent / "assets").as_posix()
for _name, _value in {**TOKENS, "assets": ASSETS}.items():
    STYLE = STYLE.replace("{" + _name + "}", _value)
STYLE += "QCheckBox::indicator:checked { image: url(\"" + ASSETS + "/check.svg\"); }"


class RoundedPopups(QObject):
    """Give drop-down lists and tooltips real rounded corners.

    Qt shows them as separate square windows, so stylesheet radii alone leave dark corners. Making
    the window frameless and translucent before it is first shown lets the rounded frame show through.
    """

    KINDS = ("QComboBoxPrivateContainer", "QTipLabel")

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Polish and isinstance(obj, QWidget) and obj.isWindow()
                and obj.metaObject().className() in self.KINDS and not obj.property("koeha_rounded")):
            obj.setProperty("koeha_rounded", True)
            obj.setWindowFlags(obj.windowFlags() | Qt.WindowType.FramelessWindowHint
                               | Qt.WindowType.NoDropShadowWindowHint)
            obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        return False


def apply(app):
    # A Thai Windows region format makes Qt show Thai digits (๔.๐๐) in number fields. KOEHA translates its
    # own text, so the Qt locale only formats numbers: keep them in Arabic digits with a "." decimal.
    QLocale.setDefault(QLocale.c())
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if not hasattr(app, "_koeha_popups"):
        app._koeha_popups = RoundedPopups(app)
        app.installEventFilter(app._koeha_popups)
