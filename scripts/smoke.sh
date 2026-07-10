#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT_DIR/backend"
export AIOPS_DB_PATH="${AIOPS_DB_PATH:-$ROOT_DIR/data/smoke.sqlite}"
export AIOPS_BOOTSTRAP_ADMIN_PASSWORD="${AIOPS_BOOTSTRAP_ADMIN_PASSWORD:-BootstrapPassword123!}"

cd "$ROOT_DIR/backend"
if [ ! -d "$ROOT_DIR/backend/.venv" ]; then
  python3 -m venv "$ROOT_DIR/backend/.venv"
fi
"$ROOT_DIR/backend/.venv/bin/pip" install -q -r "$ROOT_DIR/backend/requirements-dev.txt"
"$ROOT_DIR/backend/.venv/bin/pytest"

cd "$ROOT_DIR/frontend"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || printf '0')"
if [ "${AIOPS_FRONTEND_VIA_DOCKER:-0}" = "1" ] || [ "$NODE_MAJOR" -lt 22 ]; then
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --volume "$ROOT_DIR:/workspace" \
    --workdir /workspace/frontend \
    node:22.23.1-bookworm-slim \
    sh -lc 'npm ci --no-audit --no-fund && npm test && npm run build'
else
  if [ ! -d "$ROOT_DIR/frontend/node_modules" ]; then
    npm ci --no-audit --no-fund
  fi
  npm test
  npm run build
fi

"$ROOT_DIR/scripts/verify-production-baseline.sh"

if [ "${AIOPS_RUN_DEPENDENCY_AUDIT:-0}" = "1" ]; then
  "$ROOT_DIR/scripts/audit-dependencies.sh"
fi
