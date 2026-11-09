from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"

SPEED_PRESETS: dict[str, dict[str, float]] = {
    "fast": {
        "record_seconds": 2.0,
        "beam_size": 1,
        "max_attempts": 1,
        "retry_delay": 0.3,
        "after_click_delay": 0.5,
    },
    "balanced": {
        "record_seconds": 2.4,
        "beam_size": 1,
        "max_attempts": 2,
        "retry_delay": 0.5,
        "after_click_delay": 0.7,
    },
    "accurate": {
        "record_seconds": 3.2,
        "beam_size": 5,
        "max_attempts": 3,
        "retry_delay": 0.65,
        "after_click_delay": 0.85,
    },
}

# config.yaml 中同名的显式配置优先于档位默认值。
SPEED_OVERRIDES = {
    "record_seconds": "audio_record_seconds",
    "beam_size": "whisper_beam_size",
    "max_attempts": "dictation_max_attempts",
    "retry_delay": "dictation_retry_delay",
    "after_click_delay": "after_click_delay",
}


def speed_settings(cfg: dict[str, Any]) -> dict[str, float]:
    preset = str(cfg.get("speed_preset", "balanced")).lower().strip()
    settings = dict(SPEED_PRESETS.get(preset, SPEED_PRESETS["balanced"]))
    for key, cfg_key in SPEED_OVERRIDES.items():
        if cfg.get(cfg_key) is not None:
            settings[key] = float(cfg[cfg_key])
    settings["max_attempts"] = max(1, int(settings["max_attempts"]))
    settings["beam_size"] = max(1, int(settings["beam_size"]))
    return settings


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
