from __future__ import annotations

# DPI awareness must be enabled before importing PyAutoGUI/MSS through the bot.
from bot.windows_dpi import enable_dpi_awareness, system_scale_percent

DPI_MODE = enable_dpi_awareness()

import logging
import sys
from pathlib import Path

from bot.config import ROOT, load_config
from bot.purchase import ensure_license_or_exit, exit_if_purchase_command


def main() -> None:
    exit_if_purchase_command(sys.argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(ROOT / "wordbot.log", encoding="utf-8"),
        ],
    )
    scale = system_scale_percent()
    logging.getLogger("wordbot").info(
        "Windows DPI 模式：%s%s",
        DPI_MODE,
        f"，检测缩放约 {scale}%" if scale else "",
    )
    cfg = load_config()
    if not ensure_license_or_exit(cfg):
        raise SystemExit(1)
    from bot.controller import WordBot

    bot = WordBot(cfg, Path(ROOT))
    bot.run()


if __name__ == "__main__":
    main()
