#!/usr/bin/env python3
"""唤醒词匹配单测：不依赖麦克风与模型，纯字符串逻辑。"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("vl", os.path.join(ROOT, "src/voice_loop.py"))
vl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vl)

CASES = [
    ("小艺，今天天气怎么样。", "小艺", "今天天气怎么样"),
    ("小易，现在几点了？", "小艺", "现在几点了"),      # 同音
    ("你好，小易。", "你好小艺", ""),                   # 同音 + 无指令
    ("晓艺帮我打开文件", "小艺", "帮我打开文件"),        # 同音
    ("今天天气怎么样", None, None),                     # 不该唤醒
    ("我。", None, None),                               # 噪声
]


def main():
    failed = 0
    for text, want_wake, want_cmd in CASES:
        wake, cmd = vl.match_wake(text)
        ok = (wake == want_wake) and ((cmd or "") == (want_cmd or ""))
        print(f"{'PASS' if ok else 'FAIL'}  {text!r:24} -> wake={wake!r} cmd={cmd!r}")
        failed += 0 if ok else 1
    print(f"\n{len(CASES) - failed}/{len(CASES)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
