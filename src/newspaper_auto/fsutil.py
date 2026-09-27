"""文件系统小工具：跨模块复用的原子写。

``report`` / ``state`` / ``papers`` / ``exporter`` 都需要把内容安全落盘，
却不想互相 import 造成环，故抽出这一无依赖的小模块。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def write_text(path: str | os.PathLike[str], content: str) -> None:
    """原子写入文本文件（自动建父目录）。

    先在同目录写入唯一临时文件，``flush`` + ``fsync`` 后再替换目标，
    避免进程中断留下半截 JSON / HTML。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
