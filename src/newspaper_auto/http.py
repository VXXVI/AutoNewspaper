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
import logging
import re
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import zlib

from . import config, procutil, tls

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


def _stdlib_once(url: str, timeout: int, context: ssl.SSLContext | None = None) -> tuple[int, str]:
    """用标准库抓一次（HTTP/1.1，自动跟随重定向与 gzip/deflate）。

    ``context`` 省略时取 :func:`tls.context_for_fetch`；显式传入则用它且**不再重试**
    放宽套件（放宽重试只发生一次，「显式传上下文」天然兜住递归）。
    """
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    if context is None:
        context = tls.context_for_fetch()
        explicit_context = False
    else:
        explicit_context = True
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
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if not explicit_context and tls.should_retry_relaxed(exc):
            # 只有「还没验证书就断了」才降级重试一次；证书类失败降级无用，直接放弃。
            _LOGGER.info("握手被拒，改用放宽套件重试 %s：%s", url, exc)
            code, body = _stdlib_once(url, timeout, tls.relaxed_context())
            if code == 0:
                return 0, ""
            tls.note_relaxed_works()
            return code, body
        _LOGGER.warning("标准库抓取失败 %s：%s", url, exc)
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
        # 必须抑制控制台窗口：无窗口版 exe（父进程没有控制台）里，curl 每被派生
        # 一次就会弹一个黑窗口，而扫描是逐页调用的（默认 8 并发）。见 procutil。
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 8, **procutil.hidden_console())
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
            # 未实现 Retry-After：读取它需要把响应头穿过 _fetch_once 的 (code, body)
            # 契约，而六家站点均不返回该头，收益过小，故统一按固定退避重试。
            time.sleep(config.RETRY_BACKOFF * (attempt + 1))
    return code, body
