"""启动脚本（run_scan.sh / run_scan.ps1）与 CLI 选项的同步契约测试。

历史缺陷：脚本用严格白名单匹配参数，但白名单滞后于 ``cli.py`` 的新选项
（``--since`` / ``--until`` / ``--export-dir`` …），用户在定时任务里使用新特性会
直接 ``die``。这里用两把锁守住：

1. **文本契约**：``cli.build_parser()`` 里的每个长选项都必须出现在两个脚本里，
   新增 CLI 选项却忘了登记脚本时测试变红（含 PowerShell，CI 可在任意平台跑文本断言）。
2. **行为契约**：把 ``run_scan.sh`` 复制到临时目录、用桩解释器驱动，断言它确实把
   ``--since/--until/--export-*`` 透传给 daily_scan.py，且不会同时注入互斥的 ``--days``。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from newspaper_auto import cli

ROOT = Path(__file__).resolve().parents[1]

# 行为测试依赖 POSIX shell（Linux/macOS）；Windows 上的 Git Bash 无法可靠执行这些脚本，
# 但「文本契约」与「PowerShell 守卫」在所有平台都可运行，必须在 Windows 上照样生效。
_HAS_POSIX_BASH = os.name == "posix" and shutil.which("bash") is not None
_requires_posix_bash = pytest.mark.skipif(not _HAS_POSIX_BASH, reason="需要 POSIX bash 环境")


def _cli_long_options() -> set[str]:
    options: set[str] = set()
    for action in cli.build_parser()._actions:
        options.update(opt for opt in action.option_strings if opt.startswith("--"))
    return options


def test_scan_scripts_register_every_cli_long_option():
    """新增 CLI 选项必须同步登记到两个启动脚本，否则定时任务无法使用。"""
    options = _cli_long_options()
    for rel in ("scripts/run_scan.sh", "scripts/run_scan.ps1"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        missing = sorted(opt for opt in options if opt not in text)
        assert not missing, f"{rel} 未登记 CLI 选项：{missing}"


def test_run_scan_ps1_defaults_to_two_days_and_mentions_range_example():
    text = (ROOT / "scripts/run_scan.ps1").read_text(encoding="utf-8")
    assert "else { '2' }" in text, "PowerShell 默认天数应与 CLI / run_scan.sh 一致为 2"
    assert "--since 2026-09-20 --until 2026-09-25" in text


@pytest.mark.parametrize(
    "rel",
    [
        "scripts/run_scan.ps1",
        "scripts/install_task_scheduler.ps1",
        "scripts/build_windows.ps1",
    ],
)
def test_windows_powershell_scripts_are_utf8_bom_with_crlf(rel):
    """与 .gitattributes / CI 的约定一致：PowerShell 脚本必须 BOM + CRLF。"""
    data = (ROOT / rel).read_bytes()
    assert data[:3] == b"\xef\xbb\xbf", f"{rel} 缺少 UTF-8 BOM（PowerShell 5.1 会乱码）"
    assert data.count(b"\n") == data.count(b"\r\n"), f"{rel} 存在裸 LF 行"


def _make_isolated_project(tmp_path: Path) -> tuple[Path, Path]:
    """复制 run_scan.sh 到 tmp 项目，并造一个回显 argv 的桩 Python。"""
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "run_scan.sh", scripts / "run_scan.sh")

    stub = tmp_path / "stub_python"
    stub.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n', encoding="utf-8")
    stub.chmod(0o755)
    return tmp_path, stub


def _run_script(tmp_path: Path, stub: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, NEWSPAPER_PYTHON=str(stub))
    return subprocess.run(
        ["bash", str(tmp_path / "scripts" / "run_scan.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _logged_command(tmp_path: Path) -> str:
    log = next((tmp_path / "logs").glob("scan-*.log"))
    lines = log.read_text(encoding="utf-8").splitlines()
    matches = [line for line in lines if line.startswith("命令：")]
    assert matches, f"日志里应有「命令：」行：{lines!r}"
    return matches[-1]


@_requires_posix_bash
def test_run_scan_sh_forwards_range_and_export_options(tmp_path):
    project, stub = _make_isolated_project(tmp_path)
    proc = _run_script(
        project,
        stub,
        "--since",
        "2026-09-20",
        "--until",
        "2026-09-22",
        "--export-dir",
        "./exports",
        "--export-format",
        "json",
        "--workers",
        "4",
    )
    assert proc.returncode == 0, proc.stderr

    command = _logged_command(project)
    assert "--since 2026-09-20" in command
    assert "--until 2026-09-22" in command
    assert "--export-dir ./exports" in command
    assert "--export-format json" in command
    assert "--workers 4" in command
    assert "--no-state" in command
    # 给了显式区间就不能再注入 --days（CLI 里两者互斥）
    assert "--days" not in command


@_requires_posix_bash
def test_run_scan_sh_injects_default_days_when_no_range(tmp_path):
    project, stub = _make_isolated_project(tmp_path)
    proc = _run_script(project, stub, "--workers", "4")
    assert proc.returncode == 0, proc.stderr
    command = _logged_command(project)
    assert "--days 2" in command
    assert "--since" not in command


@_requires_posix_bash
def test_run_scan_sh_rejects_days_with_explicit_range(tmp_path):
    project, stub = _make_isolated_project(tmp_path)
    proc = _run_script(project, stub, "--days", "3", "--since", "2026-09-20")
    assert proc.returncode == 2
    assert "不能同时使用" in proc.stderr
    assert not (project / "logs").exists()
