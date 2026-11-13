"""Read-only hardware information; no model imports or downloads."""
import ctypes
import os
import platform
import shutil
import subprocess
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from koeha.core.i18n import tr


def snapshot(root):
    result = {"CPU": platform.processor() or platform.machine(), "Architecture": platform.machine()}
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                result["CPU"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in (
                    "total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            result["RAM"] = f"{memory.total / 1024 ** 3:.1f} GB"
            result["Available RAM"] = f"{memory.available / 1024 ** 3:.1f} GB"
    result["Free storage"] = f"{shutil.disk_usage(root).free / 1024 ** 3:.1f} GB"
    nvidia = shutil.which("nvidia-smi")
    result["NVIDIA GPU"] = tr("Not detected or nvidia-smi is unavailable")
    if nvidia:
        try:
            probe = subprocess.run([nvidia, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if probe.returncode == 0 and probe.stdout.strip():
                rows = [line.rsplit(",", 2) for line in probe.stdout.strip().splitlines()]
                if all(len(row) == 3 for row in rows):
                    result["NVIDIA GPU"] = "\n".join(row[0].strip() for row in rows)
                    result["VRAM"] = "\n".join(f"{float(row[1].strip().split()[0]) / 1024:.1f} GB" for row in rows)
                    result["Driver"] = ", ".join(sorted({row[2].strip() for row in rows}))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return result


class HardwarePanel(QWidget):
    ready = Signal(object)

    def __init__(self, root):
        super().__init__()
        self.root = root
        self.loaded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        self.summary = QVBoxLayout()
        self.summary.setSpacing(12)
        layout.addLayout(self.summary)
        hint = QLabel(tr("GPU acceleration is selected automatically by each installed engine."))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.refresh = QPushButton(tr("Refresh hardware"))
        self.refresh.clicked.connect(self.load)
        layout.addWidget(self.refresh)
        layout.addStretch()
        self.ready.connect(self.render)

    def load(self):
        self.loaded = True
        self.refresh.setEnabled(False)
        def work():
            try:
                result = snapshot(self.root)
            except Exception as error:
                result = {"Hardware": str(error)}
            try:
                self.ready.emit(result)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="koeha-hardware", daemon=True).start()

    def render(self, result):
        while self.summary.count():
            item = self.summary.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        top = QWidget()
        row = QHBoxLayout(top)
        row.setContentsMargins(0, 0, 0, 0)
        def card(title, keys):
            panel = QFrame()
            panel.setObjectName("card")
            content = QVBoxLayout(panel)
            content.setContentsMargins(14, 12, 14, 12)
            heading = QLabel(tr(title))
            heading.setObjectName("panelTitle")
            content.addWidget(heading)
            form = QFormLayout()
            for key in keys:
                if key in result:
                    value = QLabel(str(result[key]))
                    value.setWordWrap(True)
                    form.addRow(tr(key), value)
            content.addLayout(form)
            return panel
        row.addWidget(card("CPU", ["CPU", "Architecture"]), 1)
        row.addWidget(card("Memory capacity", ["RAM", "Available RAM"]), 1)
        self.summary.addWidget(top)
        self.summary.addWidget(card("GPU", ["NVIDIA GPU", "VRAM", "Driver"]))
        self.summary.addWidget(card("Storage", ["Free storage"]))
        self.refresh.setEnabled(True)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.loaded:
            self.load()
