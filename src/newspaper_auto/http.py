"""HTTP 抓取层：标准库优先，系统 ``curl`` 兜底。

为什么不再默认依赖 curl
------------------------
``epaper.gmw.cn``（光明日报数字报）的服务器在 **HTTP/2 下会直接断连**
（curl 默认经 ALPN 协商到 h2，报 ``52 Empty reply from server``）。标准库
``urllib`` 默认走 **HTTP/1.1**，恰好绕开该问题，因此以标准库为主路径：

- 去掉「必须装有系统 curl」这一硬依赖，便携分发 / 单文件打包更容易；
- 少一次 ``fork``/``exec``，抓取更快，Windows 上尤其明显。

curl 仍保留为**兜底**：当标准库遇到网络层失败（``code == 0``）时，若系统存在 curl
则再试一次，覆盖个别站点的 TLS/重定向边角情况。抓取语义（重试、最小正文长度、
``(http_code, body)`` 返回）保持不变。

TLS 边角情况交给 ``tls.py``
----------------------------
中国教育报（``paper.jyb.cn``）在不同 Windows 上会以**两种完全不同的方式**卡死：
OpenSSL 3 的主机被默认套件的安全级别拒掉（握手层失败），Win7 的系统根库缺
``USERTrust RSA Certification Authority`` 锚点（证书链失败）。因此标准库抓取一律
带上 ``tls`` 模块给的上下文，并在**握手层**被拒时用放宽套件重试一次 ——
详见 :mod:`newspaper_auto.tls` 的模块文档。
"""

from __future__ import annotations

import contextlib
import gzip
import http.client
import logging
import random
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zlib
from typing import Any
from urllib.parse import urljoin, urlsplit

from . import config, procutil, tls

_LOGGER = logging.getLogger(__name__)

# ``-w "\n@@HTTP:%{http_code}"`` 追加的状态码标记；curl 路径只在**末尾字节**匹配，
# 防止正文中出现字面量 ``@@HTTP:`` 干扰状态码解析。
_HTTP_CODE_RE = re.compile(rb"\r?\n@@HTTP:(\d{3})\s*$")
# 响应头 ``Content-Type`` 里的 charset（如 ``text/html; charset=gbk``）
_CONTENT_TYPE_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)
# HTML 内声明：``<meta charset="gb2312">`` 或
# ``<meta http-equiv="Content-Type" content="text/html; charset=gbk">``
_META_CHARSET_RE = re.compile(rb"<meta[^>]*?charset\s*=\s*[\"']?\s*([a-zA-Z0-9_.:-]+)", re.I)

_HOST_SEMAPHORES_LOCK = threading.Lock()
_HOST_SEMAPHORES: dict[tuple[str, str], threading.BoundedSemaphore] = {}
_HOST_SEMAPHORE_LIMIT = 0

_ORIG_URLOPEN = urllib.request.urlopen
_BAD_IP_TTL = 180.0
_POOL_IDLE_TTL = 30.0
_MAX_REDIRECTS = 5
_BAD_IPS_LOCK = threading.Lock()
_BAD_IPS: dict[tuple[str, int], float] = {}
_POOL_LOCK = threading.Lock()
_CONN_POOL: dict[tuple[str, str, int, int], list[tuple[http.client.HTTPConnection, float]]] = {}


def reset_pool() -> None:
    """清空连接池与坏 IP 缓存（主要供测试隔离用）。"""
    with _BAD_IPS_LOCK:
        _BAD_IPS.clear()
    with _POOL_LOCK:
        conns = [conn for bucket in _CONN_POOL.values() for conn, _ts in bucket]
        _CONN_POOL.clear()
    for conn in conns:
        with contextlib.suppress(Exception):
            conn.close()


