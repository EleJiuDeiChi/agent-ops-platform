#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env.production"
SETUP=0
ACTION=check

usage() {
  cat <<'EOF'
Usage: deploy-production.sh [--env-file PATH] [--setup] [check|up]

Required environment:
  AIOPS_COSIGN_CERTIFICATE_IDENTITY  Exact GitHub Actions certificate identity
  AIOPS_COSIGN_OIDC_ISSUER          Expected OIDC issuer

The script refuses mutable image tags, verifies Cosign signatures plus
SLSA-provenance and SPDX-SBOM attestations, then optionally starts Compose.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --setup)
      SETUP=1
      shift
      ;;
    check|up)
      ACTION="$1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

test -f "$ENV_FILE" || { echo "production env file not found: $ENV_FILE" >&2; exit 1; }
command -v cosign >/dev/null 2>&1 || {
  echo "cosign is required; install the reviewed version from docs/evidence/README.md" >&2
  exit 1
}
CERTIFICATE_IDENTITY="${AIOPS_COSIGN_CERTIFICATE_IDENTITY:?set the exact release workflow certificate identity}"
OIDC_ISSUER="${AIOPS_COSIGN_OIDC_ISSUER:?set the expected release OIDC issuer}"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/deploy/compose.prod.yml")
if [ "$SETUP" = "1" ]; then
  COMPOSE+=(-f "$ROOT_DIR/deploy/compose.setup.yml")
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiops-release-verify.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT
"${COMPOSE[@]}" config --format json > "$TMP_DIR/compose.json"

python3 - "$TMP_DIR/compose.json" > "$TMP_DIR/images.txt" <<'PY'
import json
import re
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
services = payload.get("services") or {}
pattern = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
backend = str((services.get("backend") or {}).get("image") or "")
frontend = str((services.get("frontend") or {}).get("image") or "")
for name, image in (("backend", backend), ("frontend", frontend)):
    if not pattern.fullmatch(image):
        raise SystemExit(f"{name} image must be a registry reference pinned by sha256 digest")
backend_env = (services.get("backend") or {}).get("environment") or {}
if backend_env.get("AIOPS_RELEASE_DIGEST") != backend.rsplit("@", 1)[1]:
    raise SystemExit("AIOPS_RELEASE_DIGEST does not match the backend image reference")
print(backend)
print(frontend)
PY

BACKEND_IMAGE_REF="$(sed -n '1p' "$TMP_DIR/images.txt")"
FRONTEND_IMAGE_REF="$(sed -n '2p' "$TMP_DIR/images.txt")"

verify_image() {
  local image_ref="$1"
  cosign verify \
    --certificate-identity "$CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    "$image_ref" > /dev/null
  cosign verify-attestation \
    --type slsaprovenance \
    --certificate-identity "$CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    "$image_ref" > /dev/null
  cosign verify-attestation \
    --type spdxjson \
    --certificate-identity "$CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    "$image_ref" > /dev/null
  docker pull "$image_ref" > /dev/null
}

verify_image "$BACKEND_IMAGE_REF"
verify_image "$FRONTEND_IMAGE_REF"
echo "signed release verification passed for both immutable image digests"

if [ "$ACTION" = "up" ]; then
  "${COMPOSE[@]}" up -d --no-build
  echo "production Compose started from verified image digests"
fi
