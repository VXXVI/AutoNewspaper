#!/usr/bin/env python3
"""组装 Windows 7 x64 免安装便携包。

为什么用官方 embeddable Python 3.8.10，而不是 PyInstaller：

- Python 3.9 起不再支持 Windows 7，3.8.10 是最后一个提供 Windows 二进制包的版本；
- PyInstaller 无法在 macOS / Linux 上交叉编译出 Windows 产物；
- 本项目运行时零第三方依赖，embeddable 包（含 stdlib + ssl + ctypes）足够跑。

在仓库根执行::

    python3 packaging/win7/make_package.py

产出::

    dist/NewspaperAuto-<版本>-win7-x64-portable.zip

顶层目录为 ``NewspaperAuto-Win7-x64/``，解压即用（见包内 README-Win7.txt）。
本脚本不做删除，重复运行会用全新暂存目录重新打包。
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import time
import urllib.request
import zipfile

HERE = pathlib.Path(__file__).resolve().parent  # packaging/win7/
REPO = HERE.parents[1]  # 仓库根
ASSETS = HERE / "assets"
BUILD = REPO / "build" / "win7"

NAME = "NewspaperAuto-Win7-x64"
EMBED_VERSION = "3.8.10"
EMBED_URL = f"https://www.python.org/ftp/python/{EMBED_VERSION}/python-{EMBED_VERSION}-embed-amd64.zip"
EMBED = BUILD / f"python-{EMBED_VERSION}-embed-amd64.zip"

# 从仓库根直接拷进包里的单个文件（其余整目录另见下面）
ROOT_FILES = ("daily_scan.py", "dashboard.py", "papers.custom.json", "README.md", "LICENSE")
# 模板在 assets/、输出到包根的启动脚本
ASSET_FILES = ("run.py", "start-dashboard.vbs", "scan-now.cmd", "stop-dashboard.cmd", "start-dashboard.cmd")
# 需要 CRLF 行尾的启动脚本（run.py 是 Python 源码，LF 即可）
CRLF_FILES = ("start-dashboard.vbs", "start-dashboard.cmd", "scan-now.cmd", "stop-dashboard.cmd")
# 一律不进包的名字（macOS 元数据、Python 字节码缓存）
EXCLUDED_NAMES = {"__pycache__", ".DS_Store"}


def package_version() -> str:
    sys.path.insert(0, str(REPO / "src"))
    import newspaper_auto  # noqa: PLC0415

    return newspaper_auto.__version__


def write_text(src: pathlib.Path, dst: pathlib.Path, *, crlf: bool = False, bom: bool = False) -> None:
    """写入目标文件；``crlf`` 时统一成 CRLF，``bom`` 时加 UTF-8 BOM（Win7 记事本需要）。"""
    text = src.read_text(encoding="utf-8-sig")
    if crlf:
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    dst.write_bytes((("\ufeff" if bom else "") + text).encode("utf-8"))


def fetch_embed() -> None:
    if EMBED.is_file():
        return
    print(f"下载 Python {EMBED_VERSION} embeddable …")
    urllib.request.urlretrieve(EMBED_URL, EMBED)  # noqa: S310


def stage_tree(version: str) -> pathlib.Path:
    """把包内容摊到全新暂存目录，返回 ``<stage>/NewspaperAuto-Win7-x64``。"""
    stage = BUILD / f"stage-{int(time.time())}"
    root = stage / NAME

    # 1) 官方 Python embeddable（amd64）
    py_dir = root / "python"
    py_dir.mkdir(parents=True)
    with zipfile.ZipFile(EMBED) as zf:
        zf.extractall(py_dir)

    # 2) 项目源码（剔除 __pycache__，避免带进开发机上的 .pyc）
    shutil.copytree(
        REPO / "src" / "newspaper_auto",
        root / "src" / "newspaper_auto",
        ignore=shutil.ignore_patterns(*EXCLUDED_NAMES),
    )
    for rel in ROOT_FILES:
        shutil.copy2(REPO / rel, root / rel)
    shutil.copytree(REPO / "docs", root / "docs", ignore=shutil.ignore_patterns(*EXCLUDED_NAMES))

    # 3) 便携版启动器与说明（模板在 assets/，输出到包根）
    for name in ASSET_FILES:
        if name in CRLF_FILES:
            write_text(ASSETS / name, root / name, crlf=True)
        else:
            shutil.copy2(ASSETS / name, root / name)
    write_text(ASSETS / "README-Win7.txt", root / "README-Win7.txt", crlf=True, bom=True)

    # 4) 把 <包根>/src 加进 sys.path。
    #    embeddable 的 ._pth 会开启隔离模式（忽略 PYTHONPATH！），所以必须写进文件本身；
    #    相对路径按 ._pth 所在目录解析，..\src = <包根>\src。
    (py_dir / "python38._pth").write_bytes(b"python38.zip\r\n.\r\n..\\src\r\n#import site\r\n")
    return root


def zip_tree(root: pathlib.Path, version: str) -> pathlib.Path:
    """把 ``<stage>/NewspaperAuto-Win7-x64`` 打成 zip，arcname 保留顶层目录名。"""
    base = root.parent
    out = REPO / "dist" / f"NewspaperAuto-{version}-win7-x64-portable.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix not in {".pyc", ".pyo"}
        and not any(part in EXCLUDED_NAMES or part.startswith("._") for part in p.parts)
    )
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(base).as_posix())
    print(f"已生成：{out}")
    print(f"  {len(files)} 个文件，{out.stat().st_size / 1024 / 1024:.1f} MB，顶层 {root.name}/")
    return out


def main() -> int:
    version = package_version()
    print(f"版本：{version}")
    fetch_embed()
    stage = stage_tree(version)
    zip_tree(stage, version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
