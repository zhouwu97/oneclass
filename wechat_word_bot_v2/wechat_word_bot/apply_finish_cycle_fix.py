from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil
import yaml

ROOT = Path(__file__).resolve().parent
CONTROLLER = ROOT / "bot" / "controller.py"
CONFIG = ROOT / "config.yaml"

for path in (CONTROLLER, CONFIG):
    if not path.exists():
        raise SystemExit(
            f"未找到 {path.relative_to(ROOT)}。请把本脚本放到项目根目录后运行。"
        )

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
controller_backup = CONTROLLER.with_name(
    f"controller.py.before_finish_cycle_{stamp}.bak"
)
config_backup = CONFIG.with_name(
    f"config.yaml.before_finish_cycle_{stamp}.bak"
)
shutil.copy2(CONTROLLER, controller_backup)
shutil.copy2(CONFIG, config_backup)

source = CONTROLLER.read_text(encoding="utf-8")

# 完整替换页面状态识别，避免第 60 题显示“完成”后仍被当成普通选择题。
new_detect_state = r'''    def detect_state(self, items: list[OCRItem]) -> str:
        text = norm_text(self.vision.joined(items))
        height = float(self.screen.region.height)

        def bottom_exact(label: str, min_ratio: float = 0.84) -> bool:
            wanted = norm_text(label)
            return any(
                norm_text(item.text) == wanted
                and item.cy >= height * min_ratio
                for item in items
            )

        # 弹窗中和页面底部可能同时出现“再来一组”，优先识别弹窗。
        if (
            "再来一组" in text
            and (
                "任务超时" in text
                or ("词语未背诵" in text and "我再想想" in text)
            )
        ):
            return "task_timeout"

        if "学习完成" in text:
            return "learn_complete"
        if "继续背单词" in text:
            return "section_complete"

        # 第 60 题答完后，底部按钮不是“下一词”，而是“完成”。
        # 必须在普通 test_choice/test_dictation 之前判断，否则会重复答最后一题。
        if "请选择正确的释义" in text and bottom_exact("完成"):
            return "choice_feedback_final"
        if "请填写你听到的单词" in text and bottom_exact("完成"):
            return "dictation_feedback_final"

        # 普通题目反馈页。
        if "请选择正确的释义" in text and bottom_exact("下一词"):
            return "choice_feedback"
        if "请填写你听到的单词" in text and bottom_exact("下一词"):
            return "dictation_feedback"

        if "请选择正确的释义" in text or (
            "单词测试" in text
            and "我不会" in text
            and "确定" not in text
        ):
            return "test_choice"
        if "请填写你听到的单词" in text:
            return "test_dictation"

        # 测验总结页：底部有“重新测验”和“完成测验”。
        if "完成测验" in text or (
            "正确率" in text and "重新测验" in text
        ):
            return "test_result"

        # 兼容其他可能的完成提示。
        if any(
            value in text
            for value in ("测验完成", "测试完成", "本次成绩")
        ):
            return "test_complete"

        # 重复复习词：显示答案后点击“标记为太简单”。
        if "标记为太简单" in text:
            return "learn_too_easy"
        if "认识" in text and ("模糊" in text or "不认识" in text):
            return "learn_revealed"
        if "下一词" in text and ("释义" in text or "例句" in text):
            return "learn_detail"
        if "显示答案" in text or "请跟读并默念" in text:
            return "learn_hidden"
        if "开始背诵" in text:
            return "word_home"

        # 没有弹窗时，单词主页底部可能直接显示橙色“再来一组”。
        if (
            "再来一组" in text
            and any(
                value in text
                for value in ("截止日期", "词库预览", "已学习")
            )
        ):
            return "word_home_extra"
        if "今日任务" in text and "学习天数" in text:
            return "home"
        return "unknown"

'''

detect_pattern = re.compile(
    r"^    def detect_state\(self, items: list\[OCRItem\]\) -> str:\n"
    r".*?"
    r"(?=^    def learn_from_page\()",
    re.MULTILINE | re.DOTALL,
)
if not detect_pattern.search(source):
    raise SystemExit(
        "未找到 detect_state 方法，controller.py 版本与预期不同。"
        "已保留备份，请把当前 bot/controller.py 发来检查。"
    )
source = detect_pattern.sub(new_detect_state, source, count=1)

