"""本地看板的单元测试（含 127.0.0.1 回环集成测试，不访问外网）。"""

from __future__ import annotations

import errno
import json
import re
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from newspaper_auto import config, dialogs, papers, web

ARCHIVE = [
    {
        "paper": "湛江日报",
        "date": "2026-09-22",
        "section": "要闻",
        "title": "标题B",
        "url": "http://x/2026-09/22/content_2.html",
        "text_len": "12",
        "snippets": ["…示例大学…"],
        "matched": ["示例大学"],
    },
    {
        "paper": "南方日报",
        "date": "2026-09-21",
        "title": "标题A",
        "url": "http://x/2026-09/21/content_1.html",
        "matched": ["示大"],
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
    assert sorted(data["keywords"]) == ["示例大学", "示大"]
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


def test_build_dashboard_cache_hit_and_invalidation(tmp_path):
    _write_archive(tmp_path, ARCHIVE)
    first = web.build_dashboard(tmp_path)
    # 文件未变 → 命中缓存：返回等值但互相独立的对象（深拷贝，避免调用方污染缓存）
    assert web.build_dashboard(tmp_path) == first
    # 归档内容变化 → 指纹变化 → 重新装配
    _write_archive(tmp_path, [*ARCHIVE, {**ARCHIVE[0], "url": "http://x/new", "title": "新"}])
    second = web.build_dashboard(tmp_path)
    assert second is not first
    assert second["count"] == first["count"] + 1


def test_build_dashboard_cache_returns_independent_copy(tmp_path):
    """调用方改动返回值不得污染缓存；后续请求仍拿到完整、独立的数据。"""
    _write_archive(tmp_path, ARCHIVE)
    first = web.build_dashboard(tmp_path)
    assert web.build_dashboard(tmp_path) is not first

    poisoned = web.build_dashboard(tmp_path)
    poisoned["records"].clear()
    poisoned["count"] = -1

    fresh = web.build_dashboard(tmp_path)
    assert fresh["count"] == first["count"]
    assert len(fresh["records"]) == first["count"]


def test_build_dashboard_now_argument_bypasses_cache(tmp_path):
    _write_archive(tmp_path, ARCHIVE)
    web.build_dashboard(tmp_path)
    fixed = web.build_dashboard(tmp_path, now=datetime(2020, 1, 2, 3, 4, 5))
    assert fixed["generated_at"] == "2020-01-02 03:04:05"


def test_build_dashboard_reads_seen_counts(tmp_path):
    _write_archive(tmp_path, ARCHIVE)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "seen.json").write_text(
        json.dumps({"示例大学": {"http://x/a": 1, "http://x/b": 1}}, ensure_ascii=False), encoding="utf-8"
    )
    assert web.build_dashboard(tmp_path)["seen_counts"] == {"示例大学": 2}


def test_build_dashboard_exposes_empty_state_hints(tmp_path):
    """空状态引导所需字段：has_web_config（是否全新环境）与 default_keywords。"""
    _write_archive(tmp_path, [])
    data = web.build_dashboard(tmp_path)
    assert data["count"] == 0
    assert data["has_web_config"] is False
    assert data["default_keywords"] == list(config.DEFAULT_KEYWORDS)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "web_config.json").write_text("{}", encoding="utf-8")
    assert web.build_dashboard(tmp_path)["has_web_config"] is True


def test_build_dashboard_exposes_configured_keywords_and_normalizes_malformed_matched(tmp_path):
    _write_archive(
        tmp_path,
        [
            {
                "paper": "甲报",
                "date": "2026-09-24",
                "title": "错误 matched 类型",
                "url": "http://x/bad",
                "matched": "人工智能",
            },
            {
                "paper": "乙报",
                "date": "2026-09-24",
                "title": "正常 matched",
                "url": "http://x/good",
                "matched": ["人工智能", "", None],
            },
        ],
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "web_config.json").write_text(
        json.dumps({"keywords": ["人工智能"], "days": 2, "workers": 8}, ensure_ascii=False),
        encoding="utf-8",
    )

    data = web.build_dashboard(tmp_path)
    by_url = {record["url"]: record for record in data["records"]}
    assert data["configured_keywords"] == ["人工智能"]
    assert data["keywords"] == ["人工智能"]
    assert by_url["http://x/bad"]["matched"] == []
    assert by_url["http://x/good"]["matched"] == ["人工智能"]


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


def test_build_dashboard_normalizes_layout(tmp_path):
    layout = {
        "basis": "area",
        "aspect_ratio": 0.7,
        "items": [
            {"url": "http://x/a", "left": 0.1, "top": 0.2, "right": 0.8, "bottom": 0.5},
            {"url": "http://x/b", "left": -1, "top": 0, "right": 2, "bottom": 1},
        ],
    }
    _write_archive(tmp_path, [{**ARCHIVE[0], "layout": layout}])
    dash = web.build_dashboard(tmp_path)
    # layout 不再挂在记录上，而是抽到顶层共享表（避免同一版面逐条重复下发）
    assert "layout" not in dash["records"][0]
    normalized = dash["layouts"][web.layout_key(dash["records"][0])]
    assert normalized["basis"] == "area"
    assert normalized["aspect_ratio"] == 0.7
    assert normalized["items"] == [layout["items"][0]]


def test_layout_key_matches_frontend_contract():
    """分组键必须与前端 render.js 的 layoutKeyOf 完全一致：paper\\u0001date\\u0001section_no\\u0001section。"""
    assert (
        web.layout_key(
            {"paper": "湛江日报", "date": "2026-09-23", "section_no": "第02版", "section": "第2版：本地"}
        )
        == "湛江日报\u00012026-09-23\u0001第02版\u0001第2版：本地"
    )
    # 缺失字段：版面名回落到 "—"，section_no 回落到空串（与前端 `r.section || "—"` 一致）
    assert web.layout_key({"paper": "A", "date": "2026-01-01"}) == "A\u00012026-01-01\u0001\u0001—"


def test_build_dashboard_deduplicates_shared_layout(tmp_path):
    """同一版面多条记录只下发一份 layout。

    回归：layout 曾逐条跟着记录重复（同一版 40 篇文章 = 同一份坐标重复 40 次），
    实测可占 /api/hits 响应体的 90%。
    """
    layout = {
        "basis": "area",
        "aspect_ratio": 0.72,
        "items": [
            {"url": f"http://x/{i}", "left": 0.1, "top": i / 5, "right": 0.4, "bottom": (i + 1) / 5}
            for i in range(5)
        ],
    }
    records = [
        {**ARCHIVE[0], "url": f"http://x/{i}", "section_no": "第02版", "layout": layout} for i in range(5)
    ]
    _write_archive(tmp_path, records)
    dash = web.build_dashboard(tmp_path)

    assert len(dash["records"]) == 5
    assert all("layout" not in rec for rec in dash["records"]), "记录里不再逐条携带 layout"
    assert len(dash["layouts"]) == 1, "同一版面只保留一份"
    assert len(dash["layouts"][web.layout_key(dash["records"][0])]["items"]) == 5


def test_build_dashboard_keeps_longest_layout_per_section(tmp_path):
    """同一版面出现多份 layout 时保留项数最多的那份（与前端原有语义一致）。"""
    short = {
        "basis": "area",
        "aspect_ratio": 0.72,
        "items": [{"url": "http://x/0", "left": 0.1, "top": 0.1, "right": 0.4, "bottom": 0.2}],
    }
    long = {
        "basis": "area",
        "aspect_ratio": 0.72,
        "items": [
            {"url": f"http://x/{i}", "left": 0.1, "top": i / 5, "right": 0.4, "bottom": (i + 1) / 5}
            for i in range(5)
        ],
    }
    records = [
        {**ARCHIVE[0], "url": "http://x/0", "section_no": "第02版", "layout": short},
        {**ARCHIVE[0], "url": "http://x/1", "section_no": "第02版", "layout": long},
    ]
    _write_archive(tmp_path, records)
    dash = web.build_dashboard(tmp_path)
    assert len(dash["layouts"]) == 1
    assert len(next(iter(dash["layouts"].values()))["items"]) == 5


