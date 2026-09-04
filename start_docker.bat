@echo off
setlocal enabledelayedexpansion
title EDA Application - Docker Launcher (DGX / Local)

echo ======================================================================
echo    Data Quiz EDA Application - Docker Launcher (Zero-Authorization)
echo ======================================================================
echo.

cd /d "%~dp0"

where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Docker is not installed or not in PATH.
    echo Please start Docker Desktop or install Docker to continue.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [.env] not found. Copying from .env.example...
    copy .env.example .env >nul
)

echo Building and starting containers via Docker Compose...
docker compose up --build -d

if %errorlevel% neq 0 (
    echo [ERROR] Docker compose failed to start.
    pause
    exit /b 1
)

echo.
echo ======================================================================
echo  EDA Application is starting up!
echo.
echo  Access URL (Nginx) : http://localhost:8090
echo  Direct Backend API : http://localhost:8000/docs
echo  Direct Frontend    : http://localhost:3000
echo.
echo  Auth Mode          : ZERO-AUTHORIZATION (Open Access)
echo ======================================================================
echo.
echo Checking service status...
docker compose ps

echo.
echo Opening http://localhost:8090 in browser in 3 seconds...
timeout /t 3 >nul
start http://localhost:8090

pause
