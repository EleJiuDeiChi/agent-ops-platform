#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${AIOPS_R0_VM_STATE_ROOT:-$HOME/.cache/agent-ops-r0-vm}"
IMAGE_ROOT="${AIOPS_R0_VM_IMAGE_ROOT:-/var/lib/libvirt/images/agent-ops-r0}"
KEEP_VMS="${AIOPS_R0_KEEP_VMS:-0}"
VM_MEMORY_MIB="${AIOPS_R0_VM_MEMORY_MIB:-4096}"
VM_VCPUS="${AIOPS_R0_VM_VCPUS:-2}"
VM_DISK_SIZE="${AIOPS_R0_VM_DISK_SIZE:-40G}"
VM_DATA_DISK_SIZE="${AIOPS_R0_VM_DATA_DISK_SIZE:-20G}"
RUN_ID="${AIOPS_R0_RUN_ID:-$(date -u +%Y%m%d%H%M%S)-$$}"
RUN_DIR="$STATE_ROOT/runs/$RUN_ID"
EVIDENCE_DIR="$ROOT_DIR/.omx/evidence/production-ga/GA-R0-001"
SSH_KEY="$RUN_DIR/id_ed25519"
KNOWN_HOSTS="$RUN_DIR/known_hosts"
RUN_LOG="$EVIDENCE_DIR/matrix-$RUN_ID.log"
RUN_LOG_OWNERSHIP_MARKER="$RUN_DIR/run-log-owned"
REGISTRY_NAME="agent-ops-r0-registry-$RUN_ID"
REGISTRY_PORT="${AIOPS_R0_REGISTRY_PORT:-$((55000 + ($$ % 1000)))}"
REGISTRY_IMAGE="${AIOPS_R0_REGISTRY_IMAGE:-registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373}"
CLOUD_KEYRING="/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg"
CLOUD_KEY_FINGERPRINT="D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81"
CLOUD_SIGNING_SUBKEY_FINGERPRINT="4A3CE3CD565D7EB5C810E2B97FF3F408476CF100"
DOCKER_GPG_FINGERPRINT="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"

COMMIT_SHA=""
SOURCE_ARCHIVE=""
SOURCE_ARCHIVE_SHA256=""
SOURCE_DIR="$RUN_DIR/source"
HOST_BACKEND_IMAGE=""
HOST_FRONTEND_IMAGE=""
HOST_TEST_IMAGE=""
HOST_BACKEND_DIGEST=""
HOST_FRONTEND_DIGEST=""
UPSTREAM_BACKEND_IMAGE_REF=""
UPSTREAM_FRONTEND_IMAGE_REF=""
RELEASE_MANIFEST=""
RELEASE_MANIFEST_SHA256=""
RELEASE_MANIFEST_EVIDENCE_PATH=""
RELEASE_TAG=""
RELEASE_CERTIFICATE_IDENTITY=""
RELEASE_OIDC_ISSUER=""
BACKEND_COSIGN_SHA256=""
FRONTEND_COSIGN_SHA256=""
BACKEND_PROVENANCE_SHA256=""
FRONTEND_PROVENANCE_SHA256=""
BACKEND_SBOM_SHA256=""
FRONTEND_SBOM_SHA256=""
LIBVIRT_GATEWAY=""
HOST_REGISTRY=""
VM_REGISTRY=""
REGISTRY_TAG=""
CURRENT_VM_NAME=""
CURRENT_VM_IP=""
REGISTRY_STARTED=0
REGISTRY_OWNED=0
LOG_TEE_PID=""
LOG_TEE_FINALIZED=0
LOG_TEE_ACTIVE=0
RUN_INITIALIZED=0
RUN_DIR_OWNED=0
RUN_LOG_OWNED=0
MATRIX_OWNS_STATUS=0
SUMMARY_GENERATOR_SHA256=""
STAGED_SUMMARY_SHA256=""
STAGED_STATUS_SHA256=""

MATRIX=(
  "ubuntu2204-ext4|jammy|ubuntu22.04|22.04|28.5.2|5:28.5.2-1~ubuntu.22.04~jammy|2.2.5-1~ubuntu.22.04~jammy|0.35.0-1~ubuntu.22.04~jammy|5.3.1-1~ubuntu.22.04~jammy|5.3.1|ext4"
  "ubuntu2204-xfs|jammy|ubuntu22.04|22.04|28.5.2|5:28.5.2-1~ubuntu.22.04~jammy|2.2.5-1~ubuntu.22.04~jammy|0.35.0-1~ubuntu.22.04~jammy|5.3.1-1~ubuntu.22.04~jammy|5.3.1|xfs"
  "ubuntu2404-ext4|noble|ubuntu24.04|24.04|29.6.1|5:29.6.1-1~ubuntu.24.04~noble|2.2.5-1~ubuntu.24.04~noble|0.35.0-1~ubuntu.24.04~noble|5.3.1-1~ubuntu.24.04~noble|5.3.1|ext4"
  "ubuntu2404-xfs|noble|ubuntu24.04|24.04|29.6.1|5:29.6.1-1~ubuntu.24.04~noble|2.2.5-1~ubuntu.24.04~noble|0.35.0-1~ubuntu.24.04~noble|5.3.1-1~ubuntu.24.04~noble|5.3.1|xfs"
)

fail() {
  echo "R0 clean-VM matrix failed: $*" >&2
  exit 1
}

retry_host_command() {
  local attempt
  for attempt in 1 2 3; do
    "$@" && return 0
    echo "host command failed on attempt $attempt/3; retrying" >&2
    sleep "$((attempt * 2))"
  done
  return 1
}

require_host() {
  local command_name
  [ "$(uname -s)" = "Linux" ] || fail "this harness requires a Linux KVM host"
  [ -e /dev/kvm ] || fail "/dev/kvm is unavailable"
  sudo -n true 2>/dev/null || fail "passwordless sudo is required on the lab host"
  for command_name in cosign curl docker flock gpg gpgv mkfifo python3 qemu-img rsync scp sha256sum ssh ssh-keygen ss tar tee virt-install; do
    command -v "$command_name" >/dev/null || fail "$command_name is required"
  done
  cosign version 2>&1 | grep -F 'v3.1.1' >/dev/null || fail "Cosign 3.1.1 is required"
  docker buildx version >/dev/null 2>&1 || fail "Docker Buildx is required"
  if ! command -v cloud-localds >/dev/null || [ ! -f "$CLOUD_KEYRING" ]; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
      cloud-image-utils ubuntu-cloudimage-keyring
  fi
  [[ "$RUN_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,39}$ ]] || fail "AIOPS_R0_RUN_ID has an invalid format"
  [[ "$REGISTRY_PORT" =~ ^[0-9]+$ ]] || fail "AIOPS_R0_REGISTRY_PORT must be numeric"
  [ "$REGISTRY_PORT" -ge 1024 ] && [ "$REGISTRY_PORT" -le 65535 ] || fail "registry port is out of range"
  [ "$KEEP_VMS" = "0" ] || [ "$KEEP_VMS" = "1" ] || fail "AIOPS_R0_KEEP_VMS must be 0 or 1"
  [[ "$REGISTRY_IMAGE" =~ ^[^@[:space:]]+@sha256:[0-9a-f]{64}$ ]] || fail "registry image must be pinned by sha256 digest"
  sudo virsh net-info default >/dev/null 2>&1 || fail "libvirt default network is missing"
  if [ "$(sudo virsh net-info default | awk '/Active:/ {print $2}')" != "yes" ]; then
    sudo virsh net-start default >/dev/null
  fi
  LIBVIRT_GATEWAY="$(sudo virsh net-dumpxml default | sed -n "s/.*<ip address='\([^']*\)'.*/\1/p" | head -1)"
  [ -n "$LIBVIRT_GATEWAY" ] || fail "could not resolve the libvirt default-network gateway"
  HOST_REGISTRY="127.0.0.1:$REGISTRY_PORT"
  VM_REGISTRY="$LIBVIRT_GATEWAY:$REGISTRY_PORT"
  if ss -H -ltn | awk -v suffix=":$REGISTRY_PORT" '$4 ~ suffix "$" {found=1} END {exit !found}'; then
    fail "registry port $REGISTRY_PORT is already in use"
  fi
  for fingerprint in "$CLOUD_KEY_FINGERPRINT" "$CLOUD_SIGNING_SUBKEY_FINGERPRINT"; do
    gpg --batch --no-default-keyring --keyring "$CLOUD_KEYRING" --with-colons --list-keys | \
      awk -F: '$1 == "fpr" {print $10}' | grep -Fx "$fingerprint" >/dev/null || \
      fail "Ubuntu cloud-image keyring is missing fingerprint $fingerprint"
  done
}

