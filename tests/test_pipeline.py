import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from koeha.audio import capture as capture_module
from koeha.audio.capture import capture, frames
from koeha.audio.pipeline import LivePipeline, Overloaded, WindowQueue
from koeha.audio.segmentation import OVERLAP_BYTES, PREROLL_FRAMES, Segmenter, StableText
from koeha.core.events import AudioWindow
from koeha.core.settings import MUSIC_VOCAL_VAD, SPEECH_VAD, Settings


def signal(seconds, level=3000):
    return [np.full(320, level, dtype="<i2").tobytes() for _ in range(round(seconds * 50))]


def test_sentence_preview_and_pause_share_id():
    splitter = Segmenter(Settings())
    events = [w for frame in signal(2) + signal(1.1, 0) for w in splitter.push(frame)]
    assert any(not w.final for w in events)
    assert events[-1].final
    assert len({w.id for w in events}) == 1
    assert events[-1].end == pytest.approx(3, abs=0.03)
    assert not splitter.flush()


def test_chunk_mode_never_emits_preview_and_flushes_tail():
    splitter = Segmenter(replace(Settings(), mode="chunk", chunk_seconds=2))
    events = [w for frame in signal(3) for w in splitter.push(frame)] + splitter.flush()
    assert len(events) == 2
    assert all(w.final for w in events)
    assert events[0].end <= 2.02
    assert events[1].id > events[0].id
    # Second window after a mid-utterance split includes the 0.3 s acoustic overlap lead-in.
    assert len(events[1].pcm) == round(1.0 * 16000 * 2) + OVERLAP_BYTES


def test_silence_and_click_do_not_reach_decoder():
    splitter = Segmenter(Settings())
    assert not [w for frame in signal(2, 0) + signal(.1) + signal(2, 0) for w in splitter.push(frame)]
    assert not splitter.flush()


SOFT = round(0.008 * 32768)  # above half the default voice threshold, below the threshold itself
PREROLL_SECONDS = PREROLL_FRAMES * 0.02


def finals_of(frames, settings=None):
    splitter = Segmenter(settings or Settings())
    return [w for frame in frames for w in splitter.push(frame) if w.final] + splitter.flush()


def lead_in(window):
    """Seconds of audio ahead of the timed start: context for the recognizer, outside the caption."""
    return len(window.pcm) / 32000 - (window.end - window.start)


def test_preroll_keeps_a_soft_onset_across_a_brief_dip():
    # A 200 ms soft onset, a 40 ms stop closure, then full-level speech from 1.24 s.
    frames = signal(1, 0) + signal(.2, SOFT) + signal(.04, 0) + signal(2) + signal(1.2, 0)
    window, = finals_of(frames)
    samples = np.frombuffer(window.pcm, dtype="<i2")
    assert np.count_nonzero(samples == SOFT) == round(.2 * 16000)
    assert lead_in(window) == pytest.approx(PREROLL_SECONDS - .02, abs=0.001)


def test_quiet_lead_in_does_not_move_caption_or_split_timing():
    # Timing matches the behaviour before the lead-in was retained: start at the audible run,
    # final after the full speech pause rather than an earlier breath split.
    frames = signal(1, 0) + signal(.2, SOFT) + signal(.04, 0) + signal(2) + signal(1.2, 0)
    window, = finals_of(frames)
    assert window.start == pytest.approx(1.24, abs=0.001)
    assert window.end == pytest.approx(4.24, abs=0.03)


def test_preroll_is_limited_to_the_latest_400_ms():
    window, = finals_of(signal(2, 0) + signal(2) + signal(1.2, 0))
    assert window.start == pytest.approx(2.0, abs=0.001)
    assert window.end == pytest.approx(5.0, abs=0.03)
    assert lead_in(window) == pytest.approx(PREROLL_SECONDS - .02, abs=0.001)


def test_preroll_never_repeats_audio_from_the_previous_window():
    first, second = finals_of(signal(2) + signal(1.3, 0) + signal(2) + signal(1.2, 0))
    assert first.id != second.id
    assert 0 < lead_in(second) < PREROLL_SECONDS
    assert second.start - lead_in(second) >= first.end - 1e-6


