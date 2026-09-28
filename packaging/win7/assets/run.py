"""便携版统一启动入口（与打包好的 ``NewspaperAuto.exe`` 子命令一致）。

用法（在包根目录）::

    python\\pythonw.exe run.py                     打开看板（无窗口，并自动打开浏览器）
    python\\pythonw.exe run.py scan --no-state     扫描一次（无窗口，输出写 logs\\scan-<日期>.log）
    python\\python.exe  run.py scan                扫描一次（有窗口，实时看结果）
    python\\python.exe  run.py dashboard --stop    停止后台看板

为什么需要这个文件：``dashboard.py`` / ``daily_scan.py`` 在**无控制台**的
``pythonw.exe`` 下 ``sys.stdout`` / ``sys.stderr`` 是 ``None``，任何 ``print`` 都会
``AttributeError`` 把进程打崩。``newspaper_auto.exe`` 这个入口已经处理了这一点
（接回父控制台，或指向 ``os.devnull``），并让 ``scan`` 自己写运行日志，所以便携版
统一走它 —— 双击 ``start-dashboard.vbs`` 时不弹任何命令行窗口，行为与 exe 一致。

本文件还把「项目根」钉死为自身所在目录（同 ``daily_scan.py`` / ``dashboard.py`` 的
约定），保证 ``state/``、``hits/``、``logs/`` 与 ``papers.custom.json`` 都落在包内。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

os.environ.setdefault("NEWSPAPER_APP_DIR", _HERE)

from newspaper_auto.exe import main  # noqa: E402  (需先补 sys.path)

if __name__ == "__main__":
    raise SystemExit(main())
