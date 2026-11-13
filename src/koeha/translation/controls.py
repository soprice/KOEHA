"""Keeps the Live page's connection and prompt selectors in sync with their Settings panels."""
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker

from koeha.core.i18n import tr


class TranslationControls:
    """MainWindow mixin. Keeps the Live page's connection and prompt selectors in sync with their Settings panels.

    Relies on the shell for self.controller, self.page, self.navigate, self.show_error and
    self.update_activity; see koeha.ui.main_window."""

    def refresh_prompt_profiles(self):
        with QSignalBlocker(self.prompt_style):
            self.prompt_style.clear()
            for profile in self.prompt_profiles.profiles:
                self.prompt_style.addItem(profile.name or tr("Unnamed profile"), profile.id)
            self.prompt_style.setCurrentIndex(self.prompt_style.findData(self.prompt_profiles.active_id))
        self.prompt_style.setToolTip(self.prompt_style.currentText())
        self.update_summary()

    def select_prompt_profile(self):
        if hasattr(self, "prompt_profiles"):
            self.prompt_profiles.select_profile(self.prompt_style.currentData())

    def show_prompt_settings(self):
        self.navigate(4)
        self.settings_sections.select_title("Translation prompts")

    def apply_live_translation_style(self, profile):
        try:
            self.controller.select_translation_style(profile)
        except ValueError as error:
            self.show_error(str(error))

    def save_prompt_profiles(self):
        try:
            settings = replace(self.controller.settings, **self.prompt_profiles.settings_patch())
            settings.validate()
            self.controller.save(settings)
            self.apply_live_translation_style(settings.active_prompt_profile)
            self.prompt_profiles.committed()
            self.banner.hide()
            return True
        except Exception as error:
            self.show_error(str(error))
            return False

    def refresh_connection_profiles(self):
        with QSignalBlocker(self.provider):
            self.provider.clear()
            for profile in self.connections.profiles:
                self.provider.addItem(profile.name or tr("Unnamed profile"), profile.id)
            self.provider.setCurrentIndex(self.provider.findData(self.connections.active_id))
        self.provider.setToolTip(self.provider.currentText())
        selected = self.connections.model.currentText().strip()
        models = self.connections.model_ids
        if selected and selected not in models:
            models = (selected, *models)
        combo = self.live_translation_model
        with QSignalBlocker(combo):
            combo.clear()
            for model in models:
                combo.addItem(model, model)
            combo.setCurrentIndex(combo.findData(selected))
        combo.setToolTip(combo.currentText())
        self.update_summary()

    def select_connection_profile(self):
        if hasattr(self, "connections"):
            self.connections.select_profile(self.provider.currentData())

    def select_translation_model(self):
        model = self.live_translation_model.currentData()
        if model:
            self.connections.model.setEditText(model)

    def show_translation_model_discovery(self, message, loading):
        self.live_translation_load.setEnabled(not loading)
        self.live_translation_status.setText(message)
        self.live_translation_status.setAccessibleName(message)

    def save_connection_profiles(self):
        try:
            settings = replace(self.controller.settings, **self.connections.settings_patch())
            settings.validate()
            self.connections.save_credentials()
            self.controller.save(settings)
            self.connections.committed()
            self.banner.hide()
            return True
        except Exception as error:
            self.show_error(str(error))
            return False
