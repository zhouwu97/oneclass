"""OneClass 背单词的图形设置与运行界面（tkinter，无额外依赖）。

设计原则：
1. 本地模式为基础核心：不填写任何 API 即可完整离线背单词与听写（通过本地 OCR + 本地 Whisper）。
2. 云端 API 为可选扩展：用于兼容语音识别接口或阅读大题视觉作答。
3. 答题速度三档控制（快 / 均衡 / 准），自由调节节奏与重听策略。
4. 提供多模型厂商一键切换、API 连通性探测、动态拉取模型列表与实时运行日志。
"""
from __future__ import annotations

# DPI awareness 必须在导入 PyAutoGUI/MSS 之前启用。
from bot.windows_dpi import enable_dpi_awareness

enable_dpi_awareness()

import logging
import copy
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any

from bot.config import ROOT, load_config, save_config
from bot.logging_utils import file_log_handler


LOCAL_MODELS = [
    "small.en",
    "base.en",
    "tiny.en",
    "distil-large-v3",
    "medium.en",
    "large-v3",
]

MODEL_GUIDE = {
    "tiny.en": ("39M · 英语专用 · 资源占用最低", "启动和 CPU 推理通常最快，适合先检查声音链路、低配置电脑。", "生词、相近发音和噪声下更容易出错；速度优先时试用。"),
    "base.en": ("74M · 英语专用 · 轻量", "比 tiny 更能兼顾识别与速度，适合希望缩短 CPU 每题耗时的用户。", "复杂发音的识别能力通常弱于 small；建议先用几道题比较。"),
    "small.en": ("244M · 英语专用 · 日常听写推荐", "清晰英语单词的准确性与 CPU 速度较均衡，适合作为默认起点。", "通常比 base 慢、比 medium 轻；当前目标是提速，可先试 base.en。"),
    "distil-large-v3": ("约 756M · 英语蒸馏模型 · 更适合 GPU", "保留较强英语识别能力，相对完整 large-v3 减少推理计算。", "仍明显大于 small/base；没有可用 GPU 时未必更快，不作为 CPU 提速首选。"),
    "medium.en": ("769M · 英语专用 · 准确性优先", "适合 small 对口音、生词识别不够可靠时尝试。", "内存与计算需求更高，CPU 上每题通常更慢；建议搭配 GPU。"),
    "large-v3": ("约 1.55B · 多语言 · 资源需求最高", "适合多语言或较复杂音频，提供更强的通用识别能力。", "下载、加载、内存与推理成本较高；单词听写提速通常不优先选它。"),
}


def model_guidance(model: str, backend: str = "local") -> str:
    if backend == "api":
        return (f"{model} · 云端语音识别\n优点：减少本机计算，无需加载本地模型。\n取舍：需要兼容语音接口，速度、费用和准确率取决于服务商与网络；音频会发往配置的服务端。")
    guide = MODEL_GUIDE.get(model)
    if guide is None:
        return f"{model} · 自定义本地模型\n请确认模型路径、语言和硬件要求；实际速度以识别耗时日志为准。"
    title, benefit, tradeoff = guide
    return f"{title}\n优点：{benefit}\n取舍：{tradeoff}"

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
        "note": "提供视觉模型；请确认所选模型支持图片输入，语音接口需单独确认。",
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
    "fast": "录音上限 2.0 秒，发音结束即停；单候选解码，最多尝试 1 次。",
    "balanced": "录音上限 2.4 秒，发音结束即停；清晰单词只解码一次，失败可重听 1 次。",
    "accurate": "录音上限 3.2 秒，发音结束即停；5 候选解码，最多尝试 3 次。",
}

LOG = logging.getLogger("wordbot")

