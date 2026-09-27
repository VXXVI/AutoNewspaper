# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件构建配置（Windows x64）。

在仓库根目录执行::

    pyinstaller --clean --noconfirm packaging/NewspaperAuto.spec

产物：``dist/NewspaperAuto.exe``。

设计要点：

- 只打包 **标准库 + newspaper_auto 自身**，不引入任何第三方运行时依赖；
- 把 ``src/newspaper_auto/static/`` 作为数据文件放进包内的
  ``newspaper_auto/static``，供看板在冻结运行时读取；
- ``excludes`` 仅剔除与运行无关的大块（Tk 等），避免误伤可选依赖。

Windows 正式分发请用 ``scripts/build_windows.ps1``（它还会做冒烟测试并组装 zip）。
"""

from pathlib import Path

# PyInstaller 在执行 spec 时注入 SPECPATH = spec 所在目录（不是文件本身）。
SPEC_DIR = Path(SPECPATH).resolve()  # packaging/
PROJECT = SPEC_DIR.parent  # 仓库根
SRC = PROJECT / "src"
STATIC = SRC / "newspaper_auto" / "static"

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
    name="NewspaperAuto",
    # 应用图标复用看板的 favicon.ico（16→256 多尺寸，小尺寸是简化版画法）。
    # 同一个文件既是浏览器标签图标也是 exe/快捷方式图标，避免两处漂移。
    icon=str(STATIC / "favicon.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
