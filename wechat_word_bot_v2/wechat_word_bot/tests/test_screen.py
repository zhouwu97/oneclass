from __future__ import annotations

import unittest

from bot import screen as screen_module
from bot.screen import Screen


class FakeShot:
    size = (1, 1)
    rgb = b"\x00\x00\x00"


class FakeMssSession:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def grab(self, monitor):
        self.calls.append(f"grab:{monitor['width']}x{monitor['height']}")
        return FakeShot()

    def close(self):
        self.calls.append("close")


class ScreenCaptureTest(unittest.TestCase):
    def test_capture_reuses_mss_session(self) -> None:
        original_mss = screen_module.mss.mss
        calls: list[str] = []

        def fake_mss():
            calls.append("mss")
            return FakeMssSession(calls)

        screen_module.mss.mss = fake_mss
        try:
            screen = Screen([0, 0, 1, 1], input_backend="pyautogui")
            screen.capture()
            screen.capture()
            screen.close()
        finally:
            screen_module.mss.mss = original_mss

        self.assertEqual(calls, ["mss", "grab:1x1", "grab:1x1", "close"])


if __name__ == "__main__":
    unittest.main()
