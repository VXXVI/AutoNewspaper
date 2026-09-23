@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
echo 正在启动本地看板（http://127.0.0.1:8787）...
python -m newspaper_auto.web --open
echo.
echo 看板已退出，按任意键关闭本窗口...
pause >nul