def test_web_parser_accepts_scan_on_start():
    args = web.build_parser().parse_args(["--scan"])
    assert args.scan is True


def test_scan_single_until_is_normalized_to_one_day():
    params, error = web.validate_scan_params({"keywords": ["x"], "until": "2026-09-20"})
    assert error is None
    assert params["days"] is None
    assert params["since"] == "2026-09-20"
    assert params["until"] == "2026-09-20"


def test_scan_single_since_enforces_range_limit():
    too_old = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=config.MAX_RANGE_DAYS
    )
    params, error = web.validate_scan_params({"keywords": ["x"], "since": too_old.strftime("%Y-%m-%d")})
    assert params is None
    assert "跨度" in error


def test_server_rejects_non_loopback_binding_before_listen(tmp_path):
    with pytest.raises(ValueError, match="只允许监听"):
        web.create_server(tmp_path, host="0.0.0.0", port=0)


def test_create_server_papers_root_follows_app_dir_env(tmp_path, monkeypatch):
    """未显式传 papers_root 时，自定义报社跟随 NEWSPAPER_APP_DIR，而不是 cwd。"""
    app_root = tmp_path / "app"
    app_root.mkdir()
    monkeypatch.setenv("NEWSPAPER_APP_DIR", str(app_root))
    monkeypatch.chdir(tmp_path)  # cwd 故意与 app_root 不同
    server = web.create_server(tmp_path / "data", host="127.0.0.1", port=0)
    try:
        assert server.papers_root == app_root.resolve()
    finally:
        server.server_close()


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
    assert "命中报道" in body.decode("utf-8")

    status, _body, ctype = _get(base_url + "/static/app.js")
    assert status == 200 and "javascript" in ctype

    status, _body, ctype = _get(base_url + "/static/style.css")
    assert status == 200 and "text/css" in ctype

    status, api_body, ctype = _get(base_url + "/static/api.js")
    assert status == 200 and "javascript" in ctype

    status, fmt_body, ctype = _get(base_url + "/static/format.js")
    assert status == 200 and "javascript" in ctype
    assert "export function" in fmt_body.decode("utf-8")

    html = body.decode("utf-8") if body else ""
    assert api_body and "/static/api.js" in html
    assert html.index("/static/api.js") < html.index("/static/app.js")
    assert 'type="module"' in html  # app.js 是 ES Module 入口


def test_all_static_assets_reachable_over_http(base_url):
    """static/ 下的每个前端资源都必须能通过 HTTP 取到。

    守护 web.STATIC_TYPES 与 static/ 目录的同步：v0.3.0 漏收 `store.js`，浏览器里
    app.js 的 `import "./store.js"` 直接 404，整个看板不执行；而当时只断言 4 个固定
    文件的测试没有发现（jsdom 测试从文件系统 import，绕过了 HTTP）。
    """
    # 后缀集合必须覆盖 static/ 下**所有**会被浏览器请求的类型：漏收即 404。
    # 白名单由 web.STATIC_TYPES 维护，这里做「目录 → 白名单」的同步守护。
    served_suffixes = {".html", ".js", ".css", ".svg", ".ico", ".png"}
    names = sorted(p.name for p in web.STATIC_DIR.iterdir() if p.suffix in served_suffixes)
    assert names, "static/ 目录下应当有前端资源"

    for name in names:
        status, body, ctype = _get(f"{base_url}/static/{name}")
        assert status == 200, f"/static/{name} 返回 {status}：STATIC_TYPES 是否漏了这一项？"
        assert body, f"/static/{name} 内容为空"
        assert ctype == web.STATIC_TYPES[name], f"/static/{name} 的 Content-Type 不符"


def test_app_js_imports_are_served(base_url):
    """app.js 静态 import 的同级模块必须全部可达：ES Module 任一 import 失败即整体不执行。"""
    status, body, _ctype = _get(base_url + "/static/app.js")
    assert status == 200

    source = body.decode("utf-8")
    targets = sorted(set(re.findall(r'from\s+"\./([A-Za-z0-9_.-]+)"', source)))
    assert targets, "app.js 应当至少 import 一个同级模块（如 ./format.js、./store.js）"

    for name in targets:
        status, dep_body, ctype = _get(f"{base_url}/static/{name}")
        assert status == 200, f"app.js 依赖的 /static/{name} 返回 {status}"
        assert dep_body, f"/static/{name} 内容为空"
        assert "javascript" in ctype