def _connect_socket(host: str, port: int, timeout: float) -> socket.socket:
    """带坏 IP 冷却与多 IP 快速切换的 TCP 连接。

    部分报社域名（如 ``epaper.gmw.cn``）的轮询 DNS 中混有不可达 IP，标准库
    ``socket.create_connection`` 会在第一条死 IP 上白等完整个 ``timeout``（25s）。
    多 IP 时每条给较短的探测超时（最多 4s），失败即加入临时冷却表，后续请求直连健康 IP。
    """
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    candidates: list[tuple[int, int, int, tuple[Any, ...]]] = []
    seen_addrs: set[tuple[Any, ...]] = set()
    for fam, socktype, proto, _canon, sockaddr in infos:
        if sockaddr in seen_addrs:
            continue
        seen_addrs.add(sockaddr)
        candidates.append((fam, socktype, proto, sockaddr))
    if not candidates:
        raise OSError(f"无法解析主机：{host}")

    now = time.monotonic()
    healthy: list[tuple[int, int, int, tuple[Any, ...]]] = []
    cooling: list[tuple[int, int, int, tuple[Any, ...]]] = []
    with _BAD_IPS_LOCK:
        expired = [k for k, deadline in _BAD_IPS.items() if deadline <= now]
        for k in expired:
            del _BAD_IPS[k]
        for item in candidates:
            ip = str(item[3][0])
            if _BAD_IPS.get((ip, port), 0.0) > now:
                cooling.append(item)
            else:
                healthy.append(item)

    ordered = healthy + cooling
    per_ip_timeout = min(4.0, max(1.5, float(timeout) / 3.0)) if len(ordered) > 1 else float(timeout)
    last_exc: OSError | None = None
    for fam, socktype, proto, sockaddr in ordered:
        ip = str(sockaddr[0])
        sock = socket.socket(fam, socktype, proto)
        sock.settimeout(per_ip_timeout)
        try:
            sock.connect(sockaddr)
        except OSError as exc:
            last_exc = exc
            with contextlib.suppress(OSError):
                sock.close()
            with _BAD_IPS_LOCK:
                _BAD_IPS[(ip, port)] = time.monotonic() + _BAD_IP_TTL
            continue
        with _BAD_IPS_LOCK:
            _BAD_IPS.pop((ip, port), None)
        sock.settimeout(float(timeout))
        return sock
    assert last_exc is not None
    raise last_exc


def _open_conn(
    scheme: str, host: str, port: int, timeout: float, context: ssl.SSLContext
) -> http.client.HTTPConnection:
    sock = _connect_socket(host, port, timeout)
    if scheme == "https":
        try:
            ssl_sock = context.wrap_socket(sock, server_hostname=host)
        except BaseException:
            with contextlib.suppress(OSError):
                sock.close()
            raise
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=context
        )
        conn.sock = ssl_sock
        return conn
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    conn.sock = sock
    return conn


def _borrow_conn(
    scheme: str, host: str, port: int, timeout: float, context: ssl.SSLContext
) -> tuple[http.client.HTTPConnection, bool]:
    key = (scheme, host, port, id(context) if scheme == "https" else 0)
    now = time.monotonic()
    stale: list[http.client.HTTPConnection] = []
    chosen: http.client.HTTPConnection | None = None
    with _POOL_LOCK:
        bucket = _CONN_POOL.get(key)
        while bucket:
            candidate, ts = bucket.pop()
            if now - ts <= _POOL_IDLE_TTL and candidate.sock is not None:
                chosen = candidate
                break
            stale.append(candidate)
    for conn in stale:
        with contextlib.suppress(Exception):
            conn.close()
    if chosen is not None:
        chosen.timeout = timeout
        with contextlib.suppress(OSError):
            if chosen.sock is not None:
                chosen.sock.settimeout(float(timeout))
        return chosen, True
    return _open_conn(scheme, host, port, timeout, context), False


def _return_conn(
    scheme: str, host: str, port: int, context: ssl.SSLContext, conn: http.client.HTTPConnection
) -> None:
    if conn.sock is None:
        return
    limit = max(1, int(getattr(config, "HOST_CONCURRENCY", 4) or 4))
    key = (scheme, host, port, id(context) if scheme == "https" else 0)
    evicted: http.client.HTTPConnection | None = None
    with _POOL_LOCK:
        bucket = _CONN_POOL.setdefault(key, [])
        if len(bucket) >= limit:
            evicted, _ts = bucket.pop(0)
        bucket.append((conn, time.monotonic()))
    if evicted is not None:
        with contextlib.suppress(Exception):
            evicted.close()


