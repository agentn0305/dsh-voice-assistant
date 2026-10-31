#!/usr/bin/env bash
# 开机后自动把最新 token URL 应用到 CDP 专用 Chrome 窗口（消除手工认证）
set -u
LOG=/tmp/dsh-voice-auth.log
exec >>"$LOG" 2>&1
echo "=== $(date "+%F %T") 认证任务启动 ==="
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for i in $(seq 1 40); do
  sleep 3
  URL=$(journalctl --user -u dsh-web.service --no-pager -n 100 2>/dev/null | grep -oE "http://127.0.0.1:3080/\?token=[A-Za-z0-9_-]+" | tail -1)
  if [ -z "$URL" ]; then echo "  第 $i 次：尚未拿到 token"; continue; fi
  if ! curl -sf -m 3 http://127.0.0.1:9222/json/version >/dev/null 2>&1; then echo "  第 $i 次：Chrome/CDP 尚未就绪"; continue; fi
  OUT=$(cd "$ROOT" && node src/cdp/auth.mjs "$URL" 2>&1)
  echo "  第 $i 次：$OUT"
  if echo "$OUT" | grep -q "\"ok\":true"; then echo "=== 认证成功 ==="; exit 0; fi
done
echo "=== 认证失败：40 次重试后仍未成功 ==="
exit 1
