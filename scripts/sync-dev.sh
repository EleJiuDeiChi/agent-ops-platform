#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVBOX_SSH_TARGET="${DEVBOX_SSH_TARGET:-yanyan-devbox-via-idc}"
DEVBOX_PROJECT_DIR="${DEVBOX_PROJECT_DIR:-/home/ubuntu/Documents/trae_projects/业务效率工具/Agent运维平台}"

ssh "$DEVBOX_SSH_TARGET" "mkdir -p '$DEVBOX_PROJECT_DIR'"

rsync -az --delete \
  --exclude '.DS_Store' \
  --exclude '.env' \
  --include '.env.example' \
  --include '.env.production.example' \
  --exclude '.env.*' \
  --exclude '.git/' \
  --exclude '.omx/' \
  --exclude '.playwright-cli/' \
  --exclude '.playwright-mcp/' \
  --exclude '.codex-artifacts/' \
  --exclude '.pytest_cache/' \
  --exclude 'demo/' \
  --exclude 'deploy/secrets/' \
  --exclude 'backend/.venv/' \
  --exclude 'backend/.pytest_cache/' \
  --exclude 'data/*.sqlite' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  --exclude 'frontend/.vite/' \
  --exclude 'frontend/.pytest_cache/' \
  "$ROOT_DIR/" "$DEVBOX_SSH_TARGET:$DEVBOX_PROJECT_DIR/"

ssh "$DEVBOX_SSH_TARGET" "cd '$DEVBOX_PROJECT_DIR' && find . -maxdepth 2 -type f | sort | sed -n '1,80p'"
