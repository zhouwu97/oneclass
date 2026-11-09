from __future__ import annotations

import types
import threading
import unittest
from unittest.mock import Mock

from bot.controller import WordBot
from bot.runtime_profile import RuntimeProfile


class FakeVision:
    @staticmethod
    def joined(items):
        return ""


class ControllerTimingTest(unittest.TestCase):
    def make_bot(self) -> WordBot:
        bot = WordBot.__new__(WordBot)
        bot.cfg = {}
        bot.profile = RuntimeProfile(
            name="test",
            loop_interval=0.11,
            after_click_delay=0.22,
            ocr_scale=1.0,
            ocr_min_score=0.45,
            unknown_delay=0.33,
            page_transition_delay=0.44,
            dictation_delay=0.55,
        )
        bot.debug = False
        bot.unknown_count = 0
        bot.vision = FakeVision()
        bot.screen = types.SimpleNamespace(save_debug=lambda *args, **kwargs: None)
        bot.root = None
        bot.click_text = lambda *args, **kwargs: True
        bot.handle_choice = lambda *args, **kwargs: None
        bot.handle_dictation = lambda *args, **kwargs: None
        return bot

    def test_page_transition_state_returns_page_delay(self) -> None:
        bot = self.make_bot()

        delay = bot.act("word_home", [], types.SimpleNamespace(height=1000))

        self.assertEqual(delay, 0.44)

    def test_normal_click_state_returns_after_click_delay(self) -> None:
        bot = self.make_bot()

        delay = bot.act("choice_feedback", [], types.SimpleNamespace(height=1000))

        self.assertEqual(delay, 0.22)

    def test_dictation_state_returns_dictation_delay(self) -> None:
        bot = self.make_bot()

        delay = bot.act("test_dictation", [], types.SimpleNamespace(height=1000))

        self.assertEqual(delay, 0.55)

    def test_unknown_state_returns_unknown_delay(self) -> None:
        bot = self.make_bot()

        delay = bot.act("unknown", [], types.SimpleNamespace(height=1000))

        self.assertEqual(delay, 0.33)
        self.assertEqual(bot.unknown_count, 1)

    def test_stop_during_recording_never_decodes_or_submits(self) -> None:
        bot = self.make_bot()
        bot.stop_requested = False
        bot._stop_event = threading.Event()
        bot.speed = {"max_attempts": 2, "retry_delay": 0.1}
        bot.points = {"speaker": [0.5, 0.3]}
        bot.screen.foreground_click_rel = Mock()
        bot.audio = Mock()
        bot.audio.record_after_click.side_effect = lambda click: bot.stop()
        bot.submit_word = Mock()
        bot.submit_dummy_dictation = Mock()
        bot.handle_dictation = WordBot.handle_dictation.__get__(bot)
        bot.handle_dictation([])
        bot.audio.transcribe.assert_not_called()
        bot.submit_word.assert_not_called()
        bot.submit_dummy_dictation.assert_not_called()

    def test_exercise_returns_numeric_delay(self) -> None:
        bot = self.make_bot()
        bot.handle_exercise = Mock()
        self.assertEqual(bot.act("exercise", [], types.SimpleNamespace(height=1000)), 1.0)


if __name__ == "__main__":
    unittest.main()
