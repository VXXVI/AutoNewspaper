"""子进程派生的「不弹控制台窗口」不变量。

背景（v0.8.0 的真实反馈）
------------------------
Windows 便携版 exe 改成无控制台窗口（``console=False``）之后，用户一扫描就看到
成百上千个黑窗口弹出来。原因不是扫描器本身，而是**抓取层的 curl 兜底**：

- 无窗口版 exe 没有控制台，它起的看板服务进程用 ``DETACHED_PROCESS`` 也没有；
- 这种进程派生**控制台子系统**子进程时，Windows 会为每个子进程新建一个控制台窗口；
- curl 兜底是**按请求**调用的（默认 8 并发、一次扫描几百个页面）。

所以不变量是：**运行时每一处 ``subprocess`` 派生都必须显式抑制控制台窗口**。
下面的 AST 扫描守的就是这条 —— 以后新增派生点忘了带参数，测试会直接失败，
而不是等用户看到满屏黑窗口。
"""

from __future__ import annotations

import ast
from pathlib import Path

from newspaper_auto import http, procutil

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "newspaper_auto"

# 允许的写法：直接给 creationflags，或调用 procutil 里的两个封装
_ALLOWED = ("creationflags", "hidden_console", "detached_console")
# 只关心「派生进程」的方法；DEVNULL / TimeoutExpired 之类是常量与异常，不算
_SPAWN_METHODS = ("Popen", "run", "call", "check_call", "check_output")


def _spawn_call_sites() -> list[tuple[str, int, str, str]]:
    """遍历运行时代码，找出所有 ``subprocess.<派生方法>(...)`` 调用及其源码片段。"""
    sites: list[tuple[str, int, str, str]] = []
    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in _SPAWN_METHODS:
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "subprocess":
                continue
            end = node.end_lineno or node.lineno
            sites.append((path.name, node.lineno, func.attr, "\n".join(lines[node.lineno - 1 : end])))
    return sites


def test_every_subprocess_spawn_suppresses_the_console_window():
    offenders = [
        f"{name}:{lineno} subprocess.{attr}"
        for name, lineno, attr, snippet in _spawn_call_sites()
        if not any(marker in snippet for marker in _ALLOWED)
    ]
    assert not offenders, (
        "以下 subprocess 派生没有抑制控制台窗口：在无窗口版 exe 里每派生一次就会弹一个黑窗口。\n"
        "请给调用加上 procutil.hidden_console() / procutil.detached_console()。\n  " + "\n  ".join(offenders)
    )


def test_the_scan_finds_the_known_spawn_sites():
    """守住扫描本身：遍历写错时不应「零命中 = 通过」。"""
    names = {name for name, _, _, _ in _spawn_call_sites()}
    assert {"http.py", "web_daemon.py"} <= names


# ---------------------------------------------------------------- 参数构造
def test_windows_flags_are_used_when_the_constant_exists(monkeypatch):
    monkeypatch.setattr(procutil.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(procutil.subprocess, "DETACHED_PROCESS", 0x00000008, raising=False)
    assert procutil.hidden_console() == {"creationflags": 0x08000000}
    assert procutil.detached_console() == {"creationflags": 0x08000000 | 0x00000008}


def test_posix_falls_back_to_session_detachment(monkeypatch):
    """POSIX 上本来就没有控制台窗口，长驻进程改用「新建会话」脱离。"""
    monkeypatch.delattr(procutil.subprocess, "CREATE_NO_WINDOW", raising=False)
    assert procutil.hidden_console() == {}
    assert procutil.detached_console() == {"start_new_session": True}


# ---------------------------------------------------------------- 回归
def test_curl_fallback_hides_its_console_window(monkeypatch):
    """回归：curl 兜底曾不带 creationflags —— 无窗口版扫一次弹几百个黑窗口。"""
    seen: dict = {}
    proc = type("P", (), {"stdout": b"x" * 500 + b"\n@@HTTP:200"})()

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen.update(kwargs)
        return proc

    monkeypatch.setattr(http.subprocess, "run", fake_run)
    monkeypatch.setattr(http.procutil.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    assert http._curl_once("http://x", 5)[0] == 200
    assert seen["creationflags"] == 0x08000000, "curl 子进程必须隐藏控制台窗口"
    assert seen["cmd"][0] == "curl"