def test_create_server_normalizes_ipv6_loopback(tmp_path):
    """``--host ::1`` 也必须能起来：服务器以 AF_INET 绑定，须归一为 127.0.0.1。"""
    server = web.create_server(tmp_path, "::1", 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_create_server_rejects_non_loopback(tmp_path):
    with pytest.raises(ValueError):
        web.create_server(tmp_path, "0.0.0.0", 0)


def test_dashboard_server_direct_construction_rejects_non_loopback(tmp_path):
    """直接构造 DashboardServer 也必须走环回校验（factory 之外的第二道门）。"""
    with pytest.raises(ValueError, match="只允许监听"):
        web.DashboardServer(("0.0.0.0", 0), tmp_path)


def test_dashboard_server_direct_construction_normalizes_ipv6(tmp_path):
    server = web.DashboardServer(("::1", 0), tmp_path)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_security_headers_on_success_and_error(base_url):
    for path in ("/api/health", "/nope"):
        request = urllib.request.Request(base_url + path)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            headers = response.headers
            assert "style-src 'self' 'unsafe-inline'" in headers["Content-Security-Policy"]
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers["Referrer-Policy"] == "no-referrer"
            assert headers["X-Frame-Options"] == "DENY"
            assert headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=()"
            assert headers["Cache-Control"] == "no-store"


def test_unsupported_method_returns_json_error_with_security_headers(base_url):
    request = urllib.request.Request(base_url + "/api/scan", data=b"{}", method="PUT")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 501
    assert "application/json" in exc.value.headers["Content-Type"]
    assert exc.value.headers["X-Content-Type-Options"] == "nosniff"
    assert json.loads(exc.value.read())["error"]


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


def test_scan_thread_start_failure_releases_lock(tmp_path, monkeypatch):
    server = web.create_server(tmp_path, host="127.0.0.1", port=0, papers_root=tmp_path)

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("no thread")

    monkeypatch.setattr(web.threading, "Thread", BrokenThread)
    try:
        with pytest.raises(RuntimeError, match="no thread"):
            server.start_scan({"keywords": ["x"], "days": 1, "only": ""})
        assert server.scan_lock.locked() is False
        assert server.scan_task.snapshot()["status"] == "error"
    finally:
        server.server_close()


def test_start_scan_revalidates_paper_selection_and_releases_lock(tmp_path):
    server = web.create_server(tmp_path, host="127.0.0.1", port=0, papers_root=tmp_path)
    try:
        with pytest.raises(web.ScanRequestError, match="不存在报"):
            server.start_scan({"keywords": ["x"], "days": 1, "only": "不存在报"})
        assert server.scan_lock.locked() is False
    finally:
        server.server_close()


# ---------------------------------------------------------------- 扫描：POST/GET /api/scan
def _noop_picker(initial):
    """默认不弹真实系统窗口：想看「选中路径」分支的用例必须显式注入 picker。"""
    raise dialogs.DirectoryPickerUnavailable("测试环境不弹原生窗口")


@contextmanager
def _serve(tmp_path, scan_runner=None, paper_probe=None, papers_root=None, folder_picker=None):
    """起一个注入了假 scan_runner / paper_probe / folder_picker 的回环看板（绝不碰网络）。

    ``papers_root`` 默认指向 ``tmp_path``，确保自定义报社的读写落在测试目录，
    **绝不污染真实项目根的 papers.custom.json**。
    """
    _write_archive(tmp_path, ARCHIVE)
    server = web.create_server(
        tmp_path,
        host="127.0.0.1",
        port=0,
        scan_runner=scan_runner or _noop_runner,
        paper_probe=paper_probe,
        papers_root=papers_root or tmp_path,
        folder_picker=folder_picker or _noop_picker,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(method, url, payload=None, headers=None):
    """返回 ``(status, body_bytes)``；4xx/5xx 不抛异常，便于直接断言状态码。

    有 body 时默认补 ``Content-Type: application/json``（可用 headers 覆盖，以测试校验分支）。
    """
    headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _noop_runner(data_dir, params, log):
    log("noop")
    return {"exit_code": 0, "summary": None}


def _wait_done(url, attempts=100, interval=0.05):
    last = None
    for _ in range(attempts):
        status, body = _request("GET", url + "/api/scan")
        assert status == 200
        last = json.loads(body)
        if last["status"] in ("done", "error", "cancelled"):
            return last
        time.sleep(interval)
    return last


def test_default_scan_runner_streams_via_explicit_logger(tmp_path, monkeypatch):
    received = []

    def fake_main(argv, default_data_dir=None, fetcher=None, logger=None):
        assert logger is not None
        logger("扫描进行中")
        logger("扫描文章 12 篇 | 命中 2 篇 | 新增 1 篇 | 耗时 0.5s")
        return 0

    monkeypatch.setattr(web.cli, "main", fake_main)
    result = web.default_scan_runner(tmp_path, {"keywords": ["x"], "days": 1}, received.append)
    assert received == ["扫描进行中", "扫描文章 12 篇 | 命中 2 篇 | 新增 1 篇 | 耗时 0.5s"]
    assert result == {
        "exit_code": 0,
        "summary": {"articles": 12, "hits": 2, "new_hits": 1, "elapsed": 0.5},
    }


def test_scan_post_runs_to_done_and_logs(tmp_path):
    release = threading.Event()

    def runner(data_dir, params, log):
        assert str(data_dir) == str(tmp_path)
        log("假装扫描开始")
        log(f"关键词：{params['keywords'][0]}")
        release.wait(timeout=5)  # 阻塞到响应读回后，避免 runner 先于 202 响应跑完
        return {"exit_code": 0, "summary": {"hits": 1}}

    with _serve(tmp_path, runner) as url:
        status, body = _request(
            "POST", url + "/api/scan", {"keywords": ["示例大学"], "days": 1, "workers": 4}
        )
        assert status == 202
        assert json.loads(body)["status"] == "running"
        release.set()

        task = _wait_done(url)
        assert task["status"] == "done"
        assert task["exit_code"] == 0
        assert task["summary"] == {"hits": 1}
        assert any("假装扫描开始" in line for line in task["log"])


def test_second_scan_while_running_returns_409(tmp_path):
    release = threading.Event()

    def runner(data_dir, params, log):
        log("阻塞中")
        release.wait(timeout=5)
        return {"exit_code": 0, "summary": None}

    with _serve(tmp_path, runner) as url:
        first, _ = _request("POST", url + "/api/scan", {"keywords": ["x"]})
        assert first == 202
        second, body = _request("POST", url + "/api/scan", {"keywords": ["x"]})
        assert second == 409
        assert json.loads(body)["error"]
        release.set()
        assert _wait_done(url)["status"] == "done"


@pytest.mark.parametrize(
    "payload",
    [
        {"keywords": []},  # 空数组
        {"keywords": [""]},  # 元素为空串
        {"keywords": ["x"], "days": 0},  # days 下界
        {"keywords": ["x"], "days": 63},  # days 上界（> 62）
        {"keywords": ["x"], "days": 2, "since": "2026-09-01"},  # days 与 since 同给
        {"keywords": ["x"], "since": "2026-09-10", "until": "2026-09-01"},  # since > until
        {"keywords": ["x"], "workers": 0},  # workers 下界
    ],
)
def test_scan_invalid_params_return_400(tmp_path, payload):
    with _serve(tmp_path, _noop_runner) as url:
        status, body = _request("POST", url + "/api/scan", payload)
        assert status == 400
        assert json.loads(body)["error"]


def test_scan_requires_json_content_type(tmp_path):
    with _serve(tmp_path, _noop_runner) as url:
        status, body = _request(
            "POST", url + "/api/scan", {"keywords": ["x"]}, headers={"Content-Type": "text/plain"}
        )
        assert status == 415
        assert json.loads(body)["error"]


def test_scan_rejects_foreign_origin(tmp_path):
    with _serve(tmp_path, _noop_runner) as url:
        status, _body = _request(
            "POST", url + "/api/scan", {"keywords": ["x"]}, headers={"Origin": "http://evil.example"}
        )
        assert status == 403


def test_scan_rejects_foreign_host(tmp_path):
    with _serve(tmp_path, _noop_runner) as url:
        status, _body = _request(
            "POST", url + "/api/scan", {"keywords": ["x"]}, headers={"Host": "evil.example"}
        )
        assert status == 403


def test_scan_status_idle_initially(tmp_path):
    with _serve(tmp_path, _noop_runner) as url:
        status, body = _request("GET", url + "/api/scan")
        assert status == 200
        data = json.loads(body)
        assert data["status"] == "idle"
        assert data["log"] == []
        assert data["error"] is None


def test_scan_accepts_only_param(tmp_path):
    seen = {}

    def runner(data_dir, params, log):
        seen.update(params)
        return {"exit_code": 0, "summary": None}

    with _serve(tmp_path, runner) as url:
        status, _body = _request("POST", url + "/api/scan", {"keywords": ["x"], "only": "湛江日报,南方日报"})
        assert status == 202
        assert _wait_done(url)["status"] == "done"
    assert seen["only"] == "湛江日报,南方日报"


def test_scan_rejects_unknown_paper_before_runner(tmp_path):
    called = False

    def runner(data_dir, params, log):  # pragma: no cover - 不应执行
        nonlocal called
        called = True
        return {"exit_code": 0, "summary": None}

    with _serve(tmp_path, runner) as url:
        status, body = _request("POST", url + "/api/scan", {"keywords": ["x"], "only": "湛江日报,不存在报"})
        assert status == 400
        assert "不存在报" in json.loads(body)["error"]
    assert called is False


def test_scan_nonzero_exit_is_error_and_lock_is_released(tmp_path):
    def runner(data_dir, params, log):
        log("部分失败")
        return {"exit_code": 1, "summary": {"hits": 2}}

    with _serve(tmp_path, runner) as url:
        status, _ = _request("POST", url + "/api/scan", {"keywords": ["x"]})
        assert status == 202
        task = _wait_done(url)
        assert task["status"] == "error"
        assert task["exit_code"] == 1
        assert task["summary"] == {"hits": 2}

        status, _ = _request("POST", url + "/api/scan", {"keywords": ["x"]})
        assert status == 202


@pytest.mark.parametrize(
    "exit_value, expected_status, expected_code",
    [(None, "done", 0), (0, "done", 0), ("停止", "error", 1)],
)
def test_scan_runner_system_exit_uses_python_exit_semantics(
    tmp_path, exit_value, expected_status, expected_code
):
    def runner(data_dir, params, log):
        raise SystemExit(exit_value)

    with _serve(tmp_path, runner) as url:
        status, _ = _request("POST", url + "/api/scan", {"keywords": ["x"], "days": 1})
        assert status == 202
        task = _wait_done(url)
        assert task["status"] == expected_status
        assert task["exit_code"] == expected_code


def test_scan_only_invalid_element_returns_400(tmp_path):
    with _serve(tmp_path, _noop_runner) as url:
        status, body = _request("POST", url + "/api/scan", {"keywords": ["x"], "only": ["湛江日报", 5]})
        assert status == 400
        assert json.loads(body)["error"]


def test_scan_body_too_large_returns_413(tmp_path):
    """声明的 Content-Length 超限即 413；用原始套接字只发头，避免大 body 与服务端提前关闭的竞态。"""
    import socket

    with _serve(tmp_path, _noop_runner) as url:
        host, _, port = url[len("http://") :].partition(":")
        request = (
            "POST /api/scan HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {web.MAX_BODY_BYTES + 1}\r\n"
            "\r\n"
        )
        with socket.create_connection((host, int(port)), timeout=5) as sock:
            sock.sendall(request.encode("ascii"))
            raw = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk

    assert b" 413 " in raw.split(b"\r\n", 1)[0], raw
    assert b"error" in raw


def test_scan_rejects_transfer_encoding_with_content_length(tmp_path):
    """同时声明 Transfer-Encoding 与 Content-Length 会产生请求走私歧义，直接 400。"""
    import socket

    with _serve(tmp_path, _noop_runner) as url:
        host, _, port = url[len("http://") :].partition(":")
        request = (
            "POST /api/scan HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 2\r\n"
            "Transfer-Encoding: chunked\r\n"
            "\r\n"
            "{}"
        )
        with socket.create_connection((host, int(port)), timeout=5) as sock:
            sock.sendall(request.encode("ascii"))
            raw = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk

    assert b" 400 " in raw.split(b"\r\n", 1)[0], raw
    assert b"Transfer-Encoding" in raw


def test_post_unknown_route_returns_404_without_falling_through_to_export(tmp_path):
    """未知 POST 路径必须 404：旧实现的 else 会把未登记的路径当作导出处理。"""
    import socket

    export_dir = tmp_path / "exports"
    body = json.dumps({"directory": str(export_dir), "format": "json"}).encode("utf-8")
    with _serve(tmp_path) as url:
        host, _, port = url[len("http://") :].partition(":")
        request = (
            "POST /api/not-a-route HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode("ascii") + body
        with socket.create_connection((host, int(port)), timeout=5) as sock:
            sock.sendall(request)
            raw = sock.recv(4096)

    assert b" 404 " in raw.split(b"\r\n", 1)[0], raw
    assert not export_dir.exists()


# ---------------------------------------------------------------- 导出：POST /api/export
def test_export_api_writes_filtered_json_to_requested_directory(tmp_path):
    export_dir = tmp_path / "chosen" / "nested"
    payload = {
        "directory": str(export_dir),
        "format": "json",
        "filters": {
            "papers": ["湛江日报"],
            "keywords": ["示例大学"],
            "from": "2026-09-22",
            "to": "2026-09-22",
            "query": "示例大学",
            "sort": "title",
        },
    }

    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/export", payload)

    response = json.loads(body)
    target = Path(response["path"])
    assert status == 201
    assert response["count"] == 1
    assert response["format"] == "json"
    assert response["path"] == str(target.resolve())
    records = json.loads(target.read_text(encoding="utf-8"))
    assert [record["title"] for record in records] == ["标题B"]


def test_export_api_filters_by_server_validated_urls(tmp_path):
    export_dir = tmp_path / "url-filtered"
    selected_url = ARCHIVE[1]["url"]

    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/export",
            {
                "directory": str(export_dir),
                "format": "json",
                "filters": {"urls": [selected_url]},
            },
        )

    response = json.loads(body)
    assert status == 201
    assert response["count"] == 1
    records = json.loads(Path(response["path"]).read_text(encoding="utf-8"))
    assert [record["url"] for record in records] == [selected_url]


def test_export_allows_unmatched_selected_keyword_and_projects_matched_labels(tmp_path):
    export_dir = tmp_path / "partial-keywords"
    with _serve(tmp_path) as url:
        _write_archive(
            tmp_path,
            [
                {
                    **ARCHIVE[0],
                    "matched": ["人工智能", "旧关键词"],
                }
            ],
        )
        status, body = _request(
            "POST",
            url + "/api/export",
            {
                "directory": str(export_dir),
                "format": "json",
                "filters": {"keywords": ["人工智能", "生成式人工智能"]},
            },
        )

    response = json.loads(body)
    assert status == 201
    assert response["count"] == 1
    records = json.loads(Path(response["path"]).read_text(encoding="utf-8"))
    assert records[0]["matched"] == ["人工智能"]


def test_validate_export_request_normalizes_and_deduplicates_urls(tmp_path):
    request, error = web.validate_export_request(
        {
            "directory": "exports",
            "format": "json",
            "filters": {"urls": [" http://x/a ", "http://x/a", "http://x/b"]},
        },
        base_dir=tmp_path,
    )

    assert error is None
    assert request["filters"]["urls"] == ["http://x/a", "http://x/b"]


def test_export_api_empty_selection_still_writes_valid_csv(tmp_path):
    export_dir = tmp_path / "exports"

    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/export",
            {
                "directory": str(export_dir),
                "format": "csv",
                "filters": {"papers": [], "sort": "date-desc"},
            },
        )

    response = json.loads(body)
    target = next(export_dir.glob("newspaper-hits-*.csv"))
    assert status == 201
    assert response["count"] == 0
    assert response["format"] == "csv"
    assert target.read_text(encoding="utf-8").lstrip("\ufeff").startswith("paper,date,section")


def test_export_api_resolves_relative_directory_from_data_dir(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/export",
            {"directory": "chosen", "format": "json"},
        )

    response = json.loads(body)
    target = Path(response["path"])
    assert status == 201
    assert target.parent == (tmp_path / "chosen").resolve()
    assert target.is_file()


@pytest.mark.parametrize(
    "payload",
    [
        {"directory": "", "format": "csv"},
        {"directory": "/tmp", "format": "xml"},
        {"directory": "/tmp", "format": "csv", "records": []},
        {"directory": "/tmp", "format": "csv", "filters": []},
        {"directory": "/tmp", "format": "csv", "filters": {"from": "2026-99-99"}},
        {
            "directory": "/tmp",
            "format": "csv",
            "filters": {"from": "2026-09-23", "to": "2026-09-22"},
        },
        {"directory": "/tmp", "format": "csv", "filters": {"sort": "unknown"}},
        {"directory": "/tmp", "format": "csv", "filters": {"papers": ["不存在报"]}},
        {"directory": "/tmp", "format": "csv", "filters": {"urls": "http://x/a"}},
        {"directory": "/tmp", "format": "csv", "filters": {"urls": [""]}},
        {"directory": "/tmp", "format": "csv", "filters": {"urls": ["http://x/missing"]}},
    ],
)
def test_export_api_rejects_invalid_request_without_writing(tmp_path, payload):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/export", payload)

    assert status == 400
    assert json.loads(body)["error"]


def test_export_api_write_failure_returns_500(tmp_path, monkeypatch):
    def fail_export(*_args, **_kwargs):
        raise OSError("磁盘只读")

    monkeypatch.setattr(web.exporter, "export_hits", fail_export)
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/export",
            {"directory": str(tmp_path / "exports"), "format": "csv"},
        )

    assert status == 500
    # 只回原因，不加动作前缀 —— 前缀由前端统一加，两端各加一次会显示成「导出失败：导出失败：…」
    assert json.loads(body)["error"] == "磁盘只读"


