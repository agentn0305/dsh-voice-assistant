#!/usr/bin/env python3
"""跨平台音频层：录音（sounddevice 回调 + 队列）与播放（sounddevice / miniaudio / ffplay 回退）。

Linux 需要系统 PortAudio：apt install libportaudio2。若它不在 ld 搜索路径里
（conda、自编译、无 root 解包），用 VL_PORTAUDIO_LIB 指向 libportaudio.so.2 的绝对路径。
环境变量：VL_INPUT_DEVICE / VL_OUTPUT_DEVICE，支持设备名子串或数字索引；
          VL_PORTAUDIO_LIB 见上。
用法：audio_io.py  打印设备表，便于排查麦克风/扬声器。
"""
import ctypes.util
import os
import queue
import shutil
import subprocess
import sys
import threading

import numpy as np

DEFAULT_SR = 16000
DEFAULT_BLOCKSIZE = 480          # 30 ms @ 16 kHz
_QUEUE_FRAMES = 256              # 回调侧最多缓存约 7.7 s，满了丢最旧的一帧
HERE = os.path.dirname(os.path.abspath(__file__))
_sd = None                       # sounddevice 模块缓存（导入一次就够）

# PortAudio 的常见落点：发行版包、无 root 解包、项目 vendor 目录
_PA_PATHS = (
    "~/.local/lib/portaudio/libportaudio.so.2",
    "/usr/lib/x86_64-linux-gnu/libportaudio.so.2",
    "/usr/lib/aarch64-linux-gnu/libportaudio.so.2",
    "/usr/local/lib/libportaudio.so.2",
    "/usr/lib/libportaudio.so.2",
)


def _portaudio_candidates():
    root = os.path.dirname(HERE)
    return [os.path.expanduser(p) for p in _PA_PATHS] + [
        os.path.join(root, "vendor", "portaudio", "libportaudio.so.2"),
        os.path.join(root, "vendor", "libportaudio.so.2"),
    ]


def _find_portaudio():
    for p in _portaudio_candidates():
        if os.path.exists(p):
            return p
    try:
        return ctypes.util.find_library("portaudio") or None
    except Exception:
        return None


def _portaudio_hint():
    return ("[audio_io] 找不到 PortAudio 共享库（sounddevice 需要它）。\n"
            "  Debian/Ubuntu: sudo apt install libportaudio2\n"
            "  没有 root:  apt-get download libportaudio2 && dpkg-deb -x libportaudio2_*.deb /tmp/pa\n"
            "             然后 export VL_PORTAUDIO_LIB=/tmp/pa/usr/lib/x86_64-linux-gnu/libportaudio.so.2\n"
            "  Windows/macOS: sounddevice 的 wheel 自带 PortAudio，通常无需处理")


def _import_with_lib(lib):
    """sounddevice 只在导入时用 find_library 查一次库，所以临时把它指向具体 .so。"""
    orig = ctypes.util.find_library

    def _find(name, *_a, **_kw):
        return lib if name == "portaudio" else orig(name, *_a, **_kw)

    ctypes.util.find_library = _find
    try:
        import sounddevice as sd
    finally:
        ctypes.util.find_library = orig
    return sd


def _import_sounddevice():
    """延迟导入 sounddevice；PortAudio 不在默认搜索路径时自动探测并重试一次。"""
    global _sd
    if _sd is not None:
        return _sd
    lib = os.environ.get("VL_PORTAUDIO_LIB")
    if lib:
        if not os.path.exists(lib):
            raise RuntimeError(f"[audio_io] VL_PORTAUDIO_LIB 不存在: {lib}")
        _sd = _import_with_lib(lib)
        return _sd
    try:
        import sounddevice as sd
    except OSError as exc:
        if "PortAudio" not in str(exc):
            raise
        found = _find_portaudio()
        if not found:
            raise RuntimeError(_portaudio_hint()) from None
        os.environ["VL_PORTAUDIO_LIB"] = found     # 让后续重导入/子进程也走同一条路
        _sd = _import_with_lib(found)
        return _sd
    _sd = sd
    return sd


def match_device(devices, spec, kind="input"):
    """在 sounddevice 设备表里按「数字索引 / 名称子串」定位设备；空 spec 返回 None（用默认设备）。"""
    if spec is None or str(spec).strip() == "":
        return None
    s = str(spec).strip()
    if s.lstrip("+-").isdigit():
        return int(s)
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    label = "输入" if kind == "input" else "输出"
    low = s.lower()
    hits = [i for i, d in enumerate(devices)
            if d.get(key, 0) > 0 and low in str(d.get("name", "")).lower()]
    if not hits:
        names = ", ".join(f"{i}:{d.get('name')}" for i, d in enumerate(devices) if d.get(key, 0) > 0)
        raise ValueError(f"[audio_io] 找不到{label}设备 {spec!r}；可用: {names}")
    return hits[0]


