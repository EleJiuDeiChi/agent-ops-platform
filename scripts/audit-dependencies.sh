#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT_DIR/backend/.venv/bin/pip-audit" -r "$ROOT_DIR/backend/requirements.txt"
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || printf '0')"
if [ "${AIOPS_FRONTEND_VIA_DOCKER:-0}" = "1" ] || [ "$NODE_MAJOR" -lt 22 ]; then
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --volume "$ROOT_DIR:/workspace" \
    --workdir /workspace/frontend \
    node:22.23.1-bookworm-slim \
    npm audit --audit-level=high
else
  (cd "$ROOT_DIR/frontend" && npm audit --audit-level=high)
fi
