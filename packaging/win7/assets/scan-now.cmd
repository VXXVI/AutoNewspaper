@echo off
chcp 65001 >nul
setlocal
title NewspaperAuto - Scan
set "HERE=%~dp0"
rem Run one scan and keep the window open so progress and results are visible.
rem Extra arguments are passed through, e.g.  scan-now.cmd --days 7 --kw 教育
"%HERE%python\python.exe" "%HERE%run.py" scan %*
echo.
echo Done. Output is also appended to logs\scan-*.log
pause
endlocal
