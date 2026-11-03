#!/usr/bin/env bash
# DSH 语音助手 · 一键安装（幂等，可重复执行）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

TSINGHUA="https://pypi.tuna.tsinghua.edu.cn/simple"
GH_PROXY="https://ghfast.top/https://github.com"
KOKORO_REL="thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m错误: %s\033[0m\n' "$*" >&2; exit 1; }

say "1/6 检查前置依赖"
command -v node >/dev/null || die "缺少 node（需 >= 22.6 且带 TypeScript 支持）"
command -v uv   >/dev/null || die "缺少 uv（curl -LsSf https://astral.sh/uv/install.sh | sh）"
if command -v ffplay >/dev/null; then
  echo "  ✓ ffplay 可用（edge-tts 流式播放，首音更快）"
else
  echo "  ⚠ 未找到 ffplay（可选）：edge-tts 将整段下载后用 miniaudio 播放，首音约 2s"
fi
if ldconfig -p 2>/dev/null | grep -q 'libportaudio\.so\.2'; then
  echo "  ✓ libportaudio2 已安装"
else
  echo "  ⚠ 未找到 libportaudio2（sounddevice 需要）；建议: sudo apt install libportaudio2"
  echo "    代码会自动探测常见路径并设置 VL_PORTAUDIO_LIB，全部落空时才报错"
fi
command -v google-chrome >/dev/null || echo "  ⚠ 未找到 google-chrome，注入通道需要它"
node -p "process.features.typescript" | grep -q strip || echo "  ⚠ node 不支持类型擦除（DSH run_code 可能不可用）"

say "2/6 Python 环境"
[ -d .venv ] || uv venv --python 3.12 .venv
. .venv/bin/activate

say "3/6 安装依赖"
uv pip install -i "$TSINGHUA" edge-tts sherpa-onnx kokoro-onnx "misaki[zh]" "sounddevice>=0.5" "miniaudio>=1.59"

say "4/6 下载模型（断点续传 + 代理）"
mkdir -p kokoro && cd kokoro
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [ -f "$f" ]; then echo "  ✓ $f 已存在"; continue; fi
  echo "  下载 $f ..."
  for i in 1 2 3 4 5; do
    curl -fL -C - --retry 2 -m 300 -o "$f" "$GH_PROXY/$KOKORO_REL/$f" && break
    echo "    第 $i 次中断，续传中..."
  done
done
cd "$ROOT"

say "5/6 修正 dsh-web 服务的 node 路径"
UNIT="$HOME/.config/systemd/user/dsh-web.service"
if [ -f "$UNIT" ] && grep -q '^ExecStart=/usr/bin/node' "$UNIT"; then
  sed -i 's|^ExecStart=/usr/bin/node|ExecStart=/usr/local/bin/node|' "$UNIT"
  systemctl --user daemon-reload
  echo "  ok: dsh-web.service ExecStart -> /usr/local/bin/node"
  echo "      需 systemctl --user restart dsh-web.service 生效（会中断当前会话）"
else
  echo "  - 无需修改"
fi

say "5b/6 安装 systemd 单元"
install -m644 deploy/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dsh-tts.service dsh-autoread.service dsh-voice-bridge.service
loginctl enable-linger "$USER" 2>/dev/null || echo "  ⚠ 未能开启 linger（需要管理员权限）"

say "6/6 自检"
./scripts/healthcheck.sh || true

cat <<'EOF'

—— 还需手工完成三步 ——

1) 专用 Chrome（注入通道），并用 dsh web 打印的 token URL 认证一次：
   google-chrome --remote-debugging-port=9222 \
     --user-data-dir=$HOME/.dsh-voice-chrome \
     --no-first-run --new-window http://127.0.0.1:3080

2) 启用语音环路（会常驻占用麦克风）：
   systemctl --user enable --now dsh-voice-loop.service

3) 把「停止朗读」按钮装进同一个 profile（client/ 是与 plugin/ 分开的第二个包）：
   dsh plugin --profile <name> add -w link:$(pwd)/client
   刷新 DSH 页面后，每条回答下方的操作条里会出现「停止朗读」。

验证：对着麦克风说「小艺，现在几点了」
EOF
