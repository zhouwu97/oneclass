import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BOT_ROOT = Path(__file__).resolve().parents[1]
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from bot.config import SPEED_PRESETS, speed_settings, load_config, save_config
from bot.vocab import VocabStore
from bot.vision import OCRItem, Vision, clean_word, norm_text, chinese_similarity
from bot.api_solver import Answer, ApiSolveError, VisionAPI, _extract_json
from bot.audio_solver import AudioSolver
from bot.exercise import ExerciseRunner, BLANK_RE, BLANK_PREFIX_RE, OPTION_RE
from bot.screen import Screen, Region


class TestConfig(unittest.TestCase):
    def test_speed_settings_defaults(self):
        cfg = {"speed_preset": "fast"}
        s = speed_settings(cfg)
        self.assertEqual(s["record_seconds"], 2.0)
        self.assertEqual(s["beam_size"], 1)
        self.assertEqual(s["max_attempts"], 1)

        cfg = {"speed_preset": "balanced"}
        s = speed_settings(cfg)
        self.assertEqual(s["record_seconds"], 2.4)
        self.assertEqual(s["beam_size"], 1)
        self.assertEqual(s["max_attempts"], 2)

        cfg = {"speed_preset": "accurate"}
        s = speed_settings(cfg)
        self.assertEqual(s["record_seconds"], 3.2)
        self.assertEqual(s["beam_size"], 5)
        self.assertEqual(s["max_attempts"], 3)

    def test_speed_settings_overrides(self):
        cfg = {
            "speed_preset": "fast",
            "audio_record_seconds": 4.0,
            "dictation_max_attempts": 5,
        }
        s = speed_settings(cfg)
        self.assertEqual(s["record_seconds"], 4.0)
        self.assertEqual(s["max_attempts"], 5)


class TestVocab(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        if self.path.exists():
            self.path.unlink()

    def test_vocab_crud(self):
        store = VocabStore(self.path)
        self.assertEqual(store.words(), [])

        self.assertTrue(store.add("abandon", "放弃"))
        self.assertEqual(store.definition("abandon"), "放弃")
        self.assertEqual(store.definition("Abandon"), "放弃")

        self.assertTrue(store.add("abandon", "放弃；抛弃"))
        self.assertEqual(store.definition("abandon"), "放弃；抛弃")

        self.assertFalse(store.add("abandon", "丢弃"))
        self.assertEqual(store.definition("abandon"), "放弃；抛弃")

        word, score = store.closest_word("abandun")
        self.assertEqual(word, "abandon")
        self.assertGreater(score, 80)


class TestVision(unittest.TestCase):
    def test_norm_text(self):
        self.assertEqual(norm_text(" 词 汇 "), "词汇")
        self.assertEqual(norm_text("Hello  World"), "helloworld")

    def test_clean_word(self):
        self.assertEqual(clean_word("  Hello-world! "), "hello-world")
        self.assertEqual(clean_word("123 apple 456"), "apple")
        self.assertEqual(clean_word(""), "")

    def test_chinese_similarity(self):
        self.assertGreater(chinese_similarity("放弃；抛弃", "放弃"), 60.0)
        self.assertEqual(chinese_similarity("", "放弃"), 0.0)
        self.assertEqual(chinese_similarity("完全不相干", "汽车"), 0.0)

    def test_ocr_item(self):
        item = OCRItem("test", 0.95, 10, 20, 50, 60)
        self.assertEqual(item.cx, 30.0)
        self.assertEqual(item.cy, 40.0)
        self.assertEqual(item.width, 40.0)
        self.assertEqual(item.height, 40.0)

    def test_vision_find(self):
        items = [
            OCRItem("开始背诵", 0.9, 10, 10, 50, 30),
            OCRItem("我不会", 0.85, 10, 40, 50, 60),
        ]
        found = Vision.find(items, "开始")
        self.assertIsNotNone(found)
        self.assertEqual(found.text, "开始背诵")
        self.assertIsNone(Vision.find(items, "不存在"))


class TestApiSolver(unittest.TestCase):
    def test_extract_json_standard(self):
        raw = '{"answers":[{"index":1,"option":"A","text":"apple","reason":"r"}]}'
        data = _extract_json(raw)
        self.assertIn("answers", data)
        self.assertEqual(len(data["answers"]), 1)
        self.assertEqual(data["answers"][0]["option"], "A")

    def test_extract_json_markdown_fence(self):
        raw = '```json\n{"answers":[{"index":2,"option":"B","text":"banana","reason":"r"}]}\n```'
        data = _extract_json(raw)
        self.assertEqual(data["answers"][0]["option"], "B")

    def test_extract_json_reversed_index_option(self):
        raw = 'Some text before\n{"answers":[{"option":"C","index":3,"text":"cat","reason":"r"}]}'
        data = _extract_json(raw)
        parsed = VisionAPI._parse(data)
        self.assertEqual(parsed[0].index, 3)
        self.assertEqual(parsed[0].option, "C")

    def test_extract_json_top_level_array(self):
        raw = '[{"index":1,"option":"A","text":"apple"},{"index":2,"option":"B"}]'
        data = _extract_json(raw)
        parsed = VisionAPI._parse(data)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].option, "A")
        self.assertEqual(parsed[1].option, "B")

    def test_detect_task(self):
        api = VisionAPI({"api": {"enabled": True}})
        self.assertEqual(api.detect_task("这是一道英语段落匹配题"), "paragraph_match")
        self.assertEqual(api.detect_task("选词填空练习题"), "word_bank_fill")
        self.assertEqual(api.detect_task("仔细阅读 Section C"), "reading_choice")
        self.assertIsNone(api.detect_task("单词测试"))

    def test_api_key_from_env(self):
        os.environ["ONECLASS_API_KEY"] = "test_env_key_123"
        try:
            api = VisionAPI({"api": {"enabled": True, "api_key": ""}})
            self.assertEqual(api.api_key, "test_env_key_123")
            self.assertTrue(api.ready)
        finally:
            del os.environ["ONECLASS_API_KEY"]


