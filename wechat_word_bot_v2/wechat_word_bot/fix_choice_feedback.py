from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "bot" / "controller.py"
BACKUP = ROOT / "bot" / "controller.py.before_choice_feedback.bak"

if not TARGET.exists():
    raise SystemExit(
        "未找到 bot/controller.py。请把本脚本放到 wechat_word_bot 项目根目录后再运行。"
    )

source = TARGET.read_text(encoding="utf-8")

if '"choice_feedback"' in source:
    print("controller.py 已包含选择题结果页处理，无需重复修改。")
    raise SystemExit(0)

detect_anchor = """        if "请选择正确的释义" in text or ("单词测试" in text and "我不会" in text and "确定" not in text):
            return "test_choice"
"""

detect_insert = """        # 选择题点击答案或“我不会”后，页面仍保留
        # “请选择正确的释义”，但底部按钮会变成“下一词”。
        # 必须在 test_choice 之前识别，否则会再次尝试答当前题。
        if "下一词" in text and "请选择正确的释义" in text:
            return "choice_feedback"
"""

act_anchor = """        elif state == "test_choice":
            self.handle_choice(items, image)
"""

act_insert = """        elif state == "choice_feedback":
            # 正确/错误反馈页无需再次答题，直接进入下一题。
            self.click_text(items, "下一词", fallback="next_word")
"""

if detect_anchor not in source:
    raise SystemExit(
        "未找到页面识别代码锚点，可能 controller.py 版本不同。"
        "请不要继续覆盖文件，把当前 bot/controller.py 发给我。"
    )

if act_anchor not in source:
    raise SystemExit(
        "未找到动作处理代码锚点，可能 controller.py 版本不同。"
        "请不要继续覆盖文件，把当前 bot/controller.py 发给我。"
    )

shutil.copy2(TARGET, BACKUP)

source = source.replace(detect_anchor, detect_insert + detect_anchor, 1)
source = source.replace(act_anchor, act_insert + act_anchor, 1)

compile(source, str(TARGET), "exec")
TARGET.write_text(source, encoding="utf-8")

print("修复完成：选择题结果页会自动点击“下一词”。")
print(f"原文件备份：{BACKUP}")
print("现在运行：python main.py")
