@echo off
REM ============================================================
REM  VLM Agent Service — Windows 启动脚本
REM
REM  使用方式：
REM    直接双击运行，或在终端执行：
REM    run_server.bat [端口号]
REM
REM  默认端口：8000
REM  环境变量覆盖：VLM_AGENT_SERVICE_PORT
REM ============================================================

setlocal enabledelayedexpansion

cd /d "%~dp0"

REM 检查 .env 是否存在
if not exist ".env" (
    echo [ERROR] .env file not found. Copy .env.example to .env and fill in your VLM credentials.
    pause
    exit /b 1
)

REM 检查 Python 环境
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python not found in PATH.
    pause
    exit /b 1
)

REM 端口设置（命令行参数 > 环境变量 > 默认 8000）
set PORT=8000
if not "%1"=="" set PORT=%1
if defined VLM_AGENT_SERVICE_PORT set PORT=%VLM_AGENT_SERVICE_PORT%

REM 工作进程数
set WORKERS=%VLM_AGENT_SERVICE_WORKERS%
if "%WORKERS%"=="" set WORKERS=1

echo ============================================================
echo   VLM Agent Service
echo   Port    : %PORT%
echo   Workers : %WORKERS%
echo   Host    : 0.0.0.0
echo ============================================================

python -m uvicorn agent.service:app --host 0.0.0.0 --port %PORT% --workers %WORKERS% --log-level info

pause
