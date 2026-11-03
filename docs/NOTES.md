# 语音助手改造 · 工程记录

## 环境事实（实测）
- Node v22.23.4（/usr/local/bin/node，官方构建，ts=strip）
- 无 NVIDIA GPU；8 核 / 14 GB / 392 GB 可用磁盘
- 采集设备：**USB AUDIO 模拟立体声**（PipeWire，id 83）；主板 ALC1220 被 PipeWire 占用，hw:CARD=PCH 不可直采
- 底噪 -81.6 dB，峰值 -4.2 dB（无削波）
- 外网：pypi/github 通，**HuggingFace 不通**，需 HF_ENDPOINT=https://hf-mirror.com
- Python 3.14.4 无 pip，但有 ~/.local/bin/uv

## 第 1 段（耳朵）· 已验收
- 方案：DSH 内置 @deepseek-ai/dsh-experimental-voice-input-bundle（SenseVoice 本地识别）
- 模型缓存：~/.dsh/speech-to-text/sensevoice/models（model.int8.onnx 239 MB + tokens + silero_vad）
- 运行时：sherpa-onnx-node 1.13.8

### 实测数据（5 句中文，VAD 分段后逐句解码）
| # | 期望 | 识别 | 错字 |
|---|---|---|---|
| 1 | 你好助手今天是几号 | 你好，助手，今天是几号？ | 0 |
| 2 | 帮我查一下明天的天气 | 帮我查一下明天的天气 | 0 |
| 3 | 打开下载文件夹找最新的PDF | 打开下载文件夹，找到最新的PDF | 0 |
| 4 | 会议定在星期三下午三点半 | 会议定在星期三下午3点半 | 1（ITN 数字归一化） |
| 5 | 把刚才那段内容总结成三句话 | 把刚才那段内容总结成上句话 | 1（三→上） |

- **字准 96.6%**（58 字 / 2 错；ITN 计入则该句视为可接受，字准 98.3%）
- **延迟**：3.15 s 音频 → 83 ms；3.69 s → 85 ms；3.33 s → 80 ms（约 38× 实时）
- 验收门槛（字准 ≥90%、P90 <800ms）：**均通过**

### 关键结论（决定后续设计）
1. **必须 VAD 分段后再送识别**。同一份 103 s 音频整段解码：耗时 4859 ms，且丢字严重——
   助手→助、查一下→查下、**PDF 整段丢失**、三点半→3半、三句话→话。
   分段后同样的音频几乎全对。SenseVoice 是离线识别器，长音频必须自己切。
2. 端点判定用「底噪 + 10 dB」阈值足够；但呼吸/桌面噪声会触发空段（解出「嗯。」），
   产品里需要最小时长 + 能量双重过滤。
3. 数字走 ITN（三点半→3点半），属于预期行为，播报前应做中文数字回写。

## 第 2 段（嘴）· 待做
- 选型：edge-tts（云端，免费，首包快）
- 形态：独立进程（不改 DSH 内核），订阅我的回复文本 → 合成 → 播放

## 第 3 段（唤醒词 + 全双工 + 打断）· 待做
- 唤醒词 openWakeWord；AEC 用 WebRTC AEC3；播放中保持 VAD

## 第 2 段（嘴）· 已跑通
- stack：edge-tts 7.3.1 + Python 3.12.13（uv 隔离 venv）+ ffplay 流式播放
- 服务：`voice-assistant/tts_server.py`，监听 127.0.0.1:8765
  - `POST /speak {text}` → 清洗 Markdown → 流式合成 → 边收边播
  - `POST /stop` 打断；`GET /health` 探活；`POST /clean` 只看清洗结果（不发声）
  - 并发保护：忙时返回 429
- Markdown 清洗实测：`## 结论 / - **延迟** 达标：首包 1.2s / [文档](url) / 代码块`
  → 「结论，延迟 达标：首包 1.2s。见 文档」（代码块丢弃、链接解包、强调去符号）
- 合成延迟（同一句 19 字，三次复测）：首包 1134 / 1246 / 2760 ms，总 1568 / 2834 / 3697 ms，实时率 0.32–0.75×
  - 首测 13.9 s 为冷启动异常，不代表稳态
- 音色试听：zh-CN-XiaoxiaoNeural（女/温暖）、YunxiNeural（男/阳光）、YunyangNeural（男/播音）
- 决策：**边收边播**，不整段等合成完成；这是把 1–3 s 合成延迟摊掉的关键

