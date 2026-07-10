# Test Spec: AI Native Server Ops Panel Phase 1

## Scope

Test the phase 1 MVP for a local-first AI-native server operations panel. The test plan covers the backend API, local tool runner, AI diagnosis loop, approval gate, audit log, and Web panel.

## Test Commands

Expected phase-1 verification commands:

```bash
cd backend && pytest
cd frontend && npm test
cd frontend && npx playwright test
./scripts/smoke.sh
```

No test may require a real external LLM provider. Real-provider checks are manual or optional integration tests guarded by environment variables.

## Acceptance Test Matrix

| Area | Scenario | Expected Result |
|---|---|---|
| Server overview | Load local health snapshot | CPU/load, memory, disk, uptime, process count, and listening ports are displayed |
| Tool registry | Authenticated user lists available tools | Tools include category, risk level, input schema, and approval requirement |
| Tool execution | Authenticated user runs read-only health tools | Commands execute through typed adapters with timeout and structured result |
| Safety | Attempt arbitrary shell | Request is rejected because no unrestricted shell exists in phase 1 |
| Safety | Attempt mutating restart without approval | Action returns approval-required, no command is executed |
| Audit | Run any tool | Audit record includes timestamp, tool, params, risk, status, duration, output summary |
| AI diagnosis | Ask server inspection question | System calls multiple read-only tools and returns evidence-backed report |
| AI diagnosis | Ask Docker failure question | System checks container state/logs/inspect output and proposes next action |
| AI diagnosis | Ask Nginx 502 question | System checks status, config test, error log, and upstream reachability |
| Output handling | Tool emits long log | Output is truncated/summarized and session continues |
| Redaction | Tool emits fake secret | Secret is redacted before persistence and before LLM context |
| UI | Diagnosis session runs | UI shows tool-call timeline and final answer |
| UI | Repair proposal appears | UI shows approval modal with command/action, risk, and rollback note |
| Access control | Unauthenticated request invokes a tool | Request is rejected |
| Access control | Unauthenticated request reads tools or runner diagnostics | Request is rejected |
| Approval integrity | Approval is replayed, actor/session changes, or parameters are changed | Request is rejected and no action executes |
| Runner privilege | Diagnostics page/API exposes runner mode | User can see runner user and whether privileged adapters are enabled |
| Browser security | Mutating request lacks CSRF token or has invalid origin | Request is rejected |
| Login bootstrap | First admin logs in with bootstrap password | Session is created and password change is required before tool execution |
| Password change | Bootstrap admin changes password | Existing session remains valid and tool execution becomes allowed |
| CSRF bootstrap | Browser requests CSRF token before login | API returns token and sets CSRF cookie |

## Unit Tests

- Tool schema validation accepts valid definitions and rejects missing name/category/risk/input schema.
- Risk classification maps read-only tools to auto-executable and mutating tools to approval-required.
- Allowlist rejects commands outside registered adapters.
- Output redaction masks common credential patterns.
- Output truncation preserves status, first/last useful excerpts, and omission marker.
- Approval token/session cannot approve a different actor/session or action than the one proposed.
- Approval token/session expires and cannot be replayed.
- Auth middleware blocks health detail, logs, tool calls, approvals, and audit APIs when unauthenticated.
- Session cookie configuration defaults to `HttpOnly` and `SameSite=Strict`, and enables `Secure` under HTTPS.
- CSRF middleware rejects mutating browser-authenticated requests without a valid token.
- Origin validation rejects mutating browser-authenticated requests from untrusted origins.
- `GET /api/csrf` returns a CSRF token and sets a CSRF cookie before login.
- Login rejects missing `X-CSRF-Token` and invalid origin.
- Successful bootstrap login sets a signed HTTP-only session cookie and returns `must_change_password: true`.
- Tool execution and approval APIs reject sessions that have not completed required password change.
- Password change accepts current password plus new password, refreshes the identity, and clears `must_change_password`.

## Integration Tests

