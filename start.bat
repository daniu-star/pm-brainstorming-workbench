@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title PM Brainstorming Workbench Launcher

echo.
echo =============================================
echo   PM Brainstorming Workbench / 一键启动
echo =============================================
echo.
echo   API :8001    Worker    Web :3001
echo   （端口被占用时会自动顺延）
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [!] 未找到 .venv\Scripts\python.exe
    echo     请先执行:  python -m venv .venv
    echo                .venv\Scripts\pip install -r backend\requirements.txt
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\launch.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo [!] 启动未完成（退出码 %RC%）。请看上面的提示或各服务窗口里的报错。
    echo.
    pause
) else (
    echo 启动完成。本启动器窗口 5 秒后自动关闭，可随时双击 stop.bat 停止全部服务。
    echo.
    ping -n 5 127.0.0.1 >nul
)

endlocal
exit /b %RC%
