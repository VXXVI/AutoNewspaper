@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动本地看板（浏览器会自动打开）...
echo.
rem --detach：服务在后台运行，关掉本窗口不会停止看板。
rem 停止看板：NewspaperAuto.exe dashboard --stop
"%~dp0NewspaperAuto.exe" dashboard --open --detach
