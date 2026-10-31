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

## 目录结构

```
voice-assistant/
├── install.sh / uninstall.sh     一键安装/卸载
├── pyproject.toml                依赖声明
├── config/.env.example           全部可配置项
├── src/
│   ├── tts_server.py             TTS 服务（:8765）
│   ├── auto_read.py              自动朗读（监听会话日志）
│   ├── voice_loop.py             语音环路（采集→VAD→ASR→唤醒词）
│   ├── voice_bridge.py           注入桥（:8766）
│   └── cdp/{send,eval}.mjs       CDP 键入 / 求值
├── deploy/*.service              systemd 单元模板
├── scripts/{healthcheck,speak}.sh
├── tests/test_wake_match.py
├── docs/{TECHNICAL,NOTES}.md
└── kokoro/                       模型（install.sh 下载）
```

## 服务一览

| 单元 | 端口 | 说明 |
|---|---|---|
| `dsh-tts.service` | 8765 | TTS 合成与播放 |
| `dsh-autoread.service` | — | 自动朗读 |
| `dsh-voice-bridge.service` | 8766 | 注入桥 |
| `dsh-voice-loop.service` | — | 麦克风环路（可选，占麦） |

## 配置

所有可调项见 `config/.env.example`。常用：

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

1. 无 AEC：播报时麦克风会听到扬声器（已关闭能量打断）
2. DSH 宿主重启后 token 失效，需要在 Chrome 窗口用新 token URL 重新打开一次
3. 注入目标是"当前打开的页面"，换会话需在 Chrome 里切换
4. 语音环路常驻时会持续占用麦克风（仅本地处理，不上传）

## License

私有项目，未授权发布。

## DSH 插件形态

本项目同时提供 DSH 插件包（`plugin/`），可让宿主直接托管三个子服务：

```bash
dsh plugin --profile web add link:$(pwd)/plugin    # 安装
# 重启宿主后生效；插件会先探测端口，被 systemd 占用时自动保持被动
```

详见 [技术文档 15 章](docs/TECHNICAL.md)。