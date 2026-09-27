"""启动脚本（run_scan.sh / run_scan.ps1）的**参数透传**契约测试。

``run_scan.sh`` / ``run_scan.ps1`` 刻意保持很薄：解析解释器、cd 项目根、补默认
（``--no-state``，未显式给日期时补 ``--days 2``），其余参数原样转发给
``daily_scan.py``。它们**不再维护 CLI 选项白名单**（历史上白名单滞后于 cli.py 的
新选项，用户在定时任务里用新特性会直接报「未知选项」），所以这里的契约是：

1. **文本契约**：两个脚本都直接转发 ``"$@"`` / ``@($args)``，且不再出现
   「未知选项」这类白名单拒绝逻辑；
2. **行为契约**：把 ``run_scan.sh`` 复制到临时目录、用桩解释器驱动，断言
   ``--since/--until/--export-*`` 等选项**逐字**透传，默认只补一次 ``--days``，
   且任何未登记过的选项也照样转发。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 行为测试依赖 POSIX shell（Linux/macOS）；Windows 上的 Git Bash 无法可靠执行这些脚本，
# 但「文本契约」与「PowerShell 守卫」在所有平台都可运行，必须在 Windows 上照样生效。
_HAS_POSIX_BASH = os.name == "posix" and shutil.which("bash") is not None
_requires_posix_bash = pytest.mark.skipif(not _HAS_POSIX_BASH, reason="需要 POSIX bash 环境")


def test_scan_scripts_forward_arguments_verbatim_without_whitelist():
    """两个脚本都原样转发参数，不再枚举 CLI 选项 / 拒绝未知选项。"""
    sh_text = (ROOT / "scripts/run_scan.sh").read_text(encoding="utf-8")
    ps_text = (ROOT / "scripts/run_scan.ps1").read_text(encoding="utf-8")

    assert 'CMD+=("$@")' in sh_text, "run_scan.sh 应把参数原样加入命令"
    assert "@($args)" in ps_text, "run_scan.ps1 应把参数原样转发"
    for label, text in (("run_scan.sh", sh_text), ("run_scan.ps1", ps_text)):
        assert "未知选项：" not in text, f"{label} 不应再有选项白名单拒绝逻辑"


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
def test_run_scan_sh_forwards_unknown_options_verbatim(tmp_path):
    """未登记过的选项也必须原样转发（白名单已删除）。"""
    project, stub = _make_isolated_project(tmp_path)
    proc = _run_script(project, stub, "--totally-new-option", "value", "--kw", "人工智能")
    assert proc.returncode == 0, proc.stderr
    command = _logged_command(project)
    assert "--totally-new-option value" in command
    assert "--kw 人工智能" in command
    assert "--days 2" in command
