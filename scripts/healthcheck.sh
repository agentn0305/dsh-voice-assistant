#!/usr/bin/env bash
# 四项服务 + 两个端口 + Chrome CDP 自检
set -u
ok=0; bad=0
chk() { if eval "$2" >/dev/null 2>&1; then echo "  ✅ $1"; ok=$((ok+1)); else echo "  ❌ $1"; bad=$((bad+1)); fi; }

echo "服务状态："
chk "dsh-tts"          "systemctl --user is-active -q dsh-tts.service"
chk "dsh-autoread"     "systemctl --user is-active -q dsh-autoread.service"
chk "dsh-voice-bridge" "systemctl --user is-active -q dsh-voice-bridge.service"
chk "dsh-voice-loop"   "systemctl --user is-active -q dsh-voice-loop.service"

echo "接口："
chk "TTS  :8765/health"  "curl -sf -m 4 http://127.0.0.1:8765/health"
chk "桥   :8766/health"  "curl -sf -m 4 http://127.0.0.1:8766/health"
chk "CDP  :9222/json"    "curl -sf -m 4 http://127.0.0.1:9222/json/version"

echo
echo "详情："
curl -s -m 4 http://127.0.0.1:8765/health; echo
echo "通过 $ok 项，失败 $bad 项"
exit $([ "$bad" -eq 0 ] && echo 0 || echo 1)
