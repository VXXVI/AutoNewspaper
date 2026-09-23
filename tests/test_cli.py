"""CLI 端到端测试（注入 fake fetcher，不访问网络）。"""

from __future__ import annotations

import json

import pytest

from newspaper_auto import cli, config

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


def test_json_and_csv_are_mutually_exclusive(capsys, site_fetcher, today):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "--csv"], fetcher=site_fetcher(today))
    assert exc.value.code == 2


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


def test_select_papers_unknown_name_does_not_silently_empty():
    assert cli.select_papers("不存在的报纸") == list(config.PAPERS)
    assert [p["name"] for p in cli.select_papers("湛江日报")] == ["湛江日报"]
    assert cli.select_papers("") == list(config.PAPERS)


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
