# DSH 语音助手技术改造文档

> 版本 1.0 · 2026-10-31 · 环境：Ubuntu（Wayland）/ 8 核 CPU / 16 GB RAM / 无 GPU
> 目标：为 DeepSeek Harness（DSH）加上**离线语音输入 + 唤醒词 + 语音播报 + 语音指令注入**能力，
> 不改动 DSH 内核源码。

---

## 1. 摘要

本方案把 DSH 从"只能键盘输入、只会显示文字"改造成"喊一声就能开工、回答能被听见"的语音助手。
关键设计原则是**一切通过官方扩展点与外部进程完成，不 fork、不改 DSH 源码**：

- **听**：复用 DSH 自带的 SenseVoice 本地识别能力（模型缓存已存在）
- **唤醒**：自建语音环路，用**拼音同音匹配**解决"专有名词识别不准"
- **说**：独立 TTS 进程，默认 edge-tts（晓晓），失败自动降级 kokoro（本地）
- **注入**：由于 DSH 0.2.x 无对外 HTTP API，改用 **Chrome DevTools Protocol** 把识别文本键入输入框并回车

实测端到端：**从说完话到文字出现在输入框约 1.25 秒**。

---

## 2. 系统架构

```
         ┌──────────────────────────── 语音输入链路 ────────────────────────────┐
         │                                                                      │
  🎤 USB 麦克风                                                               │
         │ arecord -D default -f S16_LE -r 16000 -c 1 -t raw                   │
         ▼                                                                      │
  src/voice_loop.py ── 能量 VAD（30ms 帧 / 自适应底噪 / 0.55s 收尾静音）            │
         │                                                                      │
         ▼                                                                      │
  SenseVoice int8（sherpa-onnx 2.0.1，本地）── 3.3s 音频 ≈ 141ms               │
         │                                                                      │
         ▼                                                                      │
  唤醒词匹配（精确 → 拼音同音，"小易/小一/晓艺" → 小艺）                        │
         │ VL_CMD_URL                                                                
         ▼                                                                      │
  src/voice_bridge.py :8766  ──  HTTP /inject                                     │
         │ execFile                                                                 
         ▼                                                                      │
  src/cdp/send.mjs ── Chrome DevTools Protocol :9222                              │
         │ Input.insertText + Input.dispatchKeyEvent(Enter)                     │
         ▼                                                                      │
  ┌──────────────────┐                                                        │
  │  DSH Web GUI     │  http://127.0.0.1:3080                                 │
  │  （Chrome 窗口）  │                                                        │
  └────────┬─────────┘                                                        │
           │ 会话日志 session.v4.jsonl.zstd（只读旁路）                          │
           ▼                                                                   │
      src/auto_read.py ── 监听 assistant/message + turn/end                        │
           │                                                                    │
           ▼                                                                    │
      src/tts_server.py :8765  ── edge-tts(晓晓) / kokoro(本地兜底)                 │
           │ 流式合成 + 按句切分                                                 │
           ▼                                                                    │
  🔊 扬声器 ◄── ffplay                                                        
         └──────────────────────────────────────────────────────────────────────┘
```

**数据流要点**

1. 语音输入链路与 DSH 完全解耦：环路只依赖一个 HTTP 端点（`VL_CMD_URL`）
2. 播报链路是**只读旁路**：解析会话日志，不影响 DSH 运行
3. 注入链路走浏览器协议，不碰 DSH 内部 RPC

---

## 3. 运行环境基线

| 项 | 值 | 备注 |
|---|---|---|
| OS | Ubuntu，**Wayland** 会话（DISPLAY=:0 / wayland-0） | X11 注入工具不可用 |
| CPU / 内存 | 8 核 / 16 GB（可用 ~8.6 GB） | 无 GPU |
| 声卡 | USB AUDIO（PipeWire，id 83）为当前录音源 | 主板 ALC1220 被 PipeWire 占用，`hw:CARD=PCH` 不可直采 |
| 麦克风电平 | 底噪 -81.6 dB / 讲话峰值 -16 ~ -24 dB | 无削波 |
| Node | v22.23.4（`/usr/local/bin/node`，官方构建，`process.features.typescript=strip`） | 系统自带 `/usr/bin/node` 缺 Amaro，需软链覆盖 |
| Python | 3.12.13（uv 托管虚拟环境 `.venv`） | 系统为 3.14 且无 pip |
| Chrome | 155.0.8059.84 | 独立 profile `~/.dsh-voice-chrome` |
| 外网 | pypi/github 可达；**huggingface 直连不通**；hf-mirror 抖动；GitHub 直连约 15 KB/s，`ghfast.top` 代理约 200 KB/s ~ 3.6 MB/s | 模型下载需镜像/代理 |

