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
"""

from __future__ import annotations

import contextlib
import gzip
import logging
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zlib

from . import config

_LOGGER = logging.getLogger(__name__)

# ``-w "\n@@HTTP:%{http_code}"`` 追加的状态码标记；curl 路径在**字节**层面解析。
_HTTP_CODE_RE = re.compile(rb"@@HTTP:(\d+)")
# 响应头 ``Content-Type`` 里的 charset（如 ``text/html; charset=gbk``）
_CONTENT_TYPE_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)
# HTML 内声明：``<meta charset="gb2312">`` 或
# ``<meta http-equiv="Content-Type" content="text/html; charset=gbk">``
_META_CHARSET_RE = re.compile(rb"<meta[^>]*?charset\s*=\s*[\"']?\s*([a-zA-Z0-9_.:-]+)", re.I)


def _charset_from_content_type(content_type: str) -> str:
    if not content_type:
        return ""
    match = _CONTENT_TYPE_CHARSET_RE.search(content_type)
    return match.group(1).strip() if match else ""


def _sniff_charset(data: bytes) -> str:
    """从正文前 ~2KB 的 ``<meta>`` 声明里嗅探 charset（字节级，大小写不敏感）。"""
    if not data:
        return ""
    match = _META_CHARSET_RE.search(data[:2048])
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


def _stdlib_once(url: str, timeout: int) -> tuple[int, str]:
    """用标准库抓一次（HTTP/1.1，自动跟随重定向与 gzip/deflate）。"""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _LOGGER.warning("标准库抓取失败 %s：%s", url, exc)
        return 0, ""


def _curl_available() -> bool:
    return shutil.which("curl") is not None


def _curl_once(url: str, timeout: int) -> tuple[int, str]:
    """用系统 curl 抓一次（显式 ``--http1.1``，与旧行为一致）。"""
    cmd = [
        "curl",
        "-sL",
        "--http1.1",
        "--compressed",
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
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 8)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0, ""
    raw = proc.stdout or b""
    marker = b"\n@@HTTP:"
    idx = raw.rfind(marker)
    if idx >= 0:
        match = _HTTP_CODE_RE.search(raw, idx)
        code = int(match.group(1)) if match else 0
        body_bytes = raw[:idx]
    else:
        code, body_bytes = 0, raw
    # curl 的 --compressed 已解压正文；stdout 只含正文，无法拿到响应头，故按正文内容解码。
    return code, _decode_body(body_bytes, "")


def _fetch_once(url: str, timeout: int) -> tuple[int, str]:
    """抓一次：标准库失败时用 curl 兜底（若系统装有 curl）。"""
    code, body = _stdlib_once(url, timeout)
    if code != 0:
        return code, body
    if _curl_available():
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
            # 未实现 Retry-After：读取它需要把响应头穿过 _fetch_once 的 (code, body)
            # 契约，而六家站点均不返回该头，收益过小，故统一按固定退避重试。
            time.sleep(config.RETRY_BACKOFF * (attempt + 1))
    return code, body
