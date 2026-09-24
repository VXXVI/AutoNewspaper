"""看板后台任务的线程安全状态模型。

HTTP 请求线程只读取 :meth:`ScanTask.snapshot` 返回的不可变快照；扫描线程通过
``append_log`` / ``finish`` / ``fail`` 更新状态，避免把正在变化的列表和字段直接暴露给
其他线程。日志使用绝对游标，前端可以只拉取上次轮询后的增量。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_UNIT_LINE_RE = re.compile(r"^\s*[✓✗]\s+")


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ScanTask:
    """一次扫描任务的状态。

    ``status`` 只使用 ``idle`` / ``running`` / ``done`` / ``error``。命令返回非零退出码
    时标记为 ``error``，避免前端把“部分报社抓取失败”误报成成功。
    """

    max_log_lines: int = 300
    status: str = "idle"
    task_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    summary: dict[str, Any] | None = None
    exit_code: int | None = None
    error: str | None = None
    completed_units: int = 0
    total_units: int = 0
    current_unit: str = ""
    _log: list[str] = field(default_factory=list, repr=False)
    _log_start: int = field(default=0, repr=False)
    _log_cursor: int = field(default=0, repr=False)
    _started_monotonic: float | None = field(default=None, repr=False)
    _duration_seconds: float | None = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @classmethod
    def running(cls, params: dict[str, Any], *, total_units: int, max_log_lines: int) -> ScanTask:
        task = cls(max_log_lines=max_log_lines)
        task.status = "running"
        task.task_id = uuid.uuid4().hex[:12]
        task.params = deepcopy(params)
        task.started_at = _now_stamp()
        task.total_units = max(0, int(total_units))
        task._started_monotonic = time.monotonic()
        return task

    def append_log(self, line: str) -> None:
        """追加一行日志，并从 CLI 的“报社/日期结果行”推导总体进度。"""
        text = str(line)
        with self._lock:
            self._log.append(text)
            self._log_cursor += 1
            overflow = len(self._log) - self.max_log_lines
            if overflow > 0:
                del self._log[:overflow]
                self._log_start += overflow
            if _UNIT_LINE_RE.match(text):
                self.completed_units += 1
                if self.total_units:
                    self.completed_units = min(self.completed_units, self.total_units)
                self.current_unit = text.strip()

    def finish(self, result: dict[str, Any]) -> None:
        with self._lock:
            try:
                self.exit_code = int(result.get("exit_code", 0))
            except (TypeError, ValueError):
                self.exit_code = 1
            summary = result.get("summary")
            self.summary = deepcopy(summary) if isinstance(summary, dict) else None
            self.status = "done" if self.exit_code == 0 else "error"
            if self.exit_code != 0 and not self.error:
                self.error = f"扫描未完整完成（退出码 {self.exit_code}），请查看日志。"
            self._finish_clock()

    def fail(self, error: BaseException | str, *, exit_code: int = 1) -> None:
        with self._lock:
            message = str(error).strip()
            self.error = message or error.__class__.__name__
            self.exit_code = exit_code
            self.status = "error"
            self._finish_clock()

    def _finish_clock(self) -> None:
        self.finished_at = _now_stamp()
        if self._started_monotonic is not None:
            self._duration_seconds = round(time.monotonic() - self._started_monotonic, 1)

    def snapshot(self, *, after: int | None = None) -> dict[str, Any]:
        """返回一致性快照；``after`` 为日志绝对游标，传入时只返回其后的日志。"""
        with self._lock:
            if after is None:
                logs = list(self._log)
                truncated = False
            else:
                cursor = max(0, int(after))
                truncated = cursor < self._log_start or cursor > self._log_cursor
                start_index = 0 if truncated else cursor - self._log_start
                logs = list(self._log[start_index:])

            duration = self._duration_seconds
            if duration is None and self._started_monotonic is not None:
                duration = round(time.monotonic() - self._started_monotonic, 1)
            percent = (
                round(min(self.completed_units, self.total_units) / self.total_units * 100)
                if self.total_units
                else None
            )
            return {
                "status": self.status,
                "task_id": self.task_id,
                "params": deepcopy(self.params),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration_seconds": duration,
                "log": logs,
                "log_cursor": self._log_cursor,
                "logs_truncated": truncated,
                "progress": {
                    "completed": self.completed_units,
                    "total": self.total_units,
                    "percent": percent,
                    "current": self.current_unit,
                },
                "summary": deepcopy(self.summary) if self.summary is not None else None,
                "exit_code": self.exit_code,
                "error": self.error,
            }

    def to_dict(self) -> dict[str, Any]:
        """兼容旧调用；新代码优先使用 :meth:`snapshot`。"""
        return self.snapshot()
