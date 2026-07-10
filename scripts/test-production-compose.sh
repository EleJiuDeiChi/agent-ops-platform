#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiops-prod-compose.XXXXXX")"
PROJECT_NAME="agent-ops-r0-runtime-${RANDOM}"
HTTPS_PORT="${AIOPS_TEST_HTTPS_PORT:-55443}"

export AIOPS_IMAGE_TAG="${AIOPS_IMAGE_TAG:-r0-ci}"
if [ -z "${AIOPS_BACKEND_IMAGE_REF:-}" ]; then
  export AIOPS_BACKEND_IMAGE_REF="$(docker image inspect "agent-ops-backend:$AIOPS_IMAGE_TAG" --format '{{.Id}}')"
fi
if [ -z "${AIOPS_FRONTEND_IMAGE_REF:-}" ]; then
  export AIOPS_FRONTEND_IMAGE_REF="$(docker image inspect "agent-ops-frontend:$AIOPS_IMAGE_TAG" --format '{{.Id}}')"
fi
case "$AIOPS_BACKEND_IMAGE_REF" in
  sha256:*) export AIOPS_RELEASE_DIGEST="$AIOPS_BACKEND_IMAGE_REF" ;;
  *@sha256:*) export AIOPS_RELEASE_DIGEST="${AIOPS_BACKEND_IMAGE_REF##*@}" ;;
  *) echo "AIOPS_BACKEND_IMAGE_REF must be immutable by digest" >&2; exit 1 ;;
esac
case "$AIOPS_FRONTEND_IMAGE_REF" in
  sha256:*|*@sha256:*) ;;
  *) echo "AIOPS_FRONTEND_IMAGE_REF must be immutable by digest" >&2; exit 1 ;;
esac
if [ -n "${AIOPS_COMMIT_SHA:-}" ]; then
  export AIOPS_COMMIT_SHA
elif AIOPS_COMMIT_SHA="$(git -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null)"; then
  export AIOPS_COMMIT_SHA
else
  export AIOPS_COMMIT_SHA=0000000
fi
export AIOPS_HTTPS_BIND=127.0.0.1
export AIOPS_HTTPS_PORT="$HTTPS_PORT"
export AIOPS_TRUSTED_ORIGINS="https://127.0.0.1:$HTTPS_PORT"
export AIOPS_SESSION_SECRET_FILE_HOST="$TMP_DIR/session_secret"
export AIOPS_TLS_CERT_FILE_HOST="$TMP_DIR/tls_cert.pem"
export AIOPS_TLS_KEY_FILE_HOST="$TMP_DIR/tls_key.pem"
export AIOPS_LLM_MODE=deepseek
export AIOPS_LLM_BASE_URL=https://api.deepseek.com
export AIOPS_LLM_MODEL=contract-probe-pending
export AIOPS_LLM_API_KEY_FILE_HOST="$TMP_DIR/llm_api_key"
export AIOPS_LLM_EVIDENCE_FILE_HOST="$TMP_DIR/llm_evidence.json"
export AIOPS_SETUP_TOKEN_FILE_HOST="$TMP_DIR/setup_token"
export AIOPS_SETUP_TOKEN_EXPIRES_AT="$(python3 -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat())')"

COMPOSE_PROD=(docker compose -p "$PROJECT_NAME" -f "$ROOT_DIR/deploy/compose.prod.yml")
COMPOSE_SETUP=(docker compose -p "$PROJECT_NAME" -f "$ROOT_DIR/deploy/compose.prod.yml" -f "$ROOT_DIR/deploy/compose.setup.yml")

cleanup() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    echo "production Compose validation failed; preserving diagnostics before cleanup" >&2
    "${COMPOSE_PROD[@]}" ps >&2 || true
    "${COMPOSE_PROD[@]}" logs --no-color --tail 200 >&2 || true
  fi
  "${COMPOSE_PROD[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
  return "$status"
}
trap cleanup EXIT

openssl rand -hex 32 > "$AIOPS_SESSION_SECRET_FILE_HOST"
openssl rand -hex 32 > "$AIOPS_LLM_API_KEY_FILE_HOST"
openssl rand -hex 32 > "$AIOPS_SETUP_TOKEN_FILE_HOST"
SETUP_TOKEN_VALUE="$(tr -d '\r\n' < "$AIOPS_SETUP_TOKEN_FILE_HOST")"
chmod 600 "$AIOPS_SESSION_SECRET_FILE_HOST" "$AIOPS_LLM_API_KEY_FILE_HOST" "$AIOPS_SETUP_TOKEN_FILE_HOST"
printf '%s\n' '{"schema_version":1,"result":"fail","reason":"live provider probe intentionally absent in topology test"}' > "$AIOPS_LLM_EVIDENCE_FILE_HOST"
chmod 444 "$AIOPS_LLM_EVIDENCE_FILE_HOST"
export AIOPS_LLM_EVIDENCE_SHA256="sha256:$(openssl dgst -sha256 "$AIOPS_LLM_EVIDENCE_FILE_HOST" | awk '{print $NF}')"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj '/CN=127.0.0.1' \
  -keyout "$AIOPS_TLS_KEY_FILE_HOST" -out "$AIOPS_TLS_CERT_FILE_HOST" >/dev/null 2>&1

