"""CLI 端到端测试（注入 fake fetcher，不访问网络）。"""

from __future__ import annotations

import json

import pytest

from newspaper_auto import cli, config, exporter, report

ZJ_ARGS = ["--only", "湛江日报", "--days", "1", "--kw", "广东海洋大学"]
ZJ_ARGS_NO_DAYS = ["--only", "湛江日报", "--kw", "广东海洋大学"]
HIT_FILES = {"content_101.html", "content_102.html", "content_104.html"}


def _hit_files(records) -> set[str]:
    return {record["url"].rsplit("/", 1)[-1] for record in records}


def test_json_stdout_is_pure_and_stateless(tmp_path, capsys, site_fetcher, today):
    code = cli.main(
        [*ZJ_ARGS, "--no-state", "--json", "--data-dir", str(tmp_path)], fetcher=site_fetcher(today)
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert _hit_files(json.loads(out)) == HIT_FILES
    assert "关键词" in err and "关键词" not in out  # 日志进 stderr，stdout 纯净
    assert list(tmp_path.rglob("*")) == []  # 无状态：不产生任何文件


def test_csv_stdout(tmp_path, capsys, site_fetcher, today):
    code = cli.main(
        [*ZJ_ARGS, "--no-state", "--csv", "--data-dir", str(tmp_path)], fetcher=site_fetcher(today)
    )
    out, _err = capsys.readouterr()
    lines = [line for line in out.strip().splitlines() if line]
    assert code == 0
    assert lines[0] == "paper,date,section,title,url,text_len,matched"
    assert len(lines) == 4


def test_export_dir_writes_all_hits_as_csv_without_enabling_state(tmp_path, capsys, site_fetcher, today):
    export_dir = tmp_path / "chosen" / "nested"
    data_dir = tmp_path / "data"

    code = cli.main(
        [
            *ZJ_ARGS,
            "--no-state",
            "--data-dir",
            str(data_dir),
            "--export-dir",
            str(export_dir),
        ],
        fetcher=site_fetcher(today),
    )

    out, _err = capsys.readouterr()
    files = list(export_dir.glob("newspaper-hits-*.csv"))
    assert code == 0
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").startswith("\ufeff")
    assert "导出：" in out
    assert not data_dir.exists()


def test_export_dir_supports_json(tmp_path, site_fetcher, today):
    export_dir = tmp_path / "exports"

    code = cli.main(
        [
            *ZJ_ARGS,
            "--no-state",
            "--export-dir",
            str(export_dir),
            "--export-format",
            "json",
        ],
        fetcher=site_fetcher(today),
    )

    files = list(export_dir.glob("newspaper-hits-*.json"))
    assert code == 0
    assert len(files) == 1
    assert _hit_files(json.loads(files[0].read_text(encoding="utf-8"))) == HIT_FILES


def test_export_format_without_directory_exits_2_before_network():
    calls: list[str] = []

    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["--export-format", "json"],
            fetcher=lambda url: calls.append(url) or (200, "x" * 400),
        )

    assert exc.value.code == 2
    assert calls == []


def test_dry_cannot_export_before_network(tmp_path):
    calls: list[str] = []

    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["--dry", "--export-dir", str(tmp_path / "exports")],
            fetcher=lambda url: calls.append(url) or (200, "x" * 400),
        )

    assert exc.value.code == 2
    assert calls == []


def test_export_failure_returns_nonzero(tmp_path, capsys, monkeypatch, site_fetcher, today):
    def fail_export(*_args, **_kwargs):
        raise OSError("模拟导出失败")

    monkeypatch.setattr(exporter, "export_hits", fail_export)
    code = cli.main(
        [*ZJ_ARGS, "--no-state", "--export-dir", str(tmp_path / "exports")],
        fetcher=site_fetcher(today),
    )

    out, _err = capsys.readouterr()
    assert code == 1
    assert "结果导出失败：模拟导出失败" in out


def test_json_and_csv_are_mutually_exclusive(capsys, site_fetcher, today):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "--csv"], fetcher=site_fetcher(today))
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--days", "0"],
        ["--days", "-3"],
        ["--days", str(config.MAX_RANGE_DAYS + 1)],
        ["--workers", "0"],
        ["--workers", str(config.MAX_WORKERS + 1)],
        ["--kw", ""],
        ["--kw", "   "],
    ],
)
def test_invalid_scalar_arguments_exit_2_before_network(argv):
    calls: list[str] = []
    with pytest.raises(SystemExit) as exc:
        cli.main(argv, fetcher=lambda url: calls.append(url) or (200, "x" * 400))
    assert exc.value.code == 2
    assert calls == []


