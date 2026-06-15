from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import shutil
import yaml

ROOT = Path(__file__).resolve().parent
SCREEN = ROOT / 'bot' / 'screen.py'
CONTROLLER = ROOT / 'bot' / 'controller.py'
CONFIG = ROOT / 'config.yaml'

for p in (SCREEN, CONTROLLER, CONFIG):
    if not p.exists():
        raise SystemExit(f'未找到 {p.relative_to(ROOT)}，请把本脚本放到项目根目录后运行。')

stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
shutil.copy2(SCREEN, SCREEN.with_name(f'screen.py.before_dictation_hybrid_{stamp}.bak'))
shutil.copy2(CONTROLLER, CONTROLLER.with_name(f'controller.py.before_dictation_hybrid_{stamp}.bak'))
shutil.copy2(CONFIG, CONFIG.with_name(f'config.yaml.before_dictation_hybrid_{stamp}.bak'))

# ---------------- screen.py ----------------
screen = SCREEN.read_text(encoding='utf-8')
if 'import pyperclip' not in screen:
    screen = screen.replace('import pyautogui\n', 'import pyautogui\nimport pyperclip\n', 1)

# Remove older copies if the patch is rerun.
method_pattern = re.compile(
    r'^    def foreground_click_rel\(.*?\n(?=    def save_debug\()',
    re.MULTILINE | re.DOTALL,
)
screen = method_pattern.sub('', screen)

new_screen_methods = r'''    def _restore_user_state(
        self,
        old_pos,
        old_hwnd: int | None,
    ) -> None:
        """恢复用户鼠标位置和之前的前台窗口。"""
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
        delay: float = 0.12,
    ) -> None:
        """临时使用真实鼠标点击一次，然后立即恢复鼠标和焦点。

        微信小程序的音频按钮位于 WebView/Canvas 中，部分机器不接受
        WM_LBUTTONDOWN 后台消息，因此听写喇叭必须使用一次真实点击。
        """
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
        """真实点击输入框、粘贴文字、点击确定，再恢复用户状态。

        Win32 的 WM_CHAR 对微信 WebView 输入框不稳定，会出现日志显示已
        输入但页面仍提示“答案不能为空”。这里仅在听写提交时短暂使用
        前台输入，普通学习和选择题仍保持后台点击。
        """
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
            time.sleep(0.18)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
            try:
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            except Exception:  # noqa: BLE001
                pyautogui.write(text, interval=0.03)
            time.sleep(0.22)
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
anchor = '    def save_debug(self, image: Image.Image, folder: Path, name: str) -> Path:\n'
if anchor not in screen:
    raise SystemExit('screen.py 中未找到 save_debug 锚点，请把当前 screen.py 发来检查。')
screen = screen.replace(anchor, new_screen_methods + anchor, 1)
compile(screen, str(SCREEN), 'exec')
SCREEN.write_text(screen, encoding='utf-8')

# ---------------- controller.py ----------------
controller = CONTROLLER.read_text(encoding='utf-8')

# Remove previous submit_word helper if rerun.
submit_pattern = re.compile(
    r'^    def submit_word\(.*?\n(?=    def submit_dummy_dictation\()',
    re.MULTILINE | re.DOTALL,
)
controller = submit_pattern.sub('', controller)

submit_helper = r'''    def submit_word(self, items: list[OCRItem], word: str) -> None:
        """先真实点击输入框，再粘贴单词并真实点击“确定”。"""
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
            LOG.warning("OCR 未找到确定按钮，使用兜底坐标 dictation_confirm")

        LOG.info(
            "前台辅助提交听写：先点击输入框，填写 %s，再点击确定",
            word,
        )
        self.screen.foreground_fill_and_click_abs(
            input_x,
            input_y,
            word,
            confirm_x,
            confirm_y,
        )

'''
anchor = '    def submit_dummy_dictation(self, items: list[OCRItem], reason: str = "") -> None:\n'
if anchor not in controller:
    raise SystemExit('controller.py 中未找到 submit_dummy_dictation 锚点。')
controller = controller.replace(anchor, submit_helper + anchor, 1)

# Dummy fallback: fill + confirm atomically.
controller = controller.replace(
    '        self.type_word(word)\n        self.click_text(items, "确定", fallback="dictation_confirm")\n',
    '        self.submit_word(items, word)\n',
)

# Successful Whisper result: fill + confirm atomically.
controller = controller.replace(
    '                    self.type_word(word)\n                    self.click_text(items, "确定", fallback="dictation_confirm")\n                    return\n',
    '                    self.submit_word(items, word)\n                    return\n',
)
controller = controller.replace(
    '            self.type_word(word)\n            self.click_text(items, "确定", fallback="dictation_confirm")\n',
    '            self.submit_word(items, word)\n',
)

# Speaker replays must use a real click; the method restores the user cursor/focus.
controller = controller.replace(
    'lambda: self.screen.click_rel(*speaker, delay=0.05)',
    'lambda: self.screen.foreground_click_rel(*speaker, delay=0.15)',
)

# Reinforce feedback-page priority, preserving the choice feedback rule.
feedback_rule = '''        if "下一词" in text and (
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
# Remove existing canonical dictation feedback block and reinsert before test_dictation.
existing = re.compile(
    r'^        # 听写提交后.*?^            return "dictation_feedback"\n',
    re.MULTILINE | re.DOTALL,
)
controller = existing.sub('', controller)
legacy = re.compile(
    r'^        if "下一词" in text and "释义" not in text and "例句" not in text and "请跟读" not in text:\n'
    r'^            return "dictation_feedback"\n',
    re.MULTILINE,
)
controller = legacy.sub('', controller)
dict_anchor = '        if "请填写你听到的单词" in text:\n'
if dict_anchor not in controller:
    raise SystemExit('controller.py 中未找到 test_dictation 状态锚点。')
controller = controller.replace(dict_anchor, feedback_rule + dict_anchor, 1)

compile(controller, str(CONTROLLER), 'exec')
CONTROLLER.write_text(controller, encoding='utf-8')

# ---------------- config.yaml ----------------
with CONFIG.open('r', encoding='utf-8') as f:
    cfg = yaml.safe_load(f) or {}
cfg['dictation_mode'] = 'whisper'
cfg['dictation_max_attempts'] = max(2, int(cfg.get('dictation_max_attempts', 2)))
cfg['dictation_failure_strategy'] = 'dummy'
cfg.setdefault('dictation_fallback_word', 'zzzz')
# Current calibrated interface places the input line near this point; keep the
# user's value if already configured.
cfg.setdefault('fallback_points', {})
cfg['fallback_points'].setdefault('speaker', [0.50, 0.345])
cfg['fallback_points'].setdefault('dictation_input', [0.50, 0.445])
cfg['fallback_points'].setdefault('dictation_confirm', [0.50, 0.53])
with CONFIG.open('w', encoding='utf-8') as f:
    yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)

print('修复完成：')
print('1. 每次播放/重听喇叭改为短暂真实点击，随后恢复鼠标和原窗口。')
print('2. 填写时先真实点击输入框，再粘贴单词，最后点击确定。')
print('3. 显示答案和“下一词”后会直接进入下一题，不会再次点喇叭。')
print('普通学习、选择题仍使用 Win32 后台点击。')
print('现在运行：python main.py')
