"""看板后台运行支持（``newspaper_auto.web_daemon``）的测试。

覆盖三件最容易出错的事：
1. 状态文件的读写/清理（含「不是自己的就别删」）；
2. 就绪判定 —— 必须同时满足「状态文件是本次启动写的」与「``/api/health`` 可达」
   （Windows 上 venv 的 ``python.exe`` 是启动器，会把 Popen.pid 与实际进程 pid 分开，
   曾经因此把「启动成功」误判成失败）；
3. 端口占用时快速回退，而不是白等一个就绪超时。
"""

from __future__ import annotations

import os
import signal
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from newspaper_auto import paths, web_daemon

NONE_STATUS = {"running": False, "stale": False, "host": None, "port": None, "pid": None, "url": None}


# ---------------------------------------------------------------- 状态文件
def test_read_state_returns_none_when_missing(tmp_path):
    assert web_daemon.read_state(tmp_path) is None


@pytest.mark.parametrize(
    "payload",
    [
        "不是 JSON",
        "[]",
        "null",
        '{"host": "127.0.0.1"}',  # 缺字段
        '{"host": "", "port": 8788, "pid": 1}',  # host 为空
        '{"host": "127.0.0.1", "port": 0, "pid": 1}',  # 端口非法
        '{"host": "127.0.0.1", "port": 70000, "pid": 1}',  # 端口越界
        '{"host": "127.0.0.1", "port": "x", "pid": 1}',  # 端口不是数字
    ],
)
def test_read_state_tolerates_corrupt_or_invalid(tmp_path, payload):
    path = paths.dashboard_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    assert web_daemon.read_state(tmp_path) is None


def test_write_then_read_roundtrip(tmp_path):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    state = web_daemon.read_state(tmp_path)
    assert state is not None
    assert state["host"] == "127.0.0.1"
    assert state["port"] == 8788
    assert state["pid"] == os.getpid()
    assert state["started_at"]
    assert web_daemon.url_of(state) == "http://127.0.0.1:8788/"
    # 原子写不应留下临时文件
    assert not list(paths.dashboard_file(tmp_path).parent.glob("*.tmp"))


def test_write_state_records_parent_pid_only_when_frozen(tmp_path, monkeypatch):
    """冻结运行时父进程是 PyInstaller 引导进程（同样叫 NewspaperAuto.exe）且持有 exe 句柄；
    记录它的 pid，--stop 才能等到句柄释放（否则安装/升级会认为 exe 仍被占用）。"""
    monkeypatch.setattr(web_daemon.sys, "frozen", True, raising=False)
    monkeypatch.setattr(web_daemon.os, "getppid", lambda: 4242)
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    assert web_daemon.read_state(tmp_path)["ppid"] == 4242

    monkeypatch.delattr(web_daemon.sys, "frozen", raising=False)
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    assert web_daemon.read_state(tmp_path)["ppid"] == 0, "源码运行时父进程可能是 shell，不能记它"


def test_read_state_defaults_missing_ppid(tmp_path):
    path = paths.dashboard_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"host": "127.0.0.1", "port": 8788, "pid": 1}', encoding="utf-8")
    state = web_daemon.read_state(tmp_path)
    assert state is not None and state["ppid"] == 0


def test_write_state_creates_parent_dirs(tmp_path):
    nested = tmp_path / "deep" / "dir"
    web_daemon.write_state(nested, "127.0.0.1", 8788)
    assert paths.dashboard_file(nested).is_file()


def test_clear_state_only_removes_own_record(tmp_path):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    web_daemon.clear_state(tmp_path, pid=os.getpid() + 12345)
    assert web_daemon.read_state(tmp_path) is not None, "别人的状态不该被删"
    web_daemon.clear_state(tmp_path, pid=os.getpid())
    assert web_daemon.read_state(tmp_path) is None
    web_daemon.clear_state(tmp_path)  # 幂等：再删一次不报错


