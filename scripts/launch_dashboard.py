#!/usr/bin/env python3
"""启动本地看板；默认端口被占用时自动回退到系统分配的空闲端口。

这是端口回退逻辑的**唯一**实现：``Makefile``（``make web`` / ``make start``）、
``scripts/dashboard.command``（macOS 双击）与 ``scripts/dashboard.cmd``（Windows 双击）
都调用本脚本，避免同一段逻辑在三个 shell/cmd 里各抄一份。

用法（其余参数原样透传给 ``dashboard.py``）::

    python3 scripts/launch_dashboard.py [--scan]

端口来源：``--port``（透传参数优先）→ 环境变量 ``NEWSPAPER_PORT`` → ``dashboard.py`` 默认 8788。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DASHBOARD = PROJECT_DIR / "dashboard.py"


def _split_port(passthrough: list[str]) -> tuple[str, list[str]]:
    """取出并移除透传参数里的 ``--port``，返回 ``(端口, 其余参数)``。"""
    port = os.environ.get("NEWSPAPER_PORT", "8788")
    rest: list[str] = []
    i = 0
    while i < len(passthrough):
        arg = passthrough[i]
        if arg == "--port" and i + 1 < len(passthrough):
            port = passthrough[i + 1]
            i += 2
            continue
        if arg.startswith("--port="):
            port = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return port, rest


def _launch(port: str, passthrough: list[str]) -> int:
    return subprocess.call([sys.executable, str(DASHBOARD), "--port", port, "--open", *passthrough])


def main() -> int:
    port, passthrough = _split_port(sys.argv[1:])
    code = _launch(port, passthrough)
    if code == 0:
        return 0
    print()
    print(f"端口 {port} 不可用，改用系统自动分配的空闲端口重试…")
    return _launch("0", passthrough)


if __name__ == "__main__":
    raise SystemExit(main())
