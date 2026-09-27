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

import json
import math
import re
import unicodedata
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from . import config

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
# 从已规整的版面名里抠出版号（字母前缀 + 数字），两种形态：
#   含「版」字：第02版 / 02版 / 第VD02版 / 第A03版
#   纯版次  ：(A01 要闻) / (A02) 焦点
_SECTION_NO_VERSION_RE = re.compile(r"第?\s*([A-Za-z]{0,3})\s*(\d{1,3})\s*版")
_SECTION_PLAIN_VERSION_RE = re.compile(r"^[（(]?\s*([A-Za-z]{1,3})\s*(\d{1,3})\s*[）)]?")
_MAPPING_ORIGINAL_RE = re.compile(r"mappingOriginal\s*:\s*`([^`]*)`", re.S)


class _StartTagParser(HTMLParser):
    """容错提取起始标签与属性。

    标准库解析器会统一标签/属性大小写，并同时支持单引号、双引号
    与无引号属性，避免站点只改了 HTML 书写形式就静默漏链接。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def _append(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), {key.lower(): value or "" for key, value in attrs}))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append(tag, attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append(tag, attrs)


def _start_tags(html: str) -> list[tuple[str, dict[str, str]]]:
    parser = _StartTagParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # 对极端损坏的页面继续返回已成功解析的前半部分。
        pass
    return parser.tags


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
    for _tag, attrs in _start_tags(html):
        href = attrs.get("href", "")
        href = href.split("?")[0].split("#")[0].strip()
        if not href or href.lower().startswith("javascript:"):
            continue
        if re.search(suffix_re, href):
            absolute = urljoin(base, href)
            if absolute not in out:
                out.append(absolute)
    return out


def area_rects(html: str, base: str, suffix_re: str) -> dict[str, tuple[int, int, int, int]]:
    """从版面页的 ``<area>`` 图像映射里抽「文章绝对URL → 版面坐标」。

    返回 ``{绝对URL: (x1, y1, x2, y2)}``，坐标取该 area 顶点序列的**包围盒**
    （左上 → 右下），与版面图同坐标系。站点实测只给多边形顶点，故统一按包围盒归一，
    矩形写法 ``coords="x1,y1,x2,y2"`` 是它的特例。

    ``suffix_re`` 与 :func:`links` 一致，用来只保留文章链接。没有 ``<area>``
    （六家里的其余四家是纯链接式版面）或坐标/链接解析失败时**静默跳过**，绝不抛异常，
    此时返回空 dict，由调用方降级处理。
    """
    out: dict[str, tuple[int, int, int, int]] = {}
    for tag, attrs in _start_tags(html):
        if tag != "area":
            continue
        href = attrs.get("href", "").split("?")[0].split("#")[0].strip()
        coords = attrs.get("coords", "")
        if not href or href.lower().startswith("javascript:") or not re.search(suffix_re, href):
            continue
        try:
            nums = [float(part) for part in coords.split(",") if part.strip()]
        except ValueError:
            continue
        nums = nums[: len(nums) // 2 * 2]  # 奇数个坐标时丢掉落单的最后一个
        if len(nums) < 4:
            continue
        xs, ys = nums[0::2], nums[1::2]
        rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
        out.setdefault(urljoin(base, href), rect)
    return out


def layout_mapping(html: str, base: str, suffix_re: str) -> dict[str, tuple[float, float, float, float]]:
    """返回 {文章绝对URL: (left, top, right, bottom)}，四个值均为 0–1 的版面占比。

    与 area_rects 的区别：area_rects 是版面图像素坐标，本函数是百分比占比。
    取不到时返回空 dict，绝不抛异常。
    """
    if not html:
        return {}
    match = _MAPPING_ORIGINAL_RE.search(html)
    if not match:
        return {}
    raw_json = match.group(1).strip()
    try:
        data = json.loads(raw_json)
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}

    valid_urls = set(links(html, base, suffix_re))
    if not valid_urls:
        return {}

    rects: dict[str, tuple[float, float, float, float]] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        aid = item.get("articleID")
        if aid is None:
            continue
        url = urljoin(base, f"content_{aid}.html")
        if url not in valid_urls:
            continue

        raw_mapping = item.get("mapping")
        if not isinstance(raw_mapping, list) or len(raw_mapping) < 3:
            continue

        xs: list[float] = []
        ys: list[float] = []
        valid_points = True
        for pt_str in raw_mapping:
            if not isinstance(pt_str, str):
                valid_points = False
                break
            parts = pt_str.split(",")
            if len(parts) != 2:
                valid_points = False
                break
            px_str, py_str = parts[0].strip(), parts[1].strip()
            if not (px_str.endswith("%") and py_str.endswith("%")):
                valid_points = False
                break
            try:
                vx = float(px_str[:-1].strip())
                vy = float(py_str[:-1].strip())
            except ValueError:
                valid_points = False
                break
            if math.isnan(vx) or math.isnan(vy) or not (0.0 <= vx <= 100.0 and 0.0 <= vy <= 100.0):
                valid_points = False
                break
            xs.append(vx / 100.0)
            ys.append(vy / 100.0)

        if not valid_points or not xs or not ys:
            continue

        left = min(xs)
        top = min(ys)
        right = max(xs)
        bottom = max(ys)

        if left >= right or top >= bottom:
            continue

        rects[url] = (left, top, right, bottom)

    return rects


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


def section_no(section: str) -> str:
    """从版面名字符串里抽出规范化的「版号」，供排序/统计用（不改动原 ``section`` 字段）。

    实测形态与产出：

    - ``第02版：综合`` / ``02版：要闻`` / ``第2版：要闻`` → ``第02版``
    - ``第VD02版: 遂溪视窗·关注`` → ``VD02``
    - ``(A01 要闻)`` / ``(A02) 焦点`` → ``A01`` / ``A02``

    取不到版号（如站点通用名、空串）时返回空串。
    """
    text = (section or "").strip()
    match = _SECTION_NO_VERSION_RE.search(text) or _SECTION_PLAIN_VERSION_RE.match(text)
    if not match:
        return ""
    letters, digits = match.group(1), match.group(2)
    return f"{letters.upper()}{digits}" if letters else f"第{int(digits):02d}版"


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


def normalize_for_match(text: str) -> str:
    """匹配前的归一化：用 NFKC 把全角字母/数字/括号等折叠成半角。

    关键词与正文两侧必须走同一口径（``make_kw_re`` 归一化关键词，``scanner``
    归一化正文），否则用户打成全角「ＡＩ」而正文是半角「AI」时会**静默漏匹配**——
    对监测工具来说，漏报比误报更致命。
    """
    return unicodedata.normalize("NFKC", text)


def make_kw_re(keyword: str) -> re.Pattern[str]:
    """关键词正则：先做 NFKC 归一化（全角→半角），再允许字符间出现空白，
    防止标签把中文切开导致漏匹配；大小写不敏感。"""
    normalized = normalize_for_match(keyword)
    return re.compile(r"\s*".join(re.escape(ch) for ch in normalized), re.I)


def snippet(text: str, match: re.Match[str], span: int = 70) -> str:
    """截取命中位置前后 ``span`` 字符的上下文。"""
    start = max(0, match.start() - span)
    end = min(len(text), match.end() + span)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