def test_too_many_keywords_exit_2_before_network():
    argv = [part for index in range(config.MAX_KEYWORDS + 1) for part in ("--kw", f"k{index}")]
    with pytest.raises(SystemExit) as exc:
        cli.main(argv, fetcher=lambda _url: (_ for _ in ()).throw(AssertionError("should not fetch")))
    assert exc.value.code == 2


def test_duplicate_keywords_are_deduplicated(tmp_path, capsys, site_fetcher, today):
    code = cli.main(
        [
            "--only",
            "湛江日报",
            "--days",
            "1",
            "--kw",
            "广东海洋大学",
            "--kw",
            "广东海洋大学",
            "--no-state",
            "--data-dir",
            str(tmp_path),
        ],
        fetcher=site_fetcher(today),
    )
    out, _ = capsys.readouterr()
    assert code == 0
    assert "关键词：广东海洋大学\n" in out


def test_archive_skips_dirty_rows_and_refreshes_existing_metadata(tmp_path):
    archive_path = tmp_path / "hits" / "archive.json"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_text(
        json.dumps(
            [
                {"title": "缺 URL"},
                {"url": "u1", "title": "旧", "matched": ["旧词"]},
                {"url": "u2", "matched": "损坏字符串"},
            ]
        ),
        encoding="utf-8",
    )
    merged = cli.update_archive(
        tmp_path,
        [{"url": "u1", "title": "新", "matched": ["新词"], "geometry": {"index": 1, "total": 1}}],
    )
    assert len(merged) == 2
    assert merged[0]["url"] == "u1"
    assert merged[0]["title"] == "新"
    assert merged[0]["matched"] == ["旧词", "新词"]
    assert merged[0]["geometry"] == {"index": 1, "total": 1}
    assert merged[1]["url"] == "u2"
    assert merged[1]["matched"] == []
    assert merged[1]["paper"] == "" and merged[1]["date"] == ""


def test_first_run_writes_state_and_reports_then_second_run_reports_zero_new(
    tmp_path, capsys, site_fetcher, today
):
    fetch = site_fetcher(today)
    argv = [*ZJ_ARGS, "--data-dir", str(tmp_path)]

    assert cli.main(argv, fetcher=fetch) == 0
    out1, _ = capsys.readouterr()
    assert "新增 3 篇" in out1
    assert (tmp_path / "state" / "seen.json").exists()
    assert (tmp_path / "hits" / "index.html").exists()
    assert list((tmp_path / "hits").glob("*.md"))

    assert cli.main(argv, fetcher=fetch) == 0
    out2, _ = capsys.readouterr()
    assert "新增 0 篇" in out2

    archive = json.loads((tmp_path / "hits" / "archive.json").read_text(encoding="utf-8"))
    assert len(archive) == 3
    seen = json.loads((tmp_path / "state" / "seen.json").read_text(encoding="utf-8"))
    assert len(seen["广东海洋大学"]) == 3


def test_seen_is_not_committed_when_report_write_fails(tmp_path, capsys, monkeypatch, site_fetcher, today):
    """seen.json 是最后提交标志；报告写失败时不得把命中提前标成已见。"""

    def fail_write(*_args, **_kwargs):
        raise OSError("模拟磁盘写入失败")

    monkeypatch.setattr(report, "write_md", fail_write)
    code = cli.main([*ZJ_ARGS, "--data-dir", str(tmp_path)], fetcher=site_fetcher(today))
    out, _ = capsys.readouterr()
    assert code == 1
    assert "结果写入失败" in out
    assert not (tmp_path / "state" / "seen.json").exists()


def test_no_state_ignores_existing_seen(tmp_path, capsys, site_fetcher, today):
    fetch = site_fetcher(today)
    argv = [*ZJ_ARGS, "--data-dir", str(tmp_path)]
    cli.main(argv, fetcher=fetch)
    capsys.readouterr()

    cli.main([*argv, "--no-state"], fetcher=fetch)
    out, _ = capsys.readouterr()
    assert "新增 3 篇" in out


def test_console_hit_line_includes_section(tmp_path, capsys, site_fetcher, today):
    """控制台命中行应带版面（报纸中的相对位置），便于人工快速定位。"""
    cli.main([*ZJ_ARGS, "--no-state", "--data-dir", str(tmp_path)], fetcher=site_fetcher(today))
    out, _ = capsys.readouterr()
    assert "湛江日报 · 第02版：综合" in out


