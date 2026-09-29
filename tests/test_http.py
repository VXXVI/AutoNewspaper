"""抓取层测试：标准库解码/兜底/重试/TLS 降级，均可离线驱动（本地回环 HTTP 桩）。"""

from __future__ import annotations

import gzip
import threading
import urllib.error
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from newspaper_auto import config, http, tls


@pytest.fixture(autouse=True)
def _clean_tls_state():
    """TLS 层有「本机需要放宽」这个进程级标记，测试之间必须互不污染。"""
    tls.reset()
    try:
        yield
    finally:
        tls.reset()


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def test_decode_body_gzip_roundtrip():
    payload = "中文正文 content"
    assert http._decode_body(gzip.compress(payload.encode()), "gzip") == payload


def test_decode_body_deflate_zlib_and_raw():
    payload = "deflate 正文".encode()
    assert http._decode_body(zlib.compress(payload), "deflate") == payload.decode()
    assert http._decode_body(_raw_deflate(payload), "deflate") == payload.decode()


def test_decode_body_tolerates_bad_gzip():
    # 声明 gzip 但内容不是合法 gzip → 原样解码，不抛异常
    assert http._decode_body(b"not-gzip", "gzip") == "not-gzip"


def test_fetch_once_falls_back_to_curl(monkeypatch):
    monkeypatch.setattr(http, "_stdlib_once", lambda url, timeout: (0, ""))
    monkeypatch.setattr(http, "curl_available", lambda: True)
    monkeypatch.setattr(http, "_curl_once", lambda url, timeout: (200, "x" * 400))
    assert http._fetch_once("http://x", 5) == (200, "x" * 400)


def test_fetch_once_without_curl_reports_network_failure(monkeypatch):
    monkeypatch.setattr(http, "_stdlib_once", lambda url, timeout: (0, ""))
    monkeypatch.setattr(http, "curl_available", lambda: False)
    assert http._fetch_once("http://x", 5) == (0, "")


def test_fetch_once_does_not_fallback_on_valid_http_code(monkeypatch):
    """站点侧的 4xx 是有效结果，不应触发 curl 兜底。"""
    called = {"curl": False}

    def _curl(url: str, timeout: int):
        called["curl"] = True
        return 500, ""

    monkeypatch.setattr(http, "_stdlib_once", lambda url, timeout: (404, ""))
    monkeypatch.setattr(http, "curl_available", lambda: True)
    monkeypatch.setattr(http, "_curl_once", _curl)
    assert http._fetch_once("http://x", 5) == (404, "")
    assert called["curl"] is False


# ---------------------------------------------------------------- TLS 降级重试
class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _patch_urlopen(monkeypatch, handler):
    monkeypatch.setattr(http.urllib.request, "urlopen", handler)


def test_stdlib_once_retries_once_with_relaxed_ciphers_on_handshake_failure(monkeypatch):
    """握手层被拒（OpenSSL 3 拒老套件）→ 用放宽套件重试一次，并记住本机需要放宽。"""
    seen: list[object] = []

    def _urlopen(request, timeout=None, context=None):
        seen.append(context)
        if context is tls.strict_context():
            raise urllib.error.URLError(
                "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert handshake failure"
            )
        return _FakeResponse(200, b"y" * 500)

    _patch_urlopen(monkeypatch, _urlopen)
    assert http._stdlib_once("https://x", 5) == (200, "y" * 500)
    assert seen == [tls.strict_context(), tls.relaxed_context()]
    assert tls.relaxed_is_needed() is True


def test_stdlib_once_does_not_downgrade_on_certificate_failure(monkeypatch):
    """证书链失败降级无用，不能重试：否则每次都白等一个超时。"""
    calls: list[object] = []

    def _urlopen(request, timeout=None, context=None):
        calls.append(context)
        raise urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    _patch_urlopen(monkeypatch, _urlopen)
    assert http._stdlib_once("https://x", 5) == (0, "")
    assert len(calls) == 1


def test_stdlib_once_relaxed_retry_happens_at_most_once(monkeypatch):
    """放宽后仍握手失败 → 只试一次放宽，不允许在两个上下文之间来回重试。"""
    calls: list[object] = []

    def _urlopen(request, timeout=None, context=None):
        calls.append(context)
        raise urllib.error.URLError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert handshake failure")

    _patch_urlopen(monkeypatch, _urlopen)
    assert http._stdlib_once("https://x", 5) == (0, "")
    assert len(calls) == 2


def test_stdlib_once_gives_up_when_relaxed_retry_also_fails(monkeypatch):
    def _urlopen(request, timeout=None, context=None):
        if context is tls.strict_context():
            raise urllib.error.URLError(
                "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert handshake failure"
            )
        raise urllib.error.URLError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] 又断了")

    _patch_urlopen(monkeypatch, _urlopen)
    assert http._stdlib_once("https://x", 5) == (0, "")
    # 放宽没能拿到结果就不能记「本机需要放宽」，否则会污染后续所有请求
    assert tls.relaxed_is_needed() is False


