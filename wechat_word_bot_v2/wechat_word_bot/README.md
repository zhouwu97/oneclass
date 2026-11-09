# 微信小程序背单词自动化（Windows 电脑版）

## 推荐：图形界面与快速听写

1. 在本目录双击 `启动.bat`，它会创建虚拟环境并安装依赖；已有环境可直接运行 `python gui.py`。
2. 在界面第一步「扫描窗口」中选择 OneClass，点击「预览并确认定位」。核对预览中小程序完整可见，点击「确认，使用这个窗口」，启动按钮才会开放。程序跟随窗口移动；窗口大小改变、关闭或最小化会停止，需重新定位。听写按钮位置不准时可再运行 `python calibrate_dictation_points.py`；命令行模式仍使用 `python calibrate.py`。
3. 选择 `whisper` 答题方式、本地识别引擎和模型。首次使用模型可能联网下载；本地模型可放在 `build_local_models/<模型名>`、`models/<模型名>`，或通过 `whisper_model_path` 指定。
4. 点击「刷新设备」，选择微信实际使用的扬声器/耳机输出。此设置只改变监听目标，不切换 Windows 输出。点击「试录 3 秒」后手动播放小程序发音，再「试听最近录音」确认声音完整。
5. 点击启动。小程序必须保持完整可见。设置修改在下次启动生效；点击停止后会等待当前录音、模型推理或请求结束，随后退出。

### 模型怎么选

| 模型 | 优点 | 取舍与适用场景 |
| --- | --- | --- |
| tiny.en（39M） | 最轻，CPU 推理通常最快 | 适合声音链路检查和低配置电脑；口音、生词和噪声下更易出错 |
| base.en（74M） | 轻量，兼顾速度与基础识别 | CPU 提速优先可先试它，再用几题与 small 比较 |
| small.en（244M） | 清晰英语单词的速度与准确性较均衡 | 日常听写推荐起点，本项目已带本地模型 |
| distil-large-v3（约 756M） | 相对完整 large 减少计算，保留较强英语能力 | 更适合 GPU，仍大于 small/base；CPU 上不保证更快 |
| medium.en（769M） | small 不够可靠时可尝试更强的英语识别 | 内存与推理需求更高，适合准确性优先或有 GPU |
| large-v3（约 1.55B） | 多语言和复杂音频的通用能力 | 下载、加载与计算成本最高，单词听写提速不优先选 |

模型下拉框旁会实时显示优点和取舍；接口模式会提示本机计算、网络、费用和音频传输的差异。不同硬件的速度不能只凭模型名称判断。模型未缓存时首次使用需要下载；下载失败请检查网络与代理配置。

### 为什么听写更快

- 录音时长改为上限：至少观察 1 秒，检测到发音结束并连续静音 0.32 秒就结束，避免启动短音过早结束；不再每题固定录满 3.8 秒。
- 首轮高置信度且精确命中词库时只解码一次；低置信度才启用复核。
- 两轮解码得到相同且置信度足够的新词时直接提交；低置信度的一致结果仍会重听。
- 单词解码默认限制为 16 个新 token，防止重复生成长句拖慢每题识别。
- 均衡档首轮使用 1 候选；不再默认每题执行 8 候选和 10 候选两次解码。
- 喇叭点击、输入框聚焦、粘贴和提交保留较短等待，页面切换使用独立的快速运行档位。

默认启用 `runtime_profile: fast`、`speed_preset: balanced` 和 `audio_auto_stop: true`。界面切换答题速度时清除旧的录音时长、候选数、重听次数等覆盖值，使档位生效；命令行配置中显式设置仍优先。

```yaml
speed_preset: balanced       # fast / balanced / accurate
runtime_profile: fast       # 页面与 OCR 的节奏，与听写档位分别控制
audio_auto_stop: true
audio_silence_seconds: 0.32  # 单词中间停顿较长时可加大
audio_speech_rms: 0.001     # 语音活动门限；音量较小时可适当降低
audio_pre_click_delay: 0.06
whisper_verify_pass: true   # 仅在首轮结果不够可靠时复核
audio_min_record_seconds: 1.0
whisper_fast_logprob: -0.8
whisper_max_new_tokens: 16
```

