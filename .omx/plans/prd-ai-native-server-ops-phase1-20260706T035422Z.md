# PRD: AI Native Server Ops Panel Phase 1

## Requirements Summary

Build the first real MVP for a small-team AI-native server operations panel. Phase 1 starts from a clean implementation baseline and creates a single-server product that can inspect a Linux host, summarize health, diagnose common service failures, and propose or execute approved safe repairs.

The product should not start as a Kubernetes AIOps platform. It should start as an "AI server operator" for teams with 1-20 Linux servers and limited dedicated operations staff.

## Product Positioning

One-line positioning:

> 面向中小团队的 AI 原生服务器运维面板，让没有专职 SRE 的团队也能用自然语言完成服务器巡检、故障诊断和安全修复。

First-phase product promise:

> Install on one Linux server, open a Web panel, ask "帮我检查服务器状态", and receive a structured health report with evidence, risk levels, and approved next actions.

## RALPLAN-DR Summary

### Principles

1. Start with one-server value before multi-server orchestration.
2. Read-only diagnosis is default; mutating repair must be explicit, approved, and audited.
3. The AI agent must call typed tools, not arbitrary shell by default.
4. The panel must expose evidence, not just natural-language conclusions.
5. Existing market tools should be integrated or wrapped; custom work should focus on orchestration, safety, UX, and SMB workflows.

### Decision Drivers

1. Fast MVP usefulness for small teams: health check, Docker/Nginx failures, disk pressure, security risks.
2. Safety and trust: allowlists, approval gates, operation logs, rollback notes.
3. Extensibility: tool registry can later add databases, alerts, multi-server agents, and Kubernetes.

### Viable Options

#### Option A: Local-first self-hosted panel plus local tool runner

Approach: Web panel, backend API, and tool runner run on the same Linux server in phase 1.

Pros:
- Lowest integration complexity.
- Avoids remote agent networking and tunnel design in the first phase.
- Easier for SMB users to trust because data stays on the server.
- Good fit for one-server MVP.

Cons:
- Multi-server support comes later.
- SaaS-style remote management is not solved in phase 1.

#### Option B: Central control plane plus remote server agent from day one

Approach: Build a central Web control plane and install lightweight agents on managed servers.

Pros:
- Closer to the eventual multi-server product.
- Better foundation for teams with many servers.

Cons:
- Requires agent identity, secure transport, enrollment, command authorization, and network reliability from the start.
- Higher security burden before product-market fit is proven.
- Slower MVP.

#### Option C: UI-only mock with simulated AI workflows

Approach: Build only visual screens and simulate diagnosis.

Pros:
- Fastest demo.
- Useful for sales narrative and investor walkthroughs.

Cons:
- Does not prove the product's core value.
- Cannot validate command safety, diagnosis accuracy, or real operational trust.

### Chosen Direction

Choose Option A for phase 1. Keep the architecture intentionally compatible with Option B later by separating the Web API, tool registry, and runner interface.

### Execution Trust Boundary

- Phase 1 deployment is local-first and single-server.
- Default bind mode should be localhost-only for development and explicit authenticated LAN access for real use.
- The panel must require authentication before exposing health data, logs, tool invocation, approvals, or audit records.
- The local runner must execute as a dedicated low-privilege OS user where possible.
- `sudo` is not a default capability. Any required privileged adapter must be individually declared, documented, and approval-gated.
- Tool adapters must pass arguments as structured values and validate/escape them server-side.
- The AI model never receives a raw unrestricted shell tool in phase 1.
- Approval objects must bind to exact action fingerprints: authenticated actor/session, tool name, canonical parameters, target resource, risk level, expiry, and one-time nonce.

## Phase 1 Scope

### In Scope

- Single Linux server management.
- Web panel with four primary surfaces:
  - Server overview
  - Services and containers
  - AI diagnosis console
  - Inspection reports and audit history
