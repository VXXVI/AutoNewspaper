# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件构建配置（Windows x64）。

在仓库根目录执行::

    pyinstaller --clean --noconfirm packaging/NewspaperAuto.spec

产物：``dist/NewspaperAuto.exe``（无控制台窗口）。

Windows 包需要**两个** exe，源码只有这一份 spec —— 由环境变量
``NEWSPAPERAUTO_CONSOLE`` 选择：

- 不设置（默认）：``console=False`` → ``NewspaperAuto.exe``，双击零窗口，
  起看板 + 开浏览器；
- ``NEWSPAPERAUTO_CONSOLE=1``：``console=True`` → ``NewspaperAuto-CLI.exe``，
  承载全部命令行用法（scan / dashboard / --help / --version）。

``scripts/build_windows.ps1`` 会连着跑两遍（这也是 CI 的路径）。两遍共用同一份
Analysis 配置：静态资源、图标、排除项只有一处真相，不会因为「改了一个忘了另一个」
而漂移。

设计要点：

- 只打包 **标准库 + newspaper_auto 自身**，不引入任何第三方运行时依赖；
- 把 ``src/newspaper_auto/static/`` 作为数据文件放进包内的
  ``newspaper_auto/static``，供看板在冻结运行时读取；
- ``excludes`` 仅剔除与运行无关的大块（Tk 等），避免误伤可选依赖。

无窗口版没有 stdout/stderr，由 ``newspaper_auto.exe._ensure_console_streams()``
兜底（接回父控制台或指向 devnull），不需要在 spec 里额外处理。
"""

import os
from pathlib import Path

# PyInstaller 在执行 spec 时注入 SPECPATH = spec 所在目录（不是文件本身）。
SPEC_DIR = Path(SPECPATH).resolve()  # packaging/
PROJECT = SPEC_DIR.parent  # 仓库根
SRC = PROJECT / "src"
STATIC = SRC / "newspaper_auto" / "static"

# 控制台版：带窗口的完整命令行；否则是无窗口的「双击即用」版。
CONSOLE_BUILD = os.environ.get("NEWSPAPERAUTO_CONSOLE") == "1"
EXE_NAME = "NewspaperAuto-CLI" if CONSOLE_BUILD else "NewspaperAuto"

a = Analysis(
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(STATIC), "newspaper_auto/static")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "lib2to3", "pydoc", "doctest"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_NAME,
    # 应用图标复用看板的 favicon.ico（16→256 多尺寸，同一套画法）。
    # 同一个文件既是浏览器标签图标也是 exe/快捷方式图标，避免两处漂移。
    icon=str(STATIC / "favicon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=CONSOLE_BUILD,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
