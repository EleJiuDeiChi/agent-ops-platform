# Context Snapshot: AI Ops Capability Roadmap

Created at: 2026-07-09T10:21:23Z

## Task Statement

按照已讨论的规划层、执行层和中间依托层思路，为 Agent 运维平台规划一份完整的功能能力开发计划。

## Desired Outcome

产出可交给后续执行模式使用的能力路线图，明确：

- 哪些能力属于规划层，哪些能力属于执行层。
- 规划层、任务/审计/审批/事件流、执行层之间的依托关系。
- 各能力的开发顺序、里程碑、验收标准和风险控制。
- 后续应该用何种执行模式推进实现。

## Known Facts And Evidence

- 项目定位是面向中小团队的 AI 原生服务器运维面板。
- 当前规则要求底层架构采用 `Backend Control Plane -> Task/Audit/Approval/Event Stream -> Node Agent(s)`。
- `demo/底层能力研究报告.md` 结论：1Panel 更适合作为架构参考，宝塔只作为能力清单和运维经验参考，不能直接复制宝塔业务代码。
- 现有 Phase 1 规划产物位于：
  - `.omx/context/ai-native-server-ops-phase1-20260706T035422Z.md`
  - `.omx/plans/prd-ai-native-server-ops-phase1-20260706T035422Z.md`
  - `.omx/plans/test-spec-ai-native-server-ops-phase1-20260706T035422Z.md`
- 现有 Phase 1 共识门已完成，Architect 与 Critic 均为 `APPROVE`。
- 当前代码已有基础骨架：
  - FastAPI 后端、React + TypeScript + Vite 前端。
  - SQLite 存储。
  - 登录、CSRF、密码变更、会话。
  - 工具注册、命令执行器、诊断事件、审批、审计、报告。
  - Ant Design 与 Ant Design X UI。
- 当前执行能力仍偏 MVP 骨架：
  - 工具主要是本地进程内 adapter。
  - 还没有独立 Node Agent。
  - 还没有统一 ops_tasks / ops_events 一等模型。
  - Docker restart 仍是 placeholder。
  - Nginx、Docker、数据库、文件、终端、安全策略尚未完整产品化。

## Constraints

- 控制面不能直接执行 root 命令。
- 所有高危、耗时或有副作用操作必须进入统一任务系统。
- Agent capability 必须声明 schema、risk_level、dry_run、rollback、audit fields。
- AI 只能提出建议或待审批动作，不能默认自动执行高危命令。
- 前端普通后台页面优先 Ant Design，AI 对话和诊断台优先 Ant Design X。
- 不得复制宝塔业务代码或不透明插件体系。
- 直接使用或改造 1Panel 代码前必须评估 GPLv3 义务，默认只做架构参考。
- 本次为 `$ralplan` 规划模式，只允许写 `.omx/context/`、`.omx/plans/`、`.omx/specs/`、`.omx/state/` 等规划产物。

## Unknowns And Open Questions

- 第一版 Agent 是独立进程、sidecar，还是先在后端内抽象成 agent interface 后再拆出进程。
- 首个真实 Linux 测试环境是否固定为 devbox，还是另建可破坏测试机。
- 文件管理和终端能力是否在第二阶段开放真实写操作，还是先只读/下载/审计。
- 数据库能力首选 MySQL、PostgreSQL、Redis 的顺序需要结合目标用户环境确认。

## Likely Codebase Touchpoints

- `backend/app/models/schemas.py`
- `backend/app/storage/db.py`
- `backend/app/tools/registry.py`
- `backend/app/runner/command.py`
- `backend/app/ai/agent_orchestrator.py`
- `backend/app/main.py`
- `frontend/src/main.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/types/api.ts`
- `scripts/smoke.sh`
- future: `backend/app/tasks/`, `backend/app/events/`, `backend/app/agents/`, `backend/app/capabilities/`

## Planning Assumption

第二阶段不追求宝塔式大而全菜单，也不直接跳到多节点控制台。正确顺序是先把“任务化执行底座”做扎实，再逐步挂载 Docker、Nginx、文件、终端、数据库、安全等真实执行能力，最后再扩展到多节点、插件市场和本地模型运行时。
