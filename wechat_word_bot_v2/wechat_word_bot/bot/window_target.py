"""Locate the visible OneClass mini-program using physical client coordinates."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class WindowTarget:
    handle: int
    title: str
    region: list[int]


def client_region(handle: int, expected_title: str = "OneClass") -> list[int]:
    if os.name != "nt":
        raise RuntimeError("窗口定位仅支持 Windows")
    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    if not user32.IsWindow(handle) or user32.IsIconic(handle):
        raise RuntimeError("定位的小程序窗口已关闭或最小化，请显示窗口后重新定位")
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(handle, title, len(title))
    if title.value.casefold() != expected_title.casefold():
        raise RuntimeError("定位窗口已变化，请重新选择 OneClass 小程序")
    rect = wintypes.RECT()
    point = wintypes.POINT(0, 0)
    if not user32.GetClientRect(handle, ctypes.byref(rect)) or not user32.ClientToScreen(handle, ctypes.byref(point)):
        raise RuntimeError("无法读取小程序内容区域，请重新定位")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width < 100 or height < 100:
        raise RuntimeError("小程序内容区域过小，请展开窗口后重新定位")
    return [point.x, point.y, width, height]


def discover_windows() -> list[WindowTarget]:
    if os.name != "nt":
        return []
    user32 = ctypes.windll.user32
    result: list[WindowTarget] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(handle, _):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(handle, title, len(title))
        if title.value.casefold() == "oneclass" and user32.IsWindowVisible(handle):
            try:
                result.append(WindowTarget(int(handle), title.value, client_region(handle, title.value)))
            except RuntimeError:
                pass
        return True

    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows(visit, 0)
    return result
