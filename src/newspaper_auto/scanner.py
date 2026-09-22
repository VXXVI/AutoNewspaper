"""扫描编排：单家报社单日 → 全部版面 → 全部文章 → 标题+正文匹配。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from . import config, http, parse

NODE_RE = r"node_\w+\.html?$"
CONTENT_RE = r"content_\w+\.html?$"

Fetcher = Callable[[str], tuple[int, str]]


def _classify_entry_failure(code: int, body_len: int) -> str:
    if code == 0:
        return "网络层失败：curl 无响应（代理/网络异常，或该站已改地址）"
    if code != 200:
        return f"入口 HTTP {code}（站点侧拒绝，或当天报纸尚未上线）"
    return f"入口内容异常（仅 {body_len} 字节）"


def scan_paper(
    paper: dict[str, str],
    day: datetime,
    kw_res: dict[str, re.Pattern[str]],
    workers: int,
    dry: bool = False,
    fetcher: Fetcher | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """扫描某家报社某一天的报纸。

    返回 ``(hits, all_articles, stats)``：
    - ``hits`` 标题或正文命中关键词的文章
    - ``all_articles`` 成功抓取到的全部文章（含未命中）
    - ``stats`` 本次扫描的统计与失败原因
    """
    fetcher = fetcher or http.fetch
    date_part = parse.date_path(day, paper["style"])
    entry = paper["tpl"].format(a=date_part)
    stats: dict[str, Any] = {
        "paper": paper["name"],
        "date": day.strftime("%Y-%m-%d"),
        "entry": entry,
        "entry_code": 0,
        "sections": 0,
        "articles": 0,
        "scanned": 0,
        "note": "",
    }

    code, html = fetcher(entry)
    stats["entry_code"] = code
    if code != 200 or len(html) < config.MIN_HTML_BYTES:
        stats["note"] = _classify_entry_failure(code, len(html))
        return [], [], stats

    base = entry.rsplit("/", 1)[0] + "/"
    nodes_all = parse.links(html, base, NODE_RE)
    # 只保留当日版面；若站点改了 URL 结构导致全被滤掉，则放宽，避免整期漏扫
    nodes = [n for n in nodes_all if date_part in n] or nodes_all
    if entry not in nodes:
        nodes.insert(0, entry)
    nodes = list(dict.fromkeys(nodes))
    stats["sections"] = len(nodes)
    if dry:
        stats["note"] = "dry-run"
        return [], [], stats

    # 版面页 → 文章链接 + 版面名
    article_section: dict[str, str] = {}

    def grab_section(node_url: str) -> tuple[str, str, list[str]]:
        section_code, section_html = fetcher(node_url)
        if section_code != 200 or len(section_html) < config.MIN_HTML_BYTES:
            return node_url, "", []
        section_base = node_url.rsplit("/", 1)[0] + "/"
        section_title = parse.extract_title(section_html)[:40]
        articles = [a for a in parse.links(section_html, section_base, CONTENT_RE) if date_part in a]
        return node_url, section_title, articles

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _node, section_title, articles in pool.map(grab_section, nodes):
            for article_url in articles:
                article_section.setdefault(article_url, section_title)

    stats["articles"] = len(article_section)

    # 文章页 → 标题 + 正文 → 匹配
    hits: list[dict[str, Any]] = []
    all_articles: list[dict[str, Any]] = []

    def grab_article(article_url: str) -> dict[str, Any] | None:
        article_code, article_html = fetcher(article_url)
        if article_code != 200 or len(article_html) < config.MIN_HTML_BYTES:
            return None
        title = parse.extract_title(article_html)
        text = parse.extract_text(article_html)
        blob = title + " " + text
        found = [match for match in (kr.search(blob) for kr in kw_res.values()) if match]
        record: dict[str, Any] = {
            "paper": paper["name"],
            "date": day.strftime("%Y-%m-%d"),
            "section": article_section.get(article_url, ""),
            "title": title,
            "url": article_url,
            "text_len": len(text),
        }
        if found:
            record["snippets"] = [parse.snippet(blob, match) for match in found[:3]]
            record["matched"] = [k for k, kr in kw_res.items() if kr.search(blob)]
        return record

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for record in pool.map(grab_article, list(article_section)):
            if record is None:
                continue
            all_articles.append(record)
            if "matched" in record:
                hits.append(record)

    stats["scanned"] = len(all_articles)
    hits.sort(key=lambda r: r["url"])
    return hits, all_articles, stats
