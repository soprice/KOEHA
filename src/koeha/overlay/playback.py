"""Coordinate FloaSub playback: committed translation queue, live preview strip, and hold locks."""
import math
import time
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QTimer

from koeha.core.settings import (
    OVERLAY_FLOW_COMMITTED,
    OVERLAY_FLOW_LIVE_SINGLE,
    OVERLAY_FLOW_SMART_DUAL,
    OVERLAY_LIVE_FULL,
    OVERLAY_LIVE_OFF,
    OVERLAY_LIVE_RAW,
)

MINIMUM_DISPLAY_SECONDS = 1.5
MAX_PENDING_CAPTIONS = 4


@dataclass(frozen=True)
class OverlayView:
    original: str = ""
    translation: str = ""
    translation_draft: bool = False
    translation_pending: bool = False
    live_original: str = ""
    live_stable: str = ""
    live_translation: str = ""
    live_translation_stale: bool = False
    live_translating: bool = False
    idle_hidden: bool = False


def normalize_flow(timing):
    if timing == "delayed":
        return OVERLAY_FLOW_COMMITTED
    if timing == "immediate":
        return OVERLAY_FLOW_LIVE_SINGLE
    if timing in (OVERLAY_FLOW_SMART_DUAL, OVERLAY_FLOW_COMMITTED, OVERLAY_FLOW_LIVE_SINGLE):
        return timing
    return OVERLAY_FLOW_SMART_DUAL


