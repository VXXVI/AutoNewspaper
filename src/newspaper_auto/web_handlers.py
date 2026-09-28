"""看板 POST 业务处理混入：扫描、配置、导出与自定义报社管理。

这些方法依赖 HTTP 处理器提供的 ``self.server`` / ``self._error_json`` /
``self._send_json``，以 mixin 形式注入 :class:`newspaper_auto.web_server.DashboardHandler`，
让路由与安全骨架留在主服务器模块中。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__, config, dialogs, exporter, papers
from .web_dashboard import build_dashboard, load_web_config, save_web_config
from .web_papers import _resolve_paper_by_name, build_papers_view
from .web_scan import ScanRequestError, load_task_history
from .web_static import SCAN_EVENT_INTERVAL
from .web_validate import (
    filter_export_records,
    validate_export_request,
    validate_scan_params,
    validate_web_config,
)


class _PapersWriteAbort(Exception):
    """在报社写操作内部提前终止，并携带要返回给客户端的状态码与说明。"""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _ApiHandlersMixin:
    """``DashboardHandler`` 的业务处理混入（读取输出 + 扫描 / 配置 / 导出 / 报社）。"""

    # -- GET 输出 ----------------------------------------------------
    def _serve_hits(self) -> None:
        self._serve_json(build_dashboard(self.server.data_dir))

    def _serve_health(self) -> None:
        self._serve_json({"ok": True, "version": __version__})

    def _serve_scan_history(self) -> None:
        self._serve_json({"tasks": load_task_history(self.server.data_dir)})

    def _serve_config(self) -> None:
        self._serve_json(load_web_config(self.server.data_dir))

    def _serve_papers(self) -> None:
        self._serve_json(build_papers_view(self.server.papers_root))

    def _serve_scan_status(self, query: str) -> None:
        params = parse_qs(query)
        raw_after = params.get("after", [None])[0]
        try:
            after = int(raw_after) if raw_after is not None else None
        except (TypeError, ValueError):
            self._error_json(400, "after 必须是非负整数")
            return
        if after is not None and after < 0:
            self._error_json(400, "after 必须是非负整数")
            return
        self._serve_json(self.server.scan_task.snapshot(after=after))

    def _serve_scan_events(self) -> None:
        """SSE：把扫描任务的快照（含日志增量）持续推给前端，替代高频轮询。

        任务到达终态（或客户端断开）即关闭连接；首个快照若已是终态则立即关闭。
        """
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        try:
            after = int(query.get("after", ["0"])[0])
        except (TypeError, ValueError):
            after = 0
        after = max(0, after)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        if self.command == "HEAD":
            return
        try:
            while True:
                snapshot = self.server.scan_task.snapshot(after=after)
                after = snapshot["log_cursor"]
                payload = json.dumps(snapshot, ensure_ascii=False)
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                if snapshot["status"] in ("idle", "done", "error", "cancelled"):
                    break
                time.sleep(SCAN_EVENT_INTERVAL)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            # 客户端断开：正常结束，不写访问日志错误
            pass

    # -- 业务处理 ----------------------------------------------------
    def _handle_scan_start(self, payload: Any) -> None:
        params, error = validate_scan_params(payload)
        if error:
            self._error_json(400, error)
            return
        assert params is not None
        selection_error = self.server.paper_selection_error(params["only"])
        if selection_error:
            self._error_json(400, selection_error)
            return
        try:
            task = self.server.start_scan(params)
        except ScanRequestError as exc:
            self._error_json(400, str(exc))
            return
        except Exception as exc:
            self._error_json(503, f"扫描任务启动失败：{exc}")
            return
        if task is None:
            self._error_json(409, "已有扫描在运行，请稍候")
            return
        self._send_json(202, task.snapshot(after=0))

    def _handle_scan_cancel(self, payload: Any) -> None:
        """请求取消当前扫描（协作式，在下一个工作单元边界生效）。"""
        del payload
        task = self.server.scan_task
        if not task.request_cancel():
            snapshot = task.snapshot()
            if snapshot["status"] == "idle":
                self._error_json(409, "没有正在运行的扫描")
            else:
                self._error_json(409, f"扫描已结束（{snapshot['status']}），无需取消")
            return
        self._send_json(202, task.snapshot())

    def _handle_config_save(self, payload: Any) -> None:
        cfg, error = validate_web_config(payload)
        if error:
            self._error_json(400, error)
            return
        assert cfg is not None
        selection_error = self.server.paper_selection_error(cfg["only"])
        if selection_error:
            self._error_json(400, selection_error)
            return
        try:
            with self.server.config_lock:
                save_web_config(self.server.data_dir, cfg)
        except OSError as exc:
            self._error_json(500, f"配置保存失败：{exc}")
            return
        self._send_json(200, cfg)

    def _handle_export(self, payload: Any) -> None:
        """把服务端归档的受控筛选结果原子写入用户指定文件夹。"""
        request, error = validate_export_request(payload, base_dir=self.server.data_dir)
        if error:
            self._error_json(400, error)
            return
        assert request is not None

        dashboard = build_dashboard(self.server.data_dir)
        filters = request["filters"]
        requested_papers = filters["papers"]
        if requested_papers is not None:
            available = set(dashboard["papers"])
            unknown = [value for value in requested_papers if value not in available]
            if unknown:
                self._error_json(400, f"filters.papers 包含未知值：{'、'.join(unknown)}")
                return
        requested_urls = filters["urls"]
        if requested_urls is not None:
            available_urls = {record["url"] for record in dashboard["records"]}
            unknown_urls = [value for value in requested_urls if value not in available_urls]
            if unknown_urls:
                self._error_json(400, "filters.urls 包含未知或已失效的报道链接")
                return

        records = filter_export_records(dashboard["records"], filters)
        try:
            output_path = exporter.export_hits(request["directory"], request["format"], records)
        except exporter.ExportValidationError as exc:
            self._error_json(400, str(exc))
            return
        except OSError as exc:
            self._error_json(500, f"导出失败：{exc}")
            return
        self._send_json(
            201,
            {"path": str(output_path), "count": len(records), "format": request["format"]},
        )

    def _handle_export_browse(self, payload: Any) -> None:
        """在看板**所在设备**上弹系统原生的文件夹选择窗口，把路径回填给前端。

        只回填路径，不写任何文件 —— 真正的写入仍走 ``POST /api/export`` 的完整校验，
        所以这里选出来的路径不会绕过导出逻辑。

        状态码约定（前端据此决定是「隐藏按钮」还是「报错」）：

        - ``200`` + ``{"path": ...}``：用户选了文件夹；
        - ``200`` + ``{"path": null, "cancelled": true}``：用户取消（**不是**失败）；
        - ``501``：这台设备根本弹不出窗口（无图形界面 / 缺 zenity）；
        - ``409``：已经有一个选择窗口在等用户操作。
        """
        if not isinstance(payload, dict):
            self._error_json(400, "请求体必须是 JSON 对象")
            return
        unknown = set(payload) - {"initial"}
        if unknown:
            self._error_json(400, f"不支持的字段：{'、'.join(sorted(unknown))}")
            return

        raw_initial = payload.get("initial")
        initial: str | None = None
        if raw_initial not in (None, ""):
            if not isinstance(raw_initial, str):
                self._error_json(400, "initial 必须是路径字符串")
                return
            initial = raw_initial.strip()
            if len(initial) > exporter.MAX_DIRECTORY_LENGTH:
                self._error_json(400, f"initial 不能超过 {exporter.MAX_DIRECTORY_LENGTH} 个字符")
                return
            if any(ord(char) < 32 or ord(char) == 127 for char in initial):
                self._error_json(400, "initial 不能包含控制字符")
                return
            if not initial:
                initial = None

        # 原生窗口是模态的：同一次只允许一个，否则用户会面对叠在一起的窗口。
        if not self.server.picker_lock.acquire(blocking=False):
            self._error_json(409, "已有一个文件夹选择窗口在等待操作，请先完成它")
            return
        try:
            try:
                chosen = self.server.folder_picker(initial)
            except dialogs.DirectoryPickerUnavailable as exc:
                self._send_json(
                    501,
                    {
                        "error": f"当前看板所在设备无法打开系统文件夹选择窗口（{exc}）。"
                        "请手动填写该设备上的完整路径。",
                        "supported": False,
                    },
                )
                return
            except OSError as exc:
                self._error_json(500, f"打开文件夹选择窗口失败：{exc}")
                return
        finally:
            self.server.picker_lock.release()

        if chosen is None:
            self._send_json(200, {"path": None, "cancelled": True})
            return
        # 原生窗口的返回值同样过一遍导出用的目录校验：可能指向刚开始就被删掉的目录。
        try:
            directory = exporter.resolve_directory(chosen, base_dir=self.server.data_dir, create=False)
        except exporter.ExportValidationError as exc:
            self._error_json(400, f"选择的路径不可用：{exc}")
            return
        if not directory.is_dir():
            self._error_json(400, "选择的文件夹不存在，请重新选择")
            return
        if not os.access(directory, os.W_OK):
            self._error_json(400, "选择的文件夹没有写入权限，请换一个")
            return
        self._send_json(200, {"path": str(directory), "cancelled": False})

    # -- 自定义报社 --------------------------------------------------
    def _with_papers_write(
        self,
        root: Any,
        mutate: Any,
        *,
        conflict: str,
        failure: str,
    ) -> bool:
        """报社配置写操作的公共骨架：扫描期拒绝、串行化、加载与错误映射。

        返回 True 表示已成功保存（调用方随后返回最新列表）；返回 False 表示
        已写出错误响应。``mutate(settings)`` 内如需提前报错，抛出
        :class:`_PapersWriteAbort` 即可（不会被保存）。
        """
        if self.server.scan_lock.locked():
            self._error_json(409, "扫描进行中，完成后才能修改报社配置")
            return False
        try:
            with self.server.papers_lock:
                # 拿下 papers_lock 后再查一次，封住两次检查之间扫描启动的窗口。
                if self.server.scan_lock.locked():
                    self._error_json(409, "扫描进行中，完成后才能修改报社配置")
                    return False
                settings = papers.load_settings_for_update(root)
                mutate(settings)
        except _PapersWriteAbort as abort:
            self._error_json(abort.status, abort.message)
            return False
        except papers.CustomPapersError as exc:
            self._error_json(409, f"{conflict}：{exc}。请先修复原文件")
            return False
        except OSError as exc:
            self._error_json(500, f"{failure}：{exc}")
            return False
        return True

    def _handle_paper_add(self, payload: Any) -> None:
        """新增或**同名覆盖**一条自定义报社，写回后返回最新列表。"""
        paper, error = papers.validate_paper(payload)
        if error:
            self._error_json(400, error)
            return
        assert paper is not None
        root = self.server.papers_root

        def mutate(settings: Any) -> None:
            current = list(settings.custom)
            updated: list[dict[str, Any]] = []
            replaced = False
            for existing in current:
                if existing["name"] == paper["name"]:
                    updated.append(paper)  # 原地替换，保持文件内顺序
                    replaced = True
                else:
                    updated.append(existing)
            if not replaced:
                updated.append(paper)
            known_before = {item["name"] for item in config.PAPERS} | {item["name"] for item in current}
            disabled = (
                settings.disabled
                if paper["name"] in known_before
                else tuple(item for item in settings.disabled if item != paper["name"])
            )
            # 新增同名报社会把它从「已彻底删除」里摘出来：删掉内置之后又想自己加回来时，
            # 不能让一条陈旧的 removed 记录把新加的报社吃掉。
            removed = tuple(item for item in settings.removed if item != paper["name"])
            papers.save_settings(root, updated, disabled, removed)

        if not self._with_papers_write(root, mutate, conflict="报社配置未保存", failure="报社配置保存失败"):
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_delete(self, payload: Any) -> None:
        """移除一条**自定义/覆盖**条目（内置本身保留，故也是「恢复默认」）。

        要删掉内置报社请用 :meth:`_handle_paper_remove`（``/api/papers/remove``）。
        """
        if not isinstance(payload, dict):
            self._error_json(400, "请求体必须是 JSON 对象")
            return
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            self._error_json(400, "name 必须是非空字符串")
            return
        name = name.strip()
        root = self.server.papers_root

        def mutate(settings: Any) -> None:
            current = list(settings.custom)
            if all(existing["name"] != name for existing in current):
                builtin_names = {paper["name"] for paper in config.PAPERS}
                if name in builtin_names:
                    raise _PapersWriteAbort(400, "内置报社没有可移除的自定义配置；要删掉它请用「彻底删除」")
                raise _PapersWriteAbort(404, f"未找到报社「{name}」")
            builtin_names = {paper["name"] for paper in config.PAPERS}
            disabled = (
                settings.disabled
                if name in builtin_names
                else tuple(item for item in settings.disabled if item != name)
            )
            papers.save_settings(root, [p for p in current if p["name"] != name], disabled, settings.removed)

        if not self._with_papers_write(root, mutate, conflict="报社配置未删除", failure="报社配置删除失败"):
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_remove(self, payload: Any) -> None:
        """**彻底删除**一家报社（内置也可以）；删后不再出现在列表里，也没有恢复入口。

        与 ``/api/papers/delete`` 的区别：那个只摘掉自定义/覆盖那一层（内置本身还在，
        所以同时充当「恢复默认」）。内置定义只存在于代码里，所以「删掉」记为
        ``papers.custom.json`` 的 ``removed`` 名单，并顺手清掉同名的自定义覆盖与停用记录 ——
        否则一个残留的覆盖会把这家报社又「复活」出来。
        """
        if not isinstance(payload, dict):
            self._error_json(400, "请求体必须是 JSON 对象")
            return
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            self._error_json(400, "name 必须是非空字符串")
            return
        name = name.strip()
        root = self.server.papers_root

        def mutate(settings: Any) -> None:
            catalog = papers.merge_papers(config.PAPERS, settings.custom, removed=settings.removed)
            if name not in {paper["name"] for paper in catalog}:
                raise _PapersWriteAbort(404, f"未找到报社「{name}」")
            removed = list(settings.removed)
            if name not in removed:
                removed.append(name)
            papers.save_settings(
                root,
                [paper for paper in settings.custom if paper["name"] != name],
                tuple(item for item in settings.disabled if item != name),
                removed,
            )

        if not self._with_papers_write(root, mutate, conflict="报社未删除", failure="报社删除失败"):
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_status(self, payload: Any) -> None:
        """显式启用/停用报社；操作幂等且不会删除历史结果或抓取规则。"""
        if not isinstance(payload, dict):
            self._error_json(400, "请求体必须是 JSON 对象")
            return
        unknown = set(payload) - {"name", "enabled"}
        if unknown:
            self._error_json(400, f"不支持的字段：{'、'.join(sorted(unknown))}")
            return
        name = payload.get("name")
        enabled = payload.get("enabled")
        if not isinstance(name, str) or not name.strip():
            self._error_json(400, "name 必须是非空字符串")
            return
        if not isinstance(enabled, bool):
            self._error_json(400, "enabled 必须是布尔值")
            return
        name = name.strip()
        root = self.server.papers_root

        def mutate(settings: Any) -> None:
            catalog = papers.merge_papers(config.PAPERS, settings.custom, removed=settings.removed)
            if name not in {paper["name"] for paper in catalog}:
                raise _PapersWriteAbort(404, f"未找到报社「{name}」")
            disabled = list(settings.disabled)
            if enabled:
                disabled = [item for item in disabled if item != name]
            elif name not in disabled:
                disabled.append(name)
            papers.save_settings(root, settings.custom, disabled, settings.removed)

        if not self._with_papers_write(root, mutate, conflict="报社状态未保存", failure="报社状态保存失败"):
            return
        self._send_json(200, build_papers_view(root))

    def _handle_paper_test(self, payload: Any) -> None:
        """试抓探测：body 为完整 paper 对象；只给 ``name`` 时按名称解析内置/自定义。"""
        paper, error = papers.validate_paper(payload)
        if error:
            name = payload.get("name") if isinstance(payload, dict) and set(payload) == {"name"} else None
            paper = _resolve_paper_by_name(self.server.papers_root, name)
            if paper is None:
                self._error_json(400, error)
                return
        assert paper is not None
        try:
            result = self.server.paper_probe(paper, lambda line: sys.stderr.write(f"[试抓] {line}\n"))
        except Exception as exc:  # 兜底：探测异常不该让看板工作线程炸掉
            self._error_json(500, f"试抓失败：{exc}")
            return
        try:
            entry_code = int(result.get("entry_code") or 0)
            sections = int(result.get("sections") or 0)
            sample_articles = (
                int(result["sample_articles"]) if result.get("sample_articles") is not None else None
            )
        except (TypeError, ValueError):
            entry_code, sections, sample_articles = 0, 0, None
        note = str(result.get("note") or "")
        self._send_json(
            200,
            {
                "entry": str(result.get("entry") or ""),
                "entry_code": entry_code,
                "sections": sections,
                "sample_articles": sample_articles,
                "ok": entry_code == 200 and (sample_articles is None or sample_articles > 0),
                "note": note,
            },
        )
