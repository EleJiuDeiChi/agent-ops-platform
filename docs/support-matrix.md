# R0 production support policy and evidence matrix

Review date: 2026-07-11. This file separates the intended first-GA support
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
| OS | Ubuntu Server 22.04 LTS and 24.04 LTS, x86_64 | The clean-KVM gate requires both OS releases on both supported filesystems and consumes only exact Cosign-verified GHCR release digests. Historical evidence is stale until the final release commit is rerun. | EVIDENCE-ONLY |
| Filesystem | local ext4 or xfs | The gate requires four OS/filesystem combinations. For xfs it attaches a dedicated disk at `/var/lib/docker`; every run verifies the actual SQLite named volume filesystem. SQLite on NFS/SMB is explicitly rejected. | EVIDENCE-ONLY |
| Init/firewall | systemd + UFW | Mutation remains disabled; no destructive-lab evidence. | TARGET |
| Docker Engine | Candidate release lines 28.x and 29.x, exact patch captured per release test | All four clean-VM runs must consume the same backend/frontend OCI manifests. CI must repeat against the protected release digest before `SUPPORTED`. | EVIDENCE-ONLY |
| Docker Compose | Compose plugin that supports the checked production schema, `config --format json`, health dependencies and secrets | Every clean-VM run records the exact Compose patch version. The release floor/ceiling is not a support promise until signed-release CI repeats it. | EVIDENCE-ONLY |
| Platform frontend | Build input `node:22.23.1-bookworm-slim`; runtime input `alpine:3.23.5` with `nginx~1.28.3` | The exact signed GHCR digest from canonical `GA-R0-002` must pass all four clean-VM combinations; a locally rebuilt image is rejected. | EVIDENCE-ONLY |
| Platform backend | Runtime input `python:3.12-alpine3.23` | The exact signed GHCR digest from canonical `GA-R0-002` must pass all four clean-VM combinations and bind the live provider manifest. | EVIDENCE-ONLY |
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
| Chrome | current two stable majors at release freeze | Google Chrome 150.0.7871.115 on macOS; three TLS production-topology paths passed. This is one evidence point, not a two-major matrix. | EVIDENCE-ONLY |
| Edge | current two stable majors at release freeze | Microsoft Edge 150.0.4078.50 on macOS; the same three TLS production-topology paths passed. This is one evidence point, not a two-major matrix. | EVIDENCE-ONLY |
| Firefox | current two stable majors at release freeze | No current run. | TARGET |
| Safari | current stable major at release freeze | Safari 26.4 on macOS; full critical-path matrix remains incomplete. | EVIDENCE-ONLY |
| Mobile | current iOS/Android browsers | View and approval only; administration and mutation are unsupported. | TARGET |

The CI Playwright job provides Chromium evidence only. A production-topology
hook exists at `scripts/test-production-browser.sh`; it intentionally bypasses
the Vite `webServer` configuration and targets an already-running TLS topology.

Clean-VM evidence is stored under
`.omx/evidence/production-ga/GA-R0-001/agent-ops-r0-ubuntu{2204,2404}-{ext4,xfs}.json`.
Each manifest records the GPG-verified cloud-image fixture and SHA-256, exact
OS/kernel, root and Docker-data filesystems, Docker root/storage driver,
Engine/Compose package versions, source archive, commit and both OCI digests.
The lab uses
an ephemeral registry bound only to loopback and the libvirt bridge so Docker
28 and 29 pull the same manifest rather than comparing unstable local config
IDs. The harness validates canonical `GA-R0-002`, verifies Cosign signatures,
and mirrors the exact GHCR manifest digests; diagnostic rebuilds cannot produce
passing matrix evidence.

## Live multi-provider LLM contract

The control plane now has one provider-neutral OpenAI-compatible adapter and a
fail-closed registry. One provider is active per deployment; changing provider,
account, origin, model or release digest invalidates the old evidence. Adapter
support is not a production support claim: all three built-ins remain `TARGET`
until their own live probe and governance review pass.

`AIOPS_LLM_ENABLED_PROVIDERS` is an exclusive release flag. Production accepts
exactly the selected `AIOPS_LLM_MODE`; enabling multiple providers or omitting
the selected provider fails startup. A provider can enter the release flag only
after its own account, policy and live evidence are approved.
Production and live probes accept provider credentials only through one
protected `*_API_KEY_FILE`; direct API-key environment values are unsupported
and rejected fail-closed.

