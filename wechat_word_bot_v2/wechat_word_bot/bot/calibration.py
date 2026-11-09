from __future__ import annotations

import shutil
import time
from datetime import datetime

import pyautogui
import yaml

from .config import CONFIG_PATH, ROOT, load_config, save_region
from .screen import Screen
from .windows_dpi import system_scale_percent


def wait_point(message: str) -> tuple[int, int]:
    print(message)
    input("鼠标放好后按 Enter：")
    point = pyautogui.position()
    print(f"记录坐标：({point.x}, {point.y})")
    return int(point.x), int(point.y)


def calibrate_region(dpi_mode: str = "") -> None:
    scale = system_scale_percent()
    prefix = f"DPI 模式：{dpi_mode}" if dpi_mode else "DPI 模式：已启用"
    print(prefix + (f"；检测缩放约 {scale}%" if scale else ""))
    print("请把电脑版微信小程序窗口完整显示。")
    print("本步骤只记录小程序白色内容区，不要包含标题栏、桌面背景或窗口阴影。")
    time.sleep(1)
    x1, y1 = wait_point("1/2：把鼠标移到小程序白色内容区的左上角。")
    x2, y2 = wait_point("2/2：把鼠标移到小程序内容区的右下角。")
    if x2 <= x1 or y2 <= y1:
        raise ValueError("右下角必须位于左上角的右下方")

    region = [x1, y1, x2 - x1, y2 - y1]
    save_region(region)
    preview = ROOT / "calibration_preview.png"
    Screen(region, win32_fallback_to_pyautogui=False).capture().save(preview)
    print(f"已写入 config.yaml：region={region}")
    print(f"预览图：{preview}")
    print("请确认预览图只包含小程序内容区。")


def _load_calibration_config() -> tuple[dict, list[float]]:
    cfg = load_config()
    region = cfg.get("region")
    if not isinstance(region, list) or len(region) != 4:
        raise ValueError("config.yaml 中 region 无效，请先做内容区定位。")
    return cfg, list(map(float, region))


def _capture_normalized(name: str, prompt: str, region: list[float]) -> list[float]:
    left, top, width, height = region
    input(prompt + "\n放好鼠标后按 Enter：")
    point = pyautogui.position()
    nx = (point.x - left) / width
    ny = (point.y - top) / height
    if not (0 <= nx <= 1 and 0 <= ny <= 1):
        raise ValueError(f"记录的 {name} 坐标不在小程序区域内：({point.x}, {point.y})")
    value = [round(nx, 6), round(ny, 6)]
    print(f"已记录 {name}: {value}")
    return value


def calibrate_dictation_points() -> None:
    cfg, region = _load_calibration_config()
    print("请让小程序停在空白听写页面，并保持窗口位置不变。")
    print("每一步把鼠标放到指定位置，然后回到本窗口按 Enter。")
    speaker = _capture_normalized("speaker", "1/3 把鼠标放在橙色喇叭图标正中央。", region)
    input_point = _capture_normalized("dictation_input", "2/3 把鼠标放在单词输入横线正中央。", region)
    confirm = _capture_normalized("dictation_confirm", "3/3 把鼠标放在橙色“确定”按钮正中央。", region)

    backup = CONFIG_PATH.with_name(
        f"config.yaml.before_dictation_calibration_{datetime.now():%Y%m%d_%H%M%S}.bak"
    )
    shutil.copy2(CONFIG_PATH, backup)
    points = cfg.setdefault("fallback_points", {})
    points["speaker"] = speaker
    points["dictation_input"] = input_point
    points["dictation_confirm"] = confirm
    cfg["dictation_position_mode"] = "calibrated"
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        yaml.safe_dump(cfg, file, allow_unicode=True, sort_keys=False)
    print(f"听写坐标定位完成，备份：{backup.name}")


def calibrate_next_group_button() -> None:
    cfg, region = _load_calibration_config()
    print("请保持“任务超时警告”弹窗显示。")
    point = _capture_normalized("timeout_next_group", "把鼠标放到绿色“再来一组”按钮正中央。", region)

    backup = CONFIG_PATH.with_name(
        f"config.yaml.before_next_group_calibration_{datetime.now():%Y%m%d_%H%M%S}.bak"
    )
    shutil.copy2(CONFIG_PATH, backup)
    cfg.setdefault("fallback_points", {})["timeout_next_group"] = point
    with CONFIG_PATH.open("w", encoding="utf-8") as file:
        yaml.safe_dump(cfg, file, allow_unicode=True, sort_keys=False)
    print(f"绿色按钮坐标定位完成：{point}")
    print(f"配置备份：{backup.name}")
