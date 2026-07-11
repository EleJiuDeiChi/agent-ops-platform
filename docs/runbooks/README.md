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
  deploy/secrets/llm_api_key deploy/secrets/llm-contract.json \
  deploy/secrets/setup_token
sudo chmod 0400 deploy/secrets/session_secret \
  deploy/secrets/llm_api_key deploy/secrets/llm-contract.json \
  deploy/secrets/setup_token
sudo chown 101:101 deploy/secrets/tls_cert.pem deploy/secrets/tls_key.pem
sudo chmod 0400 deploy/secrets/tls_cert.pem deploy/secrets/tls_key.pem
```

Before using a live LLM, select one active provider (`deepseek`, `moonshot`, or
`zhipu`) and complete that provider's probe manifest in
`docs/support-matrix.md`. Evidence is not transferable between providers or
accounts. A successful HTTP request alone is insufficient.

Set `AIOPS_LLM_ENABLED_PROVIDERS` to exactly the selected `AIOPS_LLM_MODE`.
This is the provider release flag: production rejects a missing flag, multiple
providers, an unknown provider, or a provider different from the active mode.
Production and the live probe accept LLM credentials only through one protected
`*_API_KEY_FILE` variable, for example `AIOPS_DEEPSEEK_API_KEY_FILE`. Direct
`AIOPS_LLM_API_KEY`, `AIOPS_DEEPSEEK_API_KEY`, `AIOPS_MOONSHOT_API_KEY` and
`AIOPS_ZHIPU_API_KEY` environment values are forbidden, including when a file
variable is also present. The probe rejects symlinks, non-regular files,
group/other access and files not owned by root or the probe operator; use mode
`0600` or stricter.

Copy `docs/security/llm-provider-policy.example.json` to a protected working
file, replace every placeholder, save the exact reviewed provider-policy page
as a snapshot, and record that snapshot's SHA-256 plus all three owner
approvals. Save a second immutable snapshot of the reviewed account-tier quota
and pricing source by copying
`docs/security/llm-quota-pricing.example.json` to a protected UTF-8 JSON file.
It has exactly this shape (values shown are placeholders):

```json
{
  "schema_version": 1,
  "provider_id": "deepseek",
  "account_identifier": "non-secret-account-id",
  "account_tier": "reviewed-tier",
  "source_url": "https://provider.example/pricing",
  "source_content_sha256": "sha256:<64-lowercase-hex>",
  "reviewed_at": "2026-07-11T00:00:00Z",
  "currency": "CNY",
  "prices_per_million_tokens": {
    "cache_hit_input": "0",
    "cache_miss_input": "0",
    "output": "0"
  },
  "quota": {
    "concurrency_limit": "account-tier",
    "requests_per_minute": "provider-managed",
    "tokens_per_minute": "provider-managed",
    "tokens_per_day": "unlimited",
    "balance_alert_threshold": "0"
  }
}
```

`source_content_sha256` is the digest of the exact bytes retrieved from
`source_url`; `quota_pricing_snapshot_sha256` is the digest of this structured
JSON file. The probe checks the JSON provider, account, tier, source URL, review
instant, currency, three prices and every quota field against the policy using
typed semantics, then separately verifies the live source bytes. AI, security
and privacy approvals require three different identities matching their owner
fields, must be created after both reviews, and must repeat both snapshot
digests. Use an explicit approved marker only where the provider manages or
tiers a limit. Set
`AIOPS_RELEASE_DIGEST` to the immutable backend image digest and
`AIOPS_COMMIT_SHA` to the reviewed 40-character release commit, and
`AIOPS_EVIDENCE_RUNNER_IDENTITY` to the accountable operator or CI identity,
then run:

```bash
./scripts/probe-llm-contract.py \
  --policy-file /protected/path/llm-provider-policy.json \
  --policy-snapshot-file /protected/path/provider-policy.snapshot \
  --quota-pricing-snapshot-file /protected/path/quota-pricing.snapshot \
  --output .omx/evidence/production-ga/GA-R0-001/llm-contract.json
