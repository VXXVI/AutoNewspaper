"""TLS 兼容层：随包根证书 + **按需**放宽密码套件。

为什么需要这一层
----------------
六家里只有 ``paper.jyb.cn``（中国教育报）会在 TLS 握手阶段就把连接掐掉，而**两台机器
卡在完全不同的环节**，补根证书只能治好其中一半：

============  ==================  ==========================================  ==============
环境          OpenSSL             站点给出的报错                              卡在哪一步
============  ==================  ==========================================  ==============
Win10/11 主线  3.x（SECLEVEL=2）   ``SSLV3_ALERT_HANDSHAKE_FAILURE``            密码套件
Win7 便携包    1.1.1（SECLEVEL=1） ``CERTIFICATE_VERIFY_FAILED ... self         证书链
                                    signed certificate in certificate chain``
============  ==================  ==========================================  ==============

- 主机：站点只提供 ``AES128-SHA``（RSA 密钥交换 + SHA1）这类老套件，OpenSSL 3 的默认
  ``SECLEVEL=2`` 直接禁用 —— **连证书都还没换到**，往根库里加锚点毫无用处；
- Win7：套件本来就放得过，但该站把**整条链（含自签根）**都发了出来，链顶是
  ``USERTrust RSA Certification Authority``，而 Win7 的系统根库里没有这个锚点。

所以这里做两件事，缺一不可：

1. :func:`strict_context` —— 系统根库之上**追加**随包 ``cacert.pem``（Mozilla，与
   certifi 同源），补的是「系统根库缺的锚点」，不替换系统信任；
2. :func:`relaxed_context` —— 把安全级别降到 1（``DEFAULT@SECLEVEL=1``），补的是
   「系统默认禁用但站点仍在用」的老套件。**证书校验始终开着**，降的只是套件强度。

降级是**按需**的：:func:`should_retry_relaxed` 只在「失败发生在握手层、且不是证书
问题」时才放行一次重试，并且一旦放宽后成功就记下来（:func:`note_relaxed_works`），
后续请求直接用放宽上下文，省掉每次注定失败的那次握手。

不要把这些上下文改到全局 ``ssl._create_default_https_context``：那是进程级的，
会波及同一进程里所有别的 HTTPS 调用。本模块只服务于抓取层。
"""

from __future__ import annotations

import logging
import ssl
import threading
from functools import lru_cache
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

#: 随包根证书（放在包内 ``newspaper_auto/cacert.pem``，由 spec / package-data 一起带走）。
#: 刷新方式：``curl -o src/newspaper_auto/cacert.pem https://curl.se/ca/cacert.pem``
#: （curl.se 那份与 certifi 同源，都是从 Mozilla 的 NSS 证书库导出）。刷完跑一次
#: ``pytest tests/test_tls.py tests/test_packaging.py`` 即可，不需要改任何代码。
CA_BUNDLE = Path(__file__).resolve().parent / "cacert.pem"

#: 放宽后的套件表达式。``@SECLEVEL`` 语法自 OpenSSL 1.1.0 起可用，Win7 包带的
#: 1.1.1 天然支持；不支持的 OpenSSL 会在 :func:`relaxed_context` 里被捕获并退回默认套件。
RELAXED_CIPHERS = "DEFAULT@SECLEVEL=1"

# 握手被拒的特征：连接还没走到证书校验就被对端（或本地 OpenSSL）掐掉。
_HANDSHAKE_MARKERS = (
    "HANDSHAKE_FAILURE",
    "ALERT_PROTOCOL_VERSION",
    "WRONG_VERSION_NUMBER",
    "NO_SHARED_CIPHER",
    "UNSUPPORTED_PROTOCOL",
    "SSLV3_ALERT_INTERNAL_ERROR",
)

# 证书校验失败：补根证书能解决，**放宽套件不能**，所以绝不能误判成「握手被拒」。
_CERTIFICATE_MARKERS = (
    "CERTIFICATE_VERIFY_FAILED",
    "certificate verify failed",
    "SELF_SIGNED_CERT_IN_CHAIN",
    "self signed certificate in certificate chain",
    "UNABLE_TO_GET_ISSUER_CERT",
    "unable to get local issuer certificate",
    "CERTIFICATE_HAS_EXPIRED",
    "certificate has expired",
    "CERTIFICATE_UNTRUSTED",
)


