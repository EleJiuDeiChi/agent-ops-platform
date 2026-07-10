# R0 threat model and trust boundaries

Source requirements: production PRD sections 1.5 and 4, plus test spec global
blocking gates and R0 scenarios.

## Assets

- administrator identities, sessions, CSRF tokens and future RBAC state;
- Control Plane SQLite data, approvals, audit/events and reports;
- TLS, session, setup, LLM and future execution-signing keys;
- managed-host configuration, data and service availability;
- future Node Agent socket, execution journal, nonce cache and rollback timers.

## Untrusted inputs

Browser requests, LLM output, logs, file contents, container/service names,
command parameters, tool results and upstream provider responses are untrusted.
They must not directly become shell strings, policy decisions or executable
mutations.

## Current boundaries

- Nginx is the only published container and terminates TLS.
- FastAPI is the authorization root but runs as UID/GID 10001 without Linux
  capabilities. It receives session/LLM secrets only from `/run/secrets`.
- Nginx runs as UID/GID 101 without Linux capabilities. It receives only TLS
  certificate material.
- No container has privileged mode, Docker socket, Agent socket or host root
  mounts.
- The current in-process runner is not an acceptable production execution
  boundary. Mutating/destructive capabilities stay disabled until R1+.

## Principal threats and R0 controls

| Threat | R0 control | Residual gate |
| --- | --- | --- |
| Default/committed credentials | prod fail-closed config; Docker secrets; no bootstrap password | setup enrollment and secret tests must pass |
| Session theft/downgrade | HTTPS-only trusted origins; Secure/HttpOnly/SameSite cookie | MFA/RBAC/login throttling are R3 |
| Container escape/host takeover | non-root, cap-drop, no-new-privileges, read-only FS, no host/socket mounts | image scan/SBOM/signature pending |
| AI prompt/tool injection | live model cannot execute host actions; mutation disabled; production diagnosis requires digest/release-bound provider evidence | R3/R4 typed domain-tool gates pending |
| Secret leakage to provider | provider payload redacts configured secrets and common PII; seeded capture regression test; API key from secret file | live staging provider capture remains blocking |
| Fake placeholder success | placeholder capabilities disabled with structured reason | blocking API/event test required |
| DB/host loss | none sufficient at R0 | off-host encrypted restore is NO-GO |
| Compromised management proxy | TLS secrets isolated to frontend; backend unexposed | signed release and external monitoring pending |

## DeepSeek/OpenAI-compatible data boundary

The current DeepSeek candidate stores personal-information service data in the
PRC; retention is not contractually fixed, and model-improvement opt-out is
required. Never send secrets, personal information, private keys, raw database
content or unredacted server logs. Security and legal owners must approve the
captured request/response probe and policy record before live mode is enabled.
No R0 document claims zero retention.

## Non-waivable NO-GO conditions

- any mutating capability enabled before a real Node Agent/domain gate;
- secret-like data in logs/events/audit/reports/artifacts/provider requests;
- production default secret, insecure cookie, HTTP trusted origin or lingering
  setup/bootstrap credential;
- Control Plane/Worker running as root or mounting Docker/host/Agent resources;
- missing off-host recovery, unsigned/tampered release acceptance, or any
  blocking production test failure.
