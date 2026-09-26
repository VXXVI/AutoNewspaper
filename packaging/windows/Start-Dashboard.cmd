@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在打开本地看板（浏览器会自动打开，关闭本窗口或按 Ctrl+C 停止）...
echo.
"%~dp0NewspaperAuto.exe" dashboard --open
echo.
echo 看板已退出。按任意键关闭本窗口...
pause >nul