# 替换“再来一组”处理：弹窗优先定位中部绿色按钮，OCR 失败再用坐标。
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

        clicked = False
        if items:
            height = float(self.screen.region.height)
            candidates = [
                item
                for item in items
                if "再来一组" in norm_text(item.text)
            ]
            if modal:
                # 弹窗绿色按钮位于页面中部；排除底部被遮挡的橙色按钮。
                candidates = [
                    item
                    for item in candidates
                    if height * 0.45 <= item.cy <= height * 0.70
                ]
            else:
                candidates = [
                    item
                    for item in candidates
                    if item.cy >= height * 0.82
                ]
            if candidates:
                button = max(
                    candidates,
                    key=lambda item: (
                        item.score,
                        item.width * item.height,
                    ),
                )
                LOG.info(
                    "定位并点击%s‘再来一组’ at (%.0f, %.0f)",
                    "绿色弹窗按钮" if modal else "底部按钮",
                    button.cx,
                    button.cy,
                )
                self.screen.click_abs(button.cx, button.cy)
                clicked = True

        if not clicked:
            point_name = (
                "timeout_next_group" if modal else "start_next_group"
            )
            LOG.warning(
                "OCR 未定位到正确的‘再来一组’，使用兜底坐标 %s",
                point_name,
            )
            self.screen.click_rel(*self.point(point_name))

        self.extra_groups_started += 1
        self.extra_group_mode = True
        LOG.info(
            "已开始额外第 %d 组；直接继续识别‘今日新词’，不等待今日复习。",
            self.extra_groups_started,
        )

'''

start_pattern = re.compile(
    r"^    def start_extra_group\(.*?"
    r"(?=^    def act\()",
    re.MULTILINE | re.DOTALL,
)
if not start_pattern.search(source):
    raise SystemExit(
        "未找到 start_extra_group 方法，controller.py 版本与预期不同。"
        "已保留备份，请把当前 bot/controller.py 发来检查。"
    )
source = start_pattern.sub(new_start_extra_group, source, count=1)

# 把弹窗和主页调用改为传入 OCR 结果，便于定位绿色按钮。
source = source.replace(
    "self.start_extra_group(modal=True)",
    "self.start_extra_group(modal=True, items=items)",
)
source = source.replace(
    "self.start_extra_group(modal=False)",
    "self.start_extra_group(modal=False, items=items)",
)

# 增加第 60 题“完成”分支。
choice_anchor = '        elif state == "choice_feedback":\n'
if choice_anchor not in source:
    raise SystemExit(
        "未找到 choice_feedback 分支。已保留备份，请把当前 controller.py 发来检查。"
    )
final_choice_action = '''        elif state == "choice_feedback_final":
            LOG.info("第 60 题已显示答案，点击底部‘完成’进入测验总结。")
            self.click_text(
                items,
                "完成",
                fallback="test_question_finish",
            )
        elif state == "dictation_feedback_final":
            LOG.info("第 60 题听写已显示答案，点击底部‘完成’进入测验总结。")
            self.click_text(
                items,
                "完成",
                fallback="test_question_finish",
            )
'''
source = source.replace(
    choice_anchor,
    final_choice_action + choice_anchor,
    1,
)

# 用明确的“完成测验”按钮处理总结页。
test_complete_pattern = re.compile(
    r'^        elif state == "test_complete":\n'
    r'.*?'
    r'(?=^        else:\n)',
    re.MULTILINE | re.DOTALL,
)
if not test_complete_pattern.search(source):
    raise SystemExit(
        "未找到 test_complete 分支。已保留备份，请把当前 controller.py 发来检查。"
    )
new_result_actions = '''        elif state == "test_result":
            LOG.info("检测到测验总结页，点击橙色‘完成测验’。")
            self.click_text(
                items,
                "完成测验",
                fallback="test_result_finish",
            )
        elif state == "test_complete":
            if not self.click_text(
                items,
                "完成测验",
                "确认",
                "完成",
                "返回首页",
                fallback="test_result_finish",
            ):
                LOG.info("检测到测试完成，但没有找到结束按钮，停止运行。")
                raise SystemExit(0)
'''
source = test_complete_pattern.sub(new_result_actions, source, count=1)

# 覆盖前先做语法检查。
compile(source, str(CONTROLLER), "exec")
CONTROLLER.write_text(source, encoding="utf-8")

with CONFIG.open("r", encoding="utf-8") as file:
    cfg = yaml.safe_load(file) or {}

points = cfg.setdefault("fallback_points", {})
# 第 60 题底部“完成”位于底部中央。
points.setdefault("test_question_finish", [0.50, 0.975])
# 总结页右下角橙色“完成测验”。
points.setdefault("test_result_finish", [0.74, 0.958])
# 保留并确认自动进入额外组。
cfg["auto_next_group"] = True

with CONFIG.open("w", encoding="utf-8") as file:
    yaml.safe_dump(cfg, file, allow_unicode=True, sort_keys=False)

print("测验结束循环修复完成：")
print("1. 第 60 题显示答案后自动点击底部‘完成’。")
print("2. 总结页自动点击橙色‘完成测验’。")
print("3. 任务超时弹窗自动定位并点击中部绿色‘再来一组’。")
print("4. 进入额外组后继续识别今日新词和后续测试。")
print(f"controller.py 备份：{controller_backup.name}")
print(f"config.yaml 备份：{config_backup.name}")
print("现在运行：python main.py")
