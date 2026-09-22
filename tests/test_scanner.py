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
    assert _hit_names(hits) == {"content_104.html"}


def test_article_fetch_failure_is_skipped(zhanjiang_paper, fixed_day, site_fetcher):
    base = f"http://paper.gdzjdaily.com.cn/html/{fixed_day:%Y-%m}/{fixed_day:%d}/"
    fetcher = site_fetcher(fixed_day, status_overrides={base + "content_101.html": (500, "")})
    hits, _articles, stats = scanner.scan_paper(zhanjiang_paper, fixed_day, KW, workers=4, fetcher=fetcher)
    assert stats["scanned"] == 3
    assert _hit_names(hits) == {"content_102.html", "content_104.html"}


def test_short_article_body_is_skipped(zhanjiang_paper, fixed_day, site_fetcher):
    base = f"http://paper.gdzjdaily.com.cn/html/{fixed_day:%Y-%m}/{fixed_day:%d}/"
    fetcher = site_fetcher(fixed_day, overrides={base + "content_101.html": "太短"})
    _hits, _articles, stats = scanner.scan_paper(zhanjiang_paper, fixed_day, KW, workers=4, fetcher=fetcher)
    assert stats["scanned"] == 3


def test_min_html_bytes_threshold_is_config():
    # 防止有人把阈值改到把真实页面判为异常
    assert config.MIN_HTML_BYTES == 300