- Tool registry with allowlisted read-only tools for:
  - system health: load, CPU, memory, disk, uptime
  - process and port inspection
  - systemd service status and logs
  - Docker status, inspect, logs, restart proposal
  - Nginx status, config test, access/error log summaries
  - basic database reachability/status checks for MySQL/PostgreSQL if configured
  - SSH/login/security checks: recent logins, failed logins, listening ports, firewall state
- Adapter delivery waves:
  - Wave 1: system health, process/port inspection, systemd status/logs.
  - Wave 2: Docker and Nginx diagnosis.
  - Wave 3: database reachability and SSH/security checks.
- AI diagnosis loop:
  - classify user intent
  - create investigation plan
  - call typed tools
  - summarize evidence
  - propose next action
  - request approval for repair
- Safe repair actions:
  - restart service/container
  - clear only known safe log/cache targets after explicit confirmation
  - run `nginx -t` before any Nginx reload
  - produce manual command suggestions for high-risk actions instead of executing automatically
- Audit trail for every tool call and repair action.

### Out of Scope

- Kubernetes.
- Multi-server fleet management.
- Full alerting platform.
- Distributed tracing.
- Complex root-cause graph engine.
- Unrestricted shell execution by LLM.
- Autonomous destructive repair.
- Enterprise RBAC/SSO.

## Recommended Phase 1 Architecture

```text
Browser Web Panel
  |
Backend API
  |-- Auth/session
  |-- Server inventory
  |-- Tool registry
  |-- AI diagnosis orchestrator
  |-- Audit/report store
  |
Local Runner
  |-- allowlisted command adapters
  |-- timeout/output limits
  |-- redaction
  |-- approval gate for mutating tools
  |
Linux Host / Docker / Nginx / systemd / logs / DB
```

## Phase 1 Implementation Stack

Choose a Python backend plus React frontend. This keeps server inspection, command adapters, LLM orchestration, and typed data contracts simple while still giving the panel a modern UI.

### Backend

- Language: Python 3.12.
- Framework: FastAPI.
- Data models: Pydantic.
- Persistence: SQLite for phase 1.
- Async/event stream: Server-Sent Events for diagnosis timeline.
- Tests: pytest.
- Command execution: Python subprocess wrappers with fixed argv arrays, timeouts, output caps, and redaction.

### Frontend

- Framework: React + TypeScript + Vite.
- UI state: simple local state plus API hooks; no global state library in phase 1.
- Tests: Playwright smoke tests for core flows.

### LLM Adapter

- Default test/dev mode: fixture/mock LLM adapter, no external API required for CI or local smoke tests.
- Real mode: OpenAI-compatible adapter behind environment configuration.
- Provider interface must support:
  - `plan(input, available_tools)`
  - `next_tool_call(session_state)`
  - `final_answer(session_state)`
  - timeout and structured error return

### Expected Repo Layout

```text
backend/
  app/
    main.py
    api/
    auth/
    models/
    storage/
    tools/
    runner/
    ai/
    reports/
  tests/
frontend/
  src/
    pages/
    components/
    api/
    types/
  tests/
fixtures/
  tool_outputs/
  diagnosis_sessions/
scripts/
  dev.sh
  smoke.sh
docker-compose.yml
README.md
```

## Deployment Contract

### Local Development

```bash
./scripts/dev.sh
```

Expected behavior:

- Starts FastAPI backend on `127.0.0.1:8080`.
- Starts Vite frontend on `127.0.0.1:5173`.
- Uses SQLite under `./data/dev.sqlite`.
- Uses fixture/mock LLM adapter unless real provider env vars are present.

### Real Server Phase 1 Install

First target: Docker Compose with host read-only mounts where possible and explicit privileged adapter configuration when needed.

```bash
docker compose up -d
```

Defaults:

- Backend binds to `127.0.0.1:8080` unless `AIOPS_BIND_HOST` is explicitly set.
- LAN exposure requires authentication and documented reverse proxy/TLS setup.
- Session secret comes from `AIOPS_SESSION_SECRET`.
- SQLite path comes from `AIOPS_DB_PATH`.
- LLM mode comes from `AIOPS_LLM_MODE=mock|openai-compatible`.
- OpenAI-compatible real mode uses `AIOPS_LLM_BASE_URL`, `AIOPS_LLM_API_KEY`, and `AIOPS_LLM_MODEL`.
- Runner privilege mode is visible in diagnostics and defaults to non-privileged adapters only.

## Minimal Data Contracts

### Tool

```json
{
  "name": "system.health",
  "category": "system",
  "description": "Collect load, uptime, memory, and disk summary",
  "input_schema": {},
  "risk_level": "read",
  "approval_required": false,
  "timeout_seconds": 10,
  "enabled": true
}
```

### ToolResult

```json
{
  "tool_name": "system.health",
  "status": "success",
  "started_at": "2026-07-06T03:54:22Z",
  "duration_ms": 240,
  "output_summary": "load average 0.31, memory 42%, root disk 61%",
  "output_ref": "audit://tool-results/...",
  "redacted": true,
  "error": null
}
```

### DiagnosisSession

```json
{
  "id": "diag_...",
  "status": "running",
  "user_question": "帮我巡检这台服务器",
  "created_at": "2026-07-06T03:54:22Z",
  "updated_at": "2026-07-06T03:54:30Z"
}
```

### DiagnosisEvent

Server-Sent Event shape:

```json
{
  "type": "tool_result",
  "session_id": "diag_...",
  "sequence": 4,
  "payload": {},
  "created_at": "2026-07-06T03:54:30Z"
}
```

Event types:

- `plan`
- `tool_call`
- `tool_result`
- `approval_required`
- `final_answer`
- `error`

### SessionIdentity

Phase 1 uses an HTTP-only signed session cookie. It is not mapped to a local OS account.

```json
{
  "actor_id": "user_admin",
  "session_id": "sess_...",
  "role": "admin",
  "created_at": "2026-07-06T03:54:22Z",
  "expires_at": "2026-07-06T11:54:22Z"
}
```

Rules:

- Bootstrap creates one local application admin user. The initial password is supplied by `AIOPS_BOOTSTRAP_ADMIN_PASSWORD` or printed once by the install script and stored only as a password hash.
- First successful login with a generated bootstrap password must require password change before tool execution or approvals.
- `GET /api/csrf` returns `{ "csrf_token": "..." }` and sets a `SameSite=Strict` CSRF cookie for double-submit validation.
- `POST /api/login` request body is `{ "username": "admin", "password": "..." }` and must include `X-CSRF-Token`.
- `POST /api/login` response body is `{ "identity": SessionIdentity, "must_change_password": boolean }` and sets the signed HTTP-only session cookie.
- `POST /api/login` creates the signed HTTP-only session cookie.
- `POST /api/logout` invalidates the server-side session record.
- `GET /api/me` returns the current `SessionIdentity`.
- `POST /api/me/password` request body is `{ "current_password": "...", "new_password": "..." }` and must include `X-CSRF-Token`.
- `POST /api/me/password` response body is `{ "identity": SessionIdentity, "must_change_password": false }`.
- After required password change succeeds, the current auth session remains valid but gets a refreshed `SessionIdentity` with `must_change_password=false`.
- All approval fingerprints include both `actor_id` and `session_id`.
- A session identity is an application identity only; it never grants OS-level shell identity.
- Session cookies must use `HttpOnly`, `SameSite=Strict` by default, and `Secure` when served over HTTPS.
- Mutating browser-authenticated endpoints must enforce CSRF protection and origin validation.
- CSRF-protected endpoints include login, logout, password change, tool invocation, diagnosis session creation, approval approve/deny, and any future mutating repair API.
- `GET /api/tools` and `GET /api/diagnostics/runner` are protected APIs in phase 1, not public metadata endpoints.
- Failed login responses must not reveal whether username or password was incorrect.

