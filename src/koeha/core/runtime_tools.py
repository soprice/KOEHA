"""Locate executables shipped with the desktop bundle or installed for development."""
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_LAUNCH_LOCK = threading.Lock()


def uv_executable():
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "tools" / "uv.exe"
        if bundled.is_file():
            return str(bundled)
    return shutil.which("uv")


def external_popen(command, **kwargs):
    """Do not leak the desktop bundle's DLL search directory into isolated engines."""
    if not (sys.platform == "win32" and getattr(sys, "frozen", False)):
        return subprocess.Popen(command, **kwargs)
    import ctypes
    import os
    environment = dict(kwargs.pop("env", os.environ))
    bundle = str(Path(sys._MEIPASS).resolve()).casefold()
    environment["PATH"] = os.pathsep.join(part for part in environment.get("PATH", "").split(os.pathsep)
                                          if not part.casefold().startswith(bundle))
    with _LAUNCH_LOCK:
        ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            return subprocess.Popen(command, env=environment, **kwargs)
        finally:
            ctypes.windll.kernel32.SetDllDirectoryW(str(sys._MEIPASS))