QUICKSTART = """第一次使用

1. 安装依赖
在项目目录运行 pip install -r requirements.txt
听写还需 pip install -r requirements-audio.txt
本地模型首次使用可能需要下载；下载完成后可离线识别。

2. 校准小程序
在界面最上方点击「扫描窗口」，选择 OneClass，再点击「预览并确认定位」。确认预览显示完整小程序后，点击「确认，使用这个窗口」，启动按钮才会开放。
移动窗口时会自动跟随位置。改变窗口大小、关闭或最小化会停止运行，需要重新定位。小程序仍须完整可见，不能被其他窗口遮挡。
听写点击不准时，可用 python calibrate_dictation_points.py 单独校准喇叭、输入框和确定按钮。命令行模式仍可用 python calibrate.py。

模型怎么选
tiny.en：最轻，适合低配置和声音测试，准确性取舍最大。
base.en：CPU 提速优先，适合清晰英语发音。
small.en：日常英语听写的均衡起点，本机已附带该模型。
distil-large-v3：相对完整 large 更省计算，更适合 GPU；CPU 上未必更快。
medium.en：准确性优先，内存和计算需求更高。
large-v3：多语言和复杂音频，资源需求最高，单词听写提速不优先选。
选择模型后会显示优点和取舍，具体速度以本机日志为准。

3. 设置听力
答题方式选 whisper，选择本地模型或兼容语音 API。
点击「刷新设备」，选择微信实际播放声音的扬声器或耳机。此项只选择监听设备，不会切换 Windows 的声音输出。
点击「试录 3 秒」后，手动播放小程序声音；再点「试听最近录音」。建议佩戴耳机，避免其他声音干扰。

4. 开始学习
保存配置，保持小程序可见，点击「启动机器人」。
切换速度会应用录音时长、候选数和重听次数；旧的高级覆盖值会清除。运行中的设置在下次启动生效。

停止与排查
点击「停止」后等待当前录音、模型或网络请求结束；停止后不会继续提交听写答案。
没有录到声音：检查微信音量、Windows 应用输出设备与当前监听设备是否一致，再刷新设备并试录。
听得到但识别不准：选「准档」，保留双次解码校验，确认单词发音未被截断。
播放按钮没反应：勾选「前台点击喇叭」并重新校准。
reveal 会点击「我不会」，会被记为错题；dummy 会提交占位词。请按需要选择。

日志与阅读题
警告为黄色，错误为红色；取消自动滚动可查看历史日志。
wordbot.log 最多保留当前文件和 3 份轮转备份，每份约 5 MB。
阅读题需要支持图片输入的兼容 API；模型列表能拉取不代表支持语音或图片。阅读自动提交默认关闭。
音频 API 的耗时取决于网络与服务商；并非所有厂商支持 /audio/transcriptions。
"""


class TkLogHandler(logging.Handler):
    def __init__(self, sink: queue.Queue):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.sink.put_nowait((record.levelname, self.format(record)))
        except queue.Full:
            pass
        except Exception:  # noqa: BLE001
            self.handleError(record)


