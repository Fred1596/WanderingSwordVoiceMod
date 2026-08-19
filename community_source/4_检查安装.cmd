@echo off
setlocal
chcp 65001 >nul
title Wandering Sword Voice Mod - Check
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\Check-Mod.ps1"
if errorlevel 1 goto :failed
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\VoicePlayer.ps1" -SelfTest
if errorlevel 1 goto :failed
echo.
echo Everything is ready.
pause
exit /b 0

:failed
echo.
echo A check failed. Run 1_Install Mod again or read Troubleshooting.md.
pause
exit /b 1
