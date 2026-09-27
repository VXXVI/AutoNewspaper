@echo off
chcp 65001 >nul
rem ==============================================================================
rem dashboard.cmd - Windows「打开本地看板」启动器（薄封装）
rem
rem 端口回退逻辑唯一实现于 scripts\launch_dashboard.py；本文件只负责双击场景：
rem 解析 Python 解释器、cd 项目根、把参数原样透传过去。
rem 也可用环境变量 NEWSPAPER_PORT 或 --port 指定端口；看板默认**后台**运行（--detach），
rem 关掉本窗口不会停止它（停止：launch_dashboard.py --stop）。
rem ==============================================================================
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

rem 解释器解析顺序：NEWSPAPER_PYTHON -> .venv\Scripts\python.exe -> PATH 上的 python
set "PY=%NEWSPAPER_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" scripts\launch_dashboard.py --detach %*
echo.
echo 看板已在后台运行，按任意键关闭本窗口（不会停止看板；停止请用 --stop）。
pause >nul
