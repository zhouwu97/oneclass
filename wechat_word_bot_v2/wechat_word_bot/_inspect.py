"""Scratch tool: inspect the OneClass mini-program without stealing focus.

Capture via PrintWindow (works even when covered); input via PostMessage.
Usage:
  python _inspect.py                  # capture only
  python _inspect.py click NX NY      # background click, then capture
  python _inspect.py scroll NX NY N   # wheel notches (negative = down), then capture
"""
from __future__ import annotations

import sys
import time
import ctypes
from ctypes import wintypes

from bot.windows_dpi import enable_dpi_awareness

enable_dpi_awareness()

from PIL import Image

u = ctypes.windll.user32
g = ctypes.windll.gdi32
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON = 0x0001
PW_RENDERFULLCONTENT = 2


def find_window(title: str) -> int:
    found = []

    def cb(hwnd, lp):
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == title:
                found.append(hwnd)
        return True

    u.EnumWindows(EnumWindowsProc(cb), 0)
    if not found:
        raise SystemExit(f"window not found: {title!r}")
    return found[0]


def capture(hwnd: int, out: str) -> None:
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mem = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, h)
    g.SelectObject(mem, bmp)
    ok = u.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    img = Image.frombuffer("RGB", (w, h), _bitmap_bytes(bmp, w, h), "raw", "BGRX", 0, 1)
    g.DeleteObject(bmp)
    g.DeleteDC(mem)
    u.ReleaseDC(hwnd, hdc)
    img.save(out)
    print(f"PrintWindow ok={ok} rect={r.left},{r.top} {w}x{h} saved {out}")


def _bitmap_bytes(bmp: int, w: int, h: int) -> bytes:
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = u.GetDC(None)
    g.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    u.ReleaseDC(None, hdc)
    return buf.raw


def client_xy(hwnd: int, nx: float, ny: float) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    sx, sy = int(r.left + nx * w), int(r.top + ny * h)
    p = wintypes.POINT(sx, sy)
    u.ScreenToClient(hwnd, ctypes.byref(p))
    return p.x, p.y, sx, sy


def raise_window(hwnd: int) -> None:
    fg = u.GetForegroundWindow()
    fg_tid = u.GetWindowThreadProcessId(fg, None)
    my_tid = ctypes.windll.kernel32.GetCurrentThreadId()
    u.AttachThreadInput(my_tid, fg_tid, True)
    u.ShowWindow(hwnd, 5)
    u.SetForegroundWindow(hwnd)
    u.BringWindowToTop(hwnd)
    u.AttachThreadInput(my_tid, fg_tid, False)
    time.sleep(0.4)
    print("foreground now:", u.GetForegroundWindow(), "target:", hwnd)


def main() -> None:
    hwnd = find_window("OneClass")
    args = sys.argv[1:]
    if args and args[0] == "click":
        cx, cy, sx, sy = client_xy(hwnd, float(args[1]), float(args[2]))
        lp = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)
        u.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
        u.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        u.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
        print(f"clicked client {cx},{cy} (screen {sx},{sy})")
        time.sleep(1.2)
    elif args and args[0] == "fclick":
        import pyautogui
        cx, cy, sx, sy = client_xy(hwnd, float(args[1]), float(args[2]))
        old = pyautogui.position()
        raise_window(hwnd)
        pyautogui.click(sx, sy)
        time.sleep(1.5)
        pyautogui.moveTo(old.x, old.y)
        print(f"foreground clicked screen {sx},{sy}")
    elif args and args[0] == "pagedown":
        import pyautogui
        cx, cy, sx, sy = client_xy(hwnd, float(args[1]), float(args[2]))
        times = int(args[3]) if len(args) > 3 else 1
        old = pyautogui.position()
        raise_window(hwnd)
        pyautogui.click(sx, sy)
        time.sleep(0.5)
        for _ in range(times):
            pyautogui.press("pagedown")
            time.sleep(0.45)
        time.sleep(0.8)
        pyautogui.moveTo(old.x, old.y)
        print(f"pagedown x{times}")
    elif args and args[0] == "scroll":
        cx, cy, sx, sy = client_xy(hwnd, float(args[1]), float(args[2]))
        notches = int(args[3])
        lp = ((sy & 0xFFFF) << 16) | (sx & 0xFFFF)
        for _ in range(abs(notches)):
            wp = ((-120 if notches < 0 else 120) << 16) & 0xFFFFFFFF
            u.PostMessageW(hwnd, WM_MOUSEWHEEL, wp, lp)
            time.sleep(0.08)
        print(f"scrolled {notches} notches at screen {sx},{sy}")
        time.sleep(1.2)
    elif args and args[0] == "fscroll":
        import pyautogui
        cx, cy, sx, sy = client_xy(hwnd, float(args[1]), float(args[2]))
        notches = int(args[3])
        old = pyautogui.position()
        raise_window(hwnd)
        pyautogui.moveTo(sx, sy)
        pyautogui.scroll(notches)
        time.sleep(1.0)
        pyautogui.moveTo(old.x, old.y)
        print(f"foreground scrolled {notches}")
    capture(hwnd, "logs/inspect.png")


if __name__ == "__main__":
    main()