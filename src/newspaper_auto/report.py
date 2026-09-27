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
from typing import Any
from urllib.parse import urlsplit

from . import fsutil

# CSV 列与 JSON 记录字段保持一致：section 与 section_no 同时输出，
# 后者是规范化版号，便于下游排序/去重。
CSV_FIELDS = ("paper", "date", "section", "section_no", "title", "url", "text_len", "matched")
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe_text(value: Any) -> str:
    """Escape text that spreadsheet applications could interpret as a formula.

    公式前缀判定前先去掉前导空白：``"  =1+1"`` 在部分表格软件里同样会被当公式执行，
    但转义时仍保留原始内容，只在最前面加一个撇号。
    """
    text = "" if value is None else str(value)
    if text.startswith(CSV_FORMULA_PREFIXES) or text.lstrip().startswith(CSV_FORMULA_PREFIXES):
        return f"'{text}"
    return text


def _csv_safe_len(value: Any) -> Any:
    """``text_len`` 专用兜底：数值原样输出，只有非数值才当文本做公式转义。

    负数（如 ``-42``）本身以 ``-`` 开头，若一律走 :func:`_csv_safe_text` 会被误加撇号；
    而 ``hits_to_csv`` 是公开函数，外部传入字符串时仍需防 ``=…`` 注入，故按类型分流。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _csv_safe_text(value)
    return value


def geometry_label(geometry: Any) -> str:
    """把「版内位置」渲染成一行人类可读文字，形如 ``版内 2/7 · 上版 38%``。

    记录里没有 ``geometry``（其余四家无 area，或旧归档）时返回空串，
    调用方据此降级 —— 这是对老 ``hits/archive.json`` 的向后兼容保证。
    """
    if not isinstance(geometry, dict):
        return ""
    parts: list[str] = []
    index, total = geometry.get("index"), geometry.get("total")
    if isinstance(index, int) and isinstance(total, int) and total > 0:
        parts.append(f"版内 {index}/{total}")
    top = geometry.get("top")
    if isinstance(top, (int, float)) and not isinstance(top, bool):
        parts.append(f"上版 {round(top * 100)}%")
    return " · ".join(parts)


def write_text(path: str | os.PathLike[str], content: str) -> None:
    """原子写入文本文件（兼容转发到 :func:`newspaper_auto.fsutil.write_text`）。"""
    fsutil.write_text(path, content)


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
                "paper": _csv_safe_text(hit.get("paper", "")),
                "date": _csv_safe_text(hit.get("date", "")),
                "section": _csv_safe_text(hit.get("section", "")),
                "section_no": _csv_safe_text(hit.get("section_no", "")),
                "title": _csv_safe_text(hit.get("title", "")),
                "url": _csv_safe_text(hit.get("url", "")),
                "text_len": _csv_safe_len(hit.get("text_len", 0)),
                "matched": _csv_safe_text(";".join(hit.get("matched", []))),
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

    lines += [
        "## 各家扫描情况",
        "",
        "| 报社 | 日期 | 版面 | 文章 | 命中 | 失败 |",
        "|---|---|---|---|---|---|",
    ]
    hit_by_paper: dict[tuple[str, str], int] = {}
    for hit in total_hits:
        key = (hit["paper"], hit["date"])
        hit_by_paper[key] = hit_by_paper.get(key, 0) + 1
    for item in stats:
        count = hit_by_paper.get((item["paper"], item["date"]), 0)
        # note 必须落在最后一个单元格的竖线之内，否则 GFM 会把整行当成多出一列而错乱。
        note = f" *{item['note']}*" if item["note"] else ""
        sec_fail = item.get("sections_failed", 0)
        art_fail = item.get("articles_failed", 0)
        fail_cell = f"{sec_fail}版/{art_fail}文" if (sec_fail or art_fail) else "—"
        lines.append(
            f"| {item['paper']} | {item['date']} | {item['sections']} | "
            f"{item['scanned']}/{item['articles']} | {count} | {fail_cell}{note} |"
        )
    lines.append("")

    # 抓取失败（缺报）节——仅在存在失败时展示
    failed_stats = [s for s in stats if s.get("sections_failed", 0) or s.get("articles_failed", 0)]
    entry_failed = [s for s in stats if not s.get("ok", s.get("entry_code", 200) == 200)]
    if failed_stats or entry_failed:
        lines += ["## 抓取失败（缺报）", ""]
        lines.append(
            "> ⚠️ 下列失败可能导致**漏报**——「无命中」不代表真的没有，页面可能下载失败或站点结构已变化。"
        )
        lines.append("")
        for item in entry_failed:
            lines.append(
                f"- **{item['paper']}** 扫描异常：{item.get('note', '')}（code={item['entry_code']}）"
            )
        for item in failed_stats:
            if item in entry_failed:
                continue
            sf = item.get("sections_failed", 0)
            af = item.get("articles_failed", 0)
            parts = []
            if sf:
                parts.append(f"版面页失败 {sf} 个")
            if af:
                parts.append(f"文章页失败 {af} 篇")
            lines.append(f"- **{item['paper']}** {item['date']}：{' / '.join(parts)}")
            for rec in item.get("failed_urls", []):
                reason = f" · {rec['reason']}" if rec.get("reason") else ""
                lines.append(f"  - code={rec['code']} {rec['url']}{reason}")
        lines.append("")

    if new_hits:
        lines += ["## 新增命中", ""]
        for index, hit in enumerate(new_hits, 1):
            lines.append(f"### {index}. [{hit['paper']}] {hit['title']}")
            lines.append("")
            lines.append(f"- 版面：{hit.get('section') or '—'}")
            position = geometry_label(hit.get("geometry"))
            if position:
                lines.append(f"- 版内位置：{position}")
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
            position = geometry_label(hit.get("geometry"))
            suffix = f" · {position}" if position else ""
            lines.append(
                f"- [{hit['paper']} · {hit.get('section') or '—'}{suffix}] {hit['title']} — {hit['url']}"
            )
        lines.append("")
    return "\n".join(lines)


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数字报历史命中汇总</title>
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
 td.k{{color:#4f5f78;font-size:12px}}
 a{{color:#1a6ef5;text-decoration:none}} a:hover{{text-decoration:underline}}
 .sn{{font-size:12px;color:#8a94a3;margin-top:4px}}
</style></head><body><div class="wrap">
<h1>数字报历史命中汇总</h1>
<div class="meta">本次更新关键词：{kw} · 共 {total} 条历史命中 · 更新于 {updated}</div>
<table><thead><tr><th>报社</th><th>日期</th><th>版面</th><th>命中关键词</th><th>标题</th></tr></thead>
<tbody>{rows}</tbody></table>
</div></body></html>
"""


