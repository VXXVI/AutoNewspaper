"""TLS 兼容层测试（全部离线：不连任何真实站点）。

覆盖三件事：失败分类（能不能降级重试）、上下文内容（信任与套件）、
以及「确认需要放宽后」的行为切换。
"""

from __future__ import annotations

import ssl
import urllib.error

import pytest

from newspaper_auto import tls


@pytest.fixture(autouse=True)
def _clean_tls_state():
    """每个用例前后都清掉「本机需要放宽」这个进程级标记与上下文缓存。"""
    tls.reset()
    try:
        yield
    finally:
        tls.reset()


# ---------------------------------------------------------------- 失败分类
def test_handshake_rejection_is_retryable_with_relaxed_ciphers():
    exc = urllib.error.URLError("[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] ssl/tls alert handshake failure")
    assert tls.should_retry_relaxed(exc) is True


def test_certificate_failure_is_not_retryable_with_relaxed_ciphers():
    """Win7 那侧的故障点：降安全级别对证书链毫无用处，再试一次只是白等一次超时。"""
    exc = urllib.error.URLError(
        "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
        "self signed certificate in certificate chain (_ssl.c:1007)"
    )
    assert tls.should_retry_relaxed(exc) is False


def test_unrelated_failure_is_not_retryable():
    assert tls.should_retry_relaxed(urllib.error.URLError("[Errno 61] Connection refused")) is False
    assert tls.should_retry_relaxed(urllib.error.URLError("timed out")) is False


def test_bare_socket_error_without_wrapping_is_also_matched():
    """直接抛出的 SSLError（没有 URLError 包装）也要能认出来。"""
    assert tls.should_retry_relaxed(ssl.SSLError(1, "[SSL: TLSV1_ALERT_HANDSHAKE_FAILURE]")) is True


def test_error_text_includes_urllib_reason_attribute():
    """URLError 把真实异常塞在 .reason 里，分类不能只看 str(exc)。"""
    exc = urllib.error.URLError(ssl.SSLError(1, "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE]"))
    assert "HANDSHAKE_FAILURE" in tls._error_text(exc)


# ---------------------------------------------------------------- 上下文内容
def test_bundled_ca_file_exists_and_looks_like_a_pem_bundle():
    assert tls.CA_BUNDLE.is_file(), "随包根证书缺失：中国教育报在系统根库缺锚点的机器上抓不到"
    # 证书包里个别 CA 名字带非 ASCII（如 NetLock 的匈牙利语名），按 utf-8 读
    text = tls.CA_BUNDLE.read_text(encoding="utf-8")
    assert "-----BEGIN CERTIFICATE-----" in text
    assert text.count("-----BEGIN CERTIFICATE-----") > 50


def test_strict_context_keeps_verification_and_includes_bundle():
    context = tls.strict_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # 系统根库在前，随包这批追加进来：总数必须比只加载随包包的多
    assert context.cert_store_stats()["x509_ca"] > 100


def test_relaxed_context_keeps_verification_but_lowers_security_level():
    context = tls.relaxed_context()
    # 降的只是套件强度，证书校验必须仍然是「必须通过」
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    ciphers = context.get_ciphers()
    names = " ".join(item["name"] for item in ciphers)
    # 默认套件会被 OpenSSL 3 的 SECLEVEL=2 挡掉的老套件，放宽后应重新可用
    assert "AES128-SHA" in names, "放宽后仍没有 AES128-SHA，中国教育报握手过不去"


def test_contexts_are_cached_so_each_fetch_does_not_reload_the_bundle():
    assert tls.strict_context() is tls.strict_context()
    assert tls.relaxed_context() is tls.relaxed_context()


def test_missing_bundle_degrades_to_system_store_only(monkeypatch, caplog):
    """文件缺失不能抛异常炸掉整条抓取路径，退回系统根库并留下告警。"""
    monkeypatch.setattr(tls, "CA_BUNDLE", tls.CA_BUNDLE.with_name("不存在的证书包.pem"))
    with caplog.at_level("WARNING"):
        context = tls.strict_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert any("未找到随包根证书" in record.message for record in caplog.records)


def test_set_ciphers_failure_keeps_default_ciphers(monkeypatch):
    """老到不认 @SECLEVEL 的 OpenSSL 不能让整层失效，退回默认套件继续。"""

    def _boom(_self, _ciphers: str) -> None:
        raise ssl.SSLError(1, "no cipher match")

    monkeypatch.setattr(ssl.SSLContext, "set_ciphers", _boom)
    context = tls.relaxed_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ciphers()


# ---------------------------------------------------------------- 放宽后的行为切换
def test_context_for_fetch_starts_strict():
    assert tls.context_for_fetch() is tls.strict_context()


def test_context_for_fetch_switches_after_relaxed_works():
    assert tls.context_for_fetch() is tls.strict_context()
    tls.note_relaxed_works()
    assert tls.relaxed_is_needed() is True
    # 已确认本机需要放宽，后续请求直接放宽，省掉每次注定失败的那次握手
    assert tls.context_for_fetch() is tls.relaxed_context()


def test_reset_clears_the_sticky_flag():
    tls.note_relaxed_works()
    tls.reset()
    assert tls.relaxed_is_needed() is False
    assert tls.context_for_fetch() is tls.strict_context()


def test_note_relaxed_works_is_idempotent():
    tls.note_relaxed_works()
    tls.note_relaxed_works()
    assert tls.relaxed_is_needed() is True
