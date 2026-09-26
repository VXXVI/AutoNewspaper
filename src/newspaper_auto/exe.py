"""统一可执行入口（供 PyInstaller 打包，也可作 ``newspaper-auto`` 命令使用）。

用法::

    NewspaperAuto.exe scan [daily_scan.py 的参数...]
    NewspaperAuto.exe dashboard [dashboard.py 的参数...]
    NewspaperAuto.exe --version

冻结（PyInstaller 单文件 exe）运行时，默认数据目录与 ``papers.custom.json`` 跟随
**可执行文件所在目录**（便携），而不是临时解包目录或当前工作目录；因此把一个
``NewspaperAuto.exe`` 放进任意可写文件夹即可「解压即用」。

子命令后面的参数与 ``daily_scan.py`` / ``dashboard.py`` 完全一致，直接透传。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from . import __version__, cli, paths, web

_USAGE = f"""\
数字报关键词每日监测 v{__version__}

用法:
  NewspaperAuto.exe scan [参数...]        扫描一次（默认扫今天 + 昨天）
  NewspaperAuto.exe dashboard [参数...]   打开本地看板（可加 --open --scan）
  NewspaperAuto.exe --version             显示版本号
  NewspaperAuto.exe --help                显示本帮助

示例:
  NewspaperAuto.exe scan --days 7 --kw 人工智能
  NewspaperAuto.exe scan --since 2026-09-20 --until 2026-09-25 --no-state
  NewspaperAuto.exe dashboard --open

scan / dashboard 后面的参数与 daily_scan.py / dashboard.py 完全一致（详见 README）。
数据（state/、hits/）与 papers.custom.json 默认放在本 exe 所在目录。
"""

_SCAN_COMMANDS = frozenset({"scan", "daily_scan", "daily"})
_DASHBOARD_COMMANDS = frozenset({"dashboard", "web"})


def main(argv: Sequence[str] | None = None) -> int:
    """分发子命令；返回进程退出码（供 ``SystemExit`` 使用）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
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
