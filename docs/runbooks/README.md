# Production runbook index (R0)

R0 is `NO-GO` for public production use until all gates in
`.omx/specs/r0-production-baseline.md` pass. These commands define the intended
installation shape; they are not evidence that GA has been reached.

## 1. Preflight

Use an x86_64 clean VM from `docs/support-matrix.md`. Confirm local ext4/xfs,
the frozen Docker/Compose versions, DNS and a valid TLS certificate. Do not use
NFS/SMB for the application data directory.

```bash
docker version
docker compose version
docker compose --env-file .env.production -f deploy/compose.prod.yml config
```

## 2. Prepare configuration and secret files

```bash
cp .env.production.example .env.production
install -d -m 0700 deploy/secrets
umask 077
openssl rand -base64 48 > deploy/secrets/session_secret
openssl rand -base64 48 > deploy/secrets/setup_token
```

Place the TLS certificate, TLS private key and live-provider API key at the
paths configured in `.env.production`. Never paste their contents into the env
file. Set `AIOPS_SETUP_TOKEN_EXPIRES_AT` to an ISO-8601 UTC timestamp no more
than 60 minutes ahead; operational policy should use 15 minutes.

Native Linux Compose preserves file-secret ownership. Before any supported
start, set backend-readable secrets to UID/GID 10001 and frontend TLS files to
UID/GID 101; all must be owner-read-only. The deployment wrapper rejects any
other ownership or mode:

```bash
sudo chown 10001:10001 deploy/secrets/session_secret \
  deploy/secrets/deepseek_api_key deploy/secrets/llm-contract.json \
  deploy/secrets/setup_token
sudo chmod 0400 deploy/secrets/session_secret \
  deploy/secrets/deepseek_api_key deploy/secrets/llm-contract.json \
  deploy/secrets/setup_token
sudo chown 101:101 deploy/secrets/tls_cert.pem deploy/secrets/tls_key.pem
sudo chmod 0400 deploy/secrets/tls_cert.pem deploy/secrets/tls_key.pem
```

Before using a live LLM, complete the probe manifest in
`docs/support-matrix.md`. A successful HTTP request alone is insufficient.

Copy `docs/security/llm-provider-policy.example.json` to a protected working
file, replace every placeholder, save the exact reviewed provider-policy page
as a snapshot, and record that snapshot's SHA-256 plus all three owner
approvals. Set `AIOPS_RELEASE_DIGEST` to the immutable backend image digest and
`AIOPS_EVIDENCE_RUNNER_IDENTITY` to the accountable operator or CI identity,
then run:

```bash
./scripts/probe-llm-contract.py \
  --policy-file /protected/path/llm-provider-policy.json \
  --policy-snapshot-file /protected/path/provider-policy.snapshot \
  --output .omx/evidence/production-ga/GA-R0-001/llm-contract.json
```

The probe verifies the live policy URL still hashes to the reviewed snapshot,
authenticated model discovery, exact model, typed tool calling, streaming,
bounded timeout/retry, invalid-tool rejection, seeded-secret redaction and
usage/cost limits. It writes the manifest read-only and prints the exact
`AIOPS_LLM_EVIDENCE_SHA256` value. Copy the manifest to the protected host path
configured by `AIOPS_LLM_EVIDENCE_FILE_HOST`; the backend verifies its digest,
expiry, provider, model and release binding before any production diagnosis can
call the provider. Missing or stale evidence keeps readiness at
`llm_unverified` and makes diagnosis return 503.

## 3. First-time enrollment

Start the one-time setup override only on an uninitialized data volume. The
supported installation path is the verification wrapper below; direct
`docker compose up` is prohibited because it can bypass signature and
attestation verification. Set the exact certificate identity for the release
tag and the GitHub Actions OIDC issuer. The wrapper requires Cosign 3.1.1,
validates provenance/SBOM semantics, checks the full Compose topology before
start, and verifies the resulting runtime allowlist after start:

```bash
AIOPS_COSIGN_CERTIFICATE_IDENTITY='https://github.com/OWNER/REPO/.github/workflows/r0-ci.yml@refs/tags/vX.Y.Z' \
AIOPS_COSIGN_OIDC_ISSUER='https://token.actions.githubusercontent.com' \
./scripts/deploy-production.sh --env-file .env.production --setup up
```

Verify `/health/live`, retrieve `/api/csrf`, then call `/api/setup/enroll` over
the HTTPS origin with the CSRF cookie/header, the one-time token, the desired
administrator username and a unique strong password. Do not put those values
in shell history or automation logs.

After enrollment succeeds, immediately remove the setup override and token:

```bash
docker compose --env-file .env.production \
  -f deploy/compose.prod.yml \
  -f deploy/compose.setup.yml \
  down
rm -f deploy/secrets/setup_token
# Remove AIOPS_SETUP_TOKEN_* from .env.production, then verify and start the normal profile.
AIOPS_COSIGN_CERTIFICATE_IDENTITY='https://github.com/OWNER/REPO/.github/workflows/r0-ci.yml@refs/tags/vX.Y.Z' \
AIOPS_COSIGN_OIDC_ISSUER='https://token.actions.githubusercontent.com' \
./scripts/deploy-production.sh --env-file .env.production up
```

An initialized production instance must fail startup if a setup token remains.

## 4. Verification

```bash
curl --fail --silent --show-error https://ops.example.com/health/live
curl --fail --silent --show-error https://ops.example.com/health/ready
curl --fail --silent --show-error https://ops.example.com/version
docker compose --env-file .env.production -f deploy/compose.prod.yml ps
```

Expected R0 readiness explicitly reports Worker and Agent as
`disabled_by_release_gate` and the LLM as unverified until its live gate is
recorded. Mutating/destructive capabilities must be disabled and must never
write a success task/event.

For the R0 clean-host gate, run the following only on the designated Linux KVM
lab host with passwordless sudo and the libvirt `default` network. It downloads
Ubuntu official cloud images, verifies their published SHA-256 sums, creates
namespaced disposable guests and destroys them after success:

```bash
AIOPS_SOURCE_COMMIT="$(git rev-parse HEAD)" \
  ./scripts/test-r0-clean-vm-matrix.sh
```

When the KVM development machine intentionally has no `.git` directory, run
the local wrapper instead. It rejects a dirty worktree, creates a SHA-256-bound
archive from the exact commit, transfers only that archive, and invokes the same
remote gate. The archive is integrity-bound to the authenticated SSH transfer;
it is not described as independently signed:

```bash
./scripts/run-r0-clean-vm-matrix-devbox.sh
```

The harness must report the same backend and frontend OCI manifest digests for
Ubuntu 22.04/Docker 28 and Ubuntu 24.04/Docker 29. A `docker save/load` config
ID or a container pretending to be a VM is not accepted evidence.

## 5. Stop, rollback and recovery limitations

```bash
docker compose --env-file .env.production -f deploy/compose.prod.yml down
```

R0 does not provide a signed release, N/N-1 upgrade, tested application
rollback, consistent off-host backup, or clean-host restore. Do not use the
current SQLite copy endpoint as disaster recovery evidence. A failed data
volume, failed upgrade or lost host currently requires manual recovery and is
a production `NO-GO`.

## 6. Incident triage

1. Check container health and `/health/ready` reason codes.
2. Capture container logs without secret values.
3. Do not add host mounts, privileged mode or Docker socket access as a fix.
4. If setup, secret or TLS validation fails, fix the external file/config and
   restart; never weaken the production profile.
5. Mutation remains disabled. Escalate any observed host state change as a P0
   security incident.
