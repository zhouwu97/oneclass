"""OneClass 背单词的图形设置与运行界面（tkinter，无额外依赖）。

设计原则：
1. 本地模式为基础核心：不填写任何 API 即可完整离线背单词与听写（通过本地 OCR + 本地 Whisper）。
2. 云端 API 纯属可选扩展：用于语音 API 毫秒级极速听写或长篇阅读大题（段落匹配/选词填空）的视觉作答。
3. 答题速度三档控制（快 / 均衡 / 准），自由调节节奏与重听策略。
4. 提供多模型厂商一键切换、API 连通性探测、动态拉取模型列表与实时运行日志。
"""
from __future__ import annotations

# DPI awareness 必须在导入 PyAutoGUI/MSS 之前启用。
from bot.windows_dpi import enable_dpi_awareness

enable_dpi_awareness()

import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from bot.config import ROOT, load_config, save_config


LOCAL_MODELS = [
    "distil-large-v3",
    "small.en",
    "base.en",
    "tiny.en",
    "medium.en",
    "large-v3",
]

PROVIDERS: dict[str, dict[str, Any]] = {
    "阿里云百炼 (通义千问 / 推荐)": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "vision_models": [
            "qwen-vl-max-latest",
            "qwen-vl-plus-latest",
            "qwen2.5-vl-72b-instruct",
            "qwen2.5-vl-7b-instruct",
            "qwen-plus",
        ],
        "default_vision": "qwen-vl-max-latest",
        "default_asr": "whisper-1",
        "note": "国内首选，长篇阅读/段落匹配准确率最高，响应极快。",
    },
    "硅基流动 (SiliconFlow)": {
        "base_url": "https://api.siliconflow.cn/v1",
        "vision_models": [
            "Qwen/Qwen2.5-VL-72B-Instruct",
            "Qwen/Qwen2-VL-72B-Instruct",
            "Pro/Qwen/Qwen2.5-VL-7B-Instruct",
            "deepseek-ai/DeepSeek-V3",
        ],
        "default_vision": "Qwen/Qwen2.5-VL-72B-Instruct",
        "default_asr": "FunAudioLLM/SenseVoiceSmall",
        "note": "支持开源 Qwen-VL 全系列与 SenseVoice 语音，性价比高。",
    },
    "OpenAI 官方": {
        "base_url": "https://api.openai.com/v1",
        "vision_models": [
            "gpt-4o",
            "gpt-4o-mini",
        ],
        "default_vision": "gpt-4o-mini",
        "default_asr": "whisper-1",
        "note": "需海外网络，支持 Whisper 官方语音接口与 GPT-4o 视觉能力。",
    },
    "智谱 AI (GLM)": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "vision_models": [
            "glm-4v-plus",
            "glm-4v",
            "glm-4-plus",
        ],
        "default_vision": "glm-4v-plus",
        "default_asr": "whisper-1",
        "note": "国内大模型，支持 GLM-4V 多模态视觉理解。",
    },
    "月之暗面 (Kimi / Moonshot)": {
        "base_url": "https://api.moonshot.cn/v1",
        "vision_models": [
            "moonshot-v1-8k-vision-preview",
            "moonshot-v1-32k-vision-preview",
        ],
        "default_vision": "moonshot-v1-8k-vision-preview",
        "default_asr": "whisper-1",
        "note": "支持超长上下文与视觉输入能力。",
    },
    "DeepSeek (深度求索)": {
        "base_url": "https://api.deepseek.com/v1",
        "vision_models": [
            "deepseek-chat",
            "deepseek-reasoner",
        ],
        "default_vision": "deepseek-chat",
        "default_asr": "whisper-1",
        "note": "DeepSeek 官方暂未开放图片视觉上传，仅建议纯文本类使用。",
    },
    "Ollama (本地接口)": {
        "base_url": "http://localhost:11434/v1",
        "vision_models": [
            "llama3.2-vision",
            "minicpm-v",
            "llava",
        ],
        "default_vision": "llama3.2-vision",
        "default_asr": "whisper-1",
        "note": "本地运行的 OpenAI 兼容服务，完全免费、无需联网和 API Key。",
    },
    "自定义 (OpenAI 兼容中转)": {
        "base_url": "",
        "vision_models": [],
        "default_vision": "",
        "default_asr": "whisper-1",
        "note": "支持任意第三方 OpenAI 格式 API 中转服务。",
    },
}

