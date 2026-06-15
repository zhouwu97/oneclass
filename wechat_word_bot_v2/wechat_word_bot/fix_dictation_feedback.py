from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import shutil

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "bot" / "controller.py"

if not TARGET.exists():
    raise SystemExit(
        "未找到 bot/controller.py。请把本脚本放到 wechat_word_bot 项目根目录后运行。"
    )

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
BACKUP = TARGET.with_name(f"controller.py.before_dictation_feedback_{stamp}.bak")
source = TARGET.read_text(encoding="utf-8")

# 1. 修复旧补丁中的方法名不一致。
source = source.replace(
    "self._submit_dummy_dictation(",
    "self.submit_dummy_dictation(",
)
source = source.replace(
    "    def _submit_dummy_dictation(",
    "    def submit_dummy_dictation(",
)

# 极少数混合版本中两种方法都不存在，补一个最小可用方法。
if "    def submit_dummy_dictation(" not in source:
    method_anchor = "    def handle_dictation("
    if method_anchor not in source:
        raise SystemExit(
            "未找到 handle_dictation 方法，请把当前 bot/controller.py 发来检查。"
        )
    dummy_method = '''    def submit_dummy_dictation(
        self,
        items: list[OCRItem],
        reason: str = "",
    ) -> None:
        word = str(
            self.cfg.get("dictation_fallback_word", "zzzz")
        ).strip() or "zzzz"
        if reason:
            LOG.warning(
                "听写改用占位词：%s；reason=%s",
                word,
                reason,
            )
        else:
            LOG.info("听写占位模式：输入 %s", word)
        self.type_word(word)
        self.click_text(
            items,
            "确定",
            fallback="dictation_confirm",
        )

'''
    source = source.replace(
        method_anchor,
        dummy_method + method_anchor,
        1,
    )

# 2. 结果页仍会保留“请填写你听到的单词”。
# 必须优先检查“下一词”，否则会被再次识别为听写题。
feedback_rule = '''        # 听写提交后，页面仍保留“请填写你听到的单词”，
        # 但底部已经出现“下一词”。必须优先识别为结果页。
        if "下一词" in text and (
            "请填写你听到的单词" in text
            or (
                "请选择正确的释义" not in text
                and "释义" not in text
                and "例句" not in text
                and "请跟读" not in text
            )
        ):
            return "dictation_feedback"
'''

# 删除旧的、位于 test_dictation 后面的规则。
old_feedback_pattern = re.compile(
    r'^        if "下一词" in text and '
    r'"释义" not in text and '
    r'"例句" not in text and '
    r'"请跟读" not in text:\n'
    r'^            return "dictation_feedback"\n',
    re.MULTILINE,
)
source = old_feedback_pattern.sub("", source)

# 支持重复运行本修复脚本。
inserted_pattern = re.compile(
    r'^        # 听写提交后，页面仍保留“请填写你听到的单词”，\n'
    r'^        # 但底部已经出现“下一词”。必须优先识别为结果页。\n'
    r'^        if "下一词" in text and \(\n'
    r'.*?'
    r'^            return "dictation_feedback"\n',
    re.MULTILINE | re.DOTALL,
)
source = inserted_pattern.sub("", source)

dictation_anchor = '        if "请填写你听到的单词" in text:\n'
if dictation_anchor not in source:
    raise SystemExit(
        "未找到听写页面状态判断，请把当前 bot/controller.py 发来检查。"
    )

source = source.replace(
    dictation_anchor,
    feedback_rule + dictation_anchor,
    1,
)

# 3. 确保结果页会点击“下一词”。
if 'elif state == "dictation_feedback":' not in source:
    act_anchor = '        elif state == "test_complete":\n'
    if act_anchor not in source:
        raise SystemExit(
            "未找到 act() 中的 test_complete 分支，"
            "请把当前 bot/controller.py 发来检查。"
        )
    feedback_action = '''        elif state == "dictation_feedback":
            found = self.vision.main_word(
                items,
                image.height,
                (0.48, 0.78),
            )
            if found:
                word, word_item = found
                definition = self.vision.definition(
                    items,
                    image.height,
                    word_item,
                )
                self.vocab.add(word, definition)
            self.click_text(
                items,
                "下一词",
                fallback="next_word",
            )
'''
    source = source.replace(
        act_anchor,
        feedback_action + act_anchor,
        1,
    )

# 先进行语法检查，成功后才覆盖。
compile(source, str(TARGET), "exec")
shutil.copy2(TARGET, BACKUP)
TARGET.write_text(source, encoding="utf-8")

print("修复完成：")
print("1. 修复 submit_dummy_dictation 方法名不一致导致的崩溃。")
print("2. 显示答案并出现“下一词”时，不再重复点击喇叭。")
print("3. 结果页会直接点击“下一词”，进入下一道题。")
print(f"原文件备份：{BACKUP.name}")
print("现在运行：python main.py")
