"""HTML 解析与关键词匹配的单元测试（纯函数，无 I/O）。"""

from __future__ import annotations

import re

import pytest

from newspaper_auto import parse


# ---------------------------------------------------------------- clean
def test_clean_removes_tags_scripts_and_entities():
    html = "<script>var x = 1;</script><style>a{color:red}</style><p>你好&nbsp;世界</p>"
    assert parse.clean(html) == "你好 世界"


def test_clean_collapses_whitespace_and_fullwidth_space():
    assert parse.clean("广东\u3000 \n\t海洋") == "广东 海洋"


def test_clean_empty():
    assert parse.clean("") == ""


# ---------------------------------------------------------------- tidy_title
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("羊城晚报 - 今日要闻", "今日要闻"),
        ("湛江日报数字报 - 第02版", "第02版"),
        ("标题 _·　", "标题"),
    ],
)
def test_tidy_title_strips_site_junk(raw, expected):
    assert parse.tidy_title(raw) == expected


# ---------------------------------------------------------------- links
def test_links_filters_absolutizes_and_dedupes():
    html = (
        '<a href="node_2.htm">a</a>'
        '<a href="./node_3.htm">b</a>'
        '<a href="node_2.htm">dup</a>'
        '<a href="javascript:void(0)">c</a>'
        '<a href="other.html">d</a>'
        '<a href="node_4.htm?x=1#frag">e</a>'
    )
    base = "http://example.com/html/2026-09/22/"
    assert parse.links(html, base, r"node_\w+\.html?$") == [
        "http://example.com/html/2026-09/22/node_2.htm",
        "http://example.com/html/2026-09/22/node_3.htm",
        "http://example.com/html/2026-09/22/node_4.htm",
    ]


def test_links_keeps_absolute_urls():
    html = '<a href="http://other.example.com/a/content_1.html">x</a>'
    assert parse.links(html, "http://base/", r"content_\w+\.html?$") == [
        "http://other.example.com/a/content_1.html",
    ]


def test_links_accepts_single_quotes_uppercase_and_unquoted_attributes():
    html = "<A HREF='node_2.htm'>a</A><a href=node_3.htm>b</a>"
    assert parse.links(html, "http://example.com/base/", r"node_\w+\.html?$") == [
        "http://example.com/base/node_2.htm",
        "http://example.com/base/node_3.htm",
    ]


# ---------------------------------------------------------------- title
def test_extract_title_prefers_h1():
    html = "<title>站点标题</title><h1>真正的文章标题</h1>"
    assert parse.extract_title(html) == "真正的文章标题"


def test_extract_title_falls_back_to_title_and_cleans_junk():
    html = "<title>湛江日报数字报 - 湛江日报</title>"
    assert parse.extract_title(html) == "湛江日报"


def test_extract_title_ignores_too_short_h1():
    html = "<title>完整标题就在这里</title><h1>短</h1>"
    assert parse.extract_title(html) == "完整标题就在这里"


def test_extract_title_missing():
    assert parse.extract_title("<div>无标题</div>") == ""


def test_extract_title_falls_back_to_title1():
    """中国教育报：标题既不在 <h1>，<title> 又是站点通用串，只能靠 <div class="title1">。"""
    html = (
        "<title>《中国教育报》电子版 - 中国教育新闻网</title>"
        '<div class="title1">真题真做练硬功 产教协同育新人</div>'
    )
    assert parse.extract_title(html) == "真题真做练硬功 产教协同育新人"


def test_extract_title_uses_title1_when_h1_too_short():
    html = "<h1>短</h1>" + '<div class="title1">这是一个足够长的完整标题</div>'
    assert parse.extract_title(html) == "这是一个足够长的完整标题"


def test_renminribao_generic_title_is_cleaned_to_empty():
    """人民日报版面页的 <title> 只有站点名，剥掉后必须是空串 —— 否则会被当成版面名。"""
    assert parse.extract_title("<title>人民日报-人民网</title>") == ""


# ---------------------------------------------------------------- page_section
def test_page_section_prefers_current_file_anchor():
    """人民日报：入口与导航同名，直接按本页文件名命中。"""
    html = (
        "<title>人民日报-人民网</title>"
        '<a href="node_01.html" id="pageLink">01版：要闻</a>'
        '<a href="node_02.html" id="pageLink">02版：要闻</a>'
    )
    url = "http://paper.people.com.cn/rmrb/pc/layout/202609/22/node_02.html"
    assert parse.page_section(html, url) == "02版：要闻"


