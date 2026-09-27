@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 数字报监测：扫描今天 + 昨天 ===
echo.
rem 控制台版：扫描进度需要看得见，所以用带控制台的 NewspaperAuto-CLI.exe
"%~dp0NewspaperAuto-CLI.exe" scan --days 2
echo.
echo 扫描结束。按任意键关闭本窗口...
pause >nul
