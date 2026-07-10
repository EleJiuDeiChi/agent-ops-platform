#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AIOPS_DB_PATH="${AIOPS_DB_PATH:-$ROOT_DIR/data/dev.sqlite}"
export AIOPS_BOOTSTRAP_ADMIN_PASSWORD="${AIOPS_BOOTSTRAP_ADMIN_PASSWORD:-BootstrapPassword123!}"
export AIOPS_LLM_MODE="${AIOPS_LLM_MODE:-mock}"
export AIOPS_DEBUG_SKIP_PASSWORD_CHANGE="${AIOPS_DEBUG_SKIP_PASSWORD_CHANGE:-1}"
export PYTHONPATH="$ROOT_DIR/backend"

cd "$ROOT_DIR"

if [ ! -d "$ROOT_DIR/backend/.venv" ]; then
  python3 -m venv "$ROOT_DIR/backend/.venv"
fi

"$ROOT_DIR/backend/.venv/bin/pip" install -q -r "$ROOT_DIR/backend/requirements.txt"

if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
  (cd "$ROOT_DIR/frontend" && npm install)
fi

"$ROOT_DIR/backend/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8080 &
BACKEND_PID=$!

cd "$ROOT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

trap 'kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true' EXIT
wait
