from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

from bot.controller import WordBot
from bot.screen import Region, Screen
from bot.vision import OCRItem, Vision
from gui import LOCAL_MODELS, MODEL_GUIDE, model_guidance


class LiveRuntimeTest(unittest.TestCase):
    def test_learning_uses_word_instead_of_larger_phonetic_label(self):
        for word, phonetic in (("arouse", "英:[arauz]"), ("career", "美：[ka'ra(r)]")):
            with self.subTest(word=word):
                bot = WordBot.__new__(WordBot)
                bot.vision = Vision
                bot.vocab = Mock()
                items = [OCRItem(word, .98, 100, 250, 300, 280),
                         OCRItem(phonetic, .99, 50, 310, 350, 355),
                         OCRItem("n.生涯，职业", .99, 20, 450, 400, 480)]
                bot.learn_from_page(items, types.SimpleNamespace(height=1000))
                bot.vocab.add.assert_called_once_with(word, "n.生涯，职业")

    def test_phonetics_are_not_a_word_when_title_is_missing(self):
        items = [OCRItem("英：['mmimam]", .99, 20, 300, 300, 350),
                 OCRItem("[arauz]", .99, 20, 360, 300, 410)]
        self.assertIsNone(Vision.main_word(items, 1000))

    def test_missing_question_word_gets_focused_ocr_before_unknown(self):
        bot = WordBot.__new__(WordBot)
        bot.cfg = {}
        bot.vision = Mock(wraps=Vision)
        title = OCRItem("minimum", .98, 100, 280, 300, 315)
        bot.vision.recover_word.return_value = ("minimum", title)
        bot.vocab = Mock()
        bot.vocab.definition.return_value = "adj.最低的，最小的"
        bot.screen = Mock()
        bot._choice_unknown = Mock()
        items = [OCRItem(text, .99, 30, 430 + n * 100, 400, 460 + n * 100)
                 for n, text in enumerate(("分类；分级", "卫生", "脚步，足迹", "最低的，最小的"))]
        bot.handle_choice(items, types.SimpleNamespace(height=1000))
        bot.screen.click_abs.assert_called_once_with(items[3].cx, items[3].cy)
        bot._choice_unknown.assert_not_called()

    def test_focused_ocr_preserves_original_title_coordinates(self):
        from PIL import Image
        vision = Vision.__new__(Vision)
        vision.read = Mock(return_value=[OCRItem("minimum", .98, 200, 80, 600, 140)])
        word, item = vision.recover_word(Image.new("RGB", (480, 1000)), (.24, .40))
        self.assertEqual(word, "minimum")
        self.assertEqual((item.x1, item.y1, item.x2, item.y2), (100, 280, 300, 310))
        self.assertEqual(vision.read.call_args.args[0].size, (960, 320))

    def test_loading_question_never_becomes_answerable(self):
        bot = WordBot.__new__(WordBot)
        bot.vision = Vision
        bot.screen = types.SimpleNamespace(region=Region(0, 0, 480, 964))
        bot.api = Mock()
        bot.api.detect_task.return_value = None
        items = [OCRItem(text, 0.99, 0, 100, 300, 130) for text in
                 ("单词测试1/30", "请选择正确的释义", "appreciate", "资源玩命加载中...")]
        self.assertEqual(bot.detect_state(items), "loading")

    def test_bound_screen_moves_without_reusing_old_origin(self):
        screen = Screen([10, 20, 480, 964], input_backend="pyautogui")
        screen.bound_window = 123
        with patch("bot.window_target.client_region", return_value=[600, 100, 480, 964]):
            self.assertEqual(screen.relative(0.5, 0.5), (840, 582))

    def test_closed_bound_window_aborts_before_clicking(self):
        screen = Screen([10, 20, 480, 964], input_backend="pyautogui")
        screen.bound_window = 123
        screen._click_screen = Mock()
        with patch("bot.window_target.client_region", side_effect=RuntimeError("closed")):
            with self.assertRaises(RuntimeError):
                screen.click_rel(0.5, 0.5)
        screen._click_screen.assert_not_called()

    def test_resize_requires_reconfirming_before_clicking(self):
        screen = Screen([10, 20, 480, 964], input_backend="pyautogui")
        screen.bound_window = 123
        screen._click_screen = Mock()
        with patch("bot.window_target.client_region", return_value=[10, 20, 600, 1000]):
            with self.assertRaises(RuntimeError):
                screen.click_rel(0.5, 0.5)
        screen._click_screen.assert_not_called()

    def test_all_local_models_have_visible_tradeoffs(self):
        self.assertEqual(set(LOCAL_MODELS), set(MODEL_GUIDE))
        for model in LOCAL_MODELS:
            self.assertIn("优点：", model_guidance(model))
            self.assertIn("取舍：", model_guidance(model))

    def test_chinese_prompt_selects_english_option_instead_of_unknown(self):
        bot = WordBot.__new__(WordBot)
        bot.cfg = {}
        bot.vision = Vision
        bot.vocab = Mock()
        bot.vocab.definition.side_effect = lambda word: {"wander": "漫游，闲逛", "wonder": "想知道", "walk": "走路", "stroll": "散步"}.get(word, "")
        bot.screen = Mock()
        bot._choice_unknown = Mock()
        items = [OCRItem("漫游，闲逛", 0.99, 20, 290, 300, 320)]
        for index, word in enumerate(("wonder", "wander", "stroll", "walk")):
            items.append(OCRItem(word, 0.99, 30, 430 + index * 100, 300, 460 + index * 100))
        bot.handle_choice(items, types.SimpleNamespace(height=1000))
        bot.screen.click_abs.assert_called_once_with(items[2].cx, items[2].cy)
        bot._choice_unknown.assert_not_called()


@unittest.skipUnless(os.name == "nt" and os.environ.get("ONECLASS_AUDIO_SMOKE") == "1", "optional Windows hardware smoke")
class NativeAudioSmokeTest(unittest.TestCase):
    def test_enumeration_and_recording_survive_across_workers(self):
        script = '''
import concurrent.futures
from bot.audio_backend import soundcard_session
def probe():
    with soundcard_session() as sc:
        devices = sc.all_microphones(include_loopback=True)
        assert devices
        speaker = sc.default_speaker()
        mic = sc.get_microphone(speaker.id, include_loopback=True)
        with mic.recorder(samplerate=48000) as recorder:
            data = recorder.record(numframes=4800)
        assert data.shape[0] == 4800
for _ in range(3):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(probe).result()
print("native audio OK")
'''
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("native audio OK", result.stdout)
