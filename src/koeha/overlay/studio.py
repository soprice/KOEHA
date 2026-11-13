from dataclasses import replace

from PySide6.QtCore import QLocale, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QDoubleSpinBox,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from koeha.core.i18n import tr
from koeha.core.settings import MAX_OVERLAY_DELAY, Settings
from koeha.overlay.captions import PRESETS, STYLE_FIELDS, CaptionCanvas, OverlayScene, matching_preset
from koeha.overlay.color_control import ColorControl
from koeha.ui.components import choices, label


class OverlayStudio(QWidget):
    appearanceChanged = Signal(dict)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = replace(settings)
        self.colors = {}
        self.sliders = {}
        self.checks = {}
        self.presets = {}
        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(14)
        canvas = QFrame()
        canvas.setObjectName("card")
        content = QVBoxLayout(canvas)
        content.setContentsMargins(1, 12, 1, 10)
        title = QLabel(tr("PREVIEW CANVAS"))
        title.setObjectName("section")
        title.setContentsMargins(15, 0, 15, 4)
        content.addWidget(title)
        self.scene = OverlayScene()
        self.scene.setMinimumHeight(400)
        scene = QVBoxLayout(self.scene)
        scene.setContentsMargins(22, 20, 22, 20)
        scene.addStretch(2)
        self.preview = CaptionCanvas(settings)
        self.preview.set_caption(tr("Subtitle style preview"), tr("Your translated captions appear here"))
        scene.addWidget(self.preview)
        scene.addStretch()
        content.addWidget(self.scene, 1)
        hint = QLabel(tr("Preview matches the floating captions."))
        hint.setObjectName("muted")
        hint.setContentsMargins(15, 0, 15, 0)
        hint.setWordWrap(True)
        content.addWidget(hint)
        self.row.addWidget(canvas, 1)
        inspector = QFrame()
        inspector.setObjectName("card")
        self.inspector_layout = QVBoxLayout(inspector)
        self.inspector_layout.setContentsMargins(16, 16, 16, 16)
        self.inspector_layout.setSpacing(12)
        self.section("Caption timing")
        self.timing = choices([("Immediate", "immediate"), ("Delayed", "delayed")], settings.overlay_timing)
        self.timing.setAccessibleName(tr("Caption timing"))
        self.timing.currentIndexChanged.connect(lambda: self.edit("overlay_timing", self.timing.currentData()))
        self.inspector_layout.addWidget(self.timing)
        self.delay = QDoubleSpinBox()
        self.delay.setLocale(QLocale.c())
        self.delay.setDecimals(1)
        self.delay.setRange(0, MAX_OVERLAY_DELAY)
        self.delay.setSingleStep(0.5)
        self.delay.setKeyboardTracking(False)
        self.delay.setAccessibleName(tr("Delay (seconds)"))
        self.delay.valueChanged.connect(lambda seconds: self.edit("overlay_delay_seconds", seconds))
        self.delay_label = label("Delay (seconds)")
        self.delay_label.setBuddy(self.delay)
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.delay_label, 1)
        delay_row.addWidget(self.delay)
        self.inspector_layout.addLayout(delay_row)
        self.timing_hint = label("", "muted")
        self.inspector_layout.addWidget(self.timing_hint)
        self.inspector_layout.addWidget(QLabel("<b>" + tr("Style inspector") + "</b>"))
        grid = QGridLayout()
        for index, (name, preset) in enumerate([
            ("Soft Glass\nTranslucent", "frosted"), ("Solid\nHigh contrast", "opaque"),
            ("Minimal\nText-first", "bare"),
        ]):
            item = QPushButton(tr(name))
            item.setCheckable(True)
            item.setMinimumHeight(38)
            item.clicked.connect(lambda checked=False, p=preset: self.apply_preset(p))
            self.presets[preset] = item
            grid.addWidget(item, index // 2, index % 2)
        self.inspector_layout.addLayout(grid)
        self.section("Typography")
        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont(settings.typeface))
        self.font_box.setAccessibleName(tr("Caption font"))
        self.font_box.currentFontChanged.connect(lambda font: self.edit("typeface", font.family()))
        self.inspector_layout.addWidget(self.font_box)
        self.add_slider("text_size", "Font size", 10, 72)
        self.add_color("source_text_color", "Original text color")
        self.add_color("target_text_color", "Translation text color")
        self.section("Text outline")
        self.add_check("text_outline_enabled", "Enable text outline")
        self.add_color("source_outline_color", "Original outline color")
        self.add_color("target_outline_color", "Translation outline color")
        self.add_slider("text_outline_width", "Outline thickness", 0, 60, 10)
        self.section("Background")
        self.add_color("background_color", "Background color")
        self.add_slider("panel_opacity", "Background opacity", 0, 100, 100)
        self.add_color("frame_color", "Border color")
        self.add_slider("frame_width", "Border thickness", 0, 60, 10)
        self.add_slider("corner_radius", "Corner radius", 0, 24)
        self.section("Display")
        self.add_check("original_visible", "Show original line")
        self.add_check("text_shadow", "Text background shadow")
        reset = QPushButton(tr("Reset appearance"))
        reset.clicked.connect(self.reset)
        self.inspector_layout.addWidget(reset)
        self.save_status = QLabel(tr("Changes are saved automatically."))
        self.save_status.setObjectName("muted")
        self.save_status.setWordWrap(True)
        self.inspector_layout.addWidget(self.save_status)
        self.inspector = QScrollArea()
        self.inspector.setWidgetResizable(True)
        self.inspector.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inspector.setWidget(inspector)
        self.inspector.setMinimumHeight(540)
        self.inspector.setMinimumWidth(335)
        self.inspector.setMaximumWidth(355)
        self.row.addWidget(self.inspector)
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(16)
        self.render_timer.timeout.connect(lambda: self.preview.apply_settings(self.settings))
        self.refresh()

    def section(self, title):
        label = QLabel(tr(title))
        label.setObjectName("section")
        self.inspector_layout.addWidget(label)

    def add_color(self, key, title):
        row = QHBoxLayout()
        title_label = QLabel(tr(title))
        title_label.setWordWrap(True)
        row.addWidget(title_label, 1)
        control = ColorControl(tr(title))
        control.setFixedWidth(152)
        control.colorChanged.connect(lambda value: self.edit(key, value))
        row.addWidget(control)
        self.inspector_layout.addLayout(row)
        self.colors[key] = control

    def add_slider(self, key, title, low, high, scale=1):
        label = QLabel()
        label.setObjectName("muted")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setAccessibleName(tr(title))
        slider.valueChanged.connect(lambda value: self.edit(key, value / scale if scale > 1 else value))
        self.inspector_layout.addWidget(label)
        self.inspector_layout.addWidget(slider)
        self.sliders[key] = (slider, label, title, scale)

    def add_check(self, key, title):
        control = QCheckBox(tr(title))
        control.toggled.connect(lambda value: self.edit(key, value))
        self.inspector_layout.addWidget(control)
        self.checks[key] = control

    def refresh(self):
        delayed = self.settings.overlay_timing == "delayed"
        with QSignalBlocker(self.timing), QSignalBlocker(self.delay):
            self.timing.setCurrentIndex(self.timing.findData(self.settings.overlay_timing))
            self.delay.setValue(self.settings.overlay_delay_seconds)
        self.delay.setVisible(delayed)
        self.delay_label.setVisible(delayed)
        self.timing_hint.setText(tr(
            "Show finalized captions in order after the delay. If translation is still pending, show the original."
            if delayed else "Show the newest caption as soon as it arrives."))
        for key, color in self.colors.items():
            color.set_color(getattr(self.settings, key))
            color.setEnabled(self.settings.text_outline_enabled if "outline_color" in key else True)
        for key, (slider, value_label, title, scale) in self.sliders.items():
            value = getattr(self.settings, key)
            with QSignalBlocker(slider):
                slider.setValue(round(value * scale))
            formatted = f"{round(value * 100)}%" if key == "panel_opacity" else f"{value:g}px"
            value_label.setText(tr(title) + " · " + formatted)
            slider.setEnabled(self.settings.text_outline_enabled if key == "text_outline_width" else True)
        for key, control in self.checks.items():
            with QSignalBlocker(control):
                control.setChecked(getattr(self.settings, key))
        for key, control in self.presets.items():
            control.setChecked(self.settings.subtitle_style == key)
        with QSignalBlocker(self.font_box):
            self.font_box.setCurrentFont(QFont(self.settings.typeface))

    def edit(self, key, value):
        patch = {key: value}
        if any(key in preset for preset in PRESETS.values()):
            patch["subtitle_style"] = matching_preset(replace(self.settings, **patch)) or "custom"
        self.apply_patch(patch)

    def apply_patch(self, patch):
        settings = replace(self.settings, **patch)
        settings.validate()
        self.settings = settings
        self.refresh()
        self.render_timer.start()
        self.save_status.setText(tr("Saving overlay settings…"))
        self.appearanceChanged.emit(patch)

    def apply_preset(self, preset):
        self.apply_patch({**PRESETS[preset], "subtitle_style": preset})

    def reset(self):
        defaults = Settings()
        self.apply_patch({key: getattr(defaults, key) for key in STYLE_FIELDS})

    def set_caption(self, original, translation=""):
        self.preview.set_caption(original, translation)

    def resizeEvent(self, event):
        stacked = self.width() < 780
        self.row.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.inspector.setMaximumWidth(16777215 if stacked else 355)
        super().resizeEvent(event)
