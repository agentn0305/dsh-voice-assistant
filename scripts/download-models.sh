#!/usr/bin/env bash
# 下载 voice-assistant 需要的模型（断点续传 + 体积校验）。
# 用法:
#   ./scripts/download-models.sh                     # kokoro 三个 + SenseVoice（缺失时）
#   ./scripts/download-models.sh --skip-sensevoice   # 只下 kokoro
# 镜像策略: hf-mirror 优先，ghfast.top 代理 GitHub 备用，最后直连 GitHub。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HF="https://hf-mirror.com"
GH="https://github.com"
GH_PROXY="${DSH_VOICE_GH_PROXY:-https://ghfast.top/https://github.com}"
KOKORO_REL="thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
SENSE_REPO="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSE_DIR="${DSH_VOICE_SENSEVOICE_DIR:-$HOME/.dsh/speech-to-text/sensevoice/models/sensevoice-onnx}"

SKIP_SENSE=0
for arg in "$@"; do
  case "$arg" in
    --skip-sensevoice) SKIP_SENSE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

say()  { printf '\n==> %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*" >&2; }

size_of() { wc -c <"$1" | tr -d ' '; }

fetch() {
  local dest="$1" size="$2"; shift 2
  if [ -f "$dest" ]; then
    local have; have="$(size_of "$dest")"
    if [ "$have" = "$size" ]; then
      echo "  ✓ 已存在 $(basename "$dest") ($have bytes)"
      return 0
    fi
    warn "$(basename "$dest") 体积 $have != $size，删除重下"
    rm -f "$dest"
  fi
  mkdir -p "$(dirname "$dest")"
  local tmp="$dest.part"
  local url attempt got
  for url in "$@"; do
    echo "  → $url"
    for attempt in 1 2 3 4; do
      if [ -f "$tmp" ] && [ "$(size_of "$tmp")" = "$size" ]; then
        mv -f "$tmp" "$dest"
        echo "  ✓ $(basename "$dest") ($size bytes, 来自上次的 .part)"
        return 0
      fi
      if curl -fL -C - --retry 3 --retry-delay 2 --retry-connrefused \
           --connect-timeout 20 -m 900 -o "$tmp" "$url"; then
        if [ -f "$tmp" ]; then
          got="$(size_of "$tmp")"
          if [ "$got" = "$size" ]; then
            mv -f "$tmp" "$dest"
            echo "  ✓ $(basename "$dest") ($got bytes)"
            return 0
          fi
          warn "体积不符: $got != $size（第 $attempt 次）"
          rm -f "$tmp"
        fi
      else
        warn "第 $attempt 次失败（保留 .part 以便续传）"
      fi
      sleep 2
    done
    rm -f "$tmp"
  done
  warn "全部源失败: $(basename "$dest")"
  return 1
}

FAILED=0
say "kokoro 模型 → $ROOT/kokoro"
fetch "$ROOT/kokoro/kokoro-v1.0.onnx" 325532387 \
  "$HF/fastrtc/kokoro-onnx/resolve/main/kokoro-v1.0.onnx" \
  "$GH_PROXY/$KOKORO_REL/kokoro-v1.0.onnx" \
  "$GH/$KOKORO_REL/kokoro-v1.0.onnx" || FAILED=1

fetch "$ROOT/kokoro/kokoro-v1.0.int8.onnx" 92361271 \
  "$HF/speaches-ai/Kokoro-82M-v1.0-ONNX-int8/resolve/main/model.onnx" \
  "$GH_PROXY/$KOKORO_REL/kokoro-v1.0.int8.onnx" \
  "$GH/$KOKORO_REL/kokoro-v1.0.int8.onnx" || FAILED=1

fetch "$ROOT/kokoro/voices-v1.0.bin" 28214398 \
  "$HF/fastrtc/kokoro-onnx/resolve/main/voices-v1.0.bin" \
  "$GH_PROXY/$KOKORO_REL/voices-v1.0.bin" \
  "$GH/$KOKORO_REL/voices-v1.0.bin" || FAILED=1

if [ "$SKIP_SENSE" -eq 0 ]; then
  say "SenseVoice（唤醒词/识别）→ $SENSE_DIR"
  fetch "$SENSE_DIR/model.int8.onnx" 239233841 \
    "$HF/$SENSE_REPO/resolve/main/model.int8.onnx" || FAILED=1
  fetch "$SENSE_DIR/tokens.txt" 315894 \
    "$HF/$SENSE_REPO/resolve/main/tokens.txt" || FAILED=1
fi

if [ "$FAILED" -ne 0 ]; then
  echo
  warn "有模型未下载成功。可重跑本脚本（断点续传），或设置代理后重试："
  warn "  DSH_VOICE_GH_PROXY=https://ghfast.top/https://github.com ./scripts/download-models.sh"
  exit 1
fi
say "模型就绪"
