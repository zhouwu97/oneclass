from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from .audio_solver import AudioSolver
from .screen import Screen
from .vision import OCRItem, Vision, chinese_similarity, clean_word, norm_text
from .vocab import VocabStore


LOG = logging.getLogger("wordbot")


class WordBot:
    def __init__(self, cfg: dict, root: Path):
        self.cfg = cfg
        self.root = root
        self.screen = Screen(
            cfg["region"],
            cfg.get("after_click_delay", 0.85),
            cfg.get("input_backend", "win32"),
            cfg.get("win32_fallback_to_pyautogui", True),
        )
        self.vision = Vision(cfg.get("ocr_scale", 1.6), cfg.get("ocr_min_score", 0.45))
        self.vocab = VocabStore(root / "vocab.json")
        self.audio = AudioSolver(cfg, root / "logs")
        self.points = cfg.get("fallback_points", {})
        self.debug = bool(cfg.get("debug", True))
        self.unknown_count = 0
        self.last_state = ""
        self.extra_group_mode = False
        self.extra_groups_started = 0

    def point(self, name: str) -> tuple[float, float]:
        value = self.points.get(name)
        if not isinstance(value, list) or len(value) != 2:
            raise KeyError(f"config.yaml 缺少 fallback_points.{name}")
        return float(value[0]), float(value[1])

    def click_text(self, items: list[OCRItem], *texts: str, fallback: str | None = None) -> bool:
        item = self.vision.find(items, *texts)
        if item:
            LOG.info("点击文字 %s at (%.0f, %.0f)", item.text, item.cx, item.cy)
            self.screen.click_abs(item.cx, item.cy)
            return True
        if fallback:
            x, y = self.point(fallback)
            LOG.warning("OCR 未找到 %s，使用兜底坐标 %s", texts, fallback)
            self.screen.click_rel(x, y)
            return True
        return False

    def detect_state(self, items: list[OCRItem]) -> str:
        text = norm_text(self.vision.joined(items))
        height = float(self.screen.region.height)

        def bottom_exact(label: str, min_ratio: float = 0.84) -> bool:
            wanted = norm_text(label)
            return any(
                norm_text(item.text) == wanted
                and item.cy >= height * min_ratio
                for item in items
            )

        # 弹窗中和页面底部可能同时出现“再来一组”，优先识别弹窗。
        if (
            "再来一组" in text
            and (
                "任务超时" in text
                or ("词语未背诵" in text and "我再想想" in text)
            )
        ):
            return "task_timeout"

        if "学习完成" in text:
            return "learn_complete"
        if "继续背单词" in text:
            return "section_complete"

        # 第 60 题答完后，底部按钮不是“下一词”，而是“完成”。
        # 必须在普通 test_choice/test_dictation 之前判断，否则会重复答最后一题。
        if "请选择正确的释义" in text and bottom_exact("完成"):
            return "choice_feedback_final"
        if "请填写你听到的单词" in text and bottom_exact("完成"):
            return "dictation_feedback_final"

        # 普通题目反馈页。
        if "请选择正确的释义" in text and bottom_exact("下一词"):
            return "choice_feedback"
        if "请填写你听到的单词" in text and bottom_exact("下一词"):
            return "dictation_feedback"

        if "请选择正确的释义" in text or (
            "单词测试" in text
            and "我不会" in text
            and "确定" not in text
        ):
            return "test_choice"
        if "请填写你听到的单词" in text:
            return "test_dictation"

        # 测验总结页：底部有“重新测验”和“完成测验”。
        if "完成测验" in text or (
            "正确率" in text and "重新测验" in text
        ):
            return "test_result"

        # 兼容其他可能的完成提示。
        if any(
            value in text
            for value in ("测验完成", "测试完成", "本次成绩")
        ):
            return "test_complete"

        # 重复复习词：显示答案后点击“标记为太简单”。
        if "标记为太简单" in text:
            return "learn_too_easy"
        if "认识" in text and ("模糊" in text or "不认识" in text):
            return "learn_revealed"
        if "下一词" in text and ("释义" in text or "例句" in text):
            return "learn_detail"
        if "显示答案" in text or "请跟读并默念" in text:
            return "learn_hidden"
        if "开始背诵" in text:
            return "word_home"

        # 没有弹窗时，单词主页底部可能直接显示橙色“再来一组”。
        if (
            "再来一组" in text
            and any(
                value in text
                for value in ("截止日期", "词库预览", "已学习")
            )
        ):
            return "word_home_extra"
        if "今日任务" in text and "学习天数" in text:
            return "home"
        return "unknown"

    def learn_from_page(self, items: list[OCRItem], image: Image.Image) -> None:
        found = self.vision.main_word(items, image.height, (0.10, 0.64))
        if not found:
            return
        word, word_item = found
        definition = self.vision.definition(items, image.height, word_item)
        if self.vocab.add(word, definition):
            LOG.info("已记录词义：%s -> %s", word, definition)

    def handle_choice(self, items: list[OCRItem], image: Image.Image) -> None:
        target = self.vision.main_word(items, image.height, (0.24, 0.48))
        if not target:
            self._choice_unknown(items, "未识别到题目单词")
            return
        word, _ = target
        definition = self.vocab.definition(word)
        rows = self.vision.choice_rows(items, image.height)
        LOG.info("选择题：word=%s, definition=%s, rows=%s", word, definition, [r[0] for r in rows])
        if definition and rows:
            scored = [(chinese_similarity(definition, text), text, x, y) for text, x, y in rows]
            score, text, x, y = max(scored, key=lambda z: z[0])
            LOG.info("最佳选项：%s，匹配分 %.1f", text, score)
            if score >= float(self.cfg.get("choice_match_threshold", 42)):
                self.screen.click_abs(x, y)
                return
        self._choice_unknown(items, f"词义库缺失或匹配度不足：{word}")

    def _choice_unknown(self, items: list[OCRItem], reason: str) -> None:
        strategy = str(self.cfg.get("unknown_choice_strategy", "pause"))
        LOG.warning("%s；unknown_choice_strategy=%s", reason, strategy)
        if strategy == "first":
            self.screen.click_rel(0.50, 0.48)
        elif strategy == "unknown":
            self.click_text(items, "我不会", fallback="unknown")
        else:
            LOG.error("已暂停。补充 vocab.json 后按 Enter 继续，或 Ctrl+C 退出。")
            input()

    def type_word(self, word: str) -> None:
        x, y = self.point("dictation_input")
        self.screen.replace_text_rel(x, y, word, delay=0.15)

    def submit_dummy_dictation(
        self,
        items: list[OCRItem],
        reason: str = "",
    ) -> None:
        word = str(
            self.cfg.get("dictation_fallback_word", "zzzz")
        ).strip() or "zzzz"
        if reason:
            LOG.warning(
                "听写改用占位词：%s；reason=%s",
                word,
                reason,
            )
        else:
            LOG.info("听写占位模式：输入 %s", word)
        self.submit_word(items, word)

    def submit_word(self, items: list[OCRItem], word: str) -> None:
        """点击输入框、填写单词并点击确定。"""
        input_nx, input_ny = self.point("dictation_input")
        input_x = input_nx * self.screen.region.width
        input_y = input_ny * self.screen.region.height

        confirm = self.vision.find(items, "确定")
        if confirm:
            confirm_x, confirm_y = confirm.cx, confirm.cy
        else:
            confirm_nx, confirm_ny = self.point("dictation_confirm")
            confirm_x = confirm_nx * self.screen.region.width
            confirm_y = confirm_ny * self.screen.region.height
            LOG.warning("OCR 未找到确定按钮，使用 dictation_confirm 兜底坐标")

        LOG.info("前台辅助提交听写：点击输入框，填写 %s，再点击确定", word)
        self.screen.foreground_fill_and_click_abs(
            input_x,
            input_y,
            word,
            confirm_x,
            confirm_y,
        )

    def handle_dictation(self, items: list[OCRItem]) -> None:
        mode = str(self.cfg.get("dictation_mode", "whisper")).lower().strip()
        if mode == "reveal":
            self.click_text(items, "我不会", fallback="unknown")
            return
        if mode == "dummy":
            self.submit_dummy_dictation(items)
            return
        if mode != "whisper":
            raise ValueError("dictation_mode 只能是 reveal、dummy 或 whisper")

        # 默认共听两次：第一次识别置信度不足或录音异常时，
        # 再点击一次喇叭重新录音和识别。次数可在 config.yaml 中修改。
        max_attempts = max(1, int(self.cfg.get("dictation_max_attempts", 2)))
        retry_delay = max(0.0, float(self.cfg.get("dictation_retry_delay", 0.65)))
        threshold = float(self.cfg.get("speech_match_threshold", 52))
        speaker = self.point("speaker")
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                LOG.info(
                    "听写识别第 %d/%d 次：点击喇叭并重新录音",
                    attempt,
                    max_attempts,
                )
                audio_path = self.audio.record_after_click(
                    lambda: self.screen.foreground_click_rel(*speaker, delay=0.25)
                )
                raw, heard = self.audio.transcribe(audio_path, self.vocab.words())
                word, score = self.vocab.closest_word(heard)
                LOG.info(
                    "听写识别第 %d/%d 次：raw=%r, heard=%r, match=%r, score=%.1f",
                    attempt,
                    max_attempts,
                    raw,
                    heard,
                    word,
                    score,
                )

                if word and score >= threshold:
                    self.submit_word(items, word)
                    return

                last_error = RuntimeError(
                    f"语音识别置信度不足：{heard!r} -> {word!r} ({score:.1f})"
                )
                if attempt < max_attempts:
                    LOG.warning(
                        "%s；%.2f 秒后再次点击喇叭重听",
                        last_error,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < max_attempts:
                    LOG.exception(
                        "第 %d/%d 次听写失败：%s；%.2f 秒后再次点击喇叭重听",
                        attempt,
                        max_attempts,
                        exc,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
                LOG.exception(
                    "第 %d/%d 次听写仍失败：%s",
                    attempt,
                    max_attempts,
                    exc,
                )

        # 重听次数全部用完后才执行兜底；默认输入 zzzz，
        # 不会一识别失败就直接点击“我不会”。
        reason = str(last_error or "多次重听后仍未识别到可靠单词")
        strategy = str(
            self.cfg.get("dictation_failure_strategy", "dummy")
        ).lower().strip()
        if strategy == "dummy":
            self.submit_dummy_dictation(items, reason)
        elif strategy == "reveal":
            self.click_text(items, "我不会", fallback="unknown")
        elif strategy == "pause":
            LOG.error("多次重听仍失败，已暂停；按 Enter 重试当前题，或 Ctrl+C 退出。")
            input()
        else:
            raise ValueError(
                "dictation_failure_strategy 只能是 dummy、reveal 或 pause"
            )

    def start_extra_group(
        self,
        modal: bool,
        items: list[OCRItem] | None = None,
    ) -> None:
        if not bool(self.cfg.get("auto_next_group", True)):
            LOG.info("检测到‘再来一组’，但 auto_next_group=false，停止运行。")
            raise SystemExit(0)

        maximum = int(self.cfg.get("max_extra_groups", 0))
        if maximum > 0 and self.extra_groups_started >= maximum:
            LOG.info("已达到 max_extra_groups=%d，停止运行。", maximum)
            raise SystemExit(0)

        now = time.monotonic()
        retry_seconds = max(
            1.0,
            float(self.cfg.get("extra_group_retry_seconds", 3.0)),
        )
        pending = bool(
            getattr(self, "_extra_group_transition_pending", False)
        )
        last_click = float(
            getattr(self, "_extra_group_last_click_at", 0.0)
        )

        if pending and now - last_click < retry_seconds:
            LOG.info(
                "已前台点击‘再来一组’，等待页面切换（%.1f/%.1f 秒）。",
                now - last_click,
                retry_seconds,
            )
            return

        width = float(self.screen.region.width)
        height = float(self.screen.region.height)

        if modal:
            target_nx, target_ny = self.point("timeout_next_group")
            LOG.info(
                "弹窗‘再来一组’使用专用校准坐标："
                "normalized=(%.4f, %.4f)，absolute≈(%.0f, %.0f)。",
                target_nx,
                target_ny,
                target_nx * width,
                target_ny * height,
            )
        else:
            target_nx = None
            target_ny = None
            if items:
                candidates = [
                    item
                    for item in items
                    if "再来一组" in norm_text(item.text)
                    and item.cy >= height * 0.80
                ]
                if candidates:
                    button = max(
                        candidates,
                        key=lambda item: (
                            item.score,
                            item.width * item.height,
                        ),
                    )
                    target_nx = float(button.cx) / width
                    target_ny = float(button.cy) / height
                    LOG.info(
                        "定位到底部‘再来一组’ at (%.0f, %.0f)。",
                        button.cx,
                        button.cy,
                    )

            if target_nx is None or target_ny is None:
                target_nx, target_ny = self.point("start_next_group")
                LOG.warning(
                    "OCR 未定位到底部‘再来一组’，使用兜底坐标。"
                )

        self.screen.foreground_click_rel(
            target_nx,
            target_ny,
            delay=0.85,
        )
        self._extra_group_last_click_at = time.monotonic()
        self._extra_group_transition_pending = True
        self.extra_group_mode = True

        if pending:
            LOG.warning(
                "上次点击后弹窗仍存在，已按专用坐标重新点击绿色‘再来一组’。"
            )
        else:
            LOG.info(
                "已点击‘再来一组’，将在识别到新词页面后确认额外组开始。"
            )

    def act(self, state: str, items: list[OCRItem], image: Image.Image) -> None:
        if (
            bool(getattr(self, "_extra_group_transition_pending", False))
            and state
            in {
                "learn_hidden",
                "learn_too_easy",
                "learn_revealed",
                "learn_detail",
                "section_complete",
                "learn_complete",
                "test_choice",
                "test_dictation",
            }
        ):
            self._extra_group_transition_pending = False
            self.extra_groups_started += 1
            LOG.info(
                "已确认进入额外第 %d 组；继续识别今日新词和后续测试。",
                self.extra_groups_started,
            )

        if state == "home":
            # 首页中“单词”卡片文字通常可直接 OCR 到。
            if not self.click_text(items, "单词"):
                self.screen.click_rel(0.50, 0.78)
        elif state == "word_home":
            self.click_text(items, "开始背诵", fallback="start_memorize")
        elif state == "task_timeout":
            self.start_extra_group(modal=True, items=items)
        elif state == "word_home_extra":
            self.start_extra_group(modal=False, items=items)
        elif state == "learn_hidden":
            # 隐藏释义页先在空白区域点一次，显示答案和操作按钮。
            self.screen.click_rel(*self.point("reveal_answer"))
        elif state == "learn_too_easy":
            # “标记为太简单”会直接跳到下一个单词：
            # 不再点击“认识”，也不进入详情页点击“下一词”。
            self.learn_from_page(items, image)
            if bool(self.cfg.get("mark_too_easy", True)):
                if self.click_text(items, "标记为太简单"):
                    return
            # 用户关闭该功能或 OCR 瞬时未找到按钮时，按普通流程处理。
            self.click_text(items, "认识", fallback="know")
        elif state == "learn_revealed":
            self.learn_from_page(items, image)
            self.click_text(items, "认识", fallback="know")
        elif state == "learn_detail":
            self.learn_from_page(items, image)
            self.click_text(items, "下一词", fallback="next_word")
        elif state == "section_complete":
            self.click_text(items, "继续背单词", fallback="continue_words")
        elif state == "learn_complete":
            self.click_text(items, "确认", fallback="complete_confirm")
        elif state == "choice_feedback_final":
            LOG.info("第 60 题已显示答案，点击底部‘完成’进入测验总结。")
            self.click_text(
                items,
                "完成",
                fallback="test_question_finish",
            )
        elif state == "dictation_feedback_final":
            LOG.info("第 60 题听写已显示答案，点击底部‘完成’进入测验总结。")
            self.click_text(
                items,
                "完成",
                fallback="test_question_finish",
            )
        elif state == "choice_feedback":
            # 正确/错误反馈页无需再次答题，直接进入下一题。
            self.click_text(items, "下一词", fallback="next_word")
        elif state == "test_choice":
            self.handle_choice(items, image)
        elif state == "test_dictation":
            self.handle_dictation(items)
        elif state == "dictation_feedback":
            # “我不会”或提交后会出现正确答案，顺便补录进词库。
            found = self.vision.main_word(items, image.height, (0.48, 0.78))
            if found:
                word, word_item = found
                definition = self.vision.definition(items, image.height, word_item)
                self.vocab.add(word, definition)
            self.click_text(items, "下一词", fallback="next_word")
        elif state == "test_result":
            LOG.info("检测到测验总结页，点击橙色‘完成测验’。")
            self.click_text(
                items,
                "完成测验",
                fallback="test_result_finish",
            )
        elif state == "test_complete":
            if not self.click_text(
                items,
                "完成测验",
                "确认",
                "完成",
                "返回首页",
                fallback="test_result_finish",
            ):
                LOG.info("检测到测试完成，但没有找到结束按钮，停止运行。")
                raise SystemExit(0)
        else:
            self.handle_unknown(image, items)

    def handle_unknown(self, image: Image.Image, items: list[OCRItem]) -> None:
        self.unknown_count += 1
        if self.unknown_count % 8 == 0:
            LOG.warning("连续未识别页面，OCR=%s", self.vision.joined(items))
            if self.debug:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = self.screen.save_debug(image, self.root / "logs", f"unknown_{stamp}.png")
                LOG.warning("已保存未知页面：%s", path)
        time.sleep(0.5)

    def run(self) -> None:
        LOG.info("机器人启动。win32 模式不会移动鼠标；按 Ctrl+C 可退出。PyAutoGUI 模式仍可用左上角紧急停止。")
        while True:
            image = self.screen.capture()
            items = self.vision.read(image)
            state = self.detect_state(items)
            if state != self.last_state:
                LOG.info("页面状态：%s | OCR=%s", state, self.vision.joined(items))
                self.last_state = state
            if state != "unknown":
                self.unknown_count = 0
            self.act(state, items, image)
            time.sleep(float(self.cfg.get("loop_interval", 0.65)))
