@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8

rem 解释器解析顺序：NEWSPAPER_PYTHON -> .venv\Scripts\python.exe -> PATH 上的 python
set "PY=%NEWSPAPER_PYTHON%"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

echo === AutoNewspaper 扫描（今天 + 昨天，无状态）===
echo 解释器：%PY%
echo.
"%PY%" daily_scan.py --days 2 --no-state
if errorlevel 1 (
  echo.
  echo ✗ 扫描失败，请查看上面的日志。
)
echo.
echo 按任意键关闭本窗口...
pause >nul
