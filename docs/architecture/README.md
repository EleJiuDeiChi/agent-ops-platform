# Production architecture baseline (R0)

This document is the R0 architecture truth source required by
`.omx/plans/prd-agent-ops-production-ga-20260710.md` Step 0 and
`.omx/plans/test-spec-agent-ops-production-ga-20260710.md` section 4.

Architecture decisions:

- [ADR-0001: pinned httpx load harness](adr-0001-load-harness.md)

## Current R0 topology

```text
Browser
  -> TLS 1.2/1.3 :443
    -> non-root Nginx/static frontend :8443
      -> Compose DNS backend:8080
        -> non-root FastAPI control plane
          -> local SQLite volume
```

The production frontend image embeds the reviewed Nginx production config,
terminates TLS, serves the immutable Vite build and
proxies API/health traffic by Compose service name. The backend is not
published on a host port. Neither container mounts the Docker socket, an Agent
socket, `/proc`, `/run/systemd`, or the host root filesystem.

## Deliberate R0 release boundary

R0 is a deployment and fail-closed configuration baseline, not the production
execution architecture. The current Worker and host Node Agent do not exist as
separate production processes. Therefore:

- every mutating/destructive placeholder capability remains `enabled=false`;
- no R0 deployment may claim host mutation, rollback or repair capability;
- the in-process local runner is usable only for the explicitly enabled
  read-only development surface;
- adding a Docker socket or host-root mount to make the current container
  "work" is forbidden;
- real mutation remains `NO-GO` until R1 establishes Worker -> Unix socket ->
  Node Agent boundaries and the later domain-specific destructive-lab gates
  pass.

The target R1+ topology is the PRD section 4 architecture: non-root Control
Plane, non-root Worker, and a narrow root Node Agent accessed only over a
permissioned Unix socket with typed capabilities and one-time grants.

## Runtime contracts

- `/health/live` is unauthenticated and proves only that the API process is
  alive. Compose healthchecks use this endpoint so first-time setup can start.
- `/health/ready` is unauthenticated and reports DB/setup/Worker/Agent/LLM
  dependency state. It must be checked separately before user traffic.
- `/version` is the release identity endpoint. A future signed release must
  include commit SHA and image digest; R0 local images are not GA artifacts.
- `/api/health-snapshot` remains an authenticated host/product diagnostic and
  must not be used as container readiness.
- Production secrets enter containers through `/run/secrets`; no secret value
  belongs in Compose environment values, image layers or Git.
- The local SQLite volume is single-node state. It must be local ext4/xfs, not
  NFS/SMB, and currently has no GA-qualified off-host restore path.

## Protected resources

The management listener, TLS configuration and keys, current source IP,
Control Plane database, future Agent socket, rollback timers, signing keys and
audit checkpoints are protected resources. Business capabilities must never
modify them. Only an explicit release/maintenance workflow may change them.

## Availability boundary

R0 has no HA. The production SLI is the PRD-defined ratio of good eligible
minutes to eligible minutes; only an approved, pre-announced maintenance window
may be excluded. Host power or hardware loss, upstream connectivity loss,
application errors, bad readiness, Worker/Agent software failures and failed
upgrades therefore consume the error budget unless the incident is part of such
an approved maintenance window.
