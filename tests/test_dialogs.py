"""原生文件夹选择窗口的平台适配测试。

原则：**绝不在测试机上弹真窗口**。所有平台分支都用假的 ``subprocess.run`` /
``shutil.which`` 驱动；只有 Windows 的 ctypes 分支在非 Windows 上「不可用要报可用性
错误」这一条，才需要碰真代码（在 Windows 上用 skipif 挡掉，否则会真弹窗卡住 CI）。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from newspaper_auto import dialogs


class _Proc:
    """``subprocess.run`` 的返回值最小替身。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------- 平台分发
def test_pick_directory_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(dialogs, "_pick_windows", lambda initial, prompt: "W")
    monkeypatch.setattr(dialogs, "_pick_macos", lambda initial, prompt: "M")
    monkeypatch.setattr(dialogs, "_pick_linux", lambda initial, prompt: "L")

    monkeypatch.setattr(dialogs.sys, "platform", "win32")
    assert dialogs.pick_directory() == "W"
    monkeypatch.setattr(dialogs.sys, "platform", "darwin")
    assert dialogs.pick_directory() == "M"
    monkeypatch.setattr(dialogs.sys, "platform", "linux")
    assert dialogs.pick_directory() == "L"


def test_pick_directory_propagates_initial(monkeypatch):
    seen: dict[str, object] = {}

    def fake(initial, prompt):
        seen["initial"] = initial
        return None

    monkeypatch.setattr(dialogs, "_pick_linux", fake)
    monkeypatch.setattr(dialogs.sys, "platform", "linux")
    dialogs.pick_directory("/tmp/start")
    assert seen["initial"] == "/tmp/start"


# ---------------------------------------------------------------- macOS
@pytest.fixture
def macos(monkeypatch):
    """把 macOS 分支接到假的 osascript 上。"""
    monkeypatch.setattr(
        dialogs.shutil, "which", lambda name: "/usr/bin/osascript" if name == "osascript" else None
    )

    def install(proc: _Proc) -> dict:
        seen: dict = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = argv
            seen.update(kwargs)
            return proc

        monkeypatch.setattr(dialogs.subprocess, "run", fake_run)
        return seen

    return install


def test_macos_picker_uses_finders_choose_folder(macos):
    seen = macos(_Proc(0, "/tmp/chosen\n"))

    assert dialogs._pick_macos("/tmp/start", "选择保存位置") == "/tmp/chosen"
    argv = seen["argv"]
    assert argv[0] == "/usr/bin/osascript" and argv[1] == "-e"
    script = argv[2]
    assert "choose folder" in script, "必须用 Finder 的选择文件夹面板"
    assert "选择保存位置" in script
    assert 'POSIX file "/tmp/start"' in script, "已有路径要作为窗口的预选位置"
    assert seen["timeout"] == dialogs.DIALOG_TIMEOUT_SECONDS


def test_macos_picker_omits_default_location_without_initial(macos):
    seen = macos(_Proc(0, "/tmp/chosen\n"))

    dialogs._pick_macos(None, "选择")

    assert "default location" not in seen["argv"][2]


def test_macos_picker_treats_user_cancel_as_none(macos):
    """用户取消时 osascript 退出码 1 且 stderr 带 -128 —— 这不是错误。"""
    macos(_Proc(1, "", "execution error: User canceled. (-128)"))

    assert dialogs._pick_macos(None, "选择") is None


def test_macos_picker_reports_a_real_failure(macos):
    """没有「自动控制」权限（-1743）等真实失败要报出来，不能被当成取消吞掉。"""
    macos(_Proc(1, "", "execution error: Not authorized to send Apple events. (-1743)"))

    with pytest.raises(dialogs.DirectoryPickerUnavailable):
        dialogs._pick_macos(None, "选择")


def test_macos_picker_requires_osascript(monkeypatch):
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: None)

    with pytest.raises(dialogs.DirectoryPickerUnavailable):
        dialogs._pick_macos(None, "选择")


