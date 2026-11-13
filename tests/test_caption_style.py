import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest

from koeha.core.settings import Settings
from koeha.overlay.captions import CaptionCanvas
from koeha.overlay.color_control import ColorControl


def count_green(image):
    return sum(image.pixelColor(x, y).green() > 180 and image.pixelColor(x, y).red() < 60
               for y in range(image.height()) for x in range(image.width()))


def test_outline_draws_a_real_colored_stroke(app):
    style = replace(Settings(), source_text_color="#ff0000", source_outline_color="#00ff00",
                    text_outline_enabled=True, text_outline_width=6, text_size=48,
                    panel_opacity=0, frame_width=0, text_shadow=False)
    canvas = CaptionCanvas(style)
    canvas.resize(300, 120)
    canvas.set_caption("TEST")
    outlined = canvas.grab().toImage()
    assert count_green(outlined) > 100
    canvas.apply_settings(replace(style, text_outline_enabled=False))
    assert count_green(canvas.grab().toImage()) == 0
    canvas.close()


def test_color_control_rejects_bad_input_without_changing_style(app):
    color = ColorControl("Original text color")
    changed = []
    color.colorChanged.connect(changed.append)
    color.set_color("#112233")
    color.edit.setText("broken")
    color.edit.editingFinished.emit()
    assert color.edit.text() == "#112233".upper()
    assert changed == []
    color.edit.setText("#80123456")
    color.edit.editingFinished.emit()
    assert changed == ["#80123456"]
    color.close()


def test_legacy_settings_receive_style_defaults_and_invalid_color_is_not_saved(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"mode":"chunk","language":"ja"}')
    settings = Settings.load(path)
    assert settings.text_size == 26 and settings.mode == "chunk"
    with pytest.raises(ValueError, match="Invalid caption color"):
        replace(settings, frame_color="invalid").save(path)
    assert Settings.load(path).mode == "chunk"


def test_long_multilingual_caption_increases_height_without_losing_text(app):
    canvas = CaptionCanvas(Settings(text_size=40))
    original = "こんにちは。今日はどこに行きますか。" * 4
    translated = "วันนี้เราจะเดินทางไปที่ไหนกันดี" * 4
    canvas.set_caption(original, translated)
    assert canvas.heightForWidth(300) > canvas.heightForWidth(900)
    assert canvas.original == original and canvas.translation == translated
    canvas.close()


def test_color_dialog_cancel_restores_the_previous_color(app, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QColorDialog
    class TestDialog(QColorDialog):
        def __init__(self, *args):
            super().__init__(*args)
            self.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog)
    monkeypatch.setattr("koeha.overlay.color_control.QColorDialog", TestDialog)
    color = ColorControl("Translation text color")
    color.set_color("#123456")
    changes = []
    color.colorChanged.connect(changes.append)
    color._pick()
    from PySide6.QtGui import QColor
    color._dialog.setCurrentColor(QColor("#abcdef"))
    color._dialog.reject()
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert changes[-1] == "#123456".upper()
    assert color.edit.text() == "#123456".upper()
    color.close()


def test_style_autosave_preserves_other_settings_and_reopens(studio):
    from koeha.app.controller import Controller
    from koeha.ui.main_window import MainWindow
    editor = studio.overlay_editor
    studio.model.setCurrentIndex(studio.model.findData("fun-asr"))
    editor.colors["source_text_color"].edit.setText("#ffdd44")
    editor.colors["source_text_color"].edit.editingFinished.emit()
    editor.checks["text_outline_enabled"].setChecked(True)
    editor.sliders["text_outline_width"][0].setValue(35)
    QTest.qWait(500)
    stored = Settings.load(studio.controller.root / "settings.json")
    assert stored.source_text_color == "#FFDD44"
    assert stored.text_outline_width == 3.5
    assert stored.model == "qwen3-asr"
    assert studio.overlay.canvas.settings.source_text_color == stored.source_text_color
    reopened = MainWindow(Controller())
    assert reopened.overlay_editor.settings.source_text_color == "#FFDD44"
    assert reopened.overlay.canvas.settings.text_outline_enabled
    reopened.close()


def test_reset_and_presets_update_preview_and_floating_captions(studio):
    editor = studio.overlay_editor
    editor.apply_preset("bare")
    QTest.qWait(30)
    assert studio.overlay.canvas.settings.panel_opacity == 0
    assert editor.preview.settings.text_outline_enabled
    editor.reset()
    QTest.qWait(30)
    assert editor.preview.settings.source_text_color == Settings().source_text_color
    assert studio.overlay.canvas.settings.panel_opacity == Settings().panel_opacity


def test_undoing_an_edit_restores_the_preset_selection(studio):
    editor = studio.overlay_editor
    editor.apply_preset("opaque")
    editor.edit("corner_radius", 9)
    assert editor.settings.subtitle_style == "custom"
    editor.edit("corner_radius", 3)
    assert editor.settings.subtitle_style == "opaque"
    assert editor.presets["opaque"].isChecked()


def test_committing_an_unchanged_color_emits_nothing(app):
    color = ColorControl("Background color")
    color.set_color("#112233")
    changed = []
    color.colorChanged.connect(changed.append)
    color.edit.setText("#112233")
    color.edit.editingFinished.emit()
    assert changed == []
    assert color.edit.text() == "#112233"
    color.close()
