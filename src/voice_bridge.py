#!/usr/bin/env python3
"""语音注入桥：POST /inject {"text": "..."} → 通过 CDP 键入 DSH 输入框并回车。
   仅本机回环；失败时返回明确错误，便于语音侧报错。"""
import json, os, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("VB_PORT", "8766"))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDP_SCRIPT = os.environ.get("VB_CDP_SCRIPT", os.path.join(ROOT, "src/cdp/send.mjs"))
NODE = os.environ.get("VB_NODE", "node")


def inject(text):
    p = subprocess.run([NODE, CDP_SCRIPT, text], capture_output=True, text=True, timeout=30)
    out = (p.stdout or "").strip().splitlines()
    payload = {}
    for line in out:
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except Exception:
                pass
    if not payload:
        payload = {"ok": False, "error": "no json from cdp script", "stderr": (p.stderr or "")[:200]}
    payload["exitCode"] = p.returncode
    return payload


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            ok = os.path.exists(CDP_SCRIPT)
            self._send(200, {"ok": ok, "cdpScript": CDP_SCRIPT})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        if self.path != "/inject":
            self._send(404, {"error": "not found"})
            return
        try:
            text = (json.loads(raw.decode() or "{}").get("text") or "").strip()
        except Exception:
            self._send(400, {"error": "bad json"})
            return
        if not text:
            self._send(400, {"error": "empty text"})
            return
        try:
            res = inject(text)
        except Exception as exc:
            self._send(500, {"ok": False, "error": repr(exc)})
            return
        self._send(200 if res.get("ok") else 502, res)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"voice_bridge on :{PORT} script={CDP_SCRIPT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
