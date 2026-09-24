@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

rem 解释器解析顺序：NEWSPAPER_PYTHON -> .venv\Scripts\python.exe -> PATH 上的 python
set "PY=%NEWSPAPER_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

rem 端口：NEWSPAPER_PORT 优先，默认 8788；被占用时回退到系统分配的端口
set "PORT=%NEWSPAPER_PORT%"
if not defined PORT set "PORT=8788"

echo 正在启动本地看板（默认 http://127.0.0.1:%PORT%；可在页面一键触发真实扫描，会写 state/ 与 hits/）...
"%PY%" dashboard.py --port %PORT% --open
if errorlevel 1 (
  echo.
  echo 端口 %PORT% 不可用，改用系统自动分配的空闲端口重试...
  "%PY%" dashboard.py --port 0 --open
)
echo.
echo 看板已退出，按任意键关闭本窗口...
pause >nul
