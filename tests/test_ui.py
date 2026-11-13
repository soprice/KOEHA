import threading
from dataclasses import replace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from koeha.app.controller import Controller
from koeha.audio.capture import Device
from koeha.core.events import Caption
from koeha.core.settings import Settings
from koeha.live.page import phase_for
from koeha.models.catalog import Progress


def test_navigation_layouts_and_overlay(studio):
    assert [title for title, _ in studio.sidebar.items] == ["Live", "Models", "Overlay", "Sessions", "Settings"]
    assert studio.pages.count() == len(studio.nav_buttons)
    for index, nav in enumerate(studio.nav_buttons):
        nav.click()
        assert studio.pages.currentIndex() == index
    studio.navigate(0)
    studio.advanced_button.click()
    assert studio.pages.currentIndex() == 4 and studio.nav_buttons[4].isChecked()
    assert studio.ui_language.isVisible()
    studio.navigate(0)
    studio.set_layout("dual")
    assert studio.transcripts.orientation() == Qt.Orientation.Horizontal
    studio.set_layout("preview")
    assert studio.translated_panel.isHidden()
    studio.toggle_overlay()
    assert studio.overlay.isVisible()
    studio.toggle_overlay()
    assert not studio.overlay.isVisible()


def test_caption_final_rejects_late_preview(studio):
    studio.on_caption(Caption(1, 0, 2, "日本語", True, "ภาษาไทย"))
    studio.on_caption(Caption(1, 0, 1, "obsolete", False))
    assert "日本語" in studio.original.toPlainText()
    assert "ภาษาไทย" in studio.translated.toPlainText()
    assert "obsolete" not in studio.original.toPlainText()
    assert len(studio.lines) == 1 and studio.lines[1].final


def test_controls_round_trip_and_lock_while_running(studio):
    studio.mode.setCurrentIndex(studio.mode.findData("chunk"))
    studio.chunk_size.setValue(3)
    assert studio.save_settings()
    assert studio.controller.settings.mode == "chunk"
    assert studio.controller.settings.chunk_seconds == 3
    studio.on_running(True)
    assert not studio.input_controls.isEnabled()
    assert not studio.mode.isEnabled()
    studio.on_running(False)
    assert studio.input_controls.isEnabled()


def test_unprepared_model_never_starts_capture(studio, monkeypatch):
    monkeypatch.setattr("koeha.app.controller.is_ready", lambda *_: False)
    studio.toggle_live()
    assert studio.controller.thread is None
    assert not studio.active
    assert "Download" in studio.banner.text()


