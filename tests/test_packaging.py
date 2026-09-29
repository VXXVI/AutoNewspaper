"""打包链路的回归闸门：随包根证书必须一路声明到位。

背景
----
v1.0.2 漏发过一次「修好的代码没进包」：``tls.py`` 与 ``cacert.pem`` 都在源码里、
``NewspaperAuto.spec`` 也声明了，但**那次构建用的不是这份源码**（修复提交没推送，
CI 从远程代码构建），于是 exe 里既没有 ``newspaper_auto.tls`` 模块、也没有根证书，
中国教育报在 Win7 上整家 0 版面。

这类事故在 CI 上唯一能提前抓住的形态是「**声明漏了**」：spec 没列数据文件、
wheel 没收 ``cacert.pem``、sdist 没带 ``cacert.pem``。三处声明各有各的入口，
少改一处就静默退化，所以在这里全部钉死。
（产物里到底有没有，靠 ``scripts/build_windows.ps1`` 的 exe 模块清单自检与
release.yml 的 wheel 冒烟。）
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "NewspaperAuto.spec"
PYPROJECT = ROOT / "pyproject.toml"
MANIFEST = ROOT / "MANIFEST.in"
CA_BUNDLE = ROOT / "src" / "newspaper_auto" / "cacert.pem"
TLS_MODULE = ROOT / "src" / "newspaper_auto" / "tls.py"


def _toml_section(text: str, header: str) -> str:
    """取出一个 TOML 表（``[header]`` 到下一个 ``[`` 之前）的原文。

    刻意不引 ``tomllib``：它要 3.11+，而 CI 矩阵里有 3.9；而这里只需要「这一节里
    有没有某个条目」，正则足够，也省掉一个仅测试期依赖。
    """
    match = re.search(rf"^\[{re.escape(header)}\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    assert match is not None, f"pyproject.toml 里没有 [{header}] 这一节"
    return match.group(1)


def test_ca_bundle_is_committed():
    """证书包是二进制风格的文本文件，漏提交时 CI 必须当场失败而不是运行时才发现。"""
    assert CA_BUNDLE.is_file(), "src/newspaper_auto/cacert.pem 缺失"
    assert CA_BUNDLE.stat().st_size > 100_000, "证书包明显不完整"
    # 证书包里个别 CA 名字带非 ASCII（如 NetLock 的匈牙利语名），按 utf-8 读
    text = CA_BUNDLE.read_text(encoding="utf-8")
    assert text.count("-----BEGIN CERTIFICATE-----") > 50


def test_tls_module_is_committed():
    assert TLS_MODULE.is_file(), "src/newspaper_auto/tls.py 缺失"


def test_spec_ships_the_ca_bundle():
    """PyInstaller 单文件 exe 必须带上 cacert.pem（Win7 系统根库缺锚点）。"""
    spec = SPEC.read_text(encoding="utf-8")
    assert re.search(r"CA_BUNDLE\s*=\s*.*cacert\.pem", spec), "spec 里没有定义 CA_BUNDLE"
    datas = re.search(r"datas\s*=\s*\[(.*?)\]", spec, re.S)
    assert datas is not None, "spec 里找不到 datas"
    assert "cacert.pem" in datas.group(1) or "CA_BUNDLE" in datas.group(1), (
        "spec 的 datas 没有带上 cacert.pem：系统根库缺锚点的机器会整家抓不到中国教育报"
    )


def test_wheel_carries_the_ca_bundle():
    section = _toml_section(PYPROJECT.read_text(encoding="utf-8"), "tool.setuptools.package-data")
    assert "cacert.pem" in section, "wheel 的 package-data 没带 cacert.pem"


def test_sdist_carries_the_ca_bundle():
    manifest = MANIFEST.read_text(encoding="utf-8")
    assert re.search(r"^include\s+src/newspaper_auto/cacert\.pem\s*$", manifest, re.M), (
        "sdist 清单没带 cacert.pem：从 sdist 装出来的环境抓不到中国教育报"
    )


def test_windows_build_script_verifies_the_exe_contains_the_fix():
    """构建脚本要读 exe 的 PYZ 模块目录表确认 tls 在包里（本次事故的直接对策）。"""
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "newspaper_auto.tls" in script, "build_windows.ps1 没有校验 exe 是否含 TLS 修复模块"
    assert "PYZ" in script, "build_windows.ps1 里的模块清单自检实现被删了"
    assert "cacert.pem" in script, "build_windows.ps1 没有校验随包根证书是否存在"


def test_release_workflow_checks_the_wheel_carries_the_ca_bundle():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "cacert.pem" in workflow, "release.yml 的 wheel 冒烟没验证 cacert.pem 随包"