class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.bot = None
        self.thread: threading.Thread | None = None
        self.fetch_thread: threading.Thread | None = None
        self.log_queue: queue.Queue = queue.Queue(maxsize=2000)
        self.ui_queue: queue.Queue = queue.Queue()
        self.stop_pending = threading.Event()
        self.audio_thread = None
        self.device_choices = {"跟随系统默认输出": ""}
        self.located_window = None
        self.window_choices = {}

        api = self.cfg.get("api") or {}
        exercise = self.cfg.get("exercise") or {}
        env_key = os.environ.get(str(api.get("api_key_env") or "ONECLASS_API_KEY"), "")

        self.root = tk.Tk()
        self.root.title("OneClass 背单词 · 设置与运行")
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.configure(background="#f3f5f9")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background="#f3f5f9")
        style.configure("TLabel", background="#f3f5f9")
        style.configure("TLabelframe", background="#f3f5f9")
        style.configure("TLabelframe.Label", background="#f3f5f9", foreground="#23456b")
        style.configure("TButton", padding=(10, 6))
        style.configure("TRadiobutton", wraplength=480)
        style.configure("TCheckbutton", wraplength=480)
        style.configure("TRadiobutton", background="#f3f5f9")
        style.configure("TCheckbutton", background="#f3f5f9")
        style.configure("TCombobox", fieldbackground="white")
        style.configure("Accent.TButton", foreground="white", background="#2463bd")
        self.var_device = tk.StringVar(value=str(self.cfg.get("audio_loopback_name", "")) or "跟随系统默认输出")
        self.var_mode = tk.StringVar(value=str(self.cfg.get("dictation_mode", "whisper")))
        self.var_speaker_click = tk.BooleanVar(value=bool(self.cfg.get("speaker_foreground_click", False)))
        self.var_verify = tk.BooleanVar(value=bool(self.cfg.get("whisper_verify_pass", True)))
        self.var_follow_log = tk.BooleanVar(value=True)
        self.var_window = tk.StringVar(value="请先扫描 OneClass 小程序窗口")

        # 变量初始化
        self.var_speed = tk.StringVar(value=str(self.cfg.get("speed_preset", "balanced")))
        self.var_asr = tk.StringVar(value=str(self.cfg.get("asr_backend", "local")))
        self.var_whisper = tk.StringVar(value=str(self.cfg.get("whisper_model", "small.en")))
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
        for variable in (self.var_whisper, self.var_asr_model, self.var_asr):
            variable.trace_add("write", lambda *_: self._refresh_model_guide())
        self._refresh_model_guide()
        self._refresh_note()
        self.root.after(80, self._drain_ui)

    def _build(self) -> None:
        header = ttk.Frame(self.root, padding=(20, 14))
        header.pack(fill="x")
        ttk.Label(header, text="OneClass · 学习助手", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(header, text="定位窗口 → 预览确认 → 配置听力 → 开始学习", foreground="#607089").pack(anchor="w", pady=(4, 0))
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
        canvas = tk.Canvas(left, highlightthickness=0, background="#f3f5f9")
        scroll = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        self.panel = ttk.Frame(canvas)
        self.panel.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        panel_window = canvas.create_window((0, 0), window=self.panel, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(panel_window, width=e.width))
        def wheel(event):
            if event.widget.winfo_toplevel() == self.root and str(event.widget).startswith(str(left)):
                canvas.yview_scroll(int(-event.delta / 120), "units")
        self.root.bind("<MouseWheel>", wheel, add="+")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.panel.columnconfigure(1, weight=1)

        # 设置模块：核心基础优先，API 选配在后
        self._section_window()
        self._section_speed()
        self._section_asr()
        self._section_audio()
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
        self.btn_start = ttk.Button(buttons, text="启动机器人", command=self.start, style="Accent.TButton", state="disabled")
        self.btn_start.pack(side="left", padx=(0, 6))
        self.btn_stop = ttk.Button(
            buttons, text="⏹ 停止", command=self.stop, state="disabled"
        )
        self.btn_stop.pack(side="left")

        self.status = ttk.Label(right, text="未运行", foreground="#555555", font=("Microsoft YaHei", 9))
        self.status.grid(row=1, column=0, sticky="w", pady=(6, 4))

        notebook = ttk.Notebook(right)
        notebook.grid(row=2, column=0, sticky="nsew")
        logs = ttk.Frame(notebook, padding=8)
        help_tab = ttk.Frame(notebook, padding=8)
        notebook.add(logs, text="运行日志")
        notebook.add(help_tab, text="使用教程")
        logs.rowconfigure(1, weight=1)
        logs.columnconfigure(0, weight=1)
        tools = ttk.Frame(logs)
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Checkbutton(tools, text="自动滚动", variable=self.var_follow_log).pack(side="left")
        ttk.Button(tools, text="清空", command=self._clear_logs).pack(side="right")
        ttk.Button(tools, text="打开日志目录", command=lambda: os.startfile(str(ROOT))).pack(side="right")
        self.log_text = tk.Text(logs, width=1, wrap="word", state="disabled", font=("Consolas", 10), background="#ffffff", relief="flat", padx=10, pady=10)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        for level, color in (("INFO", "#334155"), ("WARNING", "#a56708"), ("ERROR", "#c52f3b"), ("CRITICAL", "#c52f3b")):
            self.log_text.tag_configure(level, foreground=color)
        log_scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=1, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        from tkinter.scrolledtext import ScrolledText
        help_text = ScrolledText(help_tab, width=1, wrap="word", font=("Microsoft YaHei UI", 10), relief="flat", padx=12, pady=12)
        help_text.pack(fill="both", expand=True)
        help_text.insert("1.0", QUICKSTART)
        help_text.configure(state="disabled")
        # Wheel events over a readonly combo should scroll the settings panel,
        # rather than silently switching a model/device before the root bind.
        def bind_combo_wheel(parent):
            for child in parent.winfo_children():
                if isinstance(child, ttk.Combobox):
                    def scroll_combo(event):
                        canvas.yview_scroll(int(-event.delta / 120), "units")
                        return "break"
                    child.bind("<MouseWheel>", scroll_combo)
                bind_combo_wheel(child)
        bind_combo_wheel(self.panel)

    def _section_window(self) -> None:
        box = ttk.LabelFrame(self.panel, text="第一步 · 定位小程序窗口（启动前必须确认）", padding=10)
        box.pack(fill="x", pady=(0, 8))
        box.columnconfigure(1, weight=1)
        self.window_box = ttk.Combobox(box, textvariable=self.var_window, state="readonly", width=32)
        self._row(box, "目标窗口", self.window_box)
        self.window_box.bind("<<ComboboxSelected>>", lambda _: self._invalidate_location())
        buttons = ttk.Frame(box)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Button(buttons, text="1. 扫描窗口", command=self.refresh_windows).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="2. 预览并确认定位", command=self.preview_window).pack(side="left")
        self.location_status = ttk.Label(box, text="请打开电脑版微信里的 OneClass，保持小程序完整可见，再扫描窗口。旧截图坐标不会直接用于启动。", foreground="#a56708", wraplength=480)
        self.location_status.grid(row=2, column=0, columnspan=2, sticky="w")
        self.root.after(200, self.refresh_windows)

    def _invalidate_location(self) -> None:
        self.located_window = None
        self.btn_start.configure(state="disabled")
        self.location_status.configure(text="窗口选择已改变，请预览并确认定位。", foreground="#a56708")

    def refresh_windows(self) -> None:
        if self.thread and self.thread.is_alive():
            self.location_status.configure(text="请先停止机器人，再重新定位窗口。")
            return
        from bot.window_target import discover_windows
        targets = discover_windows()
        self._invalidate_location()
        self.window_choices = {f"{t.title} · {t.region[2]}×{t.region[3]} · 窗口 {t.handle}": t for t in targets}
        self.window_box.configure(values=list(self.window_choices))
        if len(targets) == 1:
            self.var_window.set(next(iter(self.window_choices)))
            self.location_status.configure(text="找到 OneClass。点击「预览并确认定位」，确认画面完整后才能启动。")
        else:
            self.var_window.set("请选择窗口" if targets else "未找到可用的 OneClass 窗口")
            self.location_status.configure(text="请选择对应窗口并预览。" if targets else "未找到窗口：请打开 OneClass 小程序并取消最小化，再扫描。")

    def preview_window(self) -> None:
        if self.thread and self.thread.is_alive():
            self.location_status.configure(text="请先停止机器人，再重新定位窗口。")
            return
        target = self.window_choices.get(self.var_window.get())
        if target is None:
            self.location_status.configure(text="请先扫描并选择 OneClass 窗口。")
            return
        self._invalidate_location()
        from bot.window_target import client_region
        from bot.screen import Screen
        from PIL import ImageTk
        try:
            region = client_region(target.handle)
            screen = Screen(region)
            try:
                preview = screen.capture()
            finally:
                screen.close()
        except Exception as exc:
            self.location_status.configure(text=f"无法预览：{exc}")
            return
        popup = tk.Toplevel(self.root)
        popup.title("确认 OneClass 定位范围")
        popup.transient(self.root)
        popup.geometry(f"+{self.root.winfo_rootx() + 40}+{max(0, self.root.winfo_rooty() + 20)}")
        popup.grab_set()
        frame = ttk.Frame(popup, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="确认下方画面完整、没有被其他窗口遮挡。", wraplength=360).pack(pady=(0, 6))
        preview.thumbnail((360, 650))
        picture = ImageTk.PhotoImage(preview)
        label = ttk.Label(frame, image=picture)
        label.image = picture
        label.pack()
        ttk.Label(frame, text=f"物理像素范围：{region}", wraplength=360).pack(pady=6)
        ttk.Button(frame, text="确认，使用这个窗口", style="Accent.TButton", command=lambda: self._confirm_window(target, popup)).pack(fill="x")
        ttk.Button(frame, text="取消，重新选择", command=popup.destroy).pack(fill="x", pady=(6, 0))

    def _confirm_window(self, target, popup) -> None:
        from bot.window_target import client_region
        try:
            region = client_region(target.handle)
        except RuntimeError as exc:
            self.location_status.configure(text=str(exc))
            popup.destroy()
            return
        self.located_window = target
        self.cfg["region"] = region
        self.cfg["_window_handle"] = target.handle
        self.location_status.configure(text=f"已定位：{region[2]}×{region[3]}，位置 ({region[0]}, {region[1]})。运行时跟随窗口移动；关闭或最小化会停止。", foreground="#23804a")
        self.btn_start.configure(state="normal")
        self.status.configure(text="窗口已定位，可以配置听力并启动")
        popup.destroy()

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
            ("fast", "快档：录音上限 2.0s，最多尝试 1 次"),
            ("balanced", "均衡档：录音上限 2.4s，失败可重听 1 次"),
            ("accurate", "准档：录音上限 3.2s，失败可重听 2 次"),
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
            text="本地 Whisper（无需 API · 可用 GPU 时自动加速）",
            value="local",
            variable=self.var_asr,
            command=self._refresh_note,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))

        ttk.Radiobutton(
            box,
            text="语音识别 API（减少本机计算 · 需配置兼容语音接口）",
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
        self.model_guide = ttk.Label(box, text="", foreground="#23456b", wraplength=480, justify="left", padding=(8, 8))
        self.model_guide.grid(row=box.grid_size()[1], column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(box, text="怎么选：CPU 速度优先选 base.en；日常均衡选 small.en；有 GPU 再尝试 distil-large-v3。", wraplength=480, foreground="#607089").grid(row=box.grid_size()[1], column=0, columnspan=2, sticky="w", pady=(4, 4))
        self._sync_asr_state()
        self.var_asr.trace_add("write", lambda *_: self._sync_asr_state())

        tip = ttk.Label(
            box,
            text="本地模式无需 API Key，模型首次使用可能下载，之后可离线运行。清晰单词优先单次解码；云端速度取决于网络与服务商。",
            foreground="#666666",
            wraplength=480,
        )
        tip.grid(row=box.grid_size()[1], column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _sync_asr_state(self) -> None:
        local = self.var_asr.get() == "local"
        self.whisper_box.configure(state="readonly" if local else "disabled")
        self.asr_model_box.configure(state="normal" if not local else "disabled")

    def _refresh_model_guide(self) -> None:
        backend = self.var_asr.get()
        model = self.var_whisper.get() if backend == "local" else self.var_asr_model.get()
        self.model_guide.configure(text=model_guidance(model, backend))
        if self.cfg.get("whisper_model_path") and backend == "local":
            self.model_guide.configure(text=self.model_guide.cget("text") + "\n当前配置了 whisper_model_path；实际优先加载该路径中的模型。")
        self._refresh_note()

    def _section_audio(self) -> None:
        box = ttk.LabelFrame(self.panel, text="声音监听与播放检查", padding=10)
        box.pack(fill="x", pady=(0, 8))
        box.columnconfigure(1, weight=1)
        self._row(box, "答题方式", ttk.Combobox(box, textvariable=self.var_mode, values=["whisper", "reveal", "dummy"], state="readonly"))
        self.device_box = ttk.Combobox(box, textvariable=self.var_device, values=list(self.device_choices), state="readonly")
        self._row(box, "监听的输出设备", self.device_box)
        ttk.Checkbutton(box, text="前台点击喇叭（播放无响应时启用）", variable=self.var_speaker_click).grid(row=2, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(box, text="低置信度时复核解码（清晰单词直接通过）", variable=self.var_verify).grid(row=3, column=0, columnspan=2, sticky="w")
        buttons = ttk.Frame(box)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=6)
        self.audio_buttons = []
        for label, command in (("刷新设备", self.refresh_devices), ("试录 3 秒", self.test_audio), ("试听最近录音", self.play_audio), ("停止试听", self.stop_playback)):
            btn = ttk.Button(buttons, text=label, command=command)
            btn.pack(side="left", padx=(0, 4))
            self.audio_buttons.append(btn)
        self.audio_status = ttk.Label(box, text="选择微信实际使用的输出设备；试录时请手动播放声音。", wraplength=480, foreground="#607089")
        self.audio_status.grid(row=5, column=0, columnspan=2, sticky="w")

    def _audio_job(self, task) -> None:
        if (self.thread and self.thread.is_alive()) or (self.audio_thread and self.audio_thread.is_alive()):
            self.audio_status.configure(text="请先停止机器人或等待当前声音检查结束。")
            return
        for btn in self.audio_buttons:
            btn.configure(state="disabled")
        self.btn_start.configure(state="disabled")
        def worker():
            try:
                task()
            except Exception as exc:
                message = f"声音检查失败：{exc}"
                self.ui_queue.put(lambda message=message: self.audio_status.configure(text=message))
            finally:
                self.ui_queue.put(self._audio_done)
        self.audio_thread = threading.Thread(target=worker, daemon=True)
        self.audio_thread.start()

    def _audio_done(self) -> None:
        for btn in self.audio_buttons:
            btn.configure(state="normal")
        self.btn_start.configure(state="normal" if self.located_window else "disabled")

    def refresh_devices(self) -> None:
        def task():
            from bot.audio_backend import soundcard_session
            choices = {"跟随系统默认输出": ""}
            with soundcard_session() as sc:
                for mic in sc.all_microphones(include_loopback=True):
                    if getattr(mic, "isloopback", False):
                        choices[f"{mic.name} · {mic.id}"] = str(mic.id)
            self.ui_queue.put(lambda: self._devices_ready(choices))
        self._audio_job(task)

    def _devices_ready(self, choices) -> None:
        selected = self.device_choices.get(self.var_device.get(), self.var_device.get())
        self.device_choices = choices
        self.device_box.configure(values=list(choices))
        label = next((label for label, value in choices.items() if value == selected), None)
        if label is not None:
            self.var_device.set(label)
        self.audio_status.configure(text=f"找到 {len(choices) - 1} 个回环输出设备。" + ("原设备不在列表中，请重新选择。" if label is None else ""))

    def test_audio(self) -> None:
        cfg = copy.deepcopy(self.cfg)
        cfg["audio_loopback_name"] = self.device_choices.get(self.var_device.get(), self.var_device.get())
        cfg["audio_record_seconds"] = 3.0
        cfg["audio_min_rms"] = 0.0
        cfg["audio_test_mode"] = True
        self.audio_status.configure(text="正在准备录音，请现在手动播放小程序声音（约 3 秒）。")
        self._audio_job(lambda: self._record_test(cfg))

    def _record_test(self, cfg) -> None:
        from bot.audio_solver import AudioSolver
        solver = AudioSolver(cfg, ROOT / "logs")
        solver.record_after_click(lambda: None)
        message = f"试录完成，原始音量 RMS={solver.last_recording_rms:.6f}。点击试听确认；接近 0 表示无声音。"
        self.ui_queue.put(lambda: self.audio_status.configure(text=message))

    def play_audio(self) -> None:
        if self.thread and self.thread.is_alive():
            self.audio_status.configure(text="请先停止机器人再试听，避免录入回放声音。")
            return
        import winsound
        path = ROOT / "logs" / "last_dictation.wav"
        if not path.exists():
            self.audio_status.configure(text="还没有录音，请先试录。")
            return
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            self.audio_status.configure(text="正在通过 Windows 默认输出试听最近录音。")
        except RuntimeError as exc:
            self.audio_status.configure(text=f"无法播放录音：{exc}")

    def stop_playback(self) -> None:
        import winsound
        winsound.PlaySound(None, 0)

    def _clear_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _close(self) -> None:
        if (self.thread and self.thread.is_alive()) or (self.audio_thread and self.audio_thread.is_alive()):
            self.stop()
            self.status.configure(text="正在结束任务，请稍后关闭窗口…")
            self.root.after(250, self._close)
            return
        self.stop_playback()
        self.root.destroy()

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
                self.ui_queue.put(lambda error=str(exc): self._on_fetch_failed(error))

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
                lines.append(f"✓ 听写题：语音识别 API (模型: {self.var_asr_model.get()})，耗时以运行日志为准。")
            else:
                lines.append(f"✓ 听写题：本地 Whisper (模型: {self.var_whisper.get()}) 离线推理。")
            if self.var_api_enabled.get():
                lines.append(f"✓ 阅读大题：已启用视觉大模型 ({self.var_vmodel.get()}) 自动作答。")
            else:
                lines.append("✓ 阅读大题：未启用，遇到大题将等待人工作答。")
            lines.append(f"✓ 答题速度：{speed_text}")

        self.note.configure(text="\n".join(lines))
        self.speed_desc.configure(text=speed_text)

    def save(self, silent: bool = False) -> bool:
        if self.var_speed.get() != self.cfg.get("speed_preset", "balanced"):
            from bot.config import SPEED_OVERRIDES
            for key in SPEED_OVERRIDES.values():
                self.cfg.pop(key, None)
        self.cfg["speed_preset"] = self.var_speed.get()
        self.cfg["dictation_mode"] = self.var_mode.get()
        self.cfg["audio_loopback_name"] = self.device_choices.get(self.var_device.get(), self.var_device.get())
        self.cfg["speaker_foreground_click"] = self.var_speaker_click.get()
        self.cfg["whisper_verify_pass"] = self.var_verify.get()
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
                "api_key": self.var_key.get().strip() if self.var_key.get().strip() != os.environ.get(str(api.get("api_key_env") or "ONECLASS_API_KEY"), "") else "",
            }
        )
        self.cfg["api"] = api

        exercise = dict(self.cfg.get("exercise") or {})
        exercise["auto_submit"] = bool(self.var_autosubmit.get())
        self.cfg["exercise"] = exercise

        try:
            save_config(self.cfg)
        except OSError as exc:
            self.status.configure(text=f"保存失败：{exc}")
            return False
        self._refresh_note()
        if not silent:
            self.status.configure(text="配置已成功保存到 config.yaml")
        return True

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            self.status.configure(text="机器人已在运行中")
            return
        if self.located_window is None:
            self.status.configure(text="请先在第一步预览并确认 OneClass 窗口")
            return
        from bot.window_target import client_region
        try:
            self.cfg["region"] = client_region(self.located_window.handle)
        except RuntimeError as exc:
            self._invalidate_location()
            self.status.configure(text=str(exc))
            return
        if not self.save(silent=True):
            return
        self.stop_playback()
        self.stop_pending.clear()
        self.run_cfg = copy.deepcopy(self.cfg)
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
        file_handler = file_log_handler(ROOT)
        root_logger.addHandler(file_handler)
        try:
            from bot.controller import WordBot

            LOG.info("启动配置：%s | 听写=%s | 引擎=%s | 速度=%s", ROOT / "config.yaml", self.run_cfg.get("dictation_mode"), self.run_cfg.get("asr_backend"), self.run_cfg.get("speed_preset"))
            self.bot = WordBot(self.run_cfg, Path(ROOT))
            if self.stop_pending.is_set():
                self.bot.stop()
            else:
                self.ui_queue.put(lambda: self.status.configure(text="运行中 · 配置修改将在下次启动生效"))
                self.bot.run()
        except SystemExit as exc:
            LOG.info("机器人已正常结束（code=%s）", exc.code)
        except Exception:  # noqa: BLE001
            LOG.exception("机器人异常退出")
        finally:
            if self.bot is not None:
                self.bot.screen.close()
            root_logger.removeHandler(handler)
            root_logger.removeHandler(file_handler)
            try:
                file_handler.close()
            except Exception:
                pass
            self.ui_queue.put(self._on_bot_exit)

    def _on_bot_exit(self) -> None:
        self.btn_start.configure(state="normal" if self.located_window else "disabled")
        self.btn_stop.configure(state="disabled")
        self.status.configure(text="已停止")
        self.bot = None

    def stop(self) -> None:
        self.stop_pending.set()
        self.btn_stop.configure(state="disabled")
        self.status.configure(text="正在停止，等待当前操作结束…")
        if self.bot:
            self.bot.stop()

    def _drain_ui(self) -> None:
        while True:
            try:
                task = self.ui_queue.get_nowait()
                task()
            except queue.Empty:
                break
            except Exception:  # noqa: BLE001
                LOG.exception("界面更新失败")
        self._drain_logs()
        self.root.after(80, self._drain_ui)

    def _drain_logs(self) -> None:
        chunks = []
        for _ in range(200):
            try:
                chunks.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if chunks:
            self.log_text.configure(state="normal")
            for level, message in chunks:
                self.log_text.insert("end", message + "\n", level)
            if self.var_follow_log.get():
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
