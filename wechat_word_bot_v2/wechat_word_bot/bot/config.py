from __future__ import annotations

import shutil
import math
import os
import tempfile
import sys
from pathlib import Path
from typing import Any

import yaml


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _resolve_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def _resolve_bundle_root(runtime_root: Path) -> Path:
    bundled = runtime_root / "_internal"
    if getattr(sys, "frozen", False) and bundled.exists():
        return bundled
    return SOURCE_ROOT


def _seed_runtime_file(runtime_root: Path, bundle_root: Path, filename: str) -> None:
    runtime_path = runtime_root / filename
    if runtime_path.exists():
        return
    bundled_path = bundle_root / filename
    if bundled_path.exists():
        runtime_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_path, runtime_path)


RUNTIME_ROOT = _resolve_runtime_root()
BUNDLE_ROOT = _resolve_bundle_root(RUNTIME_ROOT)
_seed_runtime_file(RUNTIME_ROOT, BUNDLE_ROOT, "config.yaml")
_seed_runtime_file(RUNTIME_ROOT, BUNDLE_ROOT, "vocab.json")

ROOT = RUNTIME_ROOT
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
    for key, value in settings.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"速度配置 {key} 必须为有限的非负数")
    if settings["record_seconds"] <= 0:
        raise ValueError("录音时长必须大于 0")
    return settings


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"找不到配置文件：{CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    region = cfg.get("region")
    if not isinstance(region, list) or len(region) != 4 or region[2] <= 0 or region[3] <= 0:
        raise ValueError("config.yaml 中 region 必须是 [left, top, width, height]")
    cfg["_config_path"] = str(CONFIG_PATH)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    persisted = {key: value for key, value in cfg.items() if not key.startswith("_")}
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_PATH.parent, suffix=".tmp", delete=False) as f:
            temp_path = Path(f.name)
            yaml.safe_dump(persisted, f, allow_unicode=True, sort_keys=False)
        os.replace(temp_path, CONFIG_PATH)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def save_region(region: list[int]) -> None:
    cfg = load_config()
    cfg["region"] = region
    save_config(cfg)