prepare_state() {
  mkdir -p "$STATE_ROOT/cache" "$STATE_ROOT/runs" "$EVIDENCE_DIR"
  exec 9>"$EVIDENCE_DIR/.matrix.lock"
  flock -n 9 || fail "another R0 clean-VM matrix is already running"
  if sudo virsh list --all --name | grep -Eq '^agent-ops-r0-'; then
    fail "stale agent-ops-r0 VM exists; preserve or clean the prior forensic run before retrying"
  fi
  if docker ps -a --format '{{.Names}}' | grep -Eq '^agent-ops-r0-registry-'; then
    fail "stale agent-ops-r0 registry exists; clean the prior forensic run before retrying"
  fi
  if find "$STATE_ROOT/runs" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    fail "stale agent-ops-r0 run directory exists; clean or archive it before retrying"
  fi
  if sudo find "$IMAGE_ROOT" -maxdepth 1 -type f \
      \( -name 'agent-ops-r0-*.qcow2' -o -name 'agent-ops-r0-*-seed.img' \) \
      -print -quit | grep -q .; then
    fail "stale agent-ops-r0 VM disk exists without an active run; inspect and clean it before retrying"
  fi
  RUN_DIR_OWNED=1
  mkdir -p "$RUN_DIR"
  : > "$KNOWN_HOSTS"
  chmod 0600 "$KNOWN_HOSTS"
  ssh-keygen -q -t ed25519 -N '' -f "$SSH_KEY"
  sudo install -d -m 0755 "$IMAGE_ROOT"
  python3 - "$RUN_LOG" "$RUN_LOG_OWNERSHIP_MARKER" <<'PY' || \
    fail "run log path already exists or could not be created safely for this run ID"
import os
import sys

path, marker = sys.argv[1:]
no_follow = getattr(os, "O_NOFOLLOW", 0)
if no_follow == 0:
    raise SystemExit("O_NOFOLLOW is unavailable")
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow, 0o600)
try:
    stat = os.fstat(descriptor)
    os.fsync(descriptor)
    marker_descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow, 0o600)
    try:
        os.write(marker_descriptor, f"{stat.st_dev}:{stat.st_ino}\n".encode())
        os.fsync(marker_descriptor)
    finally:
        os.close(marker_descriptor)
except BaseException:
    os.close(descriptor)
    os.unlink(path)
    raise
else:
    os.close(descriptor)
PY
  RUN_LOG_OWNED=1
  exec 3>&1 4>&2
  mkfifo "$RUN_DIR/run-log.pipe"
  tee -a "$RUN_LOG" < "$RUN_DIR/run-log.pipe" >&3 &
  LOG_TEE_PID="$!"
  if ! exec > "$RUN_DIR/run-log.pipe" 2>&1; then
    kill "$LOG_TEE_PID" >/dev/null 2>&1 || true
    wait "$LOG_TEE_PID" >/dev/null 2>&1 || true
    return 1
  fi
  LOG_TEE_ACTIVE=1
  RUN_INITIALIZED=1
}

finalize_run_log() {
  [ "$LOG_TEE_FINALIZED" = "0" ] || return 0
  LOG_TEE_FINALIZED=1
  if [ -z "$LOG_TEE_PID" ]; then
    return 0
  fi
  if [ "$LOG_TEE_ACTIVE" = "1" ]; then
    exec 1>&3 2>&4
    LOG_TEE_ACTIVE=0
  else
    kill "$LOG_TEE_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "$LOG_TEE_PID" ]; then
    wait "$LOG_TEE_PID" || return 1
  fi
  [ ! -f "$RUN_LOG" ] || sync "$RUN_LOG"
}

validate_source_archive() {
  python3 - "$SOURCE_ARCHIVE" <<'PY'
import pathlib
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
with tarfile.open(archive, "r:*") as payload:
    for member in payload.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe source archive path: {member.name}")
        if member.issym() or member.islnk():
            target = pathlib.PurePosixPath(member.linkname)
            if target.is_absolute() or ".." in target.parts:
                raise SystemExit(f"unsafe source archive link: {member.name}")
PY
}

