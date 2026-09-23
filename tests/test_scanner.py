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