def test_export_api_translates_filesystem_error(tmp_path, monkeypatch):
    """真实导出路径上的写盘失败（走 fsutil）要给出可读原因，而不是 errno 原文。"""

    def fail_write(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(web.exporter, "write_text", fail_write)
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/export",
            {"directory": str(tmp_path / "exports"), "format": "csv"},
        )

    assert status == 500
    error = json.loads(body)["error"]
    assert "无法写入文件夹" in error
    assert "EPERM" in error
    assert not error.startswith("导出失败"), "服务端不自带前缀，交给前端加一次"


def test_filter_export_records_matches_query_and_requested_sort():
    records = [
        {
            "paper": "乙报",
            "date": "2026-09-22",
            "title": "Beta",
            "section": "综合",
            "snippets": ["needle"],
            "matched": ["k"],
            "url": "http://x/b",
        },
        {
            "paper": "甲报",
            "date": "2026-09-21",
            "title": "Alpha needle",
            "section": "要闻",
            "snippets": [],
            "matched": ["k"],
            "url": "http://x/a",
        },
    ]
    filters = {
        "papers": None,
        "keywords": ["k"],
        "from": "2026-09-01",
        "to": "2026-09-30",
        "query": "NEEDLE",
        "sort": "title",
    }

    result = web.filter_export_records(records, filters)

    assert [record["title"] for record in result] == ["Alpha needle", "Beta"]