### 排查声音与速度

日志分别显示录音时长与「语音识别耗时」。录音一直达到上限：确认监听的是微信正在使用的输出设备，关闭其他音频，检查音量。识别阶段耗时较长：优先用 `small.en` / `base.en`、均衡或快档；可用 CUDA 时 `whisper_device: auto` 会尝试 GPU。GPU 加载失败会回退 CPU。具体耗时请以本机日志为准。

发音被截断时增加静音尾长或选择准档；播放不响应时勾选「前台点击喇叭」并检查校准。`reveal` 会点击「我不会」，`dummy` 会提交占位词，都会影响答题结果。

试录不执行自动点击，试听使用 Windows 默认输出；运行机器人时禁止试录与试听，避免把回放录进下一题。无声录音会在识别前报错，避免将静音放大后误识别。

### 日志与云端

日志页支持按级别着色、清空显示与关闭自动滚动；性能明细降为 DEBUG。`wordbot.log` 按约 5 MB 轮转，最多保留 3 份备份。界面更新通过队列执行，后台任务不会直接操作 Tk 控件。

听写 API 和阅读 API 都是可选项。拉取模型列表成功不代表服务支持音频或图片；语音引擎要求兼容 `/audio/transcriptions`，阅读题要求支持图片输入。云端耗时取决于服务商与网络。环境变量 `ONECLASS_API_KEY` 可提供密钥；界面不会将来自该环境变量的同一密钥写入配置。阅读题自动提交默认关闭。

运行验证：`python -m unittest discover -s tests -v`。

Windows 音频硬件验证（会实际打开默认输出的回环设备）：先设置 `$env:ONECLASS_AUDIO_SMOKE='1'`，再运行测试。项目对 SoundCard 0.4.6 的 Windows 属性内存分配做了兼容处理，并为每个录音线程初始化 COM；检测到已修正的结构尺寸时不替换实现。

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

双击工程目录下的 `启动.bat`，即可打开图形设置与运行界面：
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

## 收款与授权

当前工程已加入本地购买拦截，默认分为两档，并约定只走 `xiaoxin` 后端的 `易支付(epay)`：

- `3` 元：一次性购买，只开通当前版本。
- `8` 元：长期更新，后续版本继续可用。
- `6` 元：补差升级长期更新，给已购买一次性版用户补差使用。

根据你服务器里的实现，`xiaoxin` 还有 `vmq`、人工审核等分支，但这里已经收敛为只使用 `epay`。当前默认会跳到：

- `https://sylu.zhouwu.ccwu.cc/api/oneclass/pay/buy?tier=one_time`
- `https://sylu.zhouwu.ccwu.cc/api/oneclass/pay/buy?tier=lifetime_updates`
- `https://sylu.zhouwu.ccwu.cc/api/oneclass/pay/buy?tier=upgrade_updates`

客户端会先向后端创建订单并带上本机生成的 `machine_id`，再打开支付页。付款完成后，购买窗口会自动轮询订单状态，并把 `档位 + 机器标识 + 订单号` 写入本地授权；授权只对当前机器标识有效。

支付页通过易支付 MAPI 获取支付宝或微信的原生付款码。扫码后直接进入支付确认，不再先打开 `submit.php` 第三方网页进行二次跳转。

常用命令：

```powershell
python main.py purchase
python main.py purchase --tier one_time
python main.py purchase --tier lifetime_updates
python main.py purchase --tier upgrade_updates
python main.py status
python main.py sync-license
python main.py activate --tier one_time --owner 张三
```

如果付款后误关了购买窗口，再运行 `python main.py` 或 `python main.py sync-license`，程序会检查上一次创建的待支付订单；订单已付款就会补写本地授权。

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