# ---------------------------------------------------------------- 进程存活
def test_pid_alive_true_for_current_process():
    assert web_daemon.pid_alive(os.getpid()) is True


@pytest.mark.parametrize("pid", [0, -1])
def test_pid_alive_false_for_invalid_pid(pid):
    assert web_daemon.pid_alive(pid) is False


# ---------------------------------------------------------------- status
def test_status_not_running_without_state(tmp_path):
    assert web_daemon.status(tmp_path) == NONE_STATUS


def test_status_running_requires_alive_pid_and_healthy_service(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: True)
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: {"ok": True, "version": "1.2.3"})
    result = web_daemon.status(tmp_path)
    assert result["running"] is True
    assert result["stale"] is False
    assert result["url"] == "http://127.0.0.1:8788/"
    assert result["version"] == "1.2.3"


def test_status_stale_when_pid_is_gone(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: False)
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: {"ok": True, "version": "1"})
    result = web_daemon.status(tmp_path)
    assert result["running"] is False
    assert result["stale"] is True, "残留状态文件不能算「运行中」"


def test_status_stale_when_service_unreachable(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: True)
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: None)
    result = web_daemon.status(tmp_path)
    assert result["running"] is False
    assert result["stale"] is True


# ---------------------------------------------------------------- stop
def test_stop_without_state(tmp_path):
    result = web_daemon.stop(tmp_path)
    assert result["stopped"] is False
    assert "没有正在运行" in result["reason"]


def test_stop_clears_state_when_process_already_gone(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: False)
    result = web_daemon.stop(tmp_path)
    assert result["stopped"] is False
    assert "已不在" in result["reason"]
    assert web_daemon.read_state(tmp_path) is None


def test_stop_sends_sigterm_once_and_clears_state(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    signals: list[int] = []
    alive = {"value": True}

    def fake_kill(pid, sig):
        signals.append(sig)
        alive["value"] = False

    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: alive["value"])
    monkeypatch.setattr(web_daemon.os, "kill", fake_kill)
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)

    result = web_daemon.stop(tmp_path)
    assert result["stopped"] is True
    assert result["pid"] == os.getpid()
    assert signals == [signal.SIGTERM]
    assert web_daemon.read_state(tmp_path) is None


def test_stop_forces_kill_when_process_ignores_sigterm(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    signals: list[int] = []
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: True)  # 永不退出
    monkeypatch.setattr(web_daemon.os, "kill", lambda pid, sig: signals.append(sig))
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)

    result = web_daemon.stop(tmp_path, timeout=0.02)
    assert result["stopped"] is True
    assert result.get("forced") is True
    assert signals == [signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)]


def test_stop_reports_signal_failure(tmp_path, monkeypatch):
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: True)

    def boom(pid, sig):
        raise OSError("拒绝访问")

    monkeypatch.setattr(web_daemon.os, "kill", boom)
    result = web_daemon.stop(tmp_path)
    assert result["stopped"] is False
    assert "发送停止信号失败" in result["reason"]


def test_stop_waits_for_pyinstaller_bootloader(tmp_path, monkeypatch):
    """stop 必须等到引导进程也退出：它持有 exe 句柄，不等就会导致安装/卸载认为文件被占用。"""
    path = paths.dashboard_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"host": "127.0.0.1", "port": 8788, "pid": 4242, "ppid": 4243}', encoding="utf-8")

    main_alive = {"value": True}
    boot_checks = {"count": 0}

    def fake_alive(pid):
        if pid == 4242:
            return main_alive["value"]
        boot_checks["count"] += 1
        return boot_checks["count"] < 3  # 引导进程在几次轮询后自行退出

    monkeypatch.setattr(web_daemon, "pid_alive", fake_alive)
    monkeypatch.setattr(web_daemon.os, "kill", lambda pid, sig: main_alive.update(value=False))
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)

    result = web_daemon.stop(tmp_path, timeout=2.0)
    assert result["stopped"] is True
    assert boot_checks["count"] >= 3, "应当轮询等待过引导进程"
    assert web_daemon.read_state(tmp_path) is None


