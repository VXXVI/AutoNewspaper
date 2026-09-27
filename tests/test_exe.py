"""统一可执行入口（``newspaper_auto.exe``）与冻结运行路径解析的测试。"""

from __future__ import annotations

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


def _run_spec(monkeypatch, *, console: bool) -> dict[str, object]:
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

    if console:
        monkeypatch.setenv("NEWSPAPERAUTO_CONSOLE", "1")
    else:
        monkeypatch.delenv("NEWSPAPERAUTO_CONSOLE", raising=False)

    exec(
        compile(spec.read_text(encoding="utf-8"), str(spec), "exec"),
        {"SPECPATH": str(spec.parent), "Analysis": _Analysis, "PYZ": _PYZ, "EXE": _EXE},
    )
    return captured


def test_pyinstaller_spec_wires_entry_and_static(monkeypatch):
    """用桩符号执行 spec，校验入口与静态资源路径。

    注意 PyInstaller 注入的 ``SPECPATH`` 是 spec **所在目录**（不是文件本身）——
    这里按同样语义注入，守护「路径算错一级」这类只在打包时才暴露的错误。
    """
    captured = _run_spec(monkeypatch, console=False)

    assert captured["scripts"] == [str(ROOT / "packaging" / "entry.py")]
    assert captured["pathex"] == [str(ROOT / "src")]
    assert captured["datas"] == [(str(ROOT / "src" / "newspaper_auto" / "static"), "newspaper_auto/static")]
    assert (ROOT / "src" / "newspaper_auto" / "static").is_dir()
    assert captured["exe_name"] == "NewspaperAuto"
    assert captured["exe_console"] is False, "默认产物必须是无窗口版，否则双击会弹终端"


def test_pyinstaller_spec_builds_a_console_variant(monkeypatch):
    """同一个 spec 靠 NEWSPAPERAUTO_CONSOLE 出控制台版，供全部命令行用法使用。

    回归：用户要求「便携版别弹终端」，但 CLI 不能因此丢掉输出能力 ——
    两个 exe 必须来自同一份 Analysis 配置，避免静态资源/图标两处漂移。
    """
    windowed = _run_spec(monkeypatch, console=False)
    console = _run_spec(monkeypatch, console=True)

    assert console["exe_name"] == "NewspaperAuto-CLI"
    assert console["exe_console"] is True
    # 除名字与 console 外，其余（入口 / 数据文件 / 图标）必须逐项相同
    for key in ("scripts", "pathex", "datas", "exe_icon"):
        assert windowed[key] == console[key], f"两个 exe 的 {key} 必须一致"
    assert console["exe_icon"] == str(ROOT / "src" / "newspaper_auto" / "static" / "favicon.ico")
