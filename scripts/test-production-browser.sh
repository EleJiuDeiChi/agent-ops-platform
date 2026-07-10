#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="${AIOPS_PRODUCTION_BASE_URL:?set the running production HTTPS origin}"

cd "$ROOT_DIR/frontend"
PLAYWRIGHT_BASE_URL="$BASE_URL" \
PLAYWRIGHT_IGNORE_HTTPS_ERRORS=1 \
AIOPS_E2E_USERNAME="${AIOPS_E2E_USERNAME:-}" \
AIOPS_E2E_PASSWORD="${AIOPS_E2E_PASSWORD:-}" \
npm run test:e2e
