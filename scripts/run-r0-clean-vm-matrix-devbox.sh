#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVBOX_SSH_TARGET="${DEVBOX_SSH_TARGET:-yanyan-devbox}"
DEVBOX_PROJECT_DIR="${DEVBOX_PROJECT_DIR:-/home/ubuntu/Documents/trae_projects/业务效率工具/Agent运维平台}"
REMOTE_INPUT_DIR="${AIOPS_R0_REMOTE_INPUT_DIR:-/home/ubuntu/.cache/agent-ops-r0-vm/incoming}"
RELEASE_MANIFEST="${AIOPS_R0_RELEASE_MANIFEST:-}"
origin_url="$(git -C "$ROOT_DIR" remote get-url origin)"
expected_repository="$(python3 - "$origin_url" <<'PY'
import re
import sys

url = sys.argv[1]
patterns = (
    r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$",
    r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$",
    r"^ssh://git@github\.com/([^/]+/[^/]+?)(?:\.git)?$",
)
for pattern in patterns:
    match = re.fullmatch(pattern, url)
    if match:
        print(match.group(1))
        raise SystemExit(0)
raise SystemExit("origin must be a GitHub owner/repository URL")
PY
)"

[ -z "$(git -C "$ROOT_DIR" status --porcelain --untracked-files=all)" ] || {
  echo "the local worktree must be clean before clean-VM evidence can be created" >&2
  exit 1
}

commit="$(git -C "$ROOT_DIR" rev-parse --verify HEAD)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]]
[ -n "$RELEASE_MANIFEST" ] && [ -f "$RELEASE_MANIFEST" ] || {
  echo "AIOPS_R0_RELEASE_MANIFEST must point to a canonical GA-R0-002 manifest" >&2
  exit 1
}
python3 "$ROOT_DIR/scripts/validate-ga-manifest.py" \
  --expected-repository "$expected_repository" "$RELEASE_MANIFEST"
release_manifest_sha="$(sha256sum "$RELEASE_MANIFEST" | awk '{print $1}')"
archive="$(mktemp "${TMPDIR:-/tmp}/agent-ops-r0-source.XXXXXX.tar")"
trap 'rm -f "$archive"' EXIT
git -C "$ROOT_DIR" archive --format=tar \
  "--add-virtual-file=.aiops-source-commit:$commit" \
  --output "$archive" "$commit"
archive_sha="$(sha256sum "$archive" | awk '{print $1}')"
remote_archive="$REMOTE_INPUT_DIR/source-$commit-$archive_sha.tar"
remote_release_manifest="$REMOTE_INPUT_DIR/release-$commit-$release_manifest_sha.json"
local_evidence_dir="$ROOT_DIR/.omx/evidence/production-ga/GA-R0-001"
remote_evidence_dir="$DEVBOX_PROJECT_DIR/.omx/evidence/production-ga/GA-R0-001/"
printf -v remote_evidence_quoted '%q' "$remote_evidence_dir"

ssh "$DEVBOX_SSH_TARGET" "install -d -m 0700 '$REMOTE_INPUT_DIR'"
scp "$archive" "$DEVBOX_SSH_TARGET:$remote_archive"
scp "$RELEASE_MANIFEST" "$DEVBOX_SSH_TARGET:$remote_release_manifest"

if ssh "$DEVBOX_SSH_TARGET" \
  "cd '$DEVBOX_PROJECT_DIR' && AIOPS_SOURCE_COMMIT='$commit' AIOPS_SOURCE_ARCHIVE='$remote_archive' AIOPS_SOURCE_ARCHIVE_SHA256='$archive_sha' AIOPS_R0_RELEASE_MANIFEST='$remote_release_manifest' AIOPS_R0_EXPECTED_REPOSITORY='$expected_repository' ./scripts/test-r0-clean-vm-matrix.sh"; then
  mkdir -p "$local_evidence_dir"
  rsync -a \
    --exclude manifest.json \
    --exclude provider-policy.unreviewed.snapshot \
    --exclude .matrix.lock \
    "$DEVBOX_SSH_TARGET:$remote_evidence_quoted" "$local_evidence_dir/"
  ssh "$DEVBOX_SSH_TARGET" "rm -f '$remote_archive' '$remote_release_manifest'"
else
  status=$?
  echo "source archive retained for failed-run forensics: $DEVBOX_SSH_TARGET:$remote_archive" >&2
  echo "release manifest retained for failed-run forensics: $DEVBOX_SSH_TARGET:$remote_release_manifest" >&2
  exit "$status"
fi
