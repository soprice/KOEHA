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
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from koeha.core.i18n import tr
from koeha.core.settings import (
    MAX_OVERLAY_DELAY,
    OVERLAY_FLOW_COMMITTED,
    OVERLAY_FLOW_LIVE_SINGLE,
    OVERLAY_FLOW_SMART_DUAL,
    OVERLAY_LIVE_FULL,
    OVERLAY_LIVE_OFF,
    OVERLAY_LIVE_RAW,
    Settings,
)
from koeha.overlay.captions import PRESETS, STYLE_FIELDS, CaptionCanvas, OverlayScene, matching_preset
from koeha.overlay.color_control import ColorControl
from koeha.overlay.playback import OverlayView
from koeha.ui.components import card, choices, label


class OverlayStudio(QWidget):
    appearanceChanged = Signal(dict)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = replace(settings)
        self.colors = {}
        self.sliders = {}
        self.checks = {}
        self.presets = {}
        self.category_buttons = []
        self._showing_sample = True

        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(14)
        self.row.addWidget(self._build_canvas_panel(), 1)
        self.row.addWidget(self._build_inspector_panel())

        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(16)
        self.render_timer.timeout.connect(self._render_preview)
        self.refresh()
        self._apply_sample_preview()

    def _build_canvas_panel(self):
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
        self.preview = CaptionCanvas(self.settings)
        scene.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        scene.addStretch()
        content.addWidget(self.scene, 1)
        hint = QLabel(tr("Preview matches the floating captions."))
        hint.setObjectName("muted")
        hint.setContentsMargins(15, 0, 15, 0)
        hint.setWordWrap(True)
        content.addWidget(hint)
        return canvas

    def _build_inspector_panel(self):
        self.inspector = QFrame()
        self.inspector.setObjectName("card")
        self.inspector.setMinimumHeight(540)
        self.inspector.setMinimumWidth(360)
        self.inspector.setMaximumWidth(385)
        shell = QVBoxLayout(self.inspector)
        shell.setContentsMargins(14, 14, 14, 14)
        shell.setSpacing(10)

        # Segmented category tabs so controls stay grouped instead of one crowded column.
        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        categories = [
            ("Playback", self._build_playback_tab),
            ("Layout", self._build_layout_tab),
            ("Typography", self._build_typography_tab),
        ]
        self.category_pages = QStackedWidget()
        for index, (title, builder) in enumerate(categories):
            btn = QPushButton(tr(title))
            btn.setObjectName("segment")
            btn.setProperty("position", ["first", "middle", "last"][index])
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, i=index: self.select_category(i))
            tabs.addWidget(btn, 1)
            self.category_buttons.append(btn)
            self.category_pages.addWidget(self._scroll_tab(builder()))
        shell.addLayout(tabs)
        shell.addWidget(self.category_pages, 1)

        reset = QPushButton(tr("Reset appearance"))
        reset.clicked.connect(self.reset)
        shell.addWidget(reset)
        self.save_status = QLabel(tr("Changes are saved automatically."))
        self.save_status.setObjectName("muted")
        self.save_status.setWordWrap(True)
        shell.addWidget(self.save_status)
        self.select_category(0)
        return self.inspector

    @staticmethod
    def _scroll_tab(widget):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(widget)
        return area

    @staticmethod
    def _group_card(parent_layout, title):
        box, box_layout = card("inputCard")
        box_layout.setContentsMargins(12, 10, 12, 10)
        box_layout.setSpacing(8)
        header = QLabel(tr(title))
        header.setObjectName("section")
        box_layout.addWidget(header)
        parent_layout.addWidget(box)
        return box_layout

    def _build_playback_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(2, 4, 6, 4)
        layout.setSpacing(10)

        flow = self._group_card(layout, "Subtitle flow")
        self.timing = choices([
            ("Smart Dual · Committed + Live", OVERLAY_FLOW_SMART_DUAL),
            ("Committed Only · In order", OVERLAY_FLOW_COMMITTED),
            ("Live Single · Instant slot", OVERLAY_FLOW_LIVE_SINGLE),
        ], self.settings.overlay_timing)
        self.timing.setAccessibleName(tr("Subtitle flow"))
        self.timing.currentIndexChanged.connect(lambda: self.edit("overlay_timing", self.timing.currentData()))
        flow.addWidget(self.timing)
        self.timing_hint = label("", "muted")
        flow.addWidget(self.timing_hint)

        self.live_mode_label = label("Live preview strip", "muted")
        self.live_mode = choices([
            ("Full · Original + Draft translation", OVERLAY_LIVE_FULL),
            ("Raw · Original only", OVERLAY_LIVE_RAW),
            ("Off · Committed only", OVERLAY_LIVE_OFF),
        ], self.settings.overlay_live_mode)
        self.live_mode.setAccessibleName(tr("Live preview strip"))
        self.live_mode.currentIndexChanged.connect(lambda: self.edit("overlay_live_mode", self.live_mode.currentData()))
        flow.addWidget(self.live_mode_label)
        flow.addWidget(self.live_mode)
        self.add_check("draft_translation", "Translate live previews", parent=flow)
        self.draft_warning = label("Live preview translation is off — Full mode shows raw text only.", "warning")
        flow.addWidget(self.draft_warning)

        opening = self._group_card(layout, "Opening & interaction")
        self.add_check("overlay_auto_open", "Auto-open overlay when Live starts", parent=opening)
        self.add_check("overlay_click_through", "Click-through lock (pass mouse clicks)", parent=opening)

        pacing = self._group_card(layout, "Pacing & timing")
        self.wait_spin, self.wait_label = self._add_spin(
            pacing, "Wait for translation (s)", 0.0, 15.0, 0.5, "overlay_wait_seconds"
        )
        self.min_display_spin, self.min_display_label = self._add_spin(
            pacing, "Minimum reading time (s)", 0.5, 10.0, 0.5, "overlay_min_display_seconds"
        )
        self.delay, self.delay_label = self._add_spin(
            pacing, "Sync delay (s)", 0.0, MAX_OVERLAY_DELAY, 0.5, "overlay_delay_seconds"
        )
        self.auto_hide_spin, self.auto_hide_label = self._add_spin(
            pacing, "Auto-hide on silence (s)", 0.0, 60.0, 1.0, "overlay_auto_hide_seconds", special_zero="Never"
        )
        layout.addStretch()
        return tab

    def _build_layout_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(2, 4, 6, 4)
        layout.setSpacing(10)

        presets_card = self._group_card(layout, "Style presets")
        grid = QGridLayout()
        grid.setSpacing(8)
        for index, (name, preset) in enumerate([
            ("Soft Glass\nTranslucent", "frosted"),
            ("Solid\nHigh contrast", "opaque"),
            ("Minimal\nText-first", "bare"),
        ]):
            item = QPushButton(tr(name))
            item.setCheckable(True)
            item.setMinimumHeight(40)
            item.clicked.connect(lambda checked=False, p=preset: self.apply_preset(p))
            self.presets[preset] = item
            grid.addWidget(item, index // 2, index % 2)
        presets_card.addLayout(grid)

        sizing = self._group_card(layout, "Sizing & display")
        self.add_check("overlay_fit_width", "Fit width to text", parent=sizing)
        self.add_slider("overlay_max_width", "Max width", 300, 1600, parent=sizing)
        self.add_check("original_visible", "Show original line", parent=sizing)
        self.add_check("text_shadow", "Text background shadow", parent=sizing)

        panel = self._group_card(layout, "Background & border")
        self.add_color("background_color", "Background color", parent=panel)
        self.add_slider("panel_opacity", "Background opacity", 0, 100, 100, parent=panel)
        self.add_color("frame_color", "Border color", parent=panel)
        self.add_slider("frame_width", "Border thickness", 0, 60, 10, parent=panel)
        self.add_slider("corner_radius", "Corner radius", 0, 24, parent=panel)
        layout.addStretch()
        return tab

    def _build_typography_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(2, 4, 6, 4)
        layout.setSpacing(10)

        typo = self._group_card(layout, "Typography & colors")
        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont(self.settings.typeface))
        self.font_box.setAccessibleName(tr("Caption font"))
        self.font_box.currentFontChanged.connect(lambda font: self.edit("typeface", font.family()))
        typo.addWidget(self.font_box)
        self.add_slider("text_size", "Font size", 10, 72, parent=typo)
        self.add_color("source_text_color", "Original text color", parent=typo)
        self.add_color("target_text_color", "Translation text color", parent=typo)

        outline = self._group_card(layout, "Text outline")
        self.add_check("text_outline_enabled", "Enable text outline", parent=outline)
        self.add_color("source_outline_color", "Original outline color", parent=outline)
        self.add_color("target_outline_color", "Translation outline color", parent=outline)
        self.add_slider("text_outline_width", "Outline thickness", 0, 60, 10, parent=outline)
        layout.addStretch()
        return tab

    def select_category(self, index):
        self.category_pages.setCurrentIndex(index)
        for i, btn in enumerate(self.category_buttons):
            btn.setChecked(i == index)

    def _add_spin(self, parent, title, low, high, step, key, *, special_zero=None):
        spin = QDoubleSpinBox()
        spin.setLocale(QLocale.c())
        spin.setDecimals(1)
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setKeyboardTracking(False)
        spin.setAccessibleName(tr(title))
        if special_zero:
            spin.setSpecialValueText(tr(special_zero))
        spin.valueChanged.connect(lambda value: self.edit(key, round(value) if key == "overlay_max_width" else value))
        field_label = label(title)
        field_label.setBuddy(spin)
        row = QHBoxLayout()
        row.addWidget(field_label, 1)
        row.addWidget(spin)
        parent.addLayout(row)
        return spin, field_label

    def add_color(self, key, title, *, parent):
        row = QHBoxLayout()
        title_label = QLabel(tr(title))
        title_label.setWordWrap(True)
        row.addWidget(title_label, 1)
        control = ColorControl(tr(title))
        control.setFixedWidth(148)
        control.colorChanged.connect(lambda value: self.edit(key, value))
        row.addWidget(control)
        parent.addLayout(row)
        self.colors[key] = control

    def add_slider(self, key, title, low, high, scale=1, *, parent):
        value_label = QLabel()
        value_label.setObjectName("muted")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setAccessibleName(tr(title))
        slider.valueChanged.connect(lambda value: self.edit(key, value / scale if scale > 1 else value))
        parent.addWidget(value_label)
        parent.addWidget(slider)
        self.sliders[key] = (slider, value_label, title, scale)

    def add_check(self, key, title, *, parent):
        control = QCheckBox(tr(title))
        control.toggled.connect(lambda value: self.edit(key, value))
        parent.addWidget(control)
        self.checks[key] = control

    def sync_settings(self, settings):
        """Reflect external Settings updates (e.g. from Settings page) without triggering saves."""
        self.settings = replace(settings)
        self.refresh()
        self._render_preview()

    def refresh(self):
        flow = self.settings.overlay_timing
        smart_dual = flow == OVERLAY_FLOW_SMART_DUAL
        committed = flow == OVERLAY_FLOW_COMMITTED

        with (
            QSignalBlocker(self.timing),
            QSignalBlocker(self.live_mode),
            QSignalBlocker(self.delay),
            QSignalBlocker(self.wait_spin),
            QSignalBlocker(self.min_display_spin),
            QSignalBlocker(self.auto_hide_spin),
        ):
            idx = self.timing.findData(flow)
            if idx >= 0:
                self.timing.setCurrentIndex(idx)
            live_idx = self.live_mode.findData(self.settings.overlay_live_mode)
            if live_idx >= 0:
                self.live_mode.setCurrentIndex(live_idx)
            self.delay.setValue(self.settings.overlay_delay_seconds)
            self.wait_spin.setValue(self.settings.overlay_wait_seconds)
            self.min_display_spin.setValue(self.settings.overlay_min_display_seconds)
            self.auto_hide_spin.setValue(self.settings.overlay_auto_hide_seconds)

        self.live_mode.setVisible(smart_dual)
        self.live_mode_label.setVisible(smart_dual)
        self.checks["draft_translation"].setVisible(not committed)
        self.draft_warning.setVisible(
            smart_dual and self.settings.overlay_live_mode == OVERLAY_LIVE_FULL and not self.settings.draft_translation
        )
        self.delay.setVisible(flow != OVERLAY_FLOW_LIVE_SINGLE)
        self.delay_label.setVisible(flow != OVERLAY_FLOW_LIVE_SINGLE)

        hints = {
            OVERLAY_FLOW_SMART_DUAL: "Show finished translations in the main box and ongoing speech in a separate live strip.",
            OVERLAY_FLOW_COMMITTED: "Wait for accurate translations and present finalized lines in order.",
            OVERLAY_FLOW_LIVE_SINGLE: "Show the newest words in one box while holding finalized translations long enough to read.",
        }
        self.timing_hint.setText(tr(hints.get(flow, hints[OVERLAY_FLOW_SMART_DUAL])))

        for key, color in self.colors.items():
            color.set_color(getattr(self.settings, key))
            color.setEnabled(self.settings.text_outline_enabled if "outline_color" in key else True)
        for key, (slider, value_label, title, scale) in self.sliders.items():
            value = getattr(self.settings, key)
            with QSignalBlocker(slider):
                slider.setValue(round(value * scale))
            if key == "panel_opacity":
                formatted = f"{round(value * 100)}%"
            elif key == "overlay_max_width":
                title_text = "Max width" if self.settings.overlay_fit_width else "Panel width"
                value_label.setText(tr(title_text) + f" · {round(value)}px")
                continue
            else:
                formatted = f"{value:g}px"
            value_label.setText(tr(title) + " · " + formatted)
            slider.setEnabled(self.settings.text_outline_enabled if key == "text_outline_width" else True)
        for key, control in self.checks.items():
            with QSignalBlocker(control):
                control.setChecked(getattr(self.settings, key))
        for key, control in self.presets.items():
            control.setChecked(self.settings.subtitle_style == key)
        with QSignalBlocker(self.font_box):
            self.font_box.setCurrentFont(QFont(self.settings.typeface))

    def _sample_view(self):
        flow = self.settings.overlay_timing
        live_mode = self.settings.overlay_live_mode
        if flow == OVERLAY_FLOW_SMART_DUAL and live_mode != OVERLAY_LIVE_OFF:
            return OverlayView(
                original=tr("Subtitle style preview"),
                translation=tr("Your translated captions appear here"),
                live_original=tr("Live speech preview…"),
                live_stable=tr("Live speech"),
                live_translation=(
                    tr("Draft translation preview…")
                    if live_mode == OVERLAY_LIVE_FULL and self.settings.draft_translation
                    else ""
                ),
            )
        return OverlayView(
            original=tr("Subtitle style preview"),
            translation=tr("Your translated captions appear here"),
        )

    def _apply_sample_preview(self):
        self._showing_sample = True
        self.preview.set_view(self._sample_view())
        self._update_preview_geometry()

    def _render_preview(self):
        self.preview.apply_settings(self.settings)
        if self._showing_sample:
            self.preview.set_view(self._sample_view())
        self._update_preview_geometry()

    def _update_preview_geometry(self):
        available = max(260, self.scene.width() - 44) if self.scene.width() > 60 else 560
        max_w = max(260, min(self.settings.overlay_max_width, available))
        target = self.preview.content_width(max_w, min_width=240) if self.settings.overlay_fit_width else max_w
        self.preview.setFixedWidth(target)

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
        self.set_view(OverlayView(original=original, translation=translation))

    def set_view(self, view):
        if view == OverlayView() or view.idle_hidden:
            self._apply_sample_preview()
            return
        self._showing_sample = False
        self.preview.set_view(view)
        self._update_preview_geometry()

    def resizeEvent(self, event):
        stacked = self.width() < 780
        self.row.setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.inspector.setMaximumWidth(16777215 if stacked else 385)
        self._update_preview_geometry()
        super().resizeEvent(event)
