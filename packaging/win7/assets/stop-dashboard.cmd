@echo off
chcp 65001 >nul
setlocal
title NewspaperAuto - Stop
set "HERE=%~dp0"
"%HERE%python\python.exe" "%HERE%run.py" dashboard --stop
pause
endlocal