---

## 4. 组件清单

| 组件 | 文件 | 行数 | 形态 | 职责 |
|---|---|---|---|---|
| TTS 服务 | `src/tts_server.py` | 307 | systemd `dsh-tts.service` | Markdown 清洗 → 分句 → 合成 → 流式播放 |
| 自动朗读 | `src/auto_read.py` | 99 | systemd `dsh-autoread.service` | 监听会话日志，回合结束时朗读助手回复 |
| 语音环路 | `src/voice_loop.py` | 245 | systemd `dsh-voice-loop.service` | 采集 → VAD → ASR → 唤醒词 → 派发 |
| 注入桥 | `src/voice_bridge.py` | 72 | systemd `dsh-voice-bridge.service` | HTTP → 调用 CDP 脚本 |
| CDP 键入 | `src/cdp/send.mjs` | ~60 | 脚本 | 真实键入文本并回车 |
| CDP 求值 | `src/cdp/eval.mjs` | ~35 | 脚本 | 调试用：在页面执行 JS |
| 过程记录 | `NOTES.md` | — | 文档 | 每阶段实测数据与踩坑 |
| 模型目录 | `kokoro/` | 426 MB | 数据 | kokoro 权重 + 音色包 |

---

## 5. 端口与接口契约

| 端口 | 服务 | 绑定 | 说明 |
|---|---|---|---|
| 3080 | DSH Web | 127.0.0.1 | 需 token URL 认证 |
| 8765 | TTS | 127.0.0.1 | 本方案核心 |
| 8766 | 注入桥 | 127.0.0.1 | 语音 → GUI |
| 9222 | Chrome CDP | 127.0.0.1 | 独立 Chrome 实例 |

### 5.1 TTS 服务 `POST /speak`

```jsonc
// 请求
{ "text": "要朗读的文本",           // 必填，会自动清洗 Markdown
  "engine": "edge" | "kokoro",      // 可选，默认 edge
  "voice": "zh-CN-YunxiNeural"      // 可选；kokoro 只认 zf_/zm_ 前缀
}
// 响应
{ "ok": true, "engine": "edge", "voice": "zh-CN-XiaoxiaoNeural",
  "chars": 30, "finished": true, "firstChunkMs": 1037,
  "fallbackFrom": "edge",           // 仅在降级时出现
  "edgeError": "ValueError(...)"    // 仅在降级时出现
}
```

其它端点：`POST /stop`（打断）、`POST /clean`（只看清洗结果）、`GET /health`。

### 5.2 注入桥 `POST /inject`

```jsonc
// 请求
{ "text": "现在几点了" }
// 响应
{ "ok": true, "composer": { "tag": "DIV", "editable": true, "placeholder": "发消息或创建任务…" },
  "typedTail": "现在几点了", "sent": true, "exitCode": 0 }
```

错误码：`2`=页面未认证，`3`=找不到输入框，`1`=无 DSH 页面。

### 5.3 语音环路事件（stdout JSONL）

| kind | 含义 |
|---|---|
| `ready` | 环路就绪，打印唤醒词与模型 |
| `heard` | 一个语音段转写完成（含 `dur` / `peak` / `asrMs` / `text`） |
| `wake` | 唤醒词命中，附带切出的命令 |
| `awaiting_command` | 只喊了唤醒词，进入 5 秒等指令窗口 |
| `command_from_window` | 窗口期内接住命令 |
| `dispatched` | 命令已投递给注入桥 |
| `barge_in` | 播报中检测到人声（默认关闭） |

---

## 6. 模型与依赖

### 6.1 模型

| 模型 | 路径 | 大小 | 用途 |
|---|---|---|---|
| SenseVoice int8 | `~/.dsh/speech-to-text/sensevoice/models/sensevoice-onnx/` | 239 MB + tokens | 中文识别（复用 DSH 缓存） |
| Silero VAD | 同上 `silero/silero_vad.onnx` | 1.8 MB | 暂未启用（当前用能量 VAD） |
| kokoro fp32 | `kokoro/kokoro-v1.0.onnx` | 310 MB | 本地 TTS 主力 |
| kokoro int8 | `kokoro/kokoro-v1.0.int8.onnx` | 88 MB | 备选（实测比 fp32 慢，未采用） |
| kokoro 音色包 | `kokoro/voices-v1.0.bin` | 27 MB | 8 个中文音色 |

