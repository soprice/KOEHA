"""WASAPI loopback for a single process tree using the Windows COM ABI."""
import ctypes as ct
import threading
from ctypes import wintypes as wt
from uuid import UUID

from koeha.audio.applications import PROCESS_QUERY_LIMITED_INFORMATION, process_api, process_image

RATE = 44100
CHANNELS = 2
BLOCK = CHANNELS * 2
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_BUFFERFLAGS_SILENT = 2
HRESULT = wt.LONG
PTR = ct.c_void_p
HRESULT_PTR = ct.POINTER(HRESULT)
PTR_PTR = ct.POINTER(PTR)


class GUID(ct.Structure):
    _fields_ = [("bytes", ct.c_ubyte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(UUID(value).bytes_le)


IID_AUDIO_CLIENT = GUID.parse("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
IID_CAPTURE_CLIENT = GUID.parse("c8adbd64-e71e-48a0-a4de-185c395cd317")
HANDLER_IIDS = {
    UUID(value).bytes_le for value in (
        "00000000-0000-0000-c000-000000000046",  # IUnknown
        "94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90",  # IAgileObject
        "41d949ab-9862-444a-80f6-c261334da5eb",  # IActivateAudioInterfaceCompletionHandler
    )
}


class ProcessActivation(ct.Structure):
    _fields_ = [("type", wt.DWORD), ("pid", wt.DWORD), ("mode", wt.DWORD)]


class ActivationVariant(ct.Structure):
    # PROPVARIANT with its BLOB union member. ctypes handles the pointer alignment.
    _fields_ = [("vt", wt.WORD), ("reserved", wt.WORD * 3),
                ("size", wt.ULONG), ("data", PTR)]


class WaveFormat(ct.Structure):
    _pack_ = 1
    _fields_ = [("tag", wt.WORD), ("channels", wt.WORD), ("rate", wt.DWORD),
                ("bytes_per_second", wt.DWORD), ("block", wt.WORD), ("bits", wt.WORD), ("extra", wt.WORD)]


def check(result):
    if result < 0:
        raise OSError(f"WASAPI application audio failed (0x{result & 0xffffffff:08X})")


def method(interface, slot, result_type, *argument_types):
    vtable = ct.cast(interface, ct.POINTER(ct.POINTER(PTR)))[0]
    return ct.WINFUNCTYPE(result_type, PTR, *argument_types)(vtable[slot])


def release(interface):
    if interface:
        method(interface, 2, wt.ULONG)(interface)  # IUnknown::Release


# Keep callback vtables alive for as long as Windows holds an IUnknown reference.
_handlers = {}


class ActivationHandler:
    def __init__(self, pid):
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.references = 1
        self.abandoned = False
        self.client = PTR()
        self.error = None
        self.params = ProcessActivation(1, pid, 0)  # process loopback, include the process tree
        self.variant = ActivationVariant(65, (wt.WORD * 3)(), ct.sizeof(self.params), ct.addressof(self.params))
        self.callbacks = (
            ct.WINFUNCTYPE(HRESULT, PTR, ct.POINTER(GUID), PTR_PTR)(self.query_interface),
            ct.WINFUNCTYPE(wt.ULONG, PTR)(self.add_ref),
            ct.WINFUNCTYPE(wt.ULONG, PTR)(self.release),
            ct.WINFUNCTYPE(HRESULT, PTR, PTR)(self.complete),
        )
        self.vtable = (PTR * len(self.callbacks))(*(ct.cast(callback, PTR) for callback in self.callbacks))
        self.interface = ct.cast(self.vtable, PTR)
        self.pointer = ct.byref(self.interface)
        _handlers[self.interface.value] = self

    def query_interface(self, this, iid, out):
        if bytes(iid.contents.bytes) not in HANDLER_IIDS:
            out[0] = None
            return -2147467262  # E_NOINTERFACE
        self.add_ref(this)
        out[0] = this
        return 0

    def add_ref(self, _):
        with self.lock:
            self.references += 1
            return self.references

    def release(self, _):
        with self.lock:
            self.references -= 1
            if not self.references:
                _handlers.pop(self.interface.value)
            return self.references

    def complete(self, _, operation):
        result, client = HRESULT(), PTR()
        try:
            get_result = method(operation, 3, HRESULT, HRESULT_PTR, PTR_PTR)  # GetActivateResult
            check(get_result(operation, ct.byref(result), ct.byref(client)))
            check(result.value)
        except Exception as error:
            self.error = error
        with self.lock:
            if self.abandoned or self.error:
                release(client)
            else:
                self.client = client
            self.done.set()
        return 0


def activate(pid, stop):
    api = ct.WinDLL("mmdevapi")
    start = api.ActivateAudioInterfaceAsync
    start.argtypes = [wt.LPCWSTR, ct.POINTER(GUID), ct.POINTER(ActivationVariant), PTR, PTR_PTR]
    start.restype = HRESULT
    handler, operation = ActivationHandler(pid), PTR()
    try:
        check(start(r"VAD\Process_Loopback", ct.byref(IID_AUDIO_CLIENT), ct.byref(handler.variant),
                    handler.pointer, ct.byref(operation)))
        for _ in range(100):
            if handler.done.wait(0.05):
                if handler.error:
                    raise handler.error
                return handler.client
            if stop.is_set():
                break
        with handler.lock:
            handler.abandoned = True
            release(handler.client)
        if not stop.is_set():
            raise TimeoutError("Timed out opening application audio")
        return PTR()
    finally:
        release(operation)
        handler.release(None)


def packets(application, stop):
    kernel = process_api()
    process = kernel.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, application.pid)
    if not process:
        raise RuntimeError("The selected application closed. Choose an application in Live.")
    try:
        if (process_image(kernel, process).casefold() != application.executable.casefold()
                or kernel.WaitForSingleObject(process, 0) == WAIT_OBJECT_0):
            raise RuntimeError("The selected application closed. Choose an application in Live.")
        yield from _capture_packets(kernel, process, application.pid, stop)
    finally:
        kernel.CloseHandle(process)


def _capture_packets(kernel, process, pid, stop):
    ole = ct.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [PTR, wt.DWORD]
    check(ole.CoInitializeEx(None, 0))  # MTA, the same apartment as the WASAPI completion callback
    event, client, capture = None, PTR(), PTR()
    try:
        client = activate(pid, stop)
        if not client or stop.is_set():
            return
        kernel.CreateEventW.argtypes = [PTR, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        kernel.CreateEventW.restype = wt.HANDLE
        event = kernel.CreateEventW(None, False, False, None)
        if not event:
            raise ct.WinError(ct.get_last_error())
        # IAudioClient vtable indexes from audioclient.h.
        initialize = method(client, 3, HRESULT, ct.c_int, wt.DWORD, ct.c_int64, ct.c_int64,
                            ct.POINTER(WaveFormat), PTR)
        fmt = WaveFormat(1, CHANNELS, RATE, RATE * BLOCK, BLOCK, 16, 0)
        flags = AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
        check(initialize(client, 0, flags, 0, 0, ct.byref(fmt), None))
        check(method(client, 13, HRESULT, wt.HANDLE)(client, event))  # SetEventHandle
        check(method(client, 14, HRESULT, ct.POINTER(GUID), PTR_PTR)(  # GetService
            client, ct.byref(IID_CAPTURE_CLIENT), ct.byref(capture)))
        check(method(client, 10, HRESULT)(client))  # Start
        try:
            yield from _read_packets(kernel, process, event, capture, stop)
        finally:
            method(client, 11, HRESULT)(client)  # Stop
    finally:
        release(capture)
        release(client)
        if event:
            kernel.CloseHandle(event)
        ole.CoUninitialize()


def _read_packets(kernel, process, event, capture, stop):
    get_buffer = method(capture, 3, HRESULT, PTR_PTR, ct.POINTER(wt.UINT), ct.POINTER(wt.DWORD), PTR, PTR)
    release_buffer = method(capture, 4, HRESULT, wt.UINT)
    while not stop.is_set():
        status = kernel.WaitForSingleObject(event, 50)
        if status == WAIT_TIMEOUT:
            if kernel.WaitForSingleObject(process, 0) == WAIT_OBJECT_0:
                return
            yield None
            continue
        if status != WAIT_OBJECT_0:
            raise ct.WinError(ct.get_last_error())
        while not stop.is_set():
            data, frames, flags = PTR(), wt.UINT(), wt.DWORD()
            check(get_buffer(capture, ct.byref(data), ct.byref(frames), ct.byref(flags), None, None))
            if not frames.value:
                break
            try:
                length = frames.value * BLOCK
                pcm = bytes(length) if flags.value & AUDCLNT_BUFFERFLAGS_SILENT else ct.string_at(data, length)
            finally:
                check(release_buffer(capture, frames))
            yield pcm
        if kernel.WaitForSingleObject(process, 0) == WAIT_OBJECT_0:
            return
