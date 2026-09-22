"""数据目录解析的单元测试。"""

from __future__ import annotations

from newspaper_auto import paths


def test_cli_value_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSPAPER_DATA_DIR", str(tmp_path / "env"))
    assert paths.resolve_data_dir(str(tmp_path / "cli")) == (tmp_path / "cli").resolve()


def test_env_when_no_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWSPAPER_DATA_DIR", str(tmp_path / "env"))
    assert paths.resolve_data_dir(None) == (tmp_path / "env").resolve()


def test_explicit_default_when_no_cli_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWSPAPER_DATA_DIR", raising=False)
    assert paths.resolve_data_dir(None, str(tmp_path)) == tmp_path.resolve()


def test_find_project_root_walks_up(tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert paths.find_project_root(nested) == tmp_path.resolve()


def test_find_project_root_falls_back_to_start(tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert paths.find_project_root(nested) == nested.resolve()


def test_file_helpers(tmp_path):
    assert paths.state_file(tmp_path) == tmp_path / "state" / "seen.json"
    assert paths.hits_dir(tmp_path) == tmp_path / "hits"
    assert paths.archive_file(tmp_path) == tmp_path / "hits" / "archive.json"
    assert paths.index_file(tmp_path) == tmp_path / "hits" / "index.html"
