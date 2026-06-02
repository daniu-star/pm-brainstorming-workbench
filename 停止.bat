@echo off
chcp 65001 >nul
title PM Brainstorming Workbench - 停止服务

echo.
echo  ========================================
echo   PM Brainstorming Workbench 停止器
echo  ========================================
echo.

echo 正在停止所有 Node.js 进程...
taskkill /F /IM node.exe >nul 2>&1

echo 正在停止所有 Python 进程...
taskkill /F /IM python.exe >nul 2>&1

echo.
echo  所有服务已停止
echo.
timeout /t 2 /nobreak >nul
