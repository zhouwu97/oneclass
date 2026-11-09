"""Compatibility boundary for SoundCard's Windows WASAPI implementation."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import threading


_patch_lock = threading.Lock()


def soundcard_module():
    import soundcard as sc
    backend = getattr(sc, "mediafoundation", None)
    if os.name != "nt" or backend is None:
        return sc
    with _patch_lock:
        if getattr(backend, "_oneclass_propvariant_fixed", False):
            return sc
        ffi, ole32 = backend._ffi, backend._ole32
        # SoundCard 0.4.6 declares only the pointer arm of PROPVARIANT (16
        # bytes on x64). Windows writes its full union, including BLOB (24
        # bytes), so the original allocation corrupts the native heap.
        required_size = max(ffi.sizeof("PROPVARIANT"), ffi.sizeof("BLOB_PROPVARIANT"))
        if ffi.sizeof("PROPVARIANT") < required_size:
            class SafePropVariant:
                def __init__(self):
                    self.ptr = ffi.cast("PROPVARIANT *", ole32.CoTaskMemAlloc(required_size))
                    if self.ptr == ffi.NULL:
                        raise MemoryError("无法分配音频设备属性内存")
                    ffi.buffer(self.ptr, required_size)[:] = b"\0" * required_size

                def __del__(self):
                    ptr = getattr(self, "ptr", ffi.NULL)
                    if ptr != ffi.NULL:
                        ole32.PropVariantClear(ptr)
                        ole32.CoTaskMemFree(ptr)
                        self.ptr = ffi.NULL
            backend._PropVariant = SafePropVariant
        backend._oneclass_propvariant_fixed = True
    return sc


@contextmanager
def soundcard_session():
    """COM must be initialized and balanced on every recording worker."""
    sc = soundcard_module()
    initialized = False
    if os.name == "nt":
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0)
        initialized = hr in (0, 1)
        if hr not in (0, 1, -2147417850):  # RPC_E_CHANGED_MODE is already usable.
            raise RuntimeError(f"音频 COM 初始化失败：{hr:#x}")
    try:
        yield sc
    finally:
        if initialized:
            ctypes.windll.ole32.CoUninitialize()
