#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_ROOT="${AIOPS_R0_VM_STATE_ROOT:-$HOME/.cache/agent-ops-r0-vm}"
IMAGE_ROOT="${AIOPS_R0_VM_IMAGE_ROOT:-/var/lib/libvirt/images/agent-ops-r0}"
KEEP_VMS="${AIOPS_R0_KEEP_VMS:-0}"
VM_MEMORY_MIB="${AIOPS_R0_VM_MEMORY_MIB:-4096}"
VM_VCPUS="${AIOPS_R0_VM_VCPUS:-2}"
VM_DISK_SIZE="${AIOPS_R0_VM_DISK_SIZE:-40G}"
EVIDENCE_DIR="$ROOT_DIR/.omx/evidence/production-ga/GA-R0-001"
SSH_KEY="$STATE_ROOT/id_ed25519"
KNOWN_HOSTS="$STATE_ROOT/known_hosts"
HOST_BACKEND_IMAGE="${AIOPS_R0_HOST_BACKEND_IMAGE:-agent-ops-backend:r0-matrix}"
HOST_FRONTEND_IMAGE="${AIOPS_R0_HOST_FRONTEND_IMAGE:-agent-ops-frontend:r0-matrix}"
HOST_TEST_IMAGE="${AIOPS_R0_HOST_TEST_IMAGE:-agent-ops-backend-test:r0-matrix}"
REGISTRY_NAME="agent-ops-r0-registry"
REGISTRY_PORT="${AIOPS_R0_REGISTRY_PORT:-55005}"
HOST_REGISTRY="127.0.0.1:$REGISTRY_PORT"
VM_REGISTRY="192.168.122.1:$REGISTRY_PORT"
HOST_BACKEND_DIGEST=""
HOST_FRONTEND_DIGEST=""
if [ -n "${AIOPS_SOURCE_COMMIT:-}" ]; then
  COMMIT_SHA="$AIOPS_SOURCE_COMMIT"
elif COMMIT_SHA="$(git -C "$ROOT_DIR" rev-parse --verify HEAD 2>/dev/null)"; then
  :
else
  echo "AIOPS_SOURCE_COMMIT is required when the checkout has no .git directory" >&2
  exit 1
fi

MATRIX=(
  "ubuntu2204|jammy|ubuntu22.04|22.04|28"
  "ubuntu2404|noble|ubuntu24.04|24.04|29"
)

fail() {
  echo "R0 clean-VM matrix failed: $*" >&2
  exit 1
}

require_host() {
  [ "$(uname -s)" = "Linux" ] || fail "this harness requires a Linux KVM host"
  [ -e /dev/kvm ] || fail "/dev/kvm is unavailable"
  sudo -n true 2>/dev/null || fail "passwordless sudo is required on the lab host"
  command -v curl >/dev/null || fail "curl is required"
  command -v rsync >/dev/null || fail "rsync is required"
  command -v ssh >/dev/null || fail "ssh is required"
  command -v qemu-img >/dev/null || fail "qemu-img is required"
  command -v virt-install >/dev/null || fail "virt-install is required"
  if ! command -v cloud-localds >/dev/null; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y cloud-image-utils
  fi
  sudo virsh net-info default >/dev/null 2>&1 || fail "libvirt default network is missing"
  if [ "$(sudo virsh net-info default | awk '/Active:/ {print $2}')" != "yes" ]; then
    sudo virsh net-start default >/dev/null
  fi
}

prepare_state() {
  mkdir -p "$STATE_ROOT/cache" "$EVIDENCE_DIR"
  : > "$KNOWN_HOSTS"
  chmod 600 "$KNOWN_HOSTS"
  if [ ! -f "$SSH_KEY" ]; then
    ssh-keygen -q -t ed25519 -N '' -f "$SSH_KEY"
  fi
  sudo install -d -m 0755 "$IMAGE_ROOT"
}

build_diagnostic_images() {
  docker build --pull --tag "$HOST_BACKEND_IMAGE" --file "$ROOT_DIR/backend/Dockerfile" "$ROOT_DIR"
  docker build --pull --tag "$HOST_FRONTEND_IMAGE" --file "$ROOT_DIR/frontend/Dockerfile" "$ROOT_DIR"
  docker build --pull --tag "$HOST_TEST_IMAGE" --file "$ROOT_DIR/backend/Dockerfile.test" "$ROOT_DIR"
}

cleanup_registry() {
  docker rm -f "$REGISTRY_NAME" >/dev/null 2>&1 || true
  docker image rm \
    "$HOST_REGISTRY/agent-ops-backend:$COMMIT_SHA" \
    "$HOST_REGISTRY/agent-ops-frontend:$COMMIT_SHA" >/dev/null 2>&1 || true
}

