#!/usr/bin/env bash
# 卸载：停服务、删单元。模型与 venv 默认保留（加 --purge 一并删除）
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

for u in dsh-voice-loop dsh-voice-bridge dsh-autoread dsh-tts; do
  systemctl --user disable --now "$u.service" 2>/dev/null && echo "已停止 $u"
  rm -f "$HOME/.config/systemd/user/$u.service"
done
systemctl --user daemon-reload
echo "systemd 单元已移除"

if [ "$PURGE" = "1" ]; then
  rm -rf "$ROOT/kokoro" "$ROOT/.venv"
  echo "已删除模型与虚拟环境"
else
  echo "模型与 venv 保留（加 --purge 可一并删除）"
fi
