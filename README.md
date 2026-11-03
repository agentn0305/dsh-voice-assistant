# DSH Voice Assistant · 给 DeepSeek Harness 装上耳朵和嘴

喊一声「小艺」就能让 DSH 开工，它的回答会被朗读出来。
**不改 DSH 源码**——全部通过外部进程 + 官方扩展点实现。

```
🎤 麦克风 → VAD → SenseVoice(本地) → 唤醒词(拼音同音) ─┐
                                                      ├→ HTTP 注入桥 → CDP → DSH 会话
🔊 扬声器 ← edge-tts/kokoro ← 会话日志旁路监听 ←  DSH ─┘
```

## 特性

| 能力 | 实现 | 是否离线 |
|---|---|---|
| 中文语音识别 | SenseVoice int8（sherpa-onnx） | ✅ 全离线 |
| 唤醒词 | 拼音同音匹配（"小易/小一/晓艺" 都能唤醒） | ✅ |
| 语音播报 | edge-tts 晓晓（默认）/ kokoro 本地（兜底） | 默认联网，失败自动降级 |
| 指令注入 | Chrome DevTools Protocol | ✅ 本地 |
| 自动朗读回复 | 旁路监听会话日志 | ✅ |

实测：从说完话到文字进输入框 **约 1.25 秒**；识别字准 **96.6%**。

## 快速开始

```bash
git clone <repo> && cd voice-assistant
./install.sh                 # 建 venv、装依赖、下载模型、装 systemd 单元
./scripts/healthcheck.sh     # 四项服务自检
```

### 必须手工完成的两步

```bash
# 1) 专用 Chrome（注入通道），用 dsh web 打印的 token URL 完成一次认证
google-chrome --remote-debugging-port=9222 \
  --user-data-dir=$HOME/.dsh-voice-chrome \
  --no-first-run --new-window http://127.0.0.1:3080

# 2) 启用语音环路（会常驻占用麦克风，确认后再开）
systemctl --user enable --now dsh-voice-loop.service
```

## Windows（从零到「说唤醒词 → 注入 → 朗读」）

适用 Windows 10/11 x64；DSH 桌面版与 `dsh web` 都可以。下面每条命令都给出预期输出，
没有输出的步骤以退出码 0 为准。

### 1. 获取代码

```powershell
git clone https://github.com/agentn0305/dsh-voice-assistant.git
cd voice-assistant
```

预期：`Cloning into 'voice-assistant'...`，随后提示符回到仓库目录。

### 2. 运行安装器

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器依次完成：Python ≥3.11（64 位）检查 → 建 `.venv` → 装依赖（清华源优先，PyPI 兜底）
→ 检查 zstd/音频工具/node/Chrome（ffplay 可选）→ 下载模型（断点续传 + 体积校验）→ 写 `config\windows.env`
与用户环境变量 → 打印 profile 安装命令。

Windows **不需要 ffplay/ffmpeg 才能出声**：TTS 由 edge-tts 合成后交给 miniaudio 整段解码；
`zstd` 只给自动朗读解压会话日志用。

预期末尾：

```text
==> 7/7 安装到 DSH profile（profile 名需要你确认）
  现有 profile: web
...
==> 完成。下一步：按上面第 1-4 步安装插件并启动宿主。
```

常见警告与处理：

| 警告 | 处理 |
|---|---|
| `未找到 ffplay`（仅提示，不是错误） | Windows 播放不需要它（走 miniaudio）；只有想强制 ffplay 回退时才 `winget install --id Gyan.FFmpeg -e` |
| `未找到 zstd（自动朗读解压会话日志需要）` | `winget install --id Meta.Zstandard -e`，重开终端 |
| `未找到 Google Chrome` | 安装 Chrome，或设 `DSH_VOICE_CHROME` |
| `未找到 node` | 安装 Node 22+ 并确保 `node` 在 PATH；或设 `DSH_VOICE_NODE` 指向 `node.exe` |

可选参数：`-SkipModels`、`-SkipDeps`、`-NoUserEnv`、`-InstallTools`（winget 自动装 zstd，可选 ffmpeg）、`-Python <exe>`。

### 3. 确认真实 profile 名

```powershell
Get-ChildItem $HOME\.dsh\profiles -Directory | Where-Object { $_.Name -ne "node_modules" } | Select-Object -ExpandProperty Name
```

预期：至少一行 `web`（`node_modules` 是 pnpm 工作区目录，已过滤）；DSH 桌面版可能叫 `desktop` 或其它名字。
**不要照抄示例，按实际输出填下一步的 `<name>`。**

### 4. 安装插件到该 profile

```powershell
dsh plugin --profile web add -w link:C:/Users/<你>/voice-assistant/plugin
```