prepare_source() {
  local actual_commit supplied_archive supplied_sha relative source_hash running_hash
  supplied_archive="${AIOPS_SOURCE_ARCHIVE:-}"
  supplied_sha="${AIOPS_SOURCE_ARCHIVE_SHA256:-}"
  if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    [ -z "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ] || \
      fail "the source worktree must be clean before creating clean-VM evidence"
    actual_commit="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)"
    if [ -n "${AIOPS_SOURCE_COMMIT:-}" ] && [ "$AIOPS_SOURCE_COMMIT" != "$actual_commit" ]; then
      fail "AIOPS_SOURCE_COMMIT does not match the checked-out HEAD"
    fi
    COMMIT_SHA="$actual_commit"
    SOURCE_ARCHIVE="$RUN_DIR/source-$COMMIT_SHA.tar"
    git -C "$ROOT_DIR" archive --format=tar \
      "--add-virtual-file=.aiops-source-commit:$COMMIT_SHA" \
      --output "$SOURCE_ARCHIVE" "$COMMIT_SHA"
  else
    COMMIT_SHA="${AIOPS_SOURCE_COMMIT:-}"
    [ -n "$COMMIT_SHA" ] || fail "AIOPS_SOURCE_COMMIT is required without a Git checkout"
    [ -n "$supplied_archive" ] || fail "AIOPS_SOURCE_ARCHIVE is required without a Git checkout"
    [ -f "$supplied_archive" ] || fail "AIOPS_SOURCE_ARCHIVE is not a readable file"
    [ -n "$supplied_sha" ] || fail "AIOPS_SOURCE_ARCHIVE_SHA256 is required without a Git checkout"
    SOURCE_ARCHIVE="$RUN_DIR/source-$COMMIT_SHA.tar"
    if [ "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$supplied_archive")" != \
         "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$SOURCE_ARCHIVE")" ]; then
      cp "$supplied_archive" "$SOURCE_ARCHIVE"
    fi
  fi
  [[ "$COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "source commit must be a full lowercase 40-character SHA"
  SOURCE_ARCHIVE_SHA256="$(sha256sum "$SOURCE_ARCHIVE" | awk '{print $1}')"
  if [ -n "$supplied_sha" ] && [ "$supplied_sha" != "$SOURCE_ARCHIVE_SHA256" ]; then
    fail "source archive SHA-256 does not match AIOPS_SOURCE_ARCHIVE_SHA256"
  fi
  validate_source_archive
  mkdir -p "$SOURCE_DIR"
  tar -xf "$SOURCE_ARCHIVE" -C "$SOURCE_DIR"
  [ "$(<"$SOURCE_DIR/.aiops-source-commit")" = "$COMMIT_SHA" ] || fail "source archive commit marker does not match"
  for relative in scripts/test-r0-clean-vm-matrix.sh scripts/generate-r0-matrix-summary.py scripts/validate-ga-manifest.py scripts/verify-release-attestations.py; do
    source_hash="$(sha256sum "$SOURCE_DIR/$relative" | awk '{print $1}')"
    running_hash="$(sha256sum "$ROOT_DIR/$relative" | awk '{print $1}')"
    [ "$source_hash" = "$running_hash" ] || fail "running $relative does not match the source archive"
    if [ "$relative" = "scripts/generate-r0-matrix-summary.py" ]; then
      SUMMARY_GENERATOR_SHA256="$source_hash"
    fi
  done
  HOST_BACKEND_IMAGE="${AIOPS_R0_HOST_BACKEND_IMAGE:-agent-ops-backend:r0-matrix-$RUN_ID}"
  HOST_FRONTEND_IMAGE="${AIOPS_R0_HOST_FRONTEND_IMAGE:-agent-ops-frontend:r0-matrix-$RUN_ID}"
  HOST_TEST_IMAGE="${AIOPS_R0_HOST_TEST_IMAGE:-agent-ops-backend-test:r0-matrix-$RUN_ID}"
  REGISTRY_TAG="$COMMIT_SHA-$RUN_ID"
}

prepare_signed_release() {
  local -a release_fields
  local expected_repository="${AIOPS_R0_EXPECTED_REPOSITORY:-}"
  [[ "$expected_repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || \
    fail "AIOPS_R0_EXPECTED_REPOSITORY must identify the trusted owner/repository"
  RELEASE_MANIFEST="${AIOPS_R0_RELEASE_MANIFEST:-}"
  [ -n "$RELEASE_MANIFEST" ] && [ -f "$RELEASE_MANIFEST" ] || \
    fail "AIOPS_R0_RELEASE_MANIFEST must point to the canonical GA-R0-002 manifest"
  RELEASE_MANIFEST="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$RELEASE_MANIFEST")"
  "$SOURCE_DIR/scripts/validate-ga-manifest.py" \
    --expected-repository "$expected_repository" "$RELEASE_MANIFEST" || \
    fail "GA-R0-002 release manifest is invalid"
  mapfile -t release_fields < <(python3 - "$RELEASE_MANIFEST" "$COMMIT_SHA" "$expected_repository" <<'PY'
import json
import re
import sys
from pathlib import Path

path, commit, expected_repository = sys.argv[1:]
payload = json.loads(Path(path).read_text(encoding="utf-8"))
if payload.get("commit_sha") != commit:
    raise SystemExit("release manifest commit does not match the source archive")
if payload.get("repository") != expected_repository:
    raise SystemExit("release manifest repository does not match trusted configuration")
tag_signature = payload.get("tag_signature")
if not isinstance(tag_signature, dict) or tag_signature.get("verified") is not True:
    raise SystemExit("release tag signature is not verified")
if tag_signature.get("format") != "ssh":
    raise SystemExit("release tag signature must use SSH signing")
tag = str(payload.get("release_tag") or "")
if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
    raise SystemExit("release tag is not semantic")
for name in ("backend", "frontend"):
    image = payload.get(name)
    if not isinstance(image, dict):
        raise SystemExit(f"{name} image metadata is missing")
    print(f"{image['image']}@{image['digest']}")
print(tag)
PY
  )
  [ "${#release_fields[@]}" -eq 3 ] || fail "release manifest fields are incomplete"
  UPSTREAM_BACKEND_IMAGE_REF="${release_fields[0]}"
  UPSTREAM_FRONTEND_IMAGE_REF="${release_fields[1]}"
  RELEASE_TAG="${release_fields[2]}"
  RELEASE_CERTIFICATE_IDENTITY="https://github.com/${expected_repository}/.github/workflows/r0-ci.yml@refs/tags/${RELEASE_TAG}"
  RELEASE_OIDC_ISSUER="https://token.actions.githubusercontent.com"
  HOST_BACKEND_DIGEST="${UPSTREAM_BACKEND_IMAGE_REF##*@}"
  HOST_FRONTEND_DIGEST="${UPSTREAM_FRONTEND_IMAGE_REF##*@}"
  RELEASE_MANIFEST_EVIDENCE_PATH="$EVIDENCE_DIR/release-manifest-$RUN_ID.json"
  cp "$RELEASE_MANIFEST" "$RELEASE_MANIFEST_EVIDENCE_PATH"
  chmod 0600 "$RELEASE_MANIFEST_EVIDENCE_PATH"
  RELEASE_MANIFEST_SHA256="sha256:$(sha256sum "$RELEASE_MANIFEST_EVIDENCE_PATH" | awk '{print $1}')"
  [[ "$HOST_BACKEND_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "backend release digest is invalid"
  [[ "$HOST_FRONTEND_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "frontend release digest is invalid"
  local backend_cosign="$EVIDENCE_DIR/backend-cosign-$RUN_ID.json"
  local frontend_cosign="$EVIDENCE_DIR/frontend-cosign-$RUN_ID.json"
  local backend_provenance="$EVIDENCE_DIR/backend-provenance-$RUN_ID.json"
  local frontend_provenance="$EVIDENCE_DIR/frontend-provenance-$RUN_ID.json"
  local backend_sbom="$EVIDENCE_DIR/backend-sbom-$RUN_ID.json"
  local frontend_sbom="$EVIDENCE_DIR/frontend-sbom-$RUN_ID.json"
  cosign verify --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$RELEASE_OIDC_ISSUER" --output json \
    "$UPSTREAM_BACKEND_IMAGE_REF" > "$backend_cosign"
  cosign verify --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$RELEASE_OIDC_ISSUER" --output json \
    "$UPSTREAM_FRONTEND_IMAGE_REF" > "$frontend_cosign"
  cosign verify-attestation --type slsaprovenance1 \
    --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$RELEASE_OIDC_ISSUER" \
    "$UPSTREAM_BACKEND_IMAGE_REF" > "$backend_provenance"
  cosign verify-attestation --type slsaprovenance1 \
    --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$RELEASE_OIDC_ISSUER" \
    "$UPSTREAM_FRONTEND_IMAGE_REF" > "$frontend_provenance"
  cosign verify-attestation --type spdxjson \
    --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$RELEASE_OIDC_ISSUER" \
    "$UPSTREAM_BACKEND_IMAGE_REF" > "$backend_sbom"
  cosign verify-attestation --type spdxjson \
    --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY" \
    --certificate-oidc-issuer "$RELEASE_OIDC_ISSUER" \
    "$UPSTREAM_FRONTEND_IMAGE_REF" > "$frontend_sbom"
  "$SOURCE_DIR/scripts/verify-release-attestations.py" \
    --provenance "$backend_provenance" --sbom "$backend_sbom" \
    --image-ref "$UPSTREAM_BACKEND_IMAGE_REF" --commit-sha "$COMMIT_SHA" \
    --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY"
  "$SOURCE_DIR/scripts/verify-release-attestations.py" \
    --provenance "$frontend_provenance" --sbom "$frontend_sbom" \
    --image-ref "$UPSTREAM_FRONTEND_IMAGE_REF" --commit-sha "$COMMIT_SHA" \
    --certificate-identity "$RELEASE_CERTIFICATE_IDENTITY"
  chmod 0600 "$backend_cosign" "$frontend_cosign" \
    "$backend_provenance" "$frontend_provenance" "$backend_sbom" "$frontend_sbom"
  BACKEND_COSIGN_SHA256="sha256:$(sha256sum "$backend_cosign" | awk '{print $1}')"
  FRONTEND_COSIGN_SHA256="sha256:$(sha256sum "$frontend_cosign" | awk '{print $1}')"
  BACKEND_PROVENANCE_SHA256="sha256:$(sha256sum "$backend_provenance" | awk '{print $1}')"
  FRONTEND_PROVENANCE_SHA256="sha256:$(sha256sum "$frontend_provenance" | awk '{print $1}')"
  BACKEND_SBOM_SHA256="sha256:$(sha256sum "$backend_sbom" | awk '{print $1}')"
  FRONTEND_SBOM_SHA256="sha256:$(sha256sum "$frontend_sbom" | awk '{print $1}')"
}

build_test_image() {
  retry_host_command docker build --pull --tag "$HOST_TEST_IMAGE" --file "$SOURCE_DIR/backend/Dockerfile.test" "$SOURCE_DIR"
}

cleanup_registry() {
  local attempt backend_ref frontend_ref
  if [ "$REGISTRY_OWNED" = "0" ]; then
    return 0
  fi
  backend_ref="$HOST_REGISTRY/agent-ops-backend:$REGISTRY_TAG"
  frontend_ref="$HOST_REGISTRY/agent-ops-frontend:$REGISTRY_TAG"
  if docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
    docker rm -f "$REGISTRY_NAME" >/dev/null
  fi
  ! docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1 || return 1
  docker image rm "$backend_ref" "$frontend_ref" >/dev/null 2>&1 || true
  ! docker image inspect "$backend_ref" >/dev/null 2>&1 || return 1
  ! docker image inspect "$frontend_ref" >/dev/null 2>&1 || return 1
  for attempt in $(seq 1 20); do
    if ! ss -H -ltn | awk -v suffix=":$REGISTRY_PORT" '$4 ~ suffix "$" {found=1} END {exit !found}'; then
      REGISTRY_STARTED=0
      REGISTRY_OWNED=0
      return 0
    fi
    sleep 0.25
  done
  return 1
}

cleanup_success_image_artifacts() {
  local image
  for image in "$HOST_TEST_IMAGE"; do
    [ -n "$image" ] || continue
    docker image rm "$image" >/dev/null 2>&1 || true
    ! docker image inspect "$image" >/dev/null 2>&1 || \
      fail "successful run left diagnostic image tag $image"
  done
}

cleanup_success_run_artifacts() {
  if [ "$KEEP_VMS" = "0" ]; then
    rm -rf "$RUN_DIR" || return 1
    [ ! -e "$RUN_DIR" ] || return 1
    RUN_DIR_OWNED=0
  fi
}

mirror_release_images() {
  local backend_ref="$HOST_REGISTRY/agent-ops-backend:$REGISTRY_TAG"
  local frontend_ref="$HOST_REGISTRY/agent-ops-frontend:$REGISTRY_TAG"
  retry_host_command docker pull "$REGISTRY_IMAGE" >/dev/null
  REGISTRY_OWNED=1
  docker run --detach --rm \
    --name "$REGISTRY_NAME" \
    --publish "127.0.0.1:$REGISTRY_PORT:5000" \
    --publish "$LIBVIRT_GATEWAY:$REGISTRY_PORT:5000" \
    "$REGISTRY_IMAGE" >/dev/null
  REGISTRY_STARTED=1
  for _ in $(seq 1 30); do
    curl --fail --silent "http://127.0.0.1:$REGISTRY_PORT/v2/" >/dev/null && break
    sleep 1
  done
  curl --fail --silent "http://127.0.0.1:$REGISTRY_PORT/v2/" >/dev/null || \
    fail "ephemeral OCI registry did not become ready"
  retry_host_command docker buildx imagetools create \
    --tag "$backend_ref" "$UPSTREAM_BACKEND_IMAGE_REF" >/dev/null
  retry_host_command docker buildx imagetools create \
    --tag "$frontend_ref" "$UPSTREAM_FRONTEND_IMAGE_REF" >/dev/null
  docker buildx imagetools inspect "${backend_ref%:*}@$HOST_BACKEND_DIGEST" >/dev/null
  docker buildx imagetools inspect "${frontend_ref%:*}@$HOST_FRONTEND_DIGEST" >/dev/null
}

download_verified_cloud_image() {
  local release="$1"
  local filename="${release}-server-cloudimg-amd64.img"
  local base_url="https://cloud-images.ubuntu.com/${release}/current"
  local cache_image="$STATE_ROOT/cache/$filename"
  local sums="$RUN_DIR/${release}-SHA256SUMS"
  local signature="$RUN_DIR/${release}-SHA256SUMS.gpg"
  local checksum_line

  curl --fail --location --retry 3 "$base_url/SHA256SUMS" --output "$sums"
  curl --fail --location --retry 3 "$base_url/SHA256SUMS.gpg" --output "$signature"
  local gpg_status="$RUN_DIR/${release}-gpgv.status"
  gpgv --status-fd 1 --keyring "$CLOUD_KEYRING" "$signature" "$sums" \
    >"$gpg_status" 2>/dev/null || \
    fail "Ubuntu signature verification failed for $release SHA256SUMS"
  python3 - "$gpg_status" "$CLOUD_SIGNING_SUBKEY_FINGERPRINT" "$CLOUD_KEY_FINGERPRINT" <<'PY' || \
    fail "Ubuntu SHA256SUMS signer did not match the pinned primary/subkey policy"
import sys
from pathlib import Path

status_path, expected_subkey, expected_primary = sys.argv[1:]
valid = []
for line in Path(status_path).read_text(encoding="utf-8").splitlines():
    fields = line.split()
    if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
        valid.append((fields[2], fields[-1]))
if len(valid) != 1 or valid[0][0] not in {expected_primary, expected_subkey} or valid[0][1] != expected_primary:
    raise SystemExit(f"unexpected Ubuntu SHA256SUMS signer: {valid!r}")
PY
  checksum_line="$(grep -E "[ *]${filename}$" "$sums")"
  [ "$(printf '%s\n' "$checksum_line" | wc -l | tr -d ' ')" = "1" ] || \
    fail "official SHA256SUMS did not contain exactly one $filename entry"
  if [ ! -f "$cache_image" ]; then
    curl --fail --location --retry 3 --continue-at - "$base_url/$filename" --output "$cache_image"
  fi
  if ! printf '%s\n' "$checksum_line" | (cd "$STATE_ROOT/cache" && sha256sum --check --status -); then
    rm -f "$cache_image"
    curl --fail --location --retry 3 "$base_url/$filename" --output "$cache_image"
    printf '%s\n' "$checksum_line" | (cd "$STATE_ROOT/cache" && sha256sum --check --status -) || \
      fail "official SHA-256 verification failed for $filename"
  fi
  sudo install -m 0644 "$cache_image" "$IMAGE_ROOT/$filename"
  printf '%s\n' "$IMAGE_ROOT/$filename"
}

remove_namespaced_vm() {
  local name="$1"
  local state
  sudo virsh dominfo "$name" >/dev/null 2>&1 || fail "expected VM $name does not exist"
  state="$(sudo virsh domstate "$name" | tr -d '\r')"
  if [ "$state" != "shut off" ]; then
    sudo virsh destroy "$name" >/dev/null
  fi
  if ! sudo virsh undefine "$name" --nvram >/dev/null 2>&1; then
    sudo virsh undefine "$name" >/dev/null
  fi
  ! sudo virsh dominfo "$name" >/dev/null 2>&1 || fail "VM $name still exists after undefine"
  sudo rm -f "$IMAGE_ROOT/$name.qcow2" "$IMAGE_ROOT/$name-data.qcow2" "$IMAGE_ROOT/$name-seed.img"
  rm -f "$RUN_DIR/$name-user-data.yaml" "$RUN_DIR/$name-meta-data.yaml" "$RUN_DIR/$name-seed.img"
  [ ! -e "$IMAGE_ROOT/$name.qcow2" ] && \
    [ ! -e "$IMAGE_ROOT/$name-data.qcow2" ] && \
    [ ! -e "$IMAGE_ROOT/$name-seed.img" ] || \
    fail "VM $name disk artifacts were not removed"
}

create_cloud_init_seed() {
  local name="$1"
  local user_data="$RUN_DIR/$name-user-data.yaml"
  local meta_data="$RUN_DIR/$name-meta-data.yaml"
  local seed="$RUN_DIR/$name-seed.img"
  local public_key
  public_key="$(<"$SSH_KEY.pub")"

  cat > "$user_data" <<EOF
#cloud-config
hostname: $name
manage_etc_hosts: true
ssh_pwauth: false
disable_root: true
users:
  - name: aiops
    groups: [adm, sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - $public_key
package_update: true
packages:
  - ca-certificates
  - curl
  - gnupg
  - python3-venv
  - qemu-guest-agent
  - rsync
  - xfsprogs
runcmd:
  - [systemctl, enable, --now, qemu-guest-agent]
EOF
  cat > "$meta_data" <<EOF
instance-id: $name
local-hostname: $name
EOF
  cloud-localds "$seed" "$user_data" "$meta_data"
  sudo install -m 0644 "$seed" "$IMAGE_ROOT/$name-seed.img"
}

create_vm() {
  local name="$1"
  local base_image="$2"
  local os_variant="$3"
  local data_filesystem="$4"
  local disk="$IMAGE_ROOT/$name.qcow2"
  local data_disk="$IMAGE_ROOT/$name-data.qcow2"
  local -a data_disk_args=()

  ! sudo virsh dominfo "$name" >/dev/null 2>&1 || fail "refusing to replace existing VM $name"
  [ ! -e "$disk" ] && [ ! -e "$data_disk" ] && [ ! -e "$IMAGE_ROOT/$name-seed.img" ] || \
    fail "refusing to replace existing disk artifacts for $name"
  case "$data_filesystem" in
    ext4) ;;
    xfs)
      sudo qemu-img create -q -f qcow2 "$data_disk" "$VM_DATA_DISK_SIZE"
      sudo chown libvirt-qemu:kvm "$data_disk"
      data_disk_args=(--disk "path=$data_disk,format=qcow2,bus=virtio,target=vdb,serial=aiops-r0-data,cache=none,discard=unmap")
      ;;
    *) fail "unsupported clean-VM data filesystem: $data_filesystem" ;;
  esac
  sudo qemu-img create -q -f qcow2 -F qcow2 -b "$base_image" "$disk" "$VM_DISK_SIZE"
  sudo chown libvirt-qemu:kvm "$disk"
  create_cloud_init_seed "$name"
  sudo virt-install \
    --connect qemu:///system \
    --name "$name" \
    --memory "$VM_MEMORY_MIB" \
    --vcpus "$VM_VCPUS" \
    --cpu host \
    --import \
    --os-variant "$os_variant" \
    --disk "path=$disk,format=qcow2,bus=virtio,target=vda,cache=none,discard=unmap" \
    "${data_disk_args[@]}" \
    --disk "path=$IMAGE_ROOT/$name-seed.img,device=cdrom,readonly=on" \
    --network network=default,model=virtio \
    --graphics none \
    --console pty,target_type=serial \
    --noautoconsole >/dev/null
}

prepare_vm_data_filesystem() {
  local ip="$1"
  local expected_filesystem="$2"
  local expected_data_disk_size="$3"
  local -a options
  mapfile -t options < <(ssh_args)
  ssh "${options[@]}" "aiops@$ip" bash -s -- "$expected_filesystem" "$expected_data_disk_size" <<'REMOTE'
set -euo pipefail
expected_filesystem="$1"
expected_data_disk_size="$2"
sudo install -d -m 0711 /var/lib/docker
case "$expected_filesystem" in
  ext4)
    [ "$(findmnt -no FSTYPE -T /var/lib/docker)" = "ext4" ] || {
      echo "/var/lib/docker is not backed by ext4" >&2
      exit 1
    }
    ;;
  xfs)
    sudo udevadm settle
    stable_device=/dev/disk/by-id/virtio-aiops-r0-data
    [ -b "$stable_device" ] || { echo "dedicated xfs data disk identity is missing" >&2; exit 1; }
    data_device="$(readlink -f "$stable_device")"
    [ "$data_device" = "/dev/vdb" ] || { echo "unexpected xfs data device: $data_device" >&2; exit 1; }
    root_source="$(readlink -f "$(findmnt -no SOURCE /)")"
    root_parent="$(lsblk -no PKNAME "$root_source" | head -1)"
    [ -n "$root_parent" ] || { echo "root block device could not be resolved" >&2; exit 1; }
    [ "$data_device" != "/dev/$root_parent" ] || { echo "refusing to format the root disk" >&2; exit 1; }
    [ "$(sudo blockdev --getsize64 "$data_device")" = "$(numfmt --from=iec "$expected_data_disk_size")" ] || {
      echo "xfs data disk size does not match the requested fixture" >&2
      exit 1
    }
    [ "$(lsblk -nrpo TYPE "$data_device")" = "disk" ] || { echo "xfs data disk has partitions" >&2; exit 1; }
    ! find "/sys/class/block/$(basename "$data_device")/holders" -mindepth 1 -print -quit | grep -q . || {
      echo "xfs data disk has active holders" >&2
      exit 1
    }
    ! findmnt "$data_device" >/dev/null 2>&1 || { echo "$data_device is already mounted" >&2; exit 1; }
    [ -z "$(sudo blkid -o value -s TYPE "$data_device" 2>/dev/null || true)" ] || {
      echo "$data_device unexpectedly contains a filesystem" >&2
      exit 1
    }
    sudo mkfs.xfs -f -L aiops-docker "$data_device" >/dev/null
    uuid="$(sudo blkid -o value -s UUID "$data_device")"
    [ -n "$uuid" ] || { echo "xfs data disk UUID could not be resolved" >&2; exit 1; }
    printf 'UUID=%s /var/lib/docker xfs defaults 0 2\n' "$uuid" | sudo tee -a /etc/fstab >/dev/null
    sudo mount /var/lib/docker
    [ "$(findmnt -no FSTYPE -T /var/lib/docker)" = "xfs" ]
    mounted_source="$(readlink -f "$(findmnt -no SOURCE -T /var/lib/docker)")"
    [ "$mounted_source" = "$data_device" ] || { echo "xfs mount source does not match the dedicated disk" >&2; exit 1; }
    [ "$(sudo blkid -o value -s UUID "$mounted_source")" = "$uuid" ] || { echo "xfs mount UUID mismatch" >&2; exit 1; }
    sudo xfs_info /var/lib/docker | grep -Eq 'ftype=1([[:space:]]|$)'
    ;;
  *) echo "unsupported data filesystem: $expected_filesystem" >&2; exit 1 ;;
esac
REMOTE
}

wait_for_vm_ip() {
  local name="$1"
  local mac ip
  mac="$(sudo virsh domiflist "$name" | awk '$2 == "network" {print $5; exit}')"
  [ -n "$mac" ] || fail "could not resolve MAC address for $name"
  for _ in $(seq 1 120); do
    ip="$(sudo virsh net-dhcp-leases default --mac "$mac" 2>/dev/null | awk '/ipv4/ {sub("/.*", "", $5); print $5; exit}')"
    if [ -n "$ip" ]; then
      printf '%s\n' "$ip"
      return 0
    fi
    sleep 2
  done
  fail "timed out waiting for DHCP lease for $name"
}

ssh_args() {
  printf '%s\n' \
    -i "$SSH_KEY" \
    -o "UserKnownHostsFile=$KNOWN_HOSTS" \
    -o StrictHostKeyChecking=accept-new \
    -o BatchMode=yes \
    -o ConnectTimeout=10
}

wait_for_cloud_init() {
  local ip="$1"
  local -a options
  mapfile -t options < <(ssh_args)
  for _ in $(seq 1 180); do
    if ssh "${options[@]}" "aiops@$ip" 'test -f /var/lib/cloud/instance/boot-finished' >/dev/null 2>&1; then
      ssh "${options[@]}" "aiops@$ip" 'sudo cloud-init status --wait >/dev/null; test "$(sudo cloud-init status --format json | python3 -c '\''import json,sys; print(json.load(sys.stdin)["status"])'\'')" = "done"'
      return 0
    fi
    sleep 2
  done
  fail "timed out waiting for cloud-init at $ip"
}

install_docker_exact() {
  local ip="$1"
  local engine_version="$2"
  local docker_package="$3"
  local containerd_package="$4"
  local buildx_package="$5"
  local compose_package="$6"
  local compose_version="$7"
  local -a options
  mapfile -t options < <(ssh_args)
  ssh "${options[@]}" "aiops@$ip" bash -s -- \
    "$engine_version" "$docker_package" "$containerd_package" "$buildx_package" "$compose_package" "$compose_version" "$DOCKER_GPG_FINGERPRINT" <<'REMOTE'
set -euo pipefail
engine_version="$1"
docker_package="$2"
containerd_package="$3"
buildx_package="$4"
compose_package="$5"
compose_version="$6"
expected_fingerprint="$7"
. /etc/os-release
sudo install -m 0755 -d /etc/apt/keyrings
docker_gpg="$(mktemp)"
trap 'rm -f "$docker_gpg"' EXIT
curl --fail --silent --show-error --location \
  --retry 8 --retry-all-errors --connect-timeout 20 --max-time 180 \
  https://download.docker.com/linux/ubuntu/gpg --output "$docker_gpg"
actual_fingerprint="$(gpg --batch --with-colons --show-keys "$docker_gpg" | awk -F: '$1 == "fpr" {print $10; exit}')"
primary_count="$(gpg --batch --with-colons --show-keys "$docker_gpg" | awk -F: '$1 == "pub" {count++} END {print count+0}')"
[ "$primary_count" = "1" ] || { echo "Docker apt key must contain exactly one primary key" >&2; exit 1; }
[ "$actual_fingerprint" = "$expected_fingerprint" ] || { echo "Docker apt key fingerprint mismatch" >&2; exit 1; }
sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg "$docker_gpg"
sudo chmod a+r /etc/apt/keyrings/docker.gpg
[ "$(gpg --batch --with-colons --show-keys /etc/apt/keyrings/docker.gpg | awk -F: '$1 == "pub" {count++} END {print count+0}')" = "1" ]
[ "$(gpg --batch --with-colons --show-keys /etc/apt/keyrings/docker.gpg | awk -F: '$1 == "fpr" {print $10; exit}')" = "$expected_fingerprint" ]
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
  "$(dpkg --print-architecture)" "$VERSION_CODENAME" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get -o Acquire::Retries=5 update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  "docker-ce=$docker_package" \
  "docker-ce-cli=$docker_package" \
  "containerd.io=$containerd_package" \
  "docker-buildx-plugin=$buildx_package" \
  "docker-compose-plugin=$compose_package"
sudo usermod -aG docker aiops
sudo systemctl enable --now docker
[ "$(sudo docker version --format '{{.Server.Version}}')" = "$engine_version" ]
[ "$(sudo docker compose version --short)" = "$compose_version" ]
REMOTE
}

sync_checkout() {
  local ip="$1"
  local ssh_command
  ssh_command="ssh -i $SSH_KEY -o UserKnownHostsFile=$KNOWN_HOSTS -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  rsync -az --delete -e "$ssh_command" "$SOURCE_DIR/" "aiops@$ip:/home/aiops/agent-ops/"
}

transfer_test_image() {
  local ip="$1"
  local source_image="$2"
  local target_image="$3"
  local source_fingerprint remote_fingerprint
  local -a options
  mapfile -t options < <(ssh_args)
  source_fingerprint="$(docker image inspect "$source_image" --format '{{json .Config}}{{json .RootFS}}' | sha256sum | awk '{print $1}')"
  docker save "$source_image" | \
    ssh "${options[@]}" "aiops@$ip" "docker load >/dev/null && docker tag '$source_image' '$target_image'"
  remote_fingerprint="$(ssh "${options[@]}" "aiops@$ip" "docker image inspect '$target_image' --format '{{json .Config}}{{json .RootFS}}'" | sha256sum | awk '{print $1}')"
  [ "$remote_fingerprint" = "$source_fingerprint" ] || fail "$target_image content changed during transfer"
}

configure_vm_registry() {
  local ip="$1"
  local -a options
  mapfile -t options < <(ssh_args)
  ssh "${options[@]}" "aiops@$ip" bash -s -- "$VM_REGISTRY" <<'REMOTE'
set -euo pipefail
registry="$1"
if [ -f /etc/docker/daemon.json ]; then
  sudo cp -a /etc/docker/daemon.json /var/lib/aiops-r0-daemon.json.backup
else
  sudo touch /var/lib/aiops-r0-daemon-json-was-absent
fi
printf '{"insecure-registries":["%s"]}\n' "$registry" | sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl restart docker
docker info --format '{{json .RegistryConfig.IndexConfigs}}' | grep -F "$registry" >/dev/null
REMOTE
}

restore_vm_registry_config() {
  local ip="$1"
  local -a options
  mapfile -t options < <(ssh_args)
  ssh "${options[@]}" "aiops@$ip" bash -s <<'REMOTE'
set -euo pipefail
if [ -f /var/lib/aiops-r0-daemon.json.backup ]; then
  sudo mv /var/lib/aiops-r0-daemon.json.backup /etc/docker/daemon.json
elif [ -f /var/lib/aiops-r0-daemon-json-was-absent ]; then
  sudo rm -f /etc/docker/daemon.json
else
  echo "missing Docker daemon configuration restore marker" >&2
  exit 1
fi
sudo rm -f /var/lib/aiops-r0-daemon-json-was-absent
sudo systemctl restart docker
REMOTE
}

pull_release_image() {
  local ip="$1"
  local repository="$2"
  local digest="$3"
  local target_image="$4"
  local image_ref="$VM_REGISTRY/$repository@$digest"
  local -a options
  mapfile -t options < <(ssh_args)
  ssh "${options[@]}" "aiops@$ip" \
    "docker pull '$image_ref' >/dev/null && docker tag '$image_ref' '$target_image' && docker image inspect '$image_ref' --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -F '@$digest' >/dev/null"
}

transfer_release_images() {
  local ip="$1"
  configure_vm_registry "$ip"
  pull_release_image "$ip" agent-ops-backend "$HOST_BACKEND_DIGEST" agent-ops-backend:r0-ci
  pull_release_image "$ip" agent-ops-frontend "$HOST_FRONTEND_DIGEST" agent-ops-frontend:r0-ci
  transfer_test_image "$ip" "$HOST_TEST_IMAGE" agent-ops-backend-test:r0-ci
}

run_vm_gate() {
  local name="$1"
  local evidence_name="$2"
  local ip="$3"
  local expected_os="$4"
  local engine_version="$5"
  local docker_package="$6"
  local containerd_package="$7"
  local buildx_package="$8"
  local compose_package="$9"
  local cloud_release="${10}"
  local cloud_image_sha256="${11}"
  local expected_data_filesystem="${12}"
  local log="$EVIDENCE_DIR/$evidence_name-$RUN_ID.log"
  local manifest="$EVIDENCE_DIR/$evidence_name.json"
  local load_manifest="$EVIDENCE_DIR/$evidence_name-load.json"
  local raw_log_path=".omx/evidence/production-ga/GA-R0-001/$evidence_name-$RUN_ID.log"
  local backend_cosign_path=".omx/evidence/production-ga/GA-R0-001/backend-cosign-$RUN_ID.json"
  local frontend_cosign_path=".omx/evidence/production-ga/GA-R0-001/frontend-cosign-$RUN_ID.json"
  local release_manifest_path=".omx/evidence/production-ga/GA-R0-001/release-manifest-$RUN_ID.json"
  local backend_provenance_path=".omx/evidence/production-ga/GA-R0-001/backend-provenance-$RUN_ID.json"
  local frontend_provenance_path=".omx/evidence/production-ga/GA-R0-001/frontend-provenance-$RUN_ID.json"
  local backend_sbom_path=".omx/evidence/production-ga/GA-R0-001/backend-sbom-$RUN_ID.json"
  local frontend_sbom_path=".omx/evidence/production-ga/GA-R0-001/frontend-sbom-$RUN_ID.json"
  local -a options pipeline_status
  mapfile -t options < <(ssh_args)

  set +e
  ssh "${options[@]}" "aiops@$ip" \
    "AIOPS_EVIDENCE_NAME='$evidence_name' AIOPS_RAW_LOG_PATH='$raw_log_path' AIOPS_EXPECTED_OS='$expected_os' AIOPS_EXPECTED_DATA_FILESYSTEM='$expected_data_filesystem' AIOPS_EXPECTED_DOCKER_ENGINE='$engine_version' AIOPS_DOCKER_CE_PACKAGE='$docker_package' AIOPS_CONTAINERD_PACKAGE='$containerd_package' AIOPS_BUILDX_PACKAGE='$buildx_package' AIOPS_COMPOSE_PACKAGE='$compose_package' AIOPS_CLOUD_RELEASE='$cloud_release' AIOPS_CLOUD_IMAGE_SHA256='$cloud_image_sha256' AIOPS_CLOUD_KEY_FINGERPRINT='$CLOUD_KEY_FINGERPRINT' AIOPS_DOCKER_GPG_FINGERPRINT='$DOCKER_GPG_FINGERPRINT' AIOPS_REGISTRY_IMAGE='$REGISTRY_IMAGE' AIOPS_SOURCE_ARCHIVE_SHA256='$SOURCE_ARCHIVE_SHA256' AIOPS_EXPECTED_BACKEND_DIGEST='$HOST_BACKEND_DIGEST' AIOPS_EXPECTED_FRONTEND_DIGEST='$HOST_FRONTEND_DIGEST' AIOPS_BACKEND_IMAGE_REF='$VM_REGISTRY/agent-ops-backend@$HOST_BACKEND_DIGEST' AIOPS_FRONTEND_IMAGE_REF='$VM_REGISTRY/agent-ops-frontend@$HOST_FRONTEND_DIGEST' AIOPS_UPSTREAM_BACKEND_IMAGE_REF='$UPSTREAM_BACKEND_IMAGE_REF' AIOPS_UPSTREAM_FRONTEND_IMAGE_REF='$UPSTREAM_FRONTEND_IMAGE_REF' AIOPS_RELEASE_MANIFEST_SHA256='$RELEASE_MANIFEST_SHA256' AIOPS_RELEASE_MANIFEST_PATH='$release_manifest_path' AIOPS_RELEASE_TAG='$RELEASE_TAG' AIOPS_BACKEND_COSIGN_PATH='$backend_cosign_path' AIOPS_FRONTEND_COSIGN_PATH='$frontend_cosign_path' AIOPS_BACKEND_COSIGN_SHA256='$BACKEND_COSIGN_SHA256' AIOPS_FRONTEND_COSIGN_SHA256='$FRONTEND_COSIGN_SHA256' AIOPS_BACKEND_PROVENANCE_PATH='$backend_provenance_path' AIOPS_FRONTEND_PROVENANCE_PATH='$frontend_provenance_path' AIOPS_BACKEND_SBOM_PATH='$backend_sbom_path' AIOPS_FRONTEND_SBOM_PATH='$frontend_sbom_path' AIOPS_BACKEND_PROVENANCE_SHA256='$BACKEND_PROVENANCE_SHA256' AIOPS_FRONTEND_PROVENANCE_SHA256='$FRONTEND_PROVENANCE_SHA256' AIOPS_BACKEND_SBOM_SHA256='$BACKEND_SBOM_SHA256' AIOPS_FRONTEND_SBOM_SHA256='$FRONTEND_SBOM_SHA256' AIOPS_SOURCE_COMMIT='$COMMIT_SHA' bash -s" \
    <<'REMOTE' 2>&1 | tee "$log"
set -euo pipefail
umask 077
cd /home/aiops/agent-ops
export AIOPS_COMMIT_SHA="$AIOPS_SOURCE_COMMIT"
[ "$(<.aiops-source-commit)" = "$AIOPS_SOURCE_COMMIT" ]
. /etc/os-release
[ "$VERSION_ID" = "$AIOPS_EXPECTED_OS" ]
[ "$(uname -m)" = "x86_64" ]
root_filesystem="$(findmnt -no FSTYPE /)"
case "$root_filesystem" in ext4|xfs) ;; *) echo "root filesystem is outside support policy" >&2; exit 1 ;; esac
data_filesystem="$(sudo findmnt -no FSTYPE -T /var/lib/docker)"
[ "$data_filesystem" = "$AIOPS_EXPECTED_DATA_FILESYSTEM" ] || {
  echo "Docker data filesystem mismatch: expected $AIOPS_EXPECTED_DATA_FILESYSTEM, got $data_filesystem" >&2
  exit 1
}
docker_root_dir="$(docker info --format '{{.DockerRootDir}}')"
[ "$docker_root_dir" = "/var/lib/docker" ] || { echo "unexpected Docker root: $docker_root_dir" >&2; exit 1; }
[ "$(docker version --format '{{.Server.Version}}')" = "$AIOPS_EXPECTED_DOCKER_ENGINE" ]
docker image inspect "$AIOPS_BACKEND_IMAGE_REF" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -F "@$AIOPS_EXPECTED_BACKEND_DIGEST" >/dev/null
docker image inspect "$AIOPS_FRONTEND_IMAGE_REF" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep -F "@$AIOPS_EXPECTED_FRONTEND_DIGEST" >/dev/null
docker run --rm agent-ops-backend-test:r0-ci
python_wrapper="$(mktemp)"
cat > "$python_wrapper" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec docker run --rm \
  --user "$(id -u):$(id -g)" \
  --volume "$AIOPS_WRAPPER_ROOT:$AIOPS_WRAPPER_ROOT" \
  --workdir "$AIOPS_WRAPPER_ROOT" \
  --entrypoint python \
  agent-ops-backend:r0-ci "$@"