### Approval

```json
{
  "id": "appr_...",
  "auth_session_id": "sess_...",
  "diagnosis_session_id": "diag_...",
  "actor_id": "user_...",
  "nonce": "nonce_...",
  "action_fingerprint": "sha256:...",
  "tool_name": "docker.restart",
  "target": "container:orders-api",
  "canonical_params": {},
  "risk_level": "mutating",
  "expires_at": "2026-07-06T04:04:22Z",
  "status": "pending"
}
```

### AuditRecord

```json
{
  "id": "audit_...",
  "actor_id": "user_...",
  "event_type": "tool_invocation",
  "resource": "system.health",
  "risk_level": "read",
  "status": "success",
  "summary": "Collected system health",
  "created_at": "2026-07-06T03:54:22Z"
}
```

### RunnerDiagnostics

```json
{
  "runner_user": "aiops-runner",
  "effective_uid": 1001,
  "bind_host": "127.0.0.1",
  "privileged_adapters_enabled": [],
  "sudo_enabled": false,
  "tool_count": 12,
  "llm_mode": "mock"
}
```

### Report

```json
{
  "id": "report_...",
  "type": "inspection",
  "health_score": 86,
  "risk_items": [],
  "evidence_refs": [],
  "recommended_actions": [],
  "created_at": "2026-07-06T03:55:00Z"
}
```

## Minimal API Contracts

- `POST /api/login`
- `POST /api/logout`
- `GET /api/csrf`
- `GET /api/me`
- `POST /api/me/password`
- `GET /api/health-snapshot`
- `GET /api/tools`
- `POST /api/tools/{tool_name}/invoke`
- `POST /api/diagnosis/sessions`
- `GET /api/diagnosis/sessions/{id}/events`
- `POST /api/approvals/{id}/approve`
- `POST /api/approvals/{id}/deny`
- `GET /api/audit`
- `GET /api/reports`
- `GET /api/diagnostics/runner`

## Implementation Plan

### 1. Product boundary and information architecture

- Use the first-phase SMB taxonomy:
  - Overview
  - Services
  - AI Diagnosis
  - Reports
  - Settings
- Keep Agent concepts internally as orchestration roles, but avoid showing too many "Agents" as first-class product pages.
- Update the product language from "AI 运维总控 Agent" to "AI 原生服务器运维面板".

### 2. Backend foundation

- Add a backend API service with endpoints for:
  - health snapshot
  - tool listing
  - tool invocation
  - diagnosis session creation
  - diagnosis event stream
  - approval/deny operation
  - reports and audit logs
- Use a simple local persistence layer first for audit/report/session records.
- Define a provider-neutral LLM adapter interface.

### 3. Tool registry and execution runner

- Define a `Tool` schema:
  - name
  - description
  - category
  - input schema
  - risk level
  - timeout
  - command adapter
  - redaction rules
  - approval requirement
- Implement read-only tools first.
- Implement mutating tools only behind approval.
- Enforce command allowlist server-side; do not trust the model to construct arbitrary shell.

### 4. AI diagnosis orchestrator

- Implement a bounded tool-calling loop:
  - max steps
  - max tool calls per step
  - max output size
  - timeout handling
  - structured final response
- Add built-in runbooks for first MVP scenarios:
  - server health inspection
  - Docker container down
  - Nginx 502
  - disk nearly full
  - suspicious login/security review
- Require evidence in final answers:
  - commands/tools used
  - important output excerpts
  - confidence
  - recommended action
  - whether action is safe/approval-required

### 5. Web panel MVP

- Implement real UI flows:
  - Server overview cards backed by API data.
  - Service list for systemd/Docker/Nginx state.
  - AI diagnosis chat with visible tool-call timeline.
  - Approval modal for repair actions.
  - Report page with health score, risk items, and audit log.
- Build the first screen as an operational server overview, not an Agent topology demo.

### 6. Verification and packaging

