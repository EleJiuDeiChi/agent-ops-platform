# M0 Architecture Contracts

Created at: 2026-07-09

## Scope

This document records the M0 contract bridge from the existing Phase 1 MVP to the capability/task/event/agent roadmap.

M0 does not implement the full task system or remote agent. It defines stable contracts and compatibility rules so M1 and later milestones can migrate without breaking the current UI and tests.

## Current Phase 1 Tables

| Current table | Current role | Future contract direction |
| --- | --- | --- |
| `users` | local app admin identity | remains auth identity; future RBAC can extend it |
| `sessions` | signed web session backing `SessionIdentity` | remains app session; approval fingerprints keep binding to `session_id` |
| `audit_records` | tool/approval audit rows | grows task/event references and maps to audit trail for capabilities |
| `tool_results` | persisted command/tool result payloads | remains evidence store behind `output_ref` |
| `diagnosis_sessions` | AI diagnosis run metadata | can become an `ops_events` scope named `diagnosis` |
| `diagnosis_events` | diagnosis SSE events | maps to `OpsEvent(scope=\"diagnosis\")` |
| `approvals` | pending/approved/denied repair approval | remains approval object; later links to `ops_tasks` |
| `reports` | generated inspection/diagnosis reports | remains report payload, with future task/event/evidence backlinks |

## Current API Compatibility

The existing Phase 1 API remains valid during M0:

- `GET /api/tools`
- `POST /api/tools/{tool_name}/invoke`
- `POST /api/diagnosis/sessions`
- `GET /api/diagnosis/sessions/{id}/events`
- `POST /api/approvals/{id}/approve`
- `POST /api/approvals/{id}/deny`
- `GET /api/audit`
- `GET /api/reports`
- `GET /api/diagnostics/runner`

M0 adds:

- `GET /api/capabilities`

`GET /api/capabilities` is a richer contract view over the same registered tools. It must stay semantically compatible with `GET /api/tools`: every current tool maps to exactly one capability with the same id/name, risk level, approval requirement, enabled state, and input schema.

## Capability Contract

`Capability` extends the current `Tool` shape with fields required by later milestones:

- `id`
- `name`
- `display_name`
- `category`
- `description`
- `ai_description`
- `ui_description`
- `version`
- `enabled`
- `input_schema`
- `output_schema`
- `risk_level`
- `approval_required`
- `supports_dry_run`
- `supports_rollback`
- `default_timeout_seconds`
- `resource_scope`
- `executor_kind`
- `audit_fields`

M0 maps current in-process adapters to `executor_kind=\"local_process\"`. M2 will move invocation behind `AgentClient` without changing the public capability contract.

## Task/Event/Agent Contracts

M0 defines Pydantic contracts for:

- `OpsTask`
- `OpsSubtask`
- `OpsEvent`
- `ApprovalPolicy`
- `Agent`
- `AgentHeartbeat`

These are intentionally not wired to database migrations yet. M1 owns persistence and APIs for task/event lifecycle.

## Stop Gate Evidence

M0 is complete when:

- Existing Phase 1 tests still pass.
- `GET /api/capabilities` is protected like `GET /api/tools`.
- Authenticated and password-ready users can list capabilities.
- `GET /api/tools` and `GET /api/capabilities` expose matching registered tool ids.
- `docker.restart` remains `mutating` and approval-required in both old and new contract surfaces.
