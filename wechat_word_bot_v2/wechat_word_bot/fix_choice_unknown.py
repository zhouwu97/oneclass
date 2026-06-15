from __future__ import annotations

from pathlib import Path
import shutil
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
BACKUP = ROOT / "config.yaml.before_choice_unknown.bak"

if not CONFIG.exists():
    raise SystemExit("未找到 config.yaml。请把本脚本放到 wechat_word_bot 项目根目录后再运行。")

shutil.copy2(CONFIG, BACKUP)

with CONFIG.open("r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

# 选择题词库缺失、OCR 选项不完整或匹配度不足时，不再暂停，
# 而是点击底部“我不会”，让小程序显示正确答案并继续流程。
cfg["unknown_choice_strategy"] = "unknown"

# 保留原有 Whisper 方案：识别失败时才用占位词，不影响选择题。
cfg["dictation_mode"] = "whisper"
cfg["dictation_failure_strategy"] = "dummy"
cfg.setdefault("dictation_fallback_word", "zzzz")

with CONFIG.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print("修复完成：")
print("  unknown_choice_strategy: unknown")
print("  dictation_mode: whisper")
print("  dictation_failure_strategy: dummy")
print(f"原配置已备份为：{BACKUP.name}")
print("现在运行：python main.py")
