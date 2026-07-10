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
cosign version 2>&1 | grep -Eq 'GitVersion:[[:space:]]+v3\.1\.1([[:space:]]|$)' || {
  echo "cosign v3.1.1 is required by the reviewed release policy" >&2
  exit 1
}
CERTIFICATE_IDENTITY="${AIOPS_COSIGN_CERTIFICATE_IDENTITY:?set the exact release workflow certificate identity}"
OIDC_ISSUER="${AIOPS_COSIGN_OIDC_ISSUER:?set the expected release OIDC issuer}"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$ROOT_DIR/deploy/compose.prod.yml")
if [ "$SETUP" = "1" ]; then
  COMPOSE+=(-f "$ROOT_DIR/deploy/compose.setup.yml")
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/aiops-release-verify.XXXXXX")"
STARTED=0
cleanup() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ] && [ "$STARTED" = "1" ]; then
    echo "post-start verification failed; stopping the unverified deployment" >&2
    "${COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1 || \
      echo "CRITICAL: failed to stop the unverified deployment" >&2
  fi
  rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT
"${COMPOSE[@]}" config --format json > "$TMP_DIR/compose.json"

python3 - "$TMP_DIR/compose.json" "$SETUP" > "$TMP_DIR/images.txt" <<'PY'
import json
import os
import re
import stat
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

expected_secret_owners = {
    "session_secret": 10001,
    "llm_api_key": 10001,
    "llm_evidence": 10001,
    "tls_cert": 101,
    "tls_key": 101,
}
if sys.argv[2] == "1":
    expected_secret_owners["setup_token"] = 10001
secrets = payload.get("secrets") or {}
for name, expected_uid in expected_secret_owners.items():
    source = str((secrets.get(name) or {}).get("file") or "")
    if not source or os.path.islink(source):
        raise SystemExit(f"{name} must reference a non-symlink secret file")
    try:
        metadata = os.stat(source)
    except OSError as exc:
        raise SystemExit(f"{name} secret file cannot be read: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(f"{name} secret source must be a regular file")
    if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) != 0o400:
        raise SystemExit(
            f"{name} must be owned by UID {expected_uid} with mode 0400"
        )
ports = (services.get("frontend") or {}).get("ports") or []
if len(ports) != 1:
    raise SystemExit("frontend must publish exactly one HTTPS port")
published = ports[0]
commit = str(backend_env.get("AIOPS_COMMIT_SHA") or "")
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("AIOPS_COMMIT_SHA must be a full lowercase Git commit")
print(backend)
print(frontend)
print(published.get("host_ip") or "")
print(published.get("published") or "")
print(commit)
project_name = str(payload.get("name") or "")
if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project_name):
    raise SystemExit("Compose project name is missing or invalid")
print(project_name)
PY

BACKEND_IMAGE_REF="$(sed -n '1p' "$TMP_DIR/images.txt")"
FRONTEND_IMAGE_REF="$(sed -n '2p' "$TMP_DIR/images.txt")"
HTTPS_BIND="$(sed -n '3p' "$TMP_DIR/images.txt")"
HTTPS_PORT="$(sed -n '4p' "$TMP_DIR/images.txt")"
COMMIT_SHA="$(sed -n '5p' "$TMP_DIR/images.txt")"
PROJECT_NAME="$(sed -n '6p' "$TMP_DIR/images.txt")"

topology_args=(compose "$TMP_DIR/compose.json" --https-bind "$HTTPS_BIND" --https-port "$HTTPS_PORT")
if [ "$SETUP" = "1" ]; then
  topology_args+=(--setup)
fi
"$ROOT_DIR/scripts/assert-production-topology.py" "${topology_args[@]}"

verify_image() {
  local image_ref="$1"
  local label="$2"
  local provenance="$TMP_DIR/$label-provenance.json"
  local sbom="$TMP_DIR/$label-sbom.json"
  cosign verify \
    --certificate-identity "$CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    "$image_ref" > /dev/null
  cosign verify-attestation \
    --type slsaprovenance1 \
    --certificate-identity "$CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    "$image_ref" > "$provenance"
  cosign verify-attestation \
    --type spdxjson \
    --certificate-identity "$CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$OIDC_ISSUER" \
    "$image_ref" > "$sbom"
  "$ROOT_DIR/scripts/verify-release-attestations.py" \
    --provenance "$provenance" \
    --sbom "$sbom" \
    --image-ref "$image_ref" \
    --commit-sha "$COMMIT_SHA" \
    --certificate-identity "$CERTIFICATE_IDENTITY"
  docker pull "$image_ref" > /dev/null
}

verify_image "$BACKEND_IMAGE_REF" backend
verify_image "$FRONTEND_IMAGE_REF" frontend
echo "signed release verification passed for both immutable image digests"

if [ "$ACTION" = "up" ]; then
  STARTED=1
  "${COMPOSE[@]}" up -d --no-build --remove-orphans
  BACKEND_ID="$("${COMPOSE[@]}" ps -q backend)"
  FRONTEND_ID="$("${COMPOSE[@]}" ps -q frontend)"
  [ -n "$BACKEND_ID" ] && [ -n "$FRONTEND_ID" ] || {
    echo "production services did not create both expected containers" >&2
    exit 1
  }
  mapfile -t PROJECT_CONTAINER_IDS < <(
    docker ps -aq --filter "label=com.docker.compose.project=$PROJECT_NAME" | sort
  )
  mapfile -t EXPECTED_CONTAINER_IDS < <(printf '%s\n' "$BACKEND_ID" "$FRONTEND_ID" | sort)
  [ "${#PROJECT_CONTAINER_IDS[@]}" -eq 2 ] && \
    [ "${PROJECT_CONTAINER_IDS[*]}" = "${EXPECTED_CONTAINER_IDS[*]}" ] || {
      echo "Compose project contains containers outside the backend/frontend allowlist" >&2
      exit 1
    }
  docker inspect "${PROJECT_CONTAINER_IDS[@]}" > "$TMP_DIR/runtime-inspect.json"
  runtime_args=(runtime "$TMP_DIR/runtime-inspect.json" --https-bind "$HTTPS_BIND" --https-port "$HTTPS_PORT")
  if [ "$SETUP" = "1" ]; then
    runtime_args+=(--setup)
  fi
  "$ROOT_DIR/scripts/assert-production-topology.py" "${runtime_args[@]}"
  STARTED=0
  echo "production Compose started from verified image digests"
fi
