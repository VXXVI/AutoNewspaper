@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
set NEWSPAPER_PYTHON=%NEWSPAPER_PYTHON%
echo === AutoNewspaper 扫描（今天 + 昨天，无状态）===
python daily_scan.py --days 2 --no-state
echo.
echo 按任意键关闭本窗口...
pause >nul