def _error_text(exc: BaseException) -> str:
    """把异常摊平成一段可匹配的文本。

    ``urllib.error.URLError`` 会把真正的异常塞进 ``.reason``，``str(exc)`` 虽已包含
    它（``<urlopen error [SSL: ...]>``），但直接异常与被包装异常要拼在一起才能覆盖
    两种来源，省得以后 urllib 改了文案就判不出来。
    """
    parts = [str(exc)]
    reason = getattr(exc, "reason", None)
    if isinstance(reason, BaseException):
        parts.append(str(reason))
    return " ".join(parts)


def should_retry_relaxed(exc: BaseException) -> bool:
    """判断这次抓取失败是否值得「放宽密码套件」再试一次。

    只在**握手层**被拒时为真：那是「还没验证书就断了」，降安全级别就能续上。
    证书类失败返回 ``False`` —— 那种情况放宽套件一点用都没有，再试一次纯属白等一次
    超时（这正是 Win7 那侧的故障点，若也去降级就会一直失败到超时）。
    """
    text = _error_text(exc)
    if any(marker in text for marker in _CERTIFICATE_MARKERS):
        return False
    return any(marker in text.upper() for marker in _HANDSHAKE_MARKERS)


def _base_context() -> ssl.SSLContext:
    """默认上下文 + **追加**随包根证书。"""
    context = ssl.create_default_context()
    if not CA_BUNDLE.is_file():
        _LOGGER.warning("未找到随包根证书 %s（只用系统根库）", CA_BUNDLE)
        return context
    try:
        # 追加而非替换：系统根库仍在前，随包这批只负责「系统缺的锚点」。
        context.load_verify_locations(cafile=str(CA_BUNDLE))
    except (OSError, ssl.SSLError) as exc:
        _LOGGER.warning("加载随包根证书失败 %s：%s（只用系统根库）", CA_BUNDLE, exc)
    return context


@lru_cache(maxsize=1)
def strict_context() -> ssl.SSLContext:
    """首选上下文：系统根库 + 随包根证书，默认套件。"""
    return _base_context()


@lru_cache(maxsize=1)
def relaxed_context() -> ssl.SSLContext:
    """放宽上下文：与 :func:`strict_context` 同样的信任，只把安全级别降到 1。

    证书校验仍然是「必须通过」，降的只是套件强度（SHA1 / RSA 密钥交换仍在允许列表里）。
    """
    context = _base_context()
    try:
        context.set_ciphers(RELAXED_CIPHERS)
    except ssl.SSLError as exc:
        # 老到不支持 @SECLEVEL 的 OpenSSL：保持默认套件，别让整条抓取路径挂掉。
        _LOGGER.warning("设置放宽密码套件失败（%s）：%s（保持默认套件）", RELAXED_CIPHERS, exc)
    return context


# 「本进程所在网络确实需要放宽」这个事实。用 Event 而不是裸 bool，是为了跨线程读写
# 都是原子的（抓取默认 8 并发）。
_relaxed_is_needed = threading.Event()


def relaxed_is_needed() -> bool:
    """本进程是否已经确认需要放宽套件（确认过就直接用放宽上下文，省一次失败握手）。"""
    return _relaxed_is_needed.is_set()


def note_relaxed_works() -> None:
    """放宽后成功过一次 → 后续抓取直接用放宽上下文。"""
    if not _relaxed_is_needed.is_set():
        _LOGGER.info("本机需要放宽密码套件，后续抓取直接使用放宽上下文")
    _relaxed_is_needed.set()


def reset() -> None:
    """清掉缓存的上下文与「需要放宽」标记（测试用）。"""
    strict_context.cache_clear()
    relaxed_context.cache_clear()
    _relaxed_is_needed.clear()


def context_for_fetch() -> ssl.SSLContext:
    """本次抓取该用哪个上下文。"""
    return relaxed_context() if relaxed_is_needed() else strict_context()