SH
chmod 0700 "$python_wrapper"
export AIOPS_WRAPPER_ROOT="$PWD"
AIOPS_PYTHON_BIN="$python_wrapper" ./scripts/verify-production-baseline.sh
rm -f "$python_wrapper"
mkdir -p .omx/evidence/production-ga/GA-R0-001
storage_evidence_path=".omx/evidence/production-ga/GA-R0-001/${AIOPS_EVIDENCE_NAME}-storage.json"
load_evidence_path=".omx/evidence/production-ga/GA-R0-001/${AIOPS_EVIDENCE_NAME}-load.json"
AIOPS_RUN_PRODUCTION_PLAYWRIGHT=0 AIOPS_PRESERVE_ON_FAILURE=1 \
  AIOPS_EXPECTED_DATA_FILESYSTEM="$AIOPS_EXPECTED_DATA_FILESYSTEM" \
  AIOPS_DATA_FILESYSTEM_EVIDENCE_FILE="$storage_evidence_path" \
  AIOPS_LOAD_EVIDENCE_FILE="$load_evidence_path" \
  ./scripts/test-production-compose.sh
[ -f "$storage_evidence_path" ] || { echo "SQLite storage evidence was not written" >&2; exit 1; }
[ -f "$load_evidence_path" ] || { echo "load evidence was not written" >&2; exit 1; }
export AIOPS_DATA_FILESYSTEM_EVIDENCE_FILE="$storage_evidence_path"
export AIOPS_LOAD_EVIDENCE_FILE="$load_evidence_path"
export AIOPS_BACKEND_CONFIG_ID="$(docker image inspect agent-ops-backend:r0-ci --format '{{.Id}}')"
export AIOPS_FRONTEND_CONFIG_ID="$(docker image inspect agent-ops-frontend:r0-ci --format '{{.Id}}')"
export AIOPS_DOCKER_VERSION="$(docker version --format '{{.Server.Version}}')"
export AIOPS_COMPOSE_VERSION="$(docker compose version --short)"
export AIOPS_KERNEL="$(uname -r)"
export AIOPS_ROOT_FILESYSTEM="$root_filesystem"
export AIOPS_DATA_FILESYSTEM="$data_filesystem"
export AIOPS_DOCKER_ROOT_DIR="$docker_root_dir"
export AIOPS_DOCKER_STORAGE_DRIVER="$(docker info --format '{{.Driver}}')"
python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

