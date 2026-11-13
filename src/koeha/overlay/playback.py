"""Present the newest caption or play finalized captions in order with a delay."""
import math
import time

from PySide6.QtCore import QObject, QTimer

MINIMUM_DISPLAY_SECONDS = 1.5


class OverlayPlayback(QObject):
    def __init__(self, show_caption, delay=None, parent=None):
        super().__init__(parent)
        self.show_caption = show_caption
        self.delay = delay
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)
        self.reset()

    def reset(self):
        self.timer.stop()
        self.pending = {}
        self.current = None
        self.last_id = -1
        self.next_display_at = 0.0

    def configure(self, delay, latest=None):
        if delay == self.delay:
            return
        if (delay is None) != (self.delay is None):
            self.reset()
            if latest is not None:
                if delay is None:
                    self.show_caption(latest.text, latest.translation)
                else:
                    self.last_id = latest.id if latest.final else latest.id - 1
                    self.current = latest if latest.final else None
        self.delay = delay
        if self.pending:
            self._advance()

    def receive(self, caption, latest):
        if self.delay is None:
            self.show_caption(latest.text, latest.translation)
            return
        if not caption.final:
            return
        if caption.id <= self.last_id:
            if self.current and caption.id == self.current.id and caption.translation:
                if caption.translation != self.current.translation:
                    self._present(caption)
                    self._advance()
            return
        if caption.id in self.pending:
            queued, arrived = self.pending[caption.id]
            if caption.translation or not queued.translation:
                self.pending[caption.id] = (caption, arrived)
        else:
            self.pending[caption.id] = (caption, time.monotonic())
        self._advance()

    def _present(self, caption):
        self.current = caption
        self.last_id = caption.id
        self.next_display_at = time.monotonic() + MINIMUM_DISPLAY_SECONDS
        self.show_caption(caption.text, caption.translation)

    def _advance(self):
        while self.pending:
            caption_id = next(iter(self.pending))
            caption, arrived = self.pending[caption_id]
            due = max(arrived + self.delay, self.next_display_at)
            remaining = due - time.monotonic()
            if remaining > 0:
                self.timer.start(math.ceil(remaining * 1000))
                return
            del self.pending[caption_id]
            self._present(caption)
        self.timer.stop()
