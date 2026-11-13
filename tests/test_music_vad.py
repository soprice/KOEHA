import json
import sys

import numpy as np
import pytest

from koeha.audio import vad
from koeha.audio.segmentation import OVERLAP_BYTES, Segmenter
from koeha.core.settings import MUSIC_VOCAL_VAD, SPEECH_VAD, Settings

VAD_FRAME = bytes(512 * 2)
AUDIBLE_FRAME = np.full(320, 328, dtype="<i2").tobytes()  # RMS ~0.010


def stub_silero(monkeypatch, probabilities):
    probabilities = iter(probabilities)
    inputs = []

    class Session:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, outputs, feeds):
            inputs.append((feeds["state"].copy(), feeds["input"].copy()))
            return np.array([[next(probabilities)]], dtype=np.float32), feeds["state"] + 1

    monkeypatch.setattr(vad.ort, "InferenceSession", Session)
    return inputs


def test_music_vocal_requires_confirmation_before_long_hangover(monkeypatch):
    stub_silero(monkeypatch, [0.30, 0.30, *([0.08] * 3), 0.30, 0.08, 0.30, 0.30, 0.08,
                             *([0.01] * 23), 0.16])
    detector = vad.VoiceDetector("silero.onnx", threshold=0.90, profile=MUSIC_VOCAL_VAD)
    assert [detector(VAD_FRAME) for _ in range(5)] == [True, True, True, True, False]
    assert not detector.confirmed
    assert detector.continuity == 0
    # The tested profile counts onset hits across dips bridged by the short hangover.
    assert all(detector(VAD_FRAME) for _ in range(5))
    assert detector.confirmed
    assert detector.hangover == 22
    assert detector.continuity == 90
    assert all(detector(VAD_FRAME) for _ in range(22))
    assert not detector(VAD_FRAME)
    assert detector.continuity == 89
    assert detector(VAD_FRAME)  # Re-entry accepts 0.16 within the continuity interval.
    assert detector.hangover == 2
    assert not detector.confirmed


@pytest.mark.parametrize("profile, decay", [(SPEECH_VAD, 1.0), (MUSIC_VOCAL_VAD, 0.988)])
def test_vad_decay_and_reset_clear_the_stream(monkeypatch, profile, decay):
    inputs = stub_silero(monkeypatch, [0.01, 0.13, 0.7, 0.7, 0.7, 0.01])
    detector = vad.VoiceDetector("silero.onnx", profile=profile)
    tone = np.full(512, 400, dtype="<i2").tobytes()
    assert detector(tone * 5 + tone[:16])
    assert detector.pending.size == 8
    np.testing.assert_allclose(inputs[2][0], (1 + decay) * decay)
    np.testing.assert_allclose(inputs[3][0], inputs[2][0] + 1)
    np.testing.assert_allclose(inputs[2][1][0, :64], 400 / 32768)
    detector.reset()
    assert not detector.active and not detector.confirmed
    assert (detector.hangover, detector.continuity, detector.onset_streak, detector.probability) == (0, 0, 0, 0)
    assert detector.pending.size == 0
    assert not detector(VAD_FRAME)
    assert not inputs[-1][0].any()
    assert not inputs[-1][1].any()


def test_music_vocal_uses_preset_rms_and_keeps_detector_across_forced_splits(monkeypatch):
    stub_silero(monkeypatch, [0.31] * 185 + [0.08] * 65)
    settings = Settings(vad_profile=MUSIC_VOCAL_VAD, voice_threshold=0.05)
    detector = vad.VoiceDetector("silero.onnx", profile=settings.vad_profile)
    segmenter = Segmenter(settings, detector)
    audio = [window for _ in range(400) for window in segmenter.push(AUDIBLE_FRAME)]
    finals = [window for window in [*audio, *segmenter.flush()] if window.final]
    first, second = finals
    assert first.id + 1 == second.id
    assert first.end == pytest.approx(6.02)
    assert second.start == pytest.approx(first.end)
    assert second.end == pytest.approx(8)
    assert second.pcm[:OVERLAP_BYTES] == first.pcm[-OVERLAP_BYTES:]
    assert len(second.pcm) == OVERLAP_BYTES + round((second.end - second.start) * 16000) * 2
    assert detector.confirmed


def test_music_vocal_short_transient_does_not_emit_caption(monkeypatch):
    stub_silero(monkeypatch, [0.35, 0.35] + [0.03] * 60)
    settings = Settings(vad_profile=MUSIC_VOCAL_VAD)
    detector = vad.VoiceDetector("silero.onnx", profile=settings.vad_profile)
    segmenter = Segmenter(settings, detector)
    assert not [window for _ in range(96) for window in segmenter.push(AUDIBLE_FRAME)]
    assert not segmenter.flush()


