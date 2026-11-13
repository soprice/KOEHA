import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from koeha.app.controller import Controller
from koeha.ui.main_window import MainWindow


@pytest.fixture
def studio(app, tmp_path, monkeypatch):
    monkeypatch.setenv("KOEHA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("koeha.live.page.devices", lambda _: [])
    monkeypatch.setattr("koeha.translation.connections_panel.get_key", lambda _: "")
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda *_: None)
    window = MainWindow(Controller())
    window.show()
    app.processEvents()
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    # Destroy test windows and animation objects while Qt is still alive, before interpreter teardown.
    for window in application.topLevelWidgets():
        window.close()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
