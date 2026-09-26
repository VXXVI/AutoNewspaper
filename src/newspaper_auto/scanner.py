"""扫描编排：单家报社单日 → 全部版面 → 全部文章 → 标题+正文匹配。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from . import config, http, papers, parse

NODE_RE = r"node_\w+\.html?$"
CONTENT_RE = r"content_\w+\.html?$"

Fetcher = Callable[[str], tuple[int, str]]


def _classify_entry_failure(code: int, body_len: int) -> str:
    if code == 0:
        return "网络层失败：curl 无响应（代理/网络异常，或该站已改地址）"
    if code != 200:
        return f"入口 HTTP {code}（站点侧拒绝，或当天报纸尚未上线）"
    return f"入口内容异常（仅 {body_len} 字节）"


def _entry_failure_kind(code: int, body_len: int) -> str:
    if code == 0:
        return "network"
    if code != 200:
        return "http"
    if body_len < config.MIN_HTML_BYTES:
        return "too_short"
    return ""


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


def merge_geometry(
    target: dict[str, dict[str, Any]],
    rects: dict[str, tuple[int, int, int, int]],
    mapping: dict[str, tuple[float, float, float, float]],
    articles: list[str],
) -> None:
    """把本版文章位置并入 ``target``，分三条互斥的优先级路径。

    1. **有 ``<area>`` 映射**（优先级 1）：先只保留「属于本版文章」的 area，再用这些
       area 的坐标边界归一化，``index`` 按 ``y1`` 升序编号，写出原坐标与
       ``left/top/right/bottom``，``basis="area"``。``total`` 恒等于本版有坐标的文章数，
       编号连续无空号；若没有任何文章被 area 覆盖，则回退到下一级而不是留下空结果。
    2. **内嵌百分比映射**（优先级 2，如南方日报 ``window.layoutData.mappingOriginal``）：
       同样先按本版文章过滤，再按 (top, left, 原始出现顺序) 升序编号，index 从 1 开始，写
       ``left`` / ``top`` / ``right`` / ``bottom`` / ``index`` / ``total`` / ``basis="mapping"``，
       不写 ``x1..y2``。
    3. **纯链接式兜底**（优先级 3）：前两级都没有可用项时，按 ``articles`` 的出现顺序编号，
       只写 ``index`` / ``total`` / ``basis="order"``，绝不编造坐标。
    """
    article_set = set(articles)

    if rects:
        # 只对本版文章做归一化与编号：否则跨日期 / 非本版 area 会把 total 撑大、
        # 让 index 出现空号（与 build_section_layout 的口径也会不一致）。
        usable_rects = {url: rect for url, rect in rects.items() if url in article_set}
        if usable_rects:
            max_x2 = max(rect[2] for rect in usable_rects.values())
            max_y2 = max(rect[3] for rect in usable_rects.values())
            if max_x2 > 0 and max_y2 > 0:
                ordered = sorted(usable_rects.items(), key=lambda item: item[1][1])
                total = len(ordered)
                for index, (url, (x1, y1, x2, y2)) in enumerate(ordered, 1):
                    target[url] = {
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "left": round(x1 / max_x2, 4),
                        "top": round(y1 / max_y2, 4),
                        "right": round(x2 / max_x2, 4),
                        "bottom": round(y2 / max_y2, 4),
                        "index": index,
                        "total": total,
                        "basis": "area",
                    }
                return

    if mapping:
        usable_mapping = {url: box for url, box in mapping.items() if url in article_set}
        if usable_mapping:
            raw_items = list(usable_mapping.items())
            ordered_mapping = sorted(
                enumerate(raw_items),
                key=lambda entry: (entry[1][1][1], entry[1][1][0], entry[0]),
            )
            total = len(ordered_mapping)
            for index, (_orig_idx, (url, (left, top, right, bottom))) in enumerate(ordered_mapping, 1):
                target[url] = {
                    "left": round(left, 4),
                    "top": round(top, 4),
                    "right": round(right, 4),
                    "bottom": round(bottom, 4),
                    "index": index,
                    "total": total,
                    "basis": "mapping",
                }
            return

    total = len(articles)
    if total == 0:
        return
    for index, url in enumerate(articles, 1):
        target[url] = {"index": index, "total": total, "basis": "order"}


def build_section_layout(
    rects: dict[str, tuple[int, int, int, int]],
    mapping: dict[str, tuple[float, float, float, float]],
    articles: list[str],
) -> dict[str, Any] | None:
    """把网站提供的版面坐标归一化成前端可直接绘制的 0~1 布局。

    只有站点提供 ``<area>`` 或百分比 mapping 时才返回布局。纯链接顺序无法推导
    二维位置，必须返回 None，避免看板画出一个看似精确、实为猜测的版面图。
    """
    article_set = set(articles)
    if rects:
        usable = [(url, rect) for url, rect in rects.items() if url in article_set]
        if not usable:
            return None
        max_x2 = max(rect[2] for _url, rect in usable)
        max_y2 = max(rect[3] for _url, rect in usable)
        if max_x2 <= 0 or max_y2 <= 0:
            return None
        items = [
            {
                "url": url,
                "left": round(x1 / max_x2, 4),
                "top": round(y1 / max_y2, 4),
                "right": round(x2 / max_x2, 4),
                "bottom": round(y2 / max_y2, 4),
            }
            for url, (x1, y1, x2, y2) in usable
        ]
        return {
            "basis": "area",
            "aspect_ratio": round(max_x2 / max_y2, 4),
            "items": items,
        }

    if mapping:
        usable_mapping = [(url, box) for url, box in mapping.items() if url in article_set]
        if not usable_mapping:
            return None
        return {
            "basis": "mapping",
            # mappingOriginal 不提供原图宽高；0.72 是常见报纸版面比例，只影响
            # 容器外形，不影响每个矩形的相对坐标。
            "aspect_ratio": 0.72,
            "items": [
                {
                    "url": url,
                    "left": round(left, 4),
                    "top": round(top, 4),
                    "right": round(right, 4),
                    "bottom": round(bottom, 4),
                }
                for url, (left, top, right, bottom) in usable_mapping
            ],
        }
    return None


def probe_paper(
    paper: dict[str, Any],
    day: datetime,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    """轻量验证一条报社配置：入口、版面规则与文章规则各走一步。

    与完整扫描不同，本函数不抓文章正文、不写任何文件；最多抓入口和一个额外版面页，
    用于自定义报社表单的“试抓”。
    """
    base_fetcher: Fetcher = fetcher or http.fetch
    fetch = _paper_fetch(paper, base_fetcher)
    date_part = papers.format_date_path(paper, day)
    entry = paper["tpl"].format(a=date_part)
    node_pattern = str(paper.get("node_pattern") or NODE_RE)
    article_pattern = str(paper.get("article_pattern") or CONTENT_RE)

    code, html = fetch(entry)
    result: dict[str, Any] = {
        "entry": entry,
        "entry_code": code,
        "sections": 0,
        "sample_articles": 0,
        "note": "",
    }
    if code != 200 or len(html) < config.MIN_HTML_BYTES:
        result["note"] = _classify_entry_failure(code, len(html))
        return result

    base = entry.rsplit("/", 1)[0] + "/"
    nodes = parse.links(html, base, node_pattern)
    entry_articles = parse.links(html, base, article_pattern)
    result["sections"] = len(nodes) or (1 if entry_articles else 0)
    sample_articles = entry_articles

    # 入口只列版面、不列文章时，再抓一个真实版面验证 article_pattern。
    if not sample_articles and nodes:
        sample_node = nodes[0]
        section_code, section_html = fetch(sample_node)
        if section_code == 200 and len(section_html) >= config.MIN_HTML_BYTES:
            section_base = sample_node.rsplit("/", 1)[0] + "/"
            sample_articles = parse.links(section_html, section_base, article_pattern)
        else:
            result["note"] = (
                f"入口正常，但样例版面 HTTP {section_code}"
                if section_code != 200
                else f"入口正常，但样例版面内容过短（{len(section_html)} 字节）"
            )

    result["sample_articles"] = len(sample_articles)
    if not nodes and not entry_articles:
        result["note"] = "入口正常，但版面与文章链接规则均未匹配"
    elif not sample_articles and not result["note"]:
        result["note"] = "版面规则已匹配，但文章链接规则未匹配"
    return result


def scan_paper(
    paper: dict[str, Any],
    day: datetime,
    kw_res: dict[str, re.Pattern[str]],
    workers: int,
    dry: bool = False,
    fetcher: Fetcher | None = None,
    cancel: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """扫描某家报社某一天的报纸。

    返回 ``(hits, all_articles, stats)``：
    - ``hits`` 标题或正文命中关键词的文章
    - ``all_articles`` 成功抓取到的全部文章（含未命中）
    - ``stats`` 本次扫描的统计与失败原因

    ``cancel`` 是协作式取消回调：在**每个版面 / 每篇文章抓取前**检查，返回真则跳过该
    抓取（不再发新的 HTTP 请求），从而把取消延迟从「报社 × 日期」单元边界缩短到
    「单个在途请求」。已提交的请求无法中断，但排队中的任务会立即空转结束。
    """
    base_fetcher: Fetcher = fetcher or http.fetch
    _fetch = _paper_fetch(paper, base_fetcher)

    def _cancelled() -> bool:
        return cancel is not None and cancel()

    date_part = papers.format_date_path(paper, day)
    entry = paper["tpl"].format(a=date_part)
    node_pattern = str(paper.get("node_pattern") or NODE_RE)
    article_pattern = str(paper.get("article_pattern") or CONTENT_RE)
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
        "failed_urls": [],  # [{url, code, reason}, ...] 代表性失败样本（最多 3 条）
        "note": "",
        "ok": False,
        "failure_kind": "",
    }

    code, html = _fetch(entry)
    stats["entry_code"] = code
    if code != 200 or len(html) < config.MIN_HTML_BYTES:
        stats["note"] = _classify_entry_failure(code, len(html))
        stats["failure_kind"] = _entry_failure_kind(code, len(html))
        return [], [], stats

    base = entry.rsplit("/", 1)[0] + "/"
    nodes_all = parse.links(html, base, node_pattern)
    entry_articles = parse.links(html, base, article_pattern)
    if not nodes_all and not entry_articles:
        stats["note"] = "入口结构异常：HTTP 200 但未找到版面或文章链接（疑似改版/挑战页）"
        stats["failure_kind"] = "entry_structure"
        stats["failed_urls"] = [{"url": entry, "code": code, "reason": "未找到 node/content 链接"}]
        return [], [], stats
    # 可选：按正则排除噪声版面链接（如人民日报 pc 版里的「手机版」/pad/ 镜像）
    node_exclude = paper.get("node_exclude")
    if node_exclude:
        nodes_all = [n for n in nodes_all if not re.search(node_exclude, n)]
    nodes = [n for n in nodes_all if date_part in n] or nodes_all
    if entry not in nodes:
        nodes.insert(0, entry)
    nodes = list(dict.fromkeys(nodes))
    stats["sections"] = len(nodes)
    if dry:
        stats["note"] = "dry-run"
        stats["ok"] = True
        return [], [], stats

    # 版面页 → 文章链接 + 版面名 + 图像映射坐标（仅人民日报 / 湛江日报有）
    article_section: dict[str, str] = {}
    article_geometry: dict[str, dict[str, Any]] = {}
    article_layout: dict[str, dict[str, Any]] = {}
    section_failed_records: list[dict[str, Any]] = []

    def grab_section(
        node_url: str,
    ) -> tuple[
        str,
        str,
        list[str],
        dict[str, tuple[int, int, int, int]],
        dict[str, tuple[float, float, float, float]],
        int,
        str,
    ]:
        if _cancelled():
            # 取消：不发请求，也不计入失败样本
            return node_url, "", [], {}, {}, 0, ""
        section_code, section_html = _fetch(node_url)
        if section_code != 200 or len(section_html) < config.MIN_HTML_BYTES:
            reason = (
                f"HTTP {section_code}" if section_code != 200 else f"内容过短（{len(section_html)} 字节）"
            )
            return node_url, "", [], {}, {}, section_code, reason
        section_base = node_url.rsplit("/", 1)[0] + "/"
        # 版面名优先走 page_section（pageLink / 版次版名注释，权威且干净），
        # 取不到再退回页面 title。反过来「title 先行」会让多家报社拿到错值：
        # 人民日报→「人民日报-人民网」、羊城晚报→「羊城晚报」、湛江日报→「要闻」（无版次）。
        section_title = (
            parse.page_section(section_html, node_url)[:40] or parse.extract_title(section_html)[:40]
        )
        articles = [a for a in parse.links(section_html, section_base, article_pattern) if date_part in a]
        rects = parse.area_rects(section_html, section_base, article_pattern)
        mapping = parse.layout_mapping(section_html, section_base, article_pattern)
        return node_url, section_title, articles, rects, mapping, section_code, ""

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _node, section_title, articles, rects, mapping, sec_code, reason in pool.map(grab_section, nodes):
            if reason:
                section_failed_records.append({"url": _node, "code": sec_code, "reason": reason})
            merge_geometry(article_geometry, rects, mapping, articles)
            layout = build_section_layout(rects, mapping, articles)
            for article_url in articles:
                article_section.setdefault(article_url, section_title)
                if layout is not None:
                    article_layout.setdefault(article_url, layout)

    stats["sections_failed"] = len(section_failed_records)
    stats["articles"] = len(article_section)
    if not article_section:
        stats["note"] = "版面结构异常：未从任何版面解析到文章链接"
        stats["failure_kind"] = "section_structure"
        stats["failed_urls"] = (
            section_failed_records or [{"url": entry, "code": 200, "reason": "所有版面均无 content 链接"}]
        )[:3]
        return [], [], stats

    # 文章页 → 标题 + 正文 → 匹配
    hits: list[dict[str, Any]] = []
    all_articles: list[dict[str, Any]] = []
    article_failed_records: list[dict[str, Any]] = []

    def grab_article(article_url: str) -> dict[str, Any] | None:
        if _cancelled():
            return None
        article_code, article_html = _fetch(article_url)
        if article_code != 200 or len(article_html) < config.MIN_HTML_BYTES:
            reason = (
                f"HTTP {article_code}" if article_code != 200 else f"内容过短（{len(article_html)} 字节）"
            )
            article_failed_records.append({"url": article_url, "code": article_code, "reason": reason})
            return None
        title = parse.extract_title(article_html)
        text = parse.extract_text(article_html)
        if not title and not text:
            article_failed_records.append(
                {"url": article_url, "code": article_code, "reason": "标题和正文均为空（疑似文章页改版）"}
            )
            return None
        blob = title + " " + text
        found = [match for match in (kr.search(blob) for kr in kw_res.values()) if match]
        section = article_section.get(article_url, "")
        record: dict[str, Any] = {
            "paper": paper["name"],
            "date": day.strftime("%Y-%m-%d"),
            "section": section,
            "section_no": parse.section_no(section),
            "title": title,
            "url": article_url,
            "text_len": len(text),
        }
        geometry = article_geometry.get(article_url)
        if geometry:
            record["geometry"] = geometry
        layout = article_layout.get(article_url)
        if layout:
            record["layout"] = layout
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
    stats["failed_urls"] = (section_failed_records + article_failed_records)[:3]
    if not all_articles:
        stats["note"] = "文章抓取/解析全部失败"
        stats["failure_kind"] = "all_articles_failed"
    else:
        stats["ok"] = True
    hits.sort(key=lambda r: r["url"])
    return hits, all_articles, stats