def test_page_section_falls_back_to_pagelink_when_alias_differs():
    """中国教育报：本页是别名 node_1.htm，导航里只有哈希名，文件名匹配必然落空。"""
    html = (
        '<a href="node_144740.htm" id="pageLink">第01版 : 要闻</a>'
        '<a href="node_144741.htm">第02版 : 评论·时评</a>'
    )
    url = "https://paper.jyb.cn/zgjyb/html/2026-09/22/node_1.htm"
    assert parse.page_section(html, url) == "第01版 : 要闻"


def test_page_section_falls_back_to_colophon_without_ban_char():
    """羊城晚报：没有 pageLink，版次写成 (A01) 要闻，全程不含「版」字。"""
    html = "<title>羊城晚报</title><div><!--(版次版名)-->\n(A01 要闻)\n<!--(版次版名)--></div>"
    url = "http://ep.ycwb.com/epaper/ycwb/html/2026-09/22/node_1.htm"
    assert parse.page_section(html, url) == "(A01 要闻)"


def test_page_section_keeps_plain_section_number_without_ban_char():
    html = '<a href="node_2974.htm" id="pageLink">(A02) 焦点</a>'
    url = "http://ep.ycwb.com/epaper/ycwb/html/2026-09/22/node_2974.htm"
    assert parse.page_section(html, url) == "(A02) 焦点"


def test_page_section_strips_site_date_suffix():
    """南方日报：标题型版面名尾部拼了出版日期，需剥掉。"""
    html = '<a href="node_VD02.htm" id="pageLink">第VD02版: 遂溪视窗·关注-2026年09月23日</a>'
    url = "https://epaper.nfnews.com/nfdaily/html/202609/23/node_VD02.htm"
    assert parse.page_section(html, url) == "第VD02版: 遂溪视窗·关注"


def test_page_section_normalizes_noisy_candidate():
    """光明日报：候选串里混进日期与站名时，只保留版次版名。"""
    html = '<a href="node_02.htm" id="pageLink">2026年09月23日 Wed__第02版:要闻_光明网</a>'
    url = "https://epaper.gmw.cn/gmrb/html/layout/202609/23/node_02.htm"
    assert parse.page_section(html, url) == "第02版:要闻"


def test_page_section_returns_empty_without_marker():
    """无从判断时必须返回空串，由调用方决定是否退回 extract_title。"""
    html = "<title>某报数字报</title><a href='content_1.html'>文章</a>"
    assert parse.page_section(html, "http://x/html/2026-09/22/node_1.htm") == ""


def test_page_section_rejects_site_generic_name():
    """站点通用名不得被当成版面名。"""
    html = '<a href="node_1.htm" id="pageLink">羊城晚报</a>'
    url = "http://ep.ycwb.com/epaper/ycwb/html/2026-09/22/node_1.htm"
    assert parse.page_section(html, url) == ""


# ---------------------------------------------------------------- text
def test_extract_text_prefers_founder_content():
    html = "<body><p>导航</p><founder-content><p>真正的正文</p></founder-content></body>"
    assert parse.extract_text(html) == "真正的正文"


def test_extract_text_falls_back_to_body():
    html = "<html><head><title>t</title></head><body><div>正文内容</div></body></html>"
    assert parse.extract_text(html) == "正文内容"


def test_extract_text_falls_back_to_whole_html():
    assert parse.extract_text("<p>裸片段</p>") == "裸片段"


def test_extract_text_supports_enpcontent():
    """人民网系数字报（如人民日报）：正文在 <!--enpcontent--> 块内。"""
    html = (
        "<body><div>导航噪声</div>"
        "<div id='articleContent'><!--enpcontent--><p> 真正的正文 </p><!--/enpcontent-->"
        "<!--enpproperty <url>http://x</url>/enpproperty--></div>"
        "<div id='ozoom'><!--enpcontent--><p> 真正的正文 </p><!--/enpcontent--></div></body>"
    )
    assert parse.extract_text(html) == "真正的正文"


# ---------------------------------------------------------------- keyword
def test_make_kw_re_tolerates_tag_split_whitespace():
    assert parse.make_kw_re("广东海洋大学").search("来自广东 海洋 大学的专家")


def test_make_kw_re_is_case_insensitive():
    assert parse.make_kw_re("gdou").search("GDOU 校友")


