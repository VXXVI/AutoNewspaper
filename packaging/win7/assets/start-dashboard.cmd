@echo off
chcp 65001 >nul
setlocal
title NewspaperAuto - Dashboard
set "HERE=%~dp0"
rem Fallback launcher that keeps a visible console (handy for troubleshooting).
rem The normal entry point is start-dashboard.vbs, which shows no window at all.
"%HERE%python\python.exe" "%HERE%run.py"
endlocal
