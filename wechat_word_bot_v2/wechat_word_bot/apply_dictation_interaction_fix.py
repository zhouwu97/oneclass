from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import shutil
import yaml

ROOT = Path(__file__).resolve().parent
SCREEN = ROOT / "bot" / "screen.py"
CONTROLLER = ROOT / "bot" / "controller.py"
CONFIG = ROOT / "config.yaml"

for path in (SCREEN, CONTROLLER, CONFIG):
    if not path.exists():
        raise SystemExit(
            f"未找到 {path.relative_to(ROOT)}。请把本脚本放到项目根目录后运行。"
        )

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
for path in (SCREEN, CONTROLLER, CONFIG):
    shutil.copy2(path, path.with_name(f"{path.name}.before_interaction_fix_{stamp}.bak"))

# ---------- screen.py ----------
screen = SCREEN.read_text(encoding="utf-8")
if "import pyperclip" not in screen:
    if "import pyautogui\n" not in screen:
        raise SystemExit("screen.py 中未找到 pyautogui 导入。")
    screen = screen.replace("import pyautogui\n", "import pyautogui\nimport pyperclip\n", 1)

# Remove any older copy of these helpers so the patch is safe to rerun.
for method_name in ("_restore_user_state", "foreground_click_rel", "foreground_fill_and_click_abs"):
    pattern = re.compile(
        rf"^    def {method_name}\(.*?(?=^    def |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    screen = pattern.sub("", screen)

screen_helpers = r'''    def _restore_user_state(self, old_pos, old_hwnd: int | None) -> None:
        try:
            pyautogui.moveTo(old_pos.x, old_pos.y, duration=0)
        except Exception:  # noqa: BLE001
            pass
        if old_hwnd and hasattr(ctypes, "windll"):
            try:
                ctypes.windll.user32.SetForegroundWindow(old_hwnd)
            except Exception:  # noqa: BLE001
                pass

    def foreground_click_rel(
        self,
        nx: float,
        ny: float,
        delay: float = 0.20,
    ) -> None:
        """短暂使用真实鼠标点击 WebView 控件，随后恢复鼠标和焦点。"""
        screen_x, screen_y = self.relative(nx, ny)
        old_pos = pyautogui.position()
        old_hwnd = None
        if hasattr(ctypes, "windll"):
            try:
                old_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                old_hwnd = None
        try:
            pyautogui.click(screen_x, screen_y)
            time.sleep(delay)
        finally:
            self._restore_user_state(old_pos, old_hwnd)

    def foreground_fill_and_click_abs(
        self,
        input_x: float,
        input_y: float,
        text: str,
        confirm_x: float,
        confirm_y: float,
        delay: float | None = None,
    ) -> None:
        """真实点击输入框、粘贴文字并点击确定，然后恢复用户状态。"""
        input_screen = self.absolute(input_x, input_y)
        confirm_screen = self.absolute(confirm_x, confirm_y)
        old_pos = pyautogui.position()
        old_hwnd = None
        old_clipboard: str | None = None
        if hasattr(ctypes, "windll"):
            try:
                old_hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            except Exception:  # noqa: BLE001
                old_hwnd = None
        try:
            try:
                old_clipboard = pyperclip.paste()
            except Exception:  # noqa: BLE001
                old_clipboard = None

            pyautogui.click(*input_screen)
            time.sleep(0.25)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.30)
            pyautogui.click(*confirm_screen)
            time.sleep(self.after_click_delay if delay is None else delay)
        finally:
            if old_clipboard is not None:
                try:
                    pyperclip.copy(old_clipboard)
                except Exception:  # noqa: BLE001
                    pass
            self._restore_user_state(old_pos, old_hwnd)

'''

screen_anchor = "    def save_debug("
idx = screen.find(screen_anchor)
if idx < 0:
    raise SystemExit("screen.py 中未找到 save_debug 方法。")
screen = screen[:idx] + screen_helpers + screen[idx:]
compile(screen, str(SCREEN), "exec")
SCREEN.write_text(screen, encoding="utf-8")

# ---------- controller.py ----------
controller = CONTROLLER.read_text(encoding="utf-8")

# Remove an older helper if present.
controller = re.sub(
    r"^    def submit_word\(.*?(?=^    def |\Z)",
    "",
    controller,
    flags=re.MULTILINE | re.DOTALL,
)

submit_helper = r'''    def submit_word(self, items: list[OCRItem], word: str) -> None:
        """点击输入框、填写单词并点击确定。"""
        input_nx, input_ny = self.point("dictation_input")
        input_x = input_nx * self.screen.region.width
        input_y = input_ny * self.screen.region.height

        confirm = self.vision.find(items, "确定")
        if confirm:
            confirm_x, confirm_y = confirm.cx, confirm.cy
        else:
            confirm_nx, confirm_ny = self.point("dictation_confirm")
            confirm_x = confirm_nx * self.screen.region.width
            confirm_y = confirm_ny * self.screen.region.height
            LOG.warning("OCR 未找到确定按钮，使用 dictation_confirm 兜底坐标")

        LOG.info("前台辅助提交听写：点击输入框，填写 %s，再点击确定", word)
        self.screen.foreground_fill_and_click_abs(
            input_x,
            input_y,
            word,
            confirm_x,
            confirm_y,
        )

'''

handle_idx = controller.find("    def handle_dictation(")
if handle_idx < 0:
    raise SystemExit("controller.py 中未找到 handle_dictation 方法。")
controller = controller[:handle_idx] + submit_helper + controller[handle_idx:]

# Any successful or fallback submission should use the foreground helper.
controller = re.sub(
    r"(?m)^(\s*)self\.type_word\(word\)\s*\n\1self\.click_text\(items, \"确定\", fallback=\"dictation_confirm\"\)",
    r"\1self.submit_word(items, word)",
    controller,
)

# Support a multiline click_text spelling as well.
controller = re.sub(
    r"(?ms)^(\s*)self\.type_word\(word\)\s*\n\1self\.click_text\(\s*items,\s*\"确定\",\s*fallback=\"dictation_confirm\",?\s*\)",
    r"\1self.submit_word(items, word)",
    controller,
)

# Speaker replay must be a real click on the WebView control.
controller = controller.replace(
    "lambda: self.screen.click_rel(*speaker, delay=0.05)",
    "lambda: self.screen.foreground_click_rel(*speaker, delay=0.25)",
)
controller = controller.replace(
    "lambda: self.screen.click_rel(*speaker, delay=0.15)",
    "lambda: self.screen.foreground_click_rel(*speaker, delay=0.25)",
)

# Ensure feedback is detected before a new dictation page.
feedback_rule = '''        if "下一词" in text and "请填写你听到的单词" in text:
            return "dictation_feedback"
'''
controller = re.sub(
    r'^        if "下一词" in text and "请填写你听到的单词" in text:\n'
    r'^            return "dictation_feedback"\n',
    "",
    controller,
    flags=re.MULTILINE,
)
dict_anchor = '        if "请填写你听到的单词" in text:\n'
if dict_anchor not in controller:
    raise SystemExit("controller.py 中未找到听写页面状态判断。")
controller = controller.replace(dict_anchor, feedback_rule + dict_anchor, 1)

# Ensure the feedback action exists.
if 'elif state == "dictation_feedback":' not in controller:
    test_complete_anchor = '        elif state == "test_complete":\n'
    if test_complete_anchor not in controller:
        raise SystemExit("controller.py 中未找到 test_complete 分支。")
    action = '''        elif state == "dictation_feedback":
            self.click_text(items, "下一词", fallback="next_word")
'''
    controller = controller.replace(test_complete_anchor, action + test_complete_anchor, 1)

compile(controller, str(CONTROLLER), "exec")
CONTROLLER.write_text(controller, encoding="utf-8")

# ---------- config.yaml ----------
with CONFIG.open("r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

cfg["dictation_mode"] = "whisper"
cfg["dictation_max_attempts"] = max(2, int(cfg.get("dictation_max_attempts", 2)))
cfg["dictation_failure_strategy"] = "dummy"
cfg.setdefault("dictation_fallback_word", "zzzz")
points = cfg.setdefault("fallback_points", {})
# These are only defaults. Run calibrate_dictation_points.py immediately after
# this patch to record the exact positions on the user's current window.
points.setdefault("speaker", [0.50, 0.28])
points.setdefault("dictation_input", [0.50, 0.40])
points.setdefault("dictation_confirm", [0.50, 0.46])

with CONFIG.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print("代码修复完成。")
print("下一步先运行：python calibrate_dictation_points.py")
print("校准三处坐标后，再运行：python main.py")