def test_full_reports_all_hits_as_new(tmp_path, capsys, site_fetcher, today):
    fetch = site_fetcher(today)
    argv = [*ZJ_ARGS, "--data-dir", str(tmp_path)]
    cli.main(argv, fetcher=fetch)
    capsys.readouterr()

    cli.main([*argv, "--full"], fetcher=fetch)
    out, _ = capsys.readouterr()
    assert "新增 3 篇" in out


def test_dry_run_json_returns_empty_array(tmp_path, capsys, site_fetcher, today):
    code = cli.main(
        [*ZJ_ARGS, "--dry", "--json", "--no-state", "--data-dir", str(tmp_path)], fetcher=site_fetcher(today)
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert json.loads(out) == []
    assert "dry-run 结束" in err


def test_entry_failure_exits_nonzero(tmp_path, capsys, today):
    """入口失败时 CLI 应以非零退出，让 Actions 步骤变红。"""
    code = cli.main(
        ["--only", "光明日报", "--days", "1", "--data-dir", str(tmp_path)], fetcher=lambda url: (404, "")
    )
    out, _err = capsys.readouterr()
    assert code == 1, "入口失败应返回退出码 1"
    assert "入口失败" in out  # 警告已输出（--json/--csv 时才走 stderr）


def test_entry_failure_still_writes_report(tmp_path, capsys, today):
    """入口失败时，报告和 index.html 应已写入（for Actions 提交步骤）。"""
    cli.main(
        ["--only", "光明日报", "--days", "1", "--data-dir", str(tmp_path)], fetcher=lambda url: (404, "")
    )
    # hits/ 目录和报告文件在失败前已创建
    hits_dir = tmp_path / "hits"
    assert hits_dir.is_dir()


def test_partial_failure_article_does_not_block_hits(tmp_path, capsys, site_fetcher, today):
    """部分文章页失败：失败计入统计且不影响已命中的文章。"""
    date_part = f"{today.year}-{today.month:02d}/{today.day:02d}"
    bad_url = f"http://paper.gdzjdaily.com.cn/html/{date_part}/content_103.html"

    def fetcher(url: str):
        if url == bad_url:
            return (500, "")
        return site_fetcher(today)(url)

    code = cli.main(
        ["--only", "湛江日报", "--days", "1", "--no-state", "--json", "--data-dir", str(tmp_path)],
        fetcher=fetcher,
    )
    out, _err = capsys.readouterr()
    records = json.loads(out)
    hit_files = _hit_files(records)
    # content_104 命中；content_103 失败被跳过，其余正常
    assert "content_104.html" in hit_files
    # 文章失败不导致 CLI 非零退出（只有入口失败才非零）
    assert code == 0


def test_select_papers_reports_unmatched():
    """select_papers 返回 (选中, 未识别)；零匹配不再静默回退全部。"""
    selected, unmatched = cli.select_papers("不存在的报纸")
    assert selected == []
    assert unmatched == ["不存在的报纸"]

    selected, unmatched = cli.select_papers("湛江日报")
    assert [p["name"] for p in selected] == ["湛江日报"]
    assert unmatched == []

    selected, unmatched = cli.select_papers("")
    assert selected == list(config.PAPERS)
    assert unmatched == []


def test_select_papers_partial_match_warns_and_continues():
    selected, unmatched = cli.select_papers("湛江日报,不存在的报")
    assert [p["name"] for p in selected] == ["湛江日报"]
    assert unmatched == ["不存在的报"]


def test_only_single_paper_no_warning(tmp_path, capsys, site_fetcher, today):
    code = cli.main([*ZJ_ARGS, "--no-state", "--data-dir", str(tmp_path)], fetcher=site_fetcher(today))
    out, _err = capsys.readouterr()
    assert code == 0
    assert "未识别" not in out
    assert "报社：湛江日报" in out


def test_only_partial_match_warns(tmp_path, capsys, site_fetcher, today):
    code = cli.main(
        ["--only", "湛江日报,不存在的报", "--days", "1", "--no-state", "--data-dir", str(tmp_path)],
        fetcher=site_fetcher(today),
    )
    out, _err = capsys.readouterr()
    assert code == 0
    assert "⚠️ --only 未识别：不存在的报" in out
    assert "报社：湛江日报" in out


def test_only_all_unmatched_exits_2_without_network(tmp_path):
    """零匹配：退出码 2，且不发起任何网络请求。"""
    calls: list[str] = []

    def fetcher(url: str):
        calls.append(url)
        return (200, "")

    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["--only", "不存在的报", "--days", "1", "--data-dir", str(tmp_path)],
            fetcher=fetcher,
        )
    assert exc.value.code == 2
    assert calls == []


