"""HTML 解析与关键词匹配（纯函数，无 I/O）。

方正系电子报系统的页面结构高度统一：

    第 1 版页   → 含当日全部版面链接   (node_*)
    版面页      → 含本版全部文章链接   (content_*) + 版面名（见 ``page_section``）
    文章页      → 标题 + 正文（正文多在自定义标签 ``<founder-content>`` 内）

不过六家站点各有偏离「标准方正系」的地方（标题位置、正文容器、版面名来源都不完全一致），
所以 ``extract_title`` / ``extract_text`` / ``page_section`` 都是**多级兜底**实现；
改动前请先看各自 docstring 里记的站点实测依据，并跑一遍 ``tests/``。
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

# 方正系部分站点（如中国教育报 paper.jyb.cn）的文章标题不用 <h1>，而在 <div class="title1"> 内
_TITLE1_RE = re.compile(r'<[a-z]+[^>]*class="title1"[^>]*>(.*?)</[a-z]+>', re.S | re.I)

# 版面名：方正系站点把「当前版面」标注在这个锚点上
_PAGELINK_RE = re.compile(r'<a[^>]*id="pageLink"[^>]*>(.*?)</a>', re.S | re.I)
# 羊城晚报没有 pageLink，版面名只存在这对注释之间：<!--(版次版名)--> (A01 要闻) <!--(版次版名)-->
_COLOPHON_RE = re.compile(r"<!--\s*[（(]?版次版名[）)]?\s*-->\s*(.*?)\s*<!--", re.S)
# 从候选串里抠出「第XX版：版名」，压掉站点自己拼的日期/站名前缀
# 形如：02版：要闻 / 第01版 : 要闻 / 第VD02版: 遂溪视窗·关注 / 第A03版 焦点
_SECTION_NO_RE = re.compile(r"第?\s*[A-Za-z]{0,3}\s*\d{1,3}\s*版\s*[:：]?\s*[^_|]*")
# 标题型版面名常见的日期尾巴：-2026年09月23日 / -2026-09-23
_DATE_TAIL_RE = re.compile(r"\s*[-－]\s*\d{4}\s*[-年]\s*\d{1,2}\s*[-月]\s*\d{1,2}\s*日?\s*$")
_TRAIL_JUNK_RE = re.compile(r"[_|]\s*$")
# 纯版次写法（不含「版」字），如羊城晚报的「(A01) 要闻」
_SECTION_PLAIN_NO_RE = re.compile(r"^[（(]?\s*[A-Za-z]{1,3}\d{1,3}\s*[）)]?\s*\S")


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
    """优先取 ``<h1>``，其次 ``<div class="title1">``，最后退回 ``<title>``。

    中间这一层不是可有可无的兜底：中国教育报（paper.jyb.cn）的文章标题既不在 ``<h1>`` 里，
    其 ``<title>`` 又是站点通用串（"《中国教育报》电子版 - 中国教育新闻网 - …"），
    被 ``SITE_JUNK`` 清洗后正好得到**空字符串** —— 表现为命中记录标题、版面名全空。
    """
    match = _H1_RE.search(html)
    if match:
        title = tidy_title(clean(match.group(1)))
        if len(title) >= 4:
            return title
    match = _TITLE1_RE.search(html)
    if match:
        title = tidy_title(clean(match.group(1)))
        if len(title) >= 4:
            return title
    match = _TITLE_RE.search(html)
    return tidy_title(clean(match.group(1))) if match else ""


def _looks_like_section(text: str) -> bool:
    """判断一段文本是不是版面名（而非站点名或文章标题）。

    两种形态都要放行：

    - 含「版」字 —— 如「第04版 : 教育视窗」「02版：要闻」
    - 纯版次写法 —— 如「(A01) 要闻」。羊城晚报的版次全程不带「版」字，
      只靠 `"版" in text` 会把它的版面名一律判空，退化成站点名「羊城晚报」。

    候选串都取自版面导航锚点与版面注释，本身不会混入文章标题，所以放宽是安全的。
    """
    if not (2 <= len(text) <= 40):
        return False
    if "版" in text:
        return True
    return bool(_SECTION_PLAIN_NO_RE.match(text))


def page_section(html: str, url: str) -> str:
    """从版面页里取「第几版 + 版名」。三个来源按优先级依次尝试：

    1. ``href`` 等于本页文件名的锚点 —— 人民日报、光明日报等站点入口与导航同名；
    2. ``id="pageLink"`` 标注的锚点 —— 方正系通用标记。中国教育报的入口是稳定别名
       （``node_1.htm``）而导航里写的是内部哈希名（``node_144740.htm``），两者对不上，
       只能靠这条兜底；
    3. ``<!--(版次版名)-->`` 注释里的文本 —— 羊城晚报没有 pageLink，版面名只存在注释里。

    取到后统一规整成「第XX版：版名」，避免把站点自己拼的日期尾巴（如
    ``第VD02版: 遂溪视窗·关注-2026年09月23日``）带进报告。

    取不到时返回空串，由调用方决定是否退回 ``extract_title``。
    """
    filename = url.rsplit("/", 1)[-1]
    candidates = re.findall(r'<a[^>]*href="' + re.escape(filename) + r'"[^>]*>(.*?)</a>', html, re.S | re.I)
    candidates += _PAGELINK_RE.findall(html)
    candidates += _COLOPHON_RE.findall(html)

    for raw in candidates:
        text = tidy_title(clean(raw))
        if not _looks_like_section(text):
            continue
        match = _SECTION_NO_RE.search(text)
        if match:
            text = _TRAIL_JUNK_RE.sub("", match.group(0)).strip()
        return _DATE_TAIL_RE.sub("", text).strip()
    return ""


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
