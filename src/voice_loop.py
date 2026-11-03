#!/usr/bin/env python3
"""语音环路：麦克风 → VAD（Silero，可回退能量）→ SenseVoice → 唤醒词 → 动作。
   四段全部本地；唤醒后的"把话交给 agent"通过 VOICE_CMD_URL 插件化接入（暂未接）。
   用法：voice_loop.py            实时麦克风
        voice_loop.py --wav x.wav  离线回放自测"""
import argparse, errno, json, os, queue, re, socket, sys, threading, time, urllib.request
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:                 # 以脚本方式运行时 sys.path[0] 已是 src
    sys.path.insert(0, HERE)
import audio_io

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
LOCK_PORT = int(os.environ.get("VL_LOCK_PORT", "8767"))

# ── 分段器（VAD）────────────────────────────────────────────────
VAD_ENGINE = os.environ.get("VL_VAD", "silero").strip().lower()   # silero | energy
VAD_MODEL = os.environ.get("VL_VAD_MODEL",
    os.path.expanduser("~/.dsh/speech-to-text/sensevoice/models/silero/silero_vad.onnx"))
VAD_THRESHOLD = float(os.environ.get("VL_VAD_THRESHOLD", "0.5"))
VAD_MIN_SILENCE = float(os.environ.get("VL_VAD_MIN_SILENCE", "0.25"))   # 说完多久算一句结束
VAD_MIN_SPEECH = float(os.environ.get("VL_VAD_MIN_SPEECH", "0.25"))
VAD_MAX_SPEECH = float(os.environ.get("VL_VAD_MAX_SPEECH", "10"))
VAD_WINDOW = 512                 # Silero 的 window_size（采样点，32 ms）；30 ms 帧须攒窗后喂

_rec = None
_lock = None                    # 单实例锁，进程存活期间一直持有
await_cmd_until = 0.0


