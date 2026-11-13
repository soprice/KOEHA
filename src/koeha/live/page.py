"""The Live page: setup controls, session state, level meter and transcript view."""
import html
import time
from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from koeha.audio.capture import devices
from koeha.audio.segmentation import StableText
from koeha.core.i18n import tr
from koeha.core.settings import MUSIC_VOCAL_VAD, SPEECH_VAD
from koeha.models.catalog import MODELS
from koeha.translation.connection_profiles import HTTP_WARNING, uses_remote_http
from koeha.ui.components import (
    ElidedLabel,
    button,
    card,
    choices,
    empty_html,
    format_clock,
    label,
)
from koeha.ui.icons import icon
from koeha.ui.motion import Pulse, fade_in

BUSY_PHASES = {"LOADING", "STOPPING", "WORKING"}


TIMED_PHASES = BUSY_PHASES | {"LIVE"}


PHASE_COLORS = {"READY": "#9aabc0", "LIVE": "#ff7a7a", "LOADING": "#f2c46d",
                "STOPPING": "#f2c46d", "WORKING": "#f2c46d"}
VAD_PROFILE_HELP = (
    "Start with Speech for dialogue. Music/Vocal can recover masked voices and may caption instruments."
)


def phase_for(status):
    """Map the controller's status line to the short state shown beside the live controls."""
    if status == "Ready":
        return "READY"
    if status == "Listening":
        return "LIVE"
    if status.startswith(("Loading", "Warming")):
        return "LOADING"
    if status.startswith(("Stopping", "Finishing", "Cancelling")):
        return "STOPPING"
    return "WORKING"


def merge_caption(previous, caption):
    """Combine recognizer and translator updates for one utterance without regressing either.

    Previews arrive without a translation and draft translations arrive for an older preview,
    so each update keeps the newest text and the latest known translation.
    """
    if previous is None:
        return caption
    if previous.final and not caption.final:
        return None
    if not caption.final and previous.end > caption.end:
        return replace(previous, translation=caption.translation or previous.translation)
    if not caption.translation and not caption.translation_error:
        return replace(caption, translation=previous.translation)
    return caption