name = os.environ["AIOPS_EVIDENCE_NAME"]
sqlite_storage = json.loads(Path(os.environ["AIOPS_DATA_FILESYSTEM_EVIDENCE_FILE"]).read_text(encoding="utf-8"))
load_smoke = json.loads(Path(os.environ["AIOPS_LOAD_EVIDENCE_FILE"]).read_text(encoding="utf-8"))
payload = {
    "test_id": "GA-R0-001",
    "requirement": "clean Ubuntu x86_64 production fail-closed, setup, health and topology gate",
    "automation": "signed cloud image -> exact Docker packages -> pytest + static probe + production Compose runtime",
    "environment": f"Ubuntu {os.environ['AIOPS_EXPECTED_OS']} x86_64 clean KVM VM with {os.environ['AIOPS_EXPECTED_DATA_FILESYSTEM']} Docker/SQLite data",
    "evidence_name": name,
    "os_version": os.environ["AIOPS_EXPECTED_OS"],
    "data_filesystem": os.environ["AIOPS_EXPECTED_DATA_FILESYSTEM"],
    "fixture_or_seed": "GPG-verified official Ubuntu cloud image, exact shared OCI digests, ephemeral SQLite and one-time setup token",
    "sample_size_or_duration": "full backend suite and one complete production Compose setup/restart/runtime cycle",
    "expected": "all R0 local gates pass while Worker, Agent, mutation and unverified LLM remain fail-closed",
    "evidence_path": f".omx/evidence/production-ga/GA-R0-001/{name}.json",
    "raw_log_path": os.environ["AIOPS_RAW_LOG_PATH"],
    "owner": "backend and release leads",
    "release_digest": os.environ["AIOPS_EXPECTED_BACKEND_DIGEST"],
    "result": "passed",
    "executed_at": datetime.now(timezone.utc).isoformat(),
    "commit_sha": os.environ["AIOPS_SOURCE_COMMIT"],
    "source_archive_sha256": os.environ["AIOPS_SOURCE_ARCHIVE_SHA256"],
    "cloud_image_url": f"https://cloud-images.ubuntu.com/{os.environ['AIOPS_CLOUD_RELEASE']}/current/{os.environ['AIOPS_CLOUD_RELEASE']}-server-cloudimg-amd64.img",
    "cloud_image_sha256": os.environ["AIOPS_CLOUD_IMAGE_SHA256"],
    "cloud_signing_key_fingerprint": os.environ["AIOPS_CLOUD_KEY_FINGERPRINT"],
    "docker_gpg_fingerprint": os.environ["AIOPS_DOCKER_GPG_FINGERPRINT"],
    "registry_image": os.environ["AIOPS_REGISTRY_IMAGE"],
    "kernel": os.environ["AIOPS_KERNEL"],
    "filesystem": os.environ["AIOPS_DATA_FILESYSTEM"],
    "root_filesystem": os.environ["AIOPS_ROOT_FILESYSTEM"],
    "docker_root_dir": os.environ["AIOPS_DOCKER_ROOT_DIR"],
    "docker_storage_driver": os.environ["AIOPS_DOCKER_STORAGE_DRIVER"],
    "sqlite_storage": sqlite_storage,
    "load_smoke": load_smoke,
    "docker_engine": os.environ["AIOPS_DOCKER_VERSION"],
    "docker_compose": os.environ["AIOPS_COMPOSE_VERSION"],
    "docker_ce_package": os.environ["AIOPS_DOCKER_CE_PACKAGE"],
    "containerd_package": os.environ["AIOPS_CONTAINERD_PACKAGE"],
    "buildx_package": os.environ["AIOPS_BUILDX_PACKAGE"],
    "compose_package": os.environ["AIOPS_COMPOSE_PACKAGE"],
    "backend_image_ref": os.environ["AIOPS_BACKEND_IMAGE_REF"],
    "frontend_image_ref": os.environ["AIOPS_FRONTEND_IMAGE_REF"],
    "backend_config_id": os.environ["AIOPS_BACKEND_CONFIG_ID"],
    "frontend_config_id": os.environ["AIOPS_FRONTEND_CONFIG_ID"],
    "frontend_release_digest": os.environ["AIOPS_EXPECTED_FRONTEND_DIGEST"],
    "artifact_class": "signed_release",
    "upstream_backend_image_ref": os.environ["AIOPS_UPSTREAM_BACKEND_IMAGE_REF"],
    "upstream_frontend_image_ref": os.environ["AIOPS_UPSTREAM_FRONTEND_IMAGE_REF"],
    "release_manifest_sha256": os.environ["AIOPS_RELEASE_MANIFEST_SHA256"],
    "release_manifest_path": os.environ["AIOPS_RELEASE_MANIFEST_PATH"],
    "release_tag": os.environ["AIOPS_RELEASE_TAG"],
    "release_tag_signature_verified": True,
    "backend_cosign_verification_sha256": os.environ["AIOPS_BACKEND_COSIGN_SHA256"],
    "frontend_cosign_verification_sha256": os.environ["AIOPS_FRONTEND_COSIGN_SHA256"],
    "backend_cosign_verification_path": os.environ["AIOPS_BACKEND_COSIGN_PATH"],
    "frontend_cosign_verification_path": os.environ["AIOPS_FRONTEND_COSIGN_PATH"],
    "backend_provenance_verification_sha256": os.environ["AIOPS_BACKEND_PROVENANCE_SHA256"],
    "frontend_provenance_verification_sha256": os.environ["AIOPS_FRONTEND_PROVENANCE_SHA256"],
    "backend_sbom_verification_sha256": os.environ["AIOPS_BACKEND_SBOM_SHA256"],
    "frontend_sbom_verification_sha256": os.environ["AIOPS_FRONTEND_SBOM_SHA256"],
    "backend_provenance_verification_path": os.environ["AIOPS_BACKEND_PROVENANCE_PATH"],
    "frontend_provenance_verification_path": os.environ["AIOPS_FRONTEND_PROVENANCE_PATH"],
    "backend_sbom_verification_path": os.environ["AIOPS_BACKEND_SBOM_PATH"],
    "frontend_sbom_verification_path": os.environ["AIOPS_FRONTEND_SBOM_PATH"],
}
path = Path(payload["evidence_path"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
path.chmod(0o600)
PY
REMOTE
  pipeline_status=(${PIPESTATUS[*]})
  set -e
  [ "${pipeline_status[0]}" -eq 0 ] || fail "$name validation failed; VM will be retained offline"
  [ "${pipeline_status[1]}" -eq 0 ] || fail "$name evidence log could not be written"
  chmod 0600 "$log"
  scp "${options[@]}" "aiops@$ip:/home/aiops/agent-ops/.omx/evidence/production-ga/GA-R0-001/$evidence_name.json" "$manifest"
  scp "${options[@]}" "aiops@$ip:/home/aiops/agent-ops/.omx/evidence/production-ga/GA-R0-001/$evidence_name-load.json" "$load_manifest"
  chmod 0600 "$manifest"
  chmod 0600 "$load_manifest"
}

sanitize_and_power_off_vm() {
  local name="$1"
  local ip="$2"
  local strict="$3"
  local mac state restore_failed=0
  if [ -n "$ip" ]; then
    if ! restore_vm_registry_config "$ip"; then
      restore_failed=1
      echo "warning: Docker daemon configuration could not be restored on $name" >&2
    fi
  fi
  if sudo virsh dominfo "$name" >/dev/null 2>&1; then
    state="$(sudo virsh domstate "$name" | tr -d '\r')"
    if [ "$state" != "shut off" ]; then
      sudo virsh shutdown "$name" >/dev/null 2>&1 || true
      for _ in $(seq 1 30); do
        state="$(sudo virsh domstate "$name" 2>/dev/null | tr -d '\r' || true)"
        [ "$state" = "shut off" ] && break
        sleep 1
      done
      if [ "$state" != "shut off" ]; then
        sudo virsh destroy "$name" >/dev/null 2>&1 || true
      fi
    fi
    state="$(sudo virsh domstate "$name" | tr -d '\r')"
    if [ "$state" != "shut off" ]; then
      while read -r mac; do
        [ -n "$mac" ] || continue
        sudo virsh domif-setlink "$name" "$mac" down --live >/dev/null 2>&1 || true
        sudo virsh domif-setlink "$name" "$mac" down --config >/dev/null 2>&1 || true
      done < <(sudo virsh domiflist "$name" | awk '$2 == "network" {print $5}')
      return 1
    fi
  fi
  [ "$restore_failed" = "0" ] || [ "$strict" = "0" ] || return 1
}

scan_current_evidence_for_secrets() {
  python3 - "$RUN_LOG" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2204-ext4-$RUN_ID.log" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2204-xfs-$RUN_ID.log" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2404-ext4-$RUN_ID.log" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2404-xfs-$RUN_ID.log" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2204-ext4.json" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2204-xfs.json" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2404-ext4.json" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2404-xfs.json" <<'PY'
import re
import sys
from pathlib import Path

patterns = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?:AIOPS_)?(?:SESSION_SECRET|LLM_API_KEY|DEEPSEEK_API_KEY|MOONSHOT_API_KEY|ZHIPU_API_KEY|SETUP_TOKEN)=[^\s]+"),
    re.compile(r'(?i)"(?:token|(?:deepseek|moonshot|zhipu)[_-]?api[_-]?key|(?:llm[_-]?)?api[_-]?key|session[_-]?secret|setup[_-]?token|password)"\s*:\s*"(?!\*{3}|<redacted>)[^"\s]{8,}"'),
)
positive_fixtures = (
    'AIOPS_DEEPSEEK_API_KEY=fixture-secret-value',
    'DEEPSEEK_API_KEY=fixture-secret-value',
    'AIOPS_MOONSHOT_API_KEY=fixture-secret-value',
    'ZHIPU_API_KEY=fixture-secret-value',
    '{"token":"fixture-secret-value"}',
    '{"deepseek_api_key":"fixture-secret-value"}',
    '{"moonshot_api_key":"fixture-secret-value"}',
    '{"zhipu_api_key":"fixture-secret-value"}',
)
for fixture in positive_fixtures:
    if not any(pattern.search(fixture) for pattern in patterns):
        raise SystemExit(f"secret scanner self-test missed fixture: {fixture.split('=')[0]}")