### 6.2 Python 依赖（`.venv`）

```
edge-tts 7.3.1 · kokoro-onnx 0.6.1 · misaki 0.9.4 · onnxruntime 1.31.1
sherpa-onnx 2.0.1 · sherpa-onnx-core 2.0.1 · phonemizer 3.4.0 · numpy 2.5.4
```

> piper-tts 曾安装并跑通（首包 45–160 ms），后按要求卸载，模型 63 MB 已删除。

---

## 7. 关键设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 唤醒词实现 | 先 ASR 再**拼音同音匹配** | SenseVoice 不认识"小艺"，会输出小一/小易/晓艺；拼音匹配零成本解决 |
| VAD | 能量阈值 + 自适应底噪 | 依赖少、可解释；Silero 留作升级项 |
| TTS 引擎 | edge-tts 优先，kokoro 兜底 | 云端音质最好；失败自动降级保证"永远不会哑" |
| TTS 分句 | 12 字上限按标点切 | 首包与句长解耦（20 字句首包 1148 → 642 ms） |
| 注入方式 | CDP（浏览器协议） | DSH 0.2.x 无外部 HTTP API；CDP 稳定、无需改内核 |
| 播报触发 | 旁路监听会话日志 | 只读，不影响 DSH；`turn/end` 保证不漏不重 |
| 打断策略 | 语音关键词（"停止/别说了"） | 无 AEC 时能量打断会自激 |

---

## 8. 实测验收数据

### 8.1 语音识别（5 句中文，VAD 分段后逐句解码）

| 期望 | 识别 | 错字 |
|---|---|---|
| 你好助手，今天是几号 | 你好，助手，今天是几号？ | 0 |
| 帮我查一下明天的天气 | 帮我查一下明天的天气 | 0 |
| 打开下载文件夹，找最新的 PDF | 打开下载文件夹，找到最新的 PDF | 0 |
| 会议定在星期三下午三点半 | 会议定在星期三下午 3 点半 | 1（ITN） |
| 把刚才那段内容总结成三句话 | 把刚才那段内容总结成上句话 | 1 |

**字准 96.6%**（58 字 / 2 错）；识别耗时 3.1–3.7 s 音频 → **141–151 ms**。

> 反例：同一份 103 s 音频**整段**解码耗时 4859 ms 且丢字严重（PDF 全丢）——**必须 VAD 分段**。

### 8.2 唤醒词（真实麦克风）

| 用户说 | 识别 | 结果 |
|---|---|---|
| 小艺 | 小易 | ✅ 拼音命中 → awaiting_command |
| 今天天气怎么样 | 今天天气怎么样？ | ✅ 窗口期接住 |
| 小艺，打开下载文件夹 | 小易打开下载文件夹 | ✅ wake + cmd 一次切分 |
| 小艺，现在几点了 | 小易，现在几点了？ | ✅ 全链路穿透 |

### 8.3 TTS 首包

| 引擎 | 首包 | 采样率 | 网络 |
|---|---|---|---|
| edge 晓晓 | 1037–1560 ms | 24 kHz | 依赖 |
| kokoro fp32 | 457–772 ms | 24 kHz | 无 |
| kokoro int8 | 711–793 ms | 24 kHz | 无 |
| ~~piper huayan~~ | ~~45–160 ms~~ | 22.05 kHz | 无（已卸载） |

### 8.4 端到端（说完 → 文字进输入框）

| 阶段 | 耗时 |
|---|---|
| 端点判定（0.55 s 静音） | ~550 ms |
| SenseVoice 转写 | 141 ms |
| 唤醒词匹配 | < 1 ms |
| 桥 → CDP 键入 + 回车 | 560 ms |
| **合计** | **~1.25 s** |

---

## 9. 从零部署步骤