if [ "$(uname -s)" = "Linux" ]; then
  docker run --rm --user 0:0 --entrypoint sh \
    --volume "$TMP_DIR:/secrets" \
    "$AIOPS_BACKEND_IMAGE_REF" \
    -c 'chown 10001:10001 /secrets/session_secret /secrets/llm_api_key /secrets/llm_evidence.json /secrets/setup_token && chmod 0400 /secrets/session_secret /secrets/llm_api_key /secrets/llm_evidence.json /secrets/setup_token && chown 101:101 /secrets/tls_cert.pem /secrets/tls_key.pem && chmod 0400 /secrets/tls_cert.pem /secrets/tls_key.pem'
fi

"${COMPOSE_PROD[@]}" config --format json > "$TMP_DIR/compose-prod.json"
"${COMPOSE_SETUP[@]}" config --format json > "$TMP_DIR/compose-setup.json"
"$ROOT_DIR/scripts/assert-production-topology.py" compose \
  "$TMP_DIR/compose-prod.json" --https-port "$HTTPS_PORT"
"$ROOT_DIR/scripts/assert-production-topology.py" compose \
  "$TMP_DIR/compose-setup.json" --setup --https-port "$HTTPS_PORT"

wait_for_live() {
  for _ in $(seq 1 30); do
    if curl -kfsS "https://127.0.0.1:$HTTPS_PORT/health/live" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  "${COMPOSE_PROD[@]}" ps
  "${COMPOSE_PROD[@]}" logs --tail 100
  return 1
}

assert_readiness() {
  local expected_reasons="$1"
  local body="$TMP_DIR/readiness.json"
  local code
  code="$(curl -ksS -o "$body" -w '%{http_code}' "https://127.0.0.1:$HTTPS_PORT/health/ready")"
  test "$code" = "503"
  python3 - "$body" "$expected_reasons" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
expected = sys.argv[2].split(",") if sys.argv[2] else []
assert payload["status"] == "not_ready", payload
assert payload["reasons"] == expected, payload
assert payload["dependencies"]["worker"]["status"] == "disabled_by_release_gate", payload
assert payload["dependencies"]["agent"]["status"] == "disabled_by_release_gate", payload
PY
}

"${COMPOSE_SETUP[@]}" up -d --no-build
wait_for_live
assert_readiness "setup_required,llm_unverified"

COOKIE_FILE="$TMP_DIR/cookies"
CSRF_JSON="$(curl -kfsS -c "$COOKIE_FILE" "https://127.0.0.1:$HTTPS_PORT/api/csrf")"
CSRF_TOKEN="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])' <<<"$CSRF_JSON")"
SETUP_BODY="$(printf '{"token":"%s","username":"admin","password":"ProductionTestPassword123!"}' "$SETUP_TOKEN_VALUE")"
curl -kfsS -b "$COOKIE_FILE" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H "Origin: https://127.0.0.1:$HTTPS_PORT" \
  -H 'Content-Type: application/json' \
  --data "$SETUP_BODY" \
  "https://127.0.0.1:$HTTPS_PORT/api/setup/enroll" >/dev/null
assert_readiness "llm_unverified"

BACKEND_ID="$("${COMPOSE_PROD[@]}" ps -q backend)"
docker restart "$BACKEND_ID" >/dev/null
sleep 3
RESTART_LOG="$TMP_DIR/backend-restart.log"
docker logs "$BACKEND_ID" > "$RESTART_LOG" 2>&1
grep -q 'remove setup token configuration after production enrollment' "$RESTART_LOG"

"${COMPOSE_PROD[@]}" down --remove-orphans
rm -f "$AIOPS_SETUP_TOKEN_FILE_HOST"
"${COMPOSE_PROD[@]}" up -d --no-build
wait_for_live
assert_readiness "llm_unverified"

curl -kfsS "https://127.0.0.1:$HTTPS_PORT/version" | python3 -c '
import json, sys
payload = json.load(sys.stdin)
assert payload["environment"] == "prod", payload
assert payload["provenance"]["status"] == "declared", payload
assert payload["release_digest"], payload
assert payload["commit_sha"], payload
'

FRONTEND_ID="$("${COMPOSE_PROD[@]}" ps -q frontend)"
BACKEND_ID="$("${COMPOSE_PROD[@]}" ps -q backend)"
docker inspect "$BACKEND_ID" "$FRONTEND_ID" > "$TMP_DIR/runtime-inspect.json"
"$ROOT_DIR/scripts/assert-production-topology.py" runtime \
  "$TMP_DIR/runtime-inspect.json" --https-port "$HTTPS_PORT"

HEADER_FILE="$TMP_DIR/frontend-headers.txt"
curl -kfsSI "https://127.0.0.1:$HTTPS_PORT/" > "$HEADER_FILE"
grep -qi '^Strict-Transport-Security:' "$HEADER_FILE"
grep -qi '^Content-Security-Policy:' "$HEADER_FILE"

if [ "${AIOPS_RUN_PRODUCTION_PLAYWRIGHT:-0}" = "1" ]; then
  AIOPS_PRODUCTION_BASE_URL="https://127.0.0.1:$HTTPS_PORT" \
    AIOPS_E2E_USERNAME=admin \
    AIOPS_E2E_PASSWORD='ProductionTestPassword123!' \
    "$ROOT_DIR/scripts/test-production-browser.sh"
fi

echo "R0 production Compose runtime validation passed"