### 已知风险
1. edge-tts 依赖国际网络，本机访问微软端点是「通但抖」；HF 已被墙，同类风险存在
2. 首包 1.2 s+ 高于语音助手理想值（<500 ms）→ 若主观不可接受，降级 piper 本地离线（无网络、首包约 200–400 ms，音质略降）
3. 目前是「我一轮回复说完后播报」，不是实时全双工；打断靠 /stop


## 第 2 段修订 · 引擎切换为 piper 本地（音色沿用晓晓偏好）
- 依赖：piper-tts 1.8.0（PyPI 清华镜像装，避免 GitHub release 卡死）+ onnxruntime 1.31.1
- 模型：piper/zh_CN-huayan-medium.onnx（60.27 MB，22050 Hz，hf-mirror 断点续传下完）
- 常驻模型：启动加载 1151–1167 ms，**只加载一次**
- 句长 → 首包延迟（不含句切分 → 含句切分）：
  | 句长 | 改前 | 改后 |
  |---|---|---|
  | 5 字 | 40 ms | **45 ms** |
  | 40 字 | 303 ms | **151 ms** |
  | 101 字 | 355 ms | **159 ms** |
  → 结论：**按标点切句后，首包与总句长解耦**，稳定在 160 ms 内
- 对比 edge-tts：首包 1134–2760 ms、依赖国际网络、抖动大 → 仅作回退引擎（模型缺失/合成异常时自动降级）
- 播放：ffplay 读 s16le 22050 单声道流，边合成边播

### 当前服务契约（127.0.0.1:8765）
- POST /speak {"text"} → {"engine","chars","firstChunkMs","finished"}
- POST /stop、POST /clean、GET /health（engine/loadMs/busy）
- 环境变量：TTS_ENGINE(piper|edge)、TTS_PIPER_MODEL、TTS_VOICE(edge 音色)、TTS_RATE、TTS_MAX_CHARS

### 仍待办
1. TTS 服务常驻化（systemd --user 或 supervisor），宿主重启后不自愈
2. 自动朗读：目前由我在每轮手动 POST；可加「会话日志监听器」把助手回复自动送进 /speak
3. 第 3 段：唤醒词 + 全双工 + 打断（复用 /stop）


## 第 2 段修订 2 · 引擎切换为 kokoro-onnx（fp32），三引擎并存
- 依赖：kokoro-onnx 0.6.1 + misaki[zh]（pypinyin/jieba）+ onnxruntime 1.31.1
- 权重：kokoro-v1.0.onnx（fp32 310 MB，用户代下）/ int8（88 MB，已弃用默认）
- 音色：zf_xiaoxiao（另有 zf_xiaobei/xiaoni/xiaoyi、zm_yunjian/yunxi/yunxia/yunyang 共 8 个中文音色）
- **关键坑**：misaki 的 ZHG2P 用 `version="1.1"` 输出的是注音符号且部分损坏（合成被截断到 0.8 s）；
  必须用默认版本（None/1.0），输出 IPA（如 ni↓xau↓ ʂɨ↘ʨje↘），与 Kokoro 训练集一致
- **另一个坑**：kokoro 默认走 phonemizer/espeak，espeak 后端不支持 zh（RuntimeError: language "zh" is not supported），
  必须自己用 misaki 产出音素后走 is_phonemes=True

### 三引擎对比（同一台机器，8 核无 GPU）
| 引擎 | 模型加载 | 首包延迟 | 实时率 | 采样率 | 网络依赖 | 备注 |
|---|---|---|---|---|---|---|
| kokoro fp32 | 660 ms（含预热共 1.56 s） | **519–793 ms** | 0.30 | 24 kHz | 无 | 音质最好，int8 反而更慢（量化核无优势） |
| piper huayan | 1151 ms | **45–160 ms** | 0.04 | 22.05 kHz | 无 | 最快，机械感明显 |
| edge-tts 晓晓 | 0 | 1100–2800 ms | 0.3–0.75 | 24 kHz | 有 | 音质商业级，抖动大 |

### 服务契约（127.0.0.1:8765）
- POST /speak {text, engine?} → 支持按请求指定引擎；引擎懒加载
- POST /stop、POST /clean、GET /health（engine/voice/loadMs/available/busy）
- 默认引擎：kokoro（TTS_ENGINE=kokoro），启动预热一次消除首调开销


