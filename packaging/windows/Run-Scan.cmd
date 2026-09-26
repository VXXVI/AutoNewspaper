@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 数字报监测：扫描今天 + 昨天 ===
echo.
"%~dp0NewspaperAuto.exe" scan --days 2
echo.
echo 扫描结束。按任意键关闭本窗口...
pause >nul
