@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title PM Brainstorming Workbench Stopper

echo.
echo =============================================
echo   PM Brainstorming Workbench / 停止服务
echo =============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\stop.ps1"
set "RC=%ERRORLEVEL%"

echo.
pause
endlocal
exit /b %RC%