def test_preroll_over_a_music_bed_is_unchanged():
    # Accompaniment held above half the threshold already filled the whole preroll before
    # the ring became unconditional, so singing over music gets the same window as before.
    window, = finals_of(signal(2, SOFT) + signal(2) + signal(1.2, 0))
    assert window.start == pytest.approx(2.02 - PREROLL_SECONDS, abs=0.001)
    assert lead_in(window) == pytest.approx(0, abs=0.001)
    samples = np.frombuffer(window.pcm, dtype="<i2")
    assert np.count_nonzero(samples == SOFT) == round((PREROLL_SECONDS - .02) * 16000)


def test_forced_split_overlap_is_not_extended_by_preroll():
    events = finals_of(signal(3), replace(Settings(), mode="chunk", chunk_seconds=2))
    assert [len(w.pcm) for w in events] == [round(2.0 * 32000), round(1.0 * 32000) + OVERLAP_BYTES]


def marked_audio(frame_count, eof_samples=0):
    """Different PCM at each frame boundary, with all samples above the speech gate."""
    index = np.arange(frame_count * 320 + eof_samples)
    return (2000 + (index // 320) * 19 + index % 320).astype("<i2").tobytes()


@pytest.mark.parametrize(("mode", "configured", "first_end"), [
    ("sentence", 1, 1.0),
    ("sentence", 6, 6.02),
    ("sentence", 15, 6.02),
    ("sentence", 30, 6.02),
    ("chunk", 1, 4.0),
    ("chunk", 30, 4.0),
])
def test_finding_03_baseline_cap_timestamps_and_sample_ownership(mode, configured, first_end):
    source = marked_audio(1035, eof_samples=137)
    splitter = Segmenter(Settings(mode=mode, max_seconds=configured))
    events = [w for i in range(0, len(source), 640) for w in splitter.push(source[i:i + 640])]
    events.extend(splitter.flush())
    finals = [w for w in events if w.final]
    assert len(finals) > 2
    assert finals[0].end == pytest.approx(first_end)
    assert finals[-1].end == pytest.approx(len(source) / 32000, abs=1e-9)
    assert [w.id for w in finals] == list(range(1, len(finals) + 1))
    # Hard cuts consume a complete input frame, so floating-point clock drift can delay by one frame.
    cap = 4 if mode == "chunk" else min(configured, 6)
    for w in finals[:-1]:
        assert cap - 1e-9 <= w.end - w.start <= cap + 0.020000001
    reconstructed = bytearray()
    for idx, w in enumerate(finals):
        start = round(w.start * 16000) * 2
        end = round(w.end * 16000) * 2
        assert start == len(reconstructed)
        overlap = 9600 if idx else 0  # 4,800 samples at 16 kHz
        assert w.pcm == source[start - overlap:end]
        if idx:
            previous = finals[idx - 1]
            assert w.start == pytest.approx(previous.end, abs=1e-9)
            assert w.pcm[:overlap] == previous.pcm[-overlap:]
        reconstructed.extend(w.pcm[overlap:])
    assert reconstructed == source
    previews = [w for w in events if not w.final]
    if mode == "chunk" or cap == 1:
        assert not previews
    else:
        assert previews
        for preview in previews:
            final = finals[preview.id - 1]
            assert preview.start == final.start and preview.end < final.end
            assert final.pcm.startswith(preview.pcm)


def test_finding_03_pause_after_hard_cut_drops_overlap_but_keeps_idle_preroll():
    first_pcm = marked_audio(301)
    # Nonzero, individually marked quiet frames cannot open or extend a speech window.
    quiet = np.arange(50, dtype="<i2").repeat(320).tobytes()
    last_pcm = marked_audio(45, eof_samples=137)
    source = first_pcm + quiet + last_pcm
    first, second = finals_of(source[i:i + 640] for i in range(0, len(source), 640))
    assert first.end == pytest.approx(6.02, abs=1e-9)
    assert first.pcm == first_pcm
    assert second.start == pytest.approx(7.02, abs=1e-9)
    assert second.end == pytest.approx(len(source) / 32000, abs=1e-9)
    assert second.pcm == quiet[-19 * 640:] + last_pcm


def test_finding_03_adjacent_timestamps_can_have_no_pcm_overlap():
    source = marked_audio(347, eof_samples=137)
    # The first post-cut frame is loud enough for timed preroll, but VAD rejects it.
    voice = iter([True] * 301 + [False] + [True] * 46)
    splitter = Segmenter(Settings(), lambda _pcm: next(voice))
    finals = [w for i in range(0, len(source), 640) for w in splitter.push(source[i:i + 640]) if w.final]
    first, second = finals + splitter.flush()
    assert second.start == pytest.approx(first.end, abs=1e-9)
    assert first.pcm == source[:301 * 640]
    assert second.pcm == source[301 * 640:]


def test_finding_03_subminimum_voiced_tail_is_still_discarded_after_hard_cut():
    source = marked_audio(307, eof_samples=137)
    first, = finals_of(source[i:i + 640] for i in range(0, len(source), 640))
    assert first.end == pytest.approx(6.02, abs=1e-9)
    assert first.pcm == source[:301 * 640]
    assert 0 < len(source) - len(first.pcm) < 0.2 * 32000


def audio_with_dip(frame_count, start_frame, dip_frames, level):
    source = bytearray(marked_audio(frame_count, eof_samples=137))
    dip = np.frombuffer(source, dtype="<i2", count=dip_frames * 320, offset=start_frame * 640)
    dip[:] = level + dip % 100
    return bytes(source)


@pytest.mark.parametrize("level", [0, 950])
def test_finding_03_quiet_split_transfers_exact_pcm_and_flushes_suffix(level):
    source = audio_with_dip(301, 263, 6, level)
    consumed = []

    def detector(pcm):
        consumed.append(pcm)
        return True

    splitter = Segmenter(Settings(), detector, quiet_boundaries=True)
    events = [w for i in range(0, len(source), 640) for w in splitter.push(source[i:i + 640])]
    events.extend(splitter.flush())
    first, second = [w for w in events if w.final]
    assert b"".join(consumed) == source
    assert first.id + 1 == second.id
    assert first.end == second.start == 5.32
    cut = 266 * 640
    assert first.pcm == source[:cut]
    assert second.pcm == source[cut:]
    assert second.end == pytest.approx(len(source) / 32000, abs=1e-9)
    assert all(w.id == first.id and w.end <= first.end and first.pcm.startswith(w.pcm)
               for w in events if not w.final)
    assert not splitter.flush()
    legacy, = finals_of(source[i:i + 640] for i in range(0, len(source), 640))
    assert legacy.end == pytest.approx(6.02)
    assert legacy.pcm == source[:301 * 640]


def test_finding_03_consecutive_quiet_splits_keep_sample_ownership():
    source = bytearray(audio_with_dip(680, 263, 6, 0))
    later_dip = np.frombuffer(source, dtype="<i2", count=6 * 320, offset=536 * 640)
    later_dip %= 100
    splitter = Segmenter(Settings(preview_seconds=4), quiet_boundaries=True)
    finals = [w for i in range(0, len(source), 640) for w in splitter.push(source[i:i + 640]) if w.final]
    finals += splitter.flush()
    assert [w.id for w in finals] == [1, 2, 3]
    assert [w.end for w in finals] == pytest.approx([5.32, 10.78, len(source) / 32000])
    assert [w.start for w in finals[1:]] == [w.end for w in finals[:-1]]
    assert b"".join(w.pcm for w in finals) == source


@pytest.mark.parametrize(("start_frame", "dip_frames"), [(263, 2), (235, 6), (286, 18)])
def test_finding_03_quiet_split_preserves_fallback_and_natural_pause(start_frame, dip_frames):
    source = audio_with_dip(420, start_frame, dip_frames, 0)
    frames = [source[i:i + 640] for i in range(0, len(source), 640)]

    def cut(quiet_boundaries):
        splitter = Segmenter(Settings(), quiet_boundaries=quiet_boundaries)
        return [w for frame in frames for w in splitter.push(frame)] + splitter.flush()

    # A short trough, a trough before the last preview, and a natural breath split.
    assert cut(True) == cut(False)


@pytest.mark.parametrize("mode", ["sentence", "chunk"])
def test_finding_03_overlap_ab_changes_pcm_without_moving_hard_cut_times(mode, monkeypatch):
    source = marked_audio(420, eof_samples=137)
    frames = [source[i:i + 640] for i in range(0, len(source), 640)]
    settings = Settings(mode=mode)
    control = finals_of(frames, settings)
    monkeypatch.setattr("koeha.audio.segmentation.OVERLAP_BYTES", 0)
    no_overlap = finals_of(frames, settings)
    assert [(w.id, w.start, w.end) for w in no_overlap] == [(w.id, w.start, w.end) for w in control]
    assert b"".join(w.pcm for w in no_overlap) == source


def test_finding_03_recognition_explains_cap_without_clamping_saved_value(studio):
    assert "6 seconds" in studio.max_size.accessibleDescription()
    studio.max_size.setValue(23.5)
    assert studio.save_settings()
    assert Settings.load(studio.controller.root / "settings.json").max_seconds == 23.5


def test_previews_coalesce_but_final_queue_never_silently_drops():
    queue = WindowQueue(2)
    queue.put(AudioWindow(1, 0, 1, b"a", False))
    queue.put(AudioWindow(1, 0, 2, b"b", False))
    queue.put(AudioWindow(1, 0, 3, b"c", True))
    # New previews while a final window is waiting are skipped so final windows never lag behind previews.
    queue.put(AudioWindow(2, 3, 3.5, b"preview", False))
    queue.put(AudioWindow(2, 3, 4, b"d", True))
    with pytest.raises(Overloaded):
        queue.put(AudioWindow(3, 4, 5, b"e", True))
    assert queue.get().pcm == b"c"
    assert queue.get().pcm == b"d"


def test_vad_vocal_band_filter_attenuates_sub_bass_and_keeps_vocal_formants():
    from koeha.audio.vad import _focus_vad_band
    t = np.arange(512, dtype=np.float32) / 16000.0
    vocal = 0.5 * np.sin(2 * np.pi * 625.0 * t)
    bass = 0.5 * np.sin(2 * np.pi * 62.5 * t)
    assert np.sqrt(np.mean(_focus_vad_band(vocal) ** 2)) > 0.35
    assert np.sqrt(np.mean(_focus_vad_band(bass) ** 2)) < 0.05



def test_decoder_failure_stops_capture_and_reports_error():
    class Broken:
        def decode(self, _):
            raise ValueError("model failed")
    errors = []
    pipeline = LivePipeline(replace(Settings(), mode="chunk", chunk_seconds=1), Broken(), list().append,
                            on_error=errors.append)
    with pytest.raises(RuntimeError, match="model failed"):
        pipeline.run(signal(2))
    assert pipeline.stop.is_set()
    assert errors == ["model failed"]


def test_stop_drains_final_tail():
    entered = threading.Event()
    class Decoder:
        def decode(self, _):
            entered.set()
            return "hello"
    result = []
    pipeline = LivePipeline(Settings(mode="chunk"), Decoder(), result.append)
    pipeline.run(iter(signal(.5)))
    assert entered.is_set()
    assert len(result) == 1 and result[0].final


def test_stable_prefix_never_shrinks():
    stable = StableText()
    assert stable.update("今日は") == ""
    assert stable.update("今日は晴れ") == "今日は"
    assert stable.update("今日は雨") == "今日は"


def test_offline_producer_waits_without_losing_final_windows():
    result = []
    class Decoder:
        def decode(self, _):
            import time
            time.sleep(.002)
            return "speech"
    pipeline = LivePipeline(Settings(mode="chunk", chunk_seconds=1), Decoder(), result.append, realtime=False)
    pipeline.run(iter(signal(20)))
    assert len(result) == 20
    assert [c.id for c in result] == list(range(1, 21))


def test_speech_confidence_is_validated():
    assert Settings().speech_confidence == 0.5
    replace(Settings(), speech_confidence=0.2).validate()
    for value in (0.0, 1.0):
        with pytest.raises(ValueError):
            replace(Settings(), speech_confidence=value).validate()


def test_voice_detector_uses_the_configured_confidence(monkeypatch):
    from koeha.audio import vad
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def run(self, outputs, feeds):
            return np.array([[0.3]], dtype=np.float32), feeds["state"]
    monkeypatch.setattr(vad.ort, "InferenceSession", Session)
    pcm = np.zeros(512, dtype="<i2").tobytes()
    assert not vad.VoiceDetector("silero.onnx")(pcm)
    assert vad.VoiceDetector("silero.onnx", 0.25)(pcm)


def test_voice_detector_hangover_holds_across_brief_singing_dips(monkeypatch):
    from koeha.audio import vad
    probs = iter([0.6, 0.35, 0.1, 0.1, 0.1, 0.1])
    class Session:
        def __init__(self, *args, **kwargs):
            pass
        def run(self, outputs, feeds):
            return np.array([[next(probs)]], dtype=np.float32), feeds["state"]
    monkeypatch.setattr(vad.ort, "InferenceSession", Session)
    detector = vad.VoiceDetector("silero.onnx", threshold=0.5, hangover_windows=2)
    pcm = np.zeros(512, dtype="<i2").tobytes()
    assert detector(pcm)  # 0.6 >= 0.5 (onset)
    assert detector(pcm)  # 0.35 >= 0.30 (offset hysteresis sustain)
    assert detector(pcm)  # 0.10 (hangover 1)
    assert detector(pcm)  # 0.10 (hangover 2)
    assert not detector(pcm)  # hangover expired


def _drive_capture_into_segmenter(monkeypatch, phases, settings, detector=None):
    """Run capture() -> frames() -> Segmenter across tagged phases without wall-clock sleeps."""
    monkeypatch.setattr(capture_module, "POLL_SECONDS", 0.0)
    stop = threading.Event()
    script = iter(phases)
    state = {"clock": 0.0, "poll": 0.0, "phase": "init", "in_callback": False, "received": None}

    def fake_clock():
        return state["clock"] if state["in_callback"] else state["poll"]

    class Stream:
        def is_active(self):
            for phase, kind, *rest in script:
                state["phase"] = phase
                if kind == "pcm":
                    dt, pcm = rest
                    state["poll"] = state["clock"]
                    state["clock"] += dt
                    state["in_callback"] = True
                    try:
                        state["received"](pcm, len(pcm) // 2, None, 0)
                    finally:
                        state["in_callback"] = False
                    return True
                if kind == "idle":
                    (dt,) = rest
                    state["clock"] += dt
                    state["poll"] = state["clock"]
                    return True
            stop.set()
            return True

        def close(self):
            pass

    class Audio:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_default_wasapi_loopback(self):
            return {"maxInputChannels": 1, "defaultSampleRate": 16000, "index": 1}

        def open(self, **kwargs):
            state["received"] = kwargs["stream_callback"]
            return Stream()

    module = SimpleNamespace(PyAudio=Audio, paInt16=8, paInputOverflow=2,
                             paContinue=0, paComplete=1, paAbort=2)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    splitter = Segmenter(settings, detector)
    tagged = []
    for frame in frames(capture("system", None, stop, clock=fake_clock)):
        for window in splitter.push(frame):
            tagged.append((state["phase"], window))
    return tagged, splitter


@pytest.mark.parametrize("profile", [SPEECH_VAD, MUSIC_VOCAL_VAD])
def test_finding_02_idle_loopback_finalizes_during_pause_before_resume(monkeypatch, profile):
    source = marked_audio(300)
    pre_pcm = source[:100 * 640]
    post_pcm = source[100 * 640:]
    phases = [
        *[("pre", "pcm", 0.02, pre_pcm[i:i + 640]) for i in range(0, len(pre_pcm), 640)],
        *[("gap", "idle", 0.1) for _ in range(36)],
        *[("post", "pcm", 0.02, post_pcm[i:i + 640]) for i in range(0, len(post_pcm), 640)],
    ]
    settings = Settings(vad_profile=profile)
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, settings, lambda _pcm: True)
    flushed = [("flush", w) for w in splitter.flush()]
    all_tagged = tagged + flushed

    pre_windows = [w for phase, w in all_tagged if phase == "pre"]
    gap_windows = [w for phase, w in all_tagged if phase == "gap"]
    assert pre_windows and all(not w.final and w.id == 1 for w in pre_windows)
    # ID 1 finalizes during the idle pause before any post-gap frame is pushed.
    assert [w.id for w in gap_windows if w.final] == [1]

    finals = [w for _, w in all_tagged if w.final]
    assert [w.id for w in finals] == [1, 2]
    first, second = finals
    assert first.pcm.startswith(pre_pcm)
    assert not any(first.pcm[len(pre_pcm):])
    assert second.start == pytest.approx(2.0 + 3.6, abs=0.1)
    assert second.end == pytest.approx(2.0 + 3.6 + 4.0, abs=0.1)
    assert second.pcm.endswith(post_pcm)
    preroll_prefix = second.pcm[:-len(post_pcm)]
    assert len(preroll_prefix) == (PREROLL_FRAMES - 1) * 640
    assert not any(preroll_prefix)

    for preview in (w for _, w in all_tagged if not w.final):
        final = finals[preview.id - 1]
        assert preview.start == final.start and preview.end < final.end
        assert final.pcm.startswith(preview.pcm)


def test_finding_02_idle_fill_matches_digital_silence_callbacks(monkeypatch):
    source = marked_audio(200)
    first_pcm = source[:100 * 640]
    second_pcm = source[100 * 640:]
    phases = [
        *[("pre", "pcm", 0.02, first_pcm[i:i + 640]) for i in range(0, len(first_pcm), 640)],
        *[("gap1", "idle", 0.1) for _ in range(16)],
        *[("post", "pcm", 0.02, second_pcm[i:i + 640]) for i in range(0, len(second_pcm), 640)],
        *[("gap2", "idle", 0.1) for _ in range(12)],
    ]
    pattern = [True] * 100 + [False] * 80 + [True] * 100 + [False] * 60
    flags_gap = iter(pattern)
    tagged, splitter_gap = _drive_capture_into_segmenter(
        monkeypatch, phases, Settings(), lambda _pcm: next(flags_gap)
    )
    gap_windows = [w for _, w in tagged] + splitter_gap.flush()

    flags_zero = iter(pattern)
    splitter_zero = Segmenter(Settings(), lambda _pcm: next(flags_zero))
    zero_stream = (
        [first_pcm[i:i + 640] for i in range(0, len(first_pcm), 640)]
        + [bytes(640) for _ in range(80)]
        + [second_pcm[i:i + 640] for i in range(0, len(second_pcm), 640)]
        + [bytes(640) for _ in range(60)]
    )
    zero_windows = [w for f in zero_stream for w in splitter_zero.push(f)] + splitter_zero.flush()
    assert gap_windows == zero_windows


@pytest.mark.parametrize(("pause_seconds", "gap_seconds", "expects_final"), [
    (0.2, 0.3, True),
    (1.0, 0.6, False),
    (1.0, 1.1, True),
    (3.0, 1.8, False),
    (3.0, 3.1, True),
])
def test_finding_02_pause_matrix_and_breath_split(monkeypatch, pause_seconds, gap_seconds, expects_final):
    speech = marked_audio(50)  # 1.0 s speech so sub-pause gaps stay below the 3.2 s breath-split length
    polls = round(gap_seconds / 0.1)
    phases = [
        *[("pre", "pcm", 0.02, speech[i:i + 640]) for i in range(0, len(speech), 640)],
        *[("gap", "idle", 0.1) for _ in range(polls)],
    ]
    settings = Settings(pause_seconds=pause_seconds)
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, settings)
    gap_finals = [w for phase, w in tagged if phase == "gap" and w.final]
    if expects_final:
        assert len(gap_finals) == 1 and gap_finals[0].id == 1
        assert not splitter.flush()
    else:
        assert not gap_finals
        flushed = splitter.flush()
        assert len(flushed) == 1 and flushed[0].id == 1
        assert flushed[0].end == pytest.approx(1.0 + gap_seconds, abs=0.03)


def test_finding_02_breath_split_finalizes_long_sentence_on_short_idle_gap(monkeypatch):
    speech = marked_audio(180)  # 3.6 s >= 3.2 s breath-split threshold
    phases = [
        *[("pre", "pcm", 0.02, speech[i:i + 640]) for i in range(0, len(speech), 640)],
        *[("gap", "idle", 0.1) for _ in range(4)],  # 0.4 s >= 0.32 s breath-split silence
    ]
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, Settings(pause_seconds=1.0))
    gap_finals = [w for phase, w in tagged if phase == "gap" and w.final]
    assert len(gap_finals) == 1 and gap_finals[0].id == 1
    assert not splitter.flush()