预期：pnpm 输出 `+ dsh-voice-assistant 1.1.0`（或 `Already up to date`），退出码 0。核对：

```powershell
Select-String -Path $HOME\.dsh\profiles\web\package.json -Pattern "dsh-voice-assistant"
```

预期两处命中：`"dsh-voice-assistant": "link:..."` 依赖，以及 `bundles` 数组中的 `"dsh-voice-assistant"`。

### 5. 校验插件能被加载（不启动服务）

```powershell
dsh --profile web --dump-config | Select-String -Pattern "voice-assistant" -Context 0,8
```

预期出现：

```yaml
- id: voice-assistant
  name: dsh-voice-assistant
  config:
    services:
      - tts
      - autoread
      - bridge
      - loop
```

配置里不再有 `root`：插件按 `config.root` → `DSH_VOICE_ROOT` → 自身仓库位置（`plugin/` 的上一级）解析，
所以同一份包在 Linux/Windows、任意安装路径都能用。

### 6. 启动 DSH 宿主 + 专用 Chrome

先启动 DSH（桌面版直接打开；CLI 用 `dsh web`），等 `http://127.0.0.1:3080` 可访问后：

```powershell
node src\cdp\launch.mjs
```

预期：

```json
{"ok":true,"chrome":"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe","port":9222,"userDataDir":"C:\\Users\\<你>\\.dsh-voice-chrome","url":"http://127.0.0.1:3080"}
```

验证 CDP：

```powershell
curl.exe -s http://127.0.0.1:9222/json/version
```

预期：含 `"Browser": "Chrome/..."` 与 `"Protocol-Version": "1.3"` 的 JSON。

Chrome 不在默认位置时：

```powershell
$env:DSH_VOICE_CHROME = "D:\Chrome\chrome.exe"
node src\cdp\launch.mjs --print-path
```

预期：`{"ok":true,"chrome":"D:\\Chrome\\chrome.exe"}`。找不到时输出
`{"ok":false,"error":"Chrome not found...","tried":[...]}` 且退出码 1。

### 7. 完成一次认证

DSH 启动时会打印 `http://127.0.0.1:3080/?token=...`。把整条 URL 传给：

```powershell
node src\cdp\auth.mjs "http://127.0.0.1:3080/?token=<token>"
```

预期：`{"ok":true,"head":"..."}`，退出码 0。若 Chrome 没起会得到
`{"ok":false,"error":"no dsh page in CDP",...}`；token 过期则 `"ok":false`。

### 8. 验收：注入与朗读

```powershell
curl.exe -s http://127.0.0.1:8765/health
```

预期：

```json
{"ok": true, "engine": "edge", "edgeConsecutiveFailures": 0, "voice": "zh-CN-XiaoxiaoNeural", "loadMs": null, "available": ["edge"], "busy": false}
```

```powershell
curl.exe -s http://127.0.0.1:8766/health
```

预期：`{"ok": true, "cdpScript": "C:\\...\\src\\cdp\\send.mjs"}`

不经过麦克风，直接测注入：

```powershell
curl.exe -s -X POST http://127.0.0.1:8766/inject -H "Content-Type: application/json" -d '{"text":"现在几点了"}'
```

预期：`{"ok":true,"composer":{"tag":"TEXTAREA",...},"typedTail":"现在几点了","sent":true}`，
DSH 随后发出回答并被朗读。

### 9. 说唤醒词

对着麦克风说「小艺，现在几点了」。预期：文字出现在 DSH 输入框并自动发送，回答被朗读。

日志（插件把子进程日志写到系统临时目录）：

```powershell
Get-Content $env:TEMP\dsh-voice-assistant\loop.log -Tail 20
```

预期：SenseVoice 加载日志与识别结果。若报麦克风/扬声器错误，先列设备再指定（见「已知限制」第 5 条）：

```powershell
.venv\Scripts\python.exe src\audio_io.py --list
# 预期：先打印 PortAudio 版本与设备表，再打印 "默认输入/输出索引: (<输入>, <输出>)",
# 最后是 "实际录音设备: {...}" / "实际播放设备: {...}"。把名称子串或索引写进用户环境变量：
[Environment]::SetEnvironmentVariable("VL_INPUT_DEVICE", "麦克风 (Realtek(R) Audio)", "User")
[Environment]::SetEnvironmentVariable("VL_OUTPUT_DEVICE", "扬声器", "User")
# 重开 DSH 宿主后生效
```

### Windows 验证清单

- [ ] `node --check plugin\lib\index.js` 无输出、退出码 0
- [ ] `dsh --profile <name> --dump-config` 能看到 `voice-assistant`，且 `services` 含 `loop`
- [ ] `curl.exe -s http://127.0.0.1:8765/health` 与 `8766/health` 都是 `"ok": true`
- [ ] `node src\cdp\launch.mjs` 返回 `"ok":true`
- [ ] `node src\cdp\auth.mjs <token-url>` 返回 `"ok":true`
- [ ] `POST /inject` 能把文字送进输入框
- [ ] 麦克风唤醒词能触发；回答会被朗读