def render_html(kw: str, archive: list[dict[str, Any]], now: datetime | None = None) -> str:
    """历史命中汇总页 HTML 文本。"""
    now = now or datetime.now()
    rows = []
    for hit in sorted(archive, key=lambda x: (x["date"], x["paper"]), reverse=True):
        snippets = "".join(f'<div class="sn">{_html.escape(s)}</div>' for s in hit.get("snippets", [])[:1])
        section = _html.escape(hit.get("section") or "—")
        raw_matched = hit.get("matched")
        matched = (
            "、".join(str(keyword) for keyword in raw_matched if keyword)
            if isinstance(raw_matched, list)
            else ""
        )
        position = geometry_label(hit.get("geometry"))
        if position:
            section += f'<div class="sn">{_html.escape(position)}</div>'
        title = _html.escape(hit["title"])
        url = str(hit.get("url") or "")
        # 只放行 http/https：data: / javascript: 等协议一律降级为纯文本，与前端 safeUrl 口径一致
        if urlsplit(url).scheme in ("http", "https"):
            title_cell = f'<a href="{_html.escape(url)}" target="_blank" rel="noopener">{title}</a>{snippets}'
        else:
            title_cell = f"{title}{snippets}"
        rows.append(
            "<tr>"
            f'<td class="p">{_html.escape(hit["paper"])}</td>'
            f'<td class="d">{_html.escape(hit["date"])}</td>'
            f'<td class="s">{section}</td>'
            f'<td class="k">{_html.escape(matched) or "—"}</td>'
            f"<td>{title_cell}</td></tr>"
        )
    return _HTML_TEMPLATE.format(
        kw=_html.escape(kw),
        total=len(archive),
        updated=now.strftime("%Y-%m-%d %H:%M"),
        rows="".join(rows) or '<tr><td colspan="5">暂无命中</td></tr>',
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
