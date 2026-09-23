"""扫描编排的单元测试（注入 fake fetcher，不访问网络）。"""

from __future__ import annotations

import pytest

from newspaper_auto import config, parse, scanner

KW = {"广东海洋大学": parse.make_kw_re("广东海洋大学")}


def _hit_names(hits) -> set[str]:
    return {hit["url"].rsplit("/", 1)[-1] for hit in hits}


def test_happy_path_scan(zhanjiang_paper, fixed_day, site_fetcher):
    hits, _articles, stats = scanner.scan_paper(
        zhanjiang_paper, fixed_day, KW, workers=4, fetcher=site_fetcher(fixed_day)
    )

    assert stats["entry_code"] == 200
    assert stats["sections"] == 3  # node_1 + node_2 + node_3（跨日期 node 被过滤）
    assert stats["articles"] == 4  # 101/102/103/104（跨日期 content 被过滤）
    assert stats["scanned"] == 4
    assert stats["note"] == ""

    # 101 正文命中、102 标题命中、104 标签切断后仍命中；103 不命中
    assert _hit_names(hits) == {"content_101.html", "content_102.html", "content_104.html"}
    assert all(hit["matched"] == ["广东海洋大学"] for hit in hits)
    assert all(hit["snippets"] for hit in hits)


def test_hit_matched_in_title_only(read_fixture):
    """content_102 关键词只在标题里，正文不含 —— 验证标题通道独立生效。"""
    html = read_fixture("article_content_102.html")
    assert "广东海洋大学" in parse.extract_title(html)
    assert "广东海洋大学" not in parse.extract_text(html)


def test_section_name_is_recorded(zhanjiang_paper, fixed_day, site_fetcher):
    hits, _articles, _stats = scanner.scan_paper(
        zhanjiang_paper, fixed_day, KW, workers=4, fetcher=site_fetcher(fixed_day)
    )
    by_name = {hit["url"].rsplit("/", 1)[-1]: hit["section"] for hit in hits}
    assert by_name["content_101.html"] == "第02版：综合"
    assert by_name["content_104.html"] == "第03版：教育"


def test_jyb_section_and_title_both_use_fallbacks(jyb_paper, fixed_day, jyb_fetcher):
    """中国教育报：版面名只能靠 pageLink 兜底、标题只能靠 <div class="title1"> 兜底。

    这两处在旧实现下都取不到值 —— 版面名恒为空串（报告里显示「—」），
    命中记录的标题同样为空串。回归护栏：任一条链路被改坏这里都会红。
    """
    hits, articles, stats = scanner.scan_paper(
        jyb_paper, fixed_day, KW, workers=4, fetcher=jyb_fetcher(fixed_day)
    )
    assert stats["entry_code"] == 200
    assert stats["scanned"] == 2
    assert len(hits) == 2
    # 入口是别名 node_1.htm，版面导航里只有哈希名 node_144740.htm
    assert {hit["section"] for hit in hits} == {"第01版 : 要闻"}
    # <title> 是站点通用串，标题只能来自 <div class="title1">
    assert {hit["title"] for hit in hits} == {"真题真做练硬功 产教协同育新人"}
    assert all(hit["title"] and hit["section"] for hit in articles)


def test_dry_run_only_probes_entry(zhanjiang_paper, fixed_day, site_fetcher):
    calls: list[str] = []
    base_fetch = site_fetcher(fixed_day)

    def counting_fetch(url):
        calls.append(url)
        return base_fetch(url)

    hits, articles, stats = scanner.scan_paper(
        zhanjiang_paper, fixed_day, KW, workers=4, dry=True, fetcher=counting_fetch
    )

    assert stats["note"] == "dry-run"
    assert stats["sections"] == 3
    assert hits == [] and articles == []
    assert len(calls) == 1


@pytest.mark.parametrize(
    "code,body,expected",
    [
        (0, "", "网络层失败"),
        (404, "not found", "入口 HTTP 404"),
        (200, "太短", "入口内容异常"),
    ],
)
def test_entry_failure_classification(zhanjiang_paper, fixed_day, code, body, expected):
    fetcher = lambda url: (code, body)  # noqa: E731
    hits, articles, stats = scanner.scan_paper(zhanjiang_paper, fixed_day, KW, workers=2, fetcher=fetcher)
    assert stats["note"].startswith(expected)
    assert hits == [] and articles == []
    assert stats["entry_code"] == code


def test_section_fetch_failure_is_ignored(zhanjiang_paper, fixed_day, site_fetcher):
    base = f"http://paper.gdzjdaily.com.cn/html/{fixed_day:%Y-%m}/{fixed_day:%d}/"
    fetcher = site_fetcher(fixed_day, status_overrides={base + "node_2.htm": (500, "")})
    hits, _articles, stats = scanner.scan_paper(zhanjiang_paper, fixed_day, KW, workers=4, fetcher=fetcher)
    assert stats["sections"] == 3
    assert stats["articles"] == 2  # node_3 的 103/104
    assert stats["sections_failed"] == 1
    assert stats["failed_urls"][0]["code"] == 500
    assert _hit_names(hits) == {"content_104.html"}


