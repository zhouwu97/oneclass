"""Shared bounded logging for the desktop and command line entry points."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def file_log_handler(root: Path) -> logging.Handler:
    handler = RotatingFileHandler(
        root / "wordbot.log", maxBytes=5 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S",
    ))
    return handler
