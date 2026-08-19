@echo off
setlocal
chcp 65001 >nul
title Wandering Sword Offline Voice Player
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\VoicePlayer.ps1" -LaunchGame
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Voice player exited with code %RESULT%.
pause
exit /b %RESULT%
