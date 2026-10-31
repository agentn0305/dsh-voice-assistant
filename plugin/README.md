# dsh-voice-assistant (DSH plugin)

把 voice-assistant 的 Python 子服务托管进 DeepSeek Harness 宿主进程：
TTS 服务、自动朗读、CDP 注入桥。麦克风环路默认不启用（会常驻占麦）。

## 安装

    dsh plugin --profile web add link:/home/ubuntu/project/test/voice-assistant/plugin

插件不依赖任何 @deepseek-ai/* 内部包，也不声明 engines.dsh，
因此不会被插件的版本兼容守卫拒绝。

## 配置（cordis.patch.yml）

| 字段 | 默认 | 说明 |
|---|---|---|
| root | ~/project/test/voice-assistant | 项目根目录 |
| python | <root>/.venv/bin/python | 解释器 |
| services | [tts, autoread, bridge] | 要托管的子服务，加 loop 会占用麦克风 |
| ttsEngine | edge | edge / kokoro |
| ttsVoice | zh-CN-XiaoxiaoNeural | edge 音色 |
| kokoroVoice | zf_xiaoyi | kokoro 音色 |
| wakeWords | 小艺,小艺小艺,你好小艺,小艺助手 | 唤醒词 |
| logDir | ~/.dsh/logs/voice-assistant | 子进程日志 |

## 与 systemd 二选一

两者同时开会端口冲突。用插件时：

    systemctl --user disable --now dsh-tts.service dsh-autoread.service dsh-voice-bridge.service

退回 systemd：

    dsh plugin --profile web remove dsh-voice-assistant
    systemctl --user enable --now dsh-tts.service dsh-autoread.service dsh-voice-bridge.service

## 验证

    curl -s http://127.0.0.1:8765/health
    curl -s http://127.0.0.1:8766/health
    tail -f ~/.dsh/logs/voice-assistant/tts.log
