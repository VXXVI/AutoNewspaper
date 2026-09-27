"""源码入口脚本（``daily_scan.py`` / ``dashboard.py``）的「项目根」钉死回归测试。

背景：这两个入口一直只把**数据目录**钉到脚本所在目录，而 ``papers.custom.json``
（配置层）仍按 ``paths.app_dir()`` 解析——该方法从 cwd 向上找项目根。于是从仓库外调用
（例如 ``python3 /path/AutoNewspaper/daily_scan.py``）时会静默忽略仓库里已提交的自定义
报社。修复方式是入口脚本设置 ``NEWSPAPER_APP_DIR`` 环境变量；后台看板子进程会继承它。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("script", ["daily_scan.py", "dashboard.py"])
def test_entrypoint_pins_app_dir_to_repo_root(script, tmp_path):
    """从仓库外的 cwd 执行入口脚本时，``NEWSPAPER_APP_DIR`` 必须指向仓库根。

    用子进程隔离，避免脚本设置的进程级环境变量污染同进程的其他测试。
    """
    script_path = str(ROOT / script)
    code = (
        f"import os, runpy\nrunpy.run_path({script_path!r})\nprint(os.environ.get('NEWSPAPER_APP_DIR', ''))\n"
    )
    env = {key: value for key, value in os.environ.items() if key != "NEWSPAPER_APP_DIR"}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == ROOT.resolve()


def test_entrypoint_does_not_override_explicit_app_dir(tmp_path):
    """已显式设置 ``NEWSPAPER_APP_DIR`` 时应被尊重（``setdefault`` 语义）。"""
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    script_path = str(ROOT / "daily_scan.py")
    code = (
        f"import os, runpy\nrunpy.run_path({script_path!r})\nprint(os.environ.get('NEWSPAPER_APP_DIR', ''))\n"
    )
    env = dict(os.environ)
    env["NEWSPAPER_APP_DIR"] = str(explicit)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == explicit.resolve()
