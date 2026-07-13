#!/usr/bin/env bash
set -euo pipefail
set +x

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="agent-ops-dev-backend.service"
UNIT_SOURCE="$ROOT_DIR/deploy/systemd/agent-ops-dev-backend.service"
DROPIN_SOURCE="$ROOT_DIR/deploy/systemd/agent-ops-dev-backend-deepseek.conf"
UNIT_TARGET="/etc/systemd/system/$SERVICE_NAME"
DROPIN_DIR="/etc/systemd/system/$SERVICE_NAME.d"
DROPIN_TARGET="$DROPIN_DIR/20-deepseek.conf"
CREDENTIAL_DIR="/etc/credstore.encrypted"
CREDENTIAL_PATH="$CREDENTIAL_DIR/agent-ops-deepseek.cred"
BACKEND_PATTERN="$ROOT_DIR/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8080"

usage() {
  cat <<'EOF'
Usage: ./scripts/manage-dev-deepseek.sh install|remove|status

install  Read a DeepSeek API key without echo, encrypt it with TPM2, and enable
         the systemd-managed live DeepSeek development backend.
remove   Delete the encrypted credential and return the backend to mock mode.
status   Show service, encrypted-credential, and public health status only.

For non-interactive installation, provide exactly one key line on stdin.
EOF
}

require_devbox() {
  if [[ "$(uname -s)" != "Linux" ]] || ! command -v systemd-creds >/dev/null 2>&1; then
    echo "this command must run on the Linux development machine with systemd-creds" >&2
    exit 1
  fi
  if [[ ! -x "$ROOT_DIR/backend/.venv/bin/uvicorn" ]]; then
    echo "backend virtual environment is missing: $ROOT_DIR/backend/.venv" >&2
    exit 1
  fi
  sudo -n true
}

install_base_unit() {
  sudo install -o root -g root -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
}

stop_legacy_backend() {
  local pid
  while IFS= read -r pid; do
    [[ -n "$pid" ]] || continue
    kill -TERM "$pid"
  done < <(pgrep -f "$BACKEND_PATTERN" || true)

  for _ in {1..30}; do
    if ! pgrep -f "$BACKEND_PATTERN" >/dev/null 2>&1; then
      return
    fi
    sleep 0.2
  done

  echo "legacy backend did not stop within six seconds" >&2
  exit 1
}

wait_for_health() {
  for _ in {1..40}; do
    if curl --fail --silent --show-error \
      http://127.0.0.1:8080/health/live >/dev/null 2>&1; then
      return
    fi
    sleep 0.25
  done

  sudo systemctl status "$SERVICE_NAME" --no-pager -l >&2 || true
  echo "backend health check did not pass within ten seconds" >&2
  exit 1
}

read_api_key() {
  local api_key
  if [[ -t 0 ]]; then
    IFS= read -r -s -p "DeepSeek API key: " api_key || [[ -n "$api_key" ]]
    printf '\n' >&2
  else
    IFS= read -r api_key || [[ -n "$api_key" ]]
  fi
  if [[ "$api_key" != sk-* ]] || (( ${#api_key} < 20 )); then
    unset api_key
    echo "invalid DeepSeek API key format" >&2
    exit 1
  fi
  printf '%s' "$api_key"
  unset api_key
}

install_deepseek() {
  local encrypted_tmp
  encrypted_tmp="/tmp/agent-ops-deepseek.$$.cred"
  trap "sudo rm -f '$encrypted_tmp'" EXIT

  install_base_unit
  sudo install -d -o root -g root -m 0700 "$CREDENTIAL_DIR" "$DROPIN_DIR"
  read_api_key | sudo systemd-creds encrypt \
    --name=deepseek_api_key \
    --with-key=tpm2 \
    - "$encrypted_tmp"
  sudo install -o root -g root -m 0600 "$encrypted_tmp" "$CREDENTIAL_PATH"
  sudo install -o root -g root -m 0644 "$DROPIN_SOURCE" "$DROPIN_TARGET"
  sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  stop_legacy_backend
  sudo systemctl daemon-reload
  sudo systemctl enable --now "$SERVICE_NAME"
  wait_for_health
  echo "DeepSeek development credential installed and backend health check passed"
}

remove_deepseek() {
  install_base_unit
  sudo systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  sudo rm -f "$DROPIN_TARGET" "$CREDENTIAL_PATH"
  sudo systemctl daemon-reload
  stop_legacy_backend
  sudo systemctl enable --now "$SERVICE_NAME"
  wait_for_health
  echo "DeepSeek development credential removed; backend returned to mock mode"
}

show_status() {
  sudo systemctl is-active "$SERVICE_NAME"
  sudo systemctl is-enabled "$SERVICE_NAME"
  if sudo test -s "$CREDENTIAL_PATH"; then
    sudo stat -c 'encrypted credential: %n mode=%a owner=%U:%G bytes=%s' "$CREDENTIAL_PATH"
  else
    echo "encrypted credential: absent"
  fi
  sudo systemctl show "$SERVICE_NAME" \
    --property=Environment \
    --property=LoadCredentialEncrypted \
    --no-pager
  curl --fail --silent --show-error http://127.0.0.1:8080/health/live
  printf '\n'
}

case "${1:-}" in
  install)
    require_devbox
    install_deepseek
    ;;
  remove)
    require_devbox
    remove_deepseek
    ;;
  status)
    require_devbox
    show_status
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
