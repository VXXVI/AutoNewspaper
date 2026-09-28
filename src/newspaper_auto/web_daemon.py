"""看板的「后台运行」支持：脱离控制台启动、状态查询与停止。

为什么需要它
------------
看板原先跑在**前台控制台**里（``dashboard --open`` 会一直阻塞在 ``serve_forever``）。
用户扫完一次就把黑窗口关掉 —— 那正是**服务进程本体**，于是浏览器里再点「开始扫描」
只会看到「无法连接本地服务」。这里把服务进程与控制台解耦，并把 ``host/port/pid``
落到 ``state/dashboard.json``：

- 启动器可据此**复用已在运行的实例**（也顺带解决「端口变了、旧标签页连不上」）；
- ``--stop`` / ``--status`` 有了可靠的落点；
- 关掉启动窗口不再影响看板运行。

设计约定
--------
- 状态文件由**子进程**（真正在监听的那个）在绑定端口后写入，父进程只读；
- 就绪判定 = 状态文件里的 ``pid`` 等于自己启动的子进程 pid **且** ``/api/health`` 可达，
  避免把上一次运行残留的文件误判成「已就绪」；
- 探测本机服务时**显式绕过 HTTP 代理**：企业代理环境下 ``127.0.0.1`` 也可能被塞进代理。
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import fsutil, paths, procutil

READY_TIMEOUT = 25.0
POLL_INTERVAL = 0.25
STOP_TIMEOUT = 6.0

# 探测本机服务时忽略 HTTP_PROXY / HTTPS_PROXY，避免被企业代理劫持
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ---------------------------------------------------------------- 状态文件
def read_state(data_dir: str | os.PathLike[str]) -> dict | None:
    """读取 ``state/dashboard.json``；缺失或损坏一律返回 ``None``。"""
    path = paths.dashboard_file(Path(data_dir))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        host = str(raw["host"])
        port = int(raw["port"])
        pid = int(raw["pid"])
    except (KeyError, TypeError, ValueError):
        return None
    if not host or not (0 < port < 65536):
        return None
    try:
        ppid = int(raw.get("ppid", 0) or 0)
    except (TypeError, ValueError):
        ppid = 0
    return {
        "host": host,
        "port": port,
        "pid": pid,
        "ppid": ppid,
        "started_at": str(raw.get("started_at", "")),
    }


def write_state(data_dir: str | os.PathLike[str], host: str, port: int) -> None:
    """写入运行状态（由正在监听的进程调用）。

    走共享的 :func:`newspaper_auto.fsutil.write_text`（唯一临时名 + ``fsync`` + 原子替换）：
    此前用的是固定的 ``dashboard.json.tmp``，两个看板进程几乎同时启动时会抢同一个临时文件，
    后一个 ``os.replace`` 直接 ``FileNotFoundError``，启动器把它当「启动失败」报给用户。
    """
    path = paths.dashboard_file(Path(data_dir))
    payload = {
        "host": host,
        "port": int(port),
        "pid": os.getpid(),
        # 冻结运行时，父进程是 PyInstaller onefile 的引导进程（进程名同样是 NewspaperAuto.exe），
        # 它持有 exe 文件句柄；--stop 需要连它一起等，否则安装/卸载会认为 exe 仍被占用。
        "ppid": os.getppid() if getattr(sys, "frozen", False) else 0,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    fsutil.write_text(path, json.dumps(payload, ensure_ascii=False))


def clear_state(data_dir: str | os.PathLike[str], pid: int | None = None) -> None:
    """删除运行状态文件。

    传入 ``pid`` 时只删除「属于该进程」的状态，避免前台/后台实例互相清掉对方的记录。
    """
    path = paths.dashboard_file(Path(data_dir))
    if pid is not None:
        current = read_state(data_dir)
        if current is not None and current["pid"] != pid:
            return
    with contextlib.suppress(OSError):
        path.unlink()


# ---------------------------------------------------------------- 探测
def pid_alive(pid: int) -> bool:
    """进程是否仍在（跨平台；只做存在性判断，不发送信号）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def url_of(state: dict) -> str:
    return f"http://{state['host']}:{state['port']}/"


