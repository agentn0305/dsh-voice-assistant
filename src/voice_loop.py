#!/usr/bin/env python3
"""语音环路：麦克风 → 能量 VAD → SenseVoice → 唤醒词 → 动作。
   四段全部本地；唤醒后的"把话交给 agent"通过 VOICE_CMD_URL 插件化接入（暂未接）。
   用法：voice_loop.py            实时麦克风
        voice_loop.py --wav x.wav  离线回放自测"""
import argparse, json, os, queue, re, subprocess, sys, threading, time, urllib.request
import numpy as np

SR = 16000
FRAME = 480                      # 30 ms
FRAME_MS = 30

MODEL = os.environ.get("VL_MODEL",
    os.path.expanduser("~/.dsh/speech-to-text/sensevoice/models/sensevoice-onnx/model.int8.onnx"))
TOKENS = os.environ.get("VL_TOKENS",
    os.path.expanduser("~/.dsh/speech-to-text/sensevoice/models/sensevoice-onnx/tokens.txt"))
WAKE_WORDS = [w for w in os.environ.get("VL_WAKE", "小艺,小艺小艺,你好小艺,小艺助手").split(",") if w]
STOP_WORDS = ["停止", "别说了", "安静", "停下", "停"]
TTS_HEALTH = os.environ.get("VL_TTS_HEALTH", "http://127.0.0.1:8765/health")
TTS_STOP = os.environ.get("VL_TTS_STOP", "http://127.0.0.1:8765/stop")
CMD_URL = os.environ.get("VL_CMD_URL", "")          # 预留：唤醒后把命令投给 agent
NOISE_MARGIN_DB = float(os.environ.get("VL_MARGIN", "10"))
MIN_SPEECH_S = float(os.environ.get("VL_MIN_SPEECH", "0.35"))
MAX_SPEECH_S = float(os.environ.get("VL_MAX_SPEECH", "10"))
END_SILENCE_S = float(os.environ.get("VL_END_SILENCE", "0.55"))
BARGE_MODE = os.environ.get("VL_BARGE", "off")     # off = 只认语音关键词；energy = 能量打断（无 AEC 会自激）

_rec = None
await_cmd_until = 0.0


def recognizer():
    import sherpa_onnx
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=MODEL, tokens=TOKENS, num_threads=2, language="zh", use_itn=True)


def transcribe(rec, pcm):
    s = rec.create_stream()
    s.accept_waveform(SR, pcm.astype(np.float32) / 32768.0)
    rec.decode_stream(s)
    return (s.result.text or "").strip()


def norm(t):
    return re.sub(r"[\s，。？、！,.?!；;：:·~\-]", "", t or "")


def _pinyin_chars(n):
    """逐字拼音（无声调），用于同音模糊匹配：小艺/小一/小易 都算命中。"""
    try:
        from pypinyin import lazy_pinyin
        return [p.lower() for p in lazy_pinyin(n)]
    except Exception:
        return []


def match_wake(text):
    n = norm(text)
    # 1) 精确匹配优先
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        wn = norm(w)
        if wn and wn in n:
            return w, n[n.index(wn) + len(wn):]
    # 2) 同音模糊匹配
    py = _pinyin_chars(n)
    if not py:
        return None, None
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        wn = norm(w)
        wpy = "".join(_pinyin_chars(wn))
        if not wpy:
            continue
        for size in range(len(wn), min(len(wn) + 2, len(n)) + 1):
            for i in range(0, len(n) - size + 1):
                window = "".join(py[i:i + size])
                if window == wpy:
                    return w, n[i + size:]
    return None, None


def post(url, payload=None, timeout=3):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


def tts_busy():
    try:
        with urllib.request.urlopen(TTS_HEALTH, timeout=1) as r:
            return bool(json.loads(r.read().decode()).get("busy"))
    except Exception:
        return False


def emit(kind, **kw):
    print(json.dumps({"t": round(time.time(), 2), "kind": kind, **kw}, ensure_ascii=False), flush=True)


