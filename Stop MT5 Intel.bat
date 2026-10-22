@echo off
setlocal
cd /d "%~dp0"
title Stop MT5 intel
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launcher\launch.ps1" -Stop
echo.
echo Press any key to close this window.
pause >nul
