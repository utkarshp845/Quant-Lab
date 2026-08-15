#!/usr/bin/env bash
# One-command start/stop for the backend (FastAPI/uvicorn) and frontend
# (Vite) dev servers together, so you don't have to juggle two terminal
# tabs. See README sections 4-5 for what each does individually and how
# to install dependencies first (this script does not install anything).
#
# Usage:
#   ./scripts/dev.sh start     # start both, if not already running
#   ./scripts/dev.sh stop      # stop both
#   ./scripts/dev.sh restart   # stop then start
#   ./scripts/dev.sh status    # show what's running, where, and log paths
#
# PID files and logs live in .dev/ (gitignored) at the repo root -- that's
# how "stop" finds the right processes, and where to look if a server
# fails to start (the log will have the real error, e.g. a missing venv).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.dev"
mkdir -p "$RUN_DIR"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

backend_uvicorn() {
  if [ -x "$ROOT_DIR/backend/venv/bin/uvicorn" ]; then
    echo "$ROOT_DIR/backend/venv/bin/uvicorn"
  elif command -v uvicorn >/dev/null 2>&1; then
    echo "uvicorn"
  else
    echo "ERROR: no backend/venv/bin/uvicorn and no uvicorn on PATH." >&2
    echo "Set up the backend first (README section 3):" >&2
    echo "  cd backend && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt" >&2
    exit 1
  fi
}

is_running() {
  local pid_file="$1"
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

start_backend() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "Backend already running (PID $(cat "$BACKEND_PID_FILE"))."
    return
  fi
  local uvicorn_bin
  uvicorn_bin="$(backend_uvicorn)"
  echo "Starting backend on :$BACKEND_PORT (log: $BACKEND_LOG) ..."
  (
    cd "$ROOT_DIR/backend"
    nohup "$uvicorn_bin" app.main:app --reload --port "$BACKEND_PORT" >"$BACKEND_LOG" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
}

start_frontend() {
  if is_running "$FRONTEND_PID_FILE"; then
    echo "Frontend already running (PID $(cat "$FRONTEND_PID_FILE"))."
    return
  fi
  if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    echo "ERROR: frontend/node_modules missing. Run: cd frontend && npm install" >&2
    exit 1
  fi
  echo "Starting frontend on :$FRONTEND_PORT (log: $FRONTEND_LOG) ..."
  (
    cd "$ROOT_DIR/frontend"
    nohup npm run dev -- --port "$FRONTEND_PORT" >"$FRONTEND_LOG" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )
}

stop_one() {
  local name="$1" pid_file="$2"
  if is_running "$pid_file"; then
    local pid
    pid="$(cat "$pid_file")"
    echo "Stopping $name (PID $pid) ..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
  else
    echo "$name not running (no live PID file)."
  fi
  rm -f "$pid_file"
}

status_one() {
  local name="$1" pid_file="$2" port="$3"
  if is_running "$pid_file"; then
    echo "$name: running (PID $(cat "$pid_file"), port $port)"
  else
    echo "$name: not running"
  fi
}

case "${1:-}" in
  start)
    start_backend
    start_frontend
    ;;
  stop)
    stop_one "backend" "$BACKEND_PID_FILE"
    stop_one "frontend" "$FRONTEND_PID_FILE"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    status_one "backend" "$BACKEND_PID_FILE" "$BACKEND_PORT"
    status_one "frontend" "$FRONTEND_PID_FILE" "$FRONTEND_PORT"
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
