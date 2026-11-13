"""The Settings page: categorized preferences and the Save action."""
from dataclasses import replace

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
)

from koeha.audio.segmentation import PHRASE_CAP_SECONDS
from koeha.core.i18n import tr
from koeha.core.settings import SPEECH_VAD
from koeha.models.engines_panel import EnginesPanel
from koeha.preferences.hardware import HardwarePanel
from koeha.preferences.sections import SettingsSections
from koeha.translation.connections_panel import TranslationConnections
from koeha.translation.prompt_profiles_panel import PromptProfiles
from koeha.ui.components import button, card, choices, label


class SettingsPage:
    """MainWindow mixin. The Settings page: categorized preferences and the Save action.

    Relies on the shell for self.controller, self.page, self.navigate, self.show_error and
    self.update_activity; see koeha.ui.main_window."""

    def build_settings(self):
        page, layout = self.page("Settings", "Recognition, translation and session preferences.")
        settings = self.controller.settings
        self.settings_sections = SettingsSections()
        layout.addWidget(self.settings_sections, 1)
        panel, content = card()
        form = QFormLayout()
        self.ui_language = choices([("English", "en"), ("ไทย", "th")], settings.ui_language)
        form.addRow(tr("Interface language"), self.ui_language)
        content.addLayout(form)
        content.addWidget(label("Restart KOEHA to apply the interface language.", "muted"))
        content.addStretch()
        self.settings_sections.add_section("General", panel)
        panel, content = card()
        form = QFormLayout()
        self.chunk_size = self.number(1, 30, settings.chunk_seconds)
        self.pause_size = self.number(.2, 3, settings.pause_seconds)
        self.preview_size = self.number(.4, 5, settings.preview_seconds)
        self.max_size = self.number(1, 30, settings.max_seconds)
        cap_hint = label("Sentence mode caps windows at {seconds:g} seconds. Lower saved values still apply.", "muted")
        cap_hint.setText(cap_hint.text().format(seconds=PHRASE_CAP_SECONDS))
        self.max_size.setAccessibleDescription(cap_hint.text())
        self.threshold = self.number(.0001, .5, settings.voice_threshold, 4)
        self.speech_confidence = self.number(.05, .95, settings.speech_confidence)
        self.vad_profile.currentIndexChanged.connect(self.update_vad_controls)
        self.update_vad_controls()
        for title, widget in [("Chunk length (seconds)", self.chunk_size), ("Speech pause (seconds)", self.pause_size),
                              ("Preview interval (seconds)", self.preview_size), ("Maximum sentence (seconds)", self.max_size),
                              ("Voice threshold", self.threshold),
                              ("Speech confidence", self.speech_confidence)]:
            form.addRow(tr(title), widget)
        content.addLayout(form)
        content.addWidget(cap_hint)
        content.addWidget(label("Music/Vocal uses preset thresholds and may pick up instrumental audio.", "muted"))
        self.vad_setup_button = button("Choose voice detection profile in Live Setup", self.show_vad_setup, "link")
        content.addWidget(self.vad_setup_button)
        content.addStretch()
        self.settings_sections.add_section("Recognition", panel)
        panel, content = card()
        self.connections = TranslationConnections(settings)
        self.connections.saveRequested.connect(self.save_connection_profiles)
        self.connections.profilesChanged.connect(self.refresh_connection_profiles)
        self.connections.modelDiscoveryChanged.connect(self.show_translation_model_discovery)
        self.endpoint = self.connections.endpoint
        self.translation_model = self.connections.model.lineEdit()
        self.api_key = self.connections.api_key
        content.addWidget(self.connections)
        self.refresh_connection_profiles()
        self.drafts = QCheckBox(tr("Translate live previews"))
        self.drafts.setChecked(settings.draft_translation)
        content.addWidget(self.drafts)
        content.addWidget(label("Speech stays on this computer. Translation text is sent to your selected API when enabled.", "muted"))
        content.addStretch()
        self.settings_sections.add_section("Translation API", panel)
        self.prompt_profiles = PromptProfiles(settings)
        self.prompt_profiles.profilesChanged.connect(self.refresh_prompt_profiles)
        self.prompt_profiles.profileSelected.connect(self.apply_live_translation_style)
        self.prompt_profiles.saveRequested.connect(self.save_prompt_profiles)
        self.settings_sections.add_section("Translation prompts", self.prompt_profiles, footer=self.prompt_profiles.footer)
        self.refresh_prompt_profiles()
        panel, content = card()
        self.save_history = QCheckBox(tr("Save finalized captions in Sessions"))
        self.save_history.setChecked(settings.save_history)
        content.addWidget(self.save_history)
        content.addStretch()
        self.settings_sections.add_section("Sessions", panel)
        self.engines_panel = EnginesPanel(self.controller)
        self.settings_sections.add_section("Engines & Frameworks", self.engines_panel)
        self.hardware_panel = HardwarePanel(self.controller.root)
        self.settings_sections.add_section("Hardware", self.hardware_panel)
        self.settings_save_button = button("Save settings", self.save_settings, "primary")
        layout.addWidget(self.settings_save_button)
        self.settings_sections.pages.currentChanged.connect(lambda index: self.settings_save_button.setVisible(
            self.settings_sections.titles[index] not in {"Translation prompts", "Engines & Frameworks", "Hardware"}))
        return page

    def update_vad_controls(self):
        speech = self.vad_profile.currentData() == SPEECH_VAD
        self.threshold.setEnabled(speech)
        self.speech_confidence.setEnabled(speech)

    def number(self, minimum, maximum, value, decimals=2):
        widget = QDoubleSpinBox()
        # Seconds and thresholds always use Western digits and "." regardless of the Windows locale.
        widget.setLocale(QLocale.c())
        widget.lineEdit().setTextMargins(8, 0, 0, 0)  # the inner field ignores stylesheet padding
        widget.setDecimals(decimals)
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        widget.setSingleStep(.1 if decimals == 2 else .001)
        return widget

    def read_settings(self):
        return replace(self.controller.settings, model=self.model.currentData(), mode=self.mode.currentData(),
            source=self.source.currentData(), device=self.device.currentData(),
            device_name=self.device.currentText() if self.device.currentData() is not None else "",
            language=self.language.currentData(),
            target=self.target.currentData(), chunk_seconds=self.chunk_size.value(), pause_seconds=self.pause_size.value(),
            preview_seconds=self.preview_size.value(), max_seconds=self.max_size.value(), voice_threshold=self.threshold.value(),
            speech_confidence=self.speech_confidence.value(),
            vad_profile=self.vad_profile.currentData(),
            **self.connections.settings_patch(),
            **self.prompt_profiles.settings_patch(),
            draft_translation=self.drafts.isChecked(), save_history=self.save_history.isChecked(),
            ui_language=self.ui_language.currentData())

    def save_settings(self):
        try:
            settings = self.read_settings()
            settings.validate()
            self.connections.save_credentials()
            language_changed = self.controller.settings.ui_language != settings.ui_language
            self.controller.save(settings)
            self.connections.committed()
            self.prompt_profiles.committed()
            self.banner.hide()
            self.statusBar().showMessage("Restart KOEHA to apply the interface language." if language_changed
                                        else "Settings saved", 8000)
            return True
        except Exception as error:
            self.show_error(str(error))
            return False
