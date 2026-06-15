from __future__ import annotations

from pathlib import Path
import time
import yaml
import pyautogui

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"

if not CONFIG.exists():
    raise SystemExit("未找到 config.yaml。")

with CONFIG.open("r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

region = cfg.get("region")
if isinstance(region, dict):
    left = int(region["left"])
    top = int(region["top"])
    width = int(region["width"])
    height = int(region["height"])
elif isinstance(region, (list, tuple)) and len(region) == 4:
    left, top, width, height = map(int, region)
else:
    raise SystemExit(f"config.yaml 中 region 格式无效：{region!r}")

print("请保持“任务超时警告”弹窗显示。")
print("5 秒内把鼠标移动到绿色“再来一组”按钮正中央，并保持不动。")
for i in range(5, 0, -1):
    print(f"{i}...", flush=True)
    time.sleep(1)

x, y = pyautogui.position()
nx = (x - left) / width
ny = (y - top) / height

if not (0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0):
    raise SystemExit(
        f"鼠标位置 ({x}, {y}) 不在已校准的小程序区域 "
        f"({left}, {top}, {width}, {height}) 内，请重试。"
    )

points = cfg.setdefault("fallback_points", {})
points["timeout_next_group"] = [round(nx, 6), round(ny, 6)]

with CONFIG.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print("绿色按钮坐标已保存：")
print(f"  absolute=({x}, {y})")
print(f"  relative=({x-left}, {y-top})")
print(f"  normalized=({nx:.6f}, {ny:.6f})")
print("现在运行：python main.py")
