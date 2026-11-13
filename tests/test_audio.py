import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from koeha.audio import capture as capture_module
from koeha.audio.capture import (
    IDLE_CAP_SECONDS,
    IDLE_SLICE_SAMPLES,
    MAX_OVERFLOWS,
    RATE,
    IdleFill,
    _device_info,
    capture,
)


def test_silent_loopback_can_stop_without_waiting_for_audio(monkeypatch):
    closed = threading.Event()
    opened = threading.Event()
    class Stream:
        def is_active(self):
            return True
        def close(self):
            closed.set()
    class Audio:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def get_default_wasapi_loopback(self):
            return {"maxInputChannels": 2, "defaultSampleRate": 48000, "index": 1}
        def open(self, **kwargs):
            assert callable(kwargs["stream_callback"])
            opened.set()
            return Stream()
    module = SimpleNamespace(PyAudio=Audio, paInt16=8)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    stop = threading.Event()
    thread = threading.Thread(target=lambda: list(capture("system", None, stop)), daemon=True)
    thread.start()
    assert opened.wait(1)
    stop.set()
    thread.join(1)
    assert not thread.is_alive()
    assert closed.is_set()


def test_brief_overflow_keeps_capturing_but_sustained_overflow_stops(monkeypatch):
    callbacks = []
    opened = threading.Event()
    class Stream:
        def is_active(self):
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
            callbacks.append(kwargs["stream_callback"])
            opened.set()
            return Stream()
    module = SimpleNamespace(PyAudio=Audio, paInt16=8, paInputOverflow=2,
                             paContinue=0, paComplete=1, paAbort=2)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    stop = threading.Event()
    failures = []
    def consume():
        try:
            list(capture("system", None, stop))
        except RuntimeError as error:
            failures.append(error)
    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    assert opened.wait(1)
    received = callbacks[0]
    pcm = bytes(640)
    assert received(pcm, 320, None, module.paInputOverflow) == (None, module.paContinue)
    assert received(pcm, 320, None, 0) == (None, module.paContinue)
    results = [received(pcm, 320, None, module.paInputOverflow) for _ in range(MAX_OVERFLOWS)]
    assert results[-2] == (None, module.paContinue)
    assert results[-1] == (None, module.paAbort)
    thread.join(2)
    assert not thread.is_alive()
    assert failures and "overflowing" in str(failures[0])


def test_saved_device_name_wins_over_a_shifted_index():
    table = [{"index": 0, "name": "Speakers", "maxInputChannels": 0},
             {"index": 1, "name": "Other", "maxInputChannels": 1},
             {"index": 2, "name": "USB Mic", "maxInputChannels": 1}]
    audio = SimpleNamespace(get_device_info_by_index=lambda i: table[i], get_device_count=lambda: len(table))
    assert _device_info(audio, 1, "USB Mic")["index"] == 2
    assert _device_info(audio, 1, "")["index"] == 1
    with pytest.raises(RuntimeError, match="no longer available"):
        _device_info(audio, 1, "Unplugged")


