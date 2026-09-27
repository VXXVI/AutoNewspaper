"""自定义报社目录的只读装配与按名解析。

把「内置 + 自定义 + 覆盖 + 停用」合并结果整理成前端可直接渲染的结构，
并提供只给名称时的试抓解析入口。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config, papers


def build_papers_view(root: Path | str) -> dict[str, Any]:
    """组装报社目录；保留旧字段，并提供前端可直接渲染的结构化 ``items``。

    **已彻底删除（``removed``）的内置报社不会出现在 ``items`` 里** —— 用户删掉它之后
    就不该再看到它，也没有「恢复」入口（要恢复得手工编辑 ``papers.custom.json``）。
    """
    root = Path(root)
    settings = papers.load_settings(root)
    custom = list(settings.custom)
    disabled = set(settings.disabled)
    removed = set(settings.removed)
    catalog = papers.merge_papers(config.PAPERS, custom, removed=removed)
    effective = papers.merge_papers(config.PAPERS, custom, disabled, removed)
    builtin_names = {paper["name"] for paper in config.PAPERS}
    custom_names = {paper["name"] for paper in custom}
    items = []
    for paper in catalog:
        name = paper["name"]
        if name in builtin_names and name in custom_names:
            source = "override"
        elif name in custom_names:
            source = "custom"
        else:
            source = "builtin"
        items.append(
            {
                "name": name,
                "source": source,
                "enabled": name not in disabled,
                "config": dict(paper),
            }
        )
    return {
        "builtin": [paper["name"] for paper in config.PAPERS],
        "custom": custom,
        "effective": [paper["name"] for paper in effective],
        "disabled": [paper["name"] for paper in catalog if paper["name"] in disabled],
        "removed": [paper["name"] for paper in config.PAPERS if paper["name"] in removed],
        "items": items,
    }


def _resolve_paper_by_name(root: Path | str, name: Any) -> dict[str, Any] | None:
    """按名称从「内置 + 自定义」合并结果里取出完整报社对象（供仅给 name 的试抓）。"""
    if not isinstance(name, str) or not name.strip():
        return None
    wanted = name.strip()
    settings = papers.load_settings(Path(root))
    for paper in papers.merge_papers(config.PAPERS, settings.custom):
        if paper["name"] == wanted:
            return dict(paper)
    return None