- `GET /api/health-snapshot` returns a structured local or fixture snapshot.
- Authenticated `GET /api/tools` returns registered tools.
- Authenticated `POST /api/tools/{tool_name}/invoke` runs read-only tools and returns structured result.
- `POST /api/diagnosis/sessions` creates a session.
- Diagnosis event stream emits plan, tool-call, tool-result, final-answer events.
- `POST /api/approvals/{id}/approve` executes only the pending approved mutating action.
- Audit store persists tool calls and repair decisions.
- Unauthenticated requests to protected APIs return unauthorized.
- Unauthenticated requests to `GET /api/tools` and `GET /api/diagnostics/runner` return unauthorized.
- Mismatched approval fingerprint returns rejected without executing the tool.
- `GET /api/diagnosis/sessions/{id}/events` streams ordered `plan`, `tool_call`, `tool_result`, and `final_answer` events.
- Mock LLM mode returns deterministic output for fixture-backed diagnosis sessions.
- `POST /api/approvals/{id}/approve` rejects missing CSRF token and invalid origin.
- `GET /api/csrf` returns `{ "csrf_token": "..." }` and sets a CSRF cookie.
- `POST /api/login`, `POST /api/logout`, `POST /api/me/password`, `POST /api/tools/{tool_name}/invoke`, and `POST /api/diagnosis/sessions` reject missing `X-CSRF-Token` and invalid origin.
- `POST /api/login` accepts `{ "username": "admin", "password": "..." }` with `X-CSRF-Token` and returns `SessionIdentity` plus `must_change_password`.
- `POST /api/me/password` accepts `{ "current_password": "...", "new_password": "..." }` with `X-CSRF-Token` and returns refreshed `SessionIdentity` plus `must_change_password: false`.
- Login failure response does not reveal whether username or password was incorrect.

## Contract Tests

- `Tool` includes name, category, description, input schema, risk level, approval flag, timeout, and enabled state.
- `ToolResult` includes tool name, status, timing, output summary/ref, redaction flag, and error.
- `DiagnosisSession` includes id, status, user question, timestamps.
- `DiagnosisEvent` includes type, session id, sequence, payload, timestamp.
- `SessionIdentity` includes actor id, session id, role, created timestamp, expiry timestamp.
- `Approval` includes actor id, auth session id, optional diagnosis session id, nonce, action fingerprint, tool, target, canonical params, risk, expiry, status.
- `AuditRecord` includes actor, event type, resource, risk, status, summary, timestamp.
- `Report` includes type, health score, risk items, evidence refs, recommended actions, timestamp.
- `RunnerDiagnostics` includes runner user, effective uid, bind host, privileged adapters, sudo flag, tool count, LLM mode.

## End-to-End Smoke Tests

1. Healthy server inspection:
   - Open panel.
   - Ask "帮我巡检这台服务器".
   - Verify the final report includes at least CPU/load, memory, disk, ports, services, and security login checks.

2. Docker container down:
   - Use fixture or test container in stopped/crashing state.
   - Ask "这个容器为什么挂了".
   - Verify state, logs, inspect output, likely cause, and restart proposal.

3. Nginx 502:
   - Use fixture or test Nginx upstream failure.
   - Ask "网站为什么 502".
   - Verify Nginx status, config test, error log, upstream reachability, and recommended fix.

4. Disk pressure:
   - Use fixture representing high disk usage.
   - Ask "磁盘快满了怎么办".
   - Verify largest safe targets are listed and deletion requires explicit approval.

5. Security inspection:
   - Use fixture with failed SSH login entries and exposed ports.
   - Ask "服务器有没有安全风险".
   - Verify failed login summary, listening ports, firewall status, and risk ranking.

## Non-Goals For Phase 1 Testing

- Kubernetes workload diagnosis.
- Multi-server enrollment and remote execution.
- Enterprise RBAC/SSO.
- Full alerting pipeline.
- Autonomous destructive repair.

## Verification Evidence Required Before Phase 1 Completion

- Test command output for unit/integration tests.
- Screenshot or browser check of overview, diagnosis timeline, approval modal, and report page.
- Sample audit log entry.
- Sample diagnosis transcript with at least five tool calls.
- Safety proof showing arbitrary shell and unapproved mutation are rejected.
- Access-control proof showing unauthenticated tool execution is rejected.
- Access-control proof showing unauthenticated tools list and runner diagnostics are rejected.
- Approval-integrity proof showing replay and parameter substitution are rejected.
- Browser-session proof showing CSRF and origin failures are rejected.
- Bootstrap-login proof showing first login requires password change before tool execution.
- Password-change proof showing tool execution works after required password change.
- CSRF bootstrap proof showing unauthenticated browser can obtain a token before login.
- Sample SSE event stream from a diagnosis session.
- Runner diagnostics output showing current privilege mode.
