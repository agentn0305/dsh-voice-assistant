#!/usr/bin/env python3
"""audio_io 纯逻辑单测：不打开声卡，不需要 PortAudio 也能跑。"""
import os
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import audio_io

DEVICES = [
    {"name": "HDA Intel PCH: ALC257 Analog (hw:0,0)", "max_input_channels": 0, "max_output_channels": 2},
    {"name": "USB AUDIO Analog Stereo", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "default", "max_input_channels": 128, "max_output_channels": 128},
]


def test_match_device_by_index():
    assert audio_io.match_device(DEVICES, "2") == 2
    assert audio_io.match_device(DEVICES, " 1 ") == 1


def test_match_device_by_substring():
    assert audio_io.match_device(DEVICES, "usb audio", "input") == 1
    assert audio_io.match_device(DEVICES, "HDA", "output") == 0


def test_match_device_empty_means_default():
    assert audio_io.match_device(DEVICES, "") is None
    assert audio_io.match_device(DEVICES, None) is None


def test_match_device_unknown_raises_with_candidates():
    try:
        audio_io.match_device(DEVICES, "不存在的麦克风", "input")
    except ValueError as exc:
        assert "不存在的麦克风" in str(exc) and "USB AUDIO" in str(exc)
    else:
        raise AssertionError("应当抛 ValueError")


def test_to_float32_int16_and_clip():
    out = audio_io._to_float32(np.array([0, 16384, -32768], dtype=np.int16))
    assert out.dtype == np.float32
    assert np.allclose(out, [0.0, 0.5, -1.0])
    clipped = audio_io._to_float32(np.array([2.0, -3.0, 0.25], dtype=np.float64))
    assert np.allclose(clipped, [1.0, -1.0, 0.25])


def test_to_float32_int32():
    out = audio_io._to_float32(np.array([0, 2 ** 30, -(2 ** 31)], dtype=np.int32))
    assert np.allclose(out, [0.0, 0.5, -1.0])


def test_play_pcm_rejects_empty():
    try:
        audio_io.play_pcm(np.zeros(0, dtype=np.float32), 24000)
    except ValueError as exc:
        assert "空音频" in str(exc)
    else:
        raise AssertionError("空音频应当抛 ValueError")


def test_record_stream_rejects_bad_blocksize():
    gen = audio_io.record_stream(16000, 1, 0)
    try:
        next(gen)
    except ValueError as exc:
        assert "blocksize" in str(exc)
    else:
        raise AssertionError("blocksize=0 应当抛 ValueError")


def test_playback_stop_is_idempotent():
    calls = []
    pb = audio_io.Playback(lambda timeout=None: True, lambda: calls.append("stop"))
    assert pb.wait() is True
    pb.stop()
    pb.stop()
    assert calls == ["stop"]
    assert pb.stopped is True


def test_playback_wait_false_after_stop():
    pb = audio_io.Playback(lambda timeout=None: True, lambda: None)
    pb.stop()
    assert pb.wait() is False


def test_play_file_missing_raises():
    try:
        audio_io.play_file("/nonexistent/none.mp3")
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("不存在的文件应当抛 FileNotFoundError")


def test_play_file_without_decoder_says_ffplay():
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.write(fd, b"not really mp3")
    os.close(fd)
    orig_mm, orig_which = audio_io._import_miniaudio, audio_io.shutil.which
    audio_io._import_miniaudio = lambda: None
    audio_io.shutil.which = lambda name: None
    try:
        try:
            audio_io.play_file(path)
        except RuntimeError as exc:
            msg = str(exc)
            assert "ffplay" in msg and "miniaudio" in msg and "pip install miniaudio" in msg, msg
        else:
            raise AssertionError("没有解码器也没有 ffplay 时应当抛 RuntimeError")
    finally:
        audio_io._import_miniaudio, audio_io.shutil.which = orig_mm, orig_which
        os.unlink(path)


def test_lock_exclusive():
    """同一端口第二次 acquire_lock 必须优雅退出（SystemExit(0)），第一次仍持有。"""
    import voice_loop
    s = voice_loop.acquire_lock(0)
    port = s.getsockname()[1]
    try:
        try:
            voice_loop.acquire_lock(port)
        except SystemExit as exc:
            assert exc.code == 0, exc.code
        else:
            raise AssertionError("锁端口被占用时应当 sys.exit(0)")
    finally:
        s.close()


if __name__ == "__main__":
    cases = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in cases:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {name}: {exc!r}")
    print(f"\n{len(cases) - failed}/{len(cases)} 通过")
    sys.exit(1 if failed else 0)