for fixture in ('{"token":"<redacted>"}', '{"password":"********"}'):
    if any(pattern.search(fixture) for pattern in patterns):
        raise SystemExit("secret scanner self-test rejected a redacted fixture")
for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in patterns:
        if pattern.search(text):
            raise SystemExit(f"potential secret material found in evidence log: {path}")
PY
}

generate_matrix_summary() {
  local staged_summary="$EVIDENCE_DIR/.matrix-summary.$RUN_ID.pending"
  local staged_status="$EVIDENCE_DIR/.matrix-status.$RUN_ID.pending"
  [ "$(sha256sum "$SOURCE_DIR/scripts/generate-r0-matrix-summary.py" | awk '{print $1}')" = \
    "$SUMMARY_GENERATOR_SHA256" ] || fail "immutable matrix-summary generator changed during the run"
  "$SOURCE_DIR/scripts/generate-r0-matrix-summary.py" --stage \
    --root "$ROOT_DIR" \
    --evidence-dir "$EVIDENCE_DIR" \
    --commit "$COMMIT_SHA" \
    --source-archive-sha256 "$SOURCE_ARCHIVE_SHA256" \
    --registry-image "$REGISTRY_IMAGE" \
    --run-id "$RUN_ID"
  STAGED_SUMMARY_SHA256="$(sha256sum "$staged_summary" | awk '{print $1}')"
  STAGED_STATUS_SHA256="$(sha256sum "$staged_status" | awk '{print $1}')"
}