def test_article_fetch_failure_is_skipped(zhanjiang_paper, fixed_day, site_fetcher):
    base = f"http://paper.gdzjdaily.com.cn/html/{fixed_day:%Y-%m}/{fixed_day:%d}/"
    fetcher = site_fetcher(fixed_day, status_overrides={base + "content_101.html": (500, "")})
    hits, _articles, stats = scanner.scan_paper(zhanjiang_paper, fixed_day, KW, workers=4, fetcher=fetcher)
    assert stats["scanned"] == 3
    assert stats["articles_failed"] == 1
    assert _hit_names(hits) == {"content_102.html", "content_104.html"}


def test_short_article_body_is_skipped(zhanjiang_paper, fixed_day, site_fetcher):
    base = f"http://paper.gdzjdaily.com.cn/html/{fixed_day:%Y-%m}/{fixed_day:%d}/"
    fetcher = site_fetcher(fixed_day, overrides={base + "content_101.html": "太短"})
    _hits, _articles, stats = scanner.scan_paper(zhanjiang_paper, fixed_day, KW, workers=4, fetcher=fetcher)
    assert stats["scanned"] == 3


def test_min_html_bytes_threshold_is_config():
    # 防止有人把阈值改到把真实页面判为异常
    assert config.MIN_HTML_BYTES == 300


def test_per_paper_timeout_retries_override():
    """羊城晚报在 config 里设了 timeout/retries 覆盖，其他报社无覆盖。"""
    ycwb = next(p for p in config.PAPERS if p["name"] == "羊城晚报")
    assert ycwb["timeout"] >= 60, "羊城晚报超时应≥60s"
    assert ycwb["retries"] >= 4, "羊城晚报重试应≥4次"
    # 其他报社均不应设置覆盖键（保持默认全局超时/重试）
    for p in config.PAPERS:
        if p["name"] != "羊城晚报":
            assert "timeout" not in p and "retries" not in p, f"{p['name']} 不应有超时覆盖键"


def test_per_paper_override_not_applied_to_fake_fetcher(zhanjiang_paper, fixed_day, site_fetcher):
    """注入 fake fetcher 时，per-paper 包装不会被套上，保证测试隔离。"""
    calls: list[str] = []
    base_fetch = site_fetcher(fixed_day)

    def counting(url: str):
        calls.append(url)
        return base_fetch(url)

    from newspaper_auto.scanner import _paper_fetch

    wrapped = _paper_fetch(zhanjiang_paper, counting)
    # zhanjiang_paper 没有 timeout/retries 键 → 应原样返回
    assert wrapped is counting


def test_per_paper_override_applied_for_real_http_fetcher():
    """有覆盖键 + 使用真实 http.fetch 时，包装函数与原 fetcher 不同。"""
    from newspaper_auto import http
    from newspaper_auto.scanner import _paper_fetch

    ycwb = next(p for p in config.PAPERS if p["name"] == "羊城晚报")
    wrapped = _paper_fetch(ycwb, http.fetch)
    assert wrapped is not http.fetch, "羊城晚报应被包装（使用独立超时/重试）"


def test_node_exclude_filters_mirror_nodes(fixed_day):
    """人民日报式：版面页同时链到 pc 与 pad 两套 node，pad 应被 node_exclude 排除。"""
    paper = {
        "name": "测试报",
        "style": "ymd",
        "tpl": "http://example.com/pc/layout/{a}/node_01.html",
        "node_exclude": r"/pad/",
    }
    requested: list[str] = []

    def fake(url: str):
        requested.append(url)
        if url.endswith("node_01.html"):
            return 200, (
                '<a href="node_02.html">02</a>'
                '<a href="http://example.com/pad/layout/202609/22/node_01.html">手机版</a>'
            ) + "x" * 400
        if url.endswith("node_02.html"):
            return 200, '<a href="content_1.html">文章</a>' + "x" * 400
        return 200, "<html><body><h1>标题就在这里</h1><p>正文</p></body></html>"

    _hits, _articles, stats = scanner.scan_paper(paper, fixed_day, KW, workers=2, fetcher=fake)

    assert not any("/pad/" in url for url in requested), "pad 镜像不应被请求"
    assert any(url.endswith("/pc/layout/202609/22/node_02.html") for url in requested)
    assert stats["articles"] == 1