class TestAudioSolver(unittest.TestCase):
    def test_audio_solver_api_key_env(self):
        os.environ["ONECLASS_API_KEY"] = "test_audio_key_456"
        try:
            solver = AudioSolver(
                {
                    "asr_backend": "api",
                    "api": {
                        "base_url": "https://api.example.com/v1",
                        "api_key": "",
                        "api_key_env": "ONECLASS_API_KEY",
                    },
                },
                Path("."),
            )
            with patch("requests.post") as mock_post:
                mock_post.return_value.status_code = 200
                mock_post.return_value.json.return_value = {"text": "apple"}

                tmp_wav = Path(tempfile.gettempdir()) / "test.wav"
                tmp_wav.write_bytes(b"dummy")
                try:
                    raw, heard = solver.transcribe(tmp_wav, ["apple"])
                    self.assertEqual(heard, "apple")
                    mock_post.assert_called_once()
                    headers = mock_post.call_args[1]["headers"]
                    self.assertEqual(headers["Authorization"], "Bearer test_audio_key_456")
                finally:
                    if tmp_wav.exists():
                        tmp_wav.unlink()
        finally:
            del os.environ["ONECLASS_API_KEY"]


class TestExerciseRegex(unittest.TestCase):
    def test_blank_matching(self):
        blanks = ["(1)", "( 2 )", "[3]", "【4】", "_5_"]
        for b in blanks:
            m = BLANK_RE.match(b)
            self.assertIsNotNone(m, f"Failed to match {b} with BLANK_RE")

    def test_blank_prefix_matching(self):
        prefixes = ["1. What is", "2、Why did", "3) How to", "(4) Which", "[5] Where"]
        for p in prefixes:
            m = BLANK_PREFIX_RE.match(p)
            self.assertIsNotNone(m, f"Failed to match {p} with BLANK_PREFIX_RE")

    def test_option_matching(self):
        options = ["A", "(A)", "[A]", "A.", "A、", "A: apple", "A apple", "(B) banana"]
        for o in options:
            m = OPTION_RE.match(o)
            self.assertIsNotNone(m, f"Failed to match {o} with OPTION_RE")
            self.assertIn(m.group(1).upper(), ["A", "B"])


