from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

import pyautogui
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"
if not CONFIG.exists():
    raise SystemExit("未找到 config.yaml，请把本脚本放在项目根目录运行。")

with CONFIG.open("r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}
region = cfg.get("region")
if not isinstance(region, list) or len(region) != 4:
    raise SystemExit("config.yaml 中的 region 无效，请先运行 calibrate.py。")
left, top, width, height = map(float, region)

print("请让小程序停在空白听写页面，并保持窗口位置不变。")
print("每一步把鼠标放到指定位置，然后回到 PowerShell 按 Enter。")


def capture(name: str, prompt: str) -> list[float]:
    input(prompt + "\n放好鼠标后按 Enter：")
    pos = pyautogui.position()
    nx = (pos.x - left) / width
    ny = (pos.y - top) / height
    if not (0 <= nx <= 1 and 0 <= ny <= 1):
        raise SystemExit(
            f"记录的 {name} 坐标不在小程序区域内：({pos.x}, {pos.y})"
        )
    value = [round(nx, 5), round(ny, 5)]
    print(f"已记录 {name}: {value}")
    return value

speaker = capture("speaker", "1/3 把鼠标放在橙色喇叭图标正中央。")
input_point = capture("dictation_input", "2/3 把鼠标放在单词输入横线的正中央。")
confirm = capture("dictation_confirm", "3/3 把鼠标放在橙色‘确定’按钮正中央。")

backup = CONFIG.with_name(
    f"config.yaml.before_dictation_point_calibration_{datetime.now():%Y%m%d_%H%M%S}.bak"
)
shutil.copy2(CONFIG, backup)
points = cfg.setdefault("fallback_points", {})
points["speaker"] = speaker
points["dictation_input"] = input_point
points["dictation_confirm"] = confirm

with CONFIG.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print("听写坐标校准完成。")
print(f"配置备份：{backup.name}")
print("现在运行：python main.py")
