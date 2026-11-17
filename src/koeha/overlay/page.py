"""The Overlay Studio page and the floating caption window toggle."""
from dataclasses import replace

from PySide6.QtWidgets import (
    QHBoxLayout,
)

from koeha.core.i18n import tr
from koeha.core.settings import OVERLAY_FLOW_LIVE_SINGLE
from koeha.overlay.playback import OverlayPlayback, OverlayView, normalize_flow
from koeha.overlay.studio import OverlayStudio
from koeha.ui.components import button, label


class OverlayPage:
    """MainWindow mixin. The Overlay Studio page and the floating caption window toggle.

    Relies on the shell for self.controller, self.page, self.navigate, self.show_error and
    self.update_activity; see koeha.ui.main_window."""

    def build_overlay(self):
        page, layout = self.page("Overlay Studio", "Floating subtitles above your game, video or workspace.")
        actions = QHBoxLayout()
        actions.addWidget(label("Drag the caption window to move it. Close it here when finished.", "muted"), 1)
        actions.addWidget(button("Show / hide overlay", self.toggle_overlay, "primary"))
        layout.addLayout(actions)
        self.overlay_editor = OverlayStudio(self.controller.settings)
        self.overlay_playback = OverlayPlayback(self.show_overlay_view, **self.overlay_playback_config(), parent=self)
        self.overlay_editor.appearanceChanged.connect(self.update_overlay_style)
        self.overlay.settingChanged.connect(self.overlay_editor.apply_patch)
        self.overlay.hideRequested.connect(self.hide_overlay)
        layout.addWidget(self.overlay_editor, 1)
        return page

    def update_overlay_style(self, patch):
        self.controller.settings = replace(self.controller.settings, **patch)
        self.overlay.apply_settings(self.controller.settings)
        latest = self.lines[max(self.lines)] if self.lines and self.active and self.phase != "STOPPING" else None
        self.overlay_playback.configure(latest=latest, **self.overlay_playback_config())
        self.overlay_save_timer.start()

    def overlay_playback_config(self):
        settings = self.controller.settings
        flow = normalize_flow(settings.overlay_timing)
        delay = 0.0 if flow == OVERLAY_FLOW_LIVE_SINGLE else settings.overlay_delay_seconds
        if settings.overlay_timing == "immediate":
            delay = 0.0
        return dict(
            delay=delay,
            flow=flow,
            live_mode=settings.overlay_live_mode,
            translation_enabled=settings.target != "off",
            draft_enabled=settings.draft_translation,
            wait_seconds=settings.overlay_wait_seconds,
            min_display_seconds=settings.overlay_min_display_seconds,
            auto_hide_seconds=settings.overlay_auto_hide_seconds,
        )

    def overlay_delay(self):
        settings = self.controller.settings
        return settings.overlay_delay_seconds if settings.overlay_timing == "delayed" else None

    def show_overlay_view(self, view):
        if isinstance(view, OverlayView):
            if hasattr(self.overlay, "set_view"):
                self.overlay.set_view(view)
            else:
                self.overlay.set_caption(view.original, view.translation)
            if hasattr(self.overlay_editor, "set_view"):
                self.overlay_editor.set_view(view)
            else:
                self.overlay_editor.set_caption(view.original, view.translation)
            return
        self.show_overlay_caption(view, "")

    def show_overlay_caption(self, original, translation):
        self.overlay.set_caption(original, translation)
        self.overlay_editor.set_caption(original, translation)

    def clear_live_overlay(self):
        self.overlay_playback.reset()
        self.overlay.hide()
        self.overlay_button.setChecked(False)
        self.show_overlay_caption("", "")

    def save_overlay_style(self):
        self.overlay_save_timer.stop()
        try:
            self.controller.save(self.controller.settings)
            self.overlay_editor.save_status.setText(tr("Overlay settings saved"))
            return True
        except Exception as error:
            self.overlay_editor.save_status.setText(tr("Could not save overlay settings"))
            self.show_error(str(error))
            return False

    def hide_overlay(self):
        self.overlay.hide()
        self.overlay_button.setChecked(False)

    def toggle_overlay(self):
        if self.pages.currentIndex() == 2 and not self.lines and not self.overlay.isVisible():
            self.overlay.set_view(self.overlay_editor.preview.view)
        self.overlay.setVisible(not self.overlay.isVisible())
        self.overlay_button.setChecked(self.overlay.isVisible())
