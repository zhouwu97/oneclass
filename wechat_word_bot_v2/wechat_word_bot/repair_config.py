from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"

DEFAULTS = {
    "loop_interval": 0.65,
    "after_click_delay": 0.85,
    "ocr_scale": 1.6,
    "ocr_min_score": 0.45,
    "debug": True,
    "input_backend": "win32",
    "win32_fallback_to_pyautogui": False,
    "dictation_mode": "reveal",
    "fallback_on_audio_failure": "reveal",
    "auto_next_group": True,
    "max_extra_groups": 0,
    "unknown_choice_strategy": "pause",
    "choice_match_threshold": 42,
    "whisper_model": "small.en",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "audio_sample_rate": 48000,
    "audio_record_seconds": 2.8,
    "audio_loopback_name": "",
    "speech_match_threshold": 52,
    "fallback_points": {
        "reveal_answer": [0.50, 0.53],
        "know": [0.50, 0.805],
        "next_word": [0.50, 0.955],
        "continue_words": [0.50, 0.955],
        "complete_confirm": [0.50, 0.61],
        "start_memorize": [0.50, 0.95],
        "timeout_next_group": [0.66, 0.57],
        "start_next_group": [0.50, 0.95],
        "speaker": [0.50, 0.345],
        "dictation_input": [0.50, 0.445],
        "dictation_confirm": [0.50, 0.53],
        "unknown": [0.50, 0.975],
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
