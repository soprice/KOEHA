import numpy as np
import onnxruntime as ort

from koeha.core.settings import MUSIC_VOCAL_VAD, SPEECH_VAD

HANGOVER_WINDOWS = 2  # ~64 ms bridge for consonant stops without delaying sentence end
CONTINUITY_WINDOWS = 90  # ~2.9 s vocal phrase continuity window
VOCAL_CONFIRM_WINDOWS = 3
VOCAL_SUSTAIN_WINDOWS = 22
VOCAL_STATE_DECAY = np.float32(0.988)
VAD_FREQS = np.fft.rfftfreq(512, d=1.0 / 16000)
VAD_BAND_GAIN = np.ones_like(VAD_FREQS, dtype=np.float32)
VAD_BAND_GAIN[(VAD_FREQS >= 20.0) & (VAD_FREQS < 150.0)] = 0.1
VAD_BAND_GAIN[VAD_FREQS > 3800.0] = 0.2


def _focus_vad_band(samples: np.ndarray) -> np.ndarray:
    """Isolate the 150-3800 Hz vocal formant band for VAD without altering ASR audio."""
    if np.all(samples == samples[0]):
        return samples
    spec = np.fft.rfft(samples) * VAD_BAND_GAIN
    focused = np.fft.irfft(spec, n=len(samples)) * 1.35
    return np.clip(focused, -1.0, 1.0).astype(np.float32)


class VoiceDetector:
    def __init__(self, path, threshold=0.5, hangover_windows=HANGOVER_WINDOWS, *, profile=SPEECH_VAD):
        self.music_vocal = profile == MUSIC_VOCAL_VAD
        self.threshold = threshold
        self.offset_threshold = max(0.12, threshold * 0.4)
        self.reentry_threshold = max(0.18, threshold * 0.44)
        self.hangover_windows = hangover_windows
        if self.music_vocal:
            self.threshold = 0.28
            self.offset_threshold = 0.05
            self.reentry_threshold = 0.14
            self.hangover_windows = HANGOVER_WINDOWS
        options = ort.SessionOptions()
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self):
        """Start a new audio stream with no state from the previous stream."""
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)
        self.pending = np.empty(0, dtype=np.float32)
        self.probability = 0.0
        self.active = False
        self.hangover = 0
        self.continuity = 0
        self.onset_streak = 0
        self.confirmed = False

    def __call__(self, pcm):
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        self.pending = np.concatenate((self.pending, audio))
        while len(self.pending) >= 512:
            raw_window = self.pending[:512]
            self.pending = self.pending[512:]
            window = _focus_vad_band(raw_window).reshape(1, -1)
            combined = np.concatenate((self.context, window), axis=1)
            result, self.state = self.session.run(None, {"input": combined, "state": self.state,
                                                        "sr": np.array(16000, dtype=np.int64)})
            self.context = window[:, -64:]
            self.probability = float(result[0, 0])
            onset = self.reentry_threshold if self.continuity > 0 else self.threshold
            if self.music_vocal:
                self._update_vocal_activity(onset)
            else:
                self._update_speech_activity(onset)
        return self.active

    def _update_speech_activity(self, onset):
        if self.probability >= onset or (self.active and self.probability >= self.offset_threshold):
            self.active = True
            self.hangover = self.hangover_windows
            self.continuity = CONTINUITY_WINDOWS if self.hangover_windows > 0 else 0
        else:
            self._advance_hangover()

    def _update_vocal_activity(self, onset):
        # Continuous accompaniment can trap the recurrent state at very low confidence.
        if self.probability < self.reentry_threshold:
            self.state = self.state * VOCAL_STATE_DECAY
        if self.probability >= onset:
            self.active = True
            self.onset_streak += 1
            if self.onset_streak >= VOCAL_CONFIRM_WINDOWS:
                self.confirmed = True
            if not self.confirmed:
                self.hangover = max(self.hangover, self.hangover_windows)
                return
        elif not (self.active and self.confirmed and self.probability >= self.offset_threshold):
            self._advance_hangover()
            return
        self.hangover = VOCAL_SUSTAIN_WINDOWS
        self.continuity = CONTINUITY_WINDOWS

    def _advance_hangover(self):
        if self.active and self.hangover > 0:
            self.hangover -= 1
        else:
            self.active = False
            self.onset_streak = 0
            self.confirmed = False
            if self.continuity > 0:
                self.continuity -= 1
