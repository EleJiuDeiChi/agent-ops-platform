# Context Snapshot: AI Native Server Ops Phase 1

## Task Statement

Plan the first development phase for an AI-native server operations panel aimed at small and medium teams.

## Desired Outcome

Produce a consensus-ready first-phase development plan for a real MVP direction: single-server operations, tool execution, AI diagnosis, safety approvals, and a minimal Web panel.

## Known Facts / Evidence

- The old static demo page has been removed at the user's request and must not be used as a product or architecture reference.
- `README.md` now describes the project direction and points to `.omx/` planning artifacts.
- No real backend, agent daemon, tool execution, data collection, authentication, persistence, or LLM integration exists yet.
- User target is small and medium teams, not large-enterprise AIOps or Kubernetes-first cloud-native SRE.
- Prior product positioning in this thread: "面向中小团队的 AI 原生服务器运维面板", closer to "AI 版 1Panel / 宝塔" than "K8s AIOps 平台".

## Constraints

- First phase should avoid Kubernetes-first scope.
- First phase should focus on one Linux server and common SMB stack: Linux, Docker, Nginx, basic database checks, logs, and security inspection.
- Execution must be safe by default: read-only commands first; mutating actions require approval and audit.
- Product should not be a pure chat demo. It needs a concrete execution layer and observable outputs.
- Phase 1 should be planned as a real product from a clean implementation baseline, not as an iteration of the old static page.

## Unknowns / Open Questions

- Preferred implementation stack is not explicitly chosen.
- Deployment target is not explicitly chosen: local single binary, Docker Compose, or cloud-hosted control plane plus agent.
- LLM provider and model are not specified.
- Whether this is initially self-hosted only or SaaS-ready is not specified.

## Planning Assumptions

- Choose a conservative MVP stack that can run on a single server and later expand to multi-server.
- Keep phase 1 self-hosted/local-first to reduce trust, networking, and credential complexity.
- Treat LLM provider as pluggable through an adapter.
- Use a server-side allowlisted tool registry rather than letting the model execute arbitrary shell commands.

## Likely Codebase Touchpoints

- Current top-level project file: `README.md`.
- New likely modules for implementation phase:
  - backend API service
  - local server agent / command runner
  - tool registry
  - AI diagnosis orchestrator
  - persistence/audit store
  - Web panel pages for overview, services, AI diagnosis, reports
