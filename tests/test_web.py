"""本地只读看板的单元测试（含 127.0.0.1 回环集成测试，不访问外网）。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime

import pytest

from newspaper_auto import web

ARCHIVE = [
    {
        "paper": "湛江日报",
        "date": "2026-09-22",
        "section": "要闻",
        "title": "标题B",
        "url": "http://x/2026-09/22/content_2.html",
        "text_len": "12",
        "snippets": ["…广东海洋大学…"],
        "matched": ["广东海洋大学"],
    },
    {
        "paper": "南方日报",
        "date": "2026-09-21",
        "title": "标题A",
        "url": "http://x/2026-09/21/content_1.html",
        "matched": ["广海"],
    },
    {"paper": "湛江日报", "title": "缺少 url，应被丢弃"},
    "不是字典，也应被丢弃",
]


def _write_archive(data_dir, payload) -> None:
    hits = data_dir / "hits"
    hits.mkdir(parents=True, exist_ok=True)
    (hits / "archive.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 数据装配
def test_build_dashboard_normalizes_sorts_and_drops_dirty(tmp_path):
    _write_archive(tmp_path, ARCHIVE)
    data = web.build_dashboard(tmp_path, now=datetime(2026, 9, 22, 10, 0, 0))

    assert data["count"] == 2  # 缺 url / 非字典被丢弃
    assert data["generated_at"] == "2026-09-22 10:00:00"
    assert [r["date"] for r in data["records"]] == ["2026-09-22", "2026-09-21"]  # 倒序
    assert data["records"][0]["text_len"] == 12  # 字符串数字被转 int
    assert data["records"][1]["section"] == ""  # 缺失字段补空串
    assert data["papers"] == ["南方日报", "湛江日报"]
    assert sorted(data["keywords"]) == ["广东海洋大学", "广海"]
    assert data["date_counts"] == {"2026-09-22": 1, "2026-09-21": 1}


def test_build_dashboard_missing_archive(tmp_path):
    data = web.build_dashboard(tmp_path)
    assert data["count"] == 0
    assert data["records"] == []
    assert data["papers"] == []


def test_build_dashboard_corrupt_archive(tmp_path):
    hits = tmp_path / "hits"
    hits.mkdir()
    (hits / "archive.json").write_text("{ oops", encoding="utf-8")
    assert web.build_dashboard(tmp_path)["count"] == 0


def test_build_dashboard_reads_seen_counts(tmp_path):
    _write_archive(tmp_path, ARCHIVE)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "seen.json").write_text(
        json.dumps({"广东海洋大学": {"http://x/a": 1, "http://x/b": 1}}, ensure_ascii=False), encoding="utf-8"
    )
    assert web.build_dashboard(tmp_path)["seen_counts"] == {"广东海洋大学": 2}


# ---------------------------------------------------------------- 任务 B/C：geometry / section_no
def test_build_dashboard_passes_geometry_and_section_no(tmp_path):
    geometry = {
        "x1": 2,
        "y1": 100,
        "x2": 336,
        "y2": 300,
        "top": 0.38,
        "bottom": 0.9,
        "index": 2,
        "total": 7,
    }
    item = {**ARCHIVE[0], "section_no": "第02版", "geometry": geometry}
    _write_archive(tmp_path, [item])
    data = web.build_dashboard(tmp_path)
    rec = data["records"][0]
    assert rec["section_no"] == "第02版"
    assert rec["geometry"]["index"] == 2 and rec["geometry"]["total"] == 7
    assert rec["geometry"]["top"] == 0.38


def test_build_dashboard_geometry_absent_or_partial_is_none(tmp_path):
    """旧归档没有 geometry → None；残缺 geometry 也被丢弃，前端据此不显示位置。"""
    _write_archive(tmp_path, [ARCHIVE[0], {**ARCHIVE[1], "geometry": {"top": 0.5}}])
    data = web.build_dashboard(tmp_path)
    assert all(rec["geometry"] is None for rec in data["records"])
    assert all(rec["section_no"] == "" for rec in data["records"])


def test_normalize_geometry_accepts_order_fallback():
    """顺序兜底只有 index/total/basis（没有 top），也必须透出，不能再被整体丢弃。"""
    out = web._normalize_geometry({"index": 3, "total": 9, "basis": "order"})
    assert out == {"index": 3, "total": 9, "basis": "order"}


def test_normalize_geometry_keeps_area_fields_and_basis():
    raw = {
        "x1": 2,
        "y1": 100,
        "x2": 336,
        "y2": 300,
        "top": 0.38,
        "bottom": 0.9,
        "index": 2,
        "total": 7,
        "basis": "area",
    }
    out = web._normalize_geometry(raw)
    assert out["index"] == 2 and out["total"] == 7 and out["basis"] == "area"
    assert out["top"] == 0.38 and out["x1"] == 2 and out["y2"] == 300


def test_normalize_geometry_rejects_empty_and_partial():
    """缺少 index/total 的无/残缺 geometry 仍返回 None（旧归档行为不变）。"""
    assert web._normalize_geometry(None) is None
    assert web._normalize_geometry({}) is None
    assert web._normalize_geometry({"top": 0.5}) is None
    assert web._normalize_geometry({"index": 1}) is None
    assert web._normalize_geometry({"total": 3}) is None
    assert web._normalize_geometry("nope") is None


def test_build_dashboard_passes_order_fallback_geometry(tmp_path):
    """含顺序兜底 geometry 的归档条目，经看板装配后仍保留 index/total/basis。"""
    item = {**ARCHIVE[1], "section_no": "A01", "geometry": {"index": 1, "total": 9, "basis": "order"}}
    _write_archive(tmp_path, [item])
    rec = web.build_dashboard(tmp_path)["records"][0]
    assert rec["geometry"] == {"index": 1, "total": 9, "basis": "order"}


# ---------------------------------------------------------------- HTTP 集成
@pytest.fixture
def base_url(tmp_path):
    _write_archive(tmp_path, ARCHIVE)
    server = web.create_server(tmp_path, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read(), resp.headers.get("Content-Type", "")


def test_serves_index_and_static(base_url):
    status, body, ctype = _get(base_url + "/")
    assert status == 200 and "text/html" in ctype
    assert "命中列表" in body.decode("utf-8")

    status, _body, ctype = _get(base_url + "/static/app.js")
    assert status == 200 and "javascript" in ctype

    status, _body, ctype = _get(base_url + "/static/style.css")
    assert status == 200 and "text/css" in ctype


def test_api_hits(base_url):
    status, body, ctype = _get(base_url + "/api/hits")
    assert status == 200 and "application/json" in ctype
    data = json.loads(body)
    assert data["count"] == 2
    assert data["records"][0]["title"] == "标题B"


def test_api_health(base_url):
    status, body, _ = _get(base_url + "/api/health")
    assert status == 200
    assert json.loads(body)["ok"] is True


@pytest.mark.parametrize(
    "path",
    [
        "/nope",  # 未知路由
        "/static/web.py",  # 包内真实文件，但不在白名单
        "/static/%2e%2e/web.py",  # 编码后的路径穿越
    ],
)
def test_404_for_non_whitelisted(base_url, path):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(base_url + path)
    assert exc.value.code == 404


def test_head_request_has_no_body(base_url):
    request = urllib.request.Request(base_url + "/api/health", method="HEAD")
    with urllib.request.urlopen(request, timeout=5) as resp:
        assert resp.status == 200
        assert resp.read() == b""


def test_main_reports_busy_port_instead_of_traceback(tmp_path, capsys, monkeypatch):
    # 关掉 SO_REUSEADDR，保证第二次绑定同一端口必然失败（跨平台稳定）
    monkeypatch.setattr(web.DashboardServer, "allow_reuse_address", False)
    busy = web.DashboardServer(("127.0.0.1", 0), tmp_path)
    try:
        port = busy.server_address[1]
        code = web.main(["--data-dir", str(tmp_path), "--host", "127.0.0.1", "--port", str(port)])
    finally:
        busy.server_close()

    assert code == 1
    err = capsys.readouterr().err
    assert "启动失败" in err and "换端口" in err
