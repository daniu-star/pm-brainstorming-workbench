@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title PM Brainstorming Workbench - 启动中...

echo.
echo  ========================================
echo   PM Brainstorming Workbench 启动器
echo  ========================================
echo.

cd /d "%~dp0"

echo [1/4] 检查 Node.js...
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Node.js，请先安装 Node.js
    pause
    exit /b 1
)

echo [2/4] 检查 Python...
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python
    pause
    exit /b 1
)

echo [3/4] 清除旧构建缓存...
if exist "%~dp0frontend\.next" (
    rmdir /s /q "%~dp0frontend\.next" 2>nul
    echo  已清除 .next 缓存
) else (
    echo  无需清除
)

echo [4/4] 启动服务...
echo.
echo  后端启动中... (端口 8000)
start "Backend Server" cmd /k "cd /d "%~dp0backend" && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo  等待后端就绪...
set BACKEND_READY=0
for /L %%i in (1,1,30) do (
    if !BACKEND_READY! equ 0 (
        curl -s http://localhost:8000/health >nul 2>&1
        if !errorlevel! equ 0 (
            set BACKEND_READY=1
            echo  后端已就绪
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if %BACKEND_READY% equ 0 (
    echo  [警告] 后端未在 30 秒内就绪，继续启动前端...
)

echo  前端启动中... (端口 3001)
start "Frontend Server" cmd /k "cd /d "%~dp0frontend" && npx next dev --port 3001"

echo.
echo  ========================================
echo   服务启动完成！
echo  ========================================
echo.
echo  浏览器将自动打开 http://localhost:3001
echo  如未自动打开，请手动访问该地址
echo.
echo  关闭此窗口不会停止服务
echo  要停止服务，请关闭后端/前端命令行窗口
echo.

timeout /t 8 /nobreak >nul
start http://localhost:3001

echo 按任意键关闭此窗口...
pause >nul
