"""Identify Windows desktop applications by the process that owns their window."""
import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MINIMUM_BUILD = 20348


@dataclass(frozen=True)
class Application:
    pid: int
    executable: str
    title: str


def application_audio_supported():
    return sys.platform == "win32" and sys.getwindowsversion().build >= MINIMUM_BUILD


def process_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


def process_image(kernel, process):
    buffer = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buffer))
    if kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
        return buffer.value
    return ""


def applications():
    if not application_audio_supported():
        return []
    user = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi")
    kernel = process_api()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
    found = {}

    @callback_type
    def visit(window, _):
        if not user.IsWindowVisible(window) or user.GetWindow(window, 4):  # GW_OWNER
            return True
        cloaked = wintypes.DWORD()
        dwm.DwmGetWindowAttribute(window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
        if cloaked.value:
            return True
        title = ctypes.create_unicode_buffer(512)
        if not user.GetWindowTextW(window, title, len(title)) or not title.value.strip():
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value in found or pid.value == os.getpid():
            return True
        process = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if process:
            try:
                image = process_image(kernel, process)
                if image and Path(image).name.casefold() != "applicationframehost.exe":
                    found[pid.value] = Application(pid.value, image, title.value.strip())
            finally:
                kernel.CloseHandle(process)
        return True

    if not user.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return sorted(found.values(), key=lambda app: (Path(app.executable).name.casefold(), app.title.casefold()))
