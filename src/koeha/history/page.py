"""The Sessions page: browse, export and delete saved transcripts."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTextBrowser,
)

from koeha.history.store import export, local_time
from koeha.ui.components import button


class SessionsPage:
    """MainWindow mixin. The Sessions page: browse, export and delete saved transcripts.

    Relies on the shell for self.controller, self.page, self.navigate, self.show_error and
    self.update_activity; see koeha.ui.main_window."""

    def build_sessions(self):
        page, layout = self.page("Sessions", "Saved transcripts and translations, ordered by audio time.")
        self.session_list = QListWidget()
        self.session_list.currentItemChanged.connect(self.select_session)
        layout.addWidget(self.session_list, 1)
        self.session_text = QTextBrowser()
        layout.addWidget(self.session_text, 2)
        actions = QHBoxLayout()
        actions.addWidget(button("Refresh", self.refresh_sessions))
        actions.addWidget(button("Export session", self.export_session, "primary"))
        actions.addWidget(button("Delete session", self.delete_session))
        actions.addStretch()
        layout.addLayout(actions)
        return page

    def refresh_sessions(self):
        self.session_list.clear()
        for session, created, title in self.controller.history.sessions():
            item = QListWidgetItem(local_time(created) + "  ·  " + title)
            item.setData(Qt.ItemDataRole.UserRole, session)
            self.session_list.addItem(item)

    def select_session(self, item, previous=None):
        if item:
            captions = self.controller.history.captions(item.data(Qt.ItemDataRole.UserRole))
            self.session_text.setPlainText("\n\n".join(c.text + ("\n" + c.translation if c.translation else "") for c in captions))
        else:
            self.session_text.clear()

    def export_dialog(self, captions):
        path, _ = QFileDialog.getSaveFileName(self, "Export subtitles", "captions.srt",
            "SubRip (*.srt);;WebVTT (*.vtt);;Plain text (*.txt);;JSON (*.json)")
        if path:
            try:
                export(captions, Path(path))
                self.statusBar().showMessage("Exported " + path, 5000)
            except Exception as error:
                self.show_error(str(error))

    def export_session(self):
        if item := self.session_list.currentItem():
            self.export_dialog(self.controller.history.captions(item.data(Qt.ItemDataRole.UserRole)))

    def delete_session(self):
        if self.active:
            self.show_error("Stop the active session before deleting saved sessions.")
            return
        if item := self.session_list.currentItem():
            if QMessageBox.question(self, "Delete session", "Delete this saved transcript?") == QMessageBox.StandardButton.Yes:
                self.controller.history.delete(item.data(Qt.ItemDataRole.UserRole))
                self.refresh_sessions()
