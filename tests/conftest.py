"""pytest 共享夹具：离线 HTML 站点桩。

所有测试**不访问网络**：通过注入 fake fetcher（``(http_code, html)``）驱动扫描，
fixture 文件位于 ``tests/fixtures/``。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# 湛江日报：dash 风格日期目录，入口为 node_1.htm
ZHANJIANG_PAPER = {
    "name": "湛江日报",
    "style": "dash",
    "tpl": "http://paper.gdzjdaily.com.cn/html/{a}/node_1.htm",
}

# 中国教育报：入口是稳定别名 node_1.htm，而版面导航里写的是内部哈希名 node_144740.htm，
# 两者对不上；且文章标题不在 <h1> 而在 <div class="title1"> 里，<title> 是站点通用串。
JYB_PAPER = {
    "name": "中国教育报",
    "style": "dash",
    "tpl": "https://paper.jyb.cn/zgjyb/html/{a}/node_1.htm",
}

FIXED_DAY = datetime(2026, 9, 22)


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def make_zhanjiang_fetcher(
    day: datetime, *, overrides: dict | None = None, status_overrides: dict | None = None
):
    """构造一个只认识湛江日报当日页面地图的 fake fetcher。

    - 未登记 URL → ``(404, "")``（同时用于验证跨日期链接被过滤）
    - ``status_overrides`` 可按 URL 覆写为失败状态，模拟版面/文章抓取失败
    """
    date_part = day.strftime("%Y-%m/%d")
    base = f"http://paper.gdzjdaily.com.cn/html/{date_part}/"
    site = {
        base + "node_1.htm": load_fixture("entry_node_1.htm"),
        base + "node_2.htm": load_fixture("section_node_2.htm"),
        base + "node_3.htm": load_fixture("section_node_3.htm"),
        base + "content_101.html": load_fixture("article_content_101.html"),
        base + "content_102.html": load_fixture("article_content_102.html"),
        base + "content_103.html": load_fixture("article_content_103.html"),
        base + "content_104.html": load_fixture("article_content_104.html"),
    }
    site.update(overrides or {})
    status_overrides = status_overrides or {}

    def fetch(url: str):
        if url in status_overrides:
            return status_overrides[url]
        return (200, site[url]) if url in site else (404, "")

    return fetch


@pytest.fixture
def fixed_day() -> datetime:
    return FIXED_DAY


def make_jyb_fetcher(day: datetime):
    """中国教育报站点桩：入口别名与版面哈希名指向同一份版面页。"""
    date_part = day.strftime("%Y-%m/%d")
    base = f"https://paper.jyb.cn/zgjyb/html/{date_part}/"
    section = load_fixture("jyb_section_node_1.htm")
    article = load_fixture("jyb_article_title1.htm")
    site = {
        base + "node_1.htm": section,
        base + "node_144740.htm": section,
        base + "node_144741.htm": section,
        base + "content_144740_1.htm": article,
        base + "content_144740_2.htm": article,
    }

    def fetch(url: str):
        return (200, site[url]) if url in site else (404, "")

    return fetch


@pytest.fixture
def jyb_paper() -> dict:
    return dict(JYB_PAPER)


@pytest.fixture
def jyb_fetcher():
    """返回工厂函数：``jyb_fetcher(day) -> fetcher``。"""
    return make_jyb_fetcher


@pytest.fixture
def zhanjiang_paper() -> dict:
    return dict(ZHANJIANG_PAPER)


@pytest.fixture
def site_fetcher():
    """返回工厂函数：``site_fetcher(day, overrides=..., status_overrides=...) -> fetcher``。"""
    return make_zhanjiang_fetcher


@pytest.fixture
def read_fixture():
    """按文件名读取 ``tests/fixtures/`` 下的 HTML。"""
    return load_fixture


@pytest.fixture
def today() -> datetime:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
