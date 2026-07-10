#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVBOX_SSH_TARGET="${DEVBOX_SSH_TARGET:-yanyan-devbox}"
DEVBOX_PROJECT_DIR="${DEVBOX_PROJECT_DIR:-/home/ubuntu/Documents/trae_projects/业务效率工具/Agent运维平台}"
REMOTE_INPUT_DIR="${AIOPS_R0_REMOTE_INPUT_DIR:-/home/ubuntu/.cache/agent-ops-r0-vm/incoming}"

[ -z "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ] || {
  echo "the local worktree must be clean before clean-VM evidence can be created" >&2
  exit 1
}

commit="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]]
archive="$(mktemp "${TMPDIR:-/tmp}/agent-ops-r0-source.XXXXXX.tar")"
trap 'rm -f "$archive"' EXIT
git -C "$ROOT_DIR" archive --format=tar \
  "--add-virtual-file=.aiops-source-commit:$commit" \
  --output "$archive" "$commit"
archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
remote_archive="$REMOTE_INPUT_DIR/source-$commit-$archive_sha.tar"

ssh "$DEVBOX_SSH_TARGET" "install -d -m 0700 '$REMOTE_INPUT_DIR'"
scp "$archive" "$DEVBOX_SSH_TARGET:$remote_archive"

if ssh "$DEVBOX_SSH_TARGET" \
  "cd '$DEVBOX_PROJECT_DIR' && AIOPS_SOURCE_COMMIT='$commit' AIOPS_SOURCE_ARCHIVE='$remote_archive' AIOPS_SOURCE_ARCHIVE_SHA256='$archive_sha' ./scripts/test-r0-clean-vm-matrix.sh"; then
  ssh "$DEVBOX_SSH_TARGET" "rm -f '$remote_archive'"
else
  status=$?
  echo "source archive retained for failed-run forensics: $DEVBOX_SSH_TARGET:$remote_archive" >&2
  exit "$status"
fi