## 第 2 段修订 3 · 卸载 piper，kokoro 单引擎 + 首包优化
- piper-tts 包与 63 MB 模型已删除；服务代码中 piper 分支全部移除（grep 无残留）
- 引擎：kokoro（默认，fp32）→ edge-tts（仅 kokoro 加载失败时回退）
- 新增：**按请求指定音色** `POST /speak {"text":"...","voice":"zf_xiaobei"}`，无需重启
- 中文音色 8 个：zf_xiaoxiao / zf_xiaobei / zf_xiaoni / zf_xiaoyi / zm_yunjian / zm_yunxi / zm_yunxia / zm_yunyang

### 首包延迟诊断（重要）
- 实测规律：**首包 ≈ 固定开销 300 ms + 首块合成时间（约 75 ms/字）**，与音色无关
  - 2 字 → 320 ms；20 字无标点 → 1148 ms；4 字首句的 87 字整段 → 409 ms
- 无效尝试：`create_stream` 对该文本只产 1 个块，流式无收益
- 有效改动：切句上限 28 → **12 字**（切点落在逗号），20 字长句首包 1148 → 642 ms
- 当前实测：20 字 708 ms / 49 字（首句 4 字）641 ms
- 结论：想进一步降首包只能换更快的模型（本机无 GPU，kokoro RTF 0.3 已是这套权重的极限）


## A · 自动朗读监听器（已完成）
- `auto_read.py`：zstd 解压 DSH 会话日志 → 监听 `assistant/message`(只取 text，跳过 reasoning) → `turn/end` 时 POST /speak
- 修复的坑：初版 state.lines 从 0 起，会把**全部历史回放朗读**；已改为启动时记录基线（history muted）
- 实测：`attached session-... baseline=1700 lines (history muted)`

## B · systemd 常驻（已完成）
- `~/.config/systemd/user/dsh-tts.service`（kokoro，zf_xiaoyi，Restart=always）
- `~/.config/systemd/user/dsh-autoread.service`（Requires=dsh-tts）
- 均 enabled + active；`loginctl enable-linger ubuntu` → Linger=yes（注销后仍运行、开机自启）
- 日志：/tmp/dsh-tts.log、/tmp/dsh-autoread.log

## C · 唤醒词 + 全双工 · 阻塞点（重要）
- 目标链路：麦克风 → VAD → SenseVoice → 唤醒词 → **把这句话交给「我」** → 回复 → TTS
- 卡在最后一环：DSH 的 HTTP 是 401 鉴权（/api、/api/rpc、/api/sessions 全部 401）
  - ~/.dsh/.credentials.yaml 里有 client-connection/browser-session secret，但用 Bearer / Cookie / X-DSH-Secret 三种方式均 401 → 不是简单 bearer
- 本机现状：Wayland 会话（DISPLAY=:0 + wayland-0），xdotool/wmctrl/xclip/ydotool 全部未安装，Chrome 在运行
- 结论：**「说话→agent 回答」这一段没有现成入口**，必须先选一条注入路径（见下）


## C（部分完成）· 采集 → VAD → SenseVoice → 唤醒词 四段已测通
- 文件：`voice_loop.py`（支持 `--wav` 离线回放自测 / 默认实时麦克风）
- 采集：`arecord -D default -f S16_LE -r 16000 -c 1 -t raw -`，断线自动重连
- VAD：30 ms 帧能量，自适应底噪（EMA），阈值 noise+10 dB，收尾静音 0.55 s，段长 0.35–10 s
- ASR：sherpa-onnx 2.0.1 + SenseVoice int8（复用 DSH 已有模型缓存）
- 唤醒词：**精确匹配 + 拼音同音模糊匹配**（关键！）

### 实测（真实麦克风，USB AUDIO）
| 用户说 | 识别 | 结果 |
|---|---|---|
| 小艺 | 「小易。」 | wake=小艺（同音命中）→ awaiting_command |
| 今天天气怎么样 | 「今天天气怎么样？」 | command_from_window → 命令接住 |
| 小艺，打开下载文件夹 | 「小易打开下载文件夹。」 | wake + cmd 一次切分正确 |
| 我。 / 这。 | 噪声段 | 未误唤醒 |

