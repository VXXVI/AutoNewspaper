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


def _paper_fetch(paper: dict[str, Any], base_fetcher: Fetcher) -> Fetcher:
    """根据报社的 timeout/retries 覆盖键，包装出一个专用的 fetcher。

    若报社未设覆盖键（大多数），返回的 fetcher 行为与 base_fetcher 完全相同，
    保留测试注入（fake fetcher）的语义不变。
    """
    timeout = paper.get("timeout")
    retries = paper.get("retries")
    if (timeout is None and retries is None) or base_fetcher is not http.fetch:
        # 测试注入：base_fetcher 已被替换为 fake fetcher，不包装，直接透传
        return base_fetcher

    def wrapped(url: str) -> tuple[int, str]:
        return http.fetch(
            url,
            timeout=timeout if timeout is not None else config.FETCH_TIMEOUT,
            retries=retries if retries is not None else config.FETCH_RETRIES,
        )

    return wrapped


def scan_paper(
    paper: dict[str, Any],
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
    base_fetcher: Fetcher = fetcher or http.fetch
    _fetch = _paper_fetch(paper, base_fetcher)

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
        "sections_failed": 0,
        "articles_failed": 0,
        "failed_urls": [],  # [(url, http_code), ...] 代表性失败样本（最多 3 条）
        "note": "",
    }

    code, html = _fetch(entry)
    stats["entry_code"] = code
    if code != 200 or len(html) < config.MIN_HTML_BYTES:
        stats["note"] = _classify_entry_failure(code, len(html))
        return [], [], stats

    base = entry.rsplit("/", 1)[0] + "/"
    nodes_all = parse.links(html, base, NODE_RE)
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
    section_failed_records: list[tuple[str, int]] = []

    def grab_section(node_url: str) -> tuple[str, str, list[str], int]:
        section_code, section_html = _fetch(node_url)
        if section_code != 200 or len(section_html) < config.MIN_HTML_BYTES:
            return node_url, "", [], section_code
        section_base = node_url.rsplit("/", 1)[0] + "/"
        section_title = parse.extract_title(section_html)[:40]
        articles = [a for a in parse.links(section_html, section_base, CONTENT_RE) if date_part in a]
        return node_url, section_title, articles, section_code

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _node, section_title, articles, sec_code in pool.map(grab_section, nodes):
            if not articles and sec_code != 200:
                section_failed_records.append((_node, sec_code))
            for article_url in articles:
                article_section.setdefault(article_url, section_title)

    stats["sections_failed"] = len(section_failed_records)
    stats["articles"] = len(article_section)

    # 文章页 → 标题 + 正文 → 匹配
    hits: list[dict[str, Any]] = []
    all_articles: list[dict[str, Any]] = []
    article_failed_records: list[tuple[str, int]] = []

    def grab_article(article_url: str) -> dict[str, Any] | None:
        article_code, article_html = _fetch(article_url)
        if article_code != 200 or len(article_html) < config.MIN_HTML_BYTES:
            article_failed_records.append((article_url, article_code))
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
    stats["articles_failed"] = len(article_failed_records)
    # 记录代表性失败样本（最多 3 条），用于报告中展示
    stats["failed_urls"] = [
        {"url": u, "code": c} for u, c in (section_failed_records + article_failed_records)[:3]
    ]
    hits.sort(key=lambda r: r["url"])
    return hits, all_articles, stats