def acquire_lock(port=None):
    """单实例锁：bind 127.0.0.1:LOCK_PORT（不设 SO_REUSEADDR，否则锁形同虚设）。"""
    port = LOCK_PORT if port is None else port
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)             # 只为出现在 ss -ltnp 里；从不 accept
    except OSError as exc:
        s.close()
        if exc.errno == errno.EADDRINUSE:
            print(f"another voice_loop instance holds lock {port}, exiting", file=sys.stderr, flush=True)
            sys.exit(0)
        print(f"cannot bind voice_loop lock 127.0.0.1:{port}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    return s


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


def _rms_db(samples):
    x = np.asarray(samples, dtype=np.float32) / 32768.0
    return round(float(10 * np.log10(np.mean(x ** 2) + 1e-12)), 1)


class Segmenter:
    """能量 VAD（回退路径）：自适应底噪 + 起音/收尾挂起，输出语音段。"""
    engine = "energy"

    def __init__(self):
        self.noise = -70.0
        self.in_speech = False
        self.start = 0
        self.silence = 0
        self.buf = []

    @property
    def params(self):
        return {"margin": NOISE_MARGIN_DB, "minSpeech": MIN_SPEECH_S,
                "maxSpeech": MAX_SPEECH_S, "endSilence": END_SILENCE_S}

    def push(self, frame, idx):
        out = self._push(frame, idx)
        return [out] if out else []

    def flush(self):
        return [self._cut(0, forced=True)] if self.in_speech else []

    def _push(self, frame, idx):
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


class SileroSegmenter:
    """Silero VAD（sherpa-onnx）：30 ms 帧攒成 512 采样窗口后喂 VAD，段结束即 pop 出样本。"""
    engine = "silero"

    def __init__(self):
        import sherpa_onnx
        path = os.path.expanduser(VAD_MODEL)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"silero model not found: {path}")
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = path
        cfg.silero_vad.threshold = VAD_THRESHOLD
        cfg.silero_vad.min_silence_duration = VAD_MIN_SILENCE
        cfg.silero_vad.min_speech_duration = VAD_MIN_SPEECH
        cfg.silero_vad.max_speech_duration = VAD_MAX_SPEECH
        cfg.silero_vad.window_size = VAD_WINDOW
        cfg.sample_rate = SR
        cfg.num_threads = 1
        cfg.provider = "cpu"
        if not cfg.validate():
            raise RuntimeError("invalid VadModelConfig")
        self.vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=VAD_MAX_SPEECH + 30)
        self.pending = np.zeros(0, dtype=np.float32)     # 凑不满 512 的余量
        self.in_speech = False
        self.tts_at_onset = False
        self.start = 0

    @property
    def params(self):
        return {"model": os.path.basename(VAD_MODEL), "threshold": VAD_THRESHOLD,
                "minSpeech": VAD_MIN_SPEECH, "maxSpeech": VAD_MAX_SPEECH,
                "minSilence": VAD_MIN_SILENCE, "window": VAD_WINDOW}

    def push(self, frame, idx):
        if not self.in_speech and self.vad.is_speech_detected():
            self._onset()
        self.pending = np.concatenate([self.pending, frame.astype(np.float32) / 32768.0])
        while len(self.pending) >= VAD_WINDOW:
            self.vad.accept_waveform(self.pending[:VAD_WINDOW])
            self.pending = self.pending[VAD_WINDOW:]
            if not self.in_speech and self.vad.is_speech_detected():
                self._onset()
        return self._drain()

    def flush(self):
        if len(self.pending):
            self.vad.accept_waveform(np.pad(self.pending, (0, VAD_WINDOW - len(self.pending))))
            self.pending = np.zeros(0, dtype=np.float32)
        self.vad.flush()
        out = self._drain()
        self.in_speech = False
        return out

    def _onset(self):
        self.in_speech = True
        self.tts_at_onset = tts_busy()

    def _drain(self):
        out = []
        while not self.vad.empty():
            seg = self.vad.front
            pcm = (np.asarray(seg.samples, dtype=np.float32) * 32768.0).astype(np.int16)
            start = float(seg.start) / SR
            self.vad.pop()
            dur = len(pcm) / SR
            if dur < MIN_SPEECH_S:                # 与能量法同一条最小时长门槛
                continue
            if not self.vad.is_speech_detected():
                self.in_speech = False
            out.append({"pcm": pcm, "dur": round(dur, 2), "start": round(start, 3),
                        "forced": dur >= VAD_MAX_SPEECH - 0.05,
                        "peak": _rms_db(pcm), "tts_busy": getattr(self, "tts_at_onset", False)})
            self.tts_at_onset = False
        return out


def make_segmenter():
    """默认 Silero；模型缺失或初始化失败 → 一条 warning + 自动回退能量法。"""
    if VAD_ENGINE == "energy":
        return Segmenter()
    try:
        return SileroSegmenter()
    except Exception as exc:
        print(f"warning: silero VAD unavailable ({exc!r}); falling back to energy VAD",
              file=sys.stderr, flush=True)
        return Segmenter()


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
    global _lock
    _lock = acquire_lock()               # 抢麦之前先抢锁，避免重复加载模型
    try:
        dev = audio_io.device_info(kind="input")      # 默认源会随 PipeWire 变化，日志里留痕
    except Exception as exc:
        dev = {"error": repr(exc)}
    rec = recognizer()
    seg = make_segmenter()
    emit("ready", wake=WAKE_WORDS, model=os.path.basename(MODEL), input_device=dev,
         vad=seg.engine, vad_params=seg.params)
    idx = 0
    while True:
        try:
            for frame in audio_io.record_stream(SR, 1, FRAME):
                for out in seg.push(frame, idx):
                    handle_segment(rec, out)
                idx += 1
            emit("capture_eof")
        except Exception as exc:            # 设备被拔/占用：报出来，1 秒后重连
            emit("capture_error", err=repr(exc))
        time.sleep(1)


def run_wav(path):
    import wave
    rec = recognizer()
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    seg = make_segmenter()
    emit("ready", mode="wav", file=path, dur=round(len(data) / SR, 2),
         vad=seg.engine, vad_params=seg.params)
    for i in range(0, len(data) - FRAME, FRAME):
        for out in seg.push(data[i:i + FRAME], i // FRAME):
            handle_segment(rec, out)
    for out in seg.flush():
        handle_segment(rec, out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav")
    a = ap.parse_args()
    run_wav(a.wav) if a.wav else run_live()
