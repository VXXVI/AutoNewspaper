"""HTTP 抓取层：统一走系统 ``curl``。

为什么用 curl 而不是 urllib/requests
------------------------------------
`epaper.gmw.cn`（光明日报数字报）的服务器在 **HTTP/2 下会直接断连**
（curl 报 ``52 Empty reply from server``），而 curl 默认会经 ALPN 协商到 h2。
因此这里显式加 ``--http1.1``；其余四家不受影响。详见 README「已知边界」。

若把抓取逻辑移植到 requests / aiohttp / Node fetch，务必先验证该站 h2 可用性，
否则会静默漏掉光明日报。
"""

from __future__ import annotations

import re
import subprocess
import time

from . import config

_HTTP_CODE_RE = re.compile(r"@@HTTP:(\d+)\s*$")


class CurlNotFoundError(RuntimeError):
    """系统缺少 curl 可执行文件。"""


def _curl_once(url: str, timeout: int) -> str:
    """执行一次 curl，返回原始 stdout（含末尾的 ``@@HTTP:<code>`` 标记）。"""
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
    except FileNotFoundError as exc:  # curl 不在 PATH
        raise CurlNotFoundError("未找到 curl 命令，请先安装后再运行（macOS 自带）") from exc
    except subprocess.TimeoutExpired:
        return ""
    return proc.stdout.decode("utf-8", "ignore")


def _split(raw: str) -> tuple[int, str]:
    """拆出 (http_code, body)。无标记则视为网络层失败（code=0）。"""
    match = _HTTP_CODE_RE.search(raw)
    code = int(match.group(1)) if match else 0
    body = raw[: raw.rfind("\n@@HTTP:")] if "\n@@HTTP:" in raw else raw
    return code, body


def fetch(
    url: str, *, timeout: int = config.FETCH_TIMEOUT, retries: int = config.FETCH_RETRIES
) -> tuple[int, str]:
    """抓取 ``url``，返回 ``(http_code, body)``。

    带重试：报社站点在并发压力下偶发空响应，单次失败不代表当天报纸缺失。
    成功条件为 HTTP 200 且正文不小于 ``MIN_HTML_BYTES``。
    """
    code, body = 0, ""
    for attempt in range(retries + 1):
        code, body = _split(_curl_once(url, timeout))
        if code == 200 and len(body) >= config.MIN_HTML_BYTES:
            return code, body
        if attempt < retries:
            time.sleep(config.RETRY_BACKOFF * (attempt + 1))
    return code, body
