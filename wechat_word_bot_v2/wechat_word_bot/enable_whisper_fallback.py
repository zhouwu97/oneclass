from __future__ import annotations

import shutil
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
BACKUP = ROOT / "config.yaml.before_whisper_fallback.bak"


def main() -> None:
    if not CONFIG.exists():
        raise SystemExit(f"未找到配置文件：{CONFIG}")

    try:
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"config.yaml 无法读取，请先修复配置：{exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit("config.yaml 顶层必须是映射结构")

    shutil.copy2(CONFIG, BACKUP)

    # 先使用现有 Faster-Whisper 语音识别；识别为空、录音失败、
    # 置信度不足或受到其他声音干扰时，才输入占位词继续流程。
    data["dictation_mode"] = "whisper"
    data["dictation_failure_strategy"] = "dummy"
    data.setdefault("dictation_fallback_word", "zzzz")

    # 稍微延长录音，并提高匹配门槛：宁可进入占位兜底，
    # 也不要把受干扰的声音错误匹配成另一个已学单词。
    data.setdefault("whisper_model", "small.en")
    data.setdefault("whisper_device", "cpu")
    data.setdefault("whisper_compute_type", "int8")
    data["audio_record_seconds"] = max(float(data.get("audio_record_seconds", 2.8)), 3.2)
    data["speech_match_threshold"] = max(float(data.get("speech_match_threshold", 52)), 60)

    CONFIG.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    print("已启用：Whisper 语音识别优先，失败时输入占位词继续。")
    print(f"配置备份：{BACKUP.name}")
    print("下一步：python -m pip install -r requirements-audio.txt")
    print("然后：python list_audio_devices.py")
    print("最后：python main.py")


if __name__ == "__main__":
    main()
