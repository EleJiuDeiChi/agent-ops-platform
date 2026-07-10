# R0 production support policy and evidence matrix

Review date: 2026-07-10. This file separates the intended first-GA support
range from evidence actually produced. A version family or image tag is not
"supported" merely because it is current, documented upstream, or present in
a Dockerfile. `SUPPORTED` requires the blocking environment manifest and the
exact signed release digest. R0 is still `NO-GO`.

Status vocabulary:

- `TARGET`: intended range, validation incomplete;
- `EVIDENCE-ONLY`: observed in one run, not a support promise;
- `SUPPORTED`: blocking matrix and signed-release evidence passed;
- `UNSUPPORTED`: reject or show disabled; never best-effort mutation.

## Host and platform

| Area | Intended first-GA range | Evidence in this checkout / boundary | Status |
| --- | --- | --- | --- |
| OS | Ubuntu Server 22.04 LTS and 24.04 LTS, x86_64 | No clean Ubuntu VM manifest yet. Local remediation ran on macOS 26.4 arm64, which is not a production target. | TARGET |
| Filesystem | local ext4 or xfs | No Linux filesystem runtime evidence yet. SQLite on NFS/SMB is explicitly rejected. | TARGET |
| Init/firewall | systemd + UFW | Mutation remains disabled; no destructive-lab evidence. | TARGET |
| Docker Engine | Candidate release lines 28.x and 29.x, exact patch captured per release test | Local runtime evidence uses Engine 27.4.0 only; this does not validate the candidate range. CI records its runner version in the supply-chain artifact. | TARGET |
| Docker Compose | Compose plugin that supports the checked production schema, `config --format json`, health dependencies and secrets | Local static/runtime evidence uses Compose 2.31.0-desktop.2. No exact production support floor/ceiling is claimed until the Ubuntu VM matrix runs. | TARGET |
| Platform frontend | Build input `node:22.23.1-bookworm-slim`; runtime input `nginx:1.28.3-alpine3.23` | Tags are pinned inputs. Only CI-produced digest + SPDX SBOM + Trivy result + protected release signature can become release evidence. | TARGET |
| Platform backend | Build input `python:3.12.13-slim-bookworm` | Same digest-bound evidence rule; a mutable tag is never the supported artifact identity. | TARGET |
| Managed Nginx | Ubuntu distribution Nginx | Read-only diagnostics only; exact apt version is captured by the future clean-VM/R4C manifest. | TARGET |
| ACME | HTTP-01 only | No live ACME evidence. DNS-01 is outside first GA. | TARGET |

Upstream version-discovery sources remain
[Ubuntu releases](https://releases.ubuntu.com/),
[Docker Engine release notes](https://docs.docker.com/engine/release-notes/),
[Docker Compose releases](https://github.com/docker/compose/releases), and
[Nginx downloads](https://nginx.org/en/download.html). These sources select
test candidates; they do not by themselves grant support.

## Database/cache policy

| Product | Intended range | Current evidence | Status |
| --- | --- | --- | --- |
| Control Plane SQLite | Python-bundled SQLite on local ext4/xfs | Current app store exists; versioned migration, WAL policy and off-host restore gates are incomplete. | TARGET |
| MySQL | 8.4 LTS | No native dump/restore destructive-lab manifest. MySQL 8.0 is EOL and is not accepted for a new GA install. | TARGET |
| PostgreSQL | supported 15.x and 16.x patch lines | No native dump/restore destructive-lab manifest. | TARGET |
| Redis | supported 7.4.x patch line | No persistence/restore destructive-lab manifest. | TARGET |

Every run must record the exact server/client patch version. The product does
not claim MySQL/PostgreSQL/Redis support until the corresponding R4F manifest
passes against the signed release digest.

## Browser policy

| Client | Intended range | Evidence in this remediation | Status |
| --- | --- | --- | --- |
| Chrome | current two stable majors at release freeze | Google Chrome 150.0.7871.115 on macOS; one login E2E path. This is one evidence point, not a two-major matrix. | EVIDENCE-ONLY |
| Edge | current two stable majors at release freeze | No current run. | TARGET |
| Firefox | current two stable majors at release freeze | No current run. | TARGET |
| Safari | current stable major at release freeze | Safari 26.4 on macOS; full critical-path matrix remains incomplete. | EVIDENCE-ONLY |
| Mobile | current iOS/Android browsers | View and approval only; administration and mutation are unsupported. | TARGET |

The CI Playwright job provides Chromium evidence only. A production-topology
hook exists at `scripts/test-production-browser.sh`; it intentionally bypasses
the Vite `webServer` configuration and targets an already-running TLS topology.

## Live LLM contract

The candidate is OpenAI-compatible DeepSeek. No provider/model is currently
`SUPPORTED`. The operator must set an explicit model returned by authenticated
`GET /models`; no legacy alias is frozen.

| Field | Required contract |
| --- | --- |
| Owner | AI owner; security owner; legal/privacy owner |
| Base URL | `https://api.deepseek.com` candidate, HTTPS required |
| Discovery | authenticated `GET /models`; configured model ID must be present |
| Completion | `POST /chat/completions`, streaming and non-streaming |
| Tool use | function tool call with JSON-schema arguments; unknown tool/field rejected locally |
| Region | PRC |
| Retention | not contractually fixed; zero-retention must not be claimed |
| Training/model improvement | account-level opt-out required and captured |
| Data sent | redacted operational facts only; no secrets, PII, private keys, raw DB or unredacted logs |
| Timeout/quota | record connect/read timeout, retry count, rate/concurrency limit, token ceiling and cost cap |

The live probe manifest must include timestamp, account/region, exact model and
`/models` response hash, tool-call/invalid-tool/stream/timeout evidence,
redaction capture, usage/cost, policy snapshot and owner approvals. Until it
passes, readiness reports `llm_unverified` and release remains `NO-GO`.

Official contract references:
[model list](https://api-docs.deepseek.com/api/list-models),
[chat completion](https://api-docs.deepseek.com/api/create-chat-completion), and
[tool calls](https://api-docs.deepseek.com/guides/tool_calls).

## Explicitly unsupported in R0

Real host mutation, production Worker/Node Agent, remote Agent, HA, Kubernetes,
DNS-01, SQLite on network filesystems, Docker/Agent socket mounts, host-root
mounts, application store/plugins, GPU/local-model runtime and any unlisted or
unvalidated environment. Unsupported means a structured rejection/disabled
state, never a success placeholder.
