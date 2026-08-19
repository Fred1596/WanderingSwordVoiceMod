@echo off
setlocal
chcp 65001 >nul
title Wandering Sword Voice Mod - Uninstall
echo This removes only the voice bridge. UE4SS is preserved for other mods.
set /p "CONFIRM=Type YES to continue: "
if /I not "%CONFIRM%"=="YES" exit /b 0
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0runtime\Uninstall-Mod.ps1"
set "RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %RESULT%