# -------------------------------------------- 导出目录选择：POST /api/export/browse
def test_export_browse_returns_the_directory_picked_on_the_dashboard_machine(tmp_path):
    """「浏览…」必须由**服务端**弹窗：浏览器给不出可写回服务端的绝对路径。"""
    chosen = tmp_path / "chosen"
    chosen.mkdir()
    seen: dict[str, object] = {}

    def picker(initial):
        seen["initial"] = initial
        return str(chosen)

    with _serve(tmp_path, folder_picker=picker) as url:
        status, body = _request("POST", url + "/api/export/browse", {"initial": "D:\\下载"})

    assert status == 200
    assert json.loads(body) == {"path": str(chosen.resolve()), "cancelled": False}
    assert seen["initial"] == "D:\\下载"


def test_export_browse_treats_cancel_as_success(tmp_path):
    """用户点「取消」不是失败：不能回 4xx/5xx 让前端弹错误提示。"""
    with _serve(tmp_path, folder_picker=lambda initial: None) as url:
        status, body = _request("POST", url + "/api/export/browse", {})

    assert status == 200
    assert json.loads(body) == {"path": None, "cancelled": True}


def test_export_browse_reports_501_when_the_device_has_no_dialog(tmp_path):
    """VPS / 无图形界面：501 + supported=false，前端据此收起按钮而不是报错。"""
    with _serve(tmp_path) as url:  # 默认注入的就是「弹不出窗口」的假实现
        status, body = _request("POST", url + "/api/export/browse", {})

    assert status == 501
    payload = json.loads(body)
    assert payload["supported"] is False
    assert "无法打开系统文件夹选择窗口" in payload["error"]


@pytest.mark.parametrize("chosen", ["missing-directory", "a-file"])
def test_export_browse_rejects_a_path_that_cannot_be_exported_to(tmp_path, chosen):
    """原生窗口的返回值同样过一遍导出校验，不能绕开 /api/export 的目录规则。"""
    path = tmp_path / chosen
    if chosen == "a-file":
        path.write_text("x", encoding="utf-8")

    with _serve(tmp_path, folder_picker=lambda initial: str(path)) as url:
        status, body = _request("POST", url + "/api/export/browse", {})

    assert status == 400
    assert json.loads(body)["error"]


@pytest.mark.parametrize("payload", [{"initial": 123}, {"initial": "a" * 5000}, {"nope": "x"}, "nope"])
def test_export_browse_rejects_invalid_payloads(tmp_path, payload):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/export/browse", payload)

    assert status == 400
    assert json.loads(body)["error"]


def test_export_browse_allows_only_one_window_at_a_time(tmp_path):
    """原生窗口是模态的：第二个请求立刻拿 409，而不是排队等第一个窗口关掉。"""
    started = threading.Event()
    release = threading.Event()

    def picker(initial):
        started.set()
        release.wait(10)
        return None

    with _serve(tmp_path, folder_picker=picker) as url:
        first: list[tuple[int, bytes]] = []
        worker = threading.Thread(
            target=lambda: first.append(_request("POST", url + "/api/export/browse", {}))
        )
        worker.start()
        try:
            assert started.wait(5), "第一个请求应已进入选择器"
            status, body = _request("POST", url + "/api/export/browse", {})
        finally:
            release.set()
            worker.join(10)

    assert status == 409
    assert "已有一个" in json.loads(body)["error"]
    assert first[0][0] == 200, "先到的那个请求应当正常完成"


# ---------------------------------------------------------------- 配置：POST/GET /api/config
def test_config_defaults_then_roundtrip(tmp_path):
    with _serve(tmp_path, _noop_runner) as url:
        status, body = _request("GET", url + "/api/config")
        assert status == 200
        cfg = json.loads(body)
        assert cfg["keywords"] == list(config.DEFAULT_KEYWORDS)
        assert cfg["days"] == config.DEFAULT_DAYS
        assert cfg["workers"] == config.DEFAULT_WORKERS
        # 新增字段：旧配置缺省时回落「按天数」，保证向后兼容
        assert cfg["mode"] == "days"
        assert cfg["since"] is None and cfg["until"] is None
        assert cfg["only"] == ""  # 旧配置缺省 = 全选

        payload = {"keywords": ["甲", "乙"], "days": 5, "workers": 3}
        expected = {**payload, "mode": "days", "since": None, "until": None, "only": ""}
        status, body = _request("POST", url + "/api/config", payload)
        assert status == 200
        assert json.loads(body) == expected

        status, body = _request("GET", url + "/api/config")
        assert json.loads(body) == expected
        assert (tmp_path / "state" / "web_config.json").exists()


