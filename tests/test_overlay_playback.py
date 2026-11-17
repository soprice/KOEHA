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
    editor.timing.setCurrentIndex(editor.timing.findData("committed"))
    editor.delay.setValue(2.5)
    assert studio.save_overlay_style()
    saved = Settings.load(studio.controller.root / "settings.json")
    assert (saved.overlay_timing, saved.overlay_delay_seconds) == ("committed", 2.5)
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


def test_switching_to_live_single_releases_delay_and_keeps_late_translation_in_transcript(studio):
    studio.on_running(True)
    editor = studio.overlay_editor
    editor.timing.setCurrentIndex(editor.timing.findData("committed"))
    editor.delay.setValue(2.0)
    first = Caption(1, 0, 1, "first", True)
    studio.on_caption(first)
    studio.on_caption(Caption(2, 1, 2, "latest", False))
    assert studio.overlay_playback.pending

    editor.timing.setCurrentIndex(editor.timing.findData("live_single"))
    assert studio.overlay.canvas.original == "latest"
    assert not studio.overlay_playback.pending and not studio.overlay_playback.timer.isActive()
    studio.on_caption(replace(first, translation="late translation"))
    assert studio.overlay.canvas.original == "latest"
    assert "late translation" in studio.translated.toPlainText()


@pytest.mark.parametrize("timing", ["smart_dual", "committed", "live_single"])
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
    studio.update_overlay_style({"overlay_timing": "smart_dual"})
    assert not studio.overlay.isVisible() and not studio.overlay_button.isChecked()
    assert studio.overlay.canvas.original == studio.overlay.canvas.translation == ""
    assert not studio.overlay_playback.pending and not studio.overlay_playback.timer.isActive()
    assert "late translation" in studio.translated.toPlainText()


def test_smart_dual_waits_for_final_translation_and_separates_live_raw_vs_full(monkeypatch):
    now = 0.0
    monkeypatch.setattr(playback, "time", SimpleNamespace(monotonic=lambda: now))
    views = []
    pb = playback.OverlayPlayback(
        views.append,
        delay=0.0,
        flow="smart_dual",
        live_mode="full",
        translation_enabled=True,
        draft_enabled=True,
        wait_seconds=2.5,
        min_display_seconds=1.5,
    )

    # Utterance 1 live preview + draft translation (in Full live strip)
    pb.receive(Caption(1, 0, 1, "消えない胸の傷", False, "ความเจ็บในอก", translation_draft=True, translated_source="消えない胸の傷"))
    assert views[-1].original == ""
    assert views[-1].live_original == "消えない胸の傷"
    assert views[-1].live_translation == "ความเจ็บในอก"
    assert not views[-1].live_translation_stale

    # Preview grows without a new draft yet -> draft marked stale, live_translating=True
    pb.receive(Caption(1, 0, 2, "消えない胸の傷、月が。", False, "ความเจ็บในอก", translation_draft=True, translated_source="消えない胸の傷"))
    assert views[-1].live_translation_stale and views[-1].live_translating

    # Utterance 1 finalizes ASR (still carrying draft translation), waits for final translation
    pb.receive(Caption(1, 0, 3, "消えない胸の傷、月が。", True, "ความเจ็บในอก", translation_draft=True, translated_source="消えない胸の傷"))
    assert pb.pending and views[-1].original == ""

    # Utterance 2 starts immediately -> Live strip switches to 2 while 1 stays in queue
    now = 0.5
    pb.receive(Caption(2, 3, 4, "次の歌詞", False))
    assert views[-1].live_original == "次の歌詞"

    # Utterance 1 final translation arrives -> promotes to main slot without losing it to utterance 2!
    now = 0.8
    pb.receive(Caption(1, 0, 3, "消えない胸の傷、月が。", True, "บาดแผลในอกที่ไม่จางหาย ดวงจันทร์...", translation_draft=False, translated_source="消えない胸の傷、月が。"))
    assert views[-1].original == "消えない胸の傷、月が。"
    assert views[-1].translation == "บาดแผลในอกที่ไม่จางหาย ดวงจันทร์..."
    assert not views[-1].translation_draft
    assert views[-1].live_original == "次の歌詞"

    # Switch live mode to raw -> hides draft translation, keeps live original
    pb.configure(delay=0.0, flow="smart_dual", live_mode="raw")
    assert views[-1].live_original == "次の歌詞" and views[-1].live_translation == ""

    # Switch live mode to off -> clears live strip, keeps committed main
    pb.configure(delay=0.0, flow="smart_dual", live_mode="off")
    assert views[-1].live_original == "" and views[-1].original == "消えない胸の傷、月が。"