class OverlayPlayback(QObject):
    def __init__(
        self,
        show_caption,
        delay=None,
        parent=None,
        *,
        flow=None,
        live_mode=OVERLAY_LIVE_FULL,
        translation_enabled=True,
        draft_enabled=True,
        wait_seconds=2.5,
        min_display_seconds=MINIMUM_DISPLAY_SECONDS,
        auto_hide_seconds=0.0,
    ):
        super().__init__(parent)
        self.show_caption = show_caption
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self._on_idle_timeout)

        if flow is None:
            flow = OVERLAY_FLOW_COMMITTED if delay is not None else OVERLAY_FLOW_LIVE_SINGLE
        self.flow = normalize_flow(flow)
        self.live_mode = live_mode if live_mode in (OVERLAY_LIVE_OFF, OVERLAY_LIVE_RAW, OVERLAY_LIVE_FULL) else OVERLAY_LIVE_FULL
        self.translation_enabled = bool(translation_enabled)
        self.draft_enabled = bool(draft_enabled)
        self.delay = 0.0 if delay is None else float(delay)
        self.wait_seconds = float(wait_seconds)
        self.min_display_seconds = float(min_display_seconds)
        self.auto_hide_seconds = float(auto_hide_seconds)
        self.reset()

    def reset(self):
        self.timer.stop()
        self.idle_timer.stop()
        self.pending = {}
        self.current = None
        self.last_id = -1
        self.next_display_at = 0.0
        self.live_caption = None
        self.live_stable = ""
        self.queued_preview = None
        self.queued_preview_stable = ""
        self.current_finalized_at = 0.0
        self.last_activity_at = 0.0
        self.idle_hidden = False

    def configure(
        self,
        delay=0.0,
        latest=None,
        *,
        flow=None,
        live_mode=None,
        translation_enabled=None,
        draft_enabled=None,
        wait_seconds=None,
        min_display_seconds=None,
        auto_hide_seconds=None,
    ):
        new_flow = self.flow if flow is None else normalize_flow(flow)
        if flow is None and delay is None:
            new_flow = OVERLAY_FLOW_LIVE_SINGLE
        elif flow is None and delay is not None:
            new_flow = OVERLAY_FLOW_COMMITTED

        new_delay = 0.0 if delay is None else float(delay)
        new_live_mode = self.live_mode if live_mode is None else live_mode
        new_translation = self.translation_enabled if translation_enabled is None else bool(translation_enabled)
        new_draft = self.draft_enabled if draft_enabled is None else bool(draft_enabled)
        new_wait = self.wait_seconds if wait_seconds is None else float(wait_seconds)
        new_min = self.min_display_seconds if min_display_seconds is None else float(min_display_seconds)
        new_hide = self.auto_hide_seconds if auto_hide_seconds is None else float(auto_hide_seconds)

        flow_changed = new_flow != self.flow
        changed = (
            flow_changed
            or new_delay != self.delay
            or new_live_mode != self.live_mode
            or new_translation != self.translation_enabled
            or new_draft != self.draft_enabled
            or new_wait != self.wait_seconds
            or new_min != self.min_display_seconds
            or new_hide != self.auto_hide_seconds
        )
        if not changed:
            return

        self.flow = new_flow
        self.live_mode = new_live_mode
        self.translation_enabled = new_translation
        self.draft_enabled = new_draft
        self.delay = new_delay
        self.wait_seconds = new_wait
        self.min_display_seconds = new_min
        self.auto_hide_seconds = new_hide

        if flow_changed:
            self.timer.stop()
            if self.flow == OVERLAY_FLOW_LIVE_SINGLE:
                self.pending.clear()
                self.live_caption = None
                self.live_stable = ""
                if latest is not None:
                    self.current = latest
                    self.last_id = latest.id
                    self.next_display_at = 0.0
                    self._emit_view()
            else:
                if self.flow == OVERLAY_FLOW_COMMITTED:
                    self.live_caption = None
                    self.live_stable = ""
                if latest is not None and not latest.final and self.flow == OVERLAY_FLOW_SMART_DUAL:
                    if latest.id > self.last_id:
                        self.live_caption = latest
                elif latest is not None and latest.final and self.current is None and not self.pending:
                    self.last_id = latest.id
                    self.current = latest
                self._emit_view()
        else:
            if self.flow == OVERLAY_FLOW_COMMITTED or self.live_mode == OVERLAY_LIVE_OFF:
                self.live_caption = None
                self.live_stable = ""
            if self.current is not None or self.live_caption is not None:
                self._emit_view()

        if self.pending or self.queued_preview is not None:
            self._advance()
        self._schedule_idle()

    def _is_translation_ready(self, caption):
        if not self.translation_enabled:
            return True
        if caption.translation_error:
            return True
        return bool(caption.translation) and not caption.translation_draft

    def _display_translation(self, caption):
        if not caption:
            return ""
        if self.flow != OVERLAY_FLOW_LIVE_SINGLE and caption.translation_draft:
            return ""
        if caption.translation:
            return caption.translation
        return ""

    def _emit_view(self):
        self.idle_hidden = False
        self.last_activity_at = time.monotonic()

        main = self.current
        main_original = main.text if main else ""
        main_translation = self._display_translation(main)
        main_draft = bool(main and main_translation and (main.translation_draft or not main.final))
        main_pending = bool(
            main
            and main.final
            and self.translation_enabled
            and not main.translation_error
            and (not main_translation or main_draft)
        )

        live_original = ""
        live_stable = ""
        live_translation = ""
        live_stale = False
        live_translating = False

        if self.flow == OVERLAY_FLOW_SMART_DUAL and self.live_mode != OVERLAY_LIVE_OFF and self.live_caption:
            live = self.live_caption
            live_original = live.text
            live_stable = self.live_stable if live.text.startswith(self.live_stable) else ""
            if self.live_mode == OVERLAY_LIVE_FULL and self.translation_enabled:
                live_translation = live.translation
                if live_translation and live.translated_source:
                    live_stale = live.translated_source != live.text
                live_translating = self.draft_enabled and bool(live.text) and (
                    not live_translation or live_stale
                )

        view = OverlayView(
            original=main_original,
            translation=main_translation,
            translation_draft=main_draft,
            translation_pending=main_pending,
            live_original=live_original,
            live_stable=live_stable,
            live_translation=live_translation,
            live_translation_stale=live_stale,
            live_translating=live_translating,
            idle_hidden=False,
        )
        self._deliver(view)
        self._schedule_idle()

    def _deliver(self, view):
        try:
            self.show_caption(view)
        except TypeError:
            self.show_caption(view.original, view.translation)

    def _schedule_idle(self):
        if self.auto_hide_seconds <= 0 or (not self.current and not self.live_caption):
            self.idle_timer.stop()
            return
        if self.pending or self.queued_preview is not None:
            self.idle_timer.stop()
            return
        self.idle_timer.start(math.ceil(self.auto_hide_seconds * 1000))

    def _on_idle_timeout(self):
        if self.auto_hide_seconds <= 0 or self.pending or self.queued_preview is not None:
            return
        if time.monotonic() - self.last_activity_at < self.auto_hide_seconds - 0.05:
            self._schedule_idle()
            return
        self.idle_hidden = True
        self._deliver(OverlayView(idle_hidden=True))

    def receive(self, caption, latest=None, *, stable=""):
        if self.flow == OVERLAY_FLOW_LIVE_SINGLE:
            self._receive_single(caption, latest, stable=stable)
            return
        self._receive_queued(caption, stable=stable)

    def _receive_single(self, caption, latest=None, *, stable=""):
        now = time.monotonic()
        target = caption if caption is not None else latest
        if target is None:
            return

        if target.final:
            if self.current is not None and target.id < self.current.id:
                return
            if self.current is not None and target.id == self.current.id:
                was_ready = self._is_translation_ready(self.current)
                self.current = target
                self.last_id = max(self.last_id, target.id)
                if not was_ready and self._is_translation_ready(target):
                    self.next_display_at = now + self.min_display_seconds
                elif not self.current_finalized_at:
                    self.current_finalized_at = now
                self._emit_view()
                self._advance()
                return

            # New finalized utterance while previous utterance may still be holding.
            if self.current is not None and self.current.final and now < self.next_display_at:
                if target.id in self.pending:
                    _, arrived = self.pending[target.id]
                    self.pending[target.id] = (target, arrived)
                else:
                    self.pending[target.id] = (target, now)
                self._advance()
                return

            self.pending.pop(target.id, None)
            self.queued_preview = None
            self.current = target
            self.last_id = target.id
            self.current_finalized_at = now
            self.next_display_at = (
                now + self.min_display_seconds if self._is_translation_ready(target) else now
            )
            self._emit_view()
            return

        # Live preview (final=False) in live_single mode.
        if self.current is not None and target.id < self.current.id:
            return
        if self.current is not None and target.id > self.current.id and self.current.final:
            hold_until = self.next_display_at
            if self.translation_enabled and not self._is_translation_ready(self.current) and self.wait_seconds > 0:
                hold_until = max(
                    hold_until,
                    self.current_finalized_at + min(self.wait_seconds, self.min_display_seconds),
                )
            if now < hold_until:
                self.queued_preview = target
                self.queued_preview_stable = stable
                self._schedule_timer_until(hold_until, now)
                return

        self.queued_preview = None
        self.current = target
        self.last_id = max(self.last_id, target.id)
        self._emit_view()

    def _receive_queued(self, caption, *, stable=""):
        now = time.monotonic()
        if not caption.final:
            if self.flow == OVERLAY_FLOW_SMART_DUAL and self.live_mode != OVERLAY_LIVE_OFF:
                if caption.id > self.last_id and caption.id not in self.pending:
                    self.live_caption = caption
                    self.live_stable = stable
                    self._emit_view()
            return

        if caption.id <= self.last_id:
            if self.current and caption.id == self.current.id:
                updated = self._merge_final(self.current, caption)
                if updated != self.current:
                    had_final_translation = bool(self.current.translation) and not self.current.translation_draft
                    has_final_translation = bool(updated.translation) and not updated.translation_draft
                    self.current = updated
                    if not had_final_translation and has_final_translation:
                        self.next_display_at = max(self.next_display_at, now + self.min_display_seconds)
                    self._emit_view()
                    self._advance()
            return

        if caption.id in self.pending:
            queued, arrived = self.pending[caption.id]
            self.pending[caption.id] = (self._merge_final(queued, caption), arrived)
        else:
            self.pending[caption.id] = (caption, now)
            while len(self.pending) > MAX_PENDING_CAPTIONS:
                oldest = next(iter(self.pending))
                del self.pending[oldest]
                self.last_id = max(self.last_id, oldest)

        if self.live_caption and self.live_caption.id == caption.id:
            if self.flow == OVERLAY_FLOW_SMART_DUAL and self.live_mode != OVERLAY_LIVE_OFF:
                self.live_caption = self._merge_final(self.live_caption, caption)
                self._emit_view()

        self._advance()

    @staticmethod
    def _merge_final(existing, incoming):
        if not existing:
            return incoming
        if incoming.translation_error:
            return incoming
        if incoming.translation and (not incoming.translation_draft or existing.translation_draft or not existing.translation):
            return incoming
        if existing.translation and not incoming.translation:
            return replace(
                incoming,
                translation=existing.translation,
                translation_draft=existing.translation_draft,
                translated_source=existing.translated_source,
            )
        return incoming

    def _effective_min_display(self):
        if len(self.pending) >= 3:
            return max(0.7, self.min_display_seconds * 0.55)
        return self.min_display_seconds

    def _present(self, caption):
        now = time.monotonic()
        self.current = caption
        self.last_id = max(self.last_id, caption.id)
        self.current_finalized_at = now
        if self.flow == OVERLAY_FLOW_LIVE_SINGLE and not self._is_translation_ready(caption):
            self.next_display_at = now
        else:
            self.next_display_at = now + self._effective_min_display()
        if self.live_caption and self.live_caption.id <= self.last_id:
            self.live_caption = None
            self.live_stable = ""
        if self.queued_preview and self.queued_preview.id <= self.last_id:
            self.queued_preview = None
            self.queued_preview_stable = ""
        self._emit_view()

    def _schedule_timer_until(self, due, now):
        remaining = due - now
        if remaining > 0:
            self.timer.start(math.ceil(remaining * 1000))
            return True
        return False

    def _advance(self):
        now = time.monotonic()
        if self.flow == OVERLAY_FLOW_LIVE_SINGLE:
            while self.pending:
                caption_id = next(iter(self.pending))
                caption, _ = self.pending[caption_id]
                if now < self.next_display_at:
                    self._schedule_timer_until(self.next_display_at, now)
                    return
                del self.pending[caption_id]
                self._present(caption)
            if self.queued_preview is not None:
                hold_until = self.next_display_at
                if (
                    self.current is not None
                    and self.current.final
                    and self.translation_enabled
                    and not self._is_translation_ready(self.current)
                    and self.wait_seconds > 0
                ):
                    hold_until = max(
                        hold_until,
                        self.current_finalized_at + min(self.wait_seconds, self.min_display_seconds),
                    )
                if self._schedule_timer_until(hold_until, now):
                    return
                preview = self.queued_preview
                self.queued_preview = None
                self.current = preview
                self.last_id = max(self.last_id, preview.id)
                self._emit_view()
            self.timer.stop()
            return

        while self.pending:
            caption_id = next(iter(self.pending))
            caption, arrived = self.pending[caption_id]
            sync_due = arrived + self.delay
            read_due = self.next_display_at
            if self.current is not None and len(self.pending) >= 3:
                read_due = min(read_due, self.current_finalized_at + self._effective_min_display())
            due = max(sync_due, read_due)
            if not self._is_translation_ready(caption):
                due = max(due, arrived + self.delay + self.wait_seconds)
            if self._schedule_timer_until(due, now):
                return
            del self.pending[caption_id]
            self._present(caption)
        self.timer.stop()
        self._schedule_idle()
