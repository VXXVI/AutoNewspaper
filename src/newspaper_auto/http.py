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
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import zlib

from . import config

_HTTP_CODE_RE = re.compile(r"@@HTTP:(\d+)\s*$")


def _decode_body(data: bytes, content_encoding: str) -> str:
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
    return data.decode("utf-8", "ignore")


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
            return int(response.status), _decode_body(data, encoding)
    except urllib.error.HTTPError as exc:
        # 站点侧返回的 4xx/5xx 是有效结果，不能当成网络层失败
        with exc:
            data = exc.read() or b""
            encoding = exc.headers.get("Content-Encoding", "") if exc.headers else ""
        return int(exc.code), _decode_body(data, encoding)
    except (urllib.error.URLError, OSError, ValueError):
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
    raw = proc.stdout.decode("utf-8", "ignore")
    match = _HTTP_CODE_RE.search(raw)
    code = int(match.group(1)) if match else 0
    body = raw[: raw.rfind("\n@@HTTP:")] if "\n@@HTTP:" in raw else raw
    return code, body


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
        if attempt < retries:
            time.sleep(config.RETRY_BACKOFF * (attempt + 1))
    return code, body