def test_load_web_config_legacy_file_defaults_to_days(tmp_path):
    """旧配置（只有 keywords/days/workers）仍可读，mode 回落 days。"""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "web_config.json").write_text(
        json.dumps({"keywords": ["例大"], "days": 4, "workers": 2}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert web.load_web_config(tmp_path) == {
        "keywords": ["例大"],
        "days": 4,
        "workers": 2,
        "mode": "days",
        "since": None,
        "until": None,
        "only": "",
    }


def test_config_only_roundtrip(tmp_path):
    """only 字段往返：字符串按原样保存，GET 读回一致。"""
    with _serve(tmp_path) as url:
        payload = {"keywords": ["示例大学"], "days": 2, "workers": 8, "only": "湛江日报,南方日报"}
        status, body = _request("POST", url + "/api/config", payload)
        assert status == 200
        saved = json.loads(body)
        assert saved["only"] == "湛江日报,南方日报"

        status, body = _request("GET", url + "/api/config")
        assert json.loads(body)["only"] == "湛江日报,南方日报"


def test_config_only_accepts_list_and_normalizes(tmp_path):
    """only 也接受字符串数组（去空去重后以逗号连接）。"""
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/config",
            {"keywords": ["x"], "days": 2, "workers": 8, "only": ["湛江日报", " 湛江日报 ", "南方日报"]},
        )
        assert status == 200
        assert json.loads(body)["only"] == "湛江日报,南方日报"


def test_config_only_invalid_type_returns_400(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST", url + "/api/config", {"keywords": ["x"], "days": 2, "workers": 8, "only": 123}
        )
        assert status == 400
        assert json.loads(body)["error"]


def test_config_unknown_paper_does_not_write_file(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/config",
            {"keywords": ["x"], "days": 2, "workers": 8, "only": "不存在报"},
        )
        assert status == 400
        assert "不存在报" in json.loads(body)["error"]
        assert not (tmp_path / "state" / "web_config.json").exists()


def test_load_web_config_legacy_without_only_falls_back_to_empty(tmp_path):
    """旧 web_config.json（无 only）读回时 only 为空串 = 全选。"""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "web_config.json").write_text(
        json.dumps({"keywords": ["例大"], "days": 4, "workers": 2}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert web.load_web_config(tmp_path)["only"] == ""


def test_config_range_mode_roundtrip(tmp_path):
    with _serve(tmp_path) as url:
        payload = {
            "keywords": ["示例大学"],
            "workers": 8,
            "mode": "range",
            "since": "2026-09-23",
            "until": "2026-09-24",
        }
        status, body = _request("POST", url + "/api/config", payload)
        assert status == 200
        saved = json.loads(body)
        assert saved["mode"] == "range"
        assert saved["since"] == "2026-09-23"
        assert saved["until"] == "2026-09-24"

        status, body = _request("GET", url + "/api/config")
        assert json.loads(body) == saved


def test_config_invalid_mode_returns_400(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/config", {"keywords": ["x"], "mode": "weekly"})
        assert status == 400
        assert json.loads(body)["error"]


def test_config_days_mode_rejects_since_until(tmp_path):
    """mode=days 时提供 since/until 直接 400（不静默丢弃）。"""
    with _serve(tmp_path) as url:
        status, body = _request(
            "POST",
            url + "/api/config",
            {"keywords": ["x"], "mode": "days", "days": 3, "since": "2026-09-01"},
        )
        assert status == 400
        assert json.loads(body)["error"]


@pytest.mark.parametrize(
    "payload",
    [
        {"keywords": ["x"], "mode": "range", "until": "2026-09-24"},  # 缺 since
        {"keywords": ["x"], "mode": "range", "since": "2026-09-24", "until": "2026-09-23"},
        {"keywords": ["x"], "mode": "range", "since": "2026-01-01", "until": "2026-09-23"},
    ],
)
def test_config_range_invalid_dates_return_400(tmp_path, payload):
    with _serve(tmp_path) as url:
        status, _body = _request("POST", url + "/api/config", payload)
        assert status == 400


# ---------------------------------------------------------------- 自定义报社：GET/POST /api/papers
BUILTIN_FIRST = config.PAPERS[0]["name"]


def _paper_entry(name="测试报", **overrides):
    entry = {"name": name, "style": "ymd", "tpl": "https://example.com/html/layout/{a}/node_01.html"}
    entry.update(overrides)
    return entry


def test_papers_list_structure(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request("GET", url + "/api/papers")
        assert status == 200
        view = json.loads(body)
        assert view["builtin"] == [p["name"] for p in config.PAPERS]
        assert view["custom"] == []
        assert view["effective"] == view["builtin"]
        assert view["disabled"] == []
        assert [item["name"] for item in view["items"]] == view["builtin"]
        assert all(item["source"] == "builtin" for item in view["items"])
        assert all(item["enabled"] is True for item in view["items"])


def test_papers_list_items_include_disabled_override_and_custom(tmp_path):
    override = _paper_entry(
        name=BUILTIN_FIRST,
        tpl="https://example.com/override/{a}/node_01.html",
    )
    custom = _paper_entry(name="自定义日报")
    # 反向写入 disabled，确认 GET 按报社目录顺序返回稳定结果。
    papers.save_settings(tmp_path, [override, custom], ["自定义日报", BUILTIN_FIRST])

    with _serve(tmp_path) as url:
        status, body = _request("GET", url + "/api/papers")

    view = json.loads(body)
    items = {item["name"]: item for item in view["items"]}
    assert status == 200
    assert view["disabled"] == [BUILTIN_FIRST, "自定义日报"]
    assert BUILTIN_FIRST not in view["effective"]
    assert "自定义日报" not in view["effective"]
    assert items[BUILTIN_FIRST] == {
        "name": BUILTIN_FIRST,
        "source": "override",
        "enabled": False,
        "config": override,
    }
    assert items["自定义日报"] == {
        "name": "自定义日报",
        "source": "custom",
        "enabled": False,
        "config": custom,
    }


def test_papers_add_then_list(tmp_path):
    entry = _paper_entry(timeout=30, retries=2)
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers", entry)
        assert status == 200
        view = json.loads(body)
        assert view["custom"] == [entry]
        assert view["effective"][-1] == "测试报"  # 追加在内置之后
        assert (tmp_path / "papers.custom.json").exists()

        status, body = _request("GET", url + "/api/papers")
        assert json.loads(body) == view


def test_papers_cannot_change_while_scan_is_running(tmp_path):
    release = threading.Event()

    def runner(data_dir, params, log):
        release.wait(timeout=5)
        return {"exit_code": 0, "summary": None}

    with _serve(tmp_path, runner) as url:
        status, _ = _request("POST", url + "/api/scan", {"keywords": ["x"]})
        assert status == 202
        status, body = _request("POST", url + "/api/papers", _paper_entry())
        assert status == 409
        assert "扫描进行中" in json.loads(body)["error"]
        release.set()
        assert _wait_done(url)["status"] == "done"


def test_papers_add_preserves_corrupt_custom_file(tmp_path):
    custom_path = tmp_path / "papers.custom.json"
    original = "{ broken"
    custom_path.write_text(original, encoding="utf-8")
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers", _paper_entry())
        assert status == 409
        assert "请先修复原文件" in json.loads(body)["error"]
    assert custom_path.read_text(encoding="utf-8") == original


def test_papers_add_same_name_overrides_builtin(tmp_path):
    override = _paper_entry(name=BUILTIN_FIRST, tpl="https://example.com/override/{a}/node_01.html")
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers", override)
        assert status == 200
        view = json.loads(body)
        assert view["custom"] == [override]
        # 覆盖不改变内置顺序，也不新增重复项
        assert view["effective"] == [p["name"] for p in config.PAPERS]


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "", "style": "ymd", "tpl": "https://x/{a}"},
        {"name": "x", "style": "weekly", "tpl": "https://x/{a}"},
        {"name": "x", "style": "ymd", "tpl": "https://x/static"},
        {"name": "x", "style": "ymd", "tpl": "ftp://x/{a}"},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "timeout": 1},
        {"name": "x", "style": "ymd", "tpl": "https://x/{a}", "node_exclude": "("},
    ],
)
def test_papers_add_invalid_returns_400(tmp_path, bad):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers", bad)
        assert status == 400
        assert json.loads(body)["error"]


def test_papers_delete_custom_success(tmp_path):
    with _serve(tmp_path) as url:
        _request("POST", url + "/api/papers", _paper_entry())
        status, body = _request("POST", url + "/api/papers/delete", {"name": "测试报"})
        assert status == 200
        assert json.loads(body)["custom"] == []


def test_papers_delete_builtin_returns_400(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers/delete", {"name": BUILTIN_FIRST})
        assert status == 400
        assert json.loads(body)["error"]


def test_papers_delete_unknown_returns_404(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers/delete", {"name": "不存在的报社"})
        assert status == 404
        assert json.loads(body)["error"]


def test_papers_delete_override_reverts_to_builtin(tmp_path):
    """覆盖内置后再删除，等于回退到内置配置（允许）。"""
    with _serve(tmp_path) as url:
        _request("POST", url + "/api/papers", _paper_entry(name=BUILTIN_FIRST))
        status, body = _request("POST", url + "/api/papers/delete", {"name": BUILTIN_FIRST})
        assert status == 200
        assert json.loads(body)["custom"] == []


def test_papers_remove_builtin_is_permanent(tmp_path):
    """内置也可以被**彻底删除**：从列表消失、不再参与扫描、也不留「已停用」痕迹。"""
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers/remove", {"name": BUILTIN_FIRST})
        view = json.loads(body)

        assert status == 200
        assert BUILTIN_FIRST in view["removed"]
        assert BUILTIN_FIRST not in view["effective"], "删掉的内置不应再参与扫描"
        assert BUILTIN_FIRST not in [item["name"] for item in view["items"]], "不应再出现在列表里"
        assert BUILTIN_FIRST not in view["disabled"], "彻底删除不是「停用」"


def test_papers_remove_builtin_drops_its_override_and_disabled_record(tmp_path):
    """删内置时顺手清掉同名覆盖与停用记录 —— 否则残留的覆盖会把它复活。"""
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/papers", _paper_entry(name=BUILTIN_FIRST))[0] == 200
        assert (
            _request("POST", url + "/api/papers/status", {"name": BUILTIN_FIRST, "enabled": False})[0] == 200
        )

        status, body = _request("POST", url + "/api/papers/remove", {"name": BUILTIN_FIRST})
        view = json.loads(body)

        assert status == 200
        assert view["custom"] == [], "同名覆盖应被一并清掉"
        assert view["disabled"] == []
        assert BUILTIN_FIRST not in [item["name"] for item in view["items"]]


def test_papers_remove_then_add_same_name_revives_it(tmp_path):
    """删掉内置之后自己再加一家同名报社应该可用：新增时会把名字摘出 ``removed``。"""
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/papers/remove", {"name": BUILTIN_FIRST})[0] == 200

        status, body = _request("POST", url + "/api/papers", _paper_entry(name=BUILTIN_FIRST))
        view = json.loads(body)

        assert status == 200
        assert BUILTIN_FIRST in view["effective"]
        assert BUILTIN_FIRST not in view["removed"]


def test_papers_remove_custom_success_and_unknown_404(tmp_path):
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/papers", _paper_entry())[0] == 200
        status, body = _request("POST", url + "/api/papers/remove", {"name": "测试报"})
        assert status == 200
        assert json.loads(body)["custom"] == []

        status, body = _request("POST", url + "/api/papers/remove", {"name": "不存在的报社"})
        assert status == 404
        assert json.loads(body)["error"]


def test_papers_status_on_removed_builtin_returns_404(tmp_path):
    """已被彻底删除的报社不能再用 status 接口「恢复」——它已经不在目录里了。"""
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/papers/remove", {"name": BUILTIN_FIRST})[0] == 200
        status, body = _request("POST", url + "/api/papers/status", {"name": BUILTIN_FIRST, "enabled": True})
        assert status == 404
        assert json.loads(body)["error"]


def test_papers_status_disables_and_enables_builtin_and_custom(tmp_path):
    custom_name = "自定义日报"
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/papers", _paper_entry(name=custom_name))[0] == 200

        status, body = _request("POST", url + "/api/papers/status", {"name": BUILTIN_FIRST, "enabled": False})
        view = json.loads(body)
        assert status == 200
        assert BUILTIN_FIRST in view["disabled"]
        assert BUILTIN_FIRST not in view["effective"]

        status, body = _request("POST", url + "/api/papers/status", {"name": custom_name, "enabled": False})
        view = json.loads(body)
        assert status == 200
        assert custom_name in view["disabled"]
        assert custom_name not in view["effective"]

        status, body = _request("POST", url + "/api/papers/status", {"name": BUILTIN_FIRST, "enabled": True})
        view = json.loads(body)
        assert status == 200
        assert BUILTIN_FIRST not in view["disabled"]
        assert BUILTIN_FIRST in view["effective"]
        assert custom_name in view["disabled"]


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"name": "不存在报", "enabled": False}, 404),
        ({"name": BUILTIN_FIRST, "enabled": "false"}, 400),
        ({"name": BUILTIN_FIRST}, 400),
        ({"name": BUILTIN_FIRST, "enabled": False, "extra": 1}, 400),
        ([BUILTIN_FIRST, False], 400),
    ],
)
def test_papers_status_rejects_unknown_or_invalid_payload(tmp_path, payload, expected_status):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/papers/status", payload)

    assert status == expected_status
    assert json.loads(body)["error"]


