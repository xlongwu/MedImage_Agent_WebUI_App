@echo off
chcp 65001 >nul
title MedImage Agent
setlocal enabledelayedexpansion

echo ============================================
echo   MedImage Agent - One-Click Startup
echo ============================================
echo.

:: ── Navigate to project root ──
cd /d "%~dp0"

:: ── Refuse occupied ports; never terminate an unowned process ──
echo [1/4] Checking ports 8000 and 5173...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do (
    echo   ERROR: Port 8000 is already in use by PID %%a.
    echo   Stop the owning process explicitly or choose another port.
    exit /b 1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173.*LISTENING" 2^>nul') do (
    echo   ERROR: Port 5173 is already in use by PID %%a.
    echo   Stop the owning process explicitly or choose another port.
    exit /b 1
)

:: ── Start Backend (uvicorn on 8000) ──
echo [2/4] Starting backend (uvicorn :8000)...
start "MedImage-Backend" cmd /c "cd /d %~dp0 && uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000"

:: ── Wait for backend ──
echo   Waiting for backend to be ready...
set /a count=0
:wait_backend
timeout /t 1 /nobreak >nul
set /a count+=1
curl -s http://127.0.0.1:8000/api/health >nul 2>&1
if not errorlevel 1 goto backend_ready
if !count! lss 30 goto wait_backend
echo   WARNING: Backend may not have started. Continuing...
goto start_frontend
:backend_ready
echo   Backend is ready: http://127.0.0.1:8000

:: ── Start Frontend (vite on 5173) ──
:start_frontend
echo [3/4] Starting frontend (vite :5173)...
start "MedImage-Frontend" cmd /c "cd /d %~dp0src\frontend && npm run dev"

:: ── Wait for frontend ──
echo   Waiting for frontend to be ready...
set /a count=0
:wait_frontend
timeout /t 1 /nobreak >nul
set /a count+=1
curl -s http://127.0.0.1:5173 >nul 2>&1
if not errorlevel 1 goto frontend_ready
if !count! lss 30 goto wait_frontend
echo   WARNING: Frontend may not have started.
goto done
:frontend_ready
echo   Frontend is ready: http://127.0.0.1:5173

:: ── Open Browser ──
:done
echo [4/4] Opening browser...
start http://127.0.0.1:5173

echo.
echo ============================================
echo   All services should be running:
echo     Frontend : http://127.0.0.1:5173
echo     Backend  : http://127.0.0.1:8000
echo     Health   : http://127.0.0.1:8000/api/health
echo ============================================
echo.
echo Close this window or press Ctrl+C to stop.
pause >nul
