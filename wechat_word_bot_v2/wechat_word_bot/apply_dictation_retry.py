from __future__ import annotations

from pathlib import Path
import shutil
import re
import yaml

ROOT = Path(__file__).resolve().parent
CONTROLLER = ROOT / "bot" / "controller.py"
CONFIG = ROOT / "config.yaml"
CONTROLLER_BACKUP = ROOT / "bot" / "controller.py.before_dictation_retry.bak"
CONFIG_BACKUP = ROOT / "config.yaml.before_dictation_retry.bak"

if not CONTROLLER.exists():
    raise SystemExit(
        "未找到 bot/controller.py。请把本脚本放到 wechat_word_bot 项目根目录后运行。"
    )
if not CONFIG.exists():
    raise SystemExit(
        "未找到 config.yaml。请把本脚本放到 wechat_word_bot 项目根目录后运行。"
    )

source = CONTROLLER.read_text(encoding="utf-8")

new_method = r'''    def handle_dictation(self, items: list[OCRItem]) -> None:
        mode = str(self.cfg.get("dictation_mode", "whisper")).lower().strip()
        if mode == "reveal":
            self.click_text(items, "我不会", fallback="unknown")
            return
        if mode == "dummy":
            self.submit_dummy_dictation(items)
            return
        if mode != "whisper":
            raise ValueError("dictation_mode 只能是 reveal、dummy 或 whisper")

        # 默认共听两次：第一次识别置信度不足或录音异常时，
        # 再点击一次喇叭重新录音和识别。次数可在 config.yaml 中修改。
        max_attempts = max(1, int(self.cfg.get("dictation_max_attempts", 2)))
        retry_delay = max(0.0, float(self.cfg.get("dictation_retry_delay", 0.65)))
        threshold = float(self.cfg.get("speech_match_threshold", 52))
        speaker = self.point("speaker")
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                LOG.info(
                    "听写识别第 %d/%d 次：点击喇叭并重新录音",
                    attempt,
                    max_attempts,
                )
                audio_path = self.audio.record_after_click(
                    lambda: self.screen.click_rel(*speaker, delay=0.05)
                )
                raw, heard = self.audio.transcribe(audio_path, self.vocab.words())
                word, score = self.vocab.closest_word(heard)
                LOG.info(
                    "听写识别第 %d/%d 次：raw=%r, heard=%r, match=%r, score=%.1f",
                    attempt,
                    max_attempts,
                    raw,
                    heard,
                    word,
                    score,
                )

                if word and score >= threshold:
                    self.type_word(word)
                    self.click_text(items, "确定", fallback="dictation_confirm")
                    return

                last_error = RuntimeError(
                    f"语音识别置信度不足：{heard!r} -> {word!r} ({score:.1f})"
                )
                if attempt < max_attempts:
                    LOG.warning(
                        "%s；%.2f 秒后再次点击喇叭重听",
                        last_error,
                        retry_delay,
                    )
                    time.sleep(retry_delay)
                    continue
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
                    time.sleep(retry_delay)
                    continue
                LOG.exception(
                    "第 %d/%d 次听写仍失败：%s",
                    attempt,
                    max_attempts,
                    exc,
                )

        # 重听次数全部用完后才执行兜底；默认输入 zzzz，
        # 不会一识别失败就直接点击“我不会”。
        reason = str(last_error or "多次重听后仍未识别到可靠单词")
        strategy = str(
            self.cfg.get("dictation_failure_strategy", "dummy")
        ).lower().strip()
        if strategy == "dummy":
            self.submit_dummy_dictation(items, reason)
        elif strategy == "reveal":
            self.click_text(items, "我不会", fallback="unknown")
        elif strategy == "pause":
            LOG.error("多次重听仍失败，已暂停；按 Enter 重试当前题，或 Ctrl+C 退出。")
            input()
        else:
            raise ValueError(
                "dictation_failure_strategy 只能是 dummy、reveal 或 pause"
            )
'''

pattern = re.compile(
    r"^    def handle_dictation\(self, items: list\[OCRItem\]\) -> None:\n"
    r".*?"
    r"(?=^    def start_extra_group\()",
    re.MULTILINE | re.DOTALL,
)

if not pattern.search(source):
    raise SystemExit(
        "未找到 handle_dictation 方法，controller.py 版本可能不同。"
        "请不要覆盖文件，把当前 bot/controller.py 发来检查。"
    )

shutil.copy2(CONTROLLER, CONTROLLER_BACKUP)
updated = pattern.sub(new_method + "\n", source, count=1)
compile(updated, str(CONTROLLER), "exec")
CONTROLLER.write_text(updated, encoding="utf-8")

with CONFIG.open("r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

shutil.copy2(CONFIG, CONFIG_BACKUP)
cfg["dictation_mode"] = "whisper"
cfg["dictation_max_attempts"] = 2
cfg["dictation_retry_delay"] = 0.65
cfg["dictation_failure_strategy"] = "dummy"
cfg.setdefault("dictation_fallback_word", "zzzz")
cfg["audio_record_seconds"] = max(
    3.2, float(cfg.get("audio_record_seconds", 3.2))
)

with CONFIG.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print("修复完成：")
print("1. 听写先点击喇叭录音并用 Whisper 识别。")
print("2. 识别为空、录音异常或匹配分不足时，会再点击一次喇叭重听。")
print("3. 两次都失败后才输入 zzzz 兜底，不会立即点击“我不会”。")
print(f"controller.py 备份：{CONTROLLER_BACKUP.name}")
print(f"config.yaml 备份：{CONFIG_BACKUP.name}")
print("现在运行：python main.py")
