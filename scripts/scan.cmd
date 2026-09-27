@echo off
chcp 65001 >nul
rem ==============================================================================
rem scan.cmd - Windows「扫一次」启动器（run_scan.ps1 的薄封装）
rem
rem 所有扫描逻辑都在 run_scan.ps1（解释器解析 / cd 项目根 / 默认 --days 2 --no-state /
rem 参数原样透传），本文件只负责双击时的交互：执行、显示退出状态、等按键再关窗。
rem ==============================================================================
echo === AutoNewspaper 扫描（默认今天 + 昨天，无状态）===
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_scan.ps1" %*
if errorlevel 1 (
  echo.
  echo ✗ 扫描失败，请查看上面的输出与 logs\。
)
echo.
echo 按任意键关闭本窗口...
pause >nul