def resolve_device(spec, kind="input"):
    if spec is None or str(spec).strip() == "":
        return None
    sd = _import_sounddevice()
    return match_device(sd.query_devices(), spec, kind)


def device_info(spec=None, kind="input"):
    """报告实际会用的设备（索引/名称/声道/默认采样率），便于日志排查。

    spec 为空时按 VL_INPUT_DEVICE（或 VL_OUTPUT_DEVICE）→ 系统默认设备的顺序解析。
    """
    sd = _import_sounddevice()
    devs = sd.query_devices()
    if spec is None:
        spec = os.environ.get("VL_INPUT_DEVICE" if kind == "input" else "VL_OUTPUT_DEVICE")
    idx = match_device(devs, spec, kind)
    if idx is None:
        idx = int(sd.default.device[0 if kind == "input" else 1])
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    d = devs[idx] if 0 <= idx < len(devs) else {}
    return {"index": idx, "name": str(d.get("name", "?")), "spec": spec or None,
            "channels": int(d.get(key, 0)),
            "default_samplerate": round(float(d.get("default_samplerate", 0) or 0))}


def list_devices():
    """打印设备表 + 默认设备 + 实际选中的输入/输出设备。"""
    sd = _import_sounddevice()
    ver = sd.get_portaudio_version()
    print(f"PortAudio {ver.decode().strip() if isinstance(ver, bytes) else ver}")
    print(sd.query_devices())
    print("默认输入/输出索引:", sd.default.device)
    for var, kind in (("VL_INPUT_DEVICE", "input"), ("VL_OUTPUT_DEVICE", "output")):
        spec = os.environ.get(var)
        if spec:
            print(f"{var}={spec!r} -> {resolve_device(spec, kind)!r}")
    try:
        print("实际录音设备:", device_info(kind="input"))
    except Exception as exc:
        print("实际录音设备: 解析失败", repr(exc))
    try:
        print("实际播放设备:", device_info(kind="output"))
    except Exception as exc:
        print("实际播放设备: 解析失败", repr(exc))


def record_stream(samplerate=DEFAULT_SR, channels=1, blocksize=DEFAULT_BLOCKSIZE, device=None):
    """逐帧产出 int16 numpy 数组（单声道为一维，长度恒为 blocksize）。

    内部 sounddevice.InputStream 回调把原始块塞进队列，本生成器再按 blocksize 重新切帧，
    因此 VAD 侧拿到的永远是等长帧。device 缺省读 VL_INPUT_DEVICE。
    """
    frame_samples = int(blocksize)
    if frame_samples <= 0:
        raise ValueError("[audio_io] blocksize 必须为正整数")
    sd = _import_sounddevice()
    dev = resolve_device(device if device is not None else os.environ.get("VL_INPUT_DEVICE"), "input")
    q: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=_QUEUE_FRAMES)

    def _push(block):
        try:
            q.put_nowait(block)
        except queue.Full:                      # 消费端慢（ASR 忙）时丢最旧的
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(block)
            except queue.Full:
                pass

    def _callback(indata, frames, _time, _status):
        _push(np.array(indata, dtype=np.int16, copy=True).reshape(-1))

    stream = sd.InputStream(samplerate=int(samplerate), channels=int(channels), dtype="int16",
                            blocksize=frame_samples, device=dev, callback=_callback)
    try:
        stream.start()
        pending = np.empty(0, dtype=np.int16)
        while True:
            pending = np.concatenate((pending, q.get())) if pending.size else q.get()
            while pending.size >= frame_samples:
                yield pending[:frame_samples]
                pending = pending[frame_samples:]
    finally:
        try:
            stream.stop()
        finally:
            stream.close()


def _to_float32(samples):
    """int16/int32 → [-1, 1) 的 float32；浮点输入只做裁剪。"""
    a = np.asarray(samples)
    if a.dtype == np.int16:
        return a.astype(np.float32) / 32768.0
    if a.dtype.kind in "iu":
        return a.astype(np.float32) / float(np.iinfo(a.dtype).max + 1)
    a = a.astype(np.float32, copy=False)
    return np.clip(a, -1.0, 1.0)


class Playback:
    """一次播放的句柄：stop() 立即打断，wait() 阻塞到播完/被打断（返回是否完整播完）。"""

    def __init__(self, wait_fn, stop_fn):
        self._wait_fn = wait_fn
        self._stop_fn = stop_fn
        self._stopped = False
        self._lock = threading.Lock()

    @property
    def stopped(self):
        return self._stopped

    def stop(self):
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        try:
            self._stop_fn()
        except Exception:
            pass

    def wait(self, timeout=None):
        return bool(self._wait_fn(timeout)) and not self._stopped