push_release_images() {
  local backend_ref="$HOST_REGISTRY/agent-ops-backend:$COMMIT_SHA"
  local frontend_ref="$HOST_REGISTRY/agent-ops-frontend:$COMMIT_SHA"
  local backend_repo frontend_repo
  cleanup_registry
  docker pull registry:2 >/dev/null
  docker run --detach --rm \
    --name "$REGISTRY_NAME" \
    --publish "127.0.0.1:$REGISTRY_PORT:5000" \
    --publish "192.168.122.1:$REGISTRY_PORT:5000" \
    registry:2 >/dev/null
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
  local sums="$STATE_ROOT/cache/${release}-SHA256SUMS"
  local verified="$STATE_ROOT/cache/$filename.verified"

  curl --fail --location --retry 3 "$base_url/SHA256SUMS" --output "$sums"
  if [ ! -f "$cache_image" ]; then
    curl --fail --location --retry 3 --continue-at - "$base_url/$filename" --output "$cache_image"
  fi
  if ! grep -E "[ *]${filename}$" "$sums" | (cd "$STATE_ROOT/cache" && sha256sum --check --status -); then
    rm -f "$cache_image" "$verified"
    fail "official SHA-256 verification failed for $filename"
  fi
  touch "$verified"
  sudo install -m 0644 "$cache_image" "$IMAGE_ROOT/$filename"
  printf '%s\n' "$IMAGE_ROOT/$filename"
}

remove_namespaced_vm() {
  local name="$1"
  if sudo virsh dominfo "$name" >/dev/null 2>&1; then
    sudo virsh destroy "$name" >/dev/null 2>&1 || true
    sudo virsh undefine "$name" --nvram >/dev/null 2>&1 || \
      sudo virsh undefine "$name" >/dev/null 2>&1 || true
  fi
  sudo rm -f "$IMAGE_ROOT/$name.qcow2" "$IMAGE_ROOT/$name-seed.img"
}

create_cloud_init_seed() {
  local name="$1"
  local user_data="$STATE_ROOT/$name-user-data.yaml"
  local meta_data="$STATE_ROOT/$name-meta-data.yaml"
  local seed="$STATE_ROOT/$name-seed.img"
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
instance-id: $name-$(date +%s)
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

  remove_namespaced_vm "$name"
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

install_docker_major() {
  local ip="$1"
  local docker_major="$2"
  local -a options
  mapfile -t options < <(ssh_args)
  ssh "${options[@]}" "aiops@$ip" bash -s -- "$docker_major" <<'REMOTE'
set -euo pipefail
docker_major="$1"
. /etc/os-release
sudo install -m 0755 -d /etc/apt/keyrings
docker_gpg="$(mktemp)"
trap 'rm -f "$docker_gpg"' EXIT
curl --fail --silent --show-error --location \
  --retry 8 --retry-all-errors --connect-timeout 20 --max-time 180 \
  https://download.docker.com/linux/ubuntu/gpg --output "$docker_gpg"
gpg --show-keys "$docker_gpg" >/dev/null
sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg "$docker_gpg"
sudo chmod a+r /etc/apt/keyrings/docker.gpg
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
  "$(dpkg --print-architecture)" "$VERSION_CODENAME" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get -o Acquire::Retries=5 update -qq
docker_version="$(apt-cache madison docker-ce | awk '{print $3}' | grep -E "^5:${docker_major}\\." | head -1)"
[ -n "$docker_version" ] || { echo "Docker ${docker_major}.x is unavailable for $VERSION_CODENAME" >&2; exit 1; }
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  "docker-ce=$docker_version" \
  "docker-ce-cli=$docker_version" \
  containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker aiops
sudo systemctl enable --now docker
REMOTE
}

sync_checkout() {
  local ip="$1"
  local ssh_command
  ssh_command="ssh -i $SSH_KEY -o UserKnownHostsFile=$KNOWN_HOSTS -o StrictHostKeyChecking=accept-new -o BatchMode=yes"
  rsync -az --delete \
    --exclude '.DS_Store' \
    --exclude '.env' \
    --exclude '.git/' \
    --exclude '.omx/evidence/' \
    --exclude '.omx/state/' \
    --exclude '.omx/ultragoal/' \
    --exclude '.pytest_cache/' \
    --exclude 'backend/.venv/' \
    --exclude 'backend/data/' \
    --exclude 'data/' \
    --exclude 'demo/' \
    --exclude 'frontend/dist/' \
    --exclude 'frontend/node_modules/' \
    -e "$ssh_command" \
    "$ROOT_DIR/" "aiops@$ip:/home/aiops/agent-ops/"
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
printf '{"insecure-registries":["%s"]}\n' "$registry" | sudo tee /etc/docker/daemon.json >/dev/null
sudo systemctl restart docker
docker info --format '{{json .RegistryConfig.IndexConfigs}}' | grep -F "$registry" >/dev/null
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
  local ip="$2"
  local expected_os="$3"
  local docker_major="$4"
  local log="$EVIDENCE_DIR/$name.log"
  local manifest="$EVIDENCE_DIR/$name.json"
  local -a options
  mapfile -t options < <(ssh_args)

  set +e
  ssh "${options[@]}" "aiops@$ip" \
    "AIOPS_EXPECTED_OS='$expected_os' AIOPS_EXPECTED_DOCKER_MAJOR='$docker_major' AIOPS_EXPECTED_BACKEND_DIGEST='$HOST_BACKEND_DIGEST' AIOPS_EXPECTED_FRONTEND_DIGEST='$HOST_FRONTEND_DIGEST' AIOPS_BACKEND_IMAGE_REF='$VM_REGISTRY/agent-ops-backend@$HOST_BACKEND_DIGEST' AIOPS_FRONTEND_IMAGE_REF='$VM_REGISTRY/agent-ops-frontend@$HOST_FRONTEND_DIGEST' AIOPS_SOURCE_COMMIT='$COMMIT_SHA' bash -s" \
    <<'REMOTE' 2>&1 | tee "$log"
set -euo pipefail
cd /home/aiops/agent-ops
export AIOPS_COMMIT_SHA="$AIOPS_SOURCE_COMMIT"
. /etc/os-release
[ "$VERSION_ID" = "$AIOPS_EXPECTED_OS" ]
[ "$(uname -m)" = "x86_64" ]
case "$(findmnt -no FSTYPE /)" in ext4|xfs) ;; *) echo "root filesystem is outside support policy" >&2; exit 1 ;; esac
[ "$(docker version --format '{{.Server.Version}}' | cut -d. -f1)" = "$AIOPS_EXPECTED_DOCKER_MAJOR" ]
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
AIOPS_RUN_PRODUCTION_PLAYWRIGHT=0 ./scripts/test-production-compose.sh
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