def test_applescript_literal_escapes_quotes_and_backslashes():
    assert dialogs._applescript_literal('a"b') == '"a\\"b"'
    assert dialogs._applescript_literal("a\\b") == '"a\\\\b"'


# ---------------------------------------------------------------- Linux
def _linux_which(present: str | None):
    return lambda name: f"/usr/bin/{name}" if name == present else None


def test_linux_picker_prefers_zenity(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen.update(kwargs)
        return _Proc(0, "/home/me/导出\n")

    monkeypatch.setattr(dialogs.shutil, "which", _linux_which("zenity"))
    monkeypatch.setattr(dialogs.subprocess, "run", fake_run)

    assert dialogs._pick_linux("/home/me/导出", "选择") == "/home/me/导出"
    assert seen["argv"][:4] == ["/usr/bin/zenity", "--file-selection", "--directory", "--title"]
    # zenity 用「以分隔符结尾」表示预选目录本身
    assert seen["argv"][-2:] == ["--filename", "/home/me/导出/"]


def test_linux_picker_falls_back_to_kdialog(monkeypatch):
    seen: dict = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return _Proc(0, "/home/me\n")

    monkeypatch.setattr(dialogs.shutil, "which", _linux_which("kdialog"))
    monkeypatch.setattr(dialogs.subprocess, "run", fake_run)

    assert dialogs._pick_linux("/home/me", "选择") == "/home/me"
    assert seen["argv"][:2] == ["/usr/bin/kdialog", "--getexistingdirectory"]


def test_linux_picker_cancel_is_not_an_error(monkeypatch):
    """zenity/kdialog 取消时退出码为 1（stderr 为空）。"""
    monkeypatch.setattr(dialogs.shutil, "which", _linux_which("zenity"))
    monkeypatch.setattr(dialogs.subprocess, "run", lambda argv, **kwargs: _Proc(1))

    assert dialogs._pick_linux(None, "选择") is None


def test_linux_picker_without_any_dialog_is_unavailable(monkeypatch):
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: None)

    with pytest.raises(dialogs.DirectoryPickerUnavailable):
        dialogs._pick_linux(None, "选择")


# ---------------------------------------------------------------- Windows
@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="Windows 上这一条会真的弹出系统窗口",
)
def test_windows_picker_is_unavailable_without_windll():
    """非 Windows 解释器没有 ``ctypes.windll``：要报「此设备不支持」而不是崩掉。"""
    with pytest.raises(dialogs.DirectoryPickerUnavailable):
        dialogs._pick_windows(None, "选择")


# ---------------------------------------------------------------- 派生进程
def test_dialog_process_hides_the_console_window(monkeypatch, macos):
    """无窗口版 exe 派生子进程会闪黑窗口 —— 选择器也不能例外。"""
    seen = macos(_Proc(0, "/tmp/chosen\n"))
    monkeypatch.setattr(dialogs.procutil.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    dialogs._pick_macos(None, "选择")

    assert seen["creationflags"] == 0x08000000


def test_dialog_launch_failure_becomes_unavailable(monkeypatch):
    def boom(argv, **kwargs):
        raise FileNotFoundError("osascript 不见了")

    monkeypatch.setattr(dialogs.subprocess, "run", boom)

    with pytest.raises(dialogs.DirectoryPickerUnavailable):
        dialogs._run_dialog(["osascript"], where="Finder")


def test_dialog_timeout_becomes_unavailable(monkeypatch):
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, dialogs.DIALOG_TIMEOUT_SECONDS)

    monkeypatch.setattr(dialogs.subprocess, "run", timeout)

    with pytest.raises(dialogs.DirectoryPickerUnavailable):
        dialogs._run_dialog(["osascript"], where="Finder")


def test_empty_selection_is_treated_as_cancel(monkeypatch):
    """退出码 0 但没输出（部分后端如此）：按取消处理，不能回一个空路径。"""
    monkeypatch.setattr(dialogs.subprocess, "run", lambda argv, **kwargs: _Proc(0, "  \n"))

    assert dialogs._run_dialog(["zenity"], where="zenity") is None
