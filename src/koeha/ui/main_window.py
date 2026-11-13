"""Application shell: window frame, navigation, banner, activity bar and close handling.

Each page lives in its feature package as a MainWindow mixin.
"""
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QProgressBar,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from koeha.core.i18n import tr
from koeha.history.page import SessionsPage
from koeha.live.page import BUSY_PHASES, TIMED_PHASES, LivePage
from koeha.models.page import ModelsPage
from koeha.overlay.captions import Overlay
from koeha.overlay.page import OverlayPage
from koeha.preferences.page import SettingsPage
from koeha.translation.controls import TranslationControls
from koeha.ui.components import (
    Banner,
    label,
    scroll,
)
from koeha.ui.motion import MEDIUM, fade_in, set_progress
from koeha.ui.sidebar import Sidebar


class MainWindow(LivePage, ModelsPage, OverlayPage, SessionsPage, TranslationControls, SettingsPage, QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.active = False
        self.pending_close = set()
        self.activity_jobs = set()
        self.phase = "READY"
        self.status_text = "Ready"
        self.phase_started = time.monotonic()
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.show_elapsed)
        self.overlay = Overlay(controller.settings)
        self.overlay_save_timer = QTimer(self)
        self.overlay_save_timer.setSingleShot(True)
        self.overlay_save_timer.setInterval(350)
        self.overlay_save_timer.timeout.connect(self.save_overlay_style)
        self.lines = {}
        self.stability = {}
        self.model_controls = {}
        self.setWindowTitle("KOEHA — Live Speech & Translation Studio")
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "assets/koeha.ico")))
        self.resize(1220, 840)
        self.setMinimumSize(920, 650)
        container = QWidget()
        root = QHBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = Sidebar(
            [("Live", "live"), ("Models", "intelligence"), ("Overlay", "overlay"), ("Sessions", "history")],
            [("Settings", "settings")],
            collapsed=controller.settings.sidebar_collapsed)
        self.sidebar.navigated.connect(self.navigate)
        self.sidebar.collapsedChanged.connect(self.remember_sidebar)
        self.nav_buttons = self.sidebar.buttons
        self.pages = QStackedWidget()
        root.addWidget(self.sidebar)
        main = QWidget()
        main.setObjectName("workspace")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        # One thin bar for any background work, so progress is visible from every page.
        self.activity = QProgressBar()
        self.activity.setObjectName("activity")
        self.activity.setTextVisible(False)
        self.activity.setFixedHeight(3)
        self.activity.setAccessibleName(tr("Background activity"))
        self.activity.hide()
        main_layout.addWidget(self.activity)
        self.banner = Banner()
        main_layout.addWidget(self.banner)
        main_layout.addWidget(self.pages)
        root.addWidget(main, 1)
        self.setCentralWidget(container)
        for builder in [self.build_live, self.build_models, self.build_overlay, self.build_sessions]:
            self.pages.addWidget(scroll(builder()))
        # Settings categories scroll internally, keeping their navigation and Save action visible.
        self.pages.addWidget(self.build_settings())
        self.statusBar().showMessage(tr("Ready"))
        self.shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.shortcut.activated.connect(self.toggle_live)
        # Keyboard toggles are instant: an action repeated often should never wait for motion.
        self.sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.sidebar_shortcut.activated.connect(lambda: self.sidebar.set_collapsed(not self.sidebar.collapsed))
        controller.caption.connect(self.on_caption)
        controller.running.connect(self.on_running)
        controller.status.connect(self.on_status)
        controller.error.connect(self.show_error)
        controller.level.connect(self.on_level)
        controller.finished.connect(self.refresh_sessions)
        controller.models_changed.connect(self.refresh_models)
        controller.preparing.connect(self.on_preparing)
        controller.progress.connect(self.on_progress)
        controller.engine_busy.connect(self.on_engine_activity)
        self.navigate(0)
        self.refresh_models()
        self.refresh_sessions()
        self.refresh_devices()
        if controller.settings_warning:
            self.show_error(controller.settings_warning)
        self.set_layout(controller.settings.transcript_layout)
        self.level_widget.hide()
        self.render_empty_states()

    def page(self, title, subtitle):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(12)
        layout.addWidget(label(title, "heading"))
        layout.addWidget(label(subtitle, "muted"))
        layout.addSpacing(4)
        return page, layout

    def navigate(self, index):
        changed = index != self.pages.currentIndex()
        self.pages.setCurrentIndex(index)
        if changed:
            # Page switches happen often, so the fade is short and starts half-visible.
            fade_in(self.pages.currentWidget(), start=.5)
        self.sidebar.select(index)

    def remember_sidebar(self, collapsed):
        self.remember(sidebar_collapsed=collapsed)

    def remember(self, **patch):
        """Persist a view preference without touching unsaved edits on the Settings page."""
        self.controller.settings = replace(self.controller.settings, **patch)
        try:
            self.controller.save(self.controller.settings)
        except Exception as error:
            self.show_error(str(error))

    def update_activity(self):
        busy = self.phase in BUSY_PHASES or bool(self.activity_jobs)
        if busy:
            set_progress(self.activity, None)
        if busy and self.activity.isHidden():
            self.activity.show()
            fade_in(self.activity)
        elif not busy:
            self.activity.hide()
        if self.phase in TIMED_PHASES:
            self.elapsed_timer.start()
        else:
            self.elapsed_timer.stop()

    def show_error(self, message):
        self.banner.setText(tr(message))
        if self.banner.isHidden():
            self.banner.show()
            fade_in(self.banner, MEDIUM)

    def close_when(self, signal_name):
        if signal_name not in self.pending_close:
            self.pending_close.add(signal_name)
            getattr(self.controller, signal_name).connect(self.close)

    def closeEvent(self, event: QCloseEvent):
        if self.overlay_save_timer.isActive() and not self.save_overlay_style():
            event.ignore()
            return
        # Background work must stop first; close again automatically once it reports completion.
        if self.active:
            self.controller.stop()
            self.on_status("Finishing session before closing")
            self.close_when("finished")
            event.ignore()
            return
        if self.controller.jobs:
            self.controller.shutdown()
            self.on_status("Cancelling model setup; KOEHA closes when it stops")
            self.close_when("models_changed")
            event.ignore()
            return
        self.overlay.close()
        super().closeEvent(event)

    def resizeEvent(self, event):
        # Narrow windows drop secondary detail first: the level meter, then the setup recap.
        if hasattr(self, "level_widget"):
            self.level_widget.setVisible(self.active and self.workspace_width() >= 860)
        super().resizeEvent(event)

    def workspace_width(self):
        return self.width() - self.sidebar.width()

    def showEvent(self, event):
        super().showEvent(event)
        # Split the transcript evenly once real sizes are known; later resizes keep the halves equal.
        if not getattr(self, "transcripts_balanced", False):
            self.transcripts_balanced = True
            QTimer.singleShot(0, lambda: self.transcripts.setSizes([10000, 10000]))


def run_ui(screenshot=None):
    from koeha.app.controller import Controller
    from koeha.core import i18n
    from koeha.ui import theme
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("KOEHA")
    app.setOrganizationName("KOEHA")
    theme.apply(app)
    controller = Controller()
    i18n.LANGUAGE = controller.settings.ui_language
    window = MainWindow(controller)
    window.show()
    if screenshot:
        def capture():
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            if not window.grab().save(str(screenshot)):
                raise RuntimeError("Could not save screenshot")
            app.quit()
        QTimer.singleShot(1200, capture)
    return app.exec()