| Provider ID | Candidate model | Frozen production origin | Model confirmation | Provider-specific notes | Status |
| --- | --- | --- | --- | --- | --- |
| `deepseek` | `deepseek-v4-flash` | `https://api.deepseek.com` | authenticated `GET /models` | Legacy `deepseek-chat` and `deepseek-reasoner` are ineligible because their announced retirement is 2026-07-24 15:59 UTC. The reviewed public page currently lists USD 0.0028 / 1M cache-hit input, USD 0.14 / 1M cache-miss input and USD 0.28 / 1M output; the live probe must still snapshot current rates. | TARGET |
| `moonshot` | `kimi-k2.6` | `https://api.moonshot.ai/v1` | authenticated `GET /models` | K2.6 supports streaming, thinking and tool calls. Published rates are USD 0.16 / 1M cache-hit input, USD 0.95 / 1M cache-miss input and USD 4 / 1M output tokens. The public privacy policy describes Singapore storage and model-improvement processing, so acceptable enterprise terms or an approved account control are blocking. | TARGET |
| `zhipu` | `glm-5.2` | `https://open.bigmodel.cn/api/paas/v4` | authenticated `POST /chat/completions` with the exact model | The official API surface currently enumerates GLM-5.2 and documents chat, streaming, usage, Function Call and GLM-5.2 reasoning effort, but only `tool_choice=auto` and no OpenAI-shaped model-list endpoint. The authenticated completion response is therefore the discovery evidence. Current price must be captured from the account-visible price page at probe time. | TARGET |
| `openai_compatible` | operator-selected | operator-selected credential-free HTTPS origin | authenticated `GET /models` | Extension lane only. It requires the same policy, evidence and owner gates and cannot inherit evidence from a built-in provider. | TARGET |

Every provider must independently prove:

- AI, security and privacy owners, account identifier, processing region,
  retention and training/model-improvement decision;
- exact origin and model, provider-specific model confirmation, non-streaming
  completion, SSE streaming, typed tool call and local unknown-tool/field rejection;
- bounded timeout/retry, token usage, reviewed cache-hit/cache-miss/output
  prices, account concurrency/RPM/TPM/TPD limits, balance-alert threshold,
  pre-spend per-probe cost cap covering potentially billed retries, SSE media
  type/event hash and seeded-secret redaction capture;
- a structured immutable quota/pricing JSON snapshot whose provider, account,
  tier, source URL, review instant, currency, three prices and every quota field
  match the policy semantically, plus a separate digest of the live source bytes;
- an immutable provider-policy snapshot and three distinct AI/security/privacy
  approval identities, each matching its declared owner and binding both
  evidence snapshot digests, with the resulting manifest bound to provider ID,
  account, model, release digest and expiry.

Only redacted operational facts may leave the control plane. Secrets, personal
information, private keys, raw database content and unredacted logs are always
forbidden. Missing, stale or mismatched evidence keeps readiness at
`llm_unverified` and makes production diagnosis fail closed with HTTP 503.

Official references reviewed on 2026-07-11:

- DeepSeek: [model list](https://api-docs.deepseek.com/api/list-models),
  [chat completion](https://api-docs.deepseek.com/api/create-chat-completion),
  [tool calls](https://api-docs.deepseek.com/guides/tool_calls),
  [pricing](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) and
  [change log](https://api-docs.deepseek.com/zh-cn/updates).
- Moonshot Kimi: [API overview](https://platform.kimi.ai/docs/api/overview),
  [model list](https://platform.kimi.ai/docs/api/list-models),
  [K2.6 guide](https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart),
  [pricing](https://platform.kimi.ai/docs/pricing/chat-k26) and
  [privacy policy](https://platform.kimi.ai/docs/agreement/userprivacy).
- Zhipu GLM: [model overview](https://docs.bigmodel.cn/cn/guide/start/model-overview),
  [GLM-5.2](https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2),
  [chat completion](https://docs.bigmodel.cn/api-reference/模型-api/对话补全),
  [tool calling](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling),
  [user agreement](https://docs.bigmodel.cn/cn/terms/user-agreement) and
  [privacy policy](https://docs.bigmodel.cn/cn/terms/privacy-policy).

## Explicitly unsupported in R0

Real host mutation, production Worker/Node Agent, remote Agent, HA, Kubernetes,
DNS-01, SQLite on network filesystems, Docker/Agent socket mounts, host-root
mounts, application store/plugins, GPU/local-model runtime and any unlisted or
unvalidated environment. Unsupported means a structured rejection/disabled
state, never a success placeholder.
