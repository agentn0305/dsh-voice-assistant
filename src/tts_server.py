#!/usr/bin/env python3
"""独立 TTS 进程：POST /speak {text} -> 清洗 Markdown -> 合成 -> ffplay 边收边播。
   引擎：edge-tts 云端（默认，晓晓）/ kokoro 本地（保留，可切换）。不改 DSH 内核。"""
import asyncio, json, os, re, subprocess, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # 项目根目录（src 的上一层）
PORT = int(os.environ.get("TTS_PORT", "8765"))
ENGINE = os.environ.get("TTS_ENGINE", "edge")              # edge | kokoro（kokoro 保留，按需懒加载）
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "600"))
EDGE_FAIL_THRESHOLD = int(os.environ.get("TTS_EDGE_FAIL_THRESHOLD", "2"))   # 连续失败几次后永久降级

KOKORO_MODEL = os.environ.get("TTS_KOKORO_MODEL", os.path.join(ROOT, "kokoro/kokoro-v1.0.onnx"))
KOKORO_VOICES = os.environ.get("TTS_KOKORO_VOICES", os.path.join(ROOT, "kokoro/voices-v1.0.bin"))
KOKORO_VOICE = os.environ.get("TTS_KOKORO_VOICE", "zf_xiaoyi")
KOKORO_SPEED = float(os.environ.get("TTS_KOKORO_SPEED", "1.0"))

EDGE_VOICE = os.environ.get("TTS_VOICE", "zh-CN-XiaoxiaoNeural")
EDGE_RATE = os.environ.get("TTS_RATE", "+8%")

_lock = threading.Lock()
_proc = None
_engine = ENGINE
_engine_info = {}


