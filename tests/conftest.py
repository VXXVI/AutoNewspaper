"""pytest 共享夹具：离线 HTML 站点桩。

所有测试**不访问网络**：通过注入 fake fetcher（``(http_code, html)``）驱动扫描，
fixture 文件位于 ``tests/fixtures/``。
"""

from __future__ import annotations

import re
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

# 含 <area> 图像映射的真实版面页片段取自 2026-09-23，故 area 相关用例统一用这一天
AREA_DAY = datetime(2026, 9, 23)

# 湛江日报 / 人民日报的版面页含 <area> 图像映射；其余四家是纯链接式版面（无 area）
ZJ_AREA_PAPER = {
    "name": "湛江日报",
    "style": "dash",
    "tpl": "http://paper.gdzjdaily.com.cn/html/{a}/node_1.htm",
}
RMRB_AREA_PAPER = {
    "name": "人民日报",
    "style": "ymd",
    "tpl": "http://paper.people.com.cn/rmrb/pc/layout/{a}/node_01.html",
}


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


def make_area_fetcher(section_fixture: str, article_fixture: str):
    """构造离线 fetcher：所有 ``node_*`` 返回真实版面页片段，所有 ``content_*`` 返回真实文章页。

    用于验证 area 坐标链路（含/不含 ``<area>`` 两类版面）而不访问网络。
    """
    section = load_fixture(section_fixture)
    article = load_fixture(article_fixture)

    def fetch(url: str):
        if re.search(r"node_\w+\.html?$", url):
            return 200, section
        if re.search(r"content_\w+\.html?$", url):
            return 200, article
        return 404, ""

    return fetch


@pytest.fixture
def area_day() -> datetime:
    return AREA_DAY


@pytest.fixture
def zj_area_paper() -> dict:
    return dict(ZJ_AREA_PAPER)


@pytest.fixture
def rmrb_area_paper() -> dict:
    return dict(RMRB_AREA_PAPER)


@pytest.fixture
def zj_area_fetcher():
    """湛江日报：真实含 area 的版面页片段 + 真实文章页。"""
    return make_area_fetcher("zhanjiang_node_1_area.htm", "zhanjiang_article_3868001.htm")


@pytest.fixture
def rmrb_area_fetcher():
    """人民日报：真实含 <Area> 的版面页片段 + 真实文章页。"""
    return make_area_fetcher("rmrb_node_01_area.html", "rmrb_article_30182606.html")


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


# ---------------------------------------------------------------------------
# tmp_path 覆盖：WorkBuddy 沙盒下 pytest 默认的 /tmp/pytest-of-root 目录
# 会因 EEXIST 权限冲突报错，改用已知可写路径。
# ---------------------------------------------------------------------------
_SAFE_TMP = Path("/private/var/folders/8s/qd14vxd90v7c5xfjl6ls1nxw0000gp/T/pytest-np")


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:  # type: ignore[override]
    node_id = re.sub(r"[^\w]", "_", request.node.nodeid)
    p = _SAFE_TMP / node_id
    p.mkdir(parents=True, exist_ok=True)
    yield p
    import shutil

    shutil.rmtree(p, ignore_errors=True)