def _drop_host_pool(scheme: str, host: str, port: int) -> None:
    with _POOL_LOCK:
        keys = [k for k in _CONN_POOL if k[:3] == (scheme, host, port)]
        conns = [conn for k in keys for conn, _ts in _CONN_POOL.pop(k, ())]
    for conn in conns:
        with contextlib.suppress(Exception):
            conn.close()


def _host_semaphore(url: str) -> threading.BoundedSemaphore | None:
    """按 ``(scheme, netloc)`` 限制单域名并发，防止全局并发打垮单台报社服务器。"""
    limit = int(getattr(config, "HOST_CONCURRENCY", 0) or 0)
    if limit <= 0:
        return None
    parsed = urlsplit(url)
    if not parsed.netloc:
        return None
    key = (parsed.scheme.lower(), parsed.netloc.lower())
    with _HOST_SEMAPHORES_LOCK:
        global _HOST_SEMAPHORE_LIMIT
        if limit != _HOST_SEMAPHORE_LIMIT:
            _HOST_SEMAPHORES.clear()
            _HOST_SEMAPHORE_LIMIT = limit
        sem = _HOST_SEMAPHORES.get(key)
        if sem is None:
            sem = threading.BoundedSemaphore(limit)
            _HOST_SEMAPHORES[key] = sem
        return sem


def _charset_from_content_type(content_type: str) -> str:
    if not content_type:
        return ""
    match = _CONTENT_TYPE_CHARSET_RE.search(content_type)
    return match.group(1).strip() if match else ""


def _sniff_charset(data: bytes) -> str:
    """从正文前 ~8KB 的 ``<meta>`` 声明里嗅探 charset（字节级，大小写不敏感）。"""
    if not data:
        return ""
    match = _META_CHARSET_RE.search(data[:8192])
    if not match:
        return ""
    return match.group(1).decode("ascii", "ignore").strip()


def _decode_bytes(data: bytes, charset: str) -> str:
    """按优先级解码，且**绝不静默丢字节**。

    声明的 charset 优先；缺失或不可用时严格 UTF-8，失败再退 GB18030，最后用
    ``errors="replace"`` 兜底（乱码字符可见，而不是被整段丢弃造成假阴性）。
    """
    if charset:
        try:
            return data.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gb18030")
        except (LookupError, UnicodeDecodeError):
            return data.decode("utf-8", "replace")


def _decode_body(data: bytes, content_encoding: str, content_type: str = "") -> str:
    encoding = (content_encoding or "").lower()
    if "gzip" in encoding:
        with contextlib.suppress(OSError):
            data = gzip.decompress(data)
    elif "deflate" in encoding:
        try:
            data = zlib.decompress(data)
        except zlib.error:
            # 部分服务器发的是裸 deflate（无 zlib 头），回退到 raw 模式
            with contextlib.suppress(zlib.error):
                data = zlib.decompress(data, -zlib.MAX_WBITS)
    charset = _charset_from_content_type(content_type) or _sniff_charset(data)
    return _decode_bytes(data, charset)


