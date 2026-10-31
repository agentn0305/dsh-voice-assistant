#!/usr/bin/env python3
"""自动朗读监听器：盯住 DSH 会话日志，每轮结束时把助手回复送进 /speak。
   ——不改 DSH 内核，只读 session.v4.jsonl.zstd（zstd+JSONL）。"""
import glob, json, os, subprocess, sys, time, urllib.request

SESS_ROOT = os.environ.get("AR_SESSIONS", os.path.expanduser("~/.dsh/sessions"))
WORKSPACE_DIR = os.environ.get("AR_WORKSPACE", "--home-ubuntu-project-test--")
TTS_URL = os.environ.get("AR_TTS", "http://127.0.0.1:8765/speak")
POLL = float(os.environ.get("AR_POLL", "1.5"))
MAX_CHARS = int(os.environ.get("AR_MAX_CHARS", "600"))

state = {"path": None, "lines": 0, "buf": []}


def newest_session():
    files = glob.glob(os.path.join(SESS_ROOT, WORKSPACE_DIR, "*/session.v4.jsonl.zstd"))
    return max(files, key=os.path.getmtime) if files else None


def read_lines(path):
    try:
        out = subprocess.run(["zstd", "-dc", path], capture_output=True, timeout=30)
        return out.stdout.decode("utf-8", "replace").splitlines()
    except Exception as exc:
        print(f"read failed: {exc!r}", flush=True)
        return None


def text_of(message):
    parts = []
    for c in message.get("content") or []:
        if c.get("type") == "text" and c.get("text"):
            parts.append(c["text"])
    return "\n".join(parts).strip()


def speak(text):
    if not text:
        return
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(TTS_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = json.loads(r.read().decode() or "{}")
        print(f"spoke {payload.get('chars', 0)} chars firstChunk={payload.get('firstChunkMs')}ms", flush=True)
    except Exception as exc:
        print(f"speak failed: {exc!r}", flush=True)


def handle(ev):
    t = ev.get("type")
    if t == "assistant/message":
        txt = text_of(ev.get("data", {}).get("message") or {})
        if txt:
            state["buf"].append(txt)
    elif t == "turn/end":
        if state["buf"]:
            speak("\n".join(state["buf"]))
        state["buf"] = []


def main():
    path = newest_session()
    if not path:
        print("no session found", flush=True)
    else:                                  # 启动基线：历史不朗读，只读之后的新内容
        lines = read_lines(path) or []
        state["lines"] = len(lines)
        print(f"attached {os.path.basename(os.path.dirname(path))} baseline={len(lines)} lines (history muted)", flush=True)
    while True:
        cur = newest_session()
        if cur and cur != path:          # 切到新会话：只记录基线，不朗读历史
            path = cur
            lines = read_lines(path) or []
            state["lines"] = len(lines)
            state["buf"] = []
            print(f"attached {os.path.basename(os.path.dirname(path))} baseline={len(lines)} lines", flush=True)
        elif path:
            lines = read_lines(path)
            if lines is not None:
                if len(lines) < state["lines"]:      # 文件被截断/重建
                    state["lines"] = 0
                    state["buf"] = []
                for raw in lines[state["lines"]:]:
                    try:
                        handle(json.loads(raw))
                    except Exception:
                        pass
                state["lines"] = len(lines)
        time.sleep(POLL)


if __name__ == "__main__":
    if not os.path.isdir(os.path.join(SESS_ROOT, WORKSPACE_DIR)):
        print(f"workspace session dir missing: {os.path.join(SESS_ROOT, WORKSPACE_DIR)}", flush=True)
        sys.exit(1)
    main()
