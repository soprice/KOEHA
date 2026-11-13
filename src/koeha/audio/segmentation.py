"""Sample-clock segmentation with speculative previews and reliable final windows."""
from collections import deque
from itertools import groupby, takewhile
from typing import NamedTuple

import numpy as np

from koeha.audio.capture import RATE
from koeha.core.events import AudioWindow
from koeha.core.settings import Settings

OVERLAP_BYTES = int(0.3 * RATE * 2)  # 300 ms acoustic lead-in across forced window splits
PREROLL_FRAMES = 20  # 400 ms ring of idle audio kept ahead of a vocal onset
PHRASE_CAP_SECONDS = 6.0  # keep live sentence windows compact so ASR decoding stays fast
QUIET_LOOKBACK = int(1.5 * RATE)
QUIET_MIN_SAMPLES = int(0.08 * RATE)
MIN_VOICE_SAMPLES = int(0.2 * RATE)


class BoundaryFrame(NamedTuple):
    start: int
    end: int
    level: float
    speaking: bool

    @property
    def samples(self):
        return self.end - self.start


class Segmenter:
    def __init__(self, settings: Settings, detector=None, *, quiet_boundaries=False):
        self.settings = settings
        self.quiet_boundaries = quiet_boundaries and settings.mode == "sentence"
        self.clock = 0.0
        # Keep an independent input sample count for PCM ownership audits.
        self.sample_clock = 0
        self.sequence = 0
        self.start = 0.0
        self.audio = bytearray()
        self.preroll = deque(maxlen=PREROLL_FRAMES)
        self.audible_run = 0  # trailing preroll frames at or above half the voice threshold
        self.carryover = b""
        self.voice = 0.0
        self.silence = 0.0
        self.last_preview = 0.0
        self.preview_end_sample = 0
        self.boundary_frames: deque[BoundaryFrame] = deque()
        self.level = 0.0
        self.detector = detector

    def push(self, pcm: bytes) -> list[AudioWindow]:
        duration = len(pcm) / (RATE * 2)
        self.clock += duration
        frame_start = self.sample_clock
        self.sample_clock += len(pcm) // 2
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        self.level = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        voice_threshold = self.settings.vad_voice_threshold
        rms_gate = voice_threshold * (0.5 if self.audio else 1.0)
        speaking = self.level >= rms_gate
        if self.detector is not None:
            speaking = self.detector(pcm) and speaking
        if not self.audio:
            # Keep the latest idle audio unconditionally: a soft onset must survive one quiet frame,
            # such as a stop closure, between it and the first frame that counts as speech.
            self.preroll.append(pcm)
            audible = self.level >= voice_threshold * 0.5
            self.audible_run = min(self.audible_run + 1, PREROLL_FRAMES) if audible else 0
            if not speaking:
                self.carryover = b""
                return []
            self.sequence += 1
            # Carryover and quieter lead-in are acoustic context for the recognizer. The window is
            # still timed from its unbroken audible run, so split timing and captions do not move.
            timed = list(self.preroll)[-self.audible_run:]
            self.audio.extend(self.carryover)
            self.audio.extend(b"".join(self.preroll))
            self.start = max(0, self.clock - sum(len(p) for p in timed) / (RATE * 2))
            self.carryover = b""
            self.preroll.clear()
            self.audible_run = 0
            self.last_preview = self.clock
            if self.quiet_boundaries:
                self.preview_end_sample = round(self.start * RATE)
        else:
            self.audio.extend(pcm)
        self.voice += duration if speaking else 0
        self.silence = 0 if speaking else self.silence + duration
        if self.quiet_boundaries:
            self.boundary_frames.append(BoundaryFrame(frame_start, self.sample_clock, self.level, speaking))
            while self.boundary_frames[0].end <= self.sample_clock - QUIET_LOOKBACK:
                self.boundary_frames.popleft()
        length = self.clock - self.start
        limit = (
            self.settings.chunk_seconds
            if self.settings.mode == "chunk"
            else min(self.settings.max_seconds, PHRASE_CAP_SECONDS)
        )
        breath_split = (
            self.settings.mode == "sentence"
            and length >= 3.2
            and self.silence >= min(0.32, self.settings.pause_seconds)
        )
        if self.silence >= self.settings.pause_seconds or breath_split or length >= limit:
            forced_split = not breath_split and self.silence < self.settings.pause_seconds and length >= limit
            if forced_split and self.quiet_boundaries:
                if (split := self._quiet_split()) is not None:
                    return [split]
            tail = bytes(self.audio[-OVERLAP_BYTES:]) if forced_split and OVERLAP_BYTES else b""
            windows = self.flush()
            self.carryover = tail
            return windows
        if (self.settings.mode == "sentence" and self.voice >= 0.25 and
                self.clock - self.last_preview >= self.settings.preview_seconds):
            self.last_preview = self.clock
            if self.quiet_boundaries:
                self.preview_end_sample = self.sample_clock
            return [self._window(False)]
        return []

    def _quiet_split(self):
        """At the cap, move the unfinalized suffix across a sustained dip without replaying it."""
        frames = list(self.boundary_frames)
        floor = self.settings.vad_voice_threshold * 0.5
        quiet_level = max(floor, 0.35 * float(np.median([frame.level for frame in frames])))
        earliest = max(self.preview_end_sample, self.sample_clock - QUIET_LOOKBACK)
        voice_samples = round(self.voice * RATE)
        candidates = []
        for quiet, group in groupby(frames, lambda f: not f.speaking or f.level <= quiet_level):
            if not quiet:
                continue
            run = [frame for frame in group if frame.start >= earliest]
            if not run or run[-1].end - run[0].start < QUIET_MIN_SAMPLES:
                continue
            cut = run[len(run) // 2].start
            suffix = [frame for frame in frames if frame.start >= cut]
            suffix_voice = sum(frame.samples for frame in suffix if frame.speaking)
            if min(suffix_voice, voice_samples - suffix_voice) < MIN_VOICE_SAMPLES:
                continue
            candidates.append((sum(frame.level for frame in run) / len(run), -cut, suffix, suffix_voice))
        if not candidates:
            return None
        _, neg_cut, suffix, suffix_voice = min(candidates, key=lambda candidate: candidate[:2])
        cut = -neg_cut
        byte_offset = len(self.audio) - (self.sample_clock - cut) * 2
        end = cut / RATE
        final = AudioWindow(self.sequence, self.start, end, bytes(self.audio[:byte_offset]), True)
        self.audio = self.audio[byte_offset:]
        self.sequence += 1
        self.start = end
        self.voice = suffix_voice / RATE
        self.silence = sum(frame.samples for frame in takewhile(
            lambda frame: not frame.speaking, reversed(suffix)
        )) / RATE
        self.last_preview = self.clock
        self.preview_end_sample = cut
        self.boundary_frames = deque(suffix)
        return final

    def _window(self, final: bool):
        return AudioWindow(self.sequence, self.start, self.clock, bytes(self.audio), final)

    def flush(self) -> list[AudioWindow]:
        windows = [self._window(True)] if self.audio and self.voice >= 0.2 else []
        self.audio.clear()
        if self.quiet_boundaries:
            self.boundary_frames.clear()
        self.carryover = b""
        self.voice = self.silence = 0.0
        return windows


class StableText:
    """A preview is revisable; its stable prefix can only grow within one utterance."""
    def __init__(self):
        self.previous = ""
        self.stable = ""

    def update(self, text: str) -> str:
        count = 0
        for left, right in zip(self.previous, text):
            if left != right:
                break
            count += 1
        prefix = text[:count]
        if prefix.startswith(self.stable):
            self.stable = prefix
        self.previous = text
        return self.stable