publish_staged_matrix_summary() {
  local staged_summary="$EVIDENCE_DIR/.matrix-summary.$RUN_ID.pending"
  local staged_status="$EVIDENCE_DIR/.matrix-status.$RUN_ID.pending"
  [ -f "$staged_summary" ] && [ ! -L "$staged_summary" ] || return 1
  [ -f "$staged_status" ] && [ ! -L "$staged_status" ] || return 1
  [ "$(sha256sum "$staged_summary" | awk '{print $1}')" = "$STAGED_SUMMARY_SHA256" ] || return 1
  [ "$(sha256sum "$staged_status" | awk '{print $1}')" = "$STAGED_STATUS_SHA256" ] || return 1
  mv -f "$staged_summary" "$EVIDENCE_DIR/matrix-summary.json" || return 1
  if ! mv -f "$staged_status" "$EVIDENCE_DIR/matrix-status.json"; then
    rm -f "$EVIDENCE_DIR/matrix-summary.json"
    return 1
  fi
}

invalidate_published_matrix_summary() {
  rm -f \
    "$EVIDENCE_DIR/matrix-summary.json" \
    "$EVIDENCE_DIR/matrix-status.json" \
    "$EVIDENCE_DIR/.matrix-summary.$RUN_ID.pending" \
    "$EVIDENCE_DIR/.matrix-status.$RUN_ID.pending"
}

