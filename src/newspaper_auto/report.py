"""报告渲染：Markdown 报告、HTML 汇总、JSON / CSV 结构化输出。

本模块只负责「数据 → 文本」，不决定写不写文件（由 CLI 控制），便于单测。
"""

from __future__ import annotations

import csv
import html as _html
import io
import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

# 结构化输出字段（保持与历史 hits 记录一致，便于下游消费）
JSON_FIELDS = ("paper", "date", "section", "title", "url", "text_len", "snippets", "matched")
CSV_FIELDS = ("paper", "date", "section", "title", "url", "text_len", "matched")


def write_text(path: str | os.PathLike[str], content: str) -> None:
    """写入文本文件（自动建父目录）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def hits_to_json(hits: list[dict[str, Any]]) -> str:
    """JSON 数组文本（可直接 ``json.loads``）。"""
    return json.dumps(hits, ensure_ascii=False, indent=2)


def hits_to_csv(hits: list[dict[str, Any]]) -> str:
    """CSV 文本；``matched`` 用分号连接。"""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(CSV_FIELDS))
    writer.writeheader()
    for hit in hits:
        writer.writerow(
            {
                "paper": hit.get("paper", ""),
                "date": hit.get("date", ""),
                "section": hit.get("section", ""),
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
                "text_len": hit.get("text_len", 0),
                "matched": ";".join(hit.get("matched", [])),
            }
        )
    return buffer.getvalue()


def render_md(
    kw_label: str,
    dates: Iterable[datetime],
    stats: list[dict[str, Any]],
    new_hits: list[dict[str, Any]],
    total_hits: list[dict[str, Any]],
    mode: str,
    now: datetime | None = None,
) -> str:
    """Markdown 报告文本。"""
    now = now or datetime.now()
    dates = list(dates)
    lines = [
        f"# 「{kw_label}」数字报监测报告",
        "",
        f"- 运行时间：{now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 扫描日期：{', '.join(d.strftime('%Y-%m-%d') for d in dates)}",
        f"- 模式：{mode}",
        f"- 合计命中：{len(total_hits)} 条（其中新增 {len(new_hits)} 条）",
        "",
    ]

    lines += ["## 各家扫描情况", "", "| 报社 | 日期 | 版面 | 文章 | 命中 |", "|---|---|---|---|---|"]
    hit_by_paper: dict[tuple[str, str], int] = {}
    for hit in total_hits:
        key = (hit["paper"], hit["date"])
        hit_by_paper[key] = hit_by_paper.get(key, 0) + 1
    for item in stats:
        count = hit_by_paper.get((item["paper"], item["date"]), 0)
        note = f" *{item['note']}*" if item["note"] else ""
        lines.append(
            f"| {item['paper']} | {item['date']} | {item['sections']} | "
            f"{item['scanned']}/{item['articles']} | {count} |" + note
        )
    lines.append("")

    if new_hits:
        lines += ["## 新增命中", ""]
        for index, hit in enumerate(new_hits, 1):
            lines.append(f"### {index}. [{hit['paper']}] {hit['title']}")
            lines.append("")
            lines.append(f"- 版面：{hit.get('section') or '—'}")
            lines.append(f"- 日期：{hit['date']}")
            lines.append(f"- 链接：{hit['url']}")
            for snip in hit.get("snippets", [])[:2]:
                lines.append(f"- 上下文：{snip}")
            lines.append("")
    else:
        lines += ["## 新增命中", "", "无新增命中。", ""]

    if total_hits and len(total_hits) != len(new_hits):
        new_urls = {hit["url"] for hit in new_hits}
        lines += ["## 其余已知命中（历史）", ""]
        for hit in total_hits:
            if hit["url"] in new_urls:
                continue
            lines.append(f"- [{hit['paper']}] {hit['title']} — {hit['url']}")
        lines.append("")
    return "\n".join(lines)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>「{kw}」五家报社监测</title>
<style>
 body{{margin:0;background:#f6f7f9;color:#1b1f24;
   font:15px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
 .wrap{{max-width:1020px;margin:0 auto;padding:38px 22px 70px}}
 h1{{font-size:23px;margin:0 0 4px}}
 .meta{{color:#8a94a3;font-size:13px;margin-bottom:22px}}
 table{{width:100%;border-collapse:collapse;background:#fff;
   border:1px solid #e3e7ec;border-radius:12px;overflow:hidden}}
 th,td{{padding:11px 13px;text-align:left;border-bottom:1px solid #eef1f5;font-size:14px;vertical-align:top}}
 th{{background:#fafbfc;font-size:12.5px;color:#4a5462}}
 tr:last-child td{{border-bottom:none}}
 td.p{{white-space:nowrap;font-weight:600;font-size:13px;color:#4a5462}}
 td.d{{white-space:nowrap;color:#8a94a3;font-size:12.5px}}
 td.s{{color:#8a94a3;font-size:12.5px}}
 a{{color:#1a6ef5;text-decoration:none}} a:hover{{text-decoration:underline}}
 .sn{{font-size:12px;color:#8a94a3;margin-top:4px}}
</style></head><body><div class="wrap">
<h1>「{kw}」· 五家报社数字报监测</h1>
<div class="meta">共 {total} 条命中 · 更新于 {updated}</div>
<table><thead><tr><th>报社</th><th>日期</th><th>版面</th><th>标题</th></tr></thead>
<tbody>{rows}</tbody></table>
</div></body></html>
"""


def render_html(kw: str, archive: list[dict[str, Any]], now: datetime | None = None) -> str:
    """历史命中汇总页 HTML 文本。"""
    now = now or datetime.now()
    rows = []
    for hit in sorted(archive, key=lambda x: (x["date"], x["paper"]), reverse=True):
        snippets = "".join(f'<div class="sn">{_html.escape(s)}</div>' for s in hit.get("snippets", [])[:1])
        rows.append(
            "<tr>"
            f'<td class="p">{_html.escape(hit["paper"])}</td>'
            f'<td class="d">{_html.escape(hit["date"])}</td>'
            f'<td class="s">{_html.escape(hit.get("section") or "—")}</td>'
            f'<td><a href="{_html.escape(hit["url"])}" target="_blank" rel="noopener">'
            f"{_html.escape(hit['title'])}</a>{snippets}</td></tr>"
        )
    return _HTML_TEMPLATE.format(
        kw=_html.escape(kw),
        total=len(archive),
        updated=now.strftime("%Y-%m-%d %H:%M"),
        rows="".join(rows) or '<tr><td colspan="4">暂无命中</td></tr>',
    )


def write_md(
    path: str | os.PathLike[str],
    kw_label: str,
    dates: Iterable[datetime],
    stats: list[dict[str, Any]],
    new_hits: list[dict[str, Any]],
    total_hits: list[dict[str, Any]],
    mode: str,
    now: datetime | None = None,
) -> None:
    write_text(path, render_md(kw_label, dates, stats, new_hits, total_hits, mode, now))


def write_html(
    path: str | os.PathLike[str], kw: str, archive: list[dict[str, Any]], now: datetime | None = None
) -> None:
    write_text(path, render_html(kw, archive, now))