def health(host: str, port: int, timeout: float = 1.5) -> dict | None:
    """请求 ``/api/health``；成功返回解析后的 JSON，否则 ``None``。"""
    url = f"http://{host}:{port}/api/health"
    try:
        with _NO_PROXY.open(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    # 端口上可能是别的东西：确认是本看板
    return data if isinstance(data, dict) and "version" in data else None


def status(data_dir: str | os.PathLike[str], timeout: float = 1.5) -> dict:
    """后台看板状态。

    ``running`` 同时要求「状态文件里的 pid 还活着」与「``/api/health`` 可达」，
    因此残留文件不会被误报成运行中。
    """
    state = read_state(data_dir)
    if state is None:
        return {"running": False, "stale": False, "host": None, "port": None, "pid": None, "url": None}
    alive = pid_alive(state["pid"])
    healthy = health(state["host"], state["port"], timeout=timeout)
    return {
        "running": bool(alive and healthy),
        "stale": bool(not alive or not healthy),
        "host": state["host"],
        "port": state["port"],
        "pid": state["pid"],
        "url": url_of(state),
        "version": (healthy or {}).get("version", ""),
    }


# ---------------------------------------------------------------- 停止
def _wait_for_parent_exit(ppid: int, timeout: float) -> bool:
    """等待 PyInstaller onefile 的引导进程退出（它持有 exe 文件句柄）。

    冻结运行时，正在服务的应用进程的**父进程**是引导进程，可执行文件路径与进程名都与本程序
    相同。它在子进程结束后才会清理并退出；若不等它，安装/卸载程序会认为 ``NewspaperAuto.exe``
    仍被占用而中止（Inno Setup 会报 "unable to automatically close all applications"）。

    只等待、不杀：父进程本就该自行退出，而 pid 可能被复用，贸然 kill 有误伤无关进程的风险。
    """
    if ppid <= 0 or ppid == os.getpid():
        return True
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(ppid):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def stop(data_dir: str | os.PathLike[str], timeout: float = STOP_TIMEOUT) -> dict:
    """停止后台看板；先 TERM，超时再 KILL。返回处理结果。"""
    state = read_state(data_dir)
    if state is None:
        return {"stopped": False, "reason": "没有正在运行的后台看板"}

    pid = state["pid"]
    if not pid_alive(pid):
        clear_state(data_dir)
        return {"stopped": False, "reason": "看板进程已不在（已清理残留状态）"}

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:  # 权限不足等
        return {"stopped": False, "reason": f"发送停止信号失败：{exc}"}

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            clear_state(data_dir)
            _wait_for_parent_exit(state.get("ppid", 0), timeout)
            return {"stopped": True, "pid": pid}
        time.sleep(POLL_INTERVAL)

    # Windows 没有 SIGKILL：退回 SIGTERM（os.kill 在 Windows 上都会走 TerminateProcess）
    with contextlib.suppress(OSError):
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    clear_state(data_dir)
    _wait_for_parent_exit(state.get("ppid", 0), timeout)
    return {"stopped": True, "pid": pid, "forced": True}


# ---------------------------------------------------------------- 启动
def _child_argv(host: str, port: int, data_dir: Path, scan: bool) -> list[str]:
    """构造子进程命令行：冻结时是 exe 自身，源码时是 ``python -m``。

    子进程就是**普通的前台服务**（它不知道自己在后台）——控制台解耦由 Popen 的
    ``DETACHED_PROCESS`` / ``start_new_session`` 完成，因此不需要额外的内部开关。
    """
    common = ["--host", host, "--port", str(port), "--data-dir", str(data_dir)]
    if scan:
        common.append("--scan")
    if getattr(sys, "frozen", False):
        return [sys.executable, "dashboard", *common]
    return [sys.executable, "-m", "newspaper_auto.web", *common]


"""PyInstaller 用这些环境变量把「解包目录」传给自己的子进程。

冻结运行时我们又会启动一个 exe（后台看板）：新进程若继承它们，就会**复用父进程的解包目录**，
而父进程退出时会清理该目录，子进程的静态资源随之消失（表现为首页 500「静态资源读取失败」）。
所以启动子进程前必须清掉，让它自己重新解包。
"""

_PYI_ENV_PREFIXES = ("_MEI", "_PYI_")


def _child_env() -> dict[str, str]:
    """子进程环境：清掉 PyInstaller 内部变量，强制日志输出用 UTF-8。

    清 _MEI*/_PYI_*：否则会复用父进程的解包目录，父进程退出后静态资源消失。
    强制 PYTHONIOENCODING=utf-8：子进程的 stdout/stderr 是日志文件，若不指定就按自身
    locale 解码（Windows 上常是 cp1252），打印中文日志会 UnicodeEncodeError 直接崩，
    而这发生在我们拿到日志之前（连崩溃原因都看不到）。源码运行时再补 PYTHONPATH，
    保证 ``-m newspaper_auto.web`` 一定能 import。
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith(_PYI_ENV_PREFIXES)}
    env["PYTHONIOENCODING"] = "utf-8"
    if getattr(sys, "frozen", False):
        return env  # 冻结运行时子进程就是 exe 自己，自带包，无需 PYTHONPATH
    import newspaper_auto  # 只为拿到包的实际位置

    package_parent = str(Path(newspaper_auto.__file__).resolve().parent.parent)
    existing = env.get("PYTHONPATH", "")
    if package_parent not in existing.split(os.pathsep):
        env["PYTHONPATH"] = package_parent + (os.pathsep + existing if existing else "")
    return env


def spawn(data_dir: str | os.PathLike[str], host: str, port: int, *, scan: bool = False) -> subprocess.Popen:
    """以「脱离控制台」的方式启动看板服务进程（stdout/stderr 落日志文件）。"""
    data_dir = Path(data_dir)
    log_path = paths.dashboard_log_file(data_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115  交给子进程持有

    # cwd 有意继承父进程；「项目根」另由 NEWSPAPER_APP_DIR 显式传给子进程（见 paths.app_dir），
    # 因此父进程即使不是从项目根启动的，子进程也能找到正确的 papers.custom.json。
    try:
        return subprocess.Popen(
            _child_argv(host, port, data_dir, scan),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            env=_child_env(),
            # 脱离控制台：Windows 见 procutil.detached_console，POSIX 走 start_new_session。
            # 写在调用点上而不是拼 kwargs —— 方便 tests/test_procutil.py 用 AST 守住
            # 「每一处派生都抑制控制台窗口」这条不变量。
            **procutil.detached_console(),
        )
    finally:
        log_file.close()


def wait_ready(
    data_dir: str | os.PathLike[str],
    since: float,
    timeout: float = READY_TIMEOUT,
    pid: int | None = None,
) -> dict | None:
    """等待「本次启动写下的」状态文件出现，且 ``/api/health`` 可达。

    为什么不用 ``pid`` 判断归属：Windows 上 venv 的 ``python.exe`` 只是启动器，
    它会再 exec 一个真正的解释器，于是 ``Popen.pid`` 与子进程里的 ``os.getpid()``
    **本来就不相等**。因此改用「状态文件的修改时间不早于 ``since``」来判断。
    """
    path = paths.dashboard_file(Path(data_dir))
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = read_state(data_dir)
        if state is not None and (pid is None or state["pid"] == pid):
            try:
                fresh = path.stat().st_mtime >= since - 1.0
            except OSError:
                fresh = False
            if fresh and health(state["host"], state["port"]):
                return state
        time.sleep(POLL_INTERVAL)
    return None


def port_free(host: str, port: int) -> bool:
    """端口是否可绑定（``0`` 表示让系统分配，永远算空闲）。

    先探测再启动，能避免「端口被占用 → 白等一个就绪超时 → 才回退」的糟糕体验。
    """
    if port == 0:
        return True
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Windows 的 SO_REUSEADDR 允许「重复绑定」同一端口（与 POSIX 语义不同），
        # 用它探测会永远返回「空闲」；因此 Windows 上必须用最朴素的 bind 来判断占用。
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def launch(
    data_dir: str | os.PathLike[str],
    host: str,
    port: int,
    *,
    scan: bool = False,
    fallback_port: bool = True,
) -> dict:
    """确保后台看板在运行，返回状态字典。

    默认端口被占用之类的情况下，会再用系统分配的空闲端口重试一次
    （真正的端口以子进程写入的状态文件为准），彻底避免「启动器开了一个端口、
    页面却在另一个端口」这类困惑。
    """
    existing = status(data_dir)
    if existing["running"]:
        existing["reused"] = True
        return existing

    candidates = [(host, port)]
    if fallback_port and port != 0:
        candidates.append((host, 0))

    last: dict = {"running": False, "reason": "启动失败"}
    for attempt_host, attempt_port in candidates:
        if not port_free(attempt_host, attempt_port):
            last = {"running": False, "reason": f"端口 {attempt_port} 已被占用，改用系统分配的空闲端口"}
            continue
        clear_state(data_dir)  # 清掉可能的残留，避免误判就绪
        since = time.time()
        proc = spawn(data_dir, attempt_host, attempt_port, scan=scan)
        state = wait_ready(data_dir, since)
        if state is not None:
            result = status(data_dir)  # 用统一的状态结构返回（含 running/url/version）
            if result["running"]:
                result["reused"] = False
                return result
        last = {"running": False, "reason": _tail_log(data_dir), "pid": proc.pid}
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                os.kill(proc.pid, signal.SIGTERM)
    return last


def _tail_log(data_dir: Path, lines: int = 6) -> str:
    """取后台日志最后几行，用于把启动失败原因回显给用户。"""
    try:
        text = paths.dashboard_log_file(Path(data_dir)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "（没有日志，可能是端口被占用或程序被拦截）"
    tail = [line for line in text.splitlines() if line.strip()][-lines:]
    return " / ".join(tail) if tail else "（日志为空）"
