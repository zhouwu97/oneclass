from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from .api_solver import VisionAPI
from .audio_solver import AudioSolver
from .runtime_profile import resolve_runtime_profile
from .config import speed_settings
from .exercise import ExerciseRunner
import threading
from .screen import Screen
from .vision import OCRItem, Vision, chinese_similarity, clean_word, norm_text
from .vision import ENGLISH_RE, CHINESE_RE
from .vocab import VocabStore


LOG = logging.getLogger("wordbot")


class WordBot:
    def __init__(self, cfg: dict, root: Path):
        self.cfg = cfg
        self.root = root
        self.profile = resolve_runtime_profile(cfg)
        self.speed = speed_settings(cfg)
        self.stop_requested = False
        self._stop_event = threading.Event()
        self.screen = Screen(
            cfg["region"],
            0.0,
            cfg.get("input_backend", "win32"),
            cfg.get("win32_fallback_to_pyautogui", True),
            cfg.get("allow_foreground_mouse", False),
        )
        self.vision = Vision(self.profile.ocr_scale, self.profile.ocr_min_score)
        self.screen.bound_window = cfg.get("_window_handle")
        self.vocab = VocabStore(root / "vocab.json")
        self.audio = AudioSolver(cfg, root / "logs")
        self.audio.preload_model_async()
        self.api = VisionAPI(cfg)
        self.exercises = ExerciseRunner(cfg, self.screen, self.vision, self.api)
        self.exercise_cfg = cfg.get("exercise") or {}
        self.exercise_cooldown = float(self.exercise_cfg.get("cooldown_seconds", 180))
        self.exercise_until = 0.0
        self.exercise_name = ""
        self.points = cfg.get("fallback_points", {})
        self.debug = bool(cfg.get("debug", True))
        self.unknown_count = 0
        self.last_state = ""
        self.extra_group_mode = False
        self.extra_groups_started = 0
        self._perf_counter = 0
        LOG.info(
            "运行档位：%s | loop=%.2fs click=%.2fs ocr_scale=%.2f",
            self.profile.name,
            self.profile.loop_interval,
            self.profile.after_click_delay,
            self.profile.ocr_scale,
        )

    def _use_foreground(self, key: str, default: bool) -> bool:
        return bool(self.cfg.get(key, default))

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

    def exercise_state(self, text: str) -> str | None:
        """识别阅读类题型页面；已提交的习题详情页只等待，不做操作。"""
        if any(word in text for word in ("习题详情", "学生得分", "答题结果")):
            return "exercise_review"
        if self.api.detect_task(text):
            return "exercise"
        return None

    def handle_exercise(self, items: list[OCRItem]) -> None:
        text = norm_text(self.vision.joined(items))
        task = self.api.detect_task(text)
        if not task:
            return
        now = time.monotonic()
        if self.exercise_name == task and now < self.exercise_until:
            time.sleep(1.0)
            return
        if not self.api.ready:
            LOG.info(
                "检测到阅读大题（%s），因未配置云端视觉 API，保持等待，请人工完成作答或在设置中按需配置 API Key。",
                task,
            )
            self.exercise_name = task
            self.exercise_until = now + 30.0
            return
        try:
            done = self.exercises.run(task)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("视觉 API 解题失败：%s", exc)
            self.exercise_name = task
            self.exercise_until = time.monotonic() + 30.0
            return
        self.exercise_name = task
        self.exercise_until = time.monotonic() + (
            self.exercise_cooldown if done else 30.0
        )

    def detect_state(self, items: list[OCRItem]) -> str:
        text = norm_text(self.vision.joined(items))
        height = float(self.screen.region.height)

        if any(label in text for label in ("资源玩命加载", "加载中", "正在加载")):
            return "loading"

        def bottom_exact(label: str, min_ratio: float = 0.84) -> bool:
            wanted = norm_text(label)
            for item in items:
                if item.cy < height * min_ratio:
                    continue
                t = norm_text(item.text)
                if t == wanted or (wanted in t and len(t) <= len(wanted) + 2):
                    return True
            return False

        # 弹窗中和页面底部可能同时出现“再来一组”，优先识别弹窗。
        if (
            "再来一组" in text
            and (
                "任务超时" in text
                or ("词语未背诵" in text and "我再想想" in text)
            )
        ):
            return "task_timeout"

        exercise = self.exercise_state(text)
        if exercise:
            return exercise

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
            found = self.vision.recover_word(image, (0.18, 0.42))
        if not found:
            LOG.warning("学习页单词未识别，未写入词库；请检查大字单词是否清晰。")
            return
        word, word_item = found
        definition = self.vision.definition(items, image.height, word_item)
        if self.vocab.add(word, definition):
            LOG.info("已记录词义：%s -> %s", word, definition)

    def handle_choice(self, items: list[OCRItem], image: Image.Image) -> None:
        english_options = [i for i in items if 0.40 * image.height <= i.cy <= 0.82 * image.height and ENGLISH_RE.fullmatch(i.text.strip())]
        if len(english_options) >= 3:
            prompt = "".join(i.text for i in items if 0.22 * image.height <= i.cy < 0.40 * image.height and CHINESE_RE.search(i.text) and "请选择" not in i.text)
            scored = sorted(((chinese_similarity(prompt, self.vocab.definition(clean_word(i.text))), i) for i in english_options), key=lambda x: x[0], reverse=True)
            score, option = scored[0]
            if prompt and score >= float(self.cfg.get("choice_match_threshold", 42)) and score - scored[1][0] >= 8:
                LOG.info("中文选英文：%s -> %s，匹配分 %.1f", prompt, option.text, score)
                self.screen.click_abs(option.cx, option.cy)
                return
            self._choice_unknown(items, "中文选英文的词义匹配不足或选项存在歧义")
            return
        target = self.vision.main_word(items, image.height, (0.24, 0.48))
        if not target:
            target = self.vision.recover_word(image, (0.24, 0.40))
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
            LOG.error("词义匹配不足，已停止并保留当前题；检查词库后重新启动。")
            self.stop()

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
            use_foreground=self._use_foreground("dictation_foreground_click", True),
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

        max_attempts = int(self.speed["max_attempts"])
        retry_delay = max(0.0, float(self.speed["retry_delay"]))
        replace_threshold = float(
            self.cfg.get("dictation_vocab_replace_threshold", 92)
        )
        direct_threshold = float(
            self.cfg.get("dictation_direct_similarity_threshold", 86)
        )
        allow_raw_consensus = bool(
            self.cfg.get("dictation_allow_raw_consensus", True)
        )
        speaker = self.point("speaker")
        last_error: Exception | None = None
        heard_history: list[str] = []
        question_started = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            if self.stop_requested:
                return
            try:
                LOG.info(
                    "听写识别第 %d/%d 次：点击喇叭并重新录音",
                    attempt,
                    max_attempts,
                )
                audio_path = self.audio.record_after_click(
                    lambda: self.screen.foreground_click_rel(
                        *speaker,
                        delay=0.05,
                        use_foreground=self._use_foreground(
                            "speaker_foreground_click",
                            False,
                        ),
                    )
                )
                if self.stop_requested:
                    return
                raw, heard = self.audio.transcribe(audio_path, self.vocab.words())
                if self.stop_requested:
                    return
                heard_clean = clean_word(heard)
                matched_word, weighted_score = self.vocab.closest_word(heard_clean)
                direct_score = 0.0
                if heard_clean and matched_word:
                    from rapidfuzz import fuzz

                    direct_score = float(fuzz.ratio(heard_clean, matched_word))

                exact_in_vocab = bool(
                    heard_clean and self.vocab.definition(heard_clean)
                )
                LOG.info(
                    "听写识别第 %d/%d 次：raw=%r, heard=%r, "
                    "vocab_match=%r, weighted=%.1f, direct=%.1f, exact=%s",
                    attempt,
                    max_attempts,
                    raw,
                    heard_clean,
                    matched_word,
                    weighted_score,
                    direct_score,
                    exact_in_vocab,
                )

                if exact_in_vocab:
                    LOG.info("听写耗时：录音与识别 %.2fs", time.perf_counter() - question_started)
                    self.submit_word(items, heard_clean)
                    return

                if heard_clean and self.audio.last_transcription_confident:
                    LOG.info("听写采用已校验的原词，跳过再次播放：%s（%.2fs）", heard_clean, time.perf_counter() - question_started)
                    self.submit_word(items, heard_clean)
                    return

                if (
                    matched_word
                    and weighted_score >= replace_threshold
                    and direct_score >= direct_threshold
                    and abs(len(heard_clean) - len(matched_word))
                    <= max(2, round(max(len(heard_clean), len(matched_word)) * 0.22))
                ):
                    LOG.info(
                        "ASR 采用高可信词库纠正：%s -> %s",
                        heard_clean,
                        matched_word,
                    )
                    self.submit_word(items, matched_word)
                    return

                if heard_clean:
                    if allow_raw_consensus and heard_clean in heard_history:
                        LOG.warning(
                            "ASR 连续两次一致，但词库没有该词：%s；"
                            "提交原始识别结果，不使用低可信匹配 %s(%.1f)。",
                            heard_clean,
                            matched_word,
                            weighted_score,
                        )
                        self.submit_word(items, heard_clean)
                        return
                    heard_history.append(heard_clean)

                last_error = RuntimeError(
                    "语音结果不适合词库替换："
                    f"{heard_clean!r} -> {matched_word!r} "
                    f"(weighted={weighted_score:.1f}, direct={direct_score:.1f})"
                )
                if attempt < max_attempts:
                    LOG.warning(
                        "%s；%.2f 秒后再次点击喇叭确认",
                        last_error,
                        retry_delay,
                    )
                    if self._stop_event.wait(retry_delay):
                        return
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
                    if self._stop_event.wait(retry_delay):
                        return
                else:
                    LOG.exception("第 %d/%d 次听写仍失败：%s", attempt, max_attempts, exc)

        if self.stop_requested:
            return
        reason = str(last_error or "多次重听后仍未识别到可靠单词")
        strategy = str(self.cfg.get("dictation_failure_strategy", "dummy")).lower().strip()
        if strategy == "dummy":
            self.submit_dummy_dictation(items, reason)
        elif strategy == "reveal":
            self.click_text(items, "我不会", fallback="unknown")
        elif strategy == "pause":
            LOG.error("多次重听仍失败，已停止并保留当前题；检查声音设置后重新启动。")
            self.stop()
        else:
            raise ValueError("dictation_failure_strategy 只能是 dummy、reveal 或 pause")

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
            use_foreground=self._use_foreground(
                "extra_group_foreground_click",
                False,
            ),
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

    def act(self, state: str, items: list[OCRItem], image: Image.Image) -> float:
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

        if state == "loading":
            return 0.35
        elif state == "home":
            # 首页中“单词”卡片文字通常可直接 OCR 到。
            if not self.click_text(items, "单词"):
                self.screen.click_rel(0.50, 0.78)
            return self.profile.page_transition_delay
        elif state == "word_home":
            self.click_text(items, "开始背诵", fallback="start_memorize")
            return self.profile.page_transition_delay
        elif state == "task_timeout":
            self.start_extra_group(modal=True, items=items)
            return self.profile.page_transition_delay
        elif state == "word_home_extra":
            self.start_extra_group(modal=False, items=items)
            return self.profile.page_transition_delay
        elif state == "learn_hidden":
            # 隐藏释义页先在空白区域点一次，显示答案和操作按钮。
            self.screen.click_rel(*self.point("reveal_answer"))
            return self.profile.after_click_delay
        elif state == "learn_too_easy":
            # “标记为太简单”会直接跳到下一个单词：
            # 不再点击“认识”，也不进入详情页点击“下一词”。
            self.learn_from_page(items, image)
            if bool(self.cfg.get("mark_too_easy", True)):
                if self.click_text(items, "标记为太简单"):
                    return self.profile.after_click_delay
            # 用户关闭该功能或 OCR 瞬时未找到按钮时，按普通流程处理。
            self.click_text(items, "认识", fallback="know")
            return self.profile.after_click_delay
        elif state == "learn_revealed":
            self.learn_from_page(items, image)
            self.click_text(items, "认识", fallback="know")
            return self.profile.after_click_delay
        elif state == "learn_detail":
            self.learn_from_page(items, image)
            self.click_text(items, "下一词", fallback="next_word")
            return self.profile.after_click_delay
        elif state == "section_complete":
            self.click_text(items, "继续背单词", fallback="continue_words")
            return self.profile.page_transition_delay
        elif state == "learn_complete":
            self.click_text(items, "确认", fallback="complete_confirm")
            return self.profile.page_transition_delay
        elif state == "choice_feedback_final":
            LOG.info("第 60 题已显示答案，点击底部‘完成’进入测验总结。")
            self.click_text(
                items,
                "完成",
                fallback="test_question_finish",
            )
            return self.profile.page_transition_delay
        elif state == "dictation_feedback_final":
            LOG.info("第 60 题听写已显示答案，点击底部‘完成’进入测验总结。")
            self.click_text(
                items,
                "完成",
                fallback="test_question_finish",
            )
            return self.profile.page_transition_delay
        elif state == "choice_feedback":
            # 正确/错误反馈页无需再次答题，直接进入下一题。
            self.click_text(items, "下一词", fallback="next_word")
            return self.profile.after_click_delay
        elif state == "test_choice":
            self.handle_choice(items, image)
            return self.profile.after_click_delay
        elif state == "test_dictation":
            self.handle_dictation(items)
            return self.profile.dictation_delay
        elif state == "dictation_feedback":
            # “我不会”或提交后会出现正确答案，顺便补录进词库。
            found = self.vision.main_word(items, image.height, (0.48, 0.78))
            if found:
                word, word_item = found
                definition = self.vision.definition(items, image.height, word_item)
                self.vocab.add(word, definition)
            self.click_text(items, "下一词", fallback="next_word")
            return self.profile.after_click_delay
        elif state == "test_result":
            result_text = self.vision.joined(items)
            score = re.search(r"(?<![\d.])(100(?:\.0+)?|\d{1,2}(?:\.\d+)?)\s*[%％]", result_text)
            if score:
                LOG.info("本次测验正确率：%s%%（本次总结页，非历史平均正确率）", score.group(1))
            LOG.info("检测到测验总结页，点击橙色‘完成测验’。")
            self.click_text(
                items,
                "完成测验",
                fallback="test_result_finish",
            )
            return self.profile.page_transition_delay
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
            return self.profile.page_transition_delay
        elif state == "exercise":
            self.handle_exercise(items)
            return 1.0
        elif state == "exercise_review":
            return 1.0
        else:
            return self.handle_unknown(image, items)

    def handle_unknown(self, image: Image.Image, items: list[OCRItem]) -> float:
        self.unknown_count += 1
        if self.unknown_count % 8 == 0:
            LOG.warning("连续未识别页面，OCR=%s", self.vision.joined(items))
            if self.debug:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                path = self.screen.save_debug(image, self.root / "logs", f"unknown_{stamp}.png")
                LOG.warning("已保存未知页面：%s", path)
        return self.profile.unknown_delay

    def run(self) -> None:
        LOG.info("机器人启动。win32 模式不会移动鼠标；按 Ctrl+C 可退出。PyAutoGUI 模式仍可用左上角紧急停止。")
        while not self.stop_requested:
            started = time.perf_counter()
            image = self.screen.capture()
            captured = time.perf_counter()
            items = self.vision.read(image)
            ocr_done = time.perf_counter()
            state = self.detect_state(items)
            state_done = time.perf_counter()
            if state != self.last_state:
                LOG.info("页面状态：%s | OCR=%s", state, self.vision.joined(items))
                self.last_state = state
            if state != "unknown":
                self.unknown_count = 0
            if self.stop_requested:
                break
            next_delay = self.act(state, items, image)
            action_done = time.perf_counter()
            if self.debug:
                self._perf_counter += 1
                LOG.debug(
                    "性能：capture_ms=%.1f ocr_ms=%.1f state_ms=%.1f action_ms=%.1f next_delay=%.2f",
                    (captured - started) * 1000,
                    (ocr_done - captured) * 1000,
                    (state_done - ocr_done) * 1000,
                    (action_done - state_done) * 1000,
                    max(0.0, next_delay),
                )
            self._stop_event.wait(max(0.0, next_delay))

    def stop(self) -> None:
        LOG.info("收到停止请求，等待当前录音或识别结束。")
        self.stop_requested = True
        self._stop_event.set()