## 目录结构

```
voice-assistant/
├── install.sh / install.ps1      一键安装（Linux / Windows）
├── uninstall.sh
├── pyproject.toml                依赖声明
├── config/.env.example           全部可配置项
├── src/
│   ├── tts_server.py             TTS 服务（:8765）
│   ├── auto_read.py              自动朗读（监听会话日志）
│   ├── voice_loop.py             语音环路（采集→VAD→ASR→唤醒词）
│   ├── voice_bridge.py           注入桥（:8766）
│   └── cdp/
│       ├── chrome.mjs            Chrome 定位 / CDP 端点解析
│       ├── launch.mjs            启动专用 Chrome
│       └── {send,eval,auth,clear}.mjs  CDP 操作
├── plugin/                       DSH 插件包（宿主托管子服务）
├── client/                       浏览器插件包（「停止朗读」按钮）
├── deploy/*.service              systemd 单元模板
├── scripts/{healthcheck,speak}.sh
├── scripts/download-models.{sh,ps1}
├── tests/test_wake_match.py
├── docs/{TECHNICAL,NOTES}.md
└── kokoro/                       模型（安装器下载）
```

## 服务一览

| 单元 | 端口 | 说明 |
|---|---|---|
| `dsh-tts.service` | 8765 | TTS 合成与播放 |
| `dsh-autoread.service` | — | 自动朗读 |
| `dsh-voice-bridge.service` | 8766 | 注入桥 |
| `dsh-voice-loop.service` | — | 麦克风环路（可选，占麦） |

## 配置

其余键位见 `config/.env.example`；音频相关新增项如下。

### Python 依赖与音频系统库

- `sounddevice>=0.5`（麦克风采集 / 扬声器播放）与 `miniaudio>=1.59`（mp3 解码；Windows 无需 ffplay/ffmpeg 即可出声）。
- Linux 还需要系统 PortAudio：`sudo apt install libportaudio2`。`src/audio_io.py` 带自愈：默认导入失败时依次探测
  `~/.local/lib/portaudio/libportaudio.so.2`、`/usr/lib/x86_64-linux-gnu/`、`/usr/lib/aarch64-linux-gnu/`、
  `/usr/local/lib/`、`/usr/lib/`、项目 `vendor/` 与 `vendor/portaudio/`，命中后自动设置 `VL_PORTAUDIO_LIB` 重试；
  全部落空才报错，并打印 apt 安装 / 免 root 解包命令。
- `ffplay` 不是必需：POSIX 有则 edge-tts 流式播放（首音 1.07–1.5 s），没有则整段下载后播放（首音约 2 s）；
  Windows 固定走 miniaudio 整段解码，不需要 ffmpeg。

### 音频相关环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `VL_INPUT_DEVICE` | 系统默认 | 麦克风名称子串或数字索引 |
| `VL_OUTPUT_DEVICE` | 系统默认 | 扬声器名称子串或数字索引 |
| `VL_LOCK_PORT` | `8767` | voice_loop 单实例锁端口；插件守卫使用同一端口 |
| `VL_PORTAUDIO_LIB` | 自动探测 | `libportaudio.so.2` 绝对路径（conda / 自编译 / 无 root 解包时） |

设备诊断：`.venv/bin/python src/audio_io.py --list`（Windows：`.venv\Scripts\python.exe src\audio_io.py --list`）
打印设备表和实际录音/播放设备；`voice_loop.py` 的 `ready` 事件带 `input_device`（index/name/spec/channels/default_samplerate）。
PortAudio 对系统默认源可能只报别名 `default`（本机 index 18），真实节点名在设备表里。

常用：

```bash
# 换音色（systemd 覆盖）
systemctl --user edit dsh-tts.service   # 加 Environment=TTS_VOICE=zh-CN-YunxiNeural

# 临时换引擎（不重启）
curl -X POST http://127.0.0.1:8765/speak -d '{"text":"测试","engine":"kokoro"}'

# 换唤醒词
systemctl --user edit dsh-voice-loop.service  # Environment=VL_WAKE=小艺,贾维斯
```

## 文档

- [技术文档](docs/TECHNICAL.md)：架构、接口契约、实测数据、部署与排错
- [过程记录](docs/NOTES.md)：每阶段的验证数据与踩坑

## 已知限制

