from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

import cv2
import numpy as np
from PIL import Image
from rapidocr import RapidOCR


@dataclass(frozen=True)
class OCRItem:
    text: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


ENGLISH_RE = re.compile(r"^[A-Za-z][A-Za-z'-]{1,30}$")
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def norm_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", "", text).lower()


def clean_word(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").strip().lower()
    text = re.sub(r"[^a-z'-]", "", text)
    return text.strip("'-")


class Vision:
    def __init__(self, scale: float = 1.6, min_score: float = 0.45):
        self.scale = float(scale)
        self.min_score = float(min_score)
        self.engine = RapidOCR()

    def read(self, image: Image.Image) -> list[OCRItem]:
        rgb = np.asarray(image)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if self.scale != 1.0:
            bgr = cv2.resize(
                bgr, None, fx=self.scale, fy=self.scale,
                interpolation=cv2.INTER_CUBIC,
            )
        result = self.engine(bgr)
        if not getattr(result, "boxes", None) is not None:
            return []
        boxes = result.boxes
        txts = result.txts or ()
        scores = result.scores or ()
        items: list[OCRItem] = []
        for box, text, score in zip(boxes, txts, scores):
            if float(score) < self.min_score or not str(text).strip():
                continue
            pts = np.asarray(box, dtype=float) / self.scale
            x1, y1 = pts[:, 0].min(), pts[:, 1].min()
            x2, y2 = pts[:, 0].max(), pts[:, 1].max()
            items.append(OCRItem(str(text).strip(), float(score), x1, y1, x2, y2))
        return sorted(items, key=lambda i: (i.cy, i.cx))

    @staticmethod
    def joined(items: Iterable[OCRItem]) -> str:
        return "|".join(i.text for i in items)

    @staticmethod
    def find(items: Iterable[OCRItem], *needles: str) -> OCRItem | None:
        wanted = [norm_text(x) for x in needles]
        candidates: list[OCRItem] = []
        for item in items:
            t = norm_text(item.text)
            if any(n in t for n in wanted):
                candidates.append(item)
        if not candidates:
            return None
        return max(candidates, key=lambda x: (x.score, x.width * x.height))

    @staticmethod
    def contains(items: Iterable[OCRItem], needle: str) -> bool:
        n = norm_text(needle)
        return any(n in norm_text(i.text) for i in items)

    @staticmethod
    def main_word(
        items: Iterable[OCRItem],
        image_height: int,
        y_range: tuple[float, float] = (0.10, 0.72),
    ) -> tuple[str, OCRItem] | None:
        stop = {
            "min", "adj", "adv", "noun", "verb", "the", "a", "an",
            "home", "next", "word", "learn", "test", "today",
        }
        candidates: list[tuple[str, OCRItem]] = []
        for item in items:
            if not (y_range[0] * image_height <= item.cy <= y_range[1] * image_height):
                continue
            word = clean_word(item.text)
            if not ENGLISH_RE.fullmatch(word) or word in stop:
                continue
            candidates.append((word, item))
        if not candidates:
            return None
        return max(candidates, key=lambda pair: (pair[1].height, len(pair[0]), pair[1].score))

    @staticmethod
    def definition(items: list[OCRItem], image_height: int, word_item: OCRItem | None) -> str:
        label = next((i for i in items if norm_text(i.text) == "释义"), None)
        example = next((i for i in items if "例句" in norm_text(i.text)), None)
        if label:
            low = label.y2
            high = example.y1 if example else image_height * 0.56
        else:
            low = (word_item.y2 + 12) if word_item else image_height * 0.32
            high = image_height * 0.64

        blacklist = (
            "请跟读", "默念", "点击屏幕", "认识", "模糊", "不认识", "简单",
            "我要纠错", "下一词", "英:", "美:", "背单词", "今日复习",
            "今日新词", "学习时间", "单词测试", "请选择", "请填写",
        )
        parts: list[str] = []
        for item in items:
            if not (low <= item.cy <= high):
                continue
            t = item.text.strip()
            nt = norm_text(t)
            if any(norm_text(x) in nt for x in blacklist):
                continue
            if CHINESE_RE.search(t):
                parts.append(t)
        return "".join(parts)

    @staticmethod
    def choice_rows(items: list[OCRItem], height: int) -> list[tuple[str, float, float]]:
        # 选择题四个灰色长条大致位于 40%~80% 高度，按行合并 OCR 结果。
        candidates = [
            i for i in items
            if 0.40 * height <= i.cy <= 0.80 * height
            and CHINESE_RE.search(i.text)
            and "请选择" not in norm_text(i.text)
            and "我不会" not in norm_text(i.text)
            and "我要纠错" not in norm_text(i.text)
        ]
        rows: list[list[OCRItem]] = []
        for item in sorted(candidates, key=lambda x: x.cy):
            if not rows or abs(item.cy - sum(x.cy for x in rows[-1]) / len(rows[-1])) > 18:
                rows.append([item])
            else:
                rows[-1].append(item)
        result: list[tuple[str, float, float]] = []
        for row in rows:
            text = "".join(x.text for x in sorted(row, key=lambda x: x.cx))
            cx = sum(x.cx for x in row) / len(row)
            cy = sum(x.cy for x in row) / len(row)
            if text:
                result.append((text, cx, cy))
        return result[:4]


def chinese_similarity(a: str, b: str) -> float:
    a = re.sub(r"[^\u4e00-\u9fff]", "", a or "")
    b = re.sub(r"[^\u4e00-\u9fff]", "", b or "")
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio() * 100
    sa, sb = set(a), set(b)
    jac = len(sa & sb) / max(1, len(sa | sb)) * 100
    containment = len(sa & sb) / max(1, min(len(sa), len(sb))) * 100
    return 0.45 * seq + 0.35 * jac + 0.20 * containment
