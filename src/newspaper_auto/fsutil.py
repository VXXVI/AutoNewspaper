"""文件系统小工具：跨模块复用的原子写。

``report`` / ``state`` / ``papers`` / ``exporter`` 都需要把内容安全落盘，
却不想互相 import 造成环，故抽出这一无依赖的小模块。
"""

from __future__ import annotations

import contextlib
import errno
import os
import tempfile
from pathlib import Path

# 落盘失败的常见 errno → 给使用者看的原因。措辞刻意写「会怎么发生的」，
# 因为使用者看到的往往只是「Operation not permitted」，既看不出是什么问题，也不知道该改什么。
_ERRNO_REASONS = {
    errno.EPERM: "系统拒绝了写入 —— 目录只读、受管控（沙箱/安全软件）环境或文件系统限制",
    errno.EACCES: "没有写入权限 —— 目录属主或权限位不允许",
    errno.EROFS: "目标文件系统是只读的",
    errno.ENOSPC: "目标磁盘空间不足",
    errno.EDQUOT: "超出磁盘配额",
    errno.ENOENT: "目标文件夹不存在或已被移除",
    errno.ENOTDIR: "路径中有一段不是文件夹",
    errno.ENAMETOOLONG: "路径过长",
    errno.ELOOP: "路径中存在循环的符号链接",
    errno.EIO: "磁盘 I/O 错误",
    errno.EBUSY: "目标文件正被其他程序占用",
    errno.EMFILE: "本进程打开的文件过多",
    errno.ENFILE: "系统打开的文件过多",
}


def explain_oserror(exc: OSError) -> str:
    """把落盘失败的 ``OSError`` 翻成一句人话（不含路径，由调用方拼接）。

    ``str(exc)`` 在真实环境里长这样::

        [Errno 1] Operation not permitted: '/…/.x.json.abc.tmp' -> '/…/x.json'

    既看不出「哪儿写不进去」，也不知道该怎么办。已知 errno 给固定说明，未知的退回
    ``strerror``；两种情况都带上 errno 名（如 ``EPERM``），方便搜索与报障。
    """
    code = exc.errno
    name = errno.errorcode.get(code) if code is not None else None
    reason = _ERRNO_REASONS.get(code)
    if reason is None:
        reason = exc.strerror or str(exc) or exc.__class__.__name__
    return f"{reason}（{name}）" if name else reason


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
