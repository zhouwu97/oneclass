"""通过多模态视觉 API 解答 OneClass 的阅读类题型。

只发送截图，不依赖本地 OCR 的识别质量：把滚动分页的若干张截图
一起交给 OpenAI 兼容的 chat/completions 接口，要求返回结构化答案。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
from dataclasses import dataclass

import requests
from PIL import Image


LOG = logging.getLogger("wordbot")


class ApiSolveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Answer:
    index: int
    option: str
    text: str
    reason: str


PROMPTS = {
    "paragraph_match": (
        "图片是一道英语段落匹配（长篇阅读）题的连续截图，可能有多页且相邻截图有重叠。"
        "文章按段落标注 A) B) C) …，下面有若干道小题。"
        "请通读全部截图，为每一道小题选出唯一正确的段落字母。"
    ),
    "word_bank_fill": (
        "图片是一道英语选词填空题的连续截图，可能有多页且相邻截图有重叠。"
        "正文里有 (1) (2) … 编号的空白，词库中每个单词前标注 A. B. C. …。"
        "请通读全部截图，为每个空白选出唯一正确的词库字母，"
        "text 字段填写该字母对应的单词原文。"
    ),
    "reading_choice": (
        "图片是一道英语仔细阅读选择题的连续截图，可能有多页且相邻截图有重叠。"
        "每道题有 A/B/C/D 四个选项。"
        "请通读全部截图，为每道题选出正确选项字母，text 填写该选项的原文。"
    ),
}

RESPONSE_RULE = (
    "只输出一个 JSON 对象，不要输出解释、Markdown 代码块或多余文字。格式："
    '{"answers":[{"index":1,"option":"G","text":"选项原文","reason":"一句话依据"}]}。'
    "index 必须是题号或空白编号（从 1 开始的整数），option 只允许一个大写字母，"
    "并且必须覆盖截图中出现的每一道题。"
)


def _encode(image: Image.Image, max_width: int, quality: int) -> str:
    if image.width > max_width:
        height = max(1, round(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.LANCZOS)
    if image.mode != "RGB":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _extract_json(payload: str) -> dict:
    text = payload.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()

    # 1. 直接解析整段
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            return {"answers": data}
    except json.JSONDecodeError:
        pass

    # 2. 截取 {...} 片段
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 3. 截取 [...] 片段
    start_b, end_b = text.find("["), text.rfind("]")
    if start_b >= 0 and end_b > start_b:
        try:
            data = json.loads(text[start_b : end_b + 1])
            if isinstance(data, list):
                return {"answers": data}
        except json.JSONDecodeError:
            pass

    # 4. 正则兜底提取（支持 index/option 两种先后顺序）
    pairs = re.findall(
        r'"index"\s*:\s*(\d+)[^{}]*?"option"\s*:\s*"?([A-Za-z])"?',
        text,
        re.S,
    )
    if not pairs:
        pairs = [
            (i, o)
            for o, i in re.findall(
                r'"option"\s*:\s*"?([A-Za-z])"?[^{}]*?"index"\s*:\s*(\d+)',
                text,
                re.S,
            )
        ]
    if pairs:
        return {
            "answers": [
                {"index": int(i), "option": o.upper(), "text": "", "reason": ""}
                for i, o in pairs
            ]
        }
    raise ApiSolveError(f"接口返回内容无法解析为 JSON：{payload[:400]!r}")


class VisionAPI:
    def __init__(self, cfg: dict):
        api = cfg.get("api") or {}
        self.task_keywords: dict[str, str] = {
            str(k): str(v)
            for k, v in (api.get("task_keywords") or {}).items()
        } or {
            "paragraph_match": "段落匹配",
            "word_bank_fill": "选词填空",
            "reading_choice": "仔细阅读",
        }
        self.enabled = bool(api.get("enabled", True))
        self.base_url = str(
            api.get("base_url")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = str(api.get("model") or "qwen-vl-max-latest")
        env_name = str(api.get("api_key_env") or "ONECLASS_API_KEY")
        self.api_key = str(api.get("api_key") or os.environ.get(env_name) or "")
        self.api_key_env_name = env_name
        self.timeout = float(api.get("timeout", 90))
        self.max_tokens = int(api.get("max_tokens", 3000))
        self.temperature = float(api.get("temperature", 0.0))
        self.max_image_width = int(api.get("max_image_width", 900))
        self.jpeg_quality = int(api.get("jpeg_quality", 82))
        self.max_frames = int(api.get("max_frames", 8))
        self.retry = max(1, int(api.get("retry", 2)))
        self._session = requests.Session()

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.api_key)

    def detect_task(self, page_text: str) -> str | None:
        for task, keyword in self.task_keywords.items():
            if keyword and keyword in page_text:
                return task
        return None

    def solve(self, task: str, images: list[Image.Image]) -> list[Answer]:
        if task not in PROMPTS:
            raise ApiSolveError(f"未知的题型：{task}")
        if not self.enabled:
            raise ApiSolveError("config.yaml 中 api.enabled 为 false")
        if not self.api_key:
            raise ApiSolveError(
                f"缺少视觉 API 密钥：请在 config.yaml 的 api.api_key 填写，"
                f"或设置环境变量 {self.api_key_env_name}"
            )

        frames = self._dedupe(images)[: self.max_frames]
        if not frames:
            raise ApiSolveError("没有可用于识别的截图")

        content: list[dict] = [
            {"type": "text", "text": f"{PROMPTS[task]}\n{RESPONSE_RULE}"}
        ]
        for image in frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64,"
                        + _encode(image, self.max_image_width, self.jpeg_quality)
                    },
                }
            )
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        LOG.info(
            "调用视觉 API：task=%s model=%s frames=%d", task, self.model, len(frames)
        )

        last_error: Exception | None = None
        for attempt in range(1, self.retry + 1):
            try:
                answers = self._request(body)
                LOG.info(
                    "视觉 API 答案：%s",
                    ", ".join(f"{a.index}->{a.option}" for a in answers),
                )
                return answers
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                LOG.warning("视觉 API 第 %d/%d 次失败：%s", attempt, self.retry, exc)
        raise ApiSolveError(f"视觉 API 连续 {self.retry} 次失败：{last_error}")

    def _dedupe(self, images: list[Image.Image]) -> list[Image.Image]:
        seen: set[bytes] = set()
        frames: list[Image.Image] = []
        for image in images:
            digest = hashlib.md5(
                image.convert("L").resize((64, 64)).tobytes()
            ).digest()
            if digest in seen:
                continue
            seen.add(digest)
            frames.append(image)
        return frames

    def _request(self, body: dict) -> list[Answer]:
        response = self._session.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise ApiSolveError(
                f"HTTP {response.status_code}：{response.text[:300]}"
            )
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise ApiSolveError(f"接口返回异常：{json.dumps(data)[:300]}")
        message = choices[0].get("message") or {}
        payload = message.get("content")
        if isinstance(payload, list):
            payload = "".join(
                str(part.get("text", ""))
                for part in payload
                if isinstance(part, dict)
            )
        if not payload:
            raise ApiSolveError("接口返回内容为空")
        return self._parse(_extract_json(str(payload)))

    @staticmethod
    def _parse(data: dict) -> list[Answer]:
        raw = data.get("answers")
        if not isinstance(raw, list):
            raise ApiSolveError(f"接口返回缺少 answers 数组：{str(data)[:300]}")
        answers: list[Answer] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            option = re.sub(r"[^A-Za-z]", "", str(item.get("option", "")))
            if not option:
                continue
            try:
                index = int(str(item.get("index", "")).strip() or 0)
            except ValueError:
                continue
            answers.append(
                Answer(
                    index=index,
                    option=option[0].upper(),
                    text=str(item.get("text", "")).strip(),
                    reason=str(item.get("reason", "")).strip(),
                )
            )
        if not answers:
            raise ApiSolveError("接口没有返回任何有效答案")
        answers.sort(key=lambda a: a.index)
        return answers
