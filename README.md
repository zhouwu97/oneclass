# 微信小程序背单词自动化（Windows 电脑版）

## 图形界面快速开始

进入 `wechat_word_bot_v2/wechat_word_bot`，双击 `启动.bat`，或安装依赖后运行 `python gui.py`。界面包含声音设备选择、试录、录音试听、实时日志和使用教程。详细步骤见 [项目使用说明](wechat_word_bot_v2/wechat_word_bot/README.md)。

听写现在优先减少每题等待：发音结束后自动停止录音；清晰且与词库精确匹配的首轮结果跳过重复解码；低置信度才复核或重听。日志会打印录音时长和识别耗时，方便确认瓶颈。实际速度取决于 CPU/GPU、模型与音频质量。

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

## 一键启动

双击 `wechat_word_bot_v2/wechat_word_bot/启动.bat`，即可打开图形设置与运行界面：
- **模型厂商快速切换**：预置阿里云百炼（通义千问）、硅基流动（SiliconFlow）、OpenAI、智谱 AI（GLM）、月之暗面（Kimi）、DeepSeek、Ollama 及自定义，自动填入 Base URL；
- **API 探测与动态拉取**：填写 API Key 后，点击「🔍 测试连接并拉取模型列表」，自动向服务商 `/models` 接口查询并填充最新可用模型，带视觉标记的模型自动置顶；
- **答题速度三档切换**：快 / 均衡 / 准；
- **听写引擎二选一**：本地 Whisper（离线 CPU）或服务端语音 API（GPU 加速）；
- **右侧实时监控**：显示运行与识别日志，支持一键启停。

首次运行会自动创建 `.venv` 并安装全部依赖（含听写音频组件）。纯命令行运行：`.\启动.bat cli`。

仍需先完成下方「校准」，并保持微信窗口位置、大小不变。

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