def test_stdlib_once_keeps_site_side_4xx_without_touching_tls(monkeypatch):
    calls: list[object] = []

    def _urlopen(request, timeout=None, context=None):
        calls.append(context)
        exc = urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        raise exc

    _patch_urlopen(monkeypatch, _urlopen)
    assert http._stdlib_once("https://x", 5) == (404, "")
    assert len(calls) == 1
    assert tls.relaxed_is_needed() is False


def test_fetch_retries_until_min_bytes(monkeypatch):
    calls: list[int] = []

    def _fake(url: str, timeout: int):
        calls.append(1)
        return (200, "short") if len(calls) == 1 else (200, "y" * 500)

    monkeypatch.setattr(http, "_fetch_once", _fake)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    code, body = http.fetch("http://x", retries=2)
    assert code == 200 and len(body) == 500 and len(calls) == 2


# ---------------------------------------------------------------- charset 解码
def test_decode_body_prefers_content_type_charset():
    assert http._decode_body("示例大学".encode("gbk"), "", "text/html; charset=gbk") == "示例大学"


def test_decode_body_sniffs_meta_charset():
    payload = '<meta charset="gb2312"><body>示例大学</body>'.encode("gb2312")
    assert "示例大学" in http._decode_body(payload, "")


def test_decode_body_sniffs_meta_http_equiv_charset():
    payload = (
        '<meta http-equiv="Content-Type" content="text/html; charset=gbk"><body>示例大学</body>'
    ).encode("gbk")
    assert "示例大学" in http._decode_body(payload, "")


def test_decode_body_defaults_to_utf8():
    assert http._decode_body("中文正文".encode(), "") == "中文正文"


def test_decode_body_falls_back_to_gb18030_without_declaration():
    """未声明 charset 的 GBK 页面：严格 UTF-8 失败后应退 GB18030，而不是丢字节。"""
    assert http._decode_body("示例大学".encode("gbk"), "") == "示例大学"


def test_decode_body_never_drops_undecodable_bytes():
    # 既不合法 UTF-8 也不合法 GB18030 的字节以替换字符保留，而不是整段消失
    assert http._decode_body(b"\xff", "") == "\ufffd"


def test_curl_once_decodes_body_bytes_and_parses_code(monkeypatch):
    proc = type("P", (), {"stdout": "示例大学".encode("gbk") + b"\n@@HTTP:200"})()
    monkeypatch.setattr(http.subprocess, "run", lambda *args, **kwargs: proc)
    assert http._curl_once("http://x", 5) == (200, "示例大学")


def test_curl_once_without_marker_is_network_failure(monkeypatch):
    proc = type("P", (), {"stdout": "正文".encode()})()
    monkeypatch.setattr(http.subprocess, "run", lambda *args, **kwargs: proc)
    assert http._curl_once("http://x", 5) == (0, "正文")


# ---------------------------------------------------------------- 重试策略
def _drive_fetch(monkeypatch, results):
    calls: list[int] = []
    iterator = iter(results)

    def _fake(url: str, timeout: int):
        calls.append(1)
        return next(iterator)

    sleeps: list[float] = []
    monkeypatch.setattr(http, "_fetch_once", _fake)
    monkeypatch.setattr(http.time, "sleep", lambda seconds: sleeps.append(seconds))
    return calls, sleeps


def test_fetch_does_not_retry_deterministic_4xx(monkeypatch):
    """404/403/410 是站点给出的确定答案，只抓一次、不 sleep。"""
    calls, sleeps = _drive_fetch(monkeypatch, [(404, "")])
    assert http.fetch("http://x", retries=2) == (404, "")
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.parametrize("code", [0, 429, 500, 503])
def test_fetch_retries_transient_failures(monkeypatch, code):
    calls, _sleeps = _drive_fetch(monkeypatch, [(code, ""), (200, "y" * 500)])
    assert http.fetch("http://x", retries=2) == (200, "y" * 500)
    assert len(calls) == 2


def test_fetch_gives_up_after_all_retries(monkeypatch):
    calls, sleeps = _drive_fetch(monkeypatch, [(500, ""), (500, ""), (500, "")])
    assert http.fetch("http://x", retries=2) == (500, "")
    assert len(calls) == 3
    assert len(sleeps) == 2


# ---------------------------------------------------------------- 本地回环桩
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ok":
            data = gzip.compress(("正文内容" * 100).encode())
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args: object) -> None:  # 静音
        return


@pytest.fixture
def http_base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_stdlib_once_decodes_gzip(http_base):
    code, body = http._stdlib_once(http_base + "/ok", 5)
    assert code == 200
    assert body == "正文内容" * 100


def test_stdlib_once_treats_404_as_valid_result(http_base):
    assert http._stdlib_once(http_base + "/missing", 5) == (404, "")


def test_fetch_uses_stdlib_on_loopback(http_base):
    code, body = http.fetch(http_base + "/ok", retries=0)
    assert code == 200 and len(body) >= config.MIN_HTML_BYTES
