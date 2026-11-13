"""Small, interruptible motion that respects the Windows "Animation effects" setting.

Motion here only explains a change (a page arriving, progress moving, work in progress);
it is short, eases out and never delays input.
"""
import os
import sys
from functools import cache

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect

FAST = 140
MEDIUM = 220


@cache
def enabled():
    if os.environ.get("KOEHA_REDUCE_MOTION"):
        return False
    if sys.platform == "win32":
        import ctypes
        value = ctypes.c_int()
        SPI_GETCLIENTAREAANIMATION = 0x1042
        if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0):
            return bool(value.value)
    return True


def fade_in(widget, duration=FAST, start=0.0):
    """Fade a widget in, then drop the effect so normal painting is not slowed down."""
    previous = getattr(widget, "_koeha_fade", None)
    if previous is not None:
        previous.stop()
    if not enabled():
        widget.setGraphicsEffect(None)
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(start)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    def finished():
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        widget._koeha_fade = None
    animation.finished.connect(finished)
    widget._koeha_fade = animation
    animation.start()


def set_progress(bar, fraction):
    """Show determinate progress (glides to the new value) or a busy bar when ``fraction`` is None."""
    animation = getattr(bar, "_koeha_value", None)
    if fraction is None:
        if animation is not None:
            animation.stop()
        bar.setRange(0, 0)
        return
    if bar.maximum() != 1000:
        bar.setRange(0, 1000)
        bar.setValue(0)
    target = round(max(0.0, min(1.0, fraction)) * 1000)
    if not enabled() or target < bar.value():
        if animation is not None:
            animation.stop()
        bar.setValue(target)
        return
    if animation is None:
        animation = bar._koeha_value = QPropertyAnimation(bar, b"value", bar)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    animation.stop()
    animation.setDuration(MEDIUM)
    animation.setStartValue(bar.value())
    animation.setEndValue(target)
    animation.start()


class Pulse:
    """Breathing opacity for a status dot while work is in progress; steady when idle or reduced."""

    def __init__(self, widget, period=1400, low=.3):
        self.effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(self.effect)
        self.animation = QPropertyAnimation(self.effect, b"opacity", widget)
        self.animation.setDuration(period)
        self.animation.setStartValue(1.0)
        self.animation.setKeyValueAt(.5, low)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.animation.setLoopCount(-1)

    def set_active(self, active):
        if active and enabled():
            if self.animation.state() != QPropertyAnimation.State.Running:
                self.animation.start()
        else:
            self.animation.stop()
            self.effect.setOpacity(1.0)