def test_finding_02_chunk_mode_flushes_tail_during_gap_and_keeps_cadence(monkeypatch):
    source = marked_audio(400)
    pre_pcm = source[:300 * 640]   # 6.0 s -> 4.0 s cut + 2.0 s tail
    post_pcm = source[300 * 640:]  # 2.0 s after pause
    phases = [
        *[("pre", "pcm", 0.02, pre_pcm[i:i + 640]) for i in range(0, len(pre_pcm), 640)],
        *[("gap", "idle", 0.1) for _ in range(12)],
        *[("post", "pcm", 0.02, post_pcm[i:i + 640]) for i in range(0, len(post_pcm), 640)],
    ]
    settings = Settings(mode="chunk", chunk_seconds=4.0, pause_seconds=1.0)
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, settings)
    all_tagged = tagged + [("flush", w) for w in splitter.flush()]
    assert all(w.final for _, w in all_tagged)
    assert [phase for phase, _ in all_tagged] == ["pre", "gap", "flush"]
    first, second, third = [w for _, w in all_tagged]
    assert [first.id, second.id, third.id] == [1, 2, 3]
    assert first.end == pytest.approx(4.0, abs=0.02)
    assert second.start == pytest.approx(first.end, abs=1e-9)
    assert second.pcm[:OVERLAP_BYTES] == first.pcm[-OVERLAP_BYTES:]
    # Pause flush drops forced-cut carryover before the third chunk.
    assert third.start == pytest.approx(6.0 + 1.2, abs=0.05)
    assert third.pcm.endswith(post_pcm)
    assert not any(third.pcm[:-len(post_pcm)])