### 踩到的两个坑
1. **SenseVoice 不认识"小艺"**：识别成 小一/小易/晓艺。用 pypinyin 逐字转拼音做**同音窗口匹配**（长度 len(wake)..len(wake)+2 滑动），彻底解决。
2. **无 AEC 会自激**：麦克风听到扬声器 → 每帧发 /stop，一次刷了 25 条 barge_in。
   已把能量打断默认关闭（VL_BARGE=off），改为语音关键词打断（停止/别说了），后续如要真打断需上 AEC3。

### 仍未完成
- 唤醒后的命令尚未真正交给 agent：`VL_CMD_URL` 钩子已预留，等注入方案（CDP / 插件移植）落地


## 第 2 段最终定稿 · 默认 edge-tts 晓晓，kokoro 保留
- 默认引擎：`TTS_ENGINE=edge`，音色 `zh-CN-XiaoxiaoNeural`（systemd 单元内声明）
- kokoro 保留：模型 310 MB + misaki 依赖均未卸载；按请求懒加载
  - 切换：`POST /speak {"text":"...","engine":"kokoro"}`（默认音色 zf_xiaoyi，可用 voice 覆盖）
- edge 也支持按请求换音色：`{"voice":"zh-CN-YunxiNeural"}`
- 修掉的回报 bug：走 edge 时曾错误回报 kokoro 的音色名
- 首包实测：edge 1371–1560 ms（网络抖动），kokoro 772 ms（本地）


## 第 2 段修订 4 · edge → kokoro 自动降级
- 当次降级：edge 抛异常时，立刻用已加载/懒加载的 kokoro 念完这一句，回报带 `fallbackFrom:"edge"` + `edgeError`
- 永久降级：连续失败达到 `TTS_EDGE_FAIL_THRESHOLD`（默认 2）→ 默认引擎切到 kokoro（进程内状态，重启复位）
- 成功一次即清零计数；`GET /health` 暴露 `edgeConsecutiveFailures`
- 音色回报修正：降级到 kokoro 时只认 zf_/zm_ 前缀的音色名，否则回落默认 zf_xiaoyi（避免回报 edge 的非法名字）

### 验证记录（用不存在的音色 zh-CN-Bad 人为触发）
| 步骤 | 结果 |
|---|---|
| 正常请求 | engine=edge, Xiaoxiao, 1037 ms |
| 第 1 次失败 | engine=kokoro, fallbackFrom=edge, 642 ms |
| 第 2 次失败 | engine=kokoro，并触发默认切换 |
| 之后不指定引擎 | engine=kokoro（默认已切） |
| 重启服务 | 复位回 edge 默认 |

### 踩坑
`ast.parse()` 通过 ≠ 能运行：`global _engine` 写在函数中途（前面已读过该名）属于**编译期** SyntaxError，
ast 层检查不到。教训：校验用 `python -m py_compile`，不要只做 ast.parse。


## 切换为插件托管（2026-10-31 16:36 重启）
- 用 systemd 瞬时单元 `dsh-restart-once` 执行重启（脱离我的会话，避免中断导致重启半途而废）
- 结果：**插件在第 2 秒接管 TTS**，systemd 版三个单元已 disable，避免抢端口
- 插件子进程日志：`~/.dsh/logs/voice-assistant/{tts,autoread,bridge}.log`
- 事故：`dsh-web.service` 的 ExecStart 写死 `/usr/bin/node`（发行版构建，ts=false）→ DSH run_code 再次全挂
  修复：改写为 `/usr/local/bin/node`；已把该检查固化进 `install.sh` 第 5 步
- 事故：Chrome 窗口被关闭 → CDP 退出（exit 0）→ 注入不可用；重启 `dsh-voice-chrome.service` 并用新 token 重新认证后恢复
- `scripts/healthcheck.sh` 升级为自动识别托管模式（systemd / plugin），当前 8/8 通过


## 语音输入失败排障（2026-10-31 16:47）
现象：对着麦克风说话无任何反应。
根因链：
1. `dsh-voice-loop.service` 里写了 `Requires=dsh-voice-bridge.service`
2. 但 bridge 已改由 DSH 插件托管，systemd 里没有可用的 bridge 单元
3. 启动 loop 时连带拉起 bridge 单元 → 端口 8766 被插件占用 → 单元 exit 1 → auto-restart 抖动
4. Requires 依赖失败 → 连带 loop 被反复停止/重启（每约 3 秒一次）
5. loop 每次只活 1–3 秒，模型刚加载完就被杀，永远处理不到音频