class TestScreen(unittest.TestCase):
    def test_screen_coords(self):
        s = Screen([100, 200, 300, 400], after_click_delay=0.1)
        self.assertEqual(s.absolute(10, 20), (110, 220))
        self.assertEqual(s.relative(0.5, 0.5), (250, 400))

    def test_target_at_null_window(self):
        from bot.screen import Win32BackgroundInput
        w = Win32BackgroundInput()
        with patch.object(w.user32, "WindowFromPoint", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                w._target_at(9999, 9999)
            self.assertIn("没有可用窗口", str(ctx.exception))


class TestStateDetection(unittest.TestCase):
    def setUp(self):
        from bot.controller import WordBot
        cfg = {
            "region": [0, 0, 480, 800],
            "fallback_points": {
                "reveal_answer": [0.5, 0.5],
                "know": [0.5, 0.8],
                "next_word": [0.5, 0.95],
                "continue_words": [0.5, 0.95],
                "complete_confirm": [0.5, 0.6],
                "start_memorize": [0.5, 0.95],
                "timeout_next_group": [0.66, 0.56],
                "start_next_group": [0.5, 0.95],
                "speaker": [0.5, 0.3],
                "dictation_input": [0.5, 0.4],
                "dictation_confirm": [0.5, 0.5],
                "unknown": [0.5, 0.97],
                "test_question_finish": [0.5, 0.97],
                "test_result_finish": [0.74, 0.96],
            },
            "speed_preset": "balanced",
            "api": {"enabled": True},
        }
        with patch("bot.controller.Screen"):
            self.bot = WordBot(cfg, Path("."))

    def test_detect_state_choice(self):
        items = [OCRItem("请选择正确的释义", 0.9, 10, 100, 200, 130)]
        self.assertEqual(self.bot.detect_state(items), "test_choice")

        items = [
            OCRItem("请选择正确的释义", 0.9, 10, 100, 200, 130),
            OCRItem("下一词", 0.9, 10, 720, 200, 760),
        ]
        self.assertEqual(self.bot.detect_state(items), "choice_feedback")

        items = [
            OCRItem("请选择正确的释义", 0.9, 10, 100, 200, 130),
            OCRItem("完成", 0.9, 10, 720, 200, 760),
        ]
        self.assertEqual(self.bot.detect_state(items), "choice_feedback_final")

    def test_detect_state_dictation(self):
        items = [OCRItem("请填写你听到的单词", 0.9, 10, 100, 200, 130)]
        self.assertEqual(self.bot.detect_state(items), "test_dictation")

        items = [
            OCRItem("请填写你听到的单词", 0.9, 10, 100, 200, 130),
            OCRItem("下一词", 0.9, 10, 720, 200, 760),
        ]
        self.assertEqual(self.bot.detect_state(items), "dictation_feedback")

        items = [
            OCRItem("请填写你听到的单词", 0.9, 10, 100, 200, 130),
            OCRItem("完成", 0.9, 10, 720, 200, 760),
        ]
        self.assertEqual(self.bot.detect_state(items), "dictation_feedback_final")

    def test_detect_state_exercise(self):
        items = [OCRItem("英语选词填空练习", 0.9, 10, 50, 200, 80)]
        self.assertEqual(self.bot.detect_state(items), "exercise")

        items = [OCRItem("习题详情查看", 0.9, 10, 50, 200, 80)]
        self.assertEqual(self.bot.detect_state(items), "exercise_review")

    def test_detect_state_timeout(self):
        items = [
            OCRItem("任务超时警告", 0.9, 10, 100, 200, 130),
            OCRItem("再来一组", 0.9, 10, 300, 200, 340),
        ]
        self.assertEqual(self.bot.detect_state(items), "task_timeout")


class TestGuiLogic(unittest.TestCase):
    def test_providers_structure(self):
        from gui import PROVIDERS
        self.assertIn("阿里云百炼 (通义千问 / 推荐)", PROVIDERS)
        self.assertIn("硅基流动 (SiliconFlow)", PROVIDERS)
        self.assertIn("OpenAI 官方", PROVIDERS)

        for name, info in PROVIDERS.items():
            self.assertIn("base_url", info)
            self.assertIn("vision_models", info)
            self.assertIn("default_vision", info)

    def test_gui_provider_switch_and_fetch(self):
        import gui
        p_info = gui.PROVIDERS["阿里云百炼 (通义千问 / 推荐)"]
        self.assertTrue(p_info["base_url"].startswith("https://dashscope"))
        self.assertIn("qwen-vl-max-latest", p_info["vision_models"])

    def test_gui_fetch_models_worker(self):
        import gui
        with patch("gui.load_config", return_value={"region": [0,0,100,100]}):
            app = gui.App()
            app.root.withdraw()
            try:
                app.var_base.set("https://api.example.com/v1")
                app.var_key.set("test_key")

                with patch("requests.get") as mock_get:
                    mock_get.return_value.status_code = 200
                    mock_get.return_value.json.return_value = {
                        "data": [
                            {"id": "text-davinci"},
                            {"id": "qwen-vl-max"},
                            {"id": "whisper-1"},
                        ]
                    }

                    app.fetch_models()
                    if app.fetch_thread:
                        app.fetch_thread.join(timeout=2)
                    app._drain_ui()

                    self.assertIn("qwen-vl-max", app.vision_box["values"])
                    self.assertEqual(app.vision_box["values"][0], "qwen-vl-max")
                    self.assertIn("成功", app.lbl_fetch_status.cget("text"))
            finally:
                app.root.destroy()

    def test_gui_provider_switch(self):
        import gui
        with patch("gui.load_config", return_value={"region": [0,0,100,100]}):
            app = gui.App()
            app.root.withdraw()
            try:
                app.var_provider.set("硅基流动 (SiliconFlow)")
                app._on_provider_change()
                self.assertEqual(app.var_base.get(), "https://api.siliconflow.cn/v1")
                self.assertIn("Qwen/Qwen2.5-VL-72B-Instruct", app.vision_box["values"])

                app.var_provider.set("OpenAI 官方")
                app._on_provider_change()
                self.assertEqual(app.var_base.get(), "https://api.openai.com/v1")
                self.assertEqual(app.var_vmodel.get(), "gpt-4o-mini")
            finally:
                app.root.destroy()

    def test_gui_fetch_without_key_friendly_tip(self):
        import gui
        with patch("gui.load_config", return_value={"region": [0,0,100,100]}):
            app = gui.App()
            app.root.withdraw()
            try:
                app.var_base.set("https://api.openai.com/v1")
                app.var_key.set("")
                app.fetch_models()
                self.assertIn("若只使用本地 Whisper", app.lbl_fetch_status.cget("text"))
            finally:
                app.root.destroy()

    def test_gui_fetch_error_handling(self):
        import gui
        with patch("gui.load_config", return_value={"region": [0,0,100,100]}):
            app = gui.App()
            app.root.withdraw()
            try:
                app.var_base.set("https://api.example.com/v1")
                app.var_key.set("invalid_key")
                with patch("requests.get") as mock_get:
                    mock_get.return_value.status_code = 401
                    mock_get.return_value.json.return_value = {"error": {"message": "Invalid API Key"}}

                    app.fetch_models()
                    if app.fetch_thread:
                        app.fetch_thread.join(timeout=2)
                    app._drain_ui()

                    self.assertIn("失败", app.lbl_fetch_status.cget("text"))
                    self.assertIn("401", app.lbl_fetch_status.cget("text"))
            finally:
                app.root.destroy()


if __name__ == "__main__":
    unittest.main()
