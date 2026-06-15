from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "bot" / "controller.py"

if not TARGET.exists():
    raise SystemExit("未找到 bot/controller.py，请把脚本放到项目根目录后运行。")

source = TARGET.read_text(encoding="utf-8")
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = TARGET.with_name(f"controller.py.before_next_group_position_{stamp}.bak")

new_method = r'''    def start_extra_group(
        self,
        modal: bool,
        items: list[OCRItem] | None = None,
    ) -> None:
        if not bool(self.cfg.get("auto_next_group", True)):
            LOG.info("检测到‘再来一组’，但 auto_next_group=false，停止运行。")
            raise SystemExit(0)

        maximum = int(self.cfg.get("max_extra_groups", 0))
        if maximum > 0 and self.extra_groups_started >= maximum:
            LOG.info("已达到 max_extra_groups=%d，停止运行。", maximum)
            raise SystemExit(0)

        now = time.monotonic()
        retry_seconds = max(
            1.0,
            float(self.cfg.get("extra_group_retry_seconds", 3.0)),
        )
        pending = bool(
            getattr(self, "_extra_group_transition_pending", False)
        )
        last_click = float(
            getattr(self, "_extra_group_last_click_at", 0.0)
        )

        if pending and now - last_click < retry_seconds:
            LOG.info(
                "已前台点击‘再来一组’，等待页面切换（%.1f/%.1f 秒）。",
                now - last_click,
                retry_seconds,
            )
            return

        width = float(self.screen.region.width)
        height = float(self.screen.region.height)

        if modal:
            target_nx, target_ny = self.point("timeout_next_group")
            LOG.info(
                "弹窗‘再来一组’使用专用校准坐标："
                "normalized=(%.4f, %.4f)，absolute≈(%.0f, %.0f)。",
                target_nx,
                target_ny,
                target_nx * width,
                target_ny * height,
            )
        else:
            target_nx = None
            target_ny = None
            if items:
                candidates = [
                    item
                    for item in items
                    if "再来一组" in norm_text(item.text)
                    and item.cy >= height * 0.80
                ]
                if candidates:
                    button = max(
                        candidates,
                        key=lambda item: (
                            item.score,
                            item.width * item.height,
                        ),
                    )
                    target_nx = float(button.cx) / width
                    target_ny = float(button.cy) / height
                    LOG.info(
                        "定位到底部‘再来一组’ at (%.0f, %.0f)。",
                        button.cx,
                        button.cy,
                    )

            if target_nx is None or target_ny is None:
                target_nx, target_ny = self.point("start_next_group")
                LOG.warning(
                    "OCR 未定位到底部‘再来一组’，使用兜底坐标。"
                )

        self.screen.foreground_click_rel(
            target_nx,
            target_ny,
            delay=0.85,
        )
        self._extra_group_last_click_at = time.monotonic()
        self._extra_group_transition_pending = True
        self.extra_group_mode = True

        if pending:
            LOG.warning(
                "上次点击后弹窗仍存在，已按专用坐标重新点击绿色‘再来一组’。"
            )
        else:
            LOG.info(
                "已点击‘再来一组’，将在识别到新词页面后确认额外组开始。"
            )

'''

pattern = re.compile(
    r"^    def start_extra_group\(.*?(?=^    def act\()",
    re.MULTILINE | re.DOTALL,
)
if not pattern.search(source):
    raise SystemExit(
        "未找到 start_extra_group 方法。请把当前 bot/controller.py 发来检查。"
    )

updated = pattern.sub(new_method, source, count=1)
compile(updated, str(TARGET), "exec")

shutil.copy2(TARGET, backup)
TARGET.write_text(updated, encoding="utf-8")

print("代码修复完成：弹窗绿色“再来一组”不再使用 OCR 文本中心。")
print(f"原文件备份：{backup.name}")
print("下一步运行：python calibrate_next_group_button.py")