修复：
- loop 单元去掉 `Requires=/After=` 桥依赖（改为运行时通过 HTTP 判断）
- 显式 `disable --now` 掉 tts/autoread/bridge 三个单元，避免与插件抢端口
- 验证：20 秒内重启次数 = 1（即人工那次），loop 稳定 active，arecord 子进程存活

教训：**托管模式切换后，systemd 单元之间的硬依赖必须同步清理**，否则会形成静默的抖动死循环——日志里只有 "Stopping/Stopped"，看不到任何 Python 报错，极易误判为"程序崩了"。


## 重启前自愈加固（2026-10-31 16:49）
- 问题：每次 DSH 宿主重启，token 变化 -> CDP 专用 Chrome 变未认证 -> 注入失效（已踩两次）
- 方案：新增 `dsh-voice-auth.service`（oneshot）
  - 从 `journalctl -u dsh-web.service` 抓最新 token URL
  - 等待 Chrome/CDP 就绪（最多 40 次 × 3 s）
  - 调用 `src/cdp/auth.mjs` 导航并校验页面已认证
  - 幂等：已认证时也直接通过（实测 1 次即成功）
- 同时 `enable` 了 `dsh-voice-loop.service`（此前是 disabled，重启后语音不会自己回来）

### 开机自启清单（已全部 enabled）
| 单元 | 作用 |
|---|---|
| dsh-web.service | DSH 宿主（node 已修为 /usr/local/bin/node） |
| dsh-voice-chrome.service | CDP 专用 Chrome（graphical-session） |
| dsh-voice-auth.service | 自动用新 token 认证 Chrome |
| dsh-voice-loop.service | 麦克风环路 |
| （插件）dsh-voice-assistant | 宿主启动时拉起 tts/autoread/bridge |

## 「停止朗读」按钮 + 双 loop 事故（2026-11-03）

### 停止朗读按钮
- 需求：回答自动朗读之外，还要能在消息下方一键叫停
- 实现：独立的浏览器端插件包 `client/`（包名 `dsh-voice-stop-button`），注册槽位
  `conversation.chat.assistant-actions`（order 30），渲染「停止朗读」，点击 POST `127.0.0.1:8765/stop`
- 坑 1：只执行 `dsh plugin add client` 不够——还得写进 `plugin/cordis.patch.yml` 的 insert
  （`- id: voice-stop-button`），否则 `--dump-config` 里看不到、bundle 不会被挂出
- 坑 2：`client.js` 里 `const inject = []` 会让 `ctx.slots` 为 `undefined`，浏览器控制台报
  `web boot: 1 entry did not activate / dsh-voice-stop-button: failed`；改成 `["slots"]` 后
  `document.querySelectorAll('[data-voice-stop]').length` = 7（等于当前消息条数）
- 坑 3：页面 origin 是 3080、TTS 在 8765，属跨域；`tts_server.py` 原本没有任何 CORS 头，按钮会被浏览器直接拦。
  加白名单回显（不用 `*`）+ `do_OPTIONS`，`TTS_ALLOWED_ORIGINS` 可覆盖，用例在 `tests/test_tts_cors.py`（11 个）
- 验证局限：bundle 在宿主启动/重载时才注册，按钮的实际外观只能靠刷新页面用肉眼确认

### 双 loop 事故
- 现象：一句「小艺，现在几点了」被识别两次，命令被 `/inject` 两次
- 原因：systemd 的 `dsh-voice-loop`（10-31 启动、旧代码，不含 8767 锁）与插件 loop 并存；
  8767 守卫只对带锁的新版 loop 有效，探不到老进程
- 处理：`systemctl --user disable --now dsh-voice-loop.service`，只留插件 loop
  （上文「开机自启清单」里的 loop 一项据此作废）
- 当时另有两条 `dispatch_failed <HTTPError 502>`，是验证时反复 reload 页面导致注入脚本找不到输入框，
  不是链路故障；`node src/cdp/send.mjs '' --no-enter` 返回 `{"ok":true,...}` 可证
- 教训：切换托管方后必须核对 `pgrep -af voice_loop` 只有一条