def test_finding_02_stop_during_gap_does_not_duplicate_final(monkeypatch):
    speech = marked_audio(100)
    phases = [
        *[("pre", "pcm", 0.02, speech[i:i + 640]) for i in range(0, len(speech), 640)],
        *[("gap", "idle", 0.1) for _ in range(14)],
    ]
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, Settings(pause_seconds=1.0))
    finals = [w for _, w in tagged if w.final]
    assert [w.id for w in finals] == [1]
    assert not splitter.flush()


@pytest.mark.parametrize("pause_duration", [15.0, 30.0, 60.0])
def test_finding_02_long_pauses_preserve_wall_clock_caption_timestamps(monkeypatch, pause_duration):
    source = marked_audio(200)
    pre_pcm = source[:100 * 640]
    post_pcm = source[100 * 640:]
    polls = round(pause_duration / 0.1)
    phases = [
        *[("pre", "pcm", 0.02, pre_pcm[i:i + 640]) for i in range(0, len(pre_pcm), 640)],
        *[("gap", "idle", 0.1) for _ in range(polls)],
        *[("post", "pcm", 0.02, post_pcm[i:i + 640]) for i in range(0, len(post_pcm), 640)],
    ]
    tagged, splitter = _drive_capture_into_segmenter(monkeypatch, phases, Settings())
    all_tagged = tagged + [("flush", w) for w in splitter.flush()]
    assert [w.id for phase, w in all_tagged if phase == "gap" and w.final] == [1]
    first, second = [w for _, w in all_tagged if w.final]
    assert (first.id, second.id) == (1, 2)
    assert first.pcm.startswith(pre_pcm)
    assert not any(first.pcm[len(pre_pcm):])
    assert second.start == pytest.approx(2.0 + pause_duration, abs=0.05)
    assert second.end == pytest.approx(4.0 + pause_duration, abs=0.05)
    assert second.pcm.endswith(post_pcm)
    assert not any(second.pcm[:-len(post_pcm)])
