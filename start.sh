#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "  MedImage Agent - One-Click Startup"
echo "============================================"
echo ""

# ── Refuse occupied ports; never terminate an unowned process ──
echo "[1/4] Checking ports..."
for port in 8000 5173; do
    pid=""
    if command -v lsof >/dev/null 2>&1; then
        pid=$(lsof -ti:"$port" 2>/dev/null | head -1 || true)
    elif command -v netstat >/dev/null 2>&1; then
        pid=$(netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING | awk '{print $5}' | head -1 || true)
    fi
    if [ -n "$pid" ] && [ "$pid" != "0" ]; then
        echo "  ERROR: Port $port is already in use by PID $pid."
        echo "  Stop the owning process explicitly or choose another port."
        exit 1
    fi
done

# ── Start Backend ──
echo "[2/4] Starting backend (uvicorn :8000)..."
uvicorn src.backend.app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
echo "  PID: $BACKEND_PID"

# ── Wait for backend ──
echo "  Waiting for backend..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:8000/api/health > /dev/null 2>&1; then
        echo "  Backend is ready: http://127.0.0.1:8000"
        break
    fi
    sleep 1
done

# ── Start Frontend ──
echo "[3/4] Starting frontend (vite :5173)..."
cd src/frontend && npm run dev &
FRONTEND_PID=$!
cd "$SCRIPT_DIR"
echo "  PID: $FRONTEND_PID"

# ── Wait for frontend ──
echo "  Waiting for frontend..."
for i in $(seq 1 30); do
    if curl -s http://127.0.0.1:5173 > /dev/null 2>&1; then
        echo "  Frontend is ready: http://127.0.0.1:5173"
        break
    fi
    sleep 1
done

echo ""
echo "============================================"
echo "  All services running!"
echo "  Frontend : http://127.0.0.1:5173"
echo "  Backend  : http://127.0.0.1:8000"
echo "  Health   : http://127.0.0.1:8000/api/health"
echo "============================================"
echo "  Press Ctrl+C to stop all services."
echo ""

# ── Trap Ctrl+C ──
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    echo "All services stopped."
    exit 0
}
trap cleanup INT TERM

wait
