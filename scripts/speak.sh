#!/usr/bin/env bash
# 用法: ./scripts/speak.sh "要说的话" [engine] [voice]
set -eu
TEXT="${1:?用法: speak.sh <文本> [engine] [voice]}"
ENGINE="${2:-}"
VOICE="${3:-}"
BODY=$(python3 -c 'import json,sys; d={"text":sys.argv[1]};
if len(sys.argv)>2 and sys.argv[2]: d["engine"]=sys.argv[2]
if len(sys.argv)>3 and sys.argv[3]: d["voice"]=sys.argv[3]
print(json.dumps(d, ensure_ascii=False))' "$TEXT" "$ENGINE" "$VOICE")
curl -s -m 180 -X POST http://127.0.0.1:8765/speak -H 'Content-Type: application/json' -d "$BODY"
echo
