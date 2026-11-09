from __future__ import annotations

# DPI awareness must be enabled before importing PyAutoGUI/MSS through the bot.
from bot.windows_dpi import enable_dpi_awareness, system_scale_percent

DPI_MODE = enable_dpi_awareness()

import logging
import sys
from pathlib import Path

from bot.config import ROOT, load_config
from bot.logging_utils import file_log_handler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            file_log_handler(ROOT),
        ],
    )
    scale = system_scale_percent()
    logger = logging.getLogger("wordbot")
    logger.info(
        "Windows DPI 模式：%s%s",
        DPI_MODE,
        f"，检测缩放约 {scale}%" if scale else "",
    )
    cfg = load_config()
    logger.info(
        "配置文件：%s | input_backend=%s | win32_fallback_to_pyautogui=%s | allow_foreground_mouse=%s | speaker_foreground_click=%s | dictation_foreground_click=%s | extra_group_foreground_click=%s",
        cfg.get("_config_path", "unknown"),
        cfg.get("input_backend", "win32"),
        cfg.get("win32_fallback_to_pyautogui", True),
        cfg.get("allow_foreground_mouse", False),
        cfg.get("speaker_foreground_click", False),
        cfg.get("dictation_foreground_click", True),
        cfg.get("extra_group_foreground_click", False),
    )
    from bot.controller import WordBot

    bot = WordBot(cfg, Path(ROOT))
    bot.run()


if __name__ == "__main__":
    main()
