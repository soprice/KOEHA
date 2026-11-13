import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QProgressBar

from koeha.ui import motion


def test_fade_in_removes_its_effect_when_done(app, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    widget = QLabel("page")
    motion.fade_in(widget, duration=40)
    assert widget.graphicsEffect() is not None
    QTest.qWait(150)
    assert widget.graphicsEffect() is None


def test_reduced_motion_jumps_straight_to_the_final_state(app, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: False)
    widget = QLabel("page")
    motion.fade_in(widget)
    assert widget.graphicsEffect() is None
    bar = QProgressBar()
    motion.set_progress(bar, .5)
    assert bar.value() == 500
    dot = QLabel("●")
    pulse = motion.Pulse(dot)
    pulse.set_active(True)
    assert pulse.effect.opacity() == 1.0


def test_progress_glides_forward_and_switches_to_busy(app, monkeypatch):
    monkeypatch.setattr(motion, "enabled", lambda: True)
    bar = QProgressBar()
    motion.set_progress(bar, .5)
    assert bar.value() < 500
    QTest.qWait(400)
    assert bar.value() == 500
    motion.set_progress(bar, .1)
    assert bar.value() == 100
    motion.set_progress(bar, None)
    assert (bar.minimum(), bar.maximum()) == (0, 0)


def test_environment_can_turn_motion_off(monkeypatch):
    monkeypatch.setenv("KOEHA_REDUCE_MOTION", "1")
    motion.enabled.cache_clear()
    try:
        assert motion.enabled() is False
    finally:
        motion.enabled.cache_clear()