def _pooled_http_once(
    url: str, timeout: int, context: ssl.SSLContext, *, explicit_context: bool
) -> tuple[int, str]:
    """用 HTTP/1.1 Keep-Alive 连接池与坏 IP 快速切换抓取一次（纯标准库）。"""
    current_url = url
    headers = {
        "User-Agent": config.UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            parts = urlsplit(current_url)
            scheme = parts.scheme.lower()
            host = parts.hostname
            if scheme not in ("http", "https") or not host:
                return 0, ""
            port = parts.port or (443 if scheme == "https" else 80)
            req_path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            req_headers = dict(headers)
            req_headers["Host"] = parts.netloc

            conn, from_pool = _borrow_conn(scheme, host, port, float(timeout), context)
            try:
                try:
                    conn.request("GET", req_path, headers=req_headers)
                    resp = conn.getresponse()
                except (
                    http.client.RemoteDisconnected,
                    http.client.BadStatusLine,
                    ConnectionResetError,
                    BrokenPipeError,
                    ssl.SSLEOFError,
                    OSError,
                ):
                    if not from_pool:
                        raise
                    with contextlib.suppress(Exception):
                        conn.close()
                    conn = _open_conn(scheme, host, port, float(timeout), context)
                    conn.request("GET", req_path, headers=req_headers)
                    resp = conn.getresponse()

                data = resp.read()
                status = int(resp.status)
                encoding = resp.getheader("Content-Encoding", "") or ""
                content_type = resp.getheader("Content-Type", "") or ""
                conn_hdr = (resp.getheader("Connection", "") or "").lower()
                location = resp.getheader("Location")
                reusable = resp.version != 10 and "close" not in conn_hdr and not resp.will_close
            except BaseException:
                with contextlib.suppress(Exception):
                    conn.close()
                raise

            if reusable:
                _return_conn(scheme, host, port, context, conn)
            else:
                with contextlib.suppress(Exception):
                    conn.close()

            if status in (301, 302, 303, 307, 308) and location:
                current_url = urljoin(current_url, location)
                continue
            return status, _decode_body(data, encoding, content_type)
        _LOGGER.info("标准库抓取失败 %s：重定向次数过多", url)
        return 0, ""
    except (http.client.HTTPException, OSError, ValueError) as exc:
        if not explicit_context and tls.should_retry_relaxed(exc):
            _LOGGER.info("握手被拒，改用放宽套件重试 %s：%s", url, exc)
            parts = urlsplit(current_url)
            if parts.hostname:
                _drop_host_pool(
                    parts.scheme.lower(),
                    parts.hostname,
                    parts.port or (443 if parts.scheme.lower() == "https" else 80),
                )
            code, body = _pooled_http_once(url, timeout, tls.relaxed_context(), explicit_context=True)
            if code == 0:
                return 0, ""
            tls.note_relaxed_works()
            return code, body
        _LOGGER.info("标准库抓取失败 %s：%s", url, exc)
        return 0, ""


def _stdlib_once(url: str, timeout: int, context: ssl.SSLContext | None = None) -> tuple[int, str]:
    """用标准库抓一次（HTTP/1.1，自动跟随重定向与 gzip/deflate）。

    ``context`` 省略时取 :func:`tls.context_for_fetch`；显式传入则用它且**不再重试**
    放宽套件（放宽重试只发生一次，「显式传上下文」天然兜住递归）。
    """
    if context is None:
        context = tls.context_for_fetch()
        explicit_context = False
    else:
        explicit_context = True

    scheme = urlsplit(url).scheme.lower()
    proxies = urllib.request.getproxies()
    has_proxy = bool(proxies.get(scheme) or proxies.get("all"))
    if urllib.request.urlopen is _ORIG_URLOPEN and not has_proxy:
        return _pooled_http_once(url, timeout, context, explicit_context=explicit_context)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            data = response.read()
            encoding = response.headers.get("Content-Encoding", "")
            content_type = response.headers.get("Content-Type", "")
            return int(response.status), _decode_body(data, encoding, content_type)
    except urllib.error.HTTPError as exc:
        # 站点侧返回的 4xx/5xx 是有效结果，不能当成网络层失败
        with exc:
            data = exc.read() or b""
            encoding = exc.headers.get("Content-Encoding", "") if exc.headers else ""
            content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return int(exc.code), _decode_body(data, encoding, content_type)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        if not explicit_context and tls.should_retry_relaxed(exc):
            # 只有「还没验证书就断了」才降级重试一次；证书类失败降级无用，直接放弃。
            _LOGGER.info("握手被拒，改用放宽套件重试 %s：%s", url, exc)
            code, body = _stdlib_once(url, timeout, tls.relaxed_context())
            if code == 0:
                return 0, ""
            tls.note_relaxed_works()
            return code, body
        _LOGGER.info("标准库抓取失败 %s：%s", url, exc)
        return 0, ""


def curl_available() -> bool:
    """系统是否装有 curl。

    缺它不代表抓不了（标准库是主路径，TLS 边角由 :mod:`newspaper_auto.tls` 兜），
    但失败提示必须区分「压根没有 curl」与「curl 也失败」—— 否则在没有 curl 的机器
    （Win7、精简系统、容器）上会一直把排查方向带偏到「网络异常」上。
    """
    return shutil.which("curl") is not None


def _curl_once(url: str, timeout: int) -> tuple[int, str]:
    """用系统 curl 抓一次（显式 ``--http1.1``，与旧行为一致）。"""
    connect_timeout = min(10, max(1, int(timeout)))
    cmd = [
        "curl",
        "-sL",
        "--http1.1",
        "--compressed",
        "--connect-timeout",
        str(connect_timeout),
        "-m",
        str(timeout),
        "-A",
        config.UA,
        "-H",
        "Accept-Language: zh-CN,zh;q=0.9",
        "-w",
        "\n@@HTTP:%{http_code}",
        url,
    ]
    try:
        # 必须抑制控制台窗口：无窗口版 exe（父进程没有控制台）里，curl 每被派生
        # 一次就会弹一个黑窗口，而扫描是逐页调用的（默认 8 并发）。见 procutil。
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 8, **procutil.hidden_console())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0, ""
    raw = proc.stdout or b""
    match = _HTTP_CODE_RE.search(raw)
    if match:
        code = int(match.group(1))
        body_bytes = raw[: match.start()]
    else:
        code, body_bytes = 0, raw
    # curl 的 --compressed 已解压正文；stdout 只含正文，无法拿到响应头，故按正文内容解码。
    return code, _decode_body(body_bytes, "")


