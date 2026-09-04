@echo off
setlocal enabledelayedexpansion
title EDA Application - Local Launcher (No Auth)

echo ======================================================================
echo    Data Quiz EDA Application - Local Launcher (Zero-Authorization)
echo ======================================================================
echo.

cd /d "%~dp0"

:: 1. Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not found in PATH.
    echo Please install Python 3.10+ to continue.
    pause
    exit /b 1
)

:: 2. Check Node.js
where node >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Node.js is not installed or not found in PATH.
    echo Please install Node.js 18+ to continue.
    pause
    exit /b 1
)

:: 3. Setup Backend Environment
echo [1/3] Preparing Backend (FastAPI)...
cd /d "%~dp0backend"
if not exist ".venv" (
    echo Creating virtual environment for backend...
    python -m venv .venv
)

if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
    )
)

echo Installing backend dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt --quiet

:: 4. Setup Frontend Environment
echo.
echo [2/3] Preparing Frontend (Next.js)...
cd /d "%~dp0frontend"
if not exist "node_modules" (
    echo Installing npm dependencies...
    call npm install
)

if not exist ".env.local" (
    if exist ".env.local.example" (
        copy .env.local.example .env.local >nul
    )
)

:: 5. Launch Services
echo.
echo [3/3] Launching EDA Application...
echo.
echo ----------------------------------------------------------------------
echo  Backend running at : http://localhost:8000
echo  API Documentation  : http://localhost:8000/docs
echo  Frontend UI        : http://localhost:3000
echo  Auth Mode          : ZERO-AUTHORIZATION (Open Access)
echo ----------------------------------------------------------------------
echo.

:: Launch backend in a separate terminal window
cd /d "%~dp0backend"
start "EDA Backend (FastAPI :8000)" cmd /k "call .venv\Scripts\activate.bat && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload"

:: Launch frontend in a separate terminal window
cd /d "%~dp0frontend"
start "EDA Frontend (Next.js :3000)" cmd /k "npm run dev -- -p 3000"

echo Both services launched in separate windows.
echo Opening browser to http://localhost:3000 in 3 seconds...
timeout /t 3 >nul
start http://localhost:3000

echo.
echo Press any key to exit this launcher (services will remain running).
pause >nul
exit /b 0
