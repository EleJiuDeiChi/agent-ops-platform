#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiops-r0-verify.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
PYTHON_BIN="${AIOPS_PYTHON_BIN:-$ROOT_DIR/backend/.venv/bin/python}"
test -x "$PYTHON_BIN" || PYTHON_BIN=python3

required_files=(
  .dockerignore
  .env.production.example
  backend/Dockerfile.test
  deploy/compose.prod.yml
  deploy/compose.setup.yml
  deploy/nginx.conf
  deploy/nginx.dev.conf
  deploy/nginx.prod.conf
  docs/architecture/README.md
  docs/runbooks/README.md
  docs/security/llm-provider-policy.example.json
  docs/security/threat-model.md
  docs/support-matrix.md
  docs/provenance.md
  docs/evidence/README.md
  .omx/specs/r0-production-baseline.md
  scripts/probe-llm-contract.py
  scripts/deploy-production.sh
  scripts/assert-production-topology.py
  scripts/test-production-compose.sh
  scripts/test-r0-clean-vm-matrix.sh
  scripts/run-r0-clean-vm-matrix-devbox.sh
)

for file in "${required_files[@]}"; do
  test -f "$ROOT_DIR/$file" || {
    echo "missing R0 artifact: $file" >&2
    exit 1
  }
done

"$PYTHON_BIN" -m py_compile "$ROOT_DIR/scripts/probe-llm-contract.py"
"$PYTHON_BIN" -m py_compile "$ROOT_DIR/scripts/assert-production-topology.py"
"$PYTHON_BIN" "$ROOT_DIR/scripts/probe-llm-contract.py" --help >/dev/null
"$ROOT_DIR/scripts/deploy-production.sh" --help >/dev/null
"$ROOT_DIR/scripts/assert-production-topology.py" --help >/dev/null

for marker in "ubuntu-cloudimage-keyring" "source_archive_sha256" \
  "docker_ce_package" "registry@sha256:"; do
  grep -q "$marker" "$ROOT_DIR/scripts/test-r0-clean-vm-matrix.sh" || {
    echo "clean-VM evidence marker is missing: $marker" >&2
    exit 1
  }
done

for marker in "syft-version: v1.46.0" "version: v0.72.0" "cosign-release: v3.1.1" \
  "provenance: mode=max" "environment:" "production-release"; do
  grep -q "$marker" "$ROOT_DIR/.github/workflows/r0-ci.yml" || {
    echo "R0 CI supply-chain marker is missing: $marker" >&2
    exit 1
  }
done
if grep -Eq 'uses: [^#[:space:]]+@v[0-9]' "$ROOT_DIR/.github/workflows/r0-ci.yml"; then
  echo "GitHub Actions must be pinned to immutable commit SHAs" >&2
  exit 1
fi

grep -q '^USER 10001:10001$' "$ROOT_DIR/backend/Dockerfile"
grep -q '^USER 10001:10001$' "$ROOT_DIR/backend/Dockerfile.test"
grep -q '^USER 101:101$' "$ROOT_DIR/frontend/Dockerfile"
grep -q 'npm ci' "$ROOT_DIR/frontend/Dockerfile"
grep -q 'npm run build' "$ROOT_DIR/frontend/Dockerfile"
grep -q 'chmod 0644 /etc/nginx/nginx.conf /etc/nginx/conf.d/default.conf' "$ROOT_DIR/frontend/Dockerfile"
if grep -Eq 'npm run dev|vite preview' "$ROOT_DIR/frontend/Dockerfile"; then
  echo "frontend runtime must not use a development server" >&2
  exit 1
