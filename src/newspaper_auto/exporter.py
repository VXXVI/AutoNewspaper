"""把可信的扫描/归档记录导出到用户指定目录。

目录可以由 CLI 或本地看板传入，但文件名始终由程序生成，避免关键词、报社名等
外部文本参与路径拼接。最终文件通过 :func:`report.write_text` 原子替换。
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import report

EXPORT_FORMATS = ("csv", "json")
MAX_DIRECTORY_LENGTH = 4096


class ExportValidationError(ValueError):
    """导出目录或格式不合法。"""


def resolve_directory(
    value: str | os.PathLike[str],
    *,
    base_dir: str | os.PathLike[str] | None = None,
    create: bool = False,
) -> Path:
    """把用户目录收敛为绝对路径，并可选择递归创建。

    相对路径相对于 ``base_dir``（缺省为当前工作目录）解析；``~`` 使用当前运行用户
    的主目录展开。控制字符被拒绝，防止日志/JSON 响应被路径换行污染。
    """
    try:
        raw = os.fspath(value)
    except TypeError as exc:
        raise ExportValidationError("directory 必须是路径字符串") from exc
    if not isinstance(raw, str):
        raise ExportValidationError("directory 必须是路径字符串")
    raw = raw.strip()
    if not raw:
        raise ExportValidationError("directory 不能为空")
    if len(raw) > MAX_DIRECTORY_LENGTH:
        raise ExportValidationError(f"directory 不能超过 {MAX_DIRECTORY_LENGTH} 个字符")
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ExportValidationError("directory 不能包含控制字符")

    try:
        directory = Path(raw).expanduser()
        if not directory.is_absolute():
            base = Path(base_dir).expanduser() if base_dir is not None else Path.cwd()
            directory = base / directory
        directory = directory.resolve(strict=False)
        if directory.exists() and not directory.is_dir():
            raise ExportValidationError("directory 指向已有文件，必须填写文件夹")
    except ExportValidationError:
        raise
    except (OSError, RuntimeError) as exc:
        raise ExportValidationError(f"directory 无法解析：{exc}") from exc
    if create:
        # 创建/权限错误保留为 OSError，让 HTTP 层区分“参数非法”(400) 与“写入失败”(500)。
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.is_dir():  # 防御并发替换为普通文件
            raise ExportValidationError("directory 不是可用文件夹")
    return directory


def normalize_format(value: Any) -> str:
    """校验并标准化导出格式。"""
    if not isinstance(value, str):
        raise ExportValidationError("format 必须是 csv 或 json")
    format_name = value.strip().lower()
    if format_name not in EXPORT_FORMATS:
        raise ExportValidationError("format 必须是 csv 或 json")
    return format_name


def export_hits(
    directory: str | os.PathLike[str],
    format_name: str,
    hits: list[dict[str, Any]],
    *,
    base_dir: str | os.PathLike[str] | None = None,
    now: datetime | None = None,
) -> Path:
    """把命中列表导出为一个新文件并返回绝对路径。

    文件名只含 ASCII 时间戳和随机标识，兼容 Windows/macOS/Linux，且不会覆盖同秒
    内的其他导出。CSV 带 UTF-8 BOM，便于常见表格软件正确识别中文。
    """
    normalized_format = normalize_format(format_name)
    # 先校验格式再创建目录：无效请求不应留下空文件夹。
    target_dir = resolve_directory(directory, base_dir=base_dir, create=True)
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    token = uuid.uuid4().hex[:8]
    target = target_dir / f"newspaper-hits-{timestamp}-{token}.{normalized_format}"
    if normalized_format == "csv":
        content = "\ufeff" + report.hits_to_csv(hits)
    else:
        content = report.hits_to_json(hits) + "\n"
    report.write_text(target, content)
    return target