def test_only_empty_behaves_like_all(tmp_path, capsys):
    """--only "" 等价不传：扫全部、无警告。"""
    code = cli.main(
        ["--only", "", "--days", "1", "--no-state", "--data-dir", str(tmp_path)],
        fetcher=lambda url: (404, ""),
    )
    out, _err = capsys.readouterr()
    assert code == 1  # 全部入口 404，但扫描确实已对全部报社发起
    assert "未识别" not in out
    for paper in config.PAPERS:
        assert paper["name"] in out


# ---------------------------------------------------------------- 任务 A：日期范围 CLI


def test_since_without_until_defaults_to_today(tmp_path, site_fetcher, today):
    """--since today，until 默认今天，扫出单日结果。"""
    since_str = today.strftime("%Y-%m-%d")
    code = cli.main(
        [*ZJ_ARGS_NO_DAYS, "--since", since_str, "--no-state", "--json", "--data-dir", str(tmp_path)],
        fetcher=site_fetcher(today),
    )
    assert code == 0


def test_until_without_since_defaults_to_single_day(tmp_path, site_fetcher, today):
    """只给 --until，since 默认等于 until，扫单日。"""
    until_str = today.strftime("%Y-%m-%d")
    code = cli.main(
        [*ZJ_ARGS_NO_DAYS, "--until", until_str, "--no-state", "--json", "--data-dir", str(tmp_path)],
        fetcher=site_fetcher(today),
    )
    assert code == 0


def test_since_until_range_is_inclusive(tmp_path, site_fetcher, today):
    """--since 与 --until 同一天，结果与 --days 1 等价。"""
    day_str = today.strftime("%Y-%m-%d")
    rc_range = cli.main(
        [
            *ZJ_ARGS_NO_DAYS,
            "--since",
            day_str,
            "--until",
            day_str,
            "--no-state",
            "--json",
            "--data-dir",
            str(tmp_path),
        ],
        fetcher=site_fetcher(today),
    )
    assert rc_range == 0


def test_dates_order_new_to_old_with_range():
    """--since older --until newer：dates 列表从新到旧。"""
    args = cli.build_parser().parse_args(["--since", "2026-09-20", "--until", "2026-09-22"])
    dates = cli.resolve_dates(args)
    assert dates[0] >= dates[-1], "dates[0] 应为最新日期"
    assert len(dates) == 3


def test_since_after_until_exits_2():
    """since > until 应以退出码 2 拒绝，不静默扫空结果。"""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--since", "2026-09-22", "--until", "2026-09-20"])
    assert exc_info.value.code == 2


def test_span_over_limit_exits_2():
    """跨度超过 62 天应以退出码 2 拒绝。"""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--since", "2026-01-01", "--until", "2026-09-23"])
    assert exc_info.value.code == 2


def test_days_and_since_mutually_exclusive():
    """--days 与 --since 不能同时使用，argparse 应报错退出码 2。"""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--days", "3", "--since", "2026-09-20"])
    assert exc_info.value.code == 2


def test_days_and_until_mutually_exclusive():
    """--days 与 --until 不能同时使用，应报错退出码 2。"""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--days", "3", "--until", "2026-09-20"])
    assert exc_info.value.code == 2


def test_invalid_date_format_exits_2():
    """非法日期字符串应 argparse 报错，退出码 2。"""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--since", "not-a-date"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------- 任务 B：命中行带版内位置
def test_console_hit_line_includes_area_position(tmp_path, capsys, zj_area_fetcher, area_day):
    """命中行形如 [湛江日报 · 第1版：要闻 · 版内 1/7 · 上版 6%]（真实 area 坐标）。"""
    day = area_day.strftime("%Y-%m-%d")
    code = cli.main(
        [
            "--only",
            "湛江日报",
            "--since",
            day,
            "--until",
            day,
            "--kw",
            "世界技能大赛",
            "--no-state",
            "--data-dir",
            str(tmp_path),
        ],
        fetcher=zj_area_fetcher,
    )
    out, _err = capsys.readouterr()
    assert code == 0
    assert "[湛江日报 · 第1版：要闻 · 版内 1/7 · 上版 6%]" in out


def test_since_until_scans_requested_day(tmp_path, capsys, site_fetcher, today):
    """--since/--until 指定日：日志日期范围与命中都与该日一致。"""
    day = today.strftime("%Y-%m-%d")
    code = cli.main(
        [*ZJ_ARGS_NO_DAYS, "--since", day, "--until", day, "--no-state", "--data-dir", str(tmp_path)],
        fetcher=site_fetcher(today),
    )
    out, _ = capsys.readouterr()
    assert code == 0
    assert f"日期范围：{day} ~ {day}（1 天）" in out
    assert "新增 3 篇" in out
