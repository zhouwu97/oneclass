from __future__ import annotations

# Must run before importing PyAutoGUI, especially with custom scaling such as 115%.
from bot.windows_dpi import enable_dpi_awareness, system_scale_percent

DPI_MODE = enable_dpi_awareness()

import time

import pyautogui

from bot.config import ROOT, save_region
from bot.screen import Screen


def wait_point(message: str) -> tuple[int, int]:
    print(message)
    input("鼠标放好后按 Enter：")
    p = pyautogui.position()
    print(f"记录坐标：({p.x}, {p.y})")
    return p.x, p.y


def main() -> None:
    scale = system_scale_percent()
    print(f"DPI 模式：{DPI_MODE}" + (f"；检测缩放约 {scale}%" if scale else ""))
    print("请把电脑版微信小程序窗口完整显示，且之后运行时不要移动或缩放。")
    print("你当前的 2560×1440、115% 自定义缩放可以使用，但校准后不要更改缩放或切换显示器。")
    time.sleep(1)
    x1, y1 = wait_point("1/2：把鼠标移到小程序白色内容区的左上角。")
    x2, y2 = wait_point("2/2：把鼠标移到小程序内容区的右下角。")
    if x2 <= x1 or y2 <= y1:
        raise ValueError("右下角必须位于左上角的右下方")
    region = [x1, y1, x2 - x1, y2 - y1]
    save_region(region)
    screen = Screen(region)
    preview = ROOT / "calibration_preview.png"
    screen.capture().save(preview)
    print(f"已写入 config.yaml：region={region}")
    print(f"预览图：{preview}")
    print("检查预览图只包含小程序内容区，不要包含标题栏、桌面背景或窗口阴影。")


if __name__ == "__main__":
    main()