def test_wait_for_parent_exit_skips_missing_or_self(monkeypatch):
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: True)
    assert web_daemon._wait_for_parent_exit(0, 0.01) is True
    assert web_daemon._wait_for_parent_exit(os.getpid(), 0.01) is True


def test_wait_for_parent_exit_returns_false_on_timeout(monkeypatch):
    monkeypatch.setattr(web_daemon, "pid_alive", lambda pid: True)
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)
    assert web_daemon._wait_for_parent_exit(123456, 0.02) is False


# ---------------------------------------------------------------- 端口探测
def test_port_free_detects_busy_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy = sock.getsockname()[1]
        assert web_daemon.port_free("127.0.0.1", busy) is False
    assert web_daemon.port_free("127.0.0.1", busy) is True, "释放后应恢复为空闲"


def test_port_zero_is_always_free():
    assert web_daemon.port_free("127.0.0.1", 0) is True


# ---------------------------------------------------------------- health（真起一个本地服务）
class _JsonHandler(BaseHTTPRequestHandler):
    payload = b'{"ok": true, "version": "9.9.9"}'

    def do_GET(self):
        body = self.payload
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


def _serve(handler) -> tuple[socketserver.TCPServer, int]:
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_health_returns_payload_from_our_service():
    server, port = _serve(_JsonHandler)
    try:
        assert web_daemon.health("127.0.0.1", port) == {"ok": True, "version": "9.9.9"}
    finally:
        server.shutdown()
        server.server_close()


def test_health_rejects_foreign_service_on_the_port():
    """端口上可能是别的东西：没有 version 字段就不认，避免误判「看板在运行」。"""

    class Foreign(_JsonHandler):
        payload = b'{"hello": "world"}'

    server, port = _serve(Foreign)
    try:
        assert web_daemon.health("127.0.0.1", port) is None
    finally:
        server.shutdown()
        server.server_close()


def test_health_returns_none_when_nothing_listens():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        free = sock.getsockname()[1]
    assert web_daemon.health("127.0.0.1", free, timeout=0.5) is None


# ---------------------------------------------------------------- wait_ready
def test_wait_ready_times_out_without_state(tmp_path, monkeypatch):
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)
    assert web_daemon.wait_ready(tmp_path, since=time.time(), timeout=0.05) is None


def test_wait_ready_accepts_fresh_state_with_health(tmp_path, monkeypatch):
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: {"ok": True, "version": "1"})
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    state = web_daemon.wait_ready(tmp_path, since=time.time() - 5, timeout=0.5)
    assert state is not None
    assert state["port"] == 8788


def test_wait_ready_ignores_stale_state_written_before_launch(tmp_path, monkeypatch):
    """上一次运行残留的状态文件不能算就绪（否则会把「启动失败」读成成功）。"""
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: {"ok": True, "version": "1"})
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    assert web_daemon.wait_ready(tmp_path, since=time.time() + 60, timeout=0.05) is None


def test_wait_ready_can_require_exact_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(web_daemon, "health", lambda *a, **k: {"ok": True, "version": "1"})
    monkeypatch.setattr(web_daemon, "POLL_INTERVAL", 0.001)
    web_daemon.write_state(tmp_path, "127.0.0.1", 8788)
    assert web_daemon.wait_ready(tmp_path, since=time.time() - 5, timeout=0.05, pid=os.getpid() + 1) is None
    assert web_daemon.wait_ready(tmp_path, since=time.time() - 5, timeout=0.5, pid=os.getpid()) is not None


