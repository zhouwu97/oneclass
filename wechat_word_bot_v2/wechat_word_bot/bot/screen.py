from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import mss
import pyautogui
import pyperclip
from PIL import Image


LOG = logging.getLogger("wordbot")


@dataclass(frozen=True)
class Region:
    left: int
    top: int
    width: int
    height: int


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class Win32BackgroundInput:
    """通过窗口消息点击/输入，不移动系统鼠标。

    这适用于保持微信小程序窗口可见、但用户在另一块屏幕或其他
    不遮挡区域继续使用鼠标的场景。部分特殊窗口可能不接受后台消息，
    因此 Screen 仍保留 pyautogui 模式作为兼容后备。
    """

    WM_MOUSEMOVE = 0x0200
    WM_LBUTTONDOWN = 0x0201
    WM_LBUTTONUP = 0x0202
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_CHAR = 0x0102
    MK_LBUTTON = 0x0001
    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_BACK = 0x08

    def __init__(self) -> None:
        if not hasattr(ctypes, "windll"):
            raise RuntimeError("win32 后台输入仅支持 Windows")
        self.user32 = ctypes.windll.user32
        self.user32.WindowFromPoint.argtypes = [POINT]
        self.user32.WindowFromPoint.restype = wintypes.HWND
        self.user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
        self.user32.ScreenToClient.restype = wintypes.BOOL
        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.last_hwnd: int | None = None

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return ((y & 0xFFFF) << 16) | (x & 0xFFFF)

    def _target_at(self, screen_x: int, screen_y: int) -> tuple[int, int, int]:
        point = POINT(int(screen_x), int(screen_y))
        hwnd = int(self.user32.WindowFromPoint(point))
        if not hwnd:
            raise RuntimeError(f"坐标 ({screen_x}, {screen_y}) 下没有可用窗口")
        client = POINT(int(screen_x), int(screen_y))
        if not self.user32.ScreenToClient(hwnd, ctypes.byref(client)):
            raise ctypes.WinError()
        self.last_hwnd = hwnd
        return hwnd, int(client.x), int(client.y)

    def click(self, screen_x: int, screen_y: int) -> None:
        hwnd, client_x, client_y = self._target_at(screen_x, screen_y)
        lp = self._lparam(client_x, client_y)
        self.user32.PostMessageW(hwnd, self.WM_MOUSEMOVE, 0, lp)
        self.user32.PostMessageW(hwnd, self.WM_LBUTTONDOWN, self.MK_LBUTTON, lp)
        self.user32.PostMessageW(hwnd, self.WM_LBUTTONUP, 0, lp)

    def replace_text(self, text: str, screen_x: int, screen_y: int) -> None:
        """点击输入框并向目标窗口发送字符，不使用系统键盘焦点。"""
        hwnd, client_x, client_y = self._target_at(screen_x, screen_y)
        lp = self._lparam(client_x, client_y)
        self.user32.PostMessageW(hwnd, self.WM_MOUSEMOVE, 0, lp)
        self.user32.PostMessageW(hwnd, self.WM_LBUTTONDOWN, self.MK_LBUTTON, lp)
        self.user32.PostMessageW(hwnd, self.WM_LBUTTONUP, 0, lp)
        time.sleep(0.08)

        # 尝试 Ctrl+A 后退格清空输入框。
        self.user32.PostMessageW(hwnd, self.WM_KEYDOWN, self.VK_CONTROL, 0)
        self.user32.PostMessageW(hwnd, self.WM_KEYDOWN, self.VK_A, 0)
        self.user32.PostMessageW(hwnd, self.WM_KEYUP, self.VK_A, 0)
        self.user32.PostMessageW(hwnd, self.WM_KEYUP, self.VK_CONTROL, 0)
        self.user32.PostMessageW(hwnd, self.WM_KEYDOWN, self.VK_BACK, 0)
        self.user32.PostMessageW(hwnd, self.WM_KEYUP, self.VK_BACK, 0)

        for char in text:
            self.user32.PostMessageW(hwnd, self.WM_CHAR, ord(char), 0)
            time.sleep(0.015)


