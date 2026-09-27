"""子进程派生的小工具：抑制控制台窗口。

为什么必须有这个模块
--------------------
Windows 上，**自身没有控制台**的进程再派生**控制台子系统**子进程时，系统会为
每个子进程新建一个控制台窗口。项目里有两类「没有控制台」的进程：

- PyInstaller ``console=False`` 打出来的无窗口版 ``NewspaperAuto.exe``；
- 它用 ``DETACHED_PROCESS`` 起的常驻看板服务进程。

而抓取层的 curl 兜底（:mod:`newspaper_auto.http`）恰好是控制台子系统程序，
且它是**按请求**派生的：扫一次要抓几百个页面、默认 8 并发，只要走到兜底路径，
屏幕上就会刷出成百上千个黑窗口（v0.8.0 的真实反馈）。

所以**运行时每一处** ``subprocess`` 派生都必须带上这里的参数。
``tests/test_procutil.py`` 用 AST 扫描整个包守住这条不变量 —— 新增派生点时
如果忘了，测试会直接失败。
"""

from __future__ import annotations

import subprocess


def hidden_console() -> dict[str, int]:
    """短命子进程用：不弹控制台窗口（如 curl 兜底）。

    判据是「当前平台有没有 ``CREATE_NO_WINDOW`` 这个常量」（只有 Windows 有），
    而不是读 ``os.name`` —— 好处是测试可以直接补上常量来覆盖 Windows 分支，
    不必去改全局的 ``os.name``。
    """
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


def detached_console() -> dict[str, int]:
    """长驻子进程用：脱离控制台（Windows）/ 新建会话（POSIX，如看板服务进程）。

    Windows 上同时给 ``CREATE_NO_WINDOW`` 与 ``DETACHED_PROCESS``：按文档前者在
    与后者同用时会被忽略，但 ``DETACHED_PROCESS`` 本身就意味着「没有控制台」，
    结果正确；两个都写上是为了让意图在代码里读得出来。
    """
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not no_window:
        return {"start_new_session": True}
    return {"creationflags": no_window | getattr(subprocess, "DETACHED_PROCESS", 0)}
