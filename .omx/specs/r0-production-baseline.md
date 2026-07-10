# R0 production baseline contract

Status: implemented baseline, blocking evidence incomplete (`NO-GO`)

Sources:

- `.omx/plans/prd-agent-ops-production-ga-20260710.md` Step 0
- `.omx/plans/test-spec-agent-ops-production-ga-20260710.md` R0 and global gates
- `docs/support-matrix.md`
- `docs/security/threat-model.md`

## Git and release convention

- Default branch: `main`, protected once the remote exists.
- Release stabilization branches: `release/<major>.<minor>`; no direct feature
  development after freeze.
- Signed immutable tags: `v<major>.<minor>.<patch>` pointing to the reviewed
  release commit and matching image/SBOM/provenance digest.
- Every commit follows the repository Lore protocol: intent-first subject,
  rationale body and applicable `Constraint`, `Rejected`, `Confidence`,
  `Scope-risk`, `Directive`, `Tested`, and `Not-tested` trailers.
- The first reviewed `main` commit is created only after local R0 gates pass.
  Remote protection, protected release environment and signed tag remain
  external release-owner actions and keep R0 `NO-GO` until evidenced.

## Delivered deployment invariants

- Docker build context is default-deny and excludes demo, data, secrets,
  node_modules, virtualenvs and local artifacts.
- Backend listens on container `0.0.0.0:8080`, is not host-published in prod,
  and runs as UID/GID 10001.
- Frontend is a multi-stage immutable build served by Nginx 1.28.3 as UID/GID
  101; API proxy uses `backend:8080`, not container loopback.
- Production publishes TLS only; session/LLM/TLS/setup material uses Docker
  secret files. Bootstrap passwords and debug password bypass are forbidden.
- Containers are read-only, capability-dropped, no-new-privileges, PID/memory/
  CPU limited and have liveness healthchecks/restart policies.
- `/health/ready` remains the separate dependency/readiness gate and must be
  checked after startup; liveness never proves Worker/Agent/LLM readiness.
- No Docker socket, host root or Agent socket is mounted.
- Placeholder mutation is disabled by per-capability registry/API enforcement.
  There is no implemented global mutation kill-switch yet; absence of a fake
  env flag is intentional. Real mutation remains prohibited.
- `.github/workflows/r0-ci.yml` runs locked Python/Node setup, backend pytest,
  frontend unit/type/build/E2E listing, dependency audits, static baseline
  validation, both production image builds and a separate Chromium E2E job.

## R0 NO-GO rules

Release verdict is `NO-GO` if any item is true:

1. Production accepts a default/directly committed secret, HTTP/wildcard
   trusted origin, insecure cookie, bootstrap password or debug bypass.
2. An initialized instance accepts a setup token, or an uninitialized instance
   starts without a valid short-lived setup enrollment token.
3. `/health/live`, `/health/ready` or `/version` does not meet the test spec.
4. Any mutating/destructive placeholder is enabled, executes, or writes a
   success task/event.
5. Build context contains demo, data, secrets, node_modules or a virtualenv.
6. Any runtime web/control container runs as UID 0 or mounts Docker socket,
   Agent socket or host root.
7. The R0 clean Ubuntu 22.04/24.04 matrix is absent, the support range is not
   frozen, or an unsupported environment attempts mutation. Full browser and
   database domain support remain separate R5/R4 gates and must stay
   `TARGET`/disabled until their own evidence exists.
8. DeepSeek/OpenAI-compatible `/models`, tool calling, redaction, region,
   retention/training opt-out and owner approvals are incomplete.
9. Runtime/dependency/container scans have P0/P1 findings, or signed digest,
   SBOM and provenance are missing for a claimed release.
10. Root repository has no protected `main`, release/tag policy or auditable
    release commit.

R0 explicitly does not complete R1 Agent boundary, R2 durable Worker/task
runtime, R3 identity/data recovery, R4 domain mutations, R5 product UI, R6
signed release/observability, or R7 soak/canary. Their absence must never be
reported as a production closure.

## Verification commands

```bash
./scripts/verify-production-baseline.sh
./scripts/smoke.sh
./scripts/test-production-compose.sh
./scripts/test-r0-clean-vm-matrix.sh  # designated Linux KVM lab only
docker compose config
docker compose --env-file .env.production -f deploy/compose.prod.yml config
```

Docker image builds, TLS runtime, live LLM probe, destructive lab,
SBOM/signature and remote deployment require their designated environments and
evidence manifests. Static config or a container-only run cannot substitute.
