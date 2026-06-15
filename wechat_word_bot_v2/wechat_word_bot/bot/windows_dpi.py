from __future__ import annotations

import ctypes
import logging
import os


LOG = logging.getLogger("wordbot")


def enable_dpi_awareness() -> str:
    """Make MSS and PyAutoGUI use the same physical-pixel coordinate system.

    This is important on Windows when custom display scaling (for example 115%)
    is enabled. The function is deliberately safe to call more than once.
    """
    if os.name != "nt":
        return "not-windows"

    # Best option on modern Windows 10/11: per-monitor DPI awareness v2.
    try:
        context_per_monitor_v2 = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(context_per_monitor_v2):
            return "per-monitor-v2"
    except Exception:  # noqa: BLE001
        pass

    # Fallback for Windows 8.1+.
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result in (0, -2147024891):  # S_OK or E_ACCESSDENIED (already set)
            return "per-monitor"
    except Exception:  # noqa: BLE001
        pass

    # Old fallback.
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system-aware"
    except Exception:  # noqa: BLE001
        pass

    return "unavailable"


def system_scale_percent() -> int | None:
    """Return the approximate Windows scale percentage, when available."""
    if os.name != "nt":
        return None
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        return round(dpi / 96 * 100)
    except Exception:  # noqa: BLE001
        return None