class LivePage:
    """MainWindow mixin. The Live page: setup controls, session state, level meter and transcript view.

    Relies on the shell for self.controller, self.page, self.navigate, self.show_error and
    self.update_activity; see koeha.ui.main_window."""

    def build_live(self):
        page, layout = self.page("Live Transcription", "Original speech and translation, without leaving the workspace.")
        settings = self.controller.settings
        controls, controls_layout = card()
        controls_layout.setContentsMargins(16, 14, 16, 14)
        controls_layout.setSpacing(12)
        # Session bar: what is happening now on the left, the one primary action on the right.
        bar = QHBoxLayout()
        bar.setSpacing(12)
        pill = QFrame()
        pill.setObjectName("pill")
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(12, 6, 14, 6)
        pill_layout.setSpacing(8)
        self.state_dot = QLabel("●")
        self.state_dot.setStyleSheet(f"color:{PHASE_COLORS['READY']}; font-size:12px;")
        self.state_pulse = Pulse(self.state_dot)
        self.state_label = QLabel("READY")
        self.state_label.setObjectName("state")
        self.state_time = QLabel("")
        self.state_time.setObjectName("timer")
        for widget in (self.state_dot, self.state_label, self.state_time):
            pill_layout.addWidget(widget)
        bar.addWidget(pill)
        self.level_widget = QWidget()
        level = QHBoxLayout(self.level_widget)
        level.setContentsMargins(0, 0, 0, 0)
        level.setSpacing(8)
        level_icon = QLabel()
        level_icon.setPixmap(icon("audio", "#8796a8").pixmap(16, 16))
        self.meter = QProgressBar()
        self.meter.setObjectName("meter")
        self.meter.setRange(0, 100)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setFixedWidth(110)
        self.meter.setAccessibleName(tr("Input level"))
        self.meter.setToolTip(tr("Input level"))
        level.addWidget(level_icon)
        level.addWidget(self.meter)
        bar.addWidget(self.level_widget)
        self.summary = ElidedLabel()
        self.summary.setObjectName("muted")
        bar.addWidget(self.summary, 20)
        bar.addStretch(1)  # keeps controls at their natural size when the recap is hidden
        pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.mode = choices([("Live Preview", "sentence"), ("Finalized Chunks", "chunk")],
                            settings.mode)
        self.mode.setAccessibleName(tr("Delivery mode"))
        self.mode.setMinimumWidth(200)
        self.mode.setMaximumWidth(240)
        bar.addWidget(self.mode)
        self.setup_button = QPushButton(tr("Setup"))
        self.setup_button.setObjectName("ghost")
        self.setup_button.setCheckable(True)
        self.setup_button.setChecked(True)
        self.setup_button.setIcon(icon("chevron_down", "#b4c2d3"))
        self.setup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup_button.setToolTip(tr("Show or hide input, recognition and translation setup"))
        self.setup_button.toggled.connect(self.show_setup)
        self.setup_button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bar.addWidget(self.setup_button)
        self.start_button = button("Start Live", self.toggle_live, "primary")
        self.start_button.setIcon(icon("live", "#092a24"))
        self.start_button.setToolTip(tr("Start or stop live transcription") + "  (Ctrl+Enter)")
        self.start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_button.setMinimumWidth(132)
        bar.addWidget(self.start_button)
        controls_layout.addLayout(bar)
        # Setup panels: progressive disclosure, folded away automatically while a session runs.
        self.input_controls = QWidget()
        setup = QVBoxLayout(self.input_controls)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.setSpacing(8)
        options = QHBoxLayout()
        options.setSpacing(10)
        self.source = choices([("System audio / loopback", "system"), ("Microphone", "microphone")], settings.source)
        self.device = choices([])
        self.model = choices([(m.name, m.id) for m in MODELS], settings.model)
        self.vad_profile = choices([("Speech (dialogue)", SPEECH_VAD), ("Music/Vocal (singing)", MUSIC_VOCAL_VAD)],
                                   settings.vad_profile)
        self.vad_profile.setAccessibleDescription(tr(VAD_PROFILE_HELP))
        languages = [("Auto detect", "auto"), ("Japanese", "ja"), ("English", "en"), ("Thai", "th"),
                     ("Chinese", "zh"), ("Korean", "ko")]
        self.language = choices(languages, settings.language)
        self.provider = choices([(p.name, p.id) for p in settings.translation_profiles] or [("Default", "default")],
                                settings.translation_profile)
        self.provider.currentIndexChanged.connect(self.select_connection_profile)
        self.target = choices([("No translation", "off"), *languages[1:]], settings.target)
        self.live_translation_model = choices([])
        self.live_translation_model.setPlaceholderText(tr("Select a model"))
        self.live_translation_model.currentIndexChanged.connect(self.select_translation_model)
        self.live_translation_load = button("Load models", lambda: self.connections.load_models(), "ghost")
        self.live_translation_status = ElidedLabel()
        self.live_translation_status.setObjectName("muted")
        self.live_translation_status.setTextFormat(Qt.TextFormat.PlainText)
        for title, glyph, fields in [
                ("INPUT", "audio", [("Capture source", self.source), ("Audio device", self.device)]),
                ("RECOGNITION", "wave", [("ASR model", self.model), ("Spoken language", self.language),
                                         ("Voice detection profile", self.vad_profile)]),
                ("TRANSLATION", "languages", [("Connection profile", self.provider),
                                              ("Target language", self.target),
                                              ("Translation model", self.live_translation_model)])]:
            panel, panel_layout = card("inputCard")
            panel_layout.setSpacing(6)
            header = QHBoxLayout()
            header.setSpacing(6)
            mark = QLabel()
            mark.setPixmap(icon(glyph, "#8796a8").pixmap(14, 14))
            header.addWidget(mark)
            header.addWidget(label(title, "section"), 1)
            panel_layout.addLayout(header)
            for name, widget in fields:
                field_label = label(name, "muted")
                field_label.setBuddy(widget)
                panel_layout.addWidget(field_label)
                widget.setAccessibleName(tr(name))
                widget.setMinimumWidth(120)
                panel_layout.addWidget(widget)
            if title == "TRANSLATION":
                footer = QHBoxLayout()
                footer.addWidget(self.live_translation_status, 1)
                footer.addWidget(self.live_translation_load, alignment=Qt.AlignmentFlag.AlignTop)
                panel_layout.addLayout(footer)
            panel_layout.addStretch()
            options.addWidget(panel, 1)
        setup.addLayout(options)
        setup.addWidget(label(VAD_PROFILE_HELP, "muted"))
        more = QHBoxLayout()
        prompt_label = label("Translation style", "muted")
        prompt_label.setWordWrap(False)
        more.addWidget(prompt_label)
        self.prompt_style = choices([(p.name, p.id) for p in settings.prompt_profiles], settings.prompt_profile)
        self.prompt_style.setAccessibleName(tr("Translation style"))
        prompt_label.setBuddy(self.prompt_style)
        self.prompt_style.setMaximumWidth(300)
        self.prompt_style.currentIndexChanged.connect(self.select_prompt_profile)
        more.addWidget(self.prompt_style, 1)
        self.edit_prompts_button = button("Edit prompts", self.show_prompt_settings, "ghost")
        more.addWidget(self.edit_prompts_button)
        more.addStretch()
        self.advanced_button = button("More settings", lambda: self.navigate(4), "link")
        self.advanced_button.setCursor(Qt.CursorShape.PointingHandCursor)
        more.addWidget(self.advanced_button)
        for combo in (self.source, self.device, self.model, self.language, self.vad_profile, self.target, self.mode):
            combo.currentIndexChanged.connect(self.update_summary)
        self.source.currentIndexChanged.connect(self.refresh_devices)
        controls_layout.addWidget(self.input_controls)
        controls_layout.addLayout(more)
        layout.addWidget(controls)
        self.translation_http_warning = label(HTTP_WARNING, "warning")
        layout.addWidget(self.translation_http_warning)
        # Transcript view: a segmented control, so the current layout is always visible.
        views = QHBoxLayout()
        views.setSpacing(0)
        view_label = label("TRANSCRIPT VIEW", "section")
        view_label.setWordWrap(False)
        views.addWidget(view_label)
        views.addSpacing(12)
        self.layout_buttons = {}
        for index, (mode, title) in enumerate([("dual", "Side by side"), ("stack", "Stacked"),
                                               ("preview", "Original only")]):
            segment = QPushButton(tr(title))
            segment.setObjectName("segment")
            segment.setProperty("position", ["first", "middle", "last"][index])
            segment.setCheckable(True)
            segment.setAutoExclusive(True)
            segment.setCursor(Qt.CursorShape.PointingHandCursor)
            segment.clicked.connect(lambda checked=False, m=mode: self.set_layout(m, remember=True))
            views.addWidget(segment)
            self.layout_buttons[mode] = segment
        views.addStretch()
        self.overlay_button = QPushButton(tr("Floating captions"))
        self.overlay_button.setObjectName("ghost")
        self.overlay_button.setIcon(icon("overlay", "#b4c2d3"))
        self.overlay_button.setCheckable(True)
        self.overlay_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_button.clicked.connect(self.toggle_overlay)
        views.addWidget(self.overlay_button)
        layout.addSpacing(4)
        layout.addLayout(views)
        self.transcripts = QSplitter(Qt.Orientation.Horizontal)
        self.transcripts.setHandleWidth(10)
        self.original_panel, self.original = self.transcript_panel("Original transcript")
        self.translated_panel, self.translated = self.transcript_panel("Translation")
        self.transcripts.addWidget(self.original_panel)
        self.transcripts.addWidget(self.translated_panel)
        self.transcripts.setChildrenCollapsible(False)
        self.transcripts.setStretchFactor(0, 1)
        self.transcripts.setStretchFactor(1, 1)
        self.transcripts.setMinimumHeight(270)
        layout.addWidget(self.transcripts, 1)
        self.update_summary()
        return page

    def transcript_panel(self, title):
        panel, layout = card()
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(label(title, "panelTitle"))
        text = QTextBrowser()
        text.setOpenExternalLinks(False)
        text.setMinimumHeight(85)
        text.setAccessibleName(tr(title))
        layout.addWidget(text, 1)
        return panel, text

    def refresh_devices(self):
        self.device.clear()
        self.device.addItem(tr("System default"), None)
        try:
            for device in devices(self.source.currentData()):
                self.device.addItem(device.name, device.index)
            # Device indexes shift when hardware changes; the saved name identifies the device.
            settings = self.controller.settings
            if settings.device is None:
                selected = -1
            elif settings.device_name:
                selected = self.device.findText(settings.device_name, Qt.MatchFlag.MatchExactly)
            else:
                selected = self.device.findData(settings.device)
            if selected > 0:
                self.device.setCurrentIndex(selected)
        except Exception as error:
            self.show_error("Audio devices: " + str(error))

    def toggle_live(self):
        if self.active:
            self.controller.stop()
            self.start_button.setEnabled(False)
            return
        if self.save_settings():
            self.lines.clear()
            self.stability.clear()
            self.controller.start()

    def show_setup(self, visible):
        self.input_controls.setVisible(visible)
        self.setup_button.setIcon(icon("chevron_down" if visible else "chevron_right", "#b4c2d3"))
        self.setup_button.setAccessibleDescription(tr("Expanded") if visible else tr("Collapsed"))
        if visible:
            fade_in(self.input_controls)
        self.update_summary()

    def show_vad_setup(self):
        self.navigate(0)
        self.setup_button.setChecked(True)
        self.vad_profile.setFocus()

    def update_summary(self):
        """One-line recap of the setup, shown while the panels are folded away."""
        if not hasattr(self, "summary"):
            return
        target = self.target.currentData()
        endpoint = (self.connections.endpoint.text() if hasattr(self, "connections")
                    else self.controller.settings.translation_url)
        self.translation_http_warning.setVisible(target != "off" and uses_remote_http(endpoint))
        parts = [self.vad_profile.currentText(), self.source.currentText(), self.model.currentText().split(" · ")[0],
                 self.language.currentText() + (" → " + self.target.currentText() if target != "off" else ""),
                 self.mode.currentText().split(" · ")[0]]
        self.summary.setText("  ·  ".join(parts))
        self.summary.setVisible(self.input_controls.isHidden())

    def set_layout(self, mode, remember=False):
        mode = mode if mode in self.layout_buttons else "dual"
        self.transcripts.setOrientation(Qt.Orientation.Horizontal if mode == "dual" else Qt.Orientation.Vertical)
        self.translated_panel.setVisible(mode != "preview")
        self.layout_buttons[mode].setChecked(True)
        QTimer.singleShot(0, lambda: self.transcripts.setSizes([10000, 10000]))  # equal halves
        if remember:
            self.remember(transcript_layout=mode)

    def on_level(self, value):
        self.meter.setValue(min(100, round(value * 500)))

    def render_empty_states(self):
        """Explain the blank panels: what to do next, or what KOEHA is waiting for."""
        if any(c.text for c in self.lines.values()):
            return
        hints = {
            "READY": ("Waiting for audio", "Press Start Live or Ctrl+Enter to begin"),
            "LOADING": ("Loading recognition model", "The first start can take a minute"),
            "LIVE": ("Listening", "Speak, or play audio on this computer"),
            "STOPPING": ("Finishing", "Processing the last audio"),
        }
        title, hint = hints.get(self.phase, ("Working", ""))
        self.original.setHtml(empty_html(title, hint))
        translation_off = self.target.currentData() == "off"
        self.translated.setHtml(empty_html("Translation is off" if translation_off else "Translation",
                                           "Choose a target language in Setup" if translation_off
                                           else "Finalized translations appear here"))

    def on_running(self, active):
        self.active = active
        self.start_button.setEnabled(True)
        self.start_button.setText(tr("Stop" if active else "Start Live"))
        self.start_button.setIcon(icon("stop" if active else "live", "#ffd9d9" if active else "#092a24"))
        # Stop is a different kind of action, so it gets its own colour rather than reusing the primary.
        self.start_button.setObjectName("stop" if active else "primary")
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.input_controls.setEnabled(not active)
        self.mode.setEnabled(not active)
        # While live the mode is fixed (it is in the recap) and the level meter becomes meaningful.
        self.mode.setVisible(not active)
        self.level_widget.setVisible(active and self.workspace_width() >= 860)
        if active:
            # Fold the setup away so the transcript gets the room; restore the user's choice afterwards.
            self.setup_was_open = self.setup_button.isChecked()
            self.setup_button.setChecked(False)
            self.banner.hide()
            self.lines.clear()
            self.overlay_playback.reset()
            self.show_overlay_caption("", "")
        else:
            self.clear_live_overlay()
            if getattr(self, "setup_was_open", False):
                self.setup_button.setChecked(True)
        self.refresh_models()
        self.render_empty_states()

    def on_status(self, status):
        phase = phase_for(status)
        if phase != self.phase:
            self.phase_started = time.monotonic()
        changed = phase != self.phase
        self.phase, self.status_text = phase, status
        self.state_label.setText(phase)
        self.state_label.setToolTip(tr(status))
        self.state_dot.setStyleSheet(f"color:{PHASE_COLORS[phase]}; font-size:12px;")
        self.state_pulse.set_active(phase != "READY")
        if changed and self.active and phase == "STOPPING":
            self.clear_live_overlay()
        self.show_elapsed()
        self.update_activity()
        if changed:
            self.render_empty_states()

    def show_elapsed(self):
        """Long waits (model loading and stopping) show how long they have taken."""
        message = tr(self.status_text)
        timed = self.phase in TIMED_PHASES
        elapsed = format_clock(time.monotonic() - self.phase_started) if timed else ""
        if timed:
            message += f"  ·  {elapsed}"
        self.state_time.setText(elapsed)
        self.state_time.setVisible(timed)
        self.statusBar().showMessage(message)

    def on_caption(self, incoming):
        caption = merge_caption(self.lines.get(incoming.id), incoming)
        if caption is None:
            return
        self.lines[caption.id] = caption
        if not caption.final:
            self.stability.setdefault(caption.id, StableText()).update(caption.text)
        for old in sorted(self.lines)[:-200]:
            self.lines.pop(old, None)
            self.stability.pop(old, None)
        visible = [self.lines[key] for key in sorted(self.lines)][-10:]
        def block(c, translated=False):
            text = c.translation if translated else c.text
            if translated and c.translation_error:
                text = "Translation failed: " + c.translation_error
            if not text:
                return ""
            color = "#eef3fa" if c.final else "#a5e5c6"
            marker = "" if c.final else " · live preview"
            rendered = html.escape(text)
            if not c.final and not translated:
                stable = self.stability[c.id].stable
                if stable and text.startswith(stable):
                    rendered = f"<span style='color:#eef3fa'>{html.escape(stable)}</span>" + html.escape(text[len(stable):])
            return (f"<p style='margin:0 0 12px 0'>"
                    f"<span style='font-size:12px;color:#8796a8'>{format_clock(c.start)}{tr(marker)}</span><br>"
                    f"<span style='font-size:17px;color:{color}'>{rendered}</span></p>")
        original_html = "".join(block(c) for c in visible)
        translated_html = "".join(block(c, True) for c in visible)
        if not original_html:
            self.render_empty_states()
        else:
            self.original.setHtml(original_html)
            translation_off = self.target.currentData() == "off"
            self.translated.setHtml(translated_html or empty_html(
                "Translation is off" if translation_off else "Translating…",
                "Choose a target language in Setup" if translation_off else "Finalized translations appear here"))
        for text in [self.original, self.translated]:
            text.verticalScrollBar().setValue(text.verticalScrollBar().maximum())
        if visible and self.active and self.phase != "STOPPING":
            last = visible[-1]
            self.overlay_playback.receive(incoming, last)