# ---------------------------------------------------------------- launch
def test_launch_reuses_running_instance_without_spawning(tmp_path, monkeypatch):
    running = {
        "running": True,
        "stale": False,
        "host": "127.0.0.1",
        "port": 8788,
        "pid": 4242,
        "url": "http://127.0.0.1:8788/",
        "version": "1.0",
    }
    monkeypatch.setattr(web_daemon, "status", lambda *a, **k: dict(running))
    spawned: list[int] = []
    monkeypatch.setattr(web_daemon, "spawn", lambda *a, **k: spawned.append(1))

    result = web_daemon.launch(tmp_path, "127.0.0.1", 8788)
    assert result["running"] is True
    assert result["reused"] is True
    assert spawned == [], "已在运行就不该再起进程"


def test_launch_skips_busy_port_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(web_daemon, "port_free", lambda host, port: port != 8788)
    spawned: list[int] = []
    calls = {"n": 0}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_spawn(data_dir, host, port, *, scan=False):
        spawned.append(port)
        return FakeProc()

    def fake_status(data_dir, timeout=1.5):
        calls["n"] += 1
        if calls["n"] == 1:  # launch 开头的「是否已在运行」
            return dict(NONE_STATUS)
        return {
            "running": True,
            "stale": False,
            "host": "127.0.0.1",
            "port": 58009,
            "pid": 4242,
            "url": "http://127.0.0.1:58009/",
            "version": "1.0",
        }

    monkeypatch.setattr(web_daemon, "spawn", fake_spawn)
    monkeypatch.setattr(web_daemon, "status", fake_status)
    monkeypatch.setattr(
        web_daemon,
        "wait_ready",
        lambda data_dir, since, timeout=web_daemon.READY_TIMEOUT, pid=None: {
            "host": "127.0.0.1",
            "port": 58009,
            "pid": 4242,
            "started_at": "",
        },
    )

    result = web_daemon.launch(tmp_path, "127.0.0.1", 8788)
    assert spawned == [0], "被占用的端口应被跳过，直接改用系统分配"
    assert result["running"] is True
    assert result["reused"] is False
    assert result["port"] == 58009


def test_launch_reports_reason_when_every_attempt_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(web_daemon, "status", lambda *a, **k: dict(NONE_STATUS))
    monkeypatch.setattr(web_daemon, "port_free", lambda *a, **k: True)
    monkeypatch.setattr(web_daemon, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(web_daemon, "_tail_log", lambda data_dir, lines=6: "端口 8788 已被占用")

    class DeadProc:
        pid = 11

        def poll(self):
            return 1  # 已退出，不必再 kill

    monkeypatch.setattr(web_daemon, "spawn", lambda *a, **k: DeadProc())
    result = web_daemon.launch(tmp_path, "127.0.0.1", 8788)
    assert result["running"] is False
    assert result["reason"] == "端口 8788 已被占用"


def test_launch_does_not_fall_back_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(web_daemon, "status", lambda *a, **k: dict(NONE_STATUS))
    monkeypatch.setattr(web_daemon, "port_free", lambda *a, **k: True)
    monkeypatch.setattr(web_daemon, "wait_ready", lambda *a, **k: None)
    monkeypatch.setattr(web_daemon, "_tail_log", lambda data_dir, lines=6: "boom")
    spawned: list[int] = []

    class DeadProc:
        pid = 12

        def poll(self):
            return 1

    monkeypatch.setattr(web_daemon, "spawn", lambda d, h, p, **k: (spawned.append(p), DeadProc())[1])
    web_daemon.launch(tmp_path, "127.0.0.1", 8788, fallback_port=False)
    assert spawned == [8788]


# ---------------------------------------------------------------- 子进程命令行/环境
def test_child_argv_source_mode_uses_module(monkeypatch, tmp_path):
    monkeypatch.delattr(web_daemon.sys, "frozen", raising=False)
    argv = web_daemon._child_argv("127.0.0.1", 8788, tmp_path, True)
    assert argv[1:] == [
        "-m",
        "newspaper_auto.web",
        "--host",
        "127.0.0.1",
        "--port",
        "8788",
        "--data-dir",
        str(tmp_path),
        "--scan",
    ]


def test_child_argv_frozen_mode_uses_exe_subcommand(monkeypatch, tmp_path):
    monkeypatch.setattr(web_daemon.sys, "frozen", True, raising=False)
    monkeypatch.setattr(web_daemon.sys, "executable", str(tmp_path / "NewspaperAuto.exe"))
    argv = web_daemon._child_argv("127.0.0.1", 8788, tmp_path, False)
    assert argv[0].endswith("NewspaperAuto.exe")
    assert argv[1] == "dashboard"
    assert "--scan" not in argv


def test_child_env_adds_package_parent_in_source_mode(monkeypatch):
    monkeypatch.delattr(web_daemon.sys, "frozen", raising=False)
    monkeypatch.setenv("PYTHONPATH", "")
    import newspaper_auto

    package_parent = str(Path(newspaper_auto.__file__).resolve().parent.parent)
    env = web_daemon._child_env()
    assert env is not None
    assert package_parent in env["PYTHONPATH"].split(os.pathsep)


def test_child_env_keeps_existing_pythonpath(monkeypatch):
    monkeypatch.delattr(web_daemon.sys, "frozen", raising=False)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(["/existing/one", "/existing/two"]))
    env = web_daemon._child_env()
    assert env is not None
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert "/existing/one" in parts and "/existing/two" in parts