def clean(text: str) -> str:
    t = re.sub(r"```.*?```", "。", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\|.*\|\s*$", "", t, flags=re.M)
    t = re.sub(r"^\s*>-{2,}.*$", "", t, flags=re.M)
    t = re.sub(r"[*_~]{1,3}([^*_~]+)[*_~]{1,3}", r"\1", t)
    t = re.sub(r"[|>*#`]", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "。", t)
    t = re.sub(r"\n", "，", t)
    t = re.sub(r"[，。]{2,}", "。", t)
    t = t.strip(" ，。")
    if len(t) > MAX_CHARS:
        t = t[:MAX_CHARS].rsplit("。", 1)[0] + "。后面还有详细内容，看屏幕。"
    return t


def split_sentences(text, max_len=12):
    """首块越短开口越快：12 字约为 0.6 s 合成，再长即线性变慢。"""
    parts = [p for p in re.split(r"(?<=[。！？!?；;])", text) if p.strip()]
    out = []
    for p in parts:
        while len(p) > max_len:
            cut = max(p.rfind("，", 0, max_len), p.rfind(",", 0, max_len), p.rfind("、", 0, max_len))
            if cut <= 4:
                cut = max_len
            out.append(p[:cut + 1])
            p = p[cut + 1:]
        if p.strip():
            out.append(p)
    return out or [text]


def _kill():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.kill()
    _proc = None


def _player(sample_rate):
    return subprocess.Popen(
        ["ffplay", "-f", "s16le", "-ar", str(sample_rate), "-ch_layout", "mono",
         "-nodisp", "-autoexit", "-loglevel", "error", "-"],
        stdin=subprocess.PIPE)


class KokoroEngine:
    def __init__(self):
        from kokoro_onnx import Kokoro
        from misaki import zh as mzh
        t0 = time.perf_counter()
        self.k = Kokoro(KOKORO_MODEL, KOKORO_VOICES)
        self.g = mzh.ZHG2P()
        self.sr = 24000
        try:
            self.synth("预热")           # 首次推理有固定开销，启动时先跑掉
        except Exception:
            pass
        self.load_ms = round((time.perf_counter() - t0) * 1000)

    def synth(self, sentence, voice=None):
        ph = self.g(sentence)[0]
        if not ph or not ph.strip():
            return None
        samples, sr = self.k.create(ph, voice=voice or KOKORO_VOICE, speed=KOKORO_SPEED, is_phonemes=True)
        self.sr = sr
        return (samples * 32767).astype("<i2").tobytes()


def _speak_pipelined(engine, text, voice=None):
    """生产-消费流水线：一边合成下一句，一边把前一句推进播放器。"""
    import queue as _q
    global _proc
    q = _q.Queue(maxsize=4)
    errs = []

    def producer():
        try:
            for s in split_sentences(text):
                try:
                    data = engine.synth(s, voice)
                except TypeError:
                    data = engine.synth(s)
                if data:
                    q.put(data)
        except Exception as exc:
            errs.append(exc)
        finally:
            q.put(None)

    t0 = time.perf_counter()
    threading.Thread(target=producer, daemon=True).start()
    chunk = q.get()
    first_ms = round((time.perf_counter() - t0) * 1000)
    if chunk is None:
        return {"finished": False, "error": repr(errs[0]) if errs else "no audio"}
    _kill()
    proc = _player(engine.sr)
    _proc = proc
    while chunk is not None:
        if proc.poll() is not None:
            return {"finished": False, "interrupted": True}
        try:
            proc.stdin.write(chunk)
            proc.stdin.flush()
        except BrokenPipeError:
            return {"finished": False, "interrupted": True}
        chunk = q.get()
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.wait()
    return {"finished": True, "firstChunkMs": first_ms}


def _speak_edge(text, voice=None):
    global _proc
    import edge_tts
    voice = voice or EDGE_VOICE
    _kill()
    proc = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-"], stdin=subprocess.PIPE)
    _proc = proc
    t0 = time.perf_counter()
    first = None

    async def run():
        nonlocal first
        async for ch in edge_tts.Communicate(text, voice, rate=EDGE_RATE).stream():
            if ch["type"] != "audio":
                continue
            if first is None:
                first = (time.perf_counter() - t0) * 1000
            proc.stdin.write(ch["data"])
            proc.stdin.flush()

    try:
        asyncio.run(run())
    except BrokenPipeError:
        return {"finished": False, "interrupted": True}
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.wait()
    return {"finished": True, "firstChunkMs": round(first or 0)}


_engines = {}
_edge_fails = 0


def ensure_kokoro():
    """按需加载本地 kokoro，作为 edge 失败时的兜底。"""
    if "kokoro" in _engines:
        return _engines["kokoro"]
    try:
        _engines["kokoro"] = KokoroEngine()
        return _engines["kokoro"]
    except Exception as exc:
        print(f"kokoro unavailable for fallback: {exc!r}", flush=True)
        return None


def load_engines():
    """按优先级加载：首选引擎失败则依次降级。"""
    global _engine
    order = [ENGINE] + [e for e in ("kokoro",) if e != ENGINE] + ["edge"]
    for name in order:
        try:
            if name == "kokoro":
                _engines["kokoro"] = KokoroEngine()
            else:
                import edge_tts  # noqa: F401
            _engine = name
            return
        except Exception as exc:
            print(f"engine {name} unavailable: {exc!r}", flush=True)
    _engine = "edge"


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
            eng = _engines.get(_engine)
            self._send(200, {"ok": True, "engine": _engine, "edgeConsecutiveFailures": _edge_fails,
                             "voice": KOKORO_VOICE if _engine == "kokoro" else EDGE_VOICE,
                             "loadMs": getattr(eng, "load_ms", None),
                             "available": sorted(_engines) + (["edge"] if "edge" not in _engines else []),
                             "busy": _lock.locked()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        global _edge_fails, _engine
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode() or "{}")
        except Exception:
            payload = {}
        if self.path == "/stop":
            _kill()
            self._send(200, {"ok": True, "stopped": True})
            return
        if self.path == "/clean":
            self._send(200, {"speakText": clean(payload.get("text", ""))})
            return
        if self.path != "/speak":
            self._send(404, {"error": "not found"})
            return
        text = clean(payload.get("text", ""))
        if not text:
            self._send(400, {"error": "empty text"})
            return
        want = payload.get("engine") or _engine
        if not _lock.acquire(blocking=False):
            self._send(429, {"error": "busy"})
            return
        try:
            fallback_from = None
            if want == "kokoro":
                engine = _engines.get("kokoro") or ensure_kokoro()
                if engine is None:
                    self._send(503, {"error": "kokoro unavailable"})
                    return
                res = _speak_pipelined(engine, text, payload.get("voice"))
            else:
                want = "edge"
                try:
                    res = _speak_edge(text, payload.get("voice"))
                    _edge_fails = 0
                except Exception as exc:                 # 云端失败 → 当次转本地
                    _edge_fails += 1
                    print(f"edge failed ({_edge_fails}/{EDGE_FAIL_THRESHOLD}): {exc!r}", flush=True)
                    engine = ensure_kokoro()
                    if engine is None:
                        raise
                    fallback_from, want = "edge", "kokoro"
                    res = _speak_pipelined(engine, text, None)   # edge 的音色名对 kokoro 无意义
                    res["edgeError"] = repr(exc)[:160]
                    if _edge_fails >= EDGE_FAIL_THRESHOLD:
                        _engine = "kokoro"
                        print("edge failed repeatedly → default engine switched to kokoro", flush=True)
            pv = payload.get("voice")
            if want == "kokoro":                     # 只认 kokoro 自己的音色名，其余回落默认
                used_voice = pv if (pv and pv.startswith(("zf_", "zm_"))) else KOKORO_VOICE
            else:
                used_voice = pv or EDGE_VOICE
            out = {"ok": True, "engine": want, "voice": used_voice, "chars": len(text), **res}
            if fallback_from:
                out["fallbackFrom"] = fallback_from
            self._send(200, out)
        except Exception as exc:
            self._send(500, {"error": repr(exc)})
        finally:
            _lock.release()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    load_engines()
    print(f"tts_server on :{PORT} engine={_engine} loaded={sorted(_engines)}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
