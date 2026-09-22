"""CLI 端到端测试（注入 fake fetcher，不访问网络）。"""

from __future__ import annotations

import json

import pytest

from newspaper_auto import cli, config

ZJ_ARGS = ["--only", "湛江日报", "--days", "1", "--kw", "广东海洋大学"]
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


def test_entry_failure_is_reported_not_crashed(tmp_path, capsys, today):
    code = cli.main(
        ["--only", "光明日报", "--days", "1", "--data-dir", str(tmp_path)], fetcher=lambda url: (404, "")
    )
    out, _err = capsys.readouterr()
    assert code == 0
    assert "入口 HTTP 404" in out


def test_select_papers_unknown_name_does_not_silently_empty():
    assert cli.select_papers("不存在的报纸") == list(config.PAPERS)
    assert [p["name"] for p in cli.select_papers("湛江日报")] == ["湛江日报"]
    assert cli.select_papers("") == list(config.PAPERS)
