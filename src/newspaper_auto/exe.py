"""统一可执行入口（供 PyInstaller 打包，也可作 ``newspaper-auto`` 命令使用）。

用法::

    NewspaperAuto.exe                       起本地看板（后台 + 自动开浏览器）
    NewspaperAuto.exe scan [daily_scan.py 的参数...]
    NewspaperAuto.exe dashboard [dashboard.py 的参数...]   # --detach / --stop / --status
    NewspaperAuto.exe --version

Windows 包里有**两个** exe，源码只有这一份：

- ``NewspaperAuto.exe``：PyInstaller ``console=False``，双击**零控制台窗口**，
  直接起看板并打开浏览器；
- ``NewspaperAuto-CLI.exe``：``console=True``，承载全部命令行用法（``scan`` /
  ``dashboard`` / ``--help`` / ``--version``），也是 ``packaging/windows/*.cmd``
  调用的那一个。

无窗口版没有控制台，``sys.stdout`` / ``sys.stderr`` 可能是 ``None``，任何 ``print``
都会 ``AttributeError``。:func:`_ensure_console_streams` 负责把它们接回来
（从 cmd 调用时还能显示输出）或指向 ``os.devnull``，所以两个 exe 共用同一套代码路径。

冻结（PyInstaller 单文件 exe）运行时，默认数据目录与 ``papers.custom.json`` 跟随
**可执行文件所在目录**（便携），而不是临时解包目录或当前工作目录；因此把一个
``NewspaperAuto.exe`` 放进任意可写文件夹即可「解压即用」。

子命令后面的参数与 ``daily_scan.py`` / ``dashboard.py`` 完全一致，直接透传。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from . import __version__, cli, paths, web

_USAGE = f"""\
数字报关键词每日监测 v{__version__}

用法:
  NewspaperAuto.exe                       打开本地看板（后台运行并自动打开浏览器）
  NewspaperAuto.exe scan [参数...]        扫描一次（默认扫今天 + 昨天）
  NewspaperAuto.exe dashboard [参数...]   看板（默认前台；--detach 后台，--stop 停止）
  NewspaperAuto.exe --version             显示版本号
  NewspaperAuto.exe --help                显示本帮助

示例:
  NewspaperAuto.exe                       ← 双击即用：起看板并打开浏览器
  NewspaperAuto.exe dashboard --stop      停止后台看板
  NewspaperAuto.exe dashboard --status    查看看板是否在运行
  NewspaperAuto.exe scan --days 7 --kw 人工智能
  NewspaperAuto.exe scan --since 2026-09-20 --until 2026-09-25 --no-state

scan / dashboard 后面的参数与 daily_scan.py / dashboard.py 完全一致（详见 README）。
数据（state/、hits/）与 papers.custom.json 默认放在本 exe 所在目录。

看板默认在**后台**运行：关掉命令行窗口不会停止它，停止请用 dashboard --stop。

注意: Windows 包里还有一个带控制台的命令行版 NewspaperAuto-CLI.exe。
      无窗口版（NewspaperAuto.exe）双击即用，但**看不到任何命令行输出**；
      要看输出/排查问题，请改用 NewspaperAuto-CLI.exe。
"""

_SCAN_COMMANDS = frozenset({"scan", "daily_scan", "daily"})
_DASHBOARD_COMMANDS = frozenset({"dashboard", "web"})

# Windows 常量：AttachConsole 的父进程标识；MessageBoxW 的图标/按钮样式
_ATTACH_PARENT_PROCESS = -1
_MB_ICONERROR = 0x10


def _ensure_console_streams() -> None:
    """保证 ``sys.stdout`` / ``sys.stderr`` / ``sys.stdin`` 不是 ``None``。

    PyInstaller 的 ``console=False``（无窗口版 exe）下这三个流可能是 ``None``，
    任何 ``print`` 都会 ``AttributeError`` 把进程打崩 —— 而双击启动恰恰是最需要
    「别崩」的场景。处理顺序：

    1. 流已就绪 → 直接返回（源码运行、控制台版 exe 都走这条，零副作用）；
    2. Windows 下尝试 ``AttachConsole(ATTACH_PARENT_PROCESS)`` 接回父控制台：
       这样**从 cmd / PowerShell 调用无窗口版 exe 照样能打印**；
    3. 接不上就指向 ``os.devnull``：``print`` 变成静默 no-op，而不是崩溃。

    非 Windows 平台永远不会走到 AttachConsole 分支，因此测试里可以直接调用。
    """
    if sys.stdout is not None and sys.stderr is not None and sys.stdin is not None:
        return

    attached = False
    if sys.platform == "win32":
        try:
            import ctypes

            attached = bool(ctypes.windll.kernel32.AttachConsole(_ATTACH_PARENT_PROCESS))
        except Exception:
            attached = False

    for name, device in (("stdout", "CONOUT$"), ("stderr", "CONOUT$"), ("stdin", "CONIN$")):
        if getattr(sys, name, None) is not None:
            continue
        stream = None
        if attached:
            try:
                # 这些流要活到进程结束，不能交给 with 管理（关掉就没输出了）。
                stream = open(  # noqa: SIM115
                    device, "r" if name == "stdin" else "w", encoding="utf-8", buffering=1
                )
            except OSError:
                stream = None
        if stream is None:
            stream = open(  # noqa: SIM115 —— 同上，devnull 也要一直挂着
                os.devnull, "r" if name == "stdin" else "w", encoding="utf-8"
            )
        setattr(sys, name, stream)


def _report_fatal(exc: BaseException) -> None:
    """无窗口版启动失败时弹一个原生对话框。

    没有控制台时，异常信息只会写进 ``devnull``，用户看到的就是「双击没反应」——
    这类工单最难排查。仅在**冻结的 Windows 构建**上弹窗，源码运行与测试不受影响。
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"{type(exc).__name__}: {exc}\n\n"
            f"详细日志见程序目录下的 logs/。\n"
            f"需要命令行输出请改用 NewspaperAuto-CLI.exe。",
            f"NewspaperAuto {__version__} 启动失败",
            _MB_ICONERROR,
        )
    except Exception:
        pass


def main(argv: Sequence[str] | None = None) -> int:
    """分发子命令；返回进程退出码（供 ``SystemExit`` 使用）。"""
    # 无窗口版可能根本没有 stdout/stderr：必须在任何 print 之前接好，否则直接崩。
    _ensure_console_streams()
    # 帮助文本与日志都是中文：先按同一约定固定输出编码，
    # 否则在 cp1252 之类的 Windows 控制台上 print 会直接 UnicodeEncodeError。
    cli._configure_output_encoding()

    try:
        return _dispatch(argv)
    except SystemExit:
        raise
    except BaseException as exc:
        _report_fatal(exc)
        raise


def _dispatch(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        # 双击即用：直接起看板（后台运行 + 打开浏览器）。
        # 这样用户不必知道该双击哪个 .cmd，也不会再遇到「关掉窗口 = 服务没了」。
        # 注意 web.main 只接看板自己的选项，不要把子命令名也传进去。
        return web.main(["--open", "--detach"], default_data_dir=paths.app_dir())
    if args[0] in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if args[0] in ("-V", "--version", "version"):
        print(f"NewspaperAuto {__version__}")
        return 0

    command, rest = args[0], args[1:]
    if command in _SCAN_COMMANDS:
        return cli.main(rest, default_data_dir=paths.app_dir())
    if command in _DASHBOARD_COMMANDS:
        return web.main(rest, default_data_dir=paths.app_dir())

    print(f"未知子命令：{command}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