def test_make_kw_re_does_not_match_other_schools():
    assert parse.make_kw_re("广东海洋大学").search("广东林业大学") is None


# ---------------------------------------------------------------- snippet
def test_snippet_adds_ellipsis_on_both_sides():
    text = "a" * 100 + "KEY" + "b" * 100
    match = re.search("KEY", text)
    result = parse.snippet(text, match, span=10)
    assert result.startswith("…") and result.endswith("…")
    assert "KEY" in result


def test_snippet_without_ellipsis_when_short():
    match = re.search("KEY", "KEY")
    assert parse.snippet("KEY", match) == "KEY"


# ---------------------------------------------------------------- area_rects
# fixture 逐字取自真实版面页（2026-09-23）：湛江日报 node_1.htm / 人民日报 node_01.html
def test_area_rects_from_real_zhanjiang_page(read_fixture):
    html = read_fixture("zhanjiang_node_1_area.htm")
    base = "http://paper.gdzjdaily.com.cn/html/2026-09/23/"
    rects = parse.area_rects(html, base, r"content_\w+\.html?$")

    assert len(rects) == 7
    # coords 是多边形顶点序列，取包围盒（左上 → 右下）
    assert rects[base + "content_146377_3867999.htm"] == (287, 34, 326, 46)
    assert rects[base + "content_146377_3868000.htm"] == (2, 63, 336, 159)
    assert rects[base + "content_146377_3868005.htm"] == (5, 408, 258, 546)


def test_area_rects_from_real_rmrb_page(read_fixture):
    html = read_fixture("rmrb_node_01_area.html")
    base = "http://paper.people.com.cn/rmrb/pc/layout/202609/23/"
    rects = parse.area_rects(html, base, r"content_\w+\.html?$")

    assert len(rects) == 10
    # 标签是 CamelCase 的 <Area>，坐标为小数，需容错
    url = "http://paper.people.com.cn/rmrb/pc/content/202609/23/content_30182606.html"
    assert rects[url] == (3, 97, 194, 205)


def test_area_rects_empty_without_area_tags():
    """纯链接式版面（其余四家）没有 <area> → 空 dict，绝不抛异常。"""
    html = '<div class="article-list"><a href="content_1.html">文章</a></div>'
    assert parse.area_rects(html, "http://x/2026-09/23/", r"content_\w+\.html?$") == {}


def test_area_rects_skips_malformed_or_unrelated():
    """坐标不是数字、链接不匹配、缺 coords/href 的 area 一律静默跳过。"""
    html = (
        '<area coords="not,numbers" href="content_1.html">'
        '<area coords="1,2,3,4" href="other_9.html">'
        '<area href="content_2.html">'
        '<area coords="1,2,3,4">'
        '<area coords="1,2,3" href="content_3.html">'
    )
    assert parse.area_rects(html, "http://x/", r"content_\w+\.html?$") == {}


def test_area_rects_accepts_single_quotes_and_uppercase_attributes():
    html = "<AREA COORDS='1,2,30,40' HREF='content_1.html'>"
    assert parse.area_rects(html, "http://x/", r"content_\w+\.html?$") == {
        "http://x/content_1.html": (1, 2, 30, 40)
    }


# ---------------------------------------------------------------- section_no
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("第02版：综合", "第02版"),
        ("02版：要闻", "第02版"),
        ("第2版：要闻", "第02版"),
        ("第01版 : 要闻", "第01版"),
        ("第VD02版: 遂溪视窗·关注", "VD02"),
        ("第A03版 焦点", "A03"),
        ("(A01 要闻)", "A01"),
        ("(A02) 焦点", "A02"),
        ("羊城晚报", ""),
        ("", ""),
    ],
)
def test_section_no(raw, expected):
    assert parse.section_no(raw) == expected


