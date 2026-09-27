"""统一可执行入口（供 PyInstaller 打包，也可作 ``newspaper-auto`` 命令使用）。

用法::

    NewspaperAuto.exe                       起本地看板（后台 + 自动开浏览器）
    NewspaperAuto.exe scan [daily_scan.py 的参数...]
    NewspaperAuto.exe dashboard [dashboard.py 的参数...]   # --detach / --stop / --status
    NewspaperAuto.exe --version

Windows 包里**只有一个** exe：``NewspaperAuto.exe``，PyInstaller ``console=False``，
双击**零控制台窗口**，直接起看板并打开浏览器。

为什么不拆成「无窗口版 + 控制台版」两个 exe：PE 文件的 ``Subsystem`` 字段只能二选一
（``GUI`` 双击不弹窗但 shell 不等它；``CUI`` 终端里正常但双击必弹窗），一个 exe 做不到
两全。取舍见下面 :data:`_USAGE` 里的说明：主路径是双击看板，命令行作为次要路径保留，
并用 :func:`_run_scan` 自己写 ``logs/scan-YYYY-MM-DD.log`` 补上「无控制台就拿不到输出」
这个短板 —— 定时任务因此不再需要任何 ``.cmd`` 包装。

无窗口版的 ``sys.stdout`` / ``sys.stderr`` 可能是 ``None``，任何 ``print`` 都会
``AttributeError``。:func:`_ensure_console_streams` 负责把它们接回来
（从 cmd 调用时还能显示输出）或指向 ``os.devnull``。

冻结（PyInstaller 单文件 exe）运行时，默认数据目录与 ``papers.custom.json`` 跟随
**可执行文件所在目录**（便携），而不是临时解包目录或当前工作目录；因此把一个
``NewspaperAuto.exe`` 放进任意可写文件夹即可「解压即用」。

子命令后面的参数与 ``daily_scan.py`` / ``dashboard.py`` 完全一致，直接透传。
"""

from __future__ import annotations

import datetime
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

看板默认在**后台**运行：关掉任何窗口都不会停止它，停止请用 dashboard --stop。

命令行注意: 本 exe 是没有控制台窗口的 GUI 程序，Windows 的 shell 不会等它跑完，
`>` 重定向也拿不到内容。所以：

  - 每次 scan 都会把输出**同时**追加到 logs/scan-YYYY-MM-DD.log，定时任务看它即可
    （任务计划程序里直接填 `NewspaperAuto.exe scan --days 2`，不需要任何 .cmd 包装）；
  - 想在终端里等它跑完，用:  start /wait "" NewspaperAuto.exe scan --days 2
"""

_SCAN_COMMANDS = frozenset({"scan", "daily_scan", "daily"})
_DASHBOARD_COMMANDS = frozenset({"dashboard", "web"})

# Windows 常量：AttachConsole 的父进程标识；MessageBoxW 的图标/按钮样式
_ATTACH_PARENT_PROCESS = -1
_MB_ICONERROR = 0x10


class _Tee:
    """把写往某条流的内容**同时**落到日志文件。

    只实现 ``write`` / ``flush``，其余属性（``fileno`` / ``encoding`` / ``isatty`` …）
    一律透传给被包装的流，避免某处代码因缺属性而炸。
    """

    def __init__(self, stream, handle) -> None:
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> int:
        self._handle.write(text)
        return self._stream.write(text)

    def flush(self) -> None:
        self._handle.flush()
        self._stream.flush()

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _open_scan_log():
    """打开当天的扫描日志；写不了（只读目录等）返回 ``None`` 而不是让扫描失败。"""
    try:
        directory = paths.app_dir() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        path = paths.scan_log_file(directory.parent, datetime.date.today().isoformat())
        return open(path, "a", encoding="utf-8")
    except OSError as exc:
        print(f"提示：无法写入扫描日志（{exc}），本次只在控制台输出。", file=sys.stderr)
        return None


def _run_scan(args: Sequence[str]) -> int:
    """``scan`` 子命令：把本次运行的输出**同时**追加到 ``logs/scan-YYYY-MM-DD.log``。

    为什么必须自己写：Windows 便携包里没有 ``run_scan.sh`` / ``run_scan.ps1``
    （那两个脚本用 ``tee`` 写这份日志），而无窗口版 exe 一旦由任务计划程序或双击启动，
    输出就无处可去 —— 没有这份日志就完全无法回溯「今天到底扫到了什么」。
    有了它，定时任务不再需要任何 ``.cmd`` 包装。

    放在入口层而不是 ``cli.py``：源码用户走 ``run_scan.*`` 时由脚本自己 tee，
    两边都写同一个文件会互相覆盖。
    """
    handle = _open_scan_log()
    if handle is None:
        return cli.main(args, default_data_dir=paths.app_dir())

    started = datetime.datetime.now()
    handle.write(f"=== 开始监测扫描：{started:%Y-%m-%d %H:%M:%S} ===\n")
    handle.write("命令：NewspaperAuto scan " + " ".join(args) + "\n")
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _Tee(original_stdout, handle), _Tee(original_stderr, handle)
    code = 0
    try:
        code = int(cli.main(args, default_data_dir=paths.app_dir()) or 0)
    except BaseException:
        code = code or 1
        raise
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
        finished = datetime.datetime.now()
        handle.write(f"=== 扫描结束：{finished:%Y-%m-%d %H:%M:%S}，退出码：{code} ===\n")
        handle.close()
    return code


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
            f"也可以在终端里执行 `NewspaperAuto.exe --help` 查看用法。",
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
        return _run_scan(rest)
    if command in _DASHBOARD_COMMANDS:
        return web.main(rest, default_data_dir=paths.app_dir())

    print(f"未知子命令：{command}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