def test_models_are_sorted_and_only_download_after_a_click(app, tmp_path, monkeypatch):
    from koeha.models.catalog import MODELS
    from koeha.ui.main_window import MainWindow

    monkeypatch.setenv("KOEHA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("koeha.live.page.devices", lambda _: [])
    monkeypatch.setattr("koeha.translation.connections_panel.get_key", lambda _: "")
    monkeypatch.setattr("koeha.translation.connections_panel.save_key", lambda *_: None)
    requested = []
    monkeypatch.setattr(Controller, "prepare", lambda self, model_id: requested.append(model_id))
    window = MainWindow(Controller())
    try:
        names = [window.model.itemText(index) for index in range(window.model.count())]
        assert names == sorted(names, key=str.casefold)
        assert list(window.model_controls) == [m.id for m in MODELS]
        window.navigate(1)
        for model in MODELS:
            window.model.setCurrentIndex(window.model.findData(model.id))
            window.toggle_live()
            assert window.controller.thread is None
            assert "Download" in window.banner.text()
        assert requested == []
        window.model_controls["whisper-large-v3"][0].click()
        assert requested == ["whisper-large-v3"]
    finally:
        window.close()


def test_preview_updates_keep_the_draft_translation(studio):
    studio.on_caption(Caption(1, 0, 1, "こん", False))
    studio.on_caption(Caption(1, 0, 1, "こん", False, "สวัส"))
    studio.on_caption(Caption(1, 0, 2, "こんにちは", False))
    assert "สวัส" in studio.translated.toPlainText()
    studio.on_caption(Caption(1, 0, 1, "こん", False, "สวัสดี"))
    assert "こんにちは" in studio.original.toPlainText()
    assert "สวัสดี" in studio.translated.toPlainText()
    studio.on_caption(Caption(1, 0, 3, "こんにちは。", True))
    assert "สวัสดี" in studio.translated.toPlainText()
    studio.on_caption(Caption(1, 0, 3, "こんにちは。", True, "สวัสดีครับ"))
    assert "สวัสดีครับ" in studio.translated.toPlainText()
    assert studio.lines[1].translation == "สวัสดีครับ"


def test_unreadable_settings_fall_back_to_defaults(app, tmp_path, monkeypatch):
    monkeypatch.setenv("KOEHA_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    controller = Controller()
    assert controller.settings == Settings()
    assert "defaults are in use" in controller.settings_warning
    assert (tmp_path / "settings.invalid.json").read_text(encoding="utf-8") == "{broken"
    assert not (tmp_path / "settings.json").exists()


def test_saved_device_is_found_by_name_after_indexes_shift(studio, monkeypatch):
    studio.controller.settings = replace(studio.controller.settings, device=3, device_name="USB Mic")
    monkeypatch.setattr("koeha.live.page.devices", lambda _: [Device(3, "Other"), Device(5, "USB Mic")])
    studio.refresh_devices()
    assert studio.device.currentData() == 5
    settings = studio.read_settings()
    assert (settings.device, settings.device_name) == (5, "USB Mic")
    monkeypatch.setattr("koeha.live.page.devices", lambda _: [Device(3, "Other")])
    studio.refresh_devices()
    assert studio.device.currentData() is None


def test_overlay_keeps_dragged_position_when_shown_again(studio, app):
    studio.overlay.show()
    app.processEvents()
    target = studio.overlay.pos() + QPoint(-40, -30)
    studio.overlay.move(target)
    studio.overlay.hide()
    studio.overlay.show()
    app.processEvents()
    assert studio.overlay.pos() == target
    studio.overlay.hide()


def test_closing_during_model_setup_cancels_then_closes(studio, app):
    cancel = threading.Event()
    studio.controller.jobs["nemotron"] = cancel
    studio.close()
    app.processEvents()
    assert studio.isVisible()
    assert cancel.is_set()
    studio.controller.jobs.clear()
    studio.controller.models_changed.emit()
    app.processEvents()
    assert not studio.isVisible()


def test_cancelled_setup_is_not_reported_as_an_error(app, tmp_path, monkeypatch):
    monkeypatch.setenv("KOEHA_DATA_DIR", str(tmp_path))
    def cancelled(*_):
        raise InterruptedError("Model setup cancelled")
    monkeypatch.setattr("koeha.app.controller.prepare", cancelled)
    controller = Controller()
    errors, progress = [], []
    controller.error.connect(errors.append)
    controller.progress.connect(lambda _, text: progress.append(text))
    finished = []
    controller.models_changed.connect(lambda: finished.append(True))
    controller.prepare("nemotron")
    for _ in range(200):
        if finished:
            break
        QTest.qWait(10)
    assert finished and not controller.jobs
    assert errors == []
    assert progress and "cancelled" in progress[-1]


def test_model_setup_shows_steps_progress_and_cancel(studio, monkeypatch):
    cancelled = []
    monkeypatch.setattr(studio.controller, "cancel", cancelled.append)
    action, state, bar = studio.model_controls["nemotron"]
    studio.controller.jobs["nemotron"] = threading.Event()
    studio.on_preparing("nemotron", True)
    assert action.text() == "Cancel setup" and not bar.isHidden() and studio.activity.isVisible()
    studio.on_progress("nemotron", Progress("Model files", "model.safetensors (2/5)", "1.00 GB / 2.55 GB", .4, 2, 4))
    assert state.text().startswith("2/4") and "model.safetensors" in state.text()
    assert bar.maximum() == 1000
    studio.on_progress("nemotron", Progress("Inference runtime", "Installing model libraries", "0:42 elapsed", None, 3, 4))
    assert bar.maximum() == 0
    action.click()
    assert cancelled == ["nemotron"]
    studio.on_progress("nemotron", "Setup cancelled; prepare again to resume")
    studio.controller.jobs.clear()
    studio.on_preparing("nemotron", False)
    studio.refresh_models()
    assert bar.isHidden() and not studio.activity.isVisible()
    assert "cancelled" in state.text() and action.text() == "Download"


def test_status_phases_drive_dot_activity_and_elapsed_time(studio):
    assert phase_for("Warming up recognition model") == "LOADING"
    assert phase_for("Finishing pending translations") == "STOPPING"
    studio.on_status("Loading recognition model")
    assert studio.state_label.text() == "LOADING" and studio.activity.isVisible()
    assert studio.activity.maximum() == 0
    studio.phase_started -= 75
    studio.show_elapsed()
    assert studio.statusBar().currentMessage().endswith("1:15")
    studio.on_status("Listening")
    assert studio.state_label.text() == "LIVE" and not studio.activity.isVisible()
    studio.on_status("Ready")
    assert studio.state_label.text() == "READY" and not studio.elapsed_timer.isActive()


def test_sidebar_collapses_to_icons_and_remembers_it(studio, app, monkeypatch):
    from koeha.ui import sidebar
    monkeypatch.setattr("koeha.ui.motion.enabled", lambda: False)
    assert studio.sidebar.width() == sidebar.EXPANDED and studio.nav_buttons[0].text().strip() == "Live"
    studio.sidebar.toggle.click()
    app.processEvents()
    assert studio.sidebar.width() == sidebar.COLLAPSED
    assert studio.nav_buttons[0].text() == "" and studio.nav_buttons[0].toolTip() == "Live"
    assert Settings.load(studio.controller.root / "settings.json").sidebar_collapsed
    studio.sidebar_shortcut.activated.emit()
    assert studio.sidebar.width() == sidebar.EXPANDED and not studio.controller.settings.sidebar_collapsed


def test_sidebar_animation_can_be_reversed_mid_flight(studio, monkeypatch):
    from koeha.ui import sidebar
    monkeypatch.setattr("koeha.ui.motion.enabled", lambda: True)
    studio.sidebar.set_collapsed(True, animate=True)
    QTest.qWait(60)
    studio.sidebar.set_collapsed(False, animate=True)
    QTest.qWait(400)
    assert studio.sidebar.width() == sidebar.EXPANDED and studio.sidebar.animation is None


def test_transcript_layout_is_a_remembered_segmented_choice(studio):
    assert studio.layout_buttons["dual"].isChecked()
    studio.layout_buttons["stack"].click()
    assert studio.transcripts.orientation() == Qt.Orientation.Vertical
    assert studio.layout_buttons["stack"].isChecked() and not studio.layout_buttons["dual"].isChecked()
    assert Settings.load(studio.controller.root / "settings.json").transcript_layout == "stack"


def test_setup_folds_away_while_live_and_comes_back(studio, monkeypatch):
    selections = []
    monkeypatch.setattr(studio.controller, "select_translation_style", lambda profile: selections.append(profile.id))
    assert studio.setup_button.isChecked() and not studio.input_controls.isHidden()
    studio.on_running(True)
    assert studio.input_controls.isHidden() and not studio.summary.isHidden()
    assert studio.prompt_style.isVisible() and studio.prompt_style.isEnabled()
    studio.prompt_style.setCurrentIndex(studio.prompt_style.findData("subtitles"))
    assert selections == ["subtitles"]
    assert studio.start_button.objectName() == "stop"
    studio.on_status("Listening")
    assert "Listening" in studio.original.toPlainText()
    studio.on_running(False)
    assert not studio.input_controls.isHidden() and studio.start_button.objectName() == "primary"


def test_error_banner_can_be_dismissed(studio):
    studio.show_error("Something failed")
    assert studio.banner.isVisible() and studio.banner.text() == "Something failed"
    studio.banner.findChild(QPushButton, "bannerClose").click()
    assert not studio.banner.isVisible()


def test_number_fields_use_western_digits(studio):
    assert studio.chunk_size.text() == "4.00"
    assert studio.threshold.text() == "0.0120"


def test_dropdowns_open_as_rounded_windows(app):
    from PySide6.QtWidgets import QComboBox

    from koeha.ui import theme
    theme.apply(app)  # the app applies the theme before creating any widget
    combo = QComboBox()
    combo.addItems(["Japanese", "Thai"])
    combo.show()
    combo.showPopup()
    app.processEvents()
    popup = combo.view().window()
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert popup.windowFlags() & Qt.WindowType.FramelessWindowHint
    combo.hidePopup()
    combo.close()


def test_theme_keeps_numbers_in_arabic_digits_on_a_thai_system(app):
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QSpinBox

    from koeha.ui import theme
    QLocale.setDefault(QLocale(QLocale.Language.Thai))
    try:
        theme.apply(app)
        box = QSpinBox()
        box.setValue(42)
        assert box.text() == "42"
        assert QLocale().toString(4.5, "f", 2) == "4.50"
    finally:
        QLocale.setDefault(QLocale.c())


def test_models_page_exposes_delete_button_for_installed_models(studio, monkeypatch):
    import json

    from PySide6.QtWidgets import QMessageBox

    from koeha.models.catalog import by_id

    model = by_id("qwen3-asr")
    root = studio.controller.root
    folder = root / "models" / model.id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "weights.gguf").write_bytes(b"x" * 4096)
    (folder / "ready.json").write_text(json.dumps({"revision": model.revision, "files": ["weights.gguf"]}))
    (root / "runtimes/llama").mkdir(parents=True, exist_ok=True)
    (root / "runtimes/llama/llama-server.exe").touch()
    (root / "vad").mkdir(parents=True, exist_ok=True)
    (root / "vad/silero.onnx").touch()

    studio.navigate(1)
    studio.refresh_models()
    action, state, _ = studio.model_controls[model.id]
    delete_btn = studio.model_delete_buttons[model.id]
    assert not delete_btn.isHidden()
    assert action.text() == "Verify / repair"
    assert "Ready" in state.text()

    prompts = []
    def confirm(parent, title, text, *args, **kwargs):
        prompts.append((title, text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    delete_btn.click()
    assert prompts and model.name in prompts[0][1]
    assert not folder.exists()
    assert delete_btn.isHidden()
    assert action.text() == "Download"
    assert state.text() == "Not installed"


def test_setup_and_uninstaller_executables_manage_app_and_downloaded_data(tmp_path):
    import os
    import subprocess
    import sys
    import zipfile
    from pathlib import Path

    if sys.platform != "win32":
        return
    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csc.is_file():
        return
    repo_root = Path(__file__).resolve().parent.parent
    archive = tmp_path / "payload.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("KOEHA/KOEHA.exe", b"stub-app")
        bundle.writestr("KOEHA/_internal/runtime.dll", b"stub-dll")
    inventory = tmp_path / "files.txt"
    inventory.write_text("KOEHA.exe\n_internal/runtime.dll\nUninstall-KOEHA.exe\n", encoding="utf-8")

    setup_exe = tmp_path / "KOEHA-Setup.exe"
    subprocess.run([
        str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", f"/out:{setup_exe}",
        f"/win32icon:{repo_root / 'src/koeha/ui/assets/koeha.ico'}",
        f"/win32manifest:{repo_root / 'scripts/setup.manifest'}",
        f"/resource:{archive},KOEHA.zip",
        f"/resource:{inventory},KOEHA-files.txt",
        "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll",
        "/r:System.Windows.Forms.dll", "/r:System.IO.Compression.dll",
        "/r:System.IO.Compression.FileSystem.dll", str(repo_root / "scripts/setup_stub.cs"),
    ], check=True)

    target_dir = tmp_path / "Shared folder & 100%"
    data_dir = tmp_path / "LocalAppData/KOEHA"
    subprocess.run([str(setup_exe), "--silent", "--no-shortcuts", f"--target={target_dir}"], check=True)
    assert (target_dir / "KOEHA.exe").is_file()
    assert (target_dir / "Uninstall-KOEHA.exe").is_file()

    # Populate downloaded models, runtimes, VAD and settings
    (data_dir / "models/qwen3-asr").mkdir(parents=True)
    (data_dir / "models/qwen3-asr/model.gguf").write_bytes(b"m" * 1024)
    (data_dir / "runtimes/llama").mkdir(parents=True)
    (data_dir / "runtimes/llama/llama-server.exe").write_bytes(b"r" * 1024)
    (data_dir / "vad").mkdir(parents=True)
    (data_dir / "vad/silero.onnx").write_bytes(b"v" * 512)
    (data_dir / "settings.json").write_text("{}", encoding="utf-8")

    shot = tmp_path / "uninstall-ui.png"
    subprocess.run([
        str(setup_exe), "--uninstall", f"--target={target_dir}", f"--data-dir={data_dir}", f"--screenshot={shot}"
    ], check=True)
    assert shot.is_file() and shot.stat().st_size > 1000

    unrelated = target_dir / "_internal/personal.txt"
    unrelated.write_text("Keep this", encoding="utf-8")
    export = data_dir / "export.txt"
    export.write_text("Keep this too", encoding="utf-8")
    uninstall = ["--uninstall", "--silent", "--no-shortcuts", f"--target={target_dir}", f"--data-dir={data_dir}"]
    subprocess.run([str(setup_exe), *uninstall], check=True)
    assert not (target_dir / "KOEHA.exe").exists()
    assert not (target_dir / "_internal/runtime.dll").exists()
    assert unrelated.is_file()
    assert (data_dir / "settings.json").is_file()
    assert (data_dir / "models/qwen3-asr/model.gguf").is_file()

    subprocess.run([str(setup_exe), "--silent", "--no-shortcuts", f"--target={target_dir}"], check=True)
    subprocess.run([
        str(setup_exe), *uninstall, "--remove-models", "--remove-runtimes", "--remove-data"
    ], check=True)
    assert not (data_dir / "models").exists()
    assert not (data_dir / "runtimes").exists()
    assert not (data_dir / "vad").exists()
    assert not (data_dir / "settings.json").exists()
    assert unrelated.is_file() and export.is_file()