def test_smart_dual_clears_live_strip_when_same_caption_promotes_and_handles_wait_cap(monkeypatch):
    now = 0.0
    monkeypatch.setattr(playback, "time", SimpleNamespace(monotonic=lambda: now))
    views = []
    pb = playback.OverlayPlayback(
        views.append,
        delay=0.0,
        flow="smart_dual",
        live_mode="full",
        translation_enabled=True,
        wait_seconds=2.0,
        min_display_seconds=1.5,
    )
    pb.receive(Caption(1, 0, 1, "こんにちは", False, "สวัสดี", translation_draft=True, translated_source="こんにちは"))
    pb.receive(Caption(1, 0, 2, "こんにちは。", True))
    assert views[-1].live_original == "こんにちは。" and views[-1].original == ""

    # Wait cap expires before translation returns -> promotes original, clears live strip (no duplication)
    now = 2.0
    pb._advance()
    assert views[-1].original == "こんにちは。"
    assert views[-1].translation_pending
    assert views[-1].live_original == ""

    # Late final translation arrives -> updates main slot in place
    now = 2.4
    pb.receive(Caption(1, 0, 2, "こんにちは。", True, "สวัสดีครับ", translation_draft=False, translated_source="こんにちは。"))
    assert views[-1].translation == "สวัสดีครับ"
    assert not views[-1].translation_pending


def test_live_single_holds_final_translation_across_next_preview(monkeypatch):
    now = 0.0
    monkeypatch.setattr(playback, "time", SimpleNamespace(monotonic=lambda: now))
    views = []
    pb = playback.OverlayPlayback(
        views.append,
        delay=0.0,
        flow="live_single",
        translation_enabled=True,
        wait_seconds=2.0,
        min_display_seconds=1.5,
    )
    pb.receive(Caption(1, 0, 2, "文一", True))
    assert views[-1].original == "文一"

    # Utterance 2 preview starts at t=0.4 while utterance 1 is still waiting for final translation
    now = 0.4
    pb.receive(Caption(2, 2, 3, "文二の途中", False))
    assert views[-1].original == "文一"

    # Utterance 1 final translation returns at t=0.7 -> MUST display on overlay and hold until 2.2!
    now = 0.7
    pb.receive(Caption(1, 0, 2, "文一", True, "ประโยคที่หนึ่ง", translation_draft=False, translated_source="文一"))
    assert views[-1].original == "文一" and views[-1].translation == "ประโยคที่หนึ่ง"

    now = 1.5
    pb._advance()
    assert views[-1].original == "文一"

    now = 2.2
    pb._advance()
    assert views[-1].original == "文二の途中"


def test_smart_dual_queue_catch_up_caps_backlog_and_idle_hides(monkeypatch):
    now = 0.0
    monkeypatch.setattr(playback, "time", SimpleNamespace(monotonic=lambda: now))
    views = []
    pb = playback.OverlayPlayback(
        views.append,
        delay=0.0,
        flow="smart_dual",
        translation_enabled=False,
        min_display_seconds=1.5,
        auto_hide_seconds=5.0,
    )
    for idx in range(1, 7):
        pb.receive(Caption(idx, idx - 1, idx, f"line {idx}", True))
    assert views[-1].original == "line 1"
    assert len(pb.pending) == playback.MAX_PENDING_CAPTIONS
    assert list(pb.pending) == [3, 4, 5, 6]

    # Compressed reading time (~0.825s) lets backlog catch up faster
    now = 0.9
    pb._advance()
    assert views[-1].original == "line 3"

    # Clear pending and simulate silence timeout
    pb.pending.clear()
    now = 6.0
    pb._on_idle_timeout()
    assert views[-1].idle_hidden is True
