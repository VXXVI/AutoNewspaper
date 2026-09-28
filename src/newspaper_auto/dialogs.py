"""在看板**所在设备**上弹出系统原生的文件夹选择窗口。

为什么需要这个模块
------------------
看板导出的文件是**服务端进程**写盘的，所以「浏览…」必须弹**服务端那台机器**的
窗口 —— 浏览器侧的 ``<input type="file" webkitdirectory>`` / File System Access API
只能拿到文件名或句柄，拿不到可以回传给服务端的绝对路径。两者都试过的结论：
只有原生窗口能给出真正可写的那条路径。

各平台实现（全部零第三方依赖）：

- Windows：``shell32!SHBrowseForFolderW``（Vista/Win7 起可用，且不弹控制台窗口，
  这对无窗口版 exe 是硬要求）；
- macOS：``osascript`` 的 ``choose folder``，即 Finder 的面板；
- Linux：``zenity`` 或 ``kdialog``。

三条不变量
----------
1. **缺 GUI 不是错误**：看板可能跑在 VPS、SSH 会话或没有桌面的进程里。这时抛
   :class:`DirectoryPickerUnavailable`，由 HTTP 层翻译成「此设备弹不出窗口，请手动
   填写路径」（501），而不是 500 —— 后者会让用户以为导出坏了。
2. **取消不是错误**：用户点了「取消」返回 ``None``。
3. **窗口只在看板那台机器上弹**：与服务端写出文件的位置严格一致，不会出现「在
   我这台机器选了路径、却写到另一台机器」的误导。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from typing import Any

from . import procutil

PROMPT = "选择保存监测结果的文件夹"
# 原生窗口是人机交互：用户可能要想很久，所以只做一个「线程不会永久挂住」的兜底。
DIALOG_TIMEOUT_SECONDS = 900


class DirectoryPickerUnavailable(RuntimeError):
    """当前设备/会话没有可用的原生文件夹选择窗口（例如看板跑在 VPS 上）。"""


def pick_directory(initial: str | None = None, *, prompt: str = PROMPT) -> str | None:
    """弹窗选文件夹：返回用户选中的路径；取消返回 ``None``。

    无法在当前环境弹窗时抛 :class:`DirectoryPickerUnavailable`。
    ``initial`` 只是「打开时预选的位置」，可以是尚不存在的目录。
    """
    if sys.platform.startswith("win"):
        return _pick_windows(initial, prompt)
    if sys.platform == "darwin":
        return _pick_macos(initial, prompt)
    return _pick_linux(initial, prompt)


# ---------------------------------------------------------------- 子进程类平台
def _run_dialog(
    argv: list[str],
    *,
    where: str,
    cancelled_on: tuple[int, ...] = (),
) -> str | None:
    """跑一个「会阻塞到用户操作完」的选择器进程，把 stdout 当作选中路径。

    ``cancelled_on`` 列出「退出码 → 用户取消」的约定（zenity/kdialog 都是 1）；
    其余非零退出码一律翻译成 :class:`DirectoryPickerUnavailable`。
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=DIALOG_TIMEOUT_SECONDS,
            **procutil.hidden_console(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DirectoryPickerUnavailable(f"{where} 选择窗口无法启动：{exc}") from exc
    if proc.returncode != 0:
        message = (proc.stderr or "").strip()
        if proc.returncode in cancelled_on or "-128" in message or "cancel" in message.lower():
            return None
        raise DirectoryPickerUnavailable(f"{where} 选择窗口失败：{message or proc.returncode}")
    chosen = (proc.stdout or "").strip()
    return chosen or None


def _applescript_literal(value: str) -> str:
    """把 Python 字符串安全地嵌进 AppleScript 双引号字面量。

    路径来自用户输入，直接拼接会破坏脚本（引号/反斜杠）。这里只做字面量转义；
    换行等控制字符已在 HTTP 层被拒。
    """
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _pick_macos(initial: str | None, prompt: str) -> str | None:
    osascript = shutil.which("osascript")
    if not osascript:
        raise DirectoryPickerUnavailable("没有找到 osascript")
    script = "POSIX path of (choose folder with prompt " + _applescript_literal(prompt)
    if initial:
        script += " default location (POSIX file " + _applescript_literal(initial) + ")"
    script += ")"
    # 用户取消时 osascript 退出码为 1 且 stderr 含 "-128"/"User canceled"，
    # 由 _run_dialog 的消息判定分支识别，故这里不按退出码判取消。
    return _run_dialog([osascript, "-e", script], where="Finder")


def _pick_linux(initial: str | None, prompt: str) -> str | None:
    zenity = shutil.which("zenity")
    if zenity:
        argv = [zenity, "--file-selection", "--directory", "--title", prompt]
        if initial:
            # zenity 用「以分隔符结尾」表示预选目录本身，而不是它的父目录。
            argv += ["--filename", os.path.join(initial, "")]
        return _run_dialog(argv, where="zenity", cancelled_on=(1,))
    kdialog = shutil.which("kdialog")
    if kdialog:
        start = initial or os.path.expanduser("~")
        argv = [kdialog, "--getexistingdirectory", start, "--title", prompt]
        return _run_dialog(argv, where="kdialog", cancelled_on=(1,))
    raise DirectoryPickerUnavailable("没有找到 zenity / kdialog")


# ---------------------------------------------------------------- Windows
def _pick_windows(initial: str | None, prompt: str) -> str | None:
    """``SHBrowseForFolderW``：Vista 起就有、且不需要额外依赖的文件夹选择窗口。

    刻意不用更现代的 ``IFileDialog``：那是 COM 接口，需要手写 vtable 偏移，
    在无法本地验证的平台上出错会直接崩掉看板进程；这个 API 只有一个结构体，
    失败最坏也只是返回 ``NULL``。
    """
    import ctypes

    if not hasattr(ctypes, "windll"):  # 非 Windows 解释器（测试会走到这里）
        raise DirectoryPickerUnavailable("当前 Python 没有 Windows 图形接口（windll 不可用）")
    from ctypes import wintypes

    BIF_RETURNONLYFSDIRS = 0x00000001
    BIF_EDITBOX = 0x00000010
    BIF_NEWDIALOGSTYLE = 0x00000040
    BFFM_INITIALIZED = 1
    BFFM_SETSELECTIONW = 0x0400 + 103  # WM_USER + 103
    COINIT_APARTMENTTHREADED = 0x2

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    user32 = ctypes.windll.user32

    class BROWSEINFO(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    try:
        # 64 位下指针/句柄必须显式声明类型，否则 ctypes 默认按 c_int 截断返回值。
        shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFO)]
        shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
        shell32.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wintypes.LPWSTR]
        shell32.SHGetPathFromIDListW.restype = wintypes.BOOL
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole32.CoTaskMemFree.restype = None
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = ctypes.c_int
        ole32.CoUninitialize.argtypes = []
        ole32.CoUninitialize.restype = None
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        user32.SendMessageW.restype = ctypes.c_void_p
    except (AttributeError, ValueError) as exc:  # 极老的系统缺少某个导出
        raise DirectoryPickerUnavailable(f"系统缺少文件夹选择窗口所需的接口：{exc}") from exc

    selected = initial if initial and os.path.isdir(initial) else None
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, wintypes.HWND, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p
    )

    def on_message(hwnd: Any, message: int, _wparam: Any, _lparam: Any) -> int:
        # 只在窗口刚建好时把「当前值」预选上，其余消息一律不处理。
        if message == BFFM_INITIALIZED and selected:
            user32.SendMessageW(
                hwnd,
                BFFM_SETSELECTIONW,
                1,
                ctypes.cast(ctypes.c_wchar_p(selected), ctypes.c_void_p),
            )
        return 0

    # BIF_NEWDIALOGSTYLE（可缩放、可新建文件夹）要求调用线程先初始化 COM。
    # 已经是别的单元模型时 CoInitializeEx 返回 RPC_E_CHANGED_MODE，忽略即可。
    com_initialized = False
    with contextlib.suppress(OSError):
        com_initialized = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED) in (0, 1)

    callback = callback_type(on_message)  # 必须活到窗口关闭，故保持局部引用
    try:
        display = ctypes.create_unicode_buffer(260)
        info = BROWSEINFO()
        info.hwndOwner = user32.GetForegroundWindow()
        info.pidlRoot = None
        info.pszDisplayName = display
        info.lpszTitle = prompt
        info.ulFlags = BIF_RETURNONLYFSDIRS | BIF_EDITBOX | BIF_NEWDIALOGSTYLE
        info.lpfn = ctypes.cast(callback, ctypes.c_void_p)
        info.lParam = 0
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(info))
        if not pidl:
            return None  # 用户取消
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            if not shell32.SHGetPathFromIDListW(pidl, buffer):
                return None
            return buffer.value or None
        finally:
            ole32.CoTaskMemFree(pidl)
    finally:
        del callback
        if com_initialized:
            ole32.CoUninitialize()