1. 无 AEC：外放时麦克风会听到扬声器，可能自激/重复唤醒（已关闭能量打断）；建议戴耳机
2. DSH 宿主重启后 token 失效，需要在 Chrome 窗口用新 token URL 重新打开一次
3. 注入目标是"当前打开的页面"，换会话需在 Chrome 里切换
4. 语音环路常驻时会持续占用麦克风（仅本地处理，不上传）
5. 音频设备：录音/播放走 `sounddevice`（Windows 用 WASAPI），默认设备不理想时用 `VL_INPUT_DEVICE` / `VL_OUTPUT_DEVICE` 指定名称子串或索引；
   `.venv\Scripts\python.exe src\audio_io.py --list`（Linux: `.venv/bin/python src/audio_io.py --list`）列出全部设备与本次实际使用的设备。
   PortAudio 可能把系统默认源只报为别名 `default`，真实节点名看设备表。
6. 自动朗读依赖 `zstd` CLI 解压会话日志（安装器会检查并给出 winget 命令）；TTS 播放优先 `sounddevice`/`miniaudio`，
   `ffplay` 只是 POSIX 流式回退，Windows 不需要。
7. **首音 ≤1.2 s 不能硬保证**：edge-tts 云端首包实测 1.14–1.49 s（POSIX 有 ffplay 时流式首音 1.07–1.5 s）；
   Windows 无 ffplay 时整段解码，首音约 2.0 s。两条路径都可被 `/stop` 打断。kokoro 本地合成 firstChunk 514–867 ms，不受此限。

## License

私有项目，未授权发布。

## DSH 插件形态

本项目同时提供 DSH 插件包（`plugin/`），可让宿主直接托管四个子服务
（tts / autoread / bridge / loop）：

```bash
dsh plugin --profile <name> add -w link:$(pwd)/plugin    # Linux；profile 名见 ~/.dsh/profiles
```

```powershell
dsh plugin --profile <name> add -w link:C:/path/to/voice-assistant/plugin   # Windows
```

重启宿主后生效；插件先探测 8765（tts）、8766（bridge）、8767（loop 锁）：8765 被占时整组保持被动，
8766/8767 被占则只跳过对应服务，不会抢端口。
插件不硬编码项目路径：依次读 `config.root` → `DSH_VOICE_ROOT` → 自身所在仓库。
Windows 全流程见上文 [Windows 章节](#windows从零到说唤醒词--注入--朗读)。

### 停止朗读按钮

按钮装在独立的浏览器端插件包 `client/`（包名 `dsh-voice-stop-button`）里，
与 `plugin/` 是两个包，所以在 profile 里要**额外 add 一次**：

```bash
dsh plugin --profile <name> add -w link:$(pwd)/client    # Linux
```

```powershell
dsh plugin --profile <name> add -w link:C:/path/to/voice-assistant/client   # Windows
```

装完要**重启一次宿主**才生效（bundle 列表在宿主启动时组合，只刷新页面看不到），
重启前不会有任何副作用；它在
`conversation.chat.assistant-actions` 槽位注册一个条目，**每条已完成的回答下方**
都会多出「停止朗读」（带方形停止符号）。

点击后浏览器直接 `POST http://127.0.0.1:8765/stop`：

- 成功 → 按钮位置短暂显示「已停止」，1.5 秒后恢复「停止朗读」；
- 失败（TTS 服务没起来或端口不通）→ 显示「未在朗读」，不抛异常、不弹窗。

跨域由 `src/tts_server.py` 的白名单处理，页面 Origin 需在 `TTS_ALLOWED_ORIGINS` 里
（默认 `http://127.0.0.1:3080`、`http://localhost:3080`）。用别的端口打开 Web UI 时，
要么把该 Origin 加进环境变量，要么按钮会显示「未在朗读」。

**它只停止朗读，不打断对话生成。**

### 与 systemd 共存 / 迁移（Linux）

插件在启动麦克风环路前会探测本机 **8767**（`config.lockPort` / 环境变量 `VL_LOCK_PORT`，默认 8767）。
若 systemd 的 `dsh-voice-loop` 已在运行，该端口被占用，插件会记录
`loop already running externally (lock port 8767 busy), skipping` 并跳过 loop，不会出现两个进程同时开麦。
空闲时插件正常启动 loop，并把 `VL_LOCK_PORT` 写进子进程环境。

- **只想让 systemd 管 loop、插件管其余服务**：保持现状，无需改动。
- **想让插件全托管**：先 `systemctl --user disable --now dsh-voice-loop`，确认
  `ss -ltnp | grep 8767` 无输出，再重启 DSH。
- **回迁 systemd**：`dsh plugin --profile <name> remove dsh-voice-assistant`，然后
  `systemctl --user enable --now dsh-tts.service dsh-autoread.service dsh-voice-bridge.service dsh-voice-loop.service`。

不要同时让两边管理 loop。

详见 [技术文档 15 章](docs/TECHNICAL.md)。