- Add local demo mode with fixture outputs for development.
- Add a real-server mode with explicit install notes.
- Provide a one-command local start path.
- Add smoke tests for tool execution safety and diagnosis workflow.

## Execution Milestones

### Milestone 0: Skeleton and contracts

Done when:

- Backend and frontend projects start with documented commands.
- Shared API/data contracts exist.
- SQLite storage initializes.
- Mock LLM mode returns deterministic diagnosis events.

### Milestone 1: Wave 1 host inspection

Done when:

- `system.health`, `system.disk`, `system.processes`, `network.ports`, and `systemd.status` read-only adapters work through the tool registry.
- "帮我巡检这台服务器" produces a report with at least five tool calls.
- Audit records are written for every tool call.

### Milestone 2: Docker and Nginx diagnosis

Done when:

- Docker status/log/inspect adapters work in read-only mode.
- Nginx status/config-test/log/upstream reachability adapters work.
- Docker restart and Nginx reload proposals require approval.

### Milestone 3: Approval, auth, and safety hardening

Done when:

- Protected APIs reject unauthenticated requests.
- Approval binds actor/session/action fingerprint and blocks replay or parameter substitution.
- Arbitrary shell, destructive file delete, and generic sudo are rejected.
- Runner diagnostics expose current privilege mode.

### Milestone 4: Reports and UI polish

Done when:

- Overview, services, diagnosis timeline, approval modal, and report page are implemented.
- Playwright smoke tests cover the primary flow.
- README documents local dev, Docker Compose install, env vars, and safety model.


## Testable Acceptance Criteria

1. A user can open the panel and see real CPU/load, memory, disk, uptime, process count, and listening ports for the local server.
2. A user can ask "帮我巡检这台服务器", and the system runs at least five read-only tools and returns a structured report with evidence.
3. A user can ask "这个 Docker 容器为什么挂了", select a container, and receive status, recent logs, restart count, and a likely cause.
4. A user can ask "Nginx 为什么 502", and the system checks Nginx status, config test, error log, upstream port reachability, and returns a diagnosis.
5. A mutating action such as restarting a service or container is never executed without explicit approval.
6. Every tool invocation records timestamp, tool name, parameters, risk level, status, duration, and sanitized output summary.
7. Tool output longer than the configured limit is truncated or summarized without crashing the diagnosis session.
8. Failed commands return structured errors and do not break the whole AI diagnosis loop.
9. High-risk operations such as deleting arbitrary files, changing firewall rules, editing database data, or running unrestricted shell are blocked in phase 1.
10. The UI shows a visible timeline of AI reasoning steps and tool calls, not only a final chat answer.
11. The panel cannot expose tool execution or logs without authentication.
12. An approval token cannot be replayed or reused for a different actor, session, action, target, or parameter set.
13. Runner privilege mode is documented and visible in settings or diagnostics.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| LLM suggests unsafe commands | Only expose server-side allowlisted tools; require approvals for mutating actions |
| Users overtrust AI diagnosis | Always show evidence, confidence, and manual verification steps |
| Command output leaks secrets | Redact tokens, env values, private keys, DSNs, and common credentials before storing or sending to LLM |
| Scope expands into Kubernetes/AIOps | Keep phase 1 single-server and SMB-focused |
| UI becomes a demo instead of product | Prioritize real tool-backed screens before topology animation |
| Repair action causes downtime | Start with restart-only actions, approval, pre-checks, and audit |
| Local panel becomes an exposed server control surface | Default to localhost bind; require auth for LAN access; document reverse proxy/TLS requirements |
| Runner needs excessive privileges | Run as least-privilege user; isolate privileged adapters; reject generic sudo |

## Verification Steps

- Unit tests:
  - tool schema validation
  - allowlist/denylist behavior
  - output truncation/redaction
  - approval-required classification
- Integration tests:
  - health snapshot endpoint
  - diagnosis session with fake tools
  - repair approval flow
  - Docker/Nginx fixture diagnosis
