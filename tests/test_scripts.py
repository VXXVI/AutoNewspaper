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


def test_build_windows_zip_copies_explicit_user_files():
    """回归：组装 zip 曾用 ``Copy-Item packaging\\windows\\*`` 通配，把构建脚本
    ``installer.iss`` 一起装进了发给最终用户的 windows-x64.zip。现改为显式列举。"""
    text = (ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")
    assert "packaging\\windows\\*" not in text, "打包 zip 不应再用通配拷贝 packaging\\windows\\"

    zip_block = text.split("组装 zip", 1)[1]
    assert "README.txt" in zip_block, "zip 组装段应显式包含 README.txt"


def test_build_windows_produces_one_exe_and_smokes_the_health_version():
    """只构建一个 exe，且冒烟测试断言 ``/api/health`` 报告了正确版本。

    回归一：曾经出两个 exe（无窗口 + 控制台），包体积翻倍还要用户猜双击哪个；
    回归二：冒烟测试曾经只看 ``--version`` 的退出码 —— 无窗口版 stdout 是 None、
    打不出字也会显示「绿」。现在改看 /api/health 里的 version，比字符串输出更硬。
    """
    text = (ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n")

    assert "NewspaperAuto-CLI" not in normalized, "不应再构建控制台版 exe"
    # 回归：v0.9.0 里漏删了指向已删变量 $cliExePath 的拷贝行，PowerShell 直到打包阶段
    # 才报「Cannot bind argument to parameter 'LiteralPath' because it is null」。
    assert "$cliExePath" not in normalized, "不应再残留指向已删除的控制台版 exe 的变量"
    assert "NEWSPAPERAUTO_CONSOLE" not in normalized, "不应再靠环境变量切换 console 产物"
    assert normalized.count("python -m PyInstaller") == 1, "只应调用一次 PyInstaller"

    smoke = normalized.split("冒烟测试", 1)[1]
    assert "api/health" in smoke and "static/store.js" in smoke
    assert "[regex]::Escape($packageVersion)" in smoke, (
        "必须断言 /api/health 报出的**包内**版本号（不带 tag 的 v 前缀）"
    )
    # 回归：CI 传的是 tag 名（v0.9.0），直接拿去比 "0.9.0" 会永远匹配不上，v0.9.0 因此挂过
    assert "$packageVersion = $Version -replace '^[vV]', ''" in normalized, "比较前必须去掉 tag 的 v 前缀"
    assert "$sourceVersion -ne $packageVersion" in normalized, "tag 与源码版本必须一致"
    # 无窗口版打不出字：不能再有任何「跑 --version 看输出」的调用
    assert "& $exePath --version" not in smoke and "& $cliExePath --version" not in smoke


def test_windows_installer_ships_exactly_one_exe():
    """安装包只装一个 exe，且快捷方式指向它（不再有 .cmd 启动器与「扫描一次」）。"""
    assert not (ROOT / "packaging" / "windows" / "Run-Scan.cmd").exists(), "启动器应已删除"
    assert not (ROOT / "packaging" / "windows" / "Start-Dashboard.cmd").exists()

    iss = (ROOT / "packaging" / "windows" / "installer.iss").read_text(encoding="utf-8")
    assert r"{#SourceDir}\NewspaperAuto.exe" in iss
    assert "NewspaperAuto-CLI" not in iss, "安装包不应再引用控制台版"
    assert ".cmd" not in iss, "安装包不应再引用 .cmd 启动器"
    # 「打开看板」指向唯一的 exe：双击零控制台窗口
    assert r'Name: "{group}\打开看板"; Filename: "{app}\NewspaperAuto.exe"' in iss
    assert "SetupIconFile={#IconFile}" in iss, "安装包自身的品牌图标不能丢"

    readme = (ROOT / "packaging" / "windows" / "README.txt").read_text(encoding="utf-8")
    assert "NewspaperAuto.exe" in readme
    assert "NewspaperAuto-CLI" not in readme, "使用说明不应再提控制台版"
    # 无窗口版的输出只有日志这一条通道，说明里必须写清楚
    assert "logs\\scan-" in readme or "logs/scan-" in readme
    assert "任务计划程序" in readme, "说明里要给出不依赖 .cmd 的定时任务写法"


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


# ---------------------------------------------------------------- 可执行位
# macOS 上 `scripts/*.command` 由 Finder 交给 Terminal **直接 exec**，`docs/scheduling.md`
# 又让用户 `./scripts/run_scan.sh`；两条路都要求文件在 git 里带可执行位（100755）。
# Windows 的 core.fileMode=false 不会丢掉索引里记录的模式，所以断言「索引模式」跨平台成立。
_EXECUTABLE_ENTRY_SCRIPTS = (
    "scripts/scan.command",
    "scripts/dashboard.command",
    "scripts/run_scan.sh",
)


def _git_index_modes(paths: tuple[str, ...]) -> dict[str, str] | None:
    """读取这些文件在 git 索引里的模式；不是 git 工作区（或没有 git）时返回 None。"""
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        return None
    proc = subprocess.run(["git", "ls-files", "-s", "--", *paths], cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    modes: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        meta, name = line.split("\t", 1)
        modes[name] = meta.split()[0]
    return modes


def test_macos_entry_scripts_are_executable_in_git():
    """`.command` 双击入口与 `./scripts/run_scan.sh` 在 git 里必须是 100755。

    回归：这三个文件曾以 100644 入库，Finder 双击 `.command` 直接 permission denied；
    `docs/scheduling.md` 里写的 `./scripts/run_scan.sh ...` 同样报错。
    """
    modes = _git_index_modes(_EXECUTABLE_ENTRY_SCRIPTS)
    if modes is None:
        pytest.skip("需要 git 工作区才能检查索引里的可执行位")
    for rel in _EXECUTABLE_ENTRY_SCRIPTS:
        assert modes.get(rel) == "100755", (
            f"{rel} 在 git 索引里不是 100755（当前 {modes.get(rel)}），双击或直接执行会失败"
        )
