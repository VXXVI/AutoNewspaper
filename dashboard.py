#!/usr/bin/env python3
"""本地看板兼容入口。

与 ``daily_scan.py`` 一样，先把仓库内的 ``src/`` 加入模块路径，因此
即使用户没有执行 ``pip install -e .``，也能直接启动看板。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# 与 daily_scan.py 对齐：把「项目根」（配置层）钉死为本文件所在目录。否则从仓库外
# 启动看板时 papers.custom.json 会跟随 cwd 解析/写回，找错位置；后台看板子进程
# （dashboard --detach）会继承该环境变量，同样落到这里。
os.environ.setdefault("NEWSPAPER_APP_DIR", _HERE)

from newspaper_auto.web import main  # noqa: E402  (需先补 sys.path)

if __name__ == "__main__":
    # 与 daily_scan.py 对齐：显式传入脚本自身目录，从仓库外启动时数据目录仍落在本仓库，
    # 而不是跟随 cwd 落到调用处（否则 state/ hits/ papers.custom.json 会写错位置）。
    raise SystemExit(main(default_data_dir=Path(_HERE)))
