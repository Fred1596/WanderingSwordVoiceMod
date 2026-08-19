@echo off
setlocal
chcp 65001 >nul
title Wandering Sword Voice Mod - Install
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\Install-Mod.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Installation did not complete. See the message above.
pause
exit /b %RESULT%
