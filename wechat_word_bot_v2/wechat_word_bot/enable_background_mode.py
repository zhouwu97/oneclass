from __future__ import annotations

from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parent / "config.yaml"


def main() -> None:
    if not CONFIG.exists():
        raise SystemExit("未找到 config.yaml。请把脚本放到项目根目录后再运行。")
    try:
        with CONFIG.open("r", encoding="utf-8-sig") as f:
            cfg = yaml.safe_load(f) or {}
    except yaml.YAMLError:
        raise SystemExit("config.yaml 已损坏，请先运行：python repair_config.py")

    region = cfg.get("region")
    if not isinstance(region, list) or len(region) != 4:
        raise SystemExit("config.yaml 中 region 无效，请重新运行 python calibrate.py。")

    cfg["input_backend"] = "win32"
    cfg["win32_fallback_to_pyautogui"] = False
    with CONFIG.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

    print("已启用 Win32 后台点击模式，region 校准坐标已保留。")
    print("运行：python main.py")


if __name__ == "__main__":
    main()
