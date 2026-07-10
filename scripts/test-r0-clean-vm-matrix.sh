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
RUN_ID="${AIOPS_R0_RUN_ID:-$(date -u +%Y%m%d%H%M%S)-$$}"
RUN_DIR="$STATE_ROOT/runs/$RUN_ID"
EVIDENCE_DIR="$ROOT_DIR/.omx/evidence/production-ga/GA-R0-001"
SSH_KEY="$RUN_DIR/id_ed25519"
KNOWN_HOSTS="$RUN_DIR/known_hosts"
RUN_LOG="$EVIDENCE_DIR/matrix-$RUN_ID.log"
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
LIBVIRT_GATEWAY=""
HOST_REGISTRY=""
VM_REGISTRY=""
REGISTRY_TAG=""
CURRENT_VM_NAME=""
CURRENT_VM_IP=""
REGISTRY_STARTED=0

MATRIX=(
  "ubuntu2204|jammy|ubuntu22.04|22.04|28.5.2|5:28.5.2-1~ubuntu.22.04~jammy|2.2.5-1~ubuntu.22.04~jammy|0.35.0-1~ubuntu.22.04~jammy|5.3.1-1~ubuntu.22.04~jammy|5.3.1"
  "ubuntu2404|noble|ubuntu24.04|24.04|29.6.1|5:29.6.1-1~ubuntu.24.04~noble|2.2.5-1~ubuntu.24.04~noble|0.35.0-1~ubuntu.24.04~noble|5.3.1-1~ubuntu.24.04~noble|5.3.1"
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
  for command_name in curl docker flock gpg gpgv python3 qemu-img rsync scp sha256sum ssh ss tar virt-install; do
    command -v "$command_name" >/dev/null || fail "$command_name is required"
  done
  if ! command -v cloud-localds >/dev/null || [ ! -f "$CLOUD_KEYRING" ]; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
      cloud-image-utils ubuntu-cloudimage-keyring
  fi
  [[ "$RUN_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]{0,39}$ ]] || fail "AIOPS_R0_RUN_ID has an invalid format"
  [[ "$REGISTRY_PORT" =~ ^[0-9]+$ ]] || fail "AIOPS_R0_REGISTRY_PORT must be numeric"
  [ "$REGISTRY_PORT" -ge 1024 ] && [ "$REGISTRY_PORT" -le 65535 ] || fail "registry port is out of range"
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
  mkdir -p "$RUN_DIR"
  : > "$KNOWN_HOSTS"
  chmod 0600 "$KNOWN_HOSTS"
  ssh-keygen -q -t ed25519 -N '' -f "$SSH_KEY"
  sudo install -d -m 0755 "$IMAGE_ROOT"
  : > "$RUN_LOG"
  chmod 0600 "$RUN_LOG"
  exec > >(tee -a "$RUN_LOG") 2>&1
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
  local actual_commit supplied_archive supplied_sha harness_source harness_running
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
  harness_source="$(sha256sum "$SOURCE_DIR/scripts/test-r0-clean-vm-matrix.sh" | awk '{print $1}')"
  harness_running="$(sha256sum "$ROOT_DIR/scripts/test-r0-clean-vm-matrix.sh" | awk '{print $1}')"
  [ "$harness_source" = "$harness_running" ] || fail "running harness does not match the source archive"
  HOST_BACKEND_IMAGE="${AIOPS_R0_HOST_BACKEND_IMAGE:-agent-ops-backend:r0-matrix-$RUN_ID}"
  HOST_FRONTEND_IMAGE="${AIOPS_R0_HOST_FRONTEND_IMAGE:-agent-ops-frontend:r0-matrix-$RUN_ID}"
  HOST_TEST_IMAGE="${AIOPS_R0_HOST_TEST_IMAGE:-agent-ops-backend-test:r0-matrix-$RUN_ID}"
  REGISTRY_TAG="$COMMIT_SHA-$RUN_ID"
}

build_diagnostic_images() {
  retry_host_command docker build --pull --tag "$HOST_BACKEND_IMAGE" --file "$SOURCE_DIR/backend/Dockerfile" "$SOURCE_DIR"
  retry_host_command docker build --pull --tag "$HOST_FRONTEND_IMAGE" --file "$SOURCE_DIR/frontend/Dockerfile" "$SOURCE_DIR"
  retry_host_command docker build --pull --tag "$HOST_TEST_IMAGE" --file "$SOURCE_DIR/backend/Dockerfile.test" "$SOURCE_DIR"
}

cleanup_registry() {
  local attempt
  if docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
    docker rm -f "$REGISTRY_NAME" >/dev/null
  fi
  ! docker container inspect "$REGISTRY_NAME" >/dev/null 2>&1 || return 1
  docker image rm \
    "$HOST_REGISTRY/agent-ops-backend:$REGISTRY_TAG" \
    "$HOST_REGISTRY/agent-ops-frontend:$REGISTRY_TAG" >/dev/null 2>&1 || true
  for attempt in $(seq 1 20); do
    if ! ss -H -ltn | awk -v suffix=":$REGISTRY_PORT" '$4 ~ suffix "$" {found=1} END {exit !found}'; then
      REGISTRY_STARTED=0
      return 0
    fi
    sleep 0.25
  done
  return 1
}

cleanup_success_artifacts() {
  local image
  for image in "$HOST_BACKEND_IMAGE" "$HOST_FRONTEND_IMAGE" "$HOST_TEST_IMAGE"; do
    [ -n "$image" ] || continue
    docker image rm "$image" >/dev/null 2>&1 || true
    ! docker image inspect "$image" >/dev/null 2>&1 || \
      fail "successful run left diagnostic image tag $image"
  done
  if [ "$KEEP_VMS" = "0" ]; then
    rm -rf "$RUN_DIR"
    [ ! -e "$RUN_DIR" ] || fail "successful run directory was not removed"
  fi
}

push_release_images() {
  local backend_ref="$HOST_REGISTRY/agent-ops-backend:$REGISTRY_TAG"
  local frontend_ref="$HOST_REGISTRY/agent-ops-frontend:$REGISTRY_TAG"
  local backend_repo frontend_repo
  retry_host_command docker pull "$REGISTRY_IMAGE" >/dev/null
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
  docker tag "$HOST_BACKEND_IMAGE" "$backend_ref"
  docker tag "$HOST_FRONTEND_IMAGE" "$frontend_ref"
  docker push "$backend_ref" >/dev/null
  docker push "$frontend_ref" >/dev/null
  backend_repo="${backend_ref%:*}"
  frontend_repo="${frontend_ref%:*}"
  HOST_BACKEND_DIGEST="$(docker image inspect "$backend_ref" --format '{{range .RepoDigests}}{{println .}}{{end}}' | awk -F@ -v repo="$backend_repo" '$1 == repo {print $2; exit}')"
  HOST_FRONTEND_DIGEST="$(docker image inspect "$frontend_ref" --format '{{range .RepoDigests}}{{println .}}{{end}}' | awk -F@ -v repo="$frontend_repo" '$1 == repo {print $2; exit}')"
  [[ "$HOST_BACKEND_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "backend OCI digest was not resolved"
  [[ "$HOST_FRONTEND_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "frontend OCI digest was not resolved"
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
  sudo rm -f "$IMAGE_ROOT/$name.qcow2" "$IMAGE_ROOT/$name-seed.img"
  rm -f "$RUN_DIR/$name-user-data.yaml" "$RUN_DIR/$name-meta-data.yaml" "$RUN_DIR/$name-seed.img"
  [ ! -e "$IMAGE_ROOT/$name.qcow2" ] && [ ! -e "$IMAGE_ROOT/$name-seed.img" ] || \
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
  local disk="$IMAGE_ROOT/$name.qcow2"

  ! sudo virsh dominfo "$name" >/dev/null 2>&1 || fail "refusing to replace existing VM $name"
  [ ! -e "$disk" ] && [ ! -e "$IMAGE_ROOT/$name-seed.img" ] || \
    fail "refusing to replace existing disk artifacts for $name"
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
    --disk "path=$disk,format=qcow2,bus=virtio,cache=none,discard=unmap" \
    --disk "path=$IMAGE_ROOT/$name-seed.img,device=cdrom,readonly=on" \
    --network network=default,model=virtio \
    --graphics none \
    --console pty,target_type=serial \
    --noautoconsole >/dev/null
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

transfer_diagnostic_images() {
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
  local log="$EVIDENCE_DIR/$evidence_name-$RUN_ID.log"
  local manifest="$EVIDENCE_DIR/$evidence_name.json"
  local raw_log_path=".omx/evidence/production-ga/GA-R0-001/$evidence_name-$RUN_ID.log"
  local -a options pipeline_status
  mapfile -t options < <(ssh_args)

  set +e
  ssh "${options[@]}" "aiops@$ip" \
    "AIOPS_EVIDENCE_NAME='$evidence_name' AIOPS_RAW_LOG_PATH='$raw_log_path' AIOPS_EXPECTED_OS='$expected_os' AIOPS_EXPECTED_DOCKER_ENGINE='$engine_version' AIOPS_DOCKER_CE_PACKAGE='$docker_package' AIOPS_CONTAINERD_PACKAGE='$containerd_package' AIOPS_BUILDX_PACKAGE='$buildx_package' AIOPS_COMPOSE_PACKAGE='$compose_package' AIOPS_CLOUD_RELEASE='$cloud_release' AIOPS_CLOUD_IMAGE_SHA256='$cloud_image_sha256' AIOPS_CLOUD_KEY_FINGERPRINT='$CLOUD_KEY_FINGERPRINT' AIOPS_DOCKER_GPG_FINGERPRINT='$DOCKER_GPG_FINGERPRINT' AIOPS_REGISTRY_IMAGE='$REGISTRY_IMAGE' AIOPS_SOURCE_ARCHIVE_SHA256='$SOURCE_ARCHIVE_SHA256' AIOPS_EXPECTED_BACKEND_DIGEST='$HOST_BACKEND_DIGEST' AIOPS_EXPECTED_FRONTEND_DIGEST='$HOST_FRONTEND_DIGEST' AIOPS_BACKEND_IMAGE_REF='$VM_REGISTRY/agent-ops-backend@$HOST_BACKEND_DIGEST' AIOPS_FRONTEND_IMAGE_REF='$VM_REGISTRY/agent-ops-frontend@$HOST_FRONTEND_DIGEST' AIOPS_SOURCE_COMMIT='$COMMIT_SHA' bash -s" \
    <<'REMOTE' 2>&1 | tee "$log"
set -euo pipefail
umask 077
cd /home/aiops/agent-ops
export AIOPS_COMMIT_SHA="$AIOPS_SOURCE_COMMIT"
[ "$(<.aiops-source-commit)" = "$AIOPS_SOURCE_COMMIT" ]
. /etc/os-release
[ "$VERSION_ID" = "$AIOPS_EXPECTED_OS" ]
[ "$(uname -m)" = "x86_64" ]
case "$(findmnt -no FSTYPE /)" in ext4|xfs) ;; *) echo "root filesystem is outside support policy" >&2; exit 1 ;; esac
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
AIOPS_RUN_PRODUCTION_PLAYWRIGHT=0 AIOPS_PRESERVE_ON_FAILURE=1 ./scripts/test-production-compose.sh
mkdir -p .omx/evidence/production-ga/GA-R0-001
export AIOPS_BACKEND_CONFIG_ID="$(docker image inspect agent-ops-backend:r0-ci --format '{{.Id}}')"
export AIOPS_FRONTEND_CONFIG_ID="$(docker image inspect agent-ops-frontend:r0-ci --format '{{.Id}}')"
export AIOPS_DOCKER_VERSION="$(docker version --format '{{.Server.Version}}')"
export AIOPS_COMPOSE_VERSION="$(docker compose version --short)"
export AIOPS_KERNEL="$(uname -r)"
export AIOPS_FILESYSTEM="$(findmnt -no FSTYPE /)"
python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

name = os.environ["AIOPS_EVIDENCE_NAME"]
payload = {
    "test_id": "GA-R0-001",
    "requirement": "clean Ubuntu x86_64 production fail-closed, setup, health and topology gate",
    "automation": "signed cloud image -> exact Docker packages -> pytest + static probe + production Compose runtime",
    "environment": f"Ubuntu {os.environ['AIOPS_EXPECTED_OS']} x86_64 clean KVM VM",
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
    "filesystem": os.environ["AIOPS_FILESYSTEM"],
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
  chmod 0600 "$manifest"
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
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2204-$RUN_ID.log" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2404-$RUN_ID.log" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2204.json" \
    "$EVIDENCE_DIR/agent-ops-r0-ubuntu2404.json" <<'PY'
import re
import sys
from pathlib import Path

patterns = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"AIOPS_(?:SESSION_SECRET|LLM_API_KEY|SETUP_TOKEN)=[^\s]+"),
    re.compile(r'(?i)"(?:(?:llm[_-]?)?api[_-]?key|session[_-]?secret|setup[_-]?token|password)"\s*:\s*"(?!\*{3}|<redacted>)[^"\s]{8,}"'),
)
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
  python3 - "$ROOT_DIR" "$EVIDENCE_DIR" "$COMMIT_SHA" "$SOURCE_ARCHIVE_SHA256" "$REGISTRY_IMAGE" "$RUN_ID" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
evidence_dir = Path(sys.argv[2])
commit, source_sha, registry_image, run_id = sys.argv[3:]
names = ("agent-ops-r0-ubuntu2204", "agent-ops-r0-ubuntu2404")
runs = [json.loads((evidence_dir / f"{name}.json").read_text(encoding="utf-8")) for name in names]
for run in runs:
    assert run["result"] == "passed"
    assert run["commit_sha"] == commit
    assert run["source_archive_sha256"] == source_sha
    assert run["registry_image"] == registry_image
assert len({run["release_digest"] for run in runs}) == 1
assert len({run["frontend_release_digest"] for run in runs}) == 1

hashes = {}
for run in runs:
    for key in ("evidence_path", "raw_log_path"):
        path = root / run[key]
        hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()

payload = {
    "test_id": "GA-R0-001",
    "requirement": "production fail-closed and clean Ubuntu 22.04/24.04 matrix on identical OCI manifests",
    "automation": "scripts/test-r0-clean-vm-matrix.sh",
    "environment": [run["environment"] for run in runs],
    "fixture_or_seed": "GPG-verified Ubuntu cloud images and exact commit archive",
    "sample_size_or_duration": "2 clean VMs; 52 backend tests and one production Compose lifecycle per VM",
    "expected": "both clean hosts pass with the same backend/frontend OCI digests",
    "evidence_path": ".omx/evidence/production-ga/GA-R0-001/matrix-summary.json",
    "owner": "backend and release leads",
    "release_digest": runs[0]["release_digest"],
    "frontend_release_digest": runs[0]["frontend_release_digest"],
    "result": "passed",
    "executed_at": datetime.now(timezone.utc).isoformat(),
    "commit_sha": commit,
    "source_archive_sha256": source_sha,
    "registry_image": registry_image,
    "run_id": run_id,
    "evidence_sha256": hashes,
    "runs": runs,
}
output = evidence_dir / "matrix-summary.json"
output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
output.chmod(0o600)
PY
}

cleanup_on_exit() {
  local status=$? state
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
    fi
    if ! cleanup_registry; then
      echo "CRITICAL: ephemeral registry cleanup failed: $REGISTRY_NAME on port $REGISTRY_PORT" >&2
      docker container inspect "$REGISTRY_NAME" >&2 2>/dev/null || true
      ss -H -ltnp | awk -v suffix=":$REGISTRY_PORT" '$4 ~ suffix "$"' >&2 || true
    fi
    if ! scan_current_evidence_for_secrets; then
      echo "CRITICAL: retained failure evidence may contain secret material; keep it mode 0600 and review before sharing" >&2
    fi
    echo "run log retained: $RUN_LOG" >&2
  fi
  exit "$status"
}

main() {
  local row suffix release os_variant expected_os engine_version docker_package
  local containerd_package buildx_package compose_package compose_version
  local name evidence_name base_image base_image_sha256 ip

  require_host
  prepare_state
  trap cleanup_on_exit EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  prepare_source
  build_diagnostic_images
  push_release_images
  for row in "${MATRIX[@]}"; do
    IFS='|' read -r suffix release os_variant expected_os engine_version docker_package \
      containerd_package buildx_package compose_package compose_version <<< "$row"
    name="agent-ops-r0-$RUN_ID-$suffix"
    evidence_name="agent-ops-r0-$suffix"
    base_image="$(download_verified_cloud_image "$release")"
    base_image_sha256="$(sudo sha256sum "$base_image" | awk '{print $1}')"
    CURRENT_VM_NAME="$name"
    CURRENT_VM_IP=""
    echo "[$name] creating clean Ubuntu $expected_os VM"
    create_vm "$name" "$base_image" "$os_variant"
    ip="$(wait_for_vm_ip "$name")"
    CURRENT_VM_IP="$ip"
    echo "[$name] waiting for cloud-init at $ip"
    wait_for_cloud_init "$ip"
    echo "[$name] installing Docker $engine_version"
    install_docker_exact "$ip" "$engine_version" "$docker_package" "$containerd_package" \
      "$buildx_package" "$compose_package" "$compose_version"
    sync_checkout "$ip"
    echo "[$name] transferring immutable diagnostic OCI manifests"
    transfer_diagnostic_images "$ip"
    echo "[$name] running R0 blocking gates"
    run_vm_gate "$name" "$evidence_name" "$ip" "$expected_os" "$engine_version" \
      "$docker_package" "$containerd_package" "$buildx_package" "$compose_package" \
      "$release" "$base_image_sha256"
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
  scan_current_evidence_for_secrets
  cleanup_registry || fail "ephemeral registry or listening port was not removed"
  REGISTRY_STARTED=0
  cleanup_success_artifacts
  generate_matrix_summary
  echo "R0 clean-VM matrix passed"
}

main "$@"
