from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"

DEFAULTS = {
    "input_backend": "win32",
    "win32_fallback_to_pyautogui": False,
    "loop_interval": 0.65,
    "ocr_scale": 1.6,
    "ocr_min_score": 0.45,
    "debug": True,
    "mark_too_easy": True,
    "dictation_mode": "whisper",
    "fallback_on_audio_failure": "reveal",
    "auto_next_group": True,
    "max_extra_groups": 0,
    "unknown_choice_strategy": "unknown",
    "choice_match_threshold": 42,
    "speed_preset": "balanced",
    "asr_backend": "local",
    "asr_api_model": "whisper-1",
    "whisper_model": "distil-large-v3",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "whisper_vad": True,
    "audio_sample_rate": 48000,
    "audio_loopback_name": "扬声器 (Realtek(R) Audio)",
    "speech_match_threshold": 60,
    "dictation_failure_strategy": "dummy",
    "dictation_fallback_word": "zzzz",
    "extra_group_retry_seconds": 3.0,
    "fallback_points": {
        "reveal_answer": [0.50, 0.53],
        "know": [0.50, 0.805],
        "next_word": [0.50, 0.955],
        "continue_words": [0.50, 0.955],
        "complete_confirm": [0.50, 0.61],
        "start_memorize": [0.50, 0.95],
        "timeout_next_group": [0.664583, 0.559086],
        "start_next_group": [0.50, 0.95],
        "speaker": [0.49375, 0.27408],
        "dictation_input": [0.48958, 0.37934],
        "dictation_confirm": [0.49167, 0.45084],
        "unknown": [0.50, 0.975],
        "test_question_finish": [0.50, 0.975],
        "test_result_finish": [0.74, 0.958],
    },
    "api": {
        "enabled": True,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-max-latest",
        "api_key": "",
        "api_key_env": "ONECLASS_API_KEY",
        "timeout": 90,
        "max_tokens": 3000,
        "temperature": 0.0,
        "max_image_width": 900,
        "jpeg_quality": 82,
        "max_frames": 8,
        "retry": 2,
        "task_keywords": {
            "paragraph_match": "段落匹配",
            "word_bank_fill": "选词填空",
            "reading_choice": "仔细阅读",
        },
    },
    "exercise": {
        "auto_submit": False,
        "scroll_pages": 6,
        "scroll_anchor": [0.5, 0.3],
        "blank_y_range": [0.08, 0.88],
        "sheet_delay": 0.8,
        "sheet_min_y_ratio": 0.45,
        "confirm_after_pick": True,
        "cooldown_seconds": 180,
    },
}


def recover_region(text: str) -> list[int]:
    inline = re.search(r"(?m)^region\s*:\s*\[\s*(-?\d+)\s*,\s*(-?\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]", text)
    if inline:
        return [int(x) for x in inline.groups()]

    # calibrate.py may have written region as a block list. The old patch
    # inserted keys between `region:` and these dash-prefixed values.
    nums = [int(x) for x in re.findall(r"(?m)^\s*-\s*(-?\d+)\s*$", text)]
    if len(nums) >= 4:
        return nums[:4]

    raise SystemExit("无法从旧 config.yaml 恢复 region。请重新运行 python calibrate.py。")


def main() -> None:
    if not CONFIG.exists():
        raise SystemExit("当前目录没有 config.yaml，请把本文件放到项目根目录后运行。")

    text = CONFIG.read_text(encoding="utf-8-sig")
    region = recover_region(text)

    data = {"region": region, **DEFAULTS}
    backup = CONFIG.with_suffix(".yaml.bak")
    backup.write_text(text, encoding="utf-8")
    CONFIG.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # Validate immediately.
    with CONFIG.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert loaded["region"] == region
    assert loaded["input_backend"] == "win32"
    assert loaded["win32_fallback_to_pyautogui"] is False

    print(f"config.yaml 已修复，region={region}")
    print(f"旧文件备份为：{backup.name}")
    print("后台输入已启用：input_backend=win32")
    print("现在运行：python main.py")


if __name__ == "__main__":
    main()