```bash
# 1) 基础设施：确保 node 带 TS 支持（DSH run_code 依赖）
node -p "process.features.typescript"        # 应为 strip
which -a node                                 # /usr/local/bin/node 需在前

# 2) Python 环境
cd ~/project/test/voice-assistant
uv venv --python 3.12 .venv
. .venv/bin/activate
uv pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
   edge-tts sherpa-onnx kokoro-onnx "misaki[zh]"

# 3) 下载 kokoro 权重（GitHub 直连太慢，用 ghfast 代理）
B=https://ghfast.top/https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
mkdir -p kokoro && cd kokoro
curl -fL -C - -o kokoro-v1.0.onnx  "$B/kokoro-v1.0.onnx"      # 310 MB
curl -fL -C - -o voices-v1.0.bin   "$B/voices-v1.0.bin"       # 27 MB

# 4) systemd 单元
install -m644 dsh-tts.service dsh-autoread.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dsh-tts.service dsh-autoread.service
loginctl enable-linger $USER        # 注销后仍运行

# 5) 专用 Chrome（注入通道）
google-chrome --remote-debugging-port=9222 \
  --user-data-dir=$HOME/.dsh-voice-chrome \
  --no-first-run --new-window http://127.0.0.1:3080
# 用 dsh web 打印的 token URL 完成一次认证

# 6) 注入桥 + 语音环路
cd ~/project/test/voice-assistant
nohup .venv/bin/python src/voice_bridge.py > /tmp/voice_bridge.log 2>&1 &
VL_CMD_URL=http://127.0.0.1:8766/inject \
  nohup .venv/bin/python src/voice_loop.py > /tmp/voice_loop.log 2>&1 &
```

---

## 10. 运维手册

### 10.1 状态检查

```bash
systemctl --user is-active dsh-tts.service dsh-autoread.service
curl -s http://127.0.0.1:8765/health     # TTS：引擎/音色/降级计数
curl -s http://127.0.0.1:8766/health     # 桥：CDP 脚本是否就位
curl -s http://127.0.0.1:9222/json/list  # Chrome 页面 target
```

### 10.2 日志

| 日志 | 路径 |
|---|---|
| TTS | `/tmp/dsh-tts.log` |
| 自动朗读 | `/tmp/dsh-autoread.log` |
| systemd | `journalctl --user -u dsh-tts.service -f` |

### 10.3 常用操作

```bash
# 换默认音色（改 unit 里的 Environment 后重启）
sed -i 's/TTS_VOICE=.*/TTS_VOICE=zh-CN-YunxiNeural/' ~/.config/systemd/user/dsh-tts.service
systemctl --user daemon-reload && systemctl --user restart dsh-tts.service

# 临时换引擎（不重启）
curl -X POST http://127.0.0.1:8765/speak -d '{"text":"测试","engine":"kokoro"}'

# 调整唤醒词
VL_WAKE="小艺,你好小艺,贾维斯" .venv/bin/python src/voice_loop.py

# 只测识别不派发（不设 VL_CMD_URL 即可）
.venv/bin/python src/voice_loop.py --wav /tmp/test.wav
```

### 10.4 故障对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| 桥返回 `page not authenticated` | DSH 宿主重启，token 变了 | 用新 token URL 在 Chrome 窗口重新打开 |
| 桥返回 `composer not found` | 页面未加载完 / 不在会话页 | 等待或手动切到会话页 |
| 唤醒无反应 | 音量过低 / 麦克风被占用 | 看环路日志有无 `heard`；`arecord -d 3 /tmp/t.wav` 验证 |
| 播报没声音 | edge 网络失败 | 看 `/health` 的 `edgeConsecutiveFailures`；会自动降级 kokoro |
| 循环刷 `barge_in` | 能量打断被开启且无 AEC | 确认 `VL_BARGE=off`（默认） |
| 服务不自启 | 未开 linger | `loginctl enable-linger $USER` |

---

## 11. 已知限制

1. **无 AEC**：播报时麦克风会听到扬声器；当前以"关闭能量打断 + 语音关键词打断"规避，真打断需上 WebRTC AEC3
2. **唤醒延迟 0.55 s**：由收尾静音窗口决定，可调低但会切碎长句
3. **每次宿主重启需重新认证 Chrome**：token 随宿主启动变化
4. **注入目标为"当前打开的页面"**：换会话需在 Chrome 窗口里切换
5. **语音环路与注入桥未做成 systemd**：宿主重启后需手动拉起（unit 文件已给出，待确认）
6. **常驻录音**：环路运行时麦克风持续采集（仅本地处理，不上传）
7. **TTS 降级状态不持久**：进程内计数，重启复位回 edge

---

## 12. 踩坑记录（按发生顺序）

