"""阅读类题型（段落匹配 / 选词填空 / 仔细阅读）的自动作答流程。

流程：滚动截屏 -> 视觉 API 出答案 -> 逐空点击空白、在弹出选项面板中点选
-> 视 auto_submit 决定是否点击提交。默认不自动提交，交由人工确认。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from PIL import Image

from .api_solver import Answer, VisionAPI
from .screen import Screen
from .vision import OCRItem, Vision


LOG = logging.getLogger("wordbot")

BLANK_RE = re.compile(r"^[\s_.\[【(]*\s*(\d{1,2})\s*[\)\]】_.]*$")
BLANK_PREFIX_RE = re.compile(r"^\s*[(\[【]?\s*(\d{1,2})\s*[)\]】.、]")
OPTION_RE = re.compile(r"^\s*[(（\[【]?\s*([A-Za-z])\s*(?:[)）\]】.:、\s]|$)(.*)$")
SUBMIT_WORDS = ("提交答案", "确认提交", "提交")


class ExerciseRunner:
    def __init__(self, cfg: dict, screen: Screen, vision: Vision, api: VisionAPI):
        self.screen = screen
        self.vision = vision
        self.api = api
        exercise = cfg.get("exercise") or {}
        self.scroll_pages = max(1, int(exercise.get("scroll_pages", 6)))
        self.anchor = tuple(
            float(v) for v in exercise.get("scroll_anchor", [0.5, 0.30])
        )
        self.blank_y_range = tuple(
            float(v) for v in exercise.get("blank_y_range", [0.08, 0.88])
        )
        self.auto_submit = bool(exercise.get("auto_submit", False))
        self.sheet_min_y = float(exercise.get("sheet_min_y_ratio", 0.45))
        self.sheet_delay = float(exercise.get("sheet_delay", 0.8))
        self.confirm_after_pick = bool(exercise.get("confirm_after_pick", True))
        self.debug = bool(cfg.get("debug", True))

    def read(self) -> tuple[Image.Image, list[OCRItem]]:
        image = self.screen.capture()
        return image, self.vision.read(image)

    def collect_frames(self) -> list[Image.Image]:
        """从当前页顶开始向下翻页，收集覆盖全文的截图。"""
        frames: list[Image.Image] = []
        image, _ = self.read()
        frames.append(image)
        for _ in range(self.scroll_pages):
            self.screen.foreground_scroll_pages(1, anchor=self.anchor)
            image, _ = self.read()
            frames.append(image)
        self.screen.foreground_scroll_pages(
            -len(frames), anchor=self.anchor, delay=0.30
        )
        return frames

    def run(self, task: str) -> bool:
        frames = self.collect_frames()
        answers = self.api.solve(task, frames)
        if not answers:
            LOG.warning("视觉 API 未返回答案，跳过本页。")
            return False
        if not self._apply(answers):
            return False
        if self.auto_submit:
            self._submit()
        else:
            LOG.info("已填好答案，exercise.auto_submit=false，等待人工检查后提交。")
        return True

    def _apply(self, answers: list[Answer]) -> bool:
        pending = {answer.index: answer for answer in answers if answer.index > 0}
        if not pending:
            LOG.warning("答案缺少有效题号：%s", answers)
            return False
        LOG.info("待填题号：%s", sorted(pending))
        for _page in range(self.scroll_pages + 2):
            _, items = self.read()
            visible = self._visible_blanks(items)
            hit = [index for index in sorted(visible) if index in pending]
            if not hit:
                if not pending:
                    return True
                self.screen.foreground_scroll_pages(1, anchor=self.anchor)
                continue
            for index in hit:
                answer = pending[index]
                if not self._fill_one(index, answer, visible[index]):
                    LOG.error("第 %s 题填写失败：option=%s", index, answer.option)
                    return False
                pending.pop(index)
            if not pending:
                return True
        if pending:
            LOG.error(
                "翻页 %d 次后仍未找到这些空白：%s；"
                "可能需要调大 exercise.scroll_pages 或调整 blank_y_range。",
                self.scroll_pages + 2,
                sorted(pending),
            )
            self._save_debug("exercise_unfinished")
        return not pending

    def _visible_blanks(
        self, items: list[OCRItem]
    ) -> dict[int, tuple[float, float]]:
        """返回当前屏幕可见的题号/空白及其可点击位置。"""
        height = float(self.screen.region.height)
        width = float(self.screen.region.width)
        low, high = self.blank_y_range
        found: dict[int, tuple[float, float]] = {}
        for item in items:
            if not (low * height <= item.cy <= high * height):
                continue
            text = item.text.strip()
            blank = BLANK_RE.match(text)
            if blank:
                found.setdefault(int(blank.group(1)), (item.cx, item.cy))
                continue
            match = BLANK_PREFIX_RE.match(text)
            if match:
                # “1. 题干…”这类整行识别：点最左侧的编号位置。
                found.setdefault(
                    int(match.group(1)), (item.x1 + width * 0.015, item.cy)
                )
        return {
            index: point
            for index, point in found.items()
            if 0 < index <= 40
        }

    def _fill_one(
        self, index: int, answer: Answer, point: tuple[float, float]
    ) -> bool:
        LOG.info(
            "填写第 %d 题 -> %s (%s) at (%.0f, %.0f)",
            index,
            answer.option,
            answer.text,
            point[0],
            point[1],
        )
        option = self._open_sheet(point, answer.option)
        if option is None:
            LOG.error(
                "选项面板中未找到 %s；请检查截图 logs/exercise_option_missing_*.png",
                answer.option,
            )
            self._save_debug("exercise_option_missing")
            return False
        self.screen.click_abs(option.cx, option.cy, delay=self.sheet_delay)
        if self.confirm_after_pick:
            self._confirm_sheet()
        return True

    def _open_sheet(
        self, point: tuple[float, float], letter: str
    ) -> OCRItem | None:
        """点击空白弹出选项面板；后台点击无效时改用一次前台真实点击。"""
        self.screen.click_abs(point[0], point[1], delay=self.sheet_delay)
        _, items = self.read()
        option = self._find_option(items, letter)
        if option:
            return option
        LOG.info("后台点击未弹出选项面板，改用前台真实点击。")
        self.screen.foreground_click_rel(
            point[0] / self.screen.region.width,
            point[1] / self.screen.region.height,
            delay=self.sheet_delay,
        )
        _, items = self.read()
        return self._find_option(items, letter)

    @staticmethod
    def _option_matches(items: list[OCRItem], letter: str) -> list[OCRItem]:
        wanted = letter.upper()
        matches: list[OCRItem] = []
        for item in items:
            match = OPTION_RE.match(item.text.strip())
            if match and match.group(1).upper() == wanted:
                matches.append(item)
        return matches

    def _option_items(self, items: list[OCRItem]) -> list[OCRItem]:
        """面板内的候选项：字母加词干，且位于弹出层所在的屏幕下部。"""
        low = self.sheet_min_y * float(self.screen.region.height)
        return [
            item
            for item in items
            if item.cy >= low and OPTION_RE.match(item.text.strip())
        ]

    def _find_option(self, items: list[OCRItem], letter: str) -> OCRItem | None:
        """在弹出的选项面板中定位目标字母，优先取面板区域内的结果。"""
        matches = self._option_matches(items, letter)
        if not matches:
            return None
        low = self.sheet_min_y * float(self.screen.region.height)
        in_sheet = [item for item in matches if item.cy >= low]
        return max(in_sheet or matches, key=lambda item: item.width * item.height)

    def _confirm_sheet(self) -> None:
        _, items = self.read()
        if len(self._option_items(items)) < 2:
            return
        button = self.vision.find(items, "确定", "确认")
        if button:
            LOG.info("选项面板仍在，点击 %s", button.text)
            self.screen.click_abs(button.cx, button.cy, delay=self.sheet_delay)

    def _submit(self) -> None:
        for _ in range(self.scroll_pages + 2):
            _, items = self.read()
            button = next(
                (
                    item
                    for item in items
                    if any(word in item.text for word in SUBMIT_WORDS)
                    and "提交前" not in item.text
                ),
                None,
            )
            if button:
                LOG.info("点击提交按钮：%s", button.text)
                self.screen.click_abs(button.cx, button.cy)
                return
            self.screen.foreground_scroll_pages(1, anchor=self.anchor)
        LOG.error("未找到提交按钮，请人工提交。")
        self._save_debug("exercise_submit_missing")

    def _save_debug(self, name: str) -> None:
        if not self.debug:
            return
        try:
            folder = Path(__file__).resolve().parents[1] / "logs"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            image = self.screen.capture()
            path = self.screen.save_debug(image, folder, f"{name}_{stamp}.png")
            LOG.warning("已保存现场：%s", path)
        except Exception:  # noqa: BLE001
            LOG.exception("保存调试截图失败")