def test_child_env_strips_pyinstaller_vars_when_frozen(monkeypatch):
    """冻结运行时必须清掉 _MEI*/_PYI_*：否则子进程会复用父进程的解包目录，
    父进程一退出，子进程的静态资源就没了（首页 500）。"""
    monkeypatch.setattr(web_daemon.sys, "frozen", True, raising=False)
    monkeypatch.setenv("_MEIPASS2", "/tmp/mei-kept-by-parent")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("_MEI123456", "x")
    monkeypatch.setenv("KEEP_ME", "yes")

    env = web_daemon._child_env()
    assert "_MEIPASS2" not in env
    assert "_PYI_PARENT_PROCESS_LEVEL" not in env
    assert "_MEI123456" not in env
    assert env["KEEP_ME"] == "yes"


def test_child_env_strips_pyinstaller_vars_in_source_mode(monkeypatch):
    monkeypatch.delattr(web_daemon.sys, "frozen", raising=False)
    monkeypatch.setenv("_MEIPASS2", "/tmp/mei")
    env = web_daemon._child_env()
    assert "_MEIPASS2" not in env
    assert "PYTHONPATH" in env


# ---------------------------------------------------------------- 真起进程（端到端）
def test_launch_and_stop_a_real_background_server(tmp_path):
    """真起一个后台子进程 —— 这是唯一能覆盖「脱离控制台 + 状态文件归属」的测试。

    刻意用 ``--port 0``：不与任何固定端口冲突，跨平台稳定。
    """
    state = web_daemon.launch(tmp_path, "127.0.0.1", 0)
    try:
        assert state["running"] is True, f"后台启动失败：{state.get('reason')}"
        assert state["reused"] is False
        assert state["port"] > 0
        assert web_daemon.status(tmp_path)["running"] is True
        # 日志确实写到了数据目录里（后台运行的可观测性）
        log = paths.dashboard_log_file(tmp_path)
        assert log.is_file() and "看板已启动" in log.read_text(encoding="utf-8", errors="replace")
        # 复用：第二次 launch 不再起新进程
        again = web_daemon.launch(tmp_path, "127.0.0.1", 0)
        assert again["reused"] is True
        assert again["pid"] == state["pid"]
    finally:
        web_daemon.stop(tmp_path)

    assert web_daemon.status(tmp_path)["running"] is False
    assert web_daemon.read_state(tmp_path) is None
