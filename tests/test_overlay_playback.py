from dataclasses import replace
from types import SimpleNamespace

import pytest

from koeha.core.events import Caption
from koeha.core.settings import Settings
from koeha.overlay import playback


def test_delayed_overlay_shows_final_captions_in_order_and_gives_each_reading_time(studio, monkeypatch):
    now = 0.0
    monkeypatch.setattr(playback, "time", SimpleNamespace(monotonic=lambda: now))
    editor = studio.overlay_editor
    editor.timing.setCurrentIndex(editor.timing.findData("delayed"))
    editor.delay.setValue(2.5)
    assert studio.save_overlay_style()
    saved = Settings.load(studio.controller.root / "settings.json")
    assert (saved.overlay_timing, saved.overlay_delay_seconds) == ("delayed", 2.5)
    studio.on_running(True)

    first = Caption(1, 0, 1, "first", True)
    second = Caption(2, 1, 2, "second", True)
    third = Caption(3, 2, 3, "third", True)
    studio.on_caption(replace(first, final=False, translation="draft"))
    studio.on_caption(first)
    studio.on_caption(second)
    studio.on_caption(third)
    studio.on_caption(replace(second, translation="translated second"))
    canvas = studio.overlay.canvas
    assert canvas.original == ""

    now = 2.5
    studio.overlay_playback._advance()
    assert (canvas.original, canvas.translation) == ("first", "")
    now = 3.0
    studio.on_caption(replace(first, translation="translated first"))
    assert (canvas.original, canvas.translation) == ("first", "translated first")

    now = 4.0
    studio.overlay_playback._advance()
    assert canvas.original == "first"
    now = 4.5
    studio.overlay_playback._advance()
    assert (canvas.original, canvas.translation) == ("second", "translated second")
    now = 6.0
    studio.overlay_playback._advance()
    assert canvas.original == "third"

    studio.on_running(False)


def test_switching_to_immediate_releases_delay_and_keeps_late_translation_in_transcript(studio):
    studio.on_running(True)
    editor = studio.overlay_editor
    editor.timing.setCurrentIndex(editor.timing.findData("delayed"))
    first = Caption(1, 0, 1, "first", True)
    studio.on_caption(first)
    studio.on_caption(Caption(2, 1, 2, "latest", False))
    assert studio.overlay_playback.pending

    editor.timing.setCurrentIndex(editor.timing.findData("immediate"))
    assert studio.overlay.canvas.original == "latest"
    assert not studio.overlay_playback.pending and not studio.overlay_playback.timer.isActive()
    studio.on_caption(replace(first, translation="late translation"))
    assert studio.overlay.canvas.original == "latest"
    assert "late translation" in studio.translated.toPlainText()


@pytest.mark.parametrize("timing", ["immediate", "delayed"])
@pytest.mark.parametrize("status", ["Stopping; finishing the last audio window", "Finishing pending translations"])
def test_stopping_clears_and_hides_overlay_without_losing_late_translations(studio, monkeypatch, timing, status):
    now = 0.0
    monkeypatch.setattr(playback, "time", SimpleNamespace(monotonic=lambda: now))
    studio.update_overlay_style({"overlay_timing": timing, "overlay_delay_seconds": 2})
    studio.on_running(True)
    studio.toggle_overlay()
    first = Caption(1, 0, 1, "first", True)
    studio.on_caption(first)
    now = 2.0
    studio.overlay_playback._advance()
    studio.on_caption(Caption(2, 1, 2, "last", True))
    assert studio.overlay.isVisible() and studio.overlay.canvas.original

    studio.on_status(status)
    studio.on_caption(replace(first, translation="late translation"))
    assert not studio.overlay.isVisible() and not studio.overlay_playback.pending
    assert studio.overlay.canvas.original == studio.overlay.canvas.translation == ""
    studio.on_running(False)
    studio.update_overlay_style({"overlay_timing": "immediate"})
    assert not studio.overlay.isVisible() and not studio.overlay_button.isChecked()
    assert studio.overlay.canvas.original == studio.overlay.canvas.translation == ""
    assert not studio.overlay_playback.pending and not studio.overlay_playback.timer.isActive()
    assert "late translation" in studio.translated.toPlainText()
