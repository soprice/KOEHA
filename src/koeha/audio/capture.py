"""Audio boundaries: all downstream code receives 16 kHz mono PCM16 frames."""
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue

import av

RATE = 16000
FRAME_BYTES = 640
MAX_OVERFLOWS = 10  # consecutive 20 ms callbacks
POLL_SECONDS = 0.1
IDLE_GAP_SECONDS = 0.2
IDLE_CAP_SECONDS = 10.0  # bound single catch-up after OS sleep; timestamps may lag across suspend
IDLE_SLICE_SAMPLES = RATE
clock = time.monotonic


@dataclass(frozen=True)
class Device:
    index: int
    name: str


class IdleFill:
    """Track an idle WASAPI loopback gap and return incremental 16 kHz silent sample counts."""

    def __init__(self, start: float, *, enabled: bool = True):
        self.enabled = enabled
        self.last_audio = start
        self.filled = 0

    def mark(self, now: float) -> None:
        self.last_audio = now
        self.filled = 0

    def due(self, now: float) -> int:
        if not self.enabled:
            return 0
        accounted = self.last_audio + self.filled / RATE
        if now - accounted > IDLE_CAP_SECONDS:
            self.last_audio += (now - accounted) - IDLE_CAP_SECONDS
        gap = now - self.last_audio
        if gap + 1e-9 < IDLE_GAP_SECONDS:
            return 0
        target = int(gap * RATE + 1e-6)
        samples = max(0, target - self.filled)
        self.filled += samples
        return samples


def _silence_slices(samples: int, stop):
    remaining = samples
    while remaining > 0 and not stop.is_set():
        step = min(remaining, IDLE_SLICE_SAMPLES)
        remaining -= step
        yield bytes(step * 2)


def devices(source: str) -> list[Device]:
    import pyaudiowpatch as pa
    with pa.PyAudio() as audio:
        infos = (audio.get_loopback_device_info_generator() if source == "system" else
                 (audio.get_device_info_by_index(i) for i in range(audio.get_device_count())))
        return [Device(int(d["index"]), d["name"]) for d in infos
                if d["maxInputChannels"] > 0 and (source == "system" or not d.get("isLoopbackDevice"))]


def capture(source: str, device: int | None, stop, device_name: str = "", *, clock=None):
    import pyaudiowpatch as pa
    now = clock or globals()["clock"]
    with pa.PyAudio() as audio:
        if device is None:
            info = (audio.get_default_wasapi_loopback() if source == "system"
                    else audio.get_default_input_device_info())
        else:
            info = _device_info(audio, device, device_name)
        channels = min(int(info["maxInputChannels"]), 2)
        if not channels:
            raise RuntimeError("The selected device has no audio input")
        rate = int(info["defaultSampleRate"])
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        has_audio = False
        pending = Queue(maxsize=64)
        errors = []
        overflows = 0
        def received(data, count, timing, flags):
            nonlocal overflows
            if stop.is_set():
                return None, pa.paComplete
            # A momentary overflow only loses a few samples; abort only when input keeps overflowing.
            if flags & pa.paInputOverflow:
                overflows += 1
                if overflows >= MAX_OVERFLOWS:
                    errors.append(RuntimeError("Audio input keeps overflowing; choose another device "
                                               "or reduce system load"))
                    return None, pa.paAbort
            else:
                overflows = 0
            try:
                pending.put_nowait((now(), data))
            except Full:
                errors.append(RuntimeError("Audio capture cannot keep up"))
                return None, pa.paAbort
            return None, pa.paContinue
        stream = audio.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=int(info["index"]), frames_per_buffer=rate // 50,
                            stream_callback=received)
        idle = IdleFill(now(), enabled=source == "system")
        try:
            while not stop.is_set():
                if errors:
                    raise errors[0]
                try:
                    arrived, pcm = pending.get(timeout=POLL_SECONDS)
                except Empty:
                    if errors:
                        raise errors[0]
                    if not stream.is_active():
                        raise RuntimeError("The audio device stopped delivering input")
                    if stop.is_set():
                        break
                    samples = idle.due(now())
                    if samples:
                        if has_audio:
                            for converted in resampler.resample(None):
                                if converted.samples:
                                    yield bytes(converted.planes[0])[:converted.samples * 2]
                            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
                            has_audio = False
                        yield from _silence_slices(samples, stop)
                    continue
                samples = len(pcm) // (channels * 2)
                if idle.filled:
                    yield from _silence_slices(idle.due(arrived - samples / rate), stop)
                idle.mark(arrived)
                if not samples:
                    continue
                frame = av.AudioFrame(format="s16", layout="stereo" if channels == 2 else "mono",
                                      samples=samples)
                frame.sample_rate = rate
                frame.planes[0].update(pcm)
                has_audio = True
                for converted in resampler.resample(frame):
                    yield bytes(converted.planes[0])[:converted.samples * 2]
        finally:
            stream.close()


def _device_info(audio, index, name):
    """Resolve a saved device, preferring its name because indexes shift when hardware changes."""
    try:
        info = audio.get_device_info_by_index(index)
    except (OSError, ValueError):
        info = None
    if not name or (info and info["name"] == name):
        if info is None:
            raise RuntimeError("The selected audio device is no longer available")
        return info
    for i in range(audio.get_device_count()):
        candidate = audio.get_device_info_by_index(i)
        if candidate["name"] == name and candidate["maxInputChannels"] > 0:
            return candidate
    raise RuntimeError(f"The audio device \"{name}\" is no longer available; choose another in Live")


def media_duration(path: Path) -> float | None:
    """Length in seconds when the container records it; None for unknown or unreadable media."""
    try:
        with av.open(str(path)) as container:
            return container.duration / av.time_base if container.duration else None
    except (OSError, ValueError, av.FFmpegError):
        return None


def file_audio(path: Path):
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                yield bytes(converted.planes[0])[:converted.samples * 2]
        for converted in resampler.resample(None):
            yield bytes(converted.planes[0])[:converted.samples * 2]


def frames(chunks):
    pending = bytearray()
    for chunk in chunks:
        pending.extend(chunk)
        while len(pending) >= FRAME_BYTES:
            yield bytes(pending[:FRAME_BYTES])
            del pending[:FRAME_BYTES]
    if pending:
        yield bytes(pending)