# ---------------------------------------------------------------- layout_mapping
def test_layout_mapping_from_real_qd01_fixture(read_fixture):
    """南方日报真实版面页（QD01 版）：提取 4 条文章的多边形包围盒并归一化为百分比坐标。"""
    html = read_fixture("nfnews_node_QD01_20260923.html")
    base = "https://epaper.nfnews.com/nfdaily/html/202609/23/"
    mapping = parse.layout_mapping(html, base, r"content_\w+\.html?$")

    assert len(mapping) == 4
    u77 = "https://epaper.nfnews.com/nfdaily/html/202609/23/content_10182077.html"
    u78 = "https://epaper.nfnews.com/nfdaily/html/202609/23/content_10182078.html"
    u79 = "https://epaper.nfnews.com/nfdaily/html/202609/23/content_10182079.html"
    u80 = "https://epaper.nfnews.com/nfdaily/html/202609/23/content_10182080.html"

    assert set(mapping.keys()) == {u77, u78, u79, u80}

    # 10182078 的 x 范围是 29.148472% ~ 51.855895%，y 范围是 0% ~ 80.320604%
    assert mapping[u78][0] == pytest.approx(0.2915, abs=1e-4)
    assert mapping[u78][1] == pytest.approx(0.0, abs=1e-4)
    assert mapping[u78][2] == pytest.approx(0.5186, abs=1e-4)
    assert mapping[u78][3] == pytest.approx(0.8032, abs=1e-4)

    # 10182077 的 y 范围是 8.143839% ~ 98.045429%
    assert mapping[u77][1] == pytest.approx(0.0814, abs=1e-4)
    assert mapping[u77][3] == pytest.approx(0.9805, abs=1e-4)

    # 检验所有 entry 均满足 (left, top, right, bottom)
    for left, top, right, bottom in mapping.values():
        assert 0 <= top < bottom <= 1
        assert 0 <= left < right <= 1


def test_layout_mapping_empty_on_page_without_mapping(read_fixture):
    """A01 版等未内嵌 mappingOriginal 的版面，返回空字典。"""
    html = read_fixture("nfnews_node_A01_20260923.html")
    base = "https://epaper.nfnews.com/nfdaily/html/202609/23/"
    assert parse.layout_mapping(html, base, r"content_\w+\.html?$") == {}


def test_layout_mapping_cross_validates_with_links():
    """mapping 中的文章链接必须在 HTML 的 <a href> 链接池中存在，孤立项被静默过滤。"""
    html = (
        '<a href="content_1.html">文章1</a>'
        "<script>\n"
        "window.layoutData = {\n"
        '  mappingOriginal: `[{"articleID":"1","mapping":["0%,0%","10%,0%","10%,20%","0%,20%"]},'
        '{"articleID":"ghost","mapping":["20%,20%","30%,20%","30%,40%","20%,40%"]}]`\n'
        "};\n"
        "</script>"
    )
    mapping = parse.layout_mapping(html, "http://example.com/2026/", r"content_\w+\.html?$")
    assert list(mapping.keys()) == ["http://example.com/2026/content_1.html"]
    assert mapping["http://example.com/2026/content_1.html"] == pytest.approx((0.0, 0.0, 0.1, 0.2))


def test_layout_mapping_rejects_malformed_and_out_of_bounds():
    """顶点不足 3 个、坐标越界、无法转 float、颠倒等异常均整条静默丢弃。"""
    html = (
        '<a href="content_1.html">1</a><a href="content_2.html">2</a>'
        '<a href="content_3.html">3</a><a href="content_4.html">4</a>'
        '<a href="content_5.html">5</a>'
        "<script>\n"
        "window.layoutData = {\n"
        "  mappingOriginal: `[\n"
        '    {"articleID":"1","mapping":["0%,0%","10%,10%"]},\n'  # 仅 2 个点
        '    {"articleID":"2","mapping":["-1%,0%","10%,0%","10%,10%","0%,10%"]},\n'  # 越界 < 0
        '    {"articleID":"3","mapping":["0%,0%","110%,0%","110%,10%","0%,10%"]},\n'  # 越界 > 100
        '    {"articleID":"4","mapping":["bad,0%","10%,0%","10%,10%","0%,10%"]},\n'  # 非数字
        '    {"articleID":"5","mapping":["5%,5%","5%,5%","5%,5%"]}\n'  # left == right or top == bottom
        "  ]`\n"
        "};\n"
        "</script>"
    )
    mapping = parse.layout_mapping(html, "http://example.com/", r"content_\w+\.html?$")
    assert mapping == {}


def test_layout_mapping_syntax_error_or_missing_returns_empty():
    """JSON 解析失败或无 mappingOriginal 时平稳返回空字典。"""
    assert parse.layout_mapping("<html></html>", "http://x/", r".*") == {}
    bad_json = "<script>window.layoutData = { mappingOriginal: `{broken json` };</script>"
    assert parse.layout_mapping(bad_json, "http://x/", r".*") == {}
