import threading
from collections import deque

from koeha.audio.segmentation import Segmenter
from koeha.core.events import Caption


class Overloaded(RuntimeError):
    pass


class WindowQueue:
    """Replace expendable previews, preserve every final or fail explicitly."""
    def __init__(self, capacity=8):
        self.items = deque()
        self.capacity = capacity
        self.closed = False
        self.condition = threading.Condition()

    def put(self, window, block=False):
        with self.condition:
            if block:
                self.condition.wait_for(lambda: len(self.items) < self.capacity or self.closed)
            if self.closed:
                return
            if window.final:
                # Drop any pending speculative previews so final captions never wait behind them.
                self.items = deque(w for w in self.items if w.final)
            else:
                if any(w.final for w in self.items):
                    return
                self.items = deque(w for w in self.items if w.final or w.id != window.id)
            if len(self.items) >= self.capacity:
                if not window.final:
                    return
                self.items = deque(w for w in self.items if w.final)
                if len(self.items) >= self.capacity:
                    raise Overloaded("Recognition cannot keep up. Stop and choose a faster model or longer chunks.")
            self.items.append(window)
            self.condition.notify()

    def get(self):
        with self.condition:
            self.condition.wait_for(lambda: self.items or self.closed)
            result = self.items.popleft() if self.items else None
            self.condition.notify_all()
            return result

    def finish(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class LivePipeline:
    def __init__(self, settings, decoder, on_caption, on_level=lambda _: None, on_error=lambda _: None,
                 realtime=True, detector=None):
        self.settings = settings
        self.decoder = decoder
        self.on_caption = on_caption
        self.on_level = on_level
        self.on_error = on_error
        self.stop = threading.Event()
        self.queue = WindowQueue()
        self.failure = None
        self.realtime = realtime
        self.detector = detector

    def run(self, audio):
        worker = threading.Thread(target=self._decode, name="koeha-recognition")
        worker.start()
        segmenter = Segmenter(self.settings, self.detector)
        try:
            for pcm in audio:
                if self.stop.is_set():
                    break
                for window in segmenter.push(pcm):
                    if self.realtime or window.final:
                        self.queue.put(window, block=not self.realtime)
                self.on_level(segmenter.level)
            for window in segmenter.flush():
                self.queue.put(window, block=not self.realtime)
        except Exception as error:
            self.failure = error
            self.on_error(str(error))
        finally:
            self.stop.set()
            self.queue.finish()
            worker.join()
            close = getattr(audio, "close", None)
            if close:
                close()
        if self.failure:
            raise RuntimeError(str(self.failure)) from self.failure

    def _decode(self):
        try:
            while (window := self.queue.get()) is not None:
                if self.stop.is_set() and not window.final:
                    continue
                text = self.decoder.decode(window.pcm).strip()
                self.on_caption(Caption(window.id, window.start, window.end, text, window.final))
        except Exception as error:
            self.failure = error
            self.stop.set()
            self.queue.finish()
            self.on_error(str(error))