class Screen:
    def __init__(
        self,
        region: list[int],
        after_click_delay: float = 0.8,
        input_backend: str = "win32",
        win32_fallback_to_pyautogui: bool = True,
    ):
        self.region = Region(*map(int, region))
        self.after_click_delay = float(after_click_delay)
        self.input_backend = str(input_backend).lower().strip()
        self.win32_fallback_to_pyautogui = bool(win32_fallback_to_pyautogui)
        self.background: Win32BackgroundInput | None = None

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.05

        if self.input_backend == "win32":
            try:
                self.background = Win32BackgroundInput()
                LOG.info("输入模式：Win32 后台窗口消息（不会移动鼠标）")
            except Exception as exc:  # noqa: BLE001
                if not self.win32_fallback_to_pyautogui:
                    raise
                LOG.warning("Win32 后台输入初始化失败，退回 PyAutoGUI：%s", exc)
                self.input_backend = "pyautogui"
        elif self.input_backend == "pyautogui":
            LOG.info("输入模式：PyAutoGUI（会移动并占用鼠标）")
        else:
            raise ValueError("input_backend 只能是 win32 或 pyautogui")

    def capture(self) -> Image.Image:
        monitor = {
            "left": self.region.left,
            "top": self.region.top,
            "width": self.region.width,
            "height": self.region.height,
        }
        with mss.mss() as sct:
            shot = sct.grab(monitor)
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def absolute(self, x: float, y: float) -> tuple[int, int]:
        return self.region.left + round(x), self.region.top + round(y)

    def relative(self, nx: float, ny: float) -> tuple[int, int]:
        return self.absolute(nx * self.region.width, ny * self.region.height)

    def _click_screen(self, screen_x: int, screen_y: int) -> None:
        if self.input_backend == "win32" and self.background is not None:
            try:
                self.background.click(screen_x, screen_y)
                return
            except Exception as exc:  # noqa: BLE001
                if not self.win32_fallback_to_pyautogui:
                    raise
                LOG.warning("后台点击失败，单次退回 PyAutoGUI：%s", exc)
        pyautogui.click(screen_x, screen_y)

    def click_abs(self, x: float, y: float, delay: float | None = None) -> None:
        self._click_screen(*self.absolute(x, y))
        time.sleep(self.after_click_delay if delay is None else delay)

    def click_rel(self, nx: float, ny: float, delay: float | None = None) -> None:
        self._click_screen(*self.relative(nx, ny))
        time.sleep(self.after_click_delay if delay is None else delay)

    def replace_text_rel(self, nx: float, ny: float, text: str, delay: float = 0.15) -> None:
        screen_x, screen_y = self.relative(nx, ny)
        if self.input_backend == "win32" and self.background is not None:
            try:
                self.background.replace_text(text, screen_x, screen_y)
                time.sleep(delay)
                return
            except Exception as exc:  # noqa: BLE001
                if not self.win32_fallback_to_pyautogui:
                    raise
                LOG.warning("后台文字输入失败，单次退回 PyAutoGUI：%s", exc)

        pyautogui.click(screen_x, screen_y)
        time.sleep(0.08)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.write(text, interval=0.015)
        time.sleep(delay)

    def _restore_user_state(self, old_pos, old_hwnd: int | None) -> None:
        try:
            pyautogui.moveTo(old_pos.x, old_pos.y, duration=0)
        except Exception:  # noqa: BLE001
            pass
        if old_hwnd and hasattr(ctypes, "windll"):
            try:
                ctypes.windll.user32.SetForegroundWindow(old_hwnd)
            except Exception:  # noqa: BLE001
                pass

    def foreground_click_rel(
        self,
        nx: float,
        ny: float,
        delay: float = 0.20,
    ) -> None:
        """短暂使用真实鼠标点击 WebView 控件，随后恢复鼠标和焦点。"""
        screen_x, screen_y = self.relative(nx, ny)
        old_pos = pyautogui.position()
        old_hwnd = None
        if hasattr(ctypes, "windll"):
            try:
                old_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                old_hwnd = None
        try:
            pyautogui.click(screen_x, screen_y)
            time.sleep(delay)
        finally:
            self._restore_user_state(old_pos, old_hwnd)

    def foreground_fill_and_click_abs(
        self,
        input_x: float,
        input_y: float,
        text: str,
        confirm_x: float,
        confirm_y: float,
        delay: float | None = None,
    ) -> None:
        """真实点击输入框、粘贴文字并点击确定，然后恢复用户状态。"""
        input_screen = self.absolute(input_x, input_y)
        confirm_screen = self.absolute(confirm_x, confirm_y)
        old_pos = pyautogui.position()
        old_hwnd = None
        old_clipboard: str | None = None
        if hasattr(ctypes, "windll"):
            try:
                old_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                old_hwnd = None
        try:
            try:
                old_clipboard = pyperclip.paste()
            except Exception:  # noqa: BLE001
                old_clipboard = None

            pyautogui.click(*input_screen)
            time.sleep(0.25)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.30)
            pyautogui.click(*confirm_screen)
            time.sleep(self.after_click_delay if delay is None else delay)
        finally:
            if old_clipboard is not None:
                try:
                    pyperclip.copy(old_clipboard)
                except Exception:  # noqa: BLE001
                    pass
            self._restore_user_state(old_pos, old_hwnd)

    def save_debug(self, image: Image.Image, folder: Path, name: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        image.save(path)
        return path
