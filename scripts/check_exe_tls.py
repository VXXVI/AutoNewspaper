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
# 「数据文件」（如随包根证书 cacert.pem）走另一条路：它们不进 PYZ 目录表，而是以
# 长度前缀字符串出现在 CArchive 自己的 TOC 里，**同样是明文**。实测 v1.0.4 的 exe 里
# 就有 `newspaper_auto\cacert.pem`（反斜杠！），v1.0.1 的 exe 里一次都没有。
# 所以不必再靠 /static/store.js 看板冒烟间接证明 —— 直接查更硬。
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

# 随包数据文件清单（相对包根，含目录分隔符）：漏了就只剩系统根库
REQUIRED_DATA_FILES = ("newspaper_auto/cacert.pem",)

# 源码里必须存在的对应文件（构建输入自检，防止拿错工作目录构建）
CA_BUNDLE = Path(__file__).resolve().parents[1] / "src" / "newspaper_auto" / "cacert.pem"


def has_data_file(data: bytes, relative: str) -> bool:
    """exe 的 CArchive TOC 里是否声明了这个数据文件。

    PyInstaller 生成的 TOC 项名用**平台原生**分隔符：Windows 上构建就是反斜杠
    （``newspaper_auto\\cacert.pem``），所以两种写法都要接受，否则在 Windows 上
    构建出的包会被自己判成「缺 cacert.pem」——正是本脚本要消灭的那类假失败。
    """
    normalised = relative.replace("/", "\\")
    return data.find(relative.encode("utf-8")) >= 0 or data.find(normalised.encode("utf-8")) >= 0


def parse_pyz_modules(data: bytes) -> set:
    """从 exe 字节里读出 PYZ 目录表中的模块名集合；读不出就返回空集合。

    这里刻意**吞掉所有解析异常**并统一返回空集合：调用方据此报「读不到目录表」，
    而不是让栈回溯一路冒到 PowerShell —— 那条路径同样是非零退出，外层那句
    `Die 'exe 缺少 TLS 修复模块'` 又会把「解析失败」说成「包里没修复」。
    """
    marker = data.find(b"PYZ\x00")
    if marker < 0 or len(data) < marker + 16:
        return set()
    try:
        # 偏移量紧跟在 "PYZ\x00" + 4 字节魔数 + 4 字节 python 魔数之后，故从 +8 取
        offset = struct.unpack(">I", data[marker + 8 : marker + 12])[0]
        table = marshal.loads(data[marker + offset :])
        return {entry[0] for entry in table if isinstance(entry, tuple) and isinstance(entry[0], str)}
    except (struct.error, ValueError, TypeError, EOFError, MemoryError):
        return set()


def main(argv: list) -> int:
    if len(argv) != 1:
        print("usage: check_exe_tls.py <path-to-exe>", file=sys.stderr)
        return 2
    exe = Path(argv[0])
    raw = exe.read_bytes()
    modules = parse_pyz_modules(raw)
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
    absent_data = sorted(name for name in REQUIRED_DATA_FILES if not has_data_file(raw, name))
    if absent_data:
        # 数据文件漏进包 = 只剩系统根库，Win7 一类缺锚点的机器会整家抓不到中国教育报
        print(
            f"FAIL: exe TOC does not ship {', '.join(absent_data)} "
            "-- such machines would miss the paper entirely",
            file=sys.stderr,
        )
        return 1
    required = ", ".join(REQUIRED_MODULES)
    data = ", ".join(REQUIRED_DATA_FILES)
    print(f"OK: {len(modules)} modules in PYZ, TLS fix present (required: {required}; data: {data})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