def _fetch_once(url: str, timeout: int) -> tuple[int, str]:
    """抓一次：受单域名并发限制，标准库失败时用 curl 兜底（若系统装有 curl）。"""
    sem = _host_semaphore(url)
    guard = sem if sem is not None else contextlib.nullcontext()
    with guard:
        code, body = _stdlib_once(url, timeout)
        if code != 0:
            return code, body
        if curl_available():
            curl_code, curl_body = _curl_once(url, timeout)
            if curl_code != 0:
                return curl_code, curl_body
        return 0, ""


def _should_retry(code: int, body: str) -> bool:
    """判断一次抓取结果是否值得重试。

    只重试「可能是瞬时故障」的情况：网络层失败（code=0）、限流 429、5xx，以及
    HTTP 200 但正文短于 ``MIN_HTML_BYTES``。其余 4xx（404/403/410…）是站点给出的
    确定性答案，重试只会白白拖慢扫描并加重对端压力，故立即返回。
    """
    if code == 0:
        return True
    if code == 429 or 500 <= code < 600:
        return True
    return code == 200 and len(body) < config.MIN_HTML_BYTES


def fetch(
    url: str, *, timeout: int = config.FETCH_TIMEOUT, retries: int = config.FETCH_RETRIES
) -> tuple[int, str]:
    """抓取 ``url``，返回 ``(http_code, body)``。

    带重试：报社站点在并发压力下偶发空响应，单次失败不代表当天报纸缺失。
    成功条件为 HTTP 200 且正文不小于 ``MIN_HTML_BYTES``。
    """
    code, body = 0, ""
    for attempt in range(retries + 1):
        code, body = _fetch_once(url, timeout)
        if code == 200 and len(body) >= config.MIN_HTML_BYTES:
            return code, body
        if not _should_retry(code, body):
            return code, body
        if attempt < retries:
            # 未实现 Retry-After：六家站点均不返回该头；在指数退避上加抖动，
            # 避免多工作线程在同一时刻齐刷刷唤醒并再次撞向同一站点。
            delay = config.RETRY_BACKOFF * (attempt + 1) * random.uniform(0.7, 1.3)
            time.sleep(delay)
    _LOGGER.warning("抓取失败 %s：HTTP %s（长度 %d，已重试 %d 次）", url, code, len(body), retries)
    return code, body