def test_music_vocal_settings_preserve_custom_speech_thresholds(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"speech_confidence": 0.25, "voice_threshold": 0.015}))
    legacy = Settings.load(path)
    assert legacy.vad_profile == SPEECH_VAD
    assert legacy.vad_voice_threshold == 0.015
    legacy.vad_profile = MUSIC_VOCAL_VAD
    legacy.save(path)
    music = Settings.load(path)
    assert music.vad_profile == MUSIC_VOCAL_VAD
    assert music.vad_voice_threshold == 0.008
    assert music.speech_confidence == 0.25
    music.vad_profile = SPEECH_VAD
    assert music.vad_voice_threshold == 0.015
    music.vad_profile = "unknown"
    with pytest.raises(ValueError, match="voice detection profile"):
        music.validate()


def test_music_vocal_desktop_selection_persists_and_disables_speech_controls(studio, monkeypatch):
    assert studio.vad_profile.currentData() == SPEECH_VAD
    assert studio.input_controls.isAncestorOf(studio.vad_profile)
    studio.setup_button.setChecked(False)
    studio.navigate(4)
    studio.vad_setup_button.click()
    assert studio.pages.currentIndex() == 0
    assert studio.setup_button.isChecked()
    studio.threshold.setValue(0.015)
    studio.vad_profile.setCurrentIndex(studio.vad_profile.findData(MUSIC_VOCAL_VAD))
    studio.setup_button.setChecked(False)
    assert studio.summary.text().startswith(studio.vad_profile.currentText())
    assert not studio.threshold.isEnabled()
    assert not studio.speech_confidence.isEnabled()
    started_with = []
    monkeypatch.setattr(studio.controller, "start",
                        lambda: started_with.append(studio.controller.settings.vad_profile))
    studio.toggle_live()
    assert started_with == [MUSIC_VOCAL_VAD]
    settings = Settings.load(studio.controller.root / "settings.json")
    assert settings.vad_profile == MUSIC_VOCAL_VAD
    assert settings.voice_threshold == 0.015
    studio.vad_profile.setCurrentIndex(studio.vad_profile.findData(SPEECH_VAD))
    assert studio.threshold.isEnabled()
    assert studio.speech_confidence.isEnabled()


@pytest.mark.parametrize("profile", [SPEECH_VAD, MUSIC_VOCAL_VAD])
def test_transcribe_passes_the_vad_profile(monkeypatch, tmp_path, profile):
    from koeha.__main__ import main

    selected = []

    class Decoder:
        device = ""

        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            pass

    def detector(path, threshold, *, profile):
        selected.append(profile)
        return lambda pcm: False

    argv = ["koeha", "--transcribe", "sample.wav", "--output", str(tmp_path / "captions.srt")]
    if profile == MUSIC_VOCAL_VAD:
        argv += ["--vad-profile", profile]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setenv("KOEHA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("koeha.audio.capture.file_audio", lambda _: [AUDIBLE_FRAME])
    monkeypatch.setattr("koeha.recognition.recognizer.Recognizer", Decoder)
    monkeypatch.setattr(vad, "VoiceDetector", detector)
    assert main() == 0
    assert selected == [profile]


def test_music_vocal_hangover_cannot_hold_window_open_across_idle_gap(monkeypatch):
    from tests.test_pipeline import _drive_capture_into_segmenter

    inputs = stub_silero(monkeypatch, [0.31] * 200)
    settings = Settings(vad_profile=MUSIC_VOCAL_VAD, pause_seconds=1.0)
    detector = vad.VoiceDetector("silero.onnx", profile=settings.vad_profile)
    phases = [
        *[("pre", "pcm", 0.02, AUDIBLE_FRAME) for _ in range(100)],
        *[("gap", "idle", 0.1) for _ in range(12)],
    ]
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, settings, detector)
    assert detector.confirmed
    # Even with Silero probability pinned at 0.31 and hangover active, synthesized zero frames fail the RMS gate.
    gap_finals = [w for phase, w in tagged if phase == "gap" and w.final]
    assert len(gap_finals) == 1 and gap_finals[0].id == 1
    assert gap_finals[0].end == pytest.approx(3.02, abs=0.04)
    assert not splitter.flush()
    assert len(inputs) >= int((2.0 + 1.2) * 16000) // 512