SPEED_DESC = {
    "fast": "录音 2.0 秒、单候选解码、只尝试一次：每题最快，长单词偶尔被截断。",
    "balanced": "录音 2.4 秒、单候选解码、失败重听一次：速度与正确率的折中（默认推荐）。",
    "accurate": "录音 3.2 秒、5 候选解码、最多重听三次：最稳，但每题用时较长。",
}

LOG = logging.getLogger("wordbot")


class TkLogHandler(logging.Handler):
    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put(self.format(record))
        except Exception:  # noqa: BLE001
            self.handleError(record)


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.bot = None
        self.thread: threading.Thread | None = None
        self.fetch_thread: threading.Thread | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.ui_queue: queue.Queue = queue.Queue()

        api = self.cfg.get("api") or {}
        exercise = self.cfg.get("exercise") or {}
        env_key = os.environ.get(str(api.get("api_key_env") or "ONECLASS_API_KEY"), "")

        self.root = tk.Tk()
        self.root.title("OneClass 背单词 · 设置与运行")
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)

        # 变量初始化
        self.var_speed = tk.StringVar(value=str(self.cfg.get("speed_preset", "balanced")))
        self.var_asr = tk.StringVar(value=str(self.cfg.get("asr_backend", "local")))
        self.var_whisper = tk.StringVar(value=str(self.cfg.get("whisper_model", "distil-large-v3")))
        self.var_asr_model = tk.StringVar(value=str(self.cfg.get("asr_api_model", "whisper-1")))

        # 探测当前厂商
        base_url = str(api.get("base_url") or "").rstrip("/")
        saved_provider = str(api.get("provider") or "")
        detected_provider = "阿里云百炼 (通义千问 / 推荐)"
        if saved_provider in PROVIDERS:
            detected_provider = saved_provider
        elif base_url:
            for p_name, p_info in PROVIDERS.items():
                if p_info["base_url"].rstrip("/") == base_url:
                    detected_provider = p_name
                    break

        self.var_provider = tk.StringVar(value=detected_provider)
        self.var_api_enabled = tk.BooleanVar(value=bool(api.get("enabled", True)))
        self.var_base = tk.StringVar(value=base_url or PROVIDERS[detected_provider]["base_url"])
        self.var_vmodel = tk.StringVar(value=str(api.get("model") or "qwen-vl-max-latest"))
        self.var_key = tk.StringVar(value=str(api.get("api_key") or env_key))
        self.var_autosubmit = tk.BooleanVar(value=bool(exercise.get("auto_submit", False)))

        self._build()
        self._refresh_note()
        self.root.after(80, self._drain_ui)

    def _build(self) -> None:
        # 底部运行模式说明卡片
        self.note = tk.Label(
            self.root,
            text="",
            justify="left",
            anchor="w",
            wraplength=1140,
            background="#f1f8e9",
            foreground="#2e7d32",
            padx=14,
            pady=8,
            font=("Microsoft YaHei", 9),
        )
        self.note.pack(fill="x", side="bottom")

        body = ttk.Frame(self.root, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        # 左侧设置面板（带滚动条）
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        canvas = tk.Canvas(left, highlightthickness=0)
        scroll = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        self.panel = ttk.Frame(canvas)
        self.panel.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.panel, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.panel.columnconfigure(1, weight=1)

        # 设置模块：核心基础优先，API 选配在后
        self._section_speed()
        self._section_asr()
        self._section_api()
        self._section_exercise()

        # 右侧运行与日志面板
        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        buttons = ttk.Frame(right)
        buttons.grid(row=0, column=0, sticky="ew")
        ttk.Button(buttons, text="💾 保存配置", command=lambda: self.save()).pack(
            side="left", padx=(0, 6)
        )
        self.btn_start = ttk.Button(buttons, text="🚀 启动机器人", command=self.start)
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ttk.Button(
            buttons, text="⏹ 停止", command=self.stop, state="disabled"
        )
        self.btn_stop.pack(side="left")

        self.status = ttk.Label(right, text="未运行", foreground="#555555", font=("Microsoft YaHei", 9))
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 4))

        self.log_text = tk.Text(
            right, wrap="word", state="disabled", font=("Consolas", 9), background="#fafafa"
        )
        self.log_text.grid(row=2, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(right, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=2, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _row(self, parent: ttk.Frame, label: str, widget: tk.Widget) -> None:
        row_idx = parent.grid_size()[1]
        ttk.Label(parent, text=label).grid(
            column=0, row=row_idx, sticky="w", pady=3
        )
        widget.grid(column=1, row=row_idx, sticky="ew", pady=3, padx=(8, 0))

    def _section_speed(self) -> None:
        box = ttk.LabelFrame(self.panel, text="1. 答题速度（控制录音与重听策略）", padding=8)
        box.pack(fill="x", pady=(0, 8))
        for value, text in (
            ("fast", "快档（极速，录音 2.0s，单候选，适合熟词）"),
            ("balanced", "均衡档（默认推荐，录音 2.4s，重听 1 次，稳快兼顾）"),
            ("accurate", "准档（高置信度，录音 3.2s，重听 3 次，适合听音不准场景）"),
        ):
            ttk.Radiobutton(
                box, text=text, value=value, variable=self.var_speed,
                command=self._refresh_note,
            ).pack(anchor="w", pady=1)
        self.speed_desc = ttk.Label(box, text="", foreground="#666666")
        self.speed_desc.pack(anchor="w", pady=(4, 0))

    def _section_asr(self) -> None:
        box = ttk.LabelFrame(self.panel, text="2. 听写题引擎（核心功能 · 本地离线免费）", padding=8)
        box.pack(fill="x", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        ttk.Radiobutton(
            box,
            text="本地 Whisper（推荐 · 纯本地 CPU 推理 · 离线免费 · 无需任何 API）",
            value="local",
            variable=self.var_asr,
            command=self._refresh_note,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))

        ttk.Radiobutton(
            box,
            text="语音识别 API（云端 GPU 加速 · 识别极快 · 需在下方配置 API Key）",
            value="api",
            variable=self.var_asr,
            command=self._refresh_note,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.whisper_box = ttk.Combobox(
            box, values=LOCAL_MODELS, textvariable=self.var_whisper, width=20, state="readonly"
        )
        self._row(box, "本地 Whisper 模型", self.whisper_box)

        self.asr_model_box = ttk.Combobox(
            box,
            values=["whisper-1", "FunAudioLLM/SenseVoiceSmall", "paraformer-v2"],
            textvariable=self.var_asr_model,
            width=24,
        )
        self._row(box, "API 语音模型", self.asr_model_box)
        self._sync_asr_state()
        self.var_asr.trace_add("write", lambda *_: self._sync_asr_state())

        tip = ttk.Label(
            box,
            text="说明：本地 Whisper 完全离线运行，无需 API Key、不花钱；语音 API 经由服务端 GPU 识别更迅速。两者仅为答题速度差异，日常背单词不填 API 即可畅玩。",
            foreground="#666666",
            wraplength=480,
        )
        tip.grid(row=box.grid_size()[1], column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _sync_asr_state(self) -> None:
        local = self.var_asr.get() == "local"
        self.whisper_box.configure(state="readonly" if local else "disabled")
        self.asr_model_box.configure(state="normal" if not local else "disabled")

    def _section_api(self) -> None:
        box = ttk.LabelFrame(self.panel, text="3. AI 云端模型与 API 设置（可选 · 用于语音 API 加速与阅读大题）", padding=10)
        box.pack(fill="x", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        banner = ttk.Label(
            box,
            text="💡【自选配置】：不填 API 完全不影响背单词与听写正常使用；日常背单词建议直接点击右上角「启动机器人」。",
            foreground="#0066cc",
            wraplength=480,
        )
        banner.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # 厂商选择
        self.provider_box = ttk.Combobox(
            box,
            values=list(PROVIDERS.keys()),
            textvariable=self.var_provider,
            state="readonly",
            width=28,
        )
        self.provider_box.bind("<<ComboboxSelected>>", self._on_provider_change)
        self._row(box, "模型厂商", self.provider_box)

        # Base URL
        self.base_entry = ttk.Entry(box, textvariable=self.var_base)
        self._row(box, "Base URL", self.base_entry)

        # API Key
        key_frame = ttk.Frame(box)
        key_entry = ttk.Entry(key_frame, textvariable=self.var_key, show="*", width=30)
        key_entry.pack(side="left", fill="x", expand=True)

        def toggle_key() -> None:
            key_entry.configure(show="" if key_entry.cget("show") == "*" else "*")

        ttk.Button(key_frame, text="显示/隐藏", width=9, command=toggle_key).pack(
            side="left", padx=(6, 0)
        )
        self._row(box, "API Key", key_frame)

        # 拉取模型与连通性测试按钮行（换行放置状态，防止被水平挤断）
        test_frame = ttk.Frame(box)
        self.btn_fetch = ttk.Button(
            test_frame, text="🔍 测试连接并拉取模型列表", command=self.fetch_models
        )
        self.btn_fetch.pack(anchor="w")

        self.lbl_fetch_status = ttk.Label(
            test_frame,
            text="可选：填写 API Key 后可点击测试（仅使用本地模式背单词无需配置）",
            foreground="#777777",
            wraplength=460,
        )
        self.lbl_fetch_status.pack(anchor="w", pady=(3, 0))
        self._row(box, "接口探测", test_frame)

        # 视觉模型下拉框
        provider_info = PROVIDERS.get(self.var_provider.get(), PROVIDERS["阿里云百炼 (通义千问 / 推荐)"])
        preset_vmodels = provider_info.get("vision_models") or [self.var_vmodel.get()]
        self.vision_box = ttk.Combobox(
            box, values=preset_vmodels, textvariable=self.var_vmodel, width=28
        )
        self._row(box, "作答视觉模型", self.vision_box)

    def _on_provider_change(self, *_) -> None:
        p_name = self.var_provider.get()
        p_info = PROVIDERS.get(p_name)
        if not p_info:
            return
        if p_name != "自定义 (OpenAI 兼容中转)":
            self.var_base.set(p_info["base_url"])
            if p_info.get("vision_models"):
                self.vision_box["values"] = p_info["vision_models"]
                self.var_vmodel.set(p_info.get("default_vision", p_info["vision_models"][0]))
            if p_info.get("default_asr") and self.var_asr.get() == "api":
                self.var_asr_model.set(p_info["default_asr"])
        self.lbl_fetch_status.configure(
            text=f"已切换为 {p_name}，填写 API Key 后可点击上方按钮拉取最新可用模型",
            foreground="#555555",
        )
        self._refresh_note()

    def fetch_models(self) -> None:
        base_url = self.var_base.get().strip().rstrip("/")
        api_key = self.var_key.get().strip()

        if not base_url:
            self._set_fetch_status("请先填写 Base URL！", is_error=True)
            return

        # 若是需要鉴权的云端厂商但尚未填写 API Key，给出温和提示，不发起无效网络请求
        if not api_key and "localhost" not in base_url and "127.0.0.1" not in base_url:
            self._set_fetch_status(
                "💡 提示：该云端厂商需填写 API Key 才能拉取；若只使用本地 Whisper 背单词，此处无需拉取。",
                is_error=False,
                color="#8a6d00",
            )
            return

        self.btn_fetch.configure(state="disabled", text="正在拉取…")
        self._set_fetch_status("正在向服务商查询可用模型列表…", is_error=False, color="#0055aa")

        def worker() -> None:
            import requests

            url = f"{base_url}/models"
            headers = {"Accept": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            try:
                resp = requests.get(url, headers=headers, timeout=12)
                if resp.status_code != 200:
                    err_msg = f"HTTP {resp.status_code}"
                    try:
                        err_data = resp.json()
                        if isinstance(err_data, dict):
                            err_msg += "：" + str(
                                err_data.get("error", {}).get("message")
                                or err_data.get("message")
                                or resp.text[:120]
                            )
                    except Exception:
                        err_msg += f"：{resp.text[:120]}"
                    self.ui_queue.put(lambda: self._on_fetch_failed(err_msg))
                    return

                data = resp.json()
                raw_models = data.get("data") or data.get("models") or []
                model_ids: list[str] = []
                for item in raw_models:
                    if isinstance(item, dict) and "id" in item:
                        model_ids.append(str(item["id"]))
                    elif isinstance(item, str):
                        model_ids.append(item)

                if not model_ids:
                    self.ui_queue.put(lambda: self._on_fetch_failed("服务商返回了空的模型列表"))
                    return

                # 视觉模型特征排序：带 vl / vision / 4o / glm-4v 等排在前面
                def vision_rank(m: str) -> tuple[int, str]:
                    low = m.lower()
                    if any(k in low for k in ("vl", "vision", "4o", "glm-4v", "minicpm", "llava", "qwen2.5-vl")):
                        return (0, m)
                    return (1, m)

                model_ids = sorted(list(dict.fromkeys(model_ids)), key=vision_rank)
                self.ui_queue.put(lambda: self._on_fetch_success(model_ids))
            except Exception as exc:  # noqa: BLE001
                self.ui_queue.put(lambda: self._on_fetch_failed(str(exc)))

        self.fetch_thread = threading.Thread(target=worker, daemon=True)
        self.fetch_thread.start()

    def _on_fetch_success(self, models: list[str]) -> None:
        self.btn_fetch.configure(state="normal", text="🔍 测试连接并拉取模型列表")
        self.vision_box["values"] = models
        cur = self.var_vmodel.get().strip()
        if not cur or cur not in models:
            self.var_vmodel.set(models[0])
        # 若有包含 whisper / voice / audio 的模型，同步更新 ASR
        asr_candidates = [
            m for m in models
            if any(k in m.lower() for k in ("whisper", "sensevoice", "audio", "voice"))
        ]
        if asr_candidates and hasattr(self, "asr_model_box"):
            if isinstance(self.asr_model_box, ttk.Combobox):
                self.asr_model_box["values"] = asr_candidates

        self._set_fetch_status(f"✓ 连接成功！已拉取 {len(models)} 个可用模型", is_error=False, color="#008800")
        self.status.configure(text=f"已拉取 {len(models)} 个模型")

    def _on_fetch_failed(self, error: str) -> None:
        self.btn_fetch.configure(state="normal", text="🔍 测试连接并拉取模型列表")
        self._set_fetch_status(f"✗ 探测失败：{error}", is_error=True, color="#cc0000")

    def _set_fetch_status(self, text: str, is_error: bool = False, color: str = "") -> None:
        if not color:
            color = "#cc0000" if is_error else "#555555"
        self.lbl_fetch_status.configure(text=text, foreground=color)

    def _section_exercise(self) -> None:
        box = ttk.LabelFrame(self.panel, text="4. 阅读类大题设置（段落匹配 / 选词填空 / 仔细阅读）", padding=8)
        box.pack(fill="x", pady=(0, 8))

        tip = ttk.Label(
            box,
            text="说明：阅读类题型涉及长篇连续文章，需借助多模态视觉大模型作答。未配置 API 时，遇到此类大题将保持等待由人工确认，不影响日常单词和听力。",
            foreground="#666666",
            wraplength=480,
        )
        tip.pack(anchor="w", pady=(0, 4))

        ttk.Checkbutton(
            box,
            text="启用云端视觉大模型作答（需在上方填写 API Key；不启用则遇到大题时等待人工作答）",
            variable=self.var_api_enabled,
            command=self._refresh_note,
        ).pack(anchor="w", pady=(0, 2))

        ttk.Checkbutton(
            box,
            text="阅读大题填完后自动点击提交（默认关闭：填完所有空后停下，由人工检查无误后再提交）",
            variable=self.var_autosubmit,
        ).pack(anchor="w")

    def _refresh_note(self) -> None:
        speed = self.var_speed.get()
        speed_text = SPEED_DESC.get(speed, SPEED_DESC["balanced"])
        asr_mode = self.var_asr.get()
        env_name = str(self.cfg.get("api", {}).get("api_key_env") or "ONECLASS_API_KEY")
        has_key = bool(self.var_key.get().strip() or os.environ.get(env_name, "").strip())

        lines = []
        if asr_mode == "local" and not has_key:
            # 绝大多数用户的纯本地离线模式：绿意盎然，给予完全的安全感
            self.note.configure(background="#f1f8e9", foreground="#2e7d32")
            lines.append("🟢 当前运行模式：纯本地离线模式（无需任何 API Key，离线免费）")
            lines.append(f"✓ 听写题：本地 Whisper (当前模型: {self.var_whisper.get()}) 本地推理，不花钱、不联网、音频不出本机。")
            lines.append("✓ 单词题：本地 OCR 识别 + 词义库智能作答。")
            lines.append(f"✓ 答题速度：{speed_text}")
            lines.append("💡 说明：直接点击右上角「🚀 启动机器人」即可开始背单词！如需使用云端 GPU 极速听写或阅读大题自动答题，可在上方自选配置 API。")
        elif asr_mode == "api" and not has_key:
            # 选了 API 听写却没填 Key：温和提醒
            self.note.configure(background="#fff8e1", foreground="#8a6d00")
            lines.append("⚠️ 提示：听写引擎已选择「语音识别 API」，但尚未填写 API Key。")
            lines.append(f"请在下方「AI 云端模型与 API 设置」中填写 API Key（或设置环境变量 {env_name}）；")
            lines.append("或者直接将上方听写引擎切回「本地 Whisper」，即可完全离线免费使用。")
        else:
            # 启用了云端 API
            self.note.configure(background="#e3f2fd", foreground="#1565c0")
            p_name = self.var_provider.get()
            lines.append("🚀 当前运行模式：云端 API 加速模式")
            lines.append(f"✓ 模型厂商：{p_name}")
            if asr_mode == "api":
                lines.append(f"✓ 听写题：语音识别 API (模型: {self.var_asr_model.get()})，GPU 毫秒级返回。")
            else:
                lines.append(f"✓ 听写题：本地 Whisper (模型: {self.var_whisper.get()}) 离线推理。")
            if self.var_api_enabled.get():
                lines.append(f"✓ 阅读大题：已启用视觉大模型 ({self.var_vmodel.get()}) 自动作答。")
            else:
                lines.append("✓ 阅读大题：未启用，遇到大题将等待人工作答。")
            lines.append(f"✓ 答题速度：{speed_text}")

        self.note.configure(text="\n".join(lines))
        self.speed_desc.configure(text=speed_text)

    def save(self, silent: bool = False) -> None:
        self.cfg["speed_preset"] = self.var_speed.get()
        self.cfg["asr_backend"] = self.var_asr.get()
        self.cfg["whisper_model"] = self.var_whisper.get()
        self.cfg["asr_api_model"] = self.var_asr_model.get()

        api = dict(self.cfg.get("api") or {})
        api.update(
            {
                "enabled": bool(self.var_api_enabled.get()),
                "provider": self.var_provider.get(),
                "base_url": self.var_base.get().strip(),
                "model": self.var_vmodel.get().strip(),
                "api_key": self.var_key.get().strip(),
            }
        )
        self.cfg["api"] = api

        exercise = dict(self.cfg.get("exercise") or {})
        exercise["auto_submit"] = bool(self.var_autosubmit.get())
        self.cfg["exercise"] = exercise

        save_config(self.cfg)
        self._refresh_note()
        if not silent:
            self.status.configure(text="配置已成功保存到 config.yaml")

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            self.status.configure(text="机器人已在运行中")
            return
        self.save(silent=True)
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status.configure(text="启动中：加载 OCR / 语音组件…")
        self.thread = threading.Thread(target=self._run_bot, daemon=True)
        self.thread.start()

    def _run_bot(self) -> None:
        handler = TkLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)
        file_handler = logging.FileHandler(ROOT / "wordbot.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        root_logger.addHandler(file_handler)
        try:
            from bot.controller import WordBot

            self.bot = WordBot(self.cfg, Path(ROOT))
            self.bot.run()
        except SystemExit as exc:
            LOG.info("机器人已正常结束（code=%s）", exc.code)
        except Exception:  # noqa: BLE001
            LOG.exception("机器人异常退出")
        finally:
            root_logger.removeHandler(handler)
            root_logger.removeHandler(file_handler)
            try:
                file_handler.close()
            except Exception:
                pass
            self.root.after(0, self._on_bot_exit)

    def _on_bot_exit(self) -> None:
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status.configure(text="已停止")
        self.bot = None

    def stop(self) -> None:
        if self.bot:
            self.bot.stop()
            self.status.configure(text="正在停止…")

    def _drain_ui(self) -> None:
        while True:
            try:
                task = self.ui_queue.get_nowait()
                task()
            except queue.Empty:
                break
            except Exception:  # noqa: BLE001
                pass
        self._drain_logs()
        self.root.after(80, self._drain_ui)

    def _drain_logs(self) -> None:
        chunks = []
        while True:
            try:
                chunks.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if chunks:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(chunks) + "\n")
            self.log_text.see("end")
            if int(self.log_text.index("end-1c").split(".")[0]) > 1200:
                self.log_text.delete("1.0", "400.0")
            self.log_text.configure(state="disabled")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
