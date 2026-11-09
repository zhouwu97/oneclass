from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DEFAULT_PROFILE_NAME = "stable"

DEFAULT_PROFILES: dict[str, dict[str, float]] = {
    "stable": {
        "loop_interval": 0.45,
        "after_click_delay": 0.55,
        "ocr_scale": 1.45,
        "ocr_min_score": 0.45,
        "unknown_delay": 0.45,
        "page_transition_delay": 0.55,
        "dictation_delay": 0.65,
    },
    "fast": {
        "loop_interval": 0.25,
        "after_click_delay": 0.35,
        "ocr_scale": 1.30,
        "ocr_min_score": 0.45,
        "unknown_delay": 0.30,
        "page_transition_delay": 0.40,
        "dictation_delay": 0.65,
    },
}


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    loop_interval: float
    after_click_delay: float
    ocr_scale: float
    ocr_min_score: float
    unknown_delay: float
    page_transition_delay: float
    dictation_delay: float


def _as_float(data: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(data.get(key, default))
    except (TypeError, ValueError):
        return default


def _configured_profiles(cfg: dict[str, Any]) -> dict[str, dict[str, float]]:
    profiles = {name: values.copy() for name, values in DEFAULT_PROFILES.items()}
    custom = cfg.get("runtime_profiles")
    if not isinstance(custom, dict):
        return profiles
    for name, values in custom.items():
        if not isinstance(name, str) or not isinstance(values, dict):
            continue
        merged = profiles.get(name, {}).copy()
        for key, value in values.items():
            try:
                merged[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        profiles[name] = merged
    return profiles


def resolve_runtime_profile(cfg: dict[str, Any]) -> RuntimeProfile:
    profiles = _configured_profiles(cfg)
    requested = str(cfg.get("runtime_profile") or DEFAULT_PROFILE_NAME).strip().lower()
    name = requested if requested in profiles else DEFAULT_PROFILE_NAME
    using_profile_config = "runtime_profile" in cfg or "runtime_profiles" in cfg
    values = profiles.get(name, profiles[DEFAULT_PROFILE_NAME]).copy()

    # Old configs did not have runtime_profile/runtime_profiles, so keep their
    # top-level timing/OCR fields authoritative for compatibility.
    if not using_profile_config:
        for key in (
            "loop_interval",
            "after_click_delay",
            "ocr_scale",
            "ocr_min_score",
            "unknown_delay",
            "page_transition_delay",
            "dictation_delay",
        ):
            if key in cfg:
                values[key] = _as_float(cfg, key, values[key])

    return RuntimeProfile(
        name=name,
        loop_interval=_as_float(values, "loop_interval", DEFAULT_PROFILES["stable"]["loop_interval"]),
        after_click_delay=_as_float(values, "after_click_delay", DEFAULT_PROFILES["stable"]["after_click_delay"]),
        ocr_scale=_as_float(values, "ocr_scale", DEFAULT_PROFILES["stable"]["ocr_scale"]),
        ocr_min_score=_as_float(values, "ocr_min_score", DEFAULT_PROFILES["stable"]["ocr_min_score"]),
        unknown_delay=_as_float(values, "unknown_delay", DEFAULT_PROFILES["stable"]["unknown_delay"]),
        page_transition_delay=_as_float(values, "page_transition_delay", DEFAULT_PROFILES["stable"]["page_transition_delay"]),
        dictation_delay=_as_float(values, "dictation_delay", DEFAULT_PROFILES["stable"]["dictation_delay"]),
    )