```

The probe verifies the policy URL and live quota/pricing source still hash to
their independently reviewed evidence,
provider-specific authenticated model discovery, exact model, typed tool calling, SSE media type,
contract marker, normalized event hash and provider-specific cache usage,
bounded timeout/retry, invalid-tool rejection, seeded-secret redaction and
usage/cost limits. It rejects arbitrary or semantically mismatched quota/pricing
snapshot bytes and approvals that do not bind both snapshot digests. The
preflight budget uses a conservative serialized-payload
byte bound and includes every potentially billed tool retry before any provider
request. The probe also binds those limits to the reviewed quota/pricing snapshot.
It writes the manifest read-only and prints the exact
`AIOPS_LLM_EVIDENCE_SHA256` value. Copy the manifest to the protected host path
configured by `AIOPS_LLM_EVIDENCE_FILE_HOST`; the backend verifies its digest,
expiry, provider, model and release binding before any production diagnosis can
call the provider. Missing or stale evidence keeps readiness at
`llm_unverified` and makes diagnosis return 503.

## 3. First-time enrollment

Before publishing a release, provision a dedicated SSH release-signing key
outside the repository. Store its allowed-signers line (`principal`, key type
and public key) in the repository variable `AIOPS_RELEASE_ALLOWED_SIGNERS`.
Create the annotated semantic tag with Git SSH signing. The release workflow
rejects lightweight tags, unsigned tags, untrusted signers and commits that are
not reachable from `main` or `release/*`. The private signing key must never be
stored in Actions, the repository or a development `.env` file.

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

For the R0 clean-host gate, first download the canonical `GA-R0-002` artifact
from the successful signed-tag workflow into a protected path outside the clean
Git checkout. Authenticate Docker on
the lab host for read-only access to the private GHCR images and install Cosign
3.1.1. The matrix refuses locally rebuilt backend/frontend images: it verifies
both exact GHCR digests with Cosign, mirrors those immutable manifests through
the ephemeral lab registry, and requires every VM to pull the same digests.

Run the gate only on the designated Linux KVM
lab host with passwordless sudo and the libvirt `default` network. It downloads
Ubuntu official cloud images, verifies their published SHA-256 sums, creates
namespaced disposable guests and destroys them after success. The xfs cases
also attach a dedicated disposable data disk before Docker installation:

```bash
AIOPS_SOURCE_COMMIT="$(git rev-parse HEAD)" \
  AIOPS_R0_RELEASE_MANIFEST="/protected/path/GA-R0-002/manifest.json" \
  AIOPS_R0_EXPECTED_REPOSITORY="OWNER/REPO" \
  ./scripts/test-r0-clean-vm-matrix.sh
```

When the KVM development machine intentionally has no `.git` directory, run
the local wrapper instead. It rejects a dirty worktree, creates a SHA-256-bound
archive from the exact commit, transfers only that archive, and invokes the same
remote gate. The archive is integrity-bound to the authenticated SSH transfer;
it is not described as independently signed:

```bash
AIOPS_R0_RELEASE_MANIFEST="/protected/path/GA-R0-002/manifest.json" \
  ./scripts/run-r0-clean-vm-matrix-devbox.sh
```

After a successful remote run, the wrapper copies the generated GA-R0-001
matrix manifests, logs, Cosign receipts, summary and status back into the local
evidence directory. It never replaces the aggregate `manifest.json`; that file
is created only by `assemble-r0-baseline-manifest.py` after the live LLM and
independent review artifacts are also present.

Architecture and security reviewers must each sign their own JSON statement
with `ssh-keygen -Y sign -n aiops-r0-review`. Each statement binds the exact
commit plus SHA-256 values of the matrix summary, GA-R0-002 manifest and live
LLM manifest. The review manifest points to both statements, their detached
signatures. The assembler receives the protected `allowed_signers` trust file
as an external CLI input; the review manifest is forbidden from nominating its
own trust root. The two signer identities and SSH key fingerprints must both be
distinct. Assemble only after those signatures verify:

```bash
./scripts/assemble-r0-baseline-manifest.py \
  --commit "$(git rev-parse HEAD)" \
  --expected-repository "OWNER/REPO" \
  --review-allowed-signers "/protected/path/reviewers.allowed" \
  --matrix-summary .omx/evidence/production-ga/GA-R0-001/matrix-summary.json \
  --matrix-status .omx/evidence/production-ga/GA-R0-001/matrix-status.json \
  --release-manifest .omx/evidence/production-ga/GA-R0-002/manifest.json \
  --llm-manifest .omx/evidence/production-ga/GA-R0-001/llm.json \
  --review-manifest .omx/evidence/production-ga/GA-R0-001/review.json
```

Finally sign the aggregate with namespace `aiops-r0-aggregate` and run
`validate-ga-manifest.py` with `--root`, `--signature`, `--allowed-signers`
and `--signer-identity`. The validator re-hashes every referenced artifact;
an unsigned or locally edited aggregate is not accepted.

The harness must report the same signed backend and frontend GHCR manifest
digests for
Ubuntu 22.04/Docker 28 and Ubuntu 24.04/Docker 29 across both ext4 and xfs.
It must also prove that Docker's real data root and the production SQLite named
volume use the expected filesystem and that the database passes SQLite
`quick_check`. A `docker save/load` config ID, an
unrelated xfs test directory or a container pretending to be a VM is not
accepted evidence.

## 5. Stop, rollback and recovery limitations

```bash
docker compose --env-file .env.production -f deploy/compose.prod.yml down
```

R0 contains a signed-release pipeline, but no release is production-accepted
until the signed tag, GitHub Release, exact-digest four-VM matrix and aggregate
signature all exist and verify. R0 still does not provide N/N-1 upgrade,
tested application rollback, consistent off-host backup, or clean-host restore. Do not use the
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