name = os.uname().nodename
payload = {
    "test_id": "GA-R0-001",
    "requirement": "clean Ubuntu x86_64 production fail-closed, setup, health and topology gate",
    "automation": "scripts/test-r0-clean-vm-matrix.sh -> pytest + static probe + Docker build + production Compose runtime",
    "environment": f"Ubuntu {os.environ['AIOPS_EXPECTED_OS']} x86_64 clean KVM VM",
    "fixture_or_seed": "verified official Ubuntu cloud image, ephemeral SQLite volume, one-time setup token, intentionally failing provider evidence",
    "sample_size_or_duration": "full backend suite and one complete production Compose setup/restart/runtime cycle",
    "expected": "all R0 local gates pass while Worker, Agent, mutation and unverified LLM remain fail-closed",
    "evidence_path": f".omx/evidence/production-ga/GA-R0-001/{name}.json",
    "owner": "backend and release leads",
    "release_digest": os.environ["AIOPS_EXPECTED_BACKEND_DIGEST"],
    "result": "passed",
    "executed_at": datetime.now(timezone.utc).isoformat(),
    "commit_sha": os.environ["AIOPS_SOURCE_COMMIT"],
    "kernel": os.environ["AIOPS_KERNEL"],
    "filesystem": os.environ["AIOPS_FILESYSTEM"],
    "docker_engine": os.environ["AIOPS_DOCKER_VERSION"],
    "docker_compose": os.environ["AIOPS_COMPOSE_VERSION"],
    "backend_image_ref": os.environ["AIOPS_BACKEND_IMAGE_REF"],
    "frontend_image_ref": os.environ["AIOPS_FRONTEND_IMAGE_REF"],
    "backend_config_id": os.environ["AIOPS_BACKEND_CONFIG_ID"],
    "frontend_config_id": os.environ["AIOPS_FRONTEND_CONFIG_ID"],
    "frontend_release_digest": os.environ["AIOPS_EXPECTED_FRONTEND_DIGEST"],
}
path = Path(payload["evidence_path"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
REMOTE
  local pipeline_status=(${PIPESTATUS[*]})
  set -e
  [ "${pipeline_status[0]}" -eq 0 ] || fail "$name validation failed; VM preserved at $ip"

  scp "${options[@]}" "aiops@$ip:/home/aiops/agent-ops/.omx/evidence/production-ga/GA-R0-001/$name.json" "$manifest"
}

main() {
  require_host
  prepare_state
  trap cleanup_registry EXIT
  build_diagnostic_images
  push_release_images
  for row in "${MATRIX[@]}"; do
    IFS='|' read -r suffix release os_variant expected_os docker_major <<< "$row"
    name="agent-ops-r0-$suffix"
    base_image="$(download_verified_cloud_image "$release")"
    echo "[$name] creating clean Ubuntu $expected_os VM"
    create_vm "$name" "$base_image" "$os_variant"
    ip="$(wait_for_vm_ip "$name")"
    echo "[$name] waiting for cloud-init at $ip"
    wait_for_cloud_init "$ip"
    echo "[$name] installing Docker $docker_major.x"
    install_docker_major "$ip" "$docker_major"
    sync_checkout "$ip"
    echo "[$name] transferring immutable diagnostic image IDs"
    transfer_diagnostic_images "$ip"
    echo "[$name] running R0 blocking gates"
    run_vm_gate "$name" "$ip" "$expected_os" "$docker_major"
    if [ "$KEEP_VMS" = "1" ]; then
      echo "[$name] passed and retained at $ip"
    else
      remove_namespaced_vm "$name"
      echo "[$name] passed and was destroyed"
    fi
  done
  echo "R0 clean-VM matrix passed"
}

main "$@"
