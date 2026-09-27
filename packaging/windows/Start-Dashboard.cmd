@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动本地看板（浏览器会自动打开）...
echo.
rem 用控制台版启动：出错时能在这句话下面看到原因；--detach 让服务脱离本窗口后台运行。
rem 服务已经在跑就直接复用，不会重复开一个。
rem 停止看板：NewspaperAuto-CLI.exe dashboard --stop
"%~dp0NewspaperAuto-CLI.exe" dashboard --open --detach
echo.
echo 关掉本窗口不会停止看板。
timeout /t 4 >nul
