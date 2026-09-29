"""统一可执行入口（``newspaper_auto.exe``）与冻结运行路径解析的测试。"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from newspaper_auto import __version__, exe, paths, web

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- 冻结路径
def test_app_dir_uses_project_root_when_not_frozen(monkeypatch, tmp_path):
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    monkeypatch.delenv("NEWSPAPER_APP_DIR", raising=False)
    monkeypatch.setattr(paths, "find_project_root", lambda: tmp_path)
    assert paths.app_dir() == tmp_path


def test_app_dir_uses_executable_dir_when_frozen(monkeypatch, tmp_path):
    fake_exe = tmp_path / "dist" / "NewspaperAuto.exe"
    fake_exe.parent.mkdir(parents=True)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(fake_exe))
    assert paths.app_dir() == fake_exe.parent.resolve()


def test_web_static_dir_resolves_and_contains_modules():
    assert (web.STATIC_DIR / "index.html").is_file()
    assert (web.STATIC_DIR / "store.js").is_file()


def test_web_static_dir_falls_back_to_bundle(monkeypatch, tmp_path):
    """源码目录不存在时（冻结场景）应回退到 sys._MEIPASS 下的打包位置。"""
    bundle = tmp_path / "_meipass"
    static_in_bundle = bundle / "newspaper_auto" / "static"
    static_in_bundle.mkdir(parents=True)
    (static_in_bundle / "index.html").write_text("x", encoding="utf-8")

    monkeypatch.setattr(web, "__file__", str(tmp_path / "nonexistent" / "web.py"))
    monkeypatch.setattr(web.sys, "_MEIPASS", str(bundle), raising=False)
    assert web._static_dir() == static_in_bundle


# ---------------------------------------------------------------- 无窗口版流兜底
def test_ensure_console_streams_restores_none_streams():
    """无窗口版（``console=False``）下 stdout/stderr/stdin 可能是 None。

    回归：任何 ``print`` 都会 ``AttributeError`` 把进程打崩 —— 而双击启动恰恰是
    最不能崩的场景。兜底后必须可写、且不抛异常。
    """
    saved = (sys.stdout, sys.stderr, sys.stdin)
    sys.stdout = sys.stderr = sys.stdin = None  # type: ignore[assignment]
    try:
        exe._ensure_console_streams()
        assert sys.stdout is not None and sys.stderr is not None and sys.stdin is not None
        print("无窗口版也能安全 print")  # 真实断言：这一行不抛异常
        sys.stdout.flush()
    finally:
        sys.stdout, sys.stderr, sys.stdin = saved


def test_ensure_console_streams_is_noop_when_streams_are_ready():
    """控制台版 / 源码运行：流已就绪时必须原样保留，不能换成 devnull 把输出吃掉。"""
    before = (sys.stdout, sys.stderr, sys.stdin)
    exe._ensure_console_streams()
    assert (sys.stdout, sys.stderr, sys.stdin) == before


def test_report_fatal_is_silent_outside_frozen_windows():
    """源码运行与测试环境绝不能弹窗（否则 CI 会挂住）。"""
    exe._report_fatal(RuntimeError("boom"))  # 不抛异常即为通过


def test_main_dispatches_through_the_guard(monkeypatch):
    """``main`` 必须先接好流再分发 —— 否则无窗口版在第一次 print 就崩。"""
    called: list[str] = []
    monkeypatch.setattr(exe, "_ensure_console_streams", lambda: called.append("streams"))
    monkeypatch.setattr(exe, "_dispatch", lambda argv=None: 0)
    assert exe.main(["--version"]) == 0
    assert called == ["streams"]


# ---------------------------------------------------------------- 子命令分发
def test_exe_no_args_opens_dashboard_in_background(monkeypatch):
    """双击 exe（无参数）应直接起看板：后台运行 + 自动打开浏览器。

    这条守护的是一个真实的用户事故：以前无参数只打印帮助，双击后窗口一闪而过，
    用户以为程序坏了；而看板又跑在前台窗口里，关掉窗口服务就没了。

    注意断言的是**看板自己的选项**（不含子命令名）—— 多传一个 "dashboard" 会让
    argparse 直接报「unrecognized arguments」。
    """
    seen: dict[str, object] = {}

    def fake_web(argv=None, *, default_data_dir=None):
        seen["argv"] = list(argv)
        seen["dir"] = default_data_dir
        return 0

    monkeypatch.setattr(exe.web, "main", fake_web)
    assert exe.main([]) == 0
    assert seen["argv"] == ["--open", "--detach"]
    assert seen["dir"] == paths.app_dir()


def test_exe_no_args_arguments_are_accepted_by_the_real_parser():
    """上一条断言的是转发内容；这条确认它真的能被看板的 parser 接受。

    （曾出现「把子命令名也传进去」的 bug：mock 掉的 web.main 让单测察觉不到，
    只有真正解析参数才会报 unrecognized arguments。）
    """
    args = web.build_parser().parse_args(["--open", "--detach"])
    assert args.open is True
    assert args.detach is True


def test_exe_help_and_version(capsys):
    assert exe.main(["--help"]) == 0
    help_text = capsys.readouterr().out
    assert "scan" in help_text and "dashboard" in help_text
    # 后台运行必须能在帮助里看到停止方式，否则用户没法关掉它
    assert "--detach" in help_text and "--stop" in help_text

    assert exe.main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_exe_unknown_command_exits_2(capsys):
    assert exe.main(["frobnicate"]) == 2
    assert "未知子命令" in capsys.readouterr().err


def test_exe_routes_scan_and_dashboard(monkeypatch):
    seen: dict[str, object] = {}

    def fake_scan(argv, default_data_dir=None):
        seen["scan"] = (list(argv), default_data_dir)
        return 0

    def fake_web(argv=None, *, default_data_dir=None):
        seen["web"] = (list(argv), default_data_dir)
        return 7

    monkeypatch.setattr(exe.cli, "main", fake_scan)
    monkeypatch.setattr(exe.web, "main", fake_web)

    assert exe.main(["scan", "--days", "1", "--no-state"]) == 0
    assert seen["scan"][0] == ["--days", "1", "--no-state"]
    assert seen["scan"][1] == paths.app_dir()

    assert exe.main(["dashboard", "--port", "0"]) == 7
    assert seen["web"][0] == ["--port", "0"]
    assert seen["web"][1] == paths.app_dir()


def test_entry_py_is_valid_python():
    compile((ROOT / "packaging" / "entry.py").read_text(encoding="utf-8"), "entry.py", "exec")


def _run_spec() -> dict[str, object]:
    """用桩符号执行 spec，返回捕获到的关键字参数。"""
    spec = ROOT / "packaging" / "NewspaperAuto.spec"
    captured: dict[str, object] = {}

    class _Analysis:
        def __init__(self, scripts, **kwargs):
            captured["scripts"] = scripts
            captured.update(kwargs)
            self.pure = []
            self.scripts = []
            self.binaries = []
            self.datas = []

    class _PYZ:
        def __init__(self, pure):
            pass

    class _EXE:
        def __init__(self, *args, **kwargs):
            captured["exe_name"] = kwargs.get("name")
            captured["exe_console"] = kwargs.get("console")
            captured["exe_icon"] = kwargs.get("icon")

    exec(
        compile(spec.read_text(encoding="utf-8"), str(spec), "exec"),
        {"SPECPATH": str(spec.parent), "Analysis": _Analysis, "PYZ": _PYZ, "EXE": _EXE},
    )
    return captured


def test_pyinstaller_spec_wires_entry_and_static():
    """用桩符号执行 spec，校验入口与静态资源路径。

    注意 PyInstaller 注入的 ``SPECPATH`` 是 spec **所在目录**（不是文件本身）——
    这里按同样语义注入，守护「路径算错一级」这类只在打包时才暴露的错误。
    ``datas`` 里的 ``cacert.pem`` 同样是硬断言：漏了它，系统根库缺锚点的机器
    （Win7）会整家抓不到中国教育报，而症状只表现为「0 版面」，极难定位。
    """
    captured = _run_spec()

    assert captured["scripts"] == [str(ROOT / "packaging" / "entry.py")]
    assert captured["pathex"] == [str(ROOT / "src")]
    assert captured["datas"] == [
        (str(ROOT / "src" / "newspaper_auto" / "static"), "newspaper_auto/static"),
        (str(ROOT / "src" / "newspaper_auto" / "cacert.pem"), "newspaper_auto"),
    ]
    assert (ROOT / "src" / "newspaper_auto" / "static").is_dir()
    assert (ROOT / "src" / "newspaper_auto" / "cacert.pem").is_file()
    assert captured["exe_icon"] == str(ROOT / "src" / "newspaper_auto" / "static" / "favicon.ico")


def test_pyinstaller_spec_builds_exactly_one_windowed_exe():
    """只出一个**无窗口** exe。

    回归：曾经拆成「无窗口版 + 控制台版」两个 exe，包体积翻倍、用户还要猜该双击哪个。
    现在只留无窗口版（双击即用），命令行侧靠 AttachConsole 回显 + scan 自己写日志兜底。
    这里的断言是硬契约：一旦有人把 ``console`` 改成 True，双击就会弹黑窗口。
    """
    captured = _run_spec()

    assert captured["exe_name"] == "NewspaperAuto"
    assert captured["exe_console"] is False, "双击不能弹终端（console 必须为 False）"


# ---------------------------------------------------------------- scan 日志
def test_run_scan_tees_output_into_the_daily_log(monkeypatch, tmp_path):
    """``scan`` 把输出同时写进 ``logs/scan-YYYY-MM-DD.log``。

    这是无窗口版唯一的输出通道：双击或任务计划程序启动时 stdout 会消失，
    没有这份日志就无法回溯「今天扫到了什么」，也正因如此包里不再需要 .cmd 包装。
    """
    monkeypatch.setattr(exe.paths, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(exe.cli, "main", lambda argv, default_data_dir=None: print("命中 3 条") or 0)
    before = (sys.stdout, sys.stderr)

    assert exe._run_scan(["--days", "2"]) == 0

    log = exe.paths.scan_log_file(tmp_path, exe.datetime.date.today().isoformat())
    text = log.read_text(encoding="utf-8")
    assert "=== 开始监测扫描" in text
    assert "命令：NewspaperAuto scan --days 2" in text
    assert "命中 3 条" in text, "cli 的输出必须被同时写进日志"
    assert "=== 扫描结束" in text and "退出码：0" in text
    assert (sys.stdout, sys.stderr) == before, "退出时必须还原标准流"


def test_run_scan_survives_unwritable_log_dir(monkeypatch, tmp_path):
    """日志写不了（只读目录等）不该让扫描失败 —— 退回「只在控制台输出」并继续。"""
    monkeypatch.setattr(exe.paths, "app_dir", lambda: tmp_path)

    def boom(*args, **kwargs):
        raise OSError("只读文件系统")

    monkeypatch.setattr(exe.paths, "scan_log_file", boom)
    monkeypatch.setattr(exe.cli, "main", lambda argv, default_data_dir=None: 3)
    assert exe._run_scan(["--days", "1"]) == 3


class _FailingLog:
    """模拟「打开成功、之后再也写不进去」的日志文件：磁盘满 / 被独占 / 盘被拔出。"""

    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, text: str) -> int:
        self.writes.append(text)
        raise OSError(28, "No space left on device")

    def flush(self) -> None:
        raise OSError(28, "No space left on device")

    def close(self) -> None:
        raise OSError(28, "No space left on device")


def test_tee_keeps_console_output_when_the_log_write_fails():
    """日志是旁路：写日志失败不能从每一次 print 里冒出来。"""
    handle = _FailingLog()
    stream = io.StringIO()
    tee = exe._Tee(stream, handle)

    assert tee.write("命中 3 条") == len("命中 3 条")
    tee.flush()

    assert stream.getvalue() == "命中 3 条", "控制台那侧必须照常拿到内容"
    assert handle.writes == ["命中 3 条"], "日志那侧仍应尝试写入"


def test_run_scan_survives_log_failure_midway(monkeypatch, tmp_path):
    """日志打开成功、运行中写失败（满盘）时，扫描仍要跑完并返回真实退出码。"""
    monkeypatch.setattr(exe.paths, "app_dir", lambda: tmp_path)
    handle = _FailingLog()
    monkeypatch.setattr(exe, "_open_scan_log", lambda: handle)
    monkeypatch.setattr(exe.cli, "main", lambda argv, default_data_dir=None: print("命中 3 条") or 1)
    before = (sys.stdout, sys.stderr)

    assert exe._run_scan(["--days", "2"]) == 1

    assert (sys.stdout, sys.stderr) == before, "退出时必须还原标准流"
    joined = "".join(handle.writes)
    assert "=== 开始监测扫描" in joined
    assert "命中 3 条" in joined, "cli 的输出仍应尝试写进日志"
    assert "=== 扫描结束" in joined and "退出码：1" in joined


def test_scan_log_paths_match_the_shell_scripts():
    """入口写的日志与 run_scan.sh / run_scan.ps1 必须同名同位置，否则两套路径会分叉。"""
    assert paths.scan_log_file(Path("/tmp/x"), "2026-09-27") == Path("/tmp/x/logs/scan-2026-09-27.log")
