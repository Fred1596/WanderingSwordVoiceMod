@echo off
setlocal
chcp 65001 >nul
title Wandering Sword Voice Playback Speed
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\Set-PlaybackSpeed.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
