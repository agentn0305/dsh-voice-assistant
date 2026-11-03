# dsh-voice-assistant (DSH plugin)

把 voice-assistant 的 Python 子服务托管进 DeepSeek Harness 宿主进程：
TTS 服务、自动朗读、CDP 注入桥、麦克风环路（唤醒词）。插件本身不依赖任何
`@deepseek-ai/*` 内部包，也不声明 `engines.dsh`，因此不会被版本兼容守卫拒绝。

## 安装

```bash
dsh plugin --profile <name> add -w link:<绝对路径>/plugin
dsh --profile <name> --dump-config | grep voice-assistant   # 不启动服务，只校验配置树
```

`<name>` 用 `ls ~/.dsh/profiles`（Windows: `Get-ChildItem $HOME\.dsh\profiles`）里真实存在的名字；忽略 `node_modules`（pnpm 工作区目录）。
重启宿主后生效。

Python 环境需先按仓库 `pyproject.toml` / `install.ps1` 准备；音频依赖新增 `sounddevice>=0.5`、`miniaudio>=1.59`，
Linux 另需系统 `libportaudio2`（`src/audio_io.py` 会自愈探测常见路径与 `VL_PORTAUDIO_LIB`）。

## 路径解析（不再硬编码）

| 值 | 解析顺序 |
|---|---|
| 项目根 `root` | `config.root` → 环境变量 `DSH_VOICE_ROOT` → 插件自身仓库（`plugin/` 的上一级） |
| Python `python` | `config.python` → `DSH_VOICE_PYTHON` → `<root>/.venv/Scripts/python.exe`（Windows）或 `<root>/.venv/bin/python{,3}` |
| 日志 `logDir` | `config.logDir` → 系统临时目录下的 `dsh-voice-assistant/`（Windows `%TEMP%`） |

`link:` 安装时 Node 解析的是真实路径，所以 Windows 的 junction 也能正确定位仓库。

## 配置（cordis.patch.yml）

| 字段 | 默认 | 说明 |
|---|---|---|
| root | 自动 | 项目根目录；优先级见上表 |
| python | 自动 | 解释器 |
| services | [tts, autoread, bridge, loop] | 要托管的子服务；去掉 `loop` 可不占麦克风 |
| ttsEngine | edge | edge / kokoro |
| ttsVoice | zh-CN-XiaoxiaoNeural | edge 音色 |
| kokoroVoice | zf_xiaoyi | kokoro 音色 |
| modelDir | 空 | 设置后导出 `TTS_KOKORO_MODEL/VOICES` |
| sensevoiceDir | 空 | 设置后导出 `VL_MODEL/VL_TOKENS` |
| workspace | 空 | 导出 `AR_WORKSPACE`（自动朗读监听哪个工作区的会话） |
| sessionsDir | 空 | 导出 `AR_SESSIONS` |
| cmdUrl | http://127.0.0.1:8766/inject | 唤醒后注入地址 |
| wakeWords | 小艺,小艺小艺,你好小艺,小艺助手 | 唤醒词 |
| inputDevice | 空 | 导出 `VL_INPUT_DEVICE`（名称子串或索引） |
| outputDevice | 空 | 导出 `VL_OUTPUT_DEVICE` |
| logDir | <tmp>/dsh-voice-assistant | 子进程日志 |
| ttsPort / bridgePort / lockPort | 8765 / 8766 / 8767 | 端口守卫；`lockPort` 是 loop 单实例锁（调试时可改） |
| cdpScript | 空 | 导出 `VB_CDP_SCRIPT` |
| node | 空 | 导出 `VB_NODE`；默认在宿主自身是 node 时复用它，否则用 PATH 里的 `node`（也可用 `DSH_VOICE_NODE` 指定） |

## 端口守卫

宿主启动时先探测 8765（TTS）。**被占用就整组保持被动**——避免和 systemd/Desktop 内置的
服务抢端口。8766（注入桥）单独探测，被占用只跳过桥。

麦克风环路用 8767 做单实例锁（`config.lockPort` / 环境变量 `VL_LOCK_PORT`，默认 8767）。
启动 loop 前探测该端口：被占用说明 systemd 已托管 loop，插件记录
`loop already running externally (lock port 8767 busy), skipping` 并跳过，不会再开第二个麦克风；
空闲则在子进程环境里写入 `VL_LOCK_PORT` 后正常启动。

## 与 systemd 共存 / 迁移

两个实例同时开麦会重复唤醒。插件默认按上面的锁端口自动避让：

- 只想让 systemd 管 loop：保持现状，无需改动；插件会跳过 loop。
- 想让插件全托管：先 `systemctl --user disable --now dsh-voice-loop`，确认
  `ss -ltnp | grep 8767` 无输出，再重启 DSH。
- 回迁 systemd：`dsh plugin --profile <name> remove dsh-voice-assistant`，然后
  `systemctl --user enable --now dsh-tts.service dsh-autoread.service dsh-voice-bridge.service dsh-voice-loop.service`。

不要同时让两边管理 loop。

## 验证

```bash
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8766/health
tail -f "$(node -p 'require("os").tmpdir()')/dsh-voice-assistant/tts.log"
```

Windows 全流程（安装器、profile、Chrome、认证、验收）见仓库根 `README.md` 的 Windows 章节。