def test_papers_status_cannot_change_while_scan_is_running(tmp_path):
    release = threading.Event()

    def runner(data_dir, params, log):
        release.wait(timeout=5)
        return {"exit_code": 0, "summary": None}

    with _serve(tmp_path, runner) as url:
        try:
            status, _ = _request("POST", url + "/api/scan", {"keywords": ["x"]})
            assert status == 202
            status, body = _request(
                "POST", url + "/api/papers/status", {"name": BUILTIN_FIRST, "enabled": False}
            )
            assert status == 409
            assert "扫描进行中" in json.loads(body)["error"]
            assert not (tmp_path / config.CUSTOM_PAPERS_FILENAME).exists()
        finally:
            release.set()
        assert _wait_done(url)["status"] == "done"


def test_papers_status_persists_across_server_restart(tmp_path):
    with _serve(tmp_path) as url:
        status, _ = _request("POST", url + "/api/papers/status", {"name": BUILTIN_FIRST, "enabled": False})
        assert status == 200

    with _serve(tmp_path) as url:
        status, body = _request("GET", url + "/api/papers")

    view = json.loads(body)
    assert status == 200
    assert view["disabled"] == [BUILTIN_FIRST]
    assert next(item for item in view["items"] if item["name"] == BUILTIN_FIRST)["enabled"] is False


def test_scan_rejects_when_all_papers_are_disabled(tmp_path):
    papers.save_settings(tmp_path, [], [paper["name"] for paper in config.PAPERS])
    called = False

    def runner(data_dir, params, log):  # pragma: no cover - 不应执行
        nonlocal called
        called = True
        return {"exit_code": 0, "summary": None}

    with _serve(tmp_path, runner) as url:
        status, body = _request("POST", url + "/api/scan", {"keywords": ["人工智能"]})

    assert status == 400
    assert "没有已启用的报社" in json.loads(body)["error"]
    assert called is False


def test_papers_test_uses_injected_probe(tmp_path):
    seen = {}

    def probe(paper, log):
        seen["paper"] = paper
        log("探测 " + paper["name"])
        return {"entry_code": 200, "sections": 12, "note": "dry-run"}

    with _serve(tmp_path, paper_probe=probe) as url:
        status, body = _request("POST", url + "/api/papers/test", _paper_entry())
        assert status == 200
        assert json.loads(body) == {
            "entry": "",
            "entry_code": 200,
            "sections": 12,
            "sample_articles": None,
            "ok": True,
            "note": "dry-run",
        }
        assert seen["paper"]["name"] == "测试报"


def test_papers_test_by_name_resolves_builtin(tmp_path):
    def probe(paper, log):
        return {"entry_code": 200, "sections": 3, "note": ""}

    with _serve(tmp_path, paper_probe=probe) as url:
        status, body = _request("POST", url + "/api/papers/test", {"name": BUILTIN_FIRST})
        assert status == 200
        assert json.loads(body)["ok"] is True


def test_papers_test_invalid_returns_400(tmp_path):
    def probe(paper, log):  # pragma: no cover - 非法 body 不应触发探测
        raise AssertionError("非法 body 不应触发探测")

    with _serve(tmp_path, paper_probe=probe) as url:
        status, body = _request("POST", url + "/api/papers/test", {"name": "不存在的报社"})
        assert status == 400
        assert json.loads(body)["error"]


def test_papers_test_invalid_complete_object_does_not_fall_back_to_builtin_name(tmp_path):
    def probe(paper, log):  # pragma: no cover - 非法完整对象不应触发探测
        raise AssertionError("非法完整对象不应退化为按名称试抓")

    payload = {
        "name": BUILTIN_FIRST,
        "style": "weekly",
        "tpl": "https://example.com/{a}/node_01.html",
    }
    with _serve(tmp_path, paper_probe=probe) as url:
        status, body = _request("POST", url + "/api/papers/test", payload)

    assert status == 400
    assert "style" in json.loads(body)["error"]


def test_papers_post_rejects_foreign_origin(tmp_path):
    with _serve(tmp_path) as url:
        status, _body = _request(
            "POST",
            url + "/api/papers",
            _paper_entry(),
            headers={"Origin": "http://evil.example"},
        )
        assert status == 403


def test_papers_post_requires_json_content_type(tmp_path):
    with _serve(tmp_path) as url:
        status, _body = _request(
            "POST",
            url + "/api/papers",
            _paper_entry(),
            headers={"Content-Type": "text/plain"},
        )
        assert status == 415


# ---------------------------------------------------------------- 静态资源：ETag / gzip
def _open(url, headers=None):
    request = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(request, timeout=5)


def test_static_serves_etag_last_modified_and_revalidates(base_url):
    with _open(base_url + "/static/app.js") as resp:
        assert resp.status == 200
        etag = resp.headers.get("ETag")
        assert etag
        assert resp.headers.get("Last-Modified")
        assert "no-cache" in (resp.headers.get("Cache-Control") or "")
    with pytest.raises(urllib.error.HTTPError) as exc:
        _open(base_url + "/static/app.js", {"If-None-Match": etag})
    assert exc.value.code == 304
    assert exc.value.headers.get("ETag") == etag


def test_static_gzip_when_client_accepts(base_url):
    import gzip

    with _open(base_url + "/static/style.css", {"Accept-Encoding": "gzip"}) as resp:
        assert resp.headers.get("Content-Encoding") == "gzip"
        assert "Accept-Encoding" in (resp.headers.get("Vary") or "")
        raw = resp.read()
    text = gzip.decompress(raw).decode("utf-8")
    assert "--bg" in text or "body" in text


def test_static_q0_disables_gzip(base_url):
    with _open(base_url + "/static/style.css", {"Accept-Encoding": "gzip;q=0"}) as resp:
        assert resp.headers.get("Content-Encoding") is None


