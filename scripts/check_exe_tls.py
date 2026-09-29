# ==============================================================================
# check_exe_tls.py - 校验构建出来的 exe 里确实含本次 TLS 修复
#
# 用法（仓库根）：
#   python scripts\check_exe_tls.py dist\NewspaperAuto.exe
#
# 为什么要有这道闸门：
#   出过「源码修好了、但没进包」的事故 —— 修复只停在本地没推送，CI 从远程代码构建，
#   于是 v1.0.2 的 exe 里既没有 newspaper_auto.tls 模块、也没有随包根证书，而两边
#   都没人发现。中国教育报因此在 Win7 上整家漏报。
#
# 原理：PyInstaller 单文件 exe 的尾部 CArchive 里，PYZ 子归档的「模块目录表」是
# **裸 marshal**（没有 zlib 压缩），所以可以直接把模块名读出来，不用真跑一遍 exe。
#   - 先定位明文标记 b"PYZ\x00"；
#   - 从其后第 8~11 字节读大端 u32，得到目录表在文件里的偏移；
#   - marshal.loads 那张表，取每项的第一个元素（模块名）。
# 参见 tests/test_packaging.py 里用合成字节做的单测。
#
# 注意「数据文件」（如随包根证书 cacert.pem）**不能**这样查：CArchive 的数据项名字
# 不是明文存在 exe 里，字节 grep 不到。数据文件随包由 build_windows.ps1 后面的
# /static/store.js 看板冒烟间接证明。
#
# 输出刻意只用 ASCII：本脚本由 CI 的 Windows runner 执行，那边控制台是 cp1252，
# 之前在这里 print 中文把 stdout 撑爆成 UnicodeEncodeError，害得两个 Windows 构建
# job 双双假失败（日志里报的还是「exe 缺少 TLS 修复模块」，极具误导性）。
# ==============================================================================

from __future__ import annotations

import marshal
import struct
import sys
from pathlib import Path

# 修复必须在包内的模块清单（新增其中任何一个模块都要同步这里）
REQUIRED_MODULES = ("newspaper_auto.tls", "newspaper_auto.http", "newspaper_auto.scanner")

# 只有在「目录表里连老模块都没有」时，才判定是解析器本身失效（而不是缺修复）
_SANITY_MODULE = "newspaper_auto.scanner"

# 「数据文件」随包的旁证：源码里必须有随包根证书，否则 Win7 会整家抓不到中国教育报
CA_BUNDLE = Path(__file__).resolve().parents[1] / "src" / "newspaper_auto" / "cacert.pem"


def parse_pyz_modules(data: bytes) -> set:
    """从 exe 字节里读出 PYZ 目录表中的模块名集合；找不到 PYZ 返回空集合。"""
    marker = data.find(b"PYZ\x00")
    if marker < 0:
        return set()
    # 偏移量紧跟在 "PYZ\x00" + 4 字节魔数 + 4 字节 python 魔数之后，故从 +8 取
    offset = struct.unpack(">I", data[marker + 8 : marker + 12])[0]
    table = marshal.loads(data[marker + offset :])
    return {entry[0] for entry in table if isinstance(entry, tuple) and isinstance(entry[0], str)}


def main(argv: list) -> int:
    if len(argv) != 1:
        print("usage: check_exe_tls.py <path-to-exe>", file=sys.stderr)
        return 2
    exe = Path(argv[0])
    modules = parse_pyz_modules(exe.read_bytes())
    if not modules:
        print(f"FAIL: no PYZ module table found in {exe}; cannot verify", file=sys.stderr)
        return 1
    if _SANITY_MODULE not in modules:
        # 目录表都读出来了却连一直在的模块都没有 → 是解析器/格式变了，不是包缺东西
        print(
            f"FAIL: parsed {len(modules)} modules but {_SANITY_MODULE} is absent, "
            "so the parser itself is broken",
            file=sys.stderr,
        )
        return 1
    missing = sorted(name for name in REQUIRED_MODULES if name not in modules)
    if missing:
        print(
            f"FAIL: exe is missing {', '.join(missing)} -- the build input was not this source tree",
            file=sys.stderr,
        )
        return 1
    if not CA_BUNDLE.is_file():
        print(f"FAIL: {CA_BUNDLE} is missing from the source tree", file=sys.stderr)
        return 1
    required = ", ".join(REQUIRED_MODULES)
    print(f"OK: {len(modules)} modules in PYZ, TLS fix present (required: {required})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
