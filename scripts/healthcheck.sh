#!/usr/bin/env bash
# 语音栈自检：自动识别 systemd 托管 / DSH 插件托管两种模式
set -u
ok=0; bad=0
pass() { echo "  ✅ $1"; ok=$((ok+1)); }
fail() { echo "  ❌ $1"; bad=$((bad+1)); }
chk_cmd() { if eval "$2" >/dev/null 2>&1; then pass "$1"; else fail "$1"; fi; }
unit_active() { systemctl --user is-active -q "$1" 2>/dev/null; }

echo "接口："
chk_cmd "TTS   :8765/health"  "curl -sf -m 4 http://127.0.0.1:8765/health"
chk_cmd "桥    :8766/health"  "curl -sf -m 4 http://127.0.0.1:8766/health"
chk_cmd "CDP   :9222/json"    "curl -sf -m 4 http://127.0.0.1:9222/json/version"

if unit_active dsh-tts.service; then
  MODE="systemd"
elif curl -sf -m 3 http://127.0.0.1:8765/health >/dev/null 2>&1; then
  MODE="plugin"
else
  MODE="down"
fi
echo "托管模式：$MODE"

echo "服务："
if [ "$MODE" = "systemd" ]; then
  for u in dsh-tts dsh-autoread dsh-voice-bridge; do
    if unit_active "$u.service"; then pass "$u (systemd)"; else fail "$u (systemd)"; fi
  done
else
  for p in tts_server auto_read voice_bridge; do
    if pgrep -f "$p.py" >/dev/null; then pass "$p (plugin 子进程)"; else fail "$p (plugin 子进程)"; fi
  done
fi
if unit_active dsh-voice-loop.service; then pass "voice-loop (麦克风环路)"; else echo "  ⚪ voice-loop 未启用（可选，占用麦克风）"; fi
if unit_active dsh-voice-chrome.service; then pass "voice-chrome (CDP 窗口)"; else fail "voice-chrome (CDP 窗口)"; fi

echo
echo "详情："
curl -s -m 4 http://127.0.0.1:8765/health; echo
echo "通过 $ok 项，失败 $bad 项，模式 $MODE"
exit $([ "$bad" -eq 0 ] && echo 0 || echo 1)