def run_scripted_capture(monkeypatch, events, *, source="system", rate=16000, channels=1):
    """Drive capture() deterministically without wall-clock sleeps."""
    monkeypatch.setattr(capture_module, "POLL_SECONDS", 0.0)
    stop = threading.Event()
    script = iter(events)
    state = {"clock": 0.0, "poll": 0.0, "active": True, "in_callback": False, "received": None}

    def fake_clock():
        return state["clock"] if state["in_callback"] else state["poll"]

    class Stream:
        def is_active(self):
            for kind, *rest in script:
                if kind == "pcm":
                    dt, pcm = rest
                    state["poll"] = state["clock"]
                    state["clock"] += dt
                    state["in_callback"] = True
                    try:
                        state["received"](pcm, len(pcm) // (channels * 2), None, 0)
                    finally:
                        state["in_callback"] = False
                    return state["active"]
                if kind == "idle":
                    (dt,) = rest
                    state["clock"] += dt
                    state["poll"] = state["clock"]
                    return state["active"]
                if kind == "inactive":
                    (dt,) = rest
                    state["clock"] += dt
                    state["poll"] = state["clock"]
                    state["active"] = False
                    return False
            stop.set()
            return state["active"]

        def close(self):
            pass

    class Audio:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_default_wasapi_loopback(self):
            return {"maxInputChannels": channels, "defaultSampleRate": rate, "index": 1}

        def get_default_input_device_info(self):
            return {"maxInputChannels": channels, "defaultSampleRate": rate, "index": 1}

        def open(self, **kwargs):
            state["received"] = kwargs["stream_callback"]
            return Stream()

    module = SimpleNamespace(PyAudio=Audio, paInt16=8, paInputOverflow=2,
                             paContinue=0, paComplete=1, paAbort=2)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    return list(capture(source, None, stop, clock=fake_clock))


@pytest.mark.parametrize(("rate", "channels"), [(16000, 1), (48000, 2)])
def test_idle_loopback_fills_measured_gap_and_resets_on_resume(monkeypatch, rate, channels):
    frame_samples = rate // 50
    pre_frame = np.full(frame_samples * channels, 1200, dtype="<i2").tobytes()
    post_frame = np.full(frame_samples * channels, 2400, dtype="<i2").tobytes()
    events = [
        *[("pcm", 0.02, pre_frame) for _ in range(5)],
        *[("idle", 0.1) for _ in range(36)],
        *[("pcm", 0.02, post_frame) for _ in range(5)],
        ("idle", 0.15),
        *[("pcm", 0.02, post_frame) for _ in range(2)],
        *[("idle", 0.1) for _ in range(3)],
    ]
    chunks = run_scripted_capture(monkeypatch, events, rate=rate, channels=channels)
    samples = np.frombuffer(b"".join(chunks), dtype="<i2")
    nonzero = np.flatnonzero(samples != 0)
    pre_end = int(nonzero[nonzero < 320 * 6][-1]) + 1
    post_indices = nonzero[nonzero >= pre_end]
    post_start = int(post_indices[0])
    post_end = int(post_indices[-1]) + 1
    # Flush on the first fill keeps all 5 * 320 = 1,600 pre-gap samples ahead of the silence even at 48 kHz.
    assert pre_end == 5 * 320
    zero_gap = post_start - pre_end
    expected = round(3.6 * RATE)
    assert expected - 320 <= zero_gap <= expected
    # After resume, fill state resets: 150 ms jitter injects nothing, and a later 0.3 s gap fills freshly.
    assert np.all(samples[post_start:post_end] != 0)
    assert post_end - post_start == 7 * 320
    assert len(samples) - post_end == round(0.3 * RATE)


def test_callback_jitter_below_threshold_injects_no_silence(monkeypatch):
    frames = [np.full(320, 500 + i * 100, dtype="<i2").tobytes() for i in range(5)]
    events = [
        ("pcm", 0.02, frames[0]),
        ("idle", 0.025),
        ("pcm", 0.02, frames[1]),
        ("idle", 0.100),
        ("pcm", 0.02, frames[2]),
        ("idle", 0.100),
        ("idle", 0.090),
        ("pcm", 0.02, frames[3]),
        ("pcm", 0.02, frames[4]),
    ]
    chunks = run_scripted_capture(monkeypatch, events)
    assert b"".join(chunks) == b"".join(frames)


def test_startup_idle_before_first_callback_advances_sample_clock(monkeypatch):
    tone = np.full(320, 1800, dtype="<i2").tobytes()
    events = [
        *[("idle", 0.1) for _ in range(7)],
        ("pcm", 0.02, tone),
        ("pcm", 0.02, tone),
    ]
    chunks = run_scripted_capture(monkeypatch, events)
    samples = np.frombuffer(b"".join(chunks), dtype="<i2")
    first_real = int(np.flatnonzero(samples != 0)[0])
    assert first_real == pytest.approx(round(0.7 * RATE), abs=320)
    assert np.all(samples[first_real:] == 1800)


def test_microphone_and_inactive_stream_never_synthesize_silence(monkeypatch):
    tone = np.full(320, 1500, dtype="<i2").tobytes()
    mic_events = [("pcm", 0.02, tone), *[("idle", 0.5) for _ in range(4)], ("pcm", 0.02, tone)]
    mic_chunks = run_scripted_capture(monkeypatch, mic_events, source="microphone")
    assert b"".join(mic_chunks) == tone * 2

    captured = []
    with pytest.raises(RuntimeError, match="stopped delivering input"):
        for chunk in run_scripted_capture(monkeypatch, [("idle", 0.1), ("inactive", 0.5)]):
            captured.append(chunk)
    assert not captured


def test_stop_during_idle_fill_exits_within_200_ms(monkeypatch):
    closed = threading.Event()
    filling = threading.Event()
    now = [0.0]

    class Stream:
        def is_active(self):
            now[0] += 3.0
            return True

        def close(self):
            closed.set()

    class Audio:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get_default_wasapi_loopback(self):
            return {"maxInputChannels": 1, "defaultSampleRate": 16000, "index": 1}

        def open(self, **kwargs):
            return Stream()

    module = SimpleNamespace(PyAudio=Audio, paInt16=8)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", module)
    stop = threading.Event()
    chunks = []

    def consume():
        for chunk in capture("system", None, stop, clock=lambda: now[0]):
            chunks.append(chunk)
            filling.set()

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    assert filling.wait(1)
    stop.set()
    thread.join(0.2)
    assert not thread.is_alive()
    assert closed.is_set()
    assert chunks and all(b == 0 for b in chunks[0])


def test_suspend_jump_is_capped_and_sliced(monkeypatch):
    chunks = run_scripted_capture(monkeypatch, [("idle", 7200.0)])
    assert chunks
    assert sum(len(c) for c in chunks) == int(IDLE_CAP_SECONDS * RATE) * 2
    assert max(len(c) for c in chunks) <= IDLE_SLICE_SAMPLES * 2
    assert len(chunks) >= int(IDLE_CAP_SECONDS)

    helper = IdleFill(0.0)
    assert helper.due(7200.0) == int(IDLE_CAP_SECONDS * RATE)
    # Normal polling after wake-up continues advancing instead of freezing at 10 s.
    assert helper.due(7200.1) == round(0.1 * RATE)
    # A second single-poll suspend jump is again bounded by the per-poll cap.
    assert helper.due(14400.1) == int(IDLE_CAP_SECONDS * RATE)


@pytest.mark.parametrize("pause_seconds", [15, 30, 60])
def test_long_pauses_preserve_exact_wall_clock_samples(monkeypatch, pause_seconds):
    tone = np.full(320, 1600, dtype="<i2").tobytes()
    events = [
        ("pcm", 0.02, tone),
        *[("idle", 0.1) for _ in range(pause_seconds * 10)],
        ("pcm", 0.02, tone),
    ]
    chunks = run_scripted_capture(monkeypatch, events)
    samples = np.frombuffer(b"".join(chunks), dtype="<i2")
    nonzero = np.flatnonzero(samples != 0)
    pre_end = int(nonzero[nonzero < 640][-1]) + 1
    post_start = int(nonzero[nonzero >= pre_end][0])
    assert pre_end == 320
    assert post_start - pre_end == pause_seconds * RATE