# ---------------------------------------------------------------- 任务 B/C：area 坐标与版号
def test_area_geometry_from_real_zhanjiang_page(zj_area_paper, area_day, zj_area_fetcher):
    """湛江日报真实版面页（含 <area>）：每条文章都应带上版内坐标。"""
    _hits, articles, stats = scanner.scan_paper(
        zj_area_paper, area_day, KW, workers=4, fetcher=zj_area_fetcher
    )
    assert stats["entry_code"] == 200
    assert len(articles) == 7

    geoms = {rec["url"].rsplit("/", 1)[-1]: rec["geometry"] for rec in articles}
    assert len(geoms) == 7
    assert all("geometry" in rec for rec in articles)
    # 同版多条文章纵向位置互不相同；top 恒小于 bottom
    assert len({g["top"] for g in geoms.values()}) == 7
    assert all(0 <= g["top"] < g["bottom"] <= 1 for g in geoms.values())
    assert all(g["total"] == 7 and 1 <= g["index"] <= 7 for g in geoms.values())
    # 有 area 的版面一律标记 basis="area"（回归：输出字段与旧逻辑一致，只多一个 basis）
    assert all(g["basis"] == "area" for g in geoms.values())
    # index 按 y1 升序编号
    ordered = sorted(geoms.values(), key=lambda g: g["y1"])
    assert [g["index"] for g in ordered] == list(range(1, 8))
    # 首条（图内最靠上的小块）落在版面顶部
    assert geoms["content_146377_3867999.htm"]["index"] == 1
    assert geoms["content_146377_3867999.htm"]["top"] < 0.1
    # 任务 C：真实 pageLink 版名归一化出版号
    assert {rec["section"] for rec in articles} == {"第1版：要闻"}
    assert {rec["section_no"] for rec in articles} == {"第01版"}


def test_area_geometry_from_real_rmrb_page(rmrb_area_paper, area_day, rmrb_area_fetcher):
    """人民日报真实版面页（CamelCase <Area> + 小数多边形坐标）同样解析成功。"""
    _hits, articles, stats = scanner.scan_paper(
        rmrb_area_paper, area_day, KW, workers=4, fetcher=rmrb_area_fetcher
    )
    assert stats["entry_code"] == 200
    assert len(articles) == 10
    geoms = [rec["geometry"] for rec in articles]
    assert all(g["total"] == 10 for g in geoms)
    # 真实数据里可能有两条 y1 相同的分栏，用完整包围盒/编号判断「位置互不相同」
    assert len({(g["x1"], g["y1"], g["x2"], g["y2"]) for g in geoms}) == 10
    assert {g["index"] for g in geoms} == set(range(1, 11))
    assert all(0 <= g["top"] < g["bottom"] <= 1 for g in geoms)
    assert all(g["basis"] == "area" for g in geoms)


def test_paper_without_area_uses_order_fallback(zhanjiang_paper, fixed_day, site_fetcher):
    """纯链接式版面（无 area）：按本版链接出现顺序写「版内 k/m」，且绝不编造坐标。"""
    hits, articles, stats = scanner.scan_paper(
        zhanjiang_paper, fixed_day, KW, workers=4, fetcher=site_fetcher(fixed_day)
    )
    assert stats["entry_code"] == 200
    assert articles and hits
    geoms = {rec["url"].rsplit("/", 1)[-1]: rec["geometry"] for rec in articles}
    # 每版独立编号：node_2 → 101/102，node_3 → 103/104（101/102 与 103/104 各占一版）
    assert geoms["content_101.html"] == {"index": 1, "total": 2, "basis": "order"}
    assert geoms["content_102.html"] == {"index": 2, "total": 2, "basis": "order"}
    assert geoms["content_103.html"] == {"index": 1, "total": 2, "basis": "order"}
    assert geoms["content_104.html"] == {"index": 2, "total": 2, "basis": "order"}
    # 顺序兜底只写 index/total/basis，绝不出现 top/bottom/x1..y2
    assert all(set(g) == {"index", "total", "basis"} for g in geoms.values())
    assert {rec["section_no"] for rec in hits} == {"第02版", "第03版"}


def test_nfnews_real_link_only_page_uses_order_fallback(nfnews_paper, area_day, nfnews_fetcher):
    """南方日报真实版面页（2026-09-23 A01 版）：0 个 <area>，按 9 条链接顺序兜底。"""
    hits, articles, stats = scanner.scan_paper(nfnews_paper, area_day, KW, workers=4, fetcher=nfnews_fetcher)
    assert stats["entry_code"] == 200
    assert stats["articles"] == 9
    assert len(articles) == 9
    # 版面名走真实 page_section（href == node_A01.html 的锚点）
    assert {rec["section"] for rec in articles} == {"A01版：要闻"}
    assert {rec["section_no"] for rec in articles} == {"A01"}
    # 顺序与页面出现顺序一致：10182150 → 1，10182158 → 9
    by_index = {rec["geometry"]["index"]: rec["url"].rsplit("/", 1)[-1] for rec in articles}
    assert by_index[1] == "content_10182150.html"
    assert by_index[9] == "content_10182158.html"
    for rec in articles:
        geom = rec["geometry"]
        assert geom["basis"] == "order"
        assert geom["total"] == 9
        assert 1 <= geom["index"] <= 9
        assert set(geom) == {"index", "total", "basis"}  # 无 top / 坐标
    assert all("geometry" in rec for rec in hits)
