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
#
# If backend/.env exists, its values are exported into the backend's
# environment before starting it -- confirmed necessary, not a nice-to-
# have: app/config.py deliberately never auto-loads .env (see its
# docstring), so without this a variable that's sitting right there in
# backend/.env silently never reaches the running process unless your
# shell happened to have it exported already. That split is exactly
# how a real session ended up with Alpaca streaming working while
# Massive failed with "MASSIVE_API_KEY environment variable" missing --
# both keys were in .env the whole time, but only one had ever been
# exported into that shell's environment. This script deliberately
# still isn't "the app reads .env" (app/config.py's one-touchpoint
# rule is unchanged) -- it's this *launcher* choosing to do the export
# step for you, the same manual step config.py's docstring already
# suggests (`env $(cat .env | xargs)`), just automated.

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
    if [ -f .env ]; then
      set -a
      # shellcheck disable=SC1091
      source .env
      set +a
    fi
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

# All descendants of $1 (children, grandchildren, ...), one PID per
# line. Must be captured BEFORE signaling anything -- once a parent is
# gone, the OS reparents any surviving child (to PID 1 / launchd), and
# `pgrep -P <original pid>` can no longer find it.
descendants_of() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    echo "$child"
    descendants_of "$child"
  done
}

stop_one() {
  local name="$1" pid_file="$2"
  if is_running "$pid_file"; then
    local pid descendants
    pid="$(cat "$pid_file")"
    # uvicorn --reload runs its actual server as a *child* process (a
    # Python `multiprocessing` worker -- confirmed via `ps`: its command
    # line is the generic "multiprocessing.spawn_main", not anything
    # grep-able like "app.main:app", so it can only be found by this
    # PID relationship, not by pattern). Killing only the tracked PID
    # left that worker running in a real session -- still holding a
    # live streaming connection to Alpaca (see README section 14's
    # "Operational gotcha" callout) minutes after "stop" reported
    # success, which then made the *next* "start" fail to reconnect
    # with a confusing "connection limit exceeded" that looked like a
    # code bug and wasn't. Confirmed, not hypothetical -- hence killing
    # the whole descendant tree below, not just the tracked PID.
    descendants="$(descendants_of "$pid")"
    echo "Stopping $name (PID $pid) ..."
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
    for descendant in $descendants; do
      kill -9 "$descendant" 2>/dev/null || true
    done
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
