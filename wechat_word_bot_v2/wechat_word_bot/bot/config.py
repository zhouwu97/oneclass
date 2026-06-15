from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"找不到配置文件：{CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    region = cfg.get("region")
    if not isinstance(region, list) or len(region) != 4 or region[2] <= 0 or region[3] <= 0:
        raise ValueError("config.yaml 中 region 必须是 [left, top, width, height]")
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def save_region(region: list[int]) -> None:
    cfg = load_config()
    cfg["region"] = region
    save_config(cfg)