def _sounddevice_playback(data, samplerate, device):
    sd = _import_sounddevice()
    dev = resolve_device(device if device is not None else os.environ.get("VL_OUTPUT_DEVICE"), "output")
    total = data.size
    state = {"pos": 0, "closed": False}
    done = threading.Event()
    closing = threading.Lock()

    def _callback(outdata, frames, _time, _status):
        pos = state["pos"]
        n = min(frames, total - pos)
        if n > 0:
            outdata[:n, 0] = data[pos:pos + n]
            state["pos"] = pos + n
        if n < frames:
            outdata[n:, 0] = 0.0
            done.set()
            raise sd.CallbackStop

    stream = sd.OutputStream(samplerate=int(samplerate), channels=1, dtype="float32",
                             blocksize=0, device=dev, callback=_callback)
    stream.start()

    def _shutdown(abort):
        # 只允许一个线程碰 PortAudio 的停止/关闭：abort 之后再 stop 会挂死在 Pa_StopStream
        with closing:
            if state["closed"]:
                return
            state["closed"] = True
        try:
            if abort:
                stream.abort()
            else:
                stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _stop():
        done.set()
        _shutdown(abort=True)

    def _wait(timeout=None):
        if not done.wait(timeout):
            return False
        _shutdown(abort=False)
        return True

    return Playback(_wait, _stop)


def play_pcm(samples, samplerate=DEFAULT_SR, device=None, blocking=False):
    """播放 numpy 音频（kokoro 的 float32，或 int16），立刻返回可 stop() 的 Playback。

    blocking=True 时等播完再返回。samples 为 (n,) 或 (n, channels)，多声道取均值。
    """
    arr = np.asarray(samples)
    if arr.size == 0:
        raise ValueError("[audio_io] 空音频")
    if arr.ndim > 1:
        arr = arr.reshape(arr.shape[0], -1).mean(axis=1)
    pb = _sounddevice_playback(_to_float32(arr), samplerate, device)
    if blocking:
        pb.wait()
    return pb


def _import_miniaudio():
    try:
        import miniaudio
        return miniaudio
    except Exception:
        return None


def _decode_file(mm, path):
    """用 miniaudio 解码为 (float32 一维, 采样率)。mp3 走专用 s16 解码，其余走通用解码。"""
    low = path.lower()
    if low.endswith(".mp3"):
        d = mm.mp3_read_file_s16(path)
        pcm = np.frombuffer(bytes(d.samples), dtype="<i2")
        sr, ch = d.sample_rate, d.nchannels
    else:
        d = mm.decode_file(path, output_format=mm.SampleFormat.SIGNED16)
        pcm = np.frombuffer(bytes(d.samples), dtype="<i2")
        sr, ch = d.sample_rate, d.nchannels
    if ch > 1:
        pcm = pcm.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return _to_float32(pcm), int(sr)


def _no_player_error(path, decode_err):
    where = "Windows" if sys.platform == "win32" else sys.platform
    msg = (f"[audio_io] 无法播放 {os.path.basename(path)}：PATH 里没有 ffplay，"
           f"miniaudio 解码也不可用（平台 {where}）。"
           f"请 pip install miniaudio；或安装 ffmpeg 并将 ffplay 加入 PATH。")
    if decode_err is not None:
        msg += f" 解码错误: {decode_err!r}"
    return RuntimeError(msg)


def _ffplay_playback(path):
    ffplay = shutil.which("ffplay")
    if not ffplay:
        return None
    proc = subprocess.Popen([ffplay, "-nodisp", "-autoexit", "-loglevel", "error", path],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)

    def _stop():
        if proc.poll() is None:
            proc.kill()

    def _wait(timeout=None):
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    return Playback(_wait, _stop)


def play_file(path, device=None, blocking=False):
    """播放音频文件：mp3 优先 miniaudio 解码后走 play_pcm，失败/不可用时回退 ffplay。"""
    p = os.path.abspath(os.fspath(path))
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    mm = _import_miniaudio()
    decode_err = None
    if mm is not None:
        try:
            data, sr = _decode_file(mm, p)
        except Exception as exc:                # 解不了就交给 ffplay（若能找到）
            decode_err = exc
        else:
            pb = play_pcm(data, sr, device=device, blocking=blocking)
            return pb
    pb = _ffplay_playback(p)
    if pb is None:
        raise _no_player_error(p, decode_err)
    if blocking:
        pb.wait()
    return pb


if __name__ == "__main__":
    for _a in sys.argv[1:]:
        if _a not in ("--list", "-l"):
            print(f"用法: {os.path.basename(__file__)} [--list]", file=sys.stderr)
            raise SystemExit(2)
    list_devices()
