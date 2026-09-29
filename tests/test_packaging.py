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

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK_EXE_TLS = ROOT / "scripts" / "check_exe_tls.py"
SPEC = ROOT / "packaging" / "NewspaperAuto.spec"
PYPROJECT = ROOT / "pyproject.toml"
MANIFEST = ROOT / "MANIFEST.in"
CA_BUNDLE = ROOT / "src" / "newspaper_auto" / "cacert.pem"
TLS_MODULE = ROOT / "src" / "newspaper_auto" / "tls.py"


def _load_check_exe_tls():
    """按路径加载 ``scripts/check_exe_tls.py``（它不是包内模块，不走 sys.path）。"""
    spec = importlib.util.spec_from_file_location("check_exe_tls", CHECK_EXE_TLS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    """构建脚本要读 exe 的 PYZ 模块目录表确认 tls 在包里（本次事故的直接对策）。

    自检实现放在 ``scripts/check_exe_tls.py`` 独立文件里，刻意不内联 ``python -c``：
    内联版曾往 Windows runner 的 cp1252 控制台 print 中文，抛 UnicodeEncodeError 后
    脚本非零退出，v1.0.3 的两个 Windows 构建 job 双双报「exe 缺少 TLS 修复模块」，
    而 exe 其实完全正常。断言里把「不要退回内联」也钉住。
    """
    script = (ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
    assert "check_exe_tls.py" in script, "build_windows.ps1 没有调用 exe 模块清单自检"
    assert "@'" not in script, "build_windows.ps1 又把 python 代码内联回来了"
    checker = (ROOT / "scripts" / "check_exe_tls.py").read_text(encoding="utf-8")
    assert "newspaper_auto.tls" in checker, "自检脚本没要求 newspaper_auto.tls 在包里"
    assert "cacert.pem" in checker, "自检脚本没校验随包根证书是否存在"


def test_check_exe_tls_reads_the_pyz_table():
    """自检脚本的 PYZ 目录表解析要能真跑通：合成一份最小 PYZ 字节喂给它。

    这段解析是「源码修好但没进包」的唯一防线，它自己坏掉时不能靠 CI 构建才发现
    ——v1.0.3 就是自检脚本崩了、结果报了个完全错误的错因。
    """
    check_exe_tls = _load_check_exe_tls()
    import marshal
    import struct

    # 布局与真实 CArchive 一致：标记 + 4 字节魔数 + 4 字节 python 魔数 + 4 字节目录表偏移
    table = marshal.dumps([("newspaper_auto.http", False, 0, 1), ("newspaper_auto.tls", False, 0, 1)])
    header = b"PYZ\x00" + b"\x00\x00\x00\x00" + struct.pack(">I", 12)
    blob = header + table

    assert check_exe_tls.parse_pyz_modules(blob) == {"newspaper_auto.http", "newspaper_auto.tls"}
    assert check_exe_tls.parse_pyz_modules(b"not an exe at all") == set()
    # 畸形 PYZ 也必须安静地返回空集合：栈回溯冒到 PowerShell 也是非零退出，
    # 外层照样会报「exe 缺少 TLS 修复模块」，等于用一个假错因盖掉真错因。
    truncated = blob[: len(blob) - 3]
    assert check_exe_tls.parse_pyz_modules(truncated) == set()
    assert check_exe_tls.parse_pyz_modules(blob[:-1] + b"\xff") == set()
    assert "newspaper_auto.tls" in check_exe_tls.REQUIRED_MODULES


def test_check_exe_tls_detects_the_bundled_ca_bundle():
    """随包根证书必须直接验，而不是靠 /static/store.js 看板冒烟间接证明。

    数据文件不进 PYZ 目录表，而是以长度前缀字符串出现在 CArchive 自己的 TOC 里，
    **同样是明文** —— 但 PyInstaller 写的是**平台原生**分隔符，Windows 上构建出的包
    里是 ``newspaper_auto\\cacert.pem``。早先按正斜杠 grep 一无所获，正是栽在这里，
    一度误以为「数据文件没法查」，白跑了两次构建才验证清楚。
    """
    check_exe_tls = _load_check_exe_tls()
    relative = "newspaper_auto/cacert.pem"
    windows_style = b"\x1bnewspaper_auto\\cacert.pem\x00\x00"
    posix_style = b"\x1bnewspaper_auto/cacert.pem\x00\x00"

    assert check_exe_tls.has_data_file(windows_style, relative), "认不出反斜杠写法"
    assert check_exe_tls.has_data_file(posix_style, relative), "认不出正斜杠写法"
    assert not check_exe_tls.has_data_file(b"newspaper_auto\\static\\app.js", relative)
    assert "newspaper_auto/cacert.pem" in check_exe_tls.REQUIRED_DATA_FILES
    # 数据文件这一项必须是「没进包就失败」，不能是可选旁证
    source = CHECK_EXE_TLS.read_text(encoding="utf-8")
    assert "absent_data" in source and "return 1" in source
    # 输出必须纯 ASCII：CI 的 Windows 控制台是 cp1252，print 中文就是 UnicodeEncodeError
    checker_src = (ROOT / "scripts" / "check_exe_tls.py").read_text(encoding="utf-8")
    for line in checker_src.splitlines():
        for quoted in re.findall(r'"([^"]*)"|\'([^\']*)\'', line):
            text = quoted[0] or quoted[1]
            if "print" in line or "sys.stderr" in line:
                assert text.isascii(), f"自检脚本的输出含非 ASCII，cp1252 控制台会崩：{text!r}"


def test_release_workflow_checks_the_wheel_carries_the_ca_bundle():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "cacert.pem" in workflow, "release.yml 的 wheel 冒烟没验证 cacert.pem 随包"