- Manual smoke tests on a Linux test server:
  - healthy server inspection
  - stopped Docker container
  - broken Nginx config
  - full-disk fixture or simulated large logs
  - failed SSH login inspection
- Safety tests:
  - reject arbitrary shell command
  - reject destructive file delete
  - reject unapproved service restart
  - redact fake secrets in logs
  - reject unauthenticated tool invocation
  - reject replayed or mismatched approval token
  - verify runner privilege disclosure

## ADR

### Decision

Phase 1 will build a local-first single-server AI operations panel with typed tool execution and approval-gated repairs.

### Drivers

- Small and medium teams need immediate value on Linux/Docker/Nginx servers.
- Real operational trust depends on evidence and safety, not only chat.
- Local-first reduces networking, enrollment, and security complexity.

### Alternatives Considered

- Central control plane plus remote agents from day one.
- UI-only mock.
- Kubernetes-first AIOps platform.

### Why Chosen

The local-first MVP proves the hardest product loop first: AI can safely inspect a real server, explain evidence, and guide or execute approved repairs.

### Consequences

- Multi-server management is deferred.
- Agent enrollment and remote transport are deferred.
- Phase 1 must still keep clean boundaries so those later capabilities can be added.

### Follow-ups

- Phase 2: multi-server agent enrollment and grouping.
- Phase 3: scheduled inspections, alert notifications, and team permissions.
- Phase 4: richer remediation runbooks and rollback support.
- Phase 5: optional Kubernetes and cloud-native integrations.

## Available-Agent-Types Roster

- `planner`: refine scope and sequencing.
- `architect`: validate backend/runner/agent boundaries.
- `critic`: challenge risks, acceptance criteria, and product coherence.
- `executor`: implement backend, runner, and UI slices.
- `test-engineer`: design test fixtures and safety tests.
- `verifier`: validate end-to-end behavior and evidence.
- `designer`: refine SMB operations UX.
- `dependency-expert`: evaluate LLM/tool/runtime dependencies if needed.
- `security-reviewer` or `code-reviewer`: review command execution and secrets handling.

## Follow-up Staffing Guidance

### Recommended `$ultragoal` path

Use `$ultragoal` as the default durable execution path. Suggested lanes:

- Backend/API lane: executor, medium reasoning.
- Runner/tool registry lane: executor plus security review, high reasoning.
- AI orchestration lane: architect/executor, high reasoning.
- UI lane: designer/executor, medium reasoning.
- Tests/safety lane: test-engineer/verifier, high reasoning.

### Recommended `$team` path

Use `$team` if parallel implementation is desired:

```text
$team "Implement phase 1 from .omx/plans/prd-ai-native-server-ops-phase1-20260706T035422Z.md and .omx/plans/test-spec-ai-native-server-ops-phase1-20260706T035422Z.md"
```

Suggested team lanes:

- Worker 1: backend API and persistence.
- Worker 2: tool registry and local runner.
- Worker 3: AI diagnosis orchestrator and prompts.
- Worker 4: Web panel operational UI.
- Worker 5: tests, fixtures, and verification.

### `$ralph` fallback

Use `$ralph` only if a single persistent owner is intentionally preferred over parallel delivery. It is not the default for this plan.

## Team Verification Path

Before team shutdown, prove:

- API starts locally.
- At least one health snapshot is real or fixture-backed.
- Diagnosis session executes multiple typed tools.
- Approval gate blocks mutating action until approved.
- Audit log records tool calls.
- UI shows overview, diagnosis timeline, approval prompt, and report.
- Safety tests pass for arbitrary-shell rejection and secret redaction.

## Goal-Mode Follow-up Suggestions

- `$ultragoal`: default for durable implementation tracking.
- `$team`: recommended together with `$ultragoal` when parallel delivery is needed.
- `$performance-goal`: not needed unless later optimizing command latency or UI response time.
- `$autoresearch-goal`: not needed for implementation; use only if doing a separate market/technical research deliverable.
