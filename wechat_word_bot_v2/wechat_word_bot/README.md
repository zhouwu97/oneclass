# 微信小程序背单词自动化（Windows 电脑版）

这是一套“屏幕截图 + 中英文 OCR + 页面状态机 + 模拟点击”的工程。它不调用小程序内部接口，微信窗口保持前台即可运行。

## 已覆盖的实际流程

1. 首页进入“单词”，点击“开始背诵”。
2. 今日复习/今日新词的隐藏页：点击屏幕显示答案。
3. 答案页：记录“单词 → 词义”，点击“认识”。
4. 详情页：点击“下一词”。
5. 单词小节：点击“继续背单词”。
6. 学习结束弹窗：点击“确认”。
7. 出现“任务超时警告”时：点击弹窗中的绿色“再来一组”。
8. **再来一组之后直接按照“今日新词”继续，不再等待第二轮今日复习。**
9. 选择题：读取英文单词，从 `vocab.json` 找释义并匹配四个中文选项。
10. 听写题支持：
   - `reveal`：点击“我不会”，读取答案后继续；适合先调试流程，但会记为错题。
   - `whisper`：录制 Windows 扬声器回环音频，识别后填写正确单词。

## 你的显示环境

已针对你提供的环境增加 DPI 处理：

- 分辨率：2560 × 1440
- Windows 自定义缩放：115%
- 外接显示器：Mi Monitor

程序会在导入 PyAutoGUI/MSS 前启用 Windows Per-Monitor DPI Awareness，使截图坐标和点击坐标都使用物理像素。仍需运行一次 `calibrate.py`，并在运行过程中保持：

- 微信窗口位置、大小不变；
- Windows 缩放保持 115%；
- 不把窗口移动到 DPI 不同的显示器；
- 小程序完整可见且位于最前面。

## 安装

建议使用 Python 3.10 或 3.11。在工程目录打开 PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 校准

```powershell
python calibrate.py
```

依次把鼠标放到：

1. 小程序白色内容区域左上角；
2. 小程序内容区域右下角。

校准会生成 `calibration_preview.png`。预览图应只包含小程序内容，不要包含微信标题栏、桌面背景或窗口阴影。

## 运行

首次先使用：

```yaml
dictation_mode: reveal
unknown_choice_strategy: pause
```

运行：

```powershell
python main.py
```

## 收款与授权

当前工程已加入本地购买拦截，默认分为两档，并约定只走 `xiaoxin` 后端的 `易支付(epay)`：

- `3` 元：一次性购买，只开通当前版本。
- `8` 元：长期更新，后续版本继续可用。
- `6` 元：补差升级长期更新，给已购买一次性版用户补差使用。

根据你服务器里的实现，`xiaoxin` 还有 `vmq`、人工审核等分支，但这里已经收敛为只使用 `epay`。当前默认会跳到：

- `https://sylu.zhouwu.ccwu.cc/api/oneclass/pay/buy?tier=one_time`
- `https://sylu.zhouwu.ccwu.cc/api/oneclass/pay/buy?tier=lifetime_updates`
- `https://sylu.zhouwu.ccwu.cc/api/oneclass/pay/buy?tier=upgrade_updates`

购买页会自动带上本机生成的 `machine_id`，服务端会把 `档位 + 机器标识 + 联系方式` 记录下来，方便你后续手动开通授权。

常用命令：

```powershell
python main.py purchase
python main.py purchase --tier one_time
python main.py purchase --tier lifetime_updates
python main.py purchase --tier upgrade_updates
python main.py status
python main.py activate --tier one_time --owner 张三
```

紧急停止：把鼠标迅速移到整个屏幕左上角，或在控制台按 `Ctrl+C`。

## “再来一组”设置

`config.yaml` 中：

```yaml
auto_next_group: true
max_extra_groups: 0
```

含义：

- `auto_next_group: true`：自动点击“任务超时警告”里的绿色“再来一组”；如果脚本启动时弹窗已消失，也能点击页面底部橙色“再来一组”。
- `max_extra_groups: 0`：额外组数不设上限。
- 改成 `max_extra_groups: 1`：本次程序运行最多只开始一组额外任务。

弹窗页面中同时存在两个“再来一组”文字：一个是绿色弹窗按钮，一个是被遮住的橙色底部按钮。程序不依赖 OCR 随机选择，而是专门点击绿色按钮的相对位置。

## 开启听音填写

安装可选依赖：

```powershell
pip install -r requirements-audio.txt
python list_audio_devices.py
```

修改：

```yaml
dictation_mode: whisper
```

默认使用系统默认扬声器。识别不到时，把设备名称填入：

```yaml
audio_loopback_name: "你的扬声器或回环设备名称"
```

## 运行中生成的文件

- `vocab.json`：自动积累的单词词义库；
- `wordbot.log`：页面识别、点击、匹配和异常日志；
- `logs/unknown_*.png`：连续无法判断页面时保存的截图；
- `logs/last_dictation.wav`：最近一次听写录音。

## 还需要的页面样本

目前最影响测试阶段准确性的仍是：

- 选择题点击正确答案后的页面；
- 选择题点击错误答案后的页面；
- 听写输入正确后的页面；
- 最终“测验完成”页面。

学习阶段和“再来一组 → 直接今日新词”的流程已加入状态机。

## 后台点击模式（v3）

`config.yaml` 默认启用：

```yaml
input_backend: win32
win32_fallback_to_pyautogui: false
```

此模式通过 Win32 窗口消息向微信小程序发送点击和英文字符，不移动系统鼠标，适合把小程序固定放在第二显示器、同时在主显示器继续操作。

限制：OCR 仍是屏幕截图识别，所以小程序窗口必须保持完整可见，不能最小化，也不能被其他窗口覆盖。你可以在另一块显示器正常使用鼠标和键盘。

启动后日志应出现：

```text
输入模式：Win32 后台窗口消息（不会移动鼠标）
```

若小程序完全不响应后台消息，可临时改回：

```yaml
input_backend: pyautogui
```

听写输入也会优先使用窗口消息直接发送字符，不占用当前活动窗口。由于不同微信版本的渲染窗口可能有差异，首次请用 1—2 个单词观察输入是否成功。

## 重复复习词的“标记为太简单”流程（v4.1）

脚本现在严格按实际页面处理：

1. 在隐藏释义页先点击一次空白区域；
2. 页面显示释义、认识/模糊/不认识按钮，并出现“标记为太简单（无需复习）”；
3. 优先点击“标记为太简单”；
4. 小程序会直接进入下一个单词，因此脚本不会再点击“认识”，也不会进入详情页点击“下一词”；
5. 没有出现“标记为太简单”的新词，仍按“认识 → 下一词”的普通流程。

配置项：

```yaml
mark_too_easy: true
```
