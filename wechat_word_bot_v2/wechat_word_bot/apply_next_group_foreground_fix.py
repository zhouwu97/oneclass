from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil
import yaml

ROOT = Path(__file__).resolve().parent
CONTROLLER = ROOT / "bot" / "controller.py"
SCREEN = ROOT / "bot" / "screen.py"
CONFIG = ROOT / "config.yaml"

for path in (CONTROLLER, SCREEN, CONFIG):
    if not path.exists():
        raise SystemExit(
            f"未找到 {path.relative_to(ROOT)}。请把本脚本放到项目根目录后运行。"
        )

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
for path in (CONTROLLER, SCREEN, CONFIG):
    shutil.copy2(
        path,
        path.with_name(f"{path.name}.before_next_group_fix_{stamp}.bak"),
    )

screen_source = SCREEN.read_text(encoding="utf-8")
if "def foreground_click_rel(" not in screen_source:
    raise SystemExit(
        "screen.py 中没有 foreground_click_rel。"
        "请先安装之前的听写交互修复 v2，再运行本补丁。"
    )

controller = CONTROLLER.read_text(encoding="utf-8")

new_start_extra_group = r'''    def start_extra_group(
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
        target_nx: float | None = None
        target_ny: float | None = None

        if items:
            candidates = [
                item
                for item in items
                if "再来一组" in norm_text(item.text)
            ]
            if modal:
                candidates = [
                    item
                    for item in candidates
                    if height * 0.42 <= item.cy <= height * 0.72
                ]
            else:
                candidates = [
                    item
                    for item in candidates
                    if item.cy >= height * 0.80
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
                    "定位到%s‘再来一组’ at (%.0f, %.0f)，改用真实鼠标短暂点击。",
                    "绿色弹窗按钮" if modal else "底部按钮",
                    button.cx,
                    button.cy,
                )

        if target_nx is None or target_ny is None:
            point_name = (
                "timeout_next_group" if modal else "start_next_group"
            )
            target_nx, target_ny = self.point(point_name)
            LOG.warning(
                "OCR 未定位到正确的‘再来一组’，"
                "使用兜底坐标 %s，并改用真实鼠标短暂点击。",
                point_name,
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
                "上次点击后弹窗仍存在，已重新前台点击绿色‘再来一组’；"
                "继续等待页面切换。"
            )
        else:
            LOG.info(
                "已前台点击绿色‘再来一组’，"
                "将在识别到新词页面后确认额外组开始。"
            )

'''

start_pattern = re.compile(
    r"^    def start_extra_group\(.*?"
    r"(?=^    def act\()",
    re.MULTILINE | re.DOTALL,
)
if not start_pattern.search(controller):
    raise SystemExit(
        "未找到 start_extra_group 方法。"
        "请把当前 bot/controller.py 发来检查。"
    )
controller = start_pattern.sub(
    new_start_extra_group,
    controller,
    count=1,
)

act_pattern = re.compile(
    r"^(    def act\(self, state: str, items: list\[OCRItem\], image: Image\.Image\) -> None:\n)",
    re.MULTILINE,
)
if not act_pattern.search(controller):
    raise SystemExit(
        "未找到 act 方法。请把当前 bot/controller.py 发来检查。"
    )

transition_code = r'''        if (
            bool(getattr(self, "_extra_group_transition_pending", False))
            and state
            in {
                "learn_hidden",
                "learn_too_easy",
                "learn_revealed",
                "learn_detail",
                "section_complete",
                "learn_complete",
                "test_choice",
                "test_dictation",
            }
        ):
            self._extra_group_transition_pending = False
            self.extra_groups_started += 1
            LOG.info(
                "已确认进入额外第 %d 组；继续识别今日新词和后续测试。",
                self.extra_groups_started,
            )

'''

controller = re.sub(
    r'^        if \(\n'
    r'^            bool\(getattr\(self, "_extra_group_transition_pending", False\)\)\n'
    r'.*?'
    r'^            \)\n\n',
    "",
    controller,
    flags=re.MULTILINE | re.DOTALL,
)
controller = act_pattern.sub(
    lambda m: m.group(1) + transition_code,
    controller,
    count=1,
)

compile(controller, str(CONTROLLER), "exec")
CONTROLLER.write_text(controller, encoding="utf-8")

with CONFIG.open("r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

cfg["auto_next_group"] = True
cfg["extra_group_retry_seconds"] = 3.0
points = cfg.setdefault("fallback_points", {})
points["timeout_next_group"] = [0.62, 0.49]

with CONFIG.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print("修复完成：")
print("1. 绿色‘再来一组’改为真实鼠标短暂点击，不再使用无效的后台消息。")
print("2. 弹窗未消失时每 3 秒重试一次，不再每轮循环重复累加组数。")
print("3. 只有识别到新词/测试页面后，才确认额外组开始。")
print("4. 页面切换后继续自动识别，不需要重新校准。")
print("现在运行：python main.py")