append_run_log() {
  local message="$1"
  if [ -f "$RUN_LOG" ]; then
    printf '%s\n' "$message" | tee -a "$RUN_LOG"
  else
    printf '%s\n' "$message"
  fi
}

cleanup_on_exit() {
  local status=$? state scan_output
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ -n "$CURRENT_VM_NAME" ]; then
      if sanitize_and_power_off_vm "$CURRENT_VM_NAME" "$CURRENT_VM_IP" 0; then
        if sudo virsh dominfo "$CURRENT_VM_NAME" >/dev/null 2>&1; then
          state="$(sudo virsh domstate "$CURRENT_VM_NAME" | tr -d '\r')"
          if [ "$state" = "shut off" ]; then
            echo "failure VM retained offline: $CURRENT_VM_NAME" >&2
          else
            echo "CRITICAL: failure VM is still online with state $state: $CURRENT_VM_NAME" >&2
          fi
        elif [ -e "$IMAGE_ROOT/$CURRENT_VM_NAME.qcow2" ]; then
          echo "partial failure disk retained without a defined VM: $IMAGE_ROOT/$CURRENT_VM_NAME.qcow2" >&2
        else
          echo "failure occurred before VM artifacts were created: $CURRENT_VM_NAME" >&2
        fi
      else
        echo "CRITICAL: could not prove failure VM is offline; interfaces were forced down where possible: $CURRENT_VM_NAME" >&2
        sudo virsh dominfo "$CURRENT_VM_NAME" >&2 2>/dev/null || true
        sudo virsh domiflist "$CURRENT_VM_NAME" >&2 2>/dev/null || true
      fi
      [ ! -e "$IMAGE_ROOT/$CURRENT_VM_NAME.qcow2" ] || \
        echo "failure disk retained: $IMAGE_ROOT/$CURRENT_VM_NAME.qcow2" >&2
      [ ! -e "$IMAGE_ROOT/$CURRENT_VM_NAME-data.qcow2" ] || \
        echo "failure data disk retained: $IMAGE_ROOT/$CURRENT_VM_NAME-data.qcow2" >&2
    fi
    if ! cleanup_registry; then
      echo "CRITICAL: ephemeral registry cleanup failed: $REGISTRY_NAME on port $REGISTRY_PORT" >&2
      docker container inspect "$REGISTRY_NAME" >&2 2>/dev/null || true
      ss -H -ltnp | awk -v suffix=":$REGISTRY_PORT" '$4 ~ suffix "$"' >&2 || true
    fi
    if ! finalize_run_log; then
      append_run_log "CRITICAL: run log tee did not flush cleanly: $RUN_LOG" >&2
    fi
    if [ "$RUN_INITIALIZED" = "1" ]; then
      if ! scan_output="$(scan_current_evidence_for_secrets 2>&1)"; then
        append_run_log "$scan_output" >&2
        append_run_log "CRITICAL: retained failure evidence may contain secret material; keep it mode 0600 and review before sharing" >&2
      fi
      append_run_log "run log retained: $RUN_LOG" >&2
      sync "$RUN_LOG"
    else
      if [ -f "$RUN_LOG_OWNERSHIP_MARKER" ]; then
        if python3 - "$RUN_LOG" "$RUN_LOG_OWNERSHIP_MARKER" <<'PY'
import os
import stat
import sys

path, marker = sys.argv[1:]
no_follow = getattr(os, "O_NOFOLLOW", 0)
if no_follow == 0:
    raise SystemExit("O_NOFOLLOW is unavailable")
marker_descriptor = os.open(marker, os.O_RDONLY | no_follow)
try:
    marker_stat = os.fstat(marker_descriptor)
    if not stat.S_ISREG(marker_stat.st_mode):
        raise SystemExit("run-log ownership marker is not a regular file")
    expected = os.read(marker_descriptor, 128).decode("ascii").strip()
finally:
    os.close(marker_descriptor)
try:
    actual = os.lstat(path)
except FileNotFoundError:
    raise SystemExit(0)
if not stat.S_ISREG(actual.st_mode) or f"{actual.st_dev}:{actual.st_ino}" != expected:
    raise SystemExit("refusing to remove a run log not owned by this run")
os.unlink(path)
PY
        then
          RUN_LOG_OWNED=0
        else
          echo "CRITICAL: partial run log ownership could not be proven; retaining forensic artifacts" >&2
        fi
      fi
      if [ "$RUN_LOG_OWNED" = "0" ] && [ "$RUN_DIR_OWNED" = "1" ] && [ -d "$RUN_DIR" ]; then
        rm -rf "$RUN_DIR" || true
        [ ! -e "$RUN_DIR" ] && RUN_DIR_OWNED=0
      fi
    fi
    if [ "$MATRIX_OWNS_STATUS" = "1" ]; then
      invalidate_published_matrix_summary
    fi
  fi
  exit "$status"
}

main() {
  local row suffix release os_variant expected_os engine_version docker_package
  local containerd_package buildx_package compose_package compose_version
  local data_filesystem name evidence_name base_image base_image_sha256 ip
  local scan_output
  local -A base_images=()
  local -A base_image_hashes=()

  require_host
  trap cleanup_on_exit EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  prepare_state
  prepare_source
  prepare_signed_release
  "$SOURCE_DIR/scripts/generate-r0-matrix-summary.py" \
    --invalidate --root "$ROOT_DIR" --evidence-dir "$EVIDENCE_DIR" --run-id "$RUN_ID"
  MATRIX_OWNS_STATUS=1
  build_test_image
  mirror_release_images
  for row in "${MATRIX[@]}"; do
    IFS='|' read -r suffix release os_variant expected_os engine_version docker_package \
      containerd_package buildx_package compose_package compose_version data_filesystem <<< "$row"
    name="agent-ops-r0-$RUN_ID-$suffix"
    evidence_name="agent-ops-r0-$suffix"
    if [ -z "${base_images[$release]:-}" ]; then
      base_images[$release]="$(download_verified_cloud_image "$release")"
      base_image_hashes[$release]="$(sudo sha256sum "${base_images[$release]}" | awk '{print $1}')"
    fi
    base_image="${base_images[$release]}"
    base_image_sha256="${base_image_hashes[$release]}"
    CURRENT_VM_NAME="$name"
    CURRENT_VM_IP=""
    echo "[$name] creating clean Ubuntu $expected_os VM with $data_filesystem Docker data"
    create_vm "$name" "$base_image" "$os_variant" "$data_filesystem"
    ip="$(wait_for_vm_ip "$name")"
    CURRENT_VM_IP="$ip"
    echo "[$name] waiting for cloud-init at $ip"
    wait_for_cloud_init "$ip"
    echo "[$name] preparing $data_filesystem Docker data filesystem"
    prepare_vm_data_filesystem "$ip" "$data_filesystem" "$VM_DATA_DISK_SIZE"
    echo "[$name] installing Docker $engine_version"
    install_docker_exact "$ip" "$engine_version" "$docker_package" "$containerd_package" \
      "$buildx_package" "$compose_package" "$compose_version"
    sync_checkout "$ip"
    echo "[$name] transferring exact signed release OCI manifests"
    transfer_release_images "$ip"
    echo "[$name] running R0 blocking gates"
    run_vm_gate "$name" "$evidence_name" "$ip" "$expected_os" "$engine_version" \
      "$docker_package" "$containerd_package" "$buildx_package" "$compose_package" \
      "$release" "$base_image_sha256" "$data_filesystem"
    sanitize_and_power_off_vm "$name" "$ip" 1
    if [ "$KEEP_VMS" = "1" ]; then
      echo "[$name] passed and was retained offline with its original Docker daemon configuration"
    else
      remove_namespaced_vm "$name"
      echo "[$name] passed and was fully destroyed"
    fi
    CURRENT_VM_NAME=""
    CURRENT_VM_IP=""
  done
  cleanup_registry || fail "ephemeral registry or listening port was not removed"
  REGISTRY_STARTED=0
  cleanup_success_image_artifacts
  finalize_run_log || fail "run log tee did not flush cleanly"
  if ! scan_output="$(scan_current_evidence_for_secrets 2>&1)"; then
    append_run_log "$scan_output" >&2
    fail "secret material was found in completed matrix evidence"
  fi
  generate_matrix_summary
  cleanup_success_run_artifacts || fail "successful run directory was not removed"
  append_run_log "R0 matrix cleanup completed and the staged evidence passed secret scanning"
  sync "$RUN_LOG"
  scan_current_evidence_for_secrets
  publish_staged_matrix_summary || fail "staged matrix summary could not be atomically published"
  append_run_log "R0 clean-VM matrix passed"
  sync "$RUN_LOG"
  if ! scan_output="$(scan_current_evidence_for_secrets 2>&1)"; then
    invalidate_published_matrix_summary
    append_run_log "$scan_output" >&2
    fail "secret material appeared after matrix summary publication"
  fi
  MATRIX_OWNS_STATUS=0
}

main "$@"