fi
grep -q 'proxy_pass http://backend:8080' "$ROOT_DIR/deploy/nginx.prod.conf"
grep -q 'AIOPS_SESSION_SECRET_FILE: /run/secrets/session_secret' "$ROOT_DIR/deploy/compose.prod.yml"
grep -q 'AIOPS_LLM_API_KEY_FILE: /run/secrets/llm_api_key' "$ROOT_DIR/deploy/compose.prod.yml"
grep -q 'AIOPS_LLM_EVIDENCE_FILE: /run/secrets/llm_evidence' "$ROOT_DIR/deploy/compose.prod.yml"
grep -q 'cosign verify' "$ROOT_DIR/scripts/deploy-production.sh"
grep -q 'verify-attestation' "$ROOT_DIR/scripts/deploy-production.sh"
if grep -Eq 'AIOPS_(SESSION_SECRET|LLM_API_KEY):' "$ROOT_DIR/deploy/compose.prod.yml"; then
  echo "production compose must use secret files, not direct secret values" >&2
  exit 1
fi
if grep -q 'AIOPS_MUTATION_ENABLED' "$ROOT_DIR/deploy/compose.prod.yml"; then
  echo "unimplemented mutation flags create false assurance" >&2
  exit 1
fi

for excluded in demo data node_modules .venv; do
  grep -q "$excluded" "$ROOT_DIR/.dockerignore" || {
    echo ".dockerignore does not document exclusion for $excluded" >&2
    exit 1
  }
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker CLI is required for compose validation" >&2
  exit 1
fi

printf '%064d\n' 0 > "$TMP_DIR/session_secret"
printf '%064d\n' 1 > "$TMP_DIR/llm_api_key"
printf '%064d\n' 2 > "$TMP_DIR/setup_token"
printf '%s\n' '{"schema_version":1,"result":"fail"}' > "$TMP_DIR/llm_evidence.json"
printf '%s\n' 'verification-only certificate placeholder' > "$TMP_DIR/tls_cert.pem"
printf '%s\n' 'verification-only key placeholder' > "$TMP_DIR/tls_key.pem"

export AIOPS_BACKEND_IMAGE_REF="sha256:$(printf '%064d' 4)"
export AIOPS_FRONTEND_IMAGE_REF="sha256:$(printf '%064d' 5)"
export AIOPS_HTTPS_BIND=127.0.0.1
export AIOPS_HTTPS_PORT=443
export AIOPS_TRUSTED_ORIGINS=https://ops.example.invalid
export AIOPS_RELEASE_DIGEST="sha256:$(printf '%064d' 4)"
export AIOPS_COMMIT_SHA=0000000
export AIOPS_SESSION_SECRET_FILE_HOST="$TMP_DIR/session_secret"
export AIOPS_TLS_CERT_FILE_HOST="$TMP_DIR/tls_cert.pem"
export AIOPS_TLS_KEY_FILE_HOST="$TMP_DIR/tls_key.pem"
export AIOPS_LLM_MODE=deepseek
export AIOPS_LLM_BASE_URL=https://api.deepseek.com
export AIOPS_LLM_MODEL=verification-model-from-models-endpoint
export AIOPS_LLM_API_KEY_FILE_HOST="$TMP_DIR/llm_api_key"
export AIOPS_LLM_EVIDENCE_FILE_HOST="$TMP_DIR/llm_evidence.json"
export AIOPS_LLM_EVIDENCE_SHA256="sha256:$(openssl dgst -sha256 "$TMP_DIR/llm_evidence.json" | awk '{print $NF}')"
export AIOPS_SETUP_TOKEN_FILE_HOST="$TMP_DIR/setup_token"
export AIOPS_SETUP_TOKEN_EXPIRES_AT=2099-01-01T00:15:00Z

docker compose -f "$ROOT_DIR/docker-compose.yml" config --quiet
docker compose -f "$ROOT_DIR/deploy/compose.prod.yml" config --format json > "$TMP_DIR/compose-prod.json"
docker compose \
  -f "$ROOT_DIR/deploy/compose.prod.yml" \
  -f "$ROOT_DIR/deploy/compose.setup.yml" \
  config --format json > "$TMP_DIR/compose-setup.json"

"$ROOT_DIR/scripts/assert-production-topology.py" compose \
  "$TMP_DIR/compose-prod.json" --https-port 443
"$ROOT_DIR/scripts/assert-production-topology.py" compose \
  "$TMP_DIR/compose-setup.json" --setup --https-port 443

echo "R0 production baseline static validation passed"
