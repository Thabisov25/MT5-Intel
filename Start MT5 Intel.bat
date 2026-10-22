@echo off
setlocal
cd /d "%~dp0"
title MT5 intel - research console
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launcher\launch.ps1" %*
echo.
echo Press any key to close this window.
pause >nul
