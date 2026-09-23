"""HTML 解析与关键词匹配（纯函数，无 I/O）。

方正系电子报系统的页面结构高度统一：

    第 1 版页   → 含当日全部版面链接   (node_*)
    版面页      → 含本版全部文章链接   (content_*)
    文章页      → 标题 + 正文（正文多在自定义标签 ``<founder-content>`` 内）
"""

from __future__ import annotations

import re
from datetime import datetime
from html import unescape
from urllib.parse import urljoin

from . import config

_HREF_RE = re.compile(r'href="([^"]+)"')
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_FOUNDER_RE = re.compile(r"<founder-content[^>]*>(.*?)</founder-content>", re.S | re.I)
# 人民网系数字报（如人民日报）：正文包在 <!--enpcontent--> … <!--/enpcontent--> 之间
_ENP_RE = re.compile(r"<!--\s*enpcontent\s*-->(.*?)<!--\s*/enpcontent\s*-->", re.S | re.I)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.S | re.I)


def clean(html_fragment: str) -> str:
    """去标签、去脚本样式、反转义实体、压缩空白。"""
    if not html_fragment:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", html_fragment)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text).replace("\u3000", " ").replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def tidy_title(title: str) -> str:
    """剔除站点后缀与首尾噪声字符。"""
    for pattern in config.SITE_JUNK:
        title = re.sub(pattern, "", title)
    return title.strip(" -－_|·　")


def links(html: str, base: str, suffix_re: str) -> list[str]:
    """抽取匹配 ``suffix_re`` 的链接，绝对化、去重并保持出现顺序。"""
    out: list[str] = []
    for href in _HREF_RE.findall(html):
        href = href.split("?")[0].split("#")[0].strip()
        if not href or href.startswith("javascript:"):
            continue
        if re.search(suffix_re, href):
            absolute = urljoin(base, href)
            if absolute not in out:
                out.append(absolute)
    return out


def extract_title(html: str) -> str:
    """优先取 ``<h1>``，过短则退回 ``<title>``。"""
    match = _H1_RE.search(html)
    if match:
        title = tidy_title(clean(match.group(1)))
        if len(title) >= 4:
            return title
    match = _TITLE_RE.search(html)
    return tidy_title(clean(match.group(1))) if match else ""


def extract_text(html: str) -> str:
    """正文：优先 ``<founder-content>``，其次 ``<!--enpcontent-->``（人民网系），
    再退回 ``<body>``，最后整页。"""
    match = _FOUNDER_RE.search(html)
    segment = match.group(1) if match else None
    if not segment:
        match = _ENP_RE.search(html)
        segment = match.group(1) if match else None
    if not segment:
        match = _BODY_RE.search(html)
        segment = match.group(1) if match else html
    return clean(segment)


def make_kw_re(keyword: str) -> re.Pattern[str]:
    """关键词正则：允许字符间出现空白，防止标签把中文切开导致漏匹配。"""
    return re.compile(r"\s*".join(re.escape(ch) for ch in keyword), re.I)


def snippet(text: str, match: re.Match[str], span: int = 70) -> str:
    """截取命中位置前后 ``span`` 字符的上下文。"""
    start = max(0, match.start() - span)
    end = min(len(text), match.end() + span)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def date_path(day: datetime, style: str) -> str:
    """按报社风格生成日期目录片段。"""
    return day.strftime("%Y%m/%d") if style == "ymd" else day.strftime("%Y-%m/%d")