def test_accepts_gzip_parsing():
    assert web._accepts_gzip("gzip") is True
    assert web._accepts_gzip("deflate, gzip;q=0.8") is True
    assert web._accepts_gzip("gzip;q=0") is False
    assert web._accepts_gzip("identity") is False


def test_etag_matches_parsing():
    """If-None-Match 解析：容忍逗号列表、通配 *，并按弱比较忽略两端 W/ 前缀。"""
    etag = 'W/"123-456"'
    assert web._etag_matches(etag, etag) is True
    assert web._etag_matches('"123-456"', etag) is True  # 弱比较忽略 ETag 自身的 W/
    assert web._etag_matches('"abc", W/"123-456"', etag) is True  # 候选列表
    assert web._etag_matches('  W/"123-456"  ', etag) is True  # 两端空白
    assert web._etag_matches("*", etag) is True  # 通配
    assert web._etag_matches('"other"', etag) is False
    assert web._etag_matches('"123-456", "other"', etag) is True
    assert web._etag_matches(None, etag) is False
    assert web._etag_matches("", etag) is False


def test_static_not_modified_keeps_security_headers(base_url):
    """304 响应也必须带上与 200 相同的安全头，gzip 资源再附 Vary。"""
    with _open(base_url + "/static/app.js") as resp:
        etag = resp.headers.get("ETag")
        assert etag
    with pytest.raises(urllib.error.HTTPError) as exc:
        _open(base_url + "/static/app.js", {"If-None-Match": etag})
    assert exc.value.code == 304
    headers = exc.value.headers
    assert "style-src 'self' 'unsafe-inline'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Permissions-Policy"] == "camera=(), geolocation=(), microphone=()"
    assert "Accept-Encoding" in (headers.get("Vary") or "")


def test_static_etag_uses_nanosecond_mtime(tmp_path, monkeypatch):
    """ETag 由 st_mtime_ns 派生，同秒同体积改写也能被识别（不再停留在秒级）。"""
    import newspaper_auto.web_server as web_server

    static = tmp_path / "static"
    static.mkdir()
    target = static / "app.js"
    target.write_text("x" * 2000, encoding="utf-8")
    monkeypatch.setattr(web_server, "STATIC_DIR", static)

    with _serve(tmp_path) as url, _open(url + "/static/app.js") as resp:
        etag = resp.headers.get("ETag")

    stat = target.stat()
    assert etag == f'W/"{stat.st_mtime_ns}-{stat.st_size}"'


# ---------------------------------------------------------------- 取消 / 历史 / SSE
def test_scan_cancel_marks_task_cancelled(tmp_path):
    release = threading.Event()

    def runner(data_dir, params, log):
        log("扫描中")
        release.wait(timeout=5)
        return {"exit_code": 130, "summary": None}

    with _serve(tmp_path, runner) as url:
        assert _request("POST", url + "/api/scan", {"keywords": ["x"]})[0] == 202
        status, body = _request("POST", url + "/api/scan/cancel", {})
        assert status == 202
        assert json.loads(body)["cancel_requested"] is True
        release.set()
        task = _wait_done(url)
        assert task["status"] == "cancelled"
        assert task["exit_code"] == 130


def test_scan_cancel_when_idle_returns_409(tmp_path):
    with _serve(tmp_path) as url:
        status, body = _request("POST", url + "/api/scan/cancel", {})
        assert status == 409
        assert json.loads(body)["error"]


def test_scan_cancel_after_done_returns_409(tmp_path):
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/scan", {"keywords": ["x"]})[0] == 202
        assert _wait_done(url)["status"] == "done"
        status, _body = _request("POST", url + "/api/scan/cancel", {})
        assert status == 409


def test_scan_history_persisted_and_listed(tmp_path):
    with _serve(tmp_path) as url:
        assert _request("POST", url + "/api/scan", {"keywords": ["x"]})[0] == 202
        _wait_done(url)
        # 历史落盘在任务锁释放之后（不阻塞下一次扫描），属最终一致：轮询等待即可
        tasks: list[dict] = []
        for _ in range(40):
            status, body = _request("GET", url + "/api/scan/history")
            assert status == 200
            tasks = json.loads(body)["tasks"]
            if tasks:
                break
            time.sleep(0.05)
        assert len(tasks) == 1
        assert tasks[0]["status"] == "done"
        assert tasks[0]["keywords"] == ["x"]
        assert (tmp_path / "state" / "tasks.json").exists()


def test_scan_events_streams_idle_and_closes(tmp_path):
    with _serve(tmp_path) as url, _open(url + "/api/scan/events") as resp:
        assert "text/event-stream" in (resp.headers.get("Content-Type") or "")
        raw = resp.read().decode()
    assert raw.startswith("data: ")
    event = json.loads(raw[len("data: ") :].strip())
    assert event["status"] == "idle"


def test_scan_events_streams_running_until_terminal(tmp_path):
    release = threading.Event()

    def runner(data_dir, params, log):
        log("  ✓ 湛江日报 09-24")
        release.wait(timeout=5)
        return {"exit_code": 0, "summary": {"hits": 1}}

    with _serve(tmp_path, runner) as url:
        assert _request("POST", url + "/api/scan", {"keywords": ["x"]})[0] == 202
        statuses: list[str] = []
        logs: list[str] = []
        with _open(url + "/api/scan/events?after=0") as resp:
            for _ in range(50):
                line = resp.readline().decode()
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: ") :].strip())
                statuses.append(event["status"])
                logs.extend(event.get("log") or [])
                if event["status"] == "running":
                    release.set()
                if event["status"] in ("done", "error", "cancelled"):
                    break
        assert statuses[0] == "running"
        assert statuses[-1] == "done"
        assert any("湛江日报" in entry for entry in logs)


# ---------------------------------------------------------------- 后台运行（--detach/--stop/--status）
def test_main_status_reports_not_running(tmp_path, capsys):
    assert web.main(["--status", "--data-dir", str(tmp_path)]) == 1
    assert "未在运行" in capsys.readouterr().out


def test_main_stop_without_running_instance(tmp_path, capsys):
    assert web.main(["--stop", "--data-dir", str(tmp_path)]) == 1
    assert "未停止" in capsys.readouterr().out


def test_main_status_reports_running_instance(tmp_path, capsys, monkeypatch):
    web.web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web.web_daemon, "pid_alive", lambda pid: True)
    monkeypatch.setattr(web.web_daemon, "health", lambda *a, **k: {"ok": True, "version": "9.9.9"})

    assert web.main(["--status", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "看板正在运行" in out
    assert "http://127.0.0.1:8788/" in out
    assert "9.9.9" in out


def test_main_stop_reports_success(tmp_path, capsys, monkeypatch):
    web.web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web.web_daemon, "pid_alive", lambda pid: True)
    monkeypatch.setattr(web.web_daemon.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(web.web_daemon, "POLL_INTERVAL", 0.001)

    # pid_alive 一直为真 → 走强制杀分支，但对外仍是「已停止」
    assert web.main(["--stop", "--data-dir", str(tmp_path)]) == 0
    assert "已停止后台看板" in capsys.readouterr().out


def test_main_writes_and_clears_dashboard_state(tmp_path, capsys, monkeypatch):
    """前台服务也要落运行状态（这样 --stop 对前台实例同样有效），退出时清理。"""
    captured: dict[str, object] = {}

    def fake_serve_forever(self):
        captured["state"] = web.web_daemon.read_state(tmp_path)
        raise KeyboardInterrupt

    monkeypatch.setattr(web.DashboardServer, "serve_forever", fake_serve_forever)

    assert web.main(["--data-dir", str(tmp_path), "--host", "127.0.0.1", "--port", "0"]) == 0
    state = captured["state"]
    assert isinstance(state, dict)
    assert state["port"] > 0
    assert state["pid"] == web.os.getpid()
    assert "看板已启动" in capsys.readouterr().out
    assert web.web_daemon.read_state(tmp_path) is None, "退出时应清理状态文件"
