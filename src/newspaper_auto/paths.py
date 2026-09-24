"""数据目录解析。

`state/` 与 `hits/` 的落盘位置按以下优先级确定：

1. 命令行 ``--data-dir``
2. 环境变量 ``NEWSPAPER_DATA_DIR``
3. 从当前工作目录向上查找含 ``.git`` / ``pyproject.toml`` 的项目根
4. 当前工作目录

仓库根目录下的 ``daily_scan.py`` 兼容入口会显式传入自身所在目录（优先级同 1），
因此无论从哪个目录调用，状态文件都落在仓库内。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config


def find_project_root(start: Path | None = None) -> Path:
    """自 ``start``（默认 cwd）向上查找项目根，找不到则返回 ``start``。"""
    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if any((candidate / marker).exists() for marker in config.ROOT_MARKERS):
            return candidate
    return cur


def resolve_data_dir(
    cli_value: str | os.PathLike[str] | None = None, default: str | os.PathLike[str] | None = None
) -> Path:
    """解析数据目录（不创建）。"""
    if cli_value:
        return Path(cli_value).expanduser().resolve()
    env_value = os.environ.get("NEWSPAPER_DATA_DIR")
    if env_value:
        return Path(env_value).expanduser().resolve()
    if default is not None:
        return Path(default).expanduser().resolve()
    return find_project_root()


def state_file(data_dir: Path) -> Path:
    return data_dir / config.STATE_DIRNAME / config.SEEN_FILENAME


def web_config_file(data_dir: Path) -> Path:
    """看板扫描参数的持久化文件：``state/web_config.json``。"""
    return data_dir / config.STATE_DIRNAME / config.WEB_CONFIG_FILENAME


def task_history_file(data_dir: Path) -> Path:
    """看板扫描任务历史：``state/tasks.json``（最近 N 条摘要）。"""
    return data_dir / config.STATE_DIRNAME / config.TASK_HISTORY_FILENAME


def hits_dir(data_dir: Path) -> Path:
    return data_dir / config.HITS_DIRNAME


def archive_file(data_dir: Path) -> Path:
    return hits_dir(data_dir) / config.ARCHIVE_FILENAME


def index_file(data_dir: Path) -> Path:
    return hits_dir(data_dir) / config.INDEX_FILENAME