1. **DSH 0.2.x 无外部 HTTP API**：社区插件 `dsh-api` 依赖 `@deepseek-ai/dsh-host-apiproxy`，该包在 0.2.x 已被 `typert` RPC 框架取代 → 放弃插件路线，改 CDP
2. **`ast.parse()` 通过 ≠ 可运行**：`global` 声明位置错误属编译期错误，必须用 `python -m py_compile`
3. **`pkill -f <pattern>` 会杀掉自己**：命令行本身包含该字符串 → 用 `[t]pattern` 或 PID 精确匹配
4. **SenseVoice 不认识专名**："小艺"→小一/小易 → 拼音同音匹配
5. **无 AEC 导致打断自激**：一次刷出 25 条 `barge_in`
6. **misaki 的 `ZHG2P(version="1.1")` 输出注音符号且损坏** → 必须用默认版本输出 IPA
7. **kokoro 自带中文路径不可用**：默认走 espeak，报 `language "zh" is not supported` → 必须用 misaki 生成音素 + `is_phonemes=True`
8. **自动朗读回放历史**：初次监听未设基线，把 1700 行历史全念了一遍 → 启动时记录基线
9. **hf-mirror 会整站不可达 / GitHub 极慢**：大文件用 `curl -C -` 断点续传 + ghfast 代理

---

## 13. 后续路线

| 优先级 | 事项 | 预期收益 |
|---|---|---|
| P0 | 语音环路 + 注入桥 systemd 化 | 重启后自动可用 |
| P0 | 唤醒即时反馈（提示音或"我在"） | 消除 1.25 s 沉默期的不确定感 |
| P1 | WebRTC AEC3 | 可开启真打断，体验接近商用助手 |
| P1 | Silero VAD 替换能量 VAD | 抗噪更强，误切更少 |
| P2 | 唤醒后自动创建独立会话 | 语音指令不污染主对话 |
| P2 | 多轮语音上下文 | 说"再念一遍"能生效 |

---

## 14. 项目化结构（v1.1）

代码已从"根目录散落脚本"整理为标准项目结构：

```
voice-assistant/
├── install.sh / uninstall.sh      一键安装 / 卸载（--purge 连模型一起删）
├── pyproject.toml                 依赖声明（edge-tts / sherpa-onnx / kokoro-onnx / misaki）
├── config/.env.example            全部环境变量与默认值
├── src/
│   ├── src/tts_server.py              TTS 服务 :8765
│   ├── src/auto_read.py               自动朗读（旁路监听会话日志）
│   ├── src/voice_loop.py              语音环路（采集→VAD→ASR→唤醒词）
│   ├── src/voice_bridge.py            注入桥 :8766
│   └── cdp/{send,eval,clear}.mjs  CDP 键入 / 求值 / 清空输入框
├── deploy/{dsh-tts,dsh-autoread,dsh-voice-bridge,dsh-voice-loop,dsh-voice-chrome}.service
├── scripts/{healthcheck,speak}.sh
├── tests/test_wake_match.py       唤醒词单测（6/6 通过，不依赖麦克风与模型）
└── docs/{TECHNICAL,NOTES}.md
```

### 路径解析约定

- Python 脚本通过 `ROOT = dirname(dirname(__file__))` 定位项目根，模型路径不再依赖工作目录
- 注入桥默认指向 `src/cdp/send.mjs`，可用 `VB_CDP_SCRIPT` 覆盖
- systemd 单元使用 `%h` 展开家目录，模板可直接 `install` 到 `~/.config/systemd/user/`

### 服务清单（全部 systemd 托管）

| 单元 | 端口 | 自启 |
|---|---|---|
| dsh-tts.service | 8765 | ✅ |
| dsh-autoread.service | — | ✅ |
| dsh-voice-bridge.service | 8766 | ✅ |
| dsh-voice-loop.service | — | 手动启用（占麦） |
| dsh-voice-chrome.service | 9222 | ✅（graphical-session） |

### 自检

```bash
./scripts/healthcheck.sh     # 7 项检查：4 服务 + 3 接口
.venv/bin/python tests/test_wake_match.py
```

### 项目化过程中修掉的缺陷

1. 路径依赖工作目录 → 改为基于 `__file__` 解析 ROOT
2. 注入脚本原在 `/tmp`，重启即丢 → 迁入 `src/cdp/`
3. 认证判断误报（body 残留提示文本）→ 改为"找不到输入框才判未认证"
4. 清空输入框用 `innerText=""` 对 React 无效 → 新增 `clear.mjs` 走真实键盘事件
5. Chrome 只在后台任务里 → 补 `dsh-voice-chrome.service`