class Segmenter:
    """能量 VAD：自适应底噪 + 起音/收尾挂起，输出语音段。"""
    def __init__(self):
        self.noise = -70.0
        self.in_speech = False
        self.start = 0
        self.silence = 0
        self.buf = []

    def push(self, frame, idx):
        db = 10 * np.log10(np.mean((frame / 32768.0) ** 2) + 1e-12)
        if not self.in_speech:
            self.noise = 0.98 * self.noise + 0.02 * db if db < self.noise + 6 else self.noise
        th = self.noise + NOISE_MARGIN_DB
        speech = db > th
        if speech and not self.in_speech:
            self.in_speech = True
            self.start = idx * FRAME_MS / 1000.0
            self.buf = [frame]
            self.silence = 0
            self.tts_at_onset = tts_busy()      # 只在段结束时判定一次打断
            self.peak = db
            return None
        if self.in_speech:
            self.peak = max(self.peak, db)
            if speech:
                self.silence = 0
                self.buf.append(frame)
                if len(self.buf) * FRAME_MS / 1000.0 >= MAX_SPEECH_S:
                    return self._cut(idx, forced=True)
            else:
                self.silence += 1
                self.buf.append(frame)
                if self.silence * FRAME_MS / 1000.0 >= END_SILENCE_S:
                    return self._cut(idx)
        return None

    def _cut(self, idx, forced=False):
        seg = np.concatenate(self.buf)
        dur = len(seg) / SR
        busy = getattr(self, "tts_at_onset", False)
        peak = getattr(self, "peak", -99.0)
        self.in_speech = False
        self.buf = []
        self.silence = 0
        self.tts_at_onset = False
        if dur < MIN_SPEECH_S:
            return None
        return {"pcm": seg, "dur": round(dur, 2), "start": self.start,
                "forced": forced, "peak": round(float(peak), 1), "tts_busy": busy}


FILLER = {"", "嗯", "哦", "啊", "呃", "嗯嗯", "哦哦", "唉", "诶"}


def handle_segment(rec, seg):
    global await_cmd_until
    t_asr = time.time()
    text = transcribe(rec, seg["pcm"])
    asr_ms = round((time.time() - t_asr) * 1000)
    if BARGE_MODE == "energy" and seg.get("tts_busy") and seg["dur"] >= 0.45:
        try:                                  # 播报中说话且够长 → 打断（每段最多一次）
            post(TTS_STOP); emit("barge_in", at=seg["start"], dur=seg["dur"], peak=seg.get("peak"))
        except Exception as exc:
            emit("barge_in_failed", err=repr(exc))
    if norm(text) in FILLER:                 # 呼吸/桌面噪声解出的空段，丢弃
        return
    if seg.get("peak") is not None and seg["peak"] < -45:   # 电平过低=噪声，不是人声
        return
    wake, cmd = match_wake(text)
    emit("heard", dur=seg["dur"], peak=seg.get("peak"), asrMs=asr_ms, text=text,
         wake=wake or None, cmd=cmd or None)
    if wake is None and time.time() < await_cmd_until:
        await_cmd_until = 0.0
        emit("command_from_window", cmd=norm(text))
        if CMD_URL:
            try:
                post(CMD_URL, {"text": norm(text)}); emit("dispatched", cmd=norm(text))
            except Exception as exc:
                emit("dispatch_failed", err=repr(exc))
        return
    if wake is None:
        if any(w in norm(text) for w in STOP_WORDS) and tts_busy():
            try:
                post(TTS_STOP); emit("stop_command", text=text)
            except Exception:
                pass
        return
    emit("wake", word=wake, cmd=cmd)
    if not cmd:                              # 只喊了唤醒词 → 5 秒内等指令
        await_cmd_until = time.time() + 5.0
        emit("awaiting_command", secs=5)
        return
    if cmd and CMD_URL:
        try:
            post(CMD_URL, {"text": cmd}); emit("dispatched", cmd=cmd)
        except Exception as exc:
            emit("dispatch_failed", err=repr(exc))


def run_live():
    rec = recognizer()
    emit("ready", wake=WAKE_WORDS, model=os.path.basename(MODEL))
    seg = Segmenter()
    idx = 0
    while True:
        p = subprocess.Popen(["arecord", "-D", "default", "-f", "S16_LE", "-r", str(SR),
                              "-c", "1", "-t", "raw", "-q", "-"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        try:
            while True:
                chunk = p.stdout.read(FRAME * 2)
                if len(chunk) < FRAME * 2:
                    emit("capture_eof"); break
                frame = np.frombuffer(chunk, dtype="<i2")
                out = seg.push(frame, idx)
                idx += 1
                if out:
                    handle_segment(rec, out)
        finally:
            p.kill()
        time.sleep(1)                       # arecord 挂了就重连


def run_wav(path):
    import wave
    rec = recognizer()
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    emit("ready", mode="wav", file=path, dur=round(len(data) / SR, 2))
    seg = Segmenter()
    for i in range(0, len(data) - FRAME, FRAME):
        out = seg.push(data[i:i + FRAME], i // FRAME)
        if out:
            handle_segment(rec, out)
    out = seg._cut(0, forced=True) if seg.in_speech else None
    if out:
        handle_segment(rec, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    a = ap.parse_args()
    run_wav(a.wav) if a.wav else run_live()
