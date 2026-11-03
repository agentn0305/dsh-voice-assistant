#!/usr/bin/env python3
"""tts_server CORS 单测：临时端口（0）起真实 service，不需要声卡与引擎。

覆盖点：预检 204/白名单双 origin、非白名单 403 且无 Allow-Origin、
非预检响应（GET /health、POST /stop）带 Allow-Origin、无 Origin 时不回显、
TTS_ALLOWED_ORIGINS 解析与运行期白名单生效。
"""
import atexit
import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import tts_server

DEV_ORIGIN = "http://127.0.0.1:3080"
LOCAL_ORIGIN = "http://localhost:3080"

_srv = ThreadingHTTPServer(("127.0.0.1", 0), tts_server.H)
PORT = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()
atexit.register(_srv.server_close)


def _req(method, path, origin=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body)
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    hdrs = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, hdrs, data


def _assert_cors(hdrs, origin):
    assert hdrs.get("access-control-allow-origin") == origin, hdrs
    assert hdrs.get("access-control-allow-methods") == "GET, POST, OPTIONS", hdrs
    assert hdrs.get("access-control-allow-headers") == "Content-Type", hdrs
    assert hdrs.get("access-control-max-age") == "600", hdrs


def test_parse_origins():
    assert tts_server.parse_origins("") == ()
    assert tts_server.parse_origins(" a ,, b ") == ("a", "b")
    assert tts_server.parse_origins("http://127.0.0.1:3080") == (DEV_ORIGIN,)


def test_default_whitelist():
    assert tts_server.ALLOWED_ORIGINS == (DEV_ORIGIN, LOCAL_ORIGIN)


def test_preflight_allowed_origins():
    for origin in (DEV_ORIGIN, LOCAL_ORIGIN):
        status, hdrs, body = _req("OPTIONS", "/stop", origin=origin)
        assert status == 204, status
        assert body == b"", body
        _assert_cors(hdrs, origin)


def test_preflight_covers_every_route():
    for path in ("/stop", "/speak", "/clean", "/health"):
        status, hdrs, _ = _req("OPTIONS", path, origin=DEV_ORIGIN)
        assert status == 204, (path, status)
        _assert_cors(hdrs, DEV_ORIGIN)


def test_preflight_rejects_unknown_origin():
    status, hdrs, _ = _req("OPTIONS", "/stop", origin="http://evil.example")
    assert status == 403, status
    assert "access-control-allow-origin" not in hdrs, hdrs


def test_preflight_without_origin_is_403():
    status, hdrs, _ = _req("OPTIONS", "/stop")
    assert status == 403, status
    assert "access-control-allow-origin" not in hdrs, hdrs


def test_post_stop_echoes_origin():
    status, hdrs, body = _req("POST", "/stop", origin=DEV_ORIGIN)
    assert status == 200, status
    assert json.loads(body)["stopped"] is True
    _assert_cors(hdrs, DEV_ORIGIN)


def test_post_clean_echoes_origin():
    status, hdrs, body = _req("POST", "/clean", origin=LOCAL_ORIGIN, body={"text": "# hi"})
    assert status == 200, status
    assert json.loads(body)["speakText"] == "hi"
    _assert_cors(hdrs, LOCAL_ORIGIN)


def test_get_health_echoes_origin():
    status, hdrs, body = _req("GET", "/health", origin=DEV_ORIGIN)
    assert status == 200, status
    assert json.loads(body)["ok"] is True
    _assert_cors(hdrs, DEV_ORIGIN)


def test_no_origin_header_no_cors():
    status, hdrs, _ = _req("GET", "/health")
    assert status == 200, status
    assert "access-control-allow-origin" not in hdrs, hdrs


def test_env_override_whitelist_takes_effect():
    saved = tts_server.ALLOWED_ORIGINS
    try:
        tts_server.ALLOWED_ORIGINS = tts_server.parse_origins("http://127.0.0.1:9999")
        status, hdrs, _ = _req("OPTIONS", "/stop", origin="http://127.0.0.1:9999")
        assert status == 204, status
        _assert_cors(hdrs, "http://127.0.0.1:9999")
        status, _, _ = _req("OPTIONS", "/stop", origin=DEV_ORIGIN)
        assert status == 403, status
    finally:
        tts_server.ALLOWED_ORIGINS = saved


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
