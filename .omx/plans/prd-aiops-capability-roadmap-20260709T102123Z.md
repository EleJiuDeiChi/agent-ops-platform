# PRD: AI Ops Platform Capability Roadmap

## Requirements Summary

在现有 Phase 1 MVP 骨架基础上，建设一套完整的 AI 原生服务器运维能力体系。核心不是补菜单，而是形成可审计、可审批、可回滚的闭环：

```text
执行层采集事实
  -> 规划层生成方案
  -> 审批策略判断风险
  -> 任务系统拆分步骤
  -> 执行层按能力接口执行
  -> 事件流和审计回传
  -> 规划层复核并生成报告
```

## Product Positioning

面向中小团队的 AI 原生服务器运维平台，让团队通过自然语言完成服务器巡检、故障诊断、风险识别、变更审批、执行回滚和报告沉淀。

## RALPLAN-DR Summary

### Principles

1. 规划层负责判断和编排，执行层负责受控动作，中间层负责任务、审批、审计和事件流。
2. 先建设任务化执行底座，再扩展具体运维能力。
3. 所有 capability 必须 typed、可审计、可 dry-run 或显式说明不支持。
4. 高危动作默认审批，不把 AI 建议等同于执行许可。
5. UI 必须服务 AI 运维闭环，而不是复刻传统面板菜单。

### Decision Drivers

1. 安全可信：高危操作需要审批、回滚、事件流和审计证据。
2. 能力复用：Docker、Nginx、数据库、文件、终端都要复用统一任务/事件/命令底座。
3. 渐进交付：先单机真实可用，再扩展多节点和插件市场。

### Viable Options

#### Option A: 先做统一任务执行底座，再挂载能力域

先实现 `ops_tasks`、`ops_events`、`command_runner`、`agent_capabilities`、`approval_policy`，再逐步接 Docker、Nginx、数据库、文件、终端等能力。

Pros:
- 安全和审计边界先统一，后续能力不会散落实现。
- 后续扩展多节点 Agent 更自然。
- 能让 AI 诊断、任务、审批、报告共享一条事件链。

Cons:
- 初期可见功能推进慢于直接加菜单。
- 需要先做较多基础模型和 API。

#### Option B: 直接按宝塔/1Panel 菜单补齐功能

优先补 Docker、Nginx、数据库、文件、终端等页面，每个页面按当前需要实现后端 API。

Pros:
- 短期页面可见度高。
- 容易对照传统面板能力清单验收。

Cons:
- 容易产生散落命令执行和重复审批逻辑。
- AI 难以复用工具链，后续审计和回滚成本高。
- 不符合项目规则中的控制面/Agent 分层模型。

#### Option C: 先做多节点 Agent 架构

立即拆出独立 Agent、远程 mTLS、节点注册、心跳、远程任务下发。

Pros:
- 更接近长期架构。
- 多服务器场景更早验证。

Cons:
- 身份、网络、安全和部署复杂度提前爆炸。
- 单机价值没有完全打透前，容易做成复杂控制台。

### Chosen Direction

选择 Option A。第二阶段先完成任务化执行底座和本地 Agent 抽象，再按能力域挂载真实 adapter。远程独立 Agent 和多节点放到后续阶段，但所有接口从一开始按可拆分 Agent 设计。

### Pre-mortem

#### Failure 1: 高危命令绕过审批或任务系统执行

- 触发条件：domain adapter 直接调用 `subprocess`、SDK mutating API 或 shell compatibility layer，没有创建 task，也没有 approval fingerprint。
- 预防机制：所有 mutating/destructive capability 必须经 `approval_policy -> ops_tasks -> command_runner/AgentClient`；代码层禁止业务 API 直接调用底层命令 adapter；shell compatibility layer 默认禁用并标记 destructive。
- 检测信号：审计中出现 mutating 结果但没有 task_id、approval_id 或 ops_events；静态检查发现 adapter 绕过 AgentClient；测试中直接命中 API 但没有 approval_required。
- 回滚/止损：立即禁用该 capability，撤销对应任务入口，保留审计证据；如果已执行真实变更，使用该 capability 声明的 rollback_plan 或人工恢复手册。
- 对应测试：mutating adapter contract test、approval fingerprint test、audit/task linkage test、arbitrary shell rejection test。

#### Failure 2: SSH 或防火墙变更导致服务器失联

- 触发条件：修改 SSH 端口、登录策略、防火墙规则或默认策略时，没有 preflight、连通性保护和定时自动回滚。
- 预防机制：SSH/firewall 写操作必须生成 rollback_plan，必须先做当前连接保护、目标端口探测、规则 dry-run，并创建延迟自动回滚任务。
- 检测信号：变更后 heartbeat 中断、控制面无法拉取 agent diagnostics、目标端口探测失败、自动回滚确认超时。
- 回滚/止损：自动执行预先登记的 revert command；如果 agent 失联，报告中输出云控制台/本地控制台人工恢复步骤。
- 对应测试：firewall/SSH preflight test、connectivity guard test、rollback timer test、heartbeat loss simulation。

#### Failure 3: 文件写入或数据库恢复破坏数据且无法回滚

- 触发条件：写文件前没有 backup artifact，数据库 restore/import 前没有校验备份、确认目标库或记录恢复点。
- 预防机制：文件写必须遵守 allowed roots、diff、backup、审批、写后校验、rollback artifact；DB restore/import 必须验证备份可读、目标库匹配、恢复点存在，并走 destructive 二次审批。
- 检测信号：写入事件缺少 backup_ref、diff_ref、verification_result；DB restore task 缺少 backup_checksum、target_db、restore_point、second_approval。
- 回滚/止损：文件从 backup artifact 恢复；数据库按恢复点或最近备份回滚；如果回滚失败，任务状态必须标记 failed_with_manual_recovery 并输出人工步骤。
- 对应测试：file write invariant test、backup artifact test、DB restore destructive approval test、rollback failure reporting test。

## Layer Definitions

### 规划层

规划层回答“该做什么、为什么做、怎么安全做”。

能力包括：

- AI 诊断台。
- 诊断编排器。
- 工具/能力注册表。
- 风险分类和审批策略。
- dry-run、diff、回滚方案生成。
- 诊断报告、巡检报告和复核报告。
- 专业工具区中的“建议动作”生成。

规划层不直接执行命令，也不持有 root 权限。

### 中间依托层

中间依托层回答“动作如何被追踪、审批、调度、回放”。

能力包括：

- `ops_tasks`：任务、子任务、状态、取消、失败原因。
- `ops_events`：计划、工具调用、输出流、审批、回滚、最终结果。
- `approval_policy`：按工具、参数、资源、风险等级判断是否需要审批。
- `audit_records`：所有调用、审批和结果的一等审计记录。
- `command_runner`：统一命令执行、timeout、stdout/stderr、取消、输出截断、敏感信息脱敏。
- `report_builder`：从事件和审计生成报告。

这一层是规划层和执行层之间的强制边界。

### 执行层

执行层回答“被批准的动作如何落到服务器”。

能力包括：

- Node Agent / 本地 Agent interface。
- 主机指标和系统状态。
- systemd 服务状态、日志、restart/reload。
- Docker 容器、镜像、网络、卷、Compose。
- Nginx 站点、反代、配置测试、reload、回滚。
- SSL / ACME 证书申请、续签、部署。
- MySQL / PostgreSQL / Redis 状态、备份、恢复、连接诊断。
- 文件浏览、上传、下载、编辑、压缩、权限检查。
- 受控终端、SSH 会话审计、命令风险拦截。
- 防火墙、SSH 安全策略和登录风险检查。

执行层不自行决定业务策略，只按任务和 capability schema 执行。

## Capability Dependency Model

```text
Capability Registry
  -> Approval Policy
  -> Task Model
  -> Event Stream
  -> Command Runner
  -> Local Agent Interface
  -> Domain Adapters
  -> AI Diagnosis / Reports / UI
```

关键依赖：

- 没有 capability registry，就不能稳定暴露工具给 AI。
- 没有 approval policy，就不能安全开放 mutating adapter。
- 没有 task/event model，就不能执行长任务、输出进度、失败回滚。
- 没有 command runner，就不能统一 timeout、取消、脱敏、审计。
- 没有 agent interface，就会把控制面和执行面绑死。

## Functional Capability Plan

### Foundation 1: Capability Registry

目标：把工具从内存字典升级为可声明、可展示、可审计的能力目录。

范围：

- capability id、display name、category、version、enabled。
- input_schema、output_schema、risk_level。
- supports_dry_run、supports_rollback。
- requires_approval、default_timeout、resource_scope。
- executor kind：local_process、local_agent、remote_agent、mock。
- UI 可读说明和 AI 可用描述分离。

验收：

- `GET /api/tools` 返回完整 capability metadata。
- AI 只能调用 enabled 且 schema 合法的 capability。
- 前端工具区能按能力域、风险等级、是否审批过滤。

### Foundation 2: Unified Task System

目标：所有耗时、高危、有副作用的操作都进入任务系统。

范围：

- `ops_tasks`：id、type、title、status、actor、resource、risk、reason、created_at、started_at、finished_at、cancel_requested、failure_reason。
- `ops_subtasks`：step name、status、started_at、finished_at、rollback_step、output_ref。
- 任务状态：pending、waiting_approval、running、succeeded、failed、cancelled、rolled_back。
- API：创建任务、读取任务、列表、取消、重试、读取事件。
- `PathPolicy`、`TerminalSession` 元数据和 command risk classifier 在本阶段建立基础模型；完整文件 UI 和终端 UI 可后置，但审计与风险分类不能后置。

验收：

- Docker restart、Nginx reload、文件写入、数据库恢复等 mutating 操作不能绕过 task。
- 任务详情页能看到步骤和事件流。
- 任务失败有结构化失败原因。
- 文件路径策略、终端会话审计和危险命令分类有最小 contract test。

### Foundation 3: Event Stream And Audit

目标：统一 AI 诊断事件、任务事件、命令输出事件。

范围：

- `ops_events`：event id、scope、scope_id、sequence、type、payload、created_at。
- 事件类型：plan、task_created、approval_required、approval_granted、command_started、stdout、stderr、command_finished、rollback_started、rollback_finished、final_report。
- SSE 支持任务流和诊断流。
- 审计记录引用事件和任务。

验收：

- UI 诊断台和任务详情使用同一事件模型。
- 每个 mutating 操作至少有审批事件、执行事件、结果事件。
- 报告可以从事件流重建主要过程。

### Foundation 4: Command Runner

目标：把命令执行做成独立、安全、可测试的底层能力。

范围：

- fixed argv，不允许业务代码拼裸 shell。
- timeout、cwd、env allowlist、stdout/stderr streaming。
- max output、secret redaction、exit code、duration。
- cancel token。
- dry-run mode。
- shell compatibility layer 仅作为高危 capability。
- command risk classifier：识别删除、覆盖、权限变更、网络暴露、服务重启、数据库恢复、SSH/firewall 变更等风险动作。

验收：

- 所有 adapter 通过 command runner 或 SDK wrapper 执行。
- 任意 shell 命令默认不可用。
- 超时、取消、长输出、敏感信息脱敏有单元测试。
- AI 可以生成命令建议，但不能自动提交终端执行；终端执行必须由用户触发并进入 session audit。

### Foundation 5: Local Agent Interface

目标：先在单机内建立 Agent 边界，为后续独立进程/远程 Agent 铺路。

范围：

- `AgentClient` interface：list_capabilities、invoke、stream_task、heartbeat、diagnostics。
- 第一版可由 in-process local adapter 实现。
- 设计 unix socket transport 预留。
- agent diagnostics 暴露当前用户、权限、能力数量、privileged adapters。

验收：

- 控制面 API 不直接调用具体命令 adapter，而是通过 agent/capability service。
- runner diagnostics 能说明是否为 in-process/local-socket/remote。
- 后续拆出独立 Agent 不需要改 AI orchestrator 和前端 API。

## Domain Capability Waves

说明：`AGENTS.md` 将终端/SSH 会话审计和受限文件管理列为第一优先级，因此本路线把 `PathPolicy`、`TerminalSession` 元数据、命令风险分类和审计基础提前到 Foundation 2-4。Wave 4 延后的是完整文件管理 UI、写入体验、压缩解压和交互式终端产品化，不是延后安全底座。

### Wave 1: Host And Service Inspection

目标：把现有主机巡检从 MVP 升级为稳定执行层。

能力：

- system.health、system.disk、system.processes、network.ports。
- systemd.status、systemd.logs、systemd.restart、systemd.reload。
- journal/log tail with output limits。
- SSH 登录风险、failed login、监听端口、基础防火墙状态。

依赖：

- Capability Registry。
- Command Runner。
- Audit/Event。

风险策略：

- status/log/read-only 自动执行。
- restart/reload 需要 approval。

### Wave 2: Docker And Compose

目标：从 Docker 查询扩展到容器诊断和受控修复。

能力：

- docker.ps、docker.inspect、docker.logs、docker.stats。
- docker.restart、docker.stop、docker.start。
- compose.list、compose.config、compose.up、compose.down。
- 镜像、网络、卷只读清单。

依赖：

- Task System。
- Approval Policy。
- Event Stream。

风险策略：

- inspect/logs/stats 自动执行。
- restart/start/stop 需要审批。
- compose down、volume delete 标记 destructive，默认不开放或需要二次审批。

### Wave 3: Nginx / Site / SSL

目标：实现网站故障诊断和低风险站点变更闭环。

能力：

- nginx.status、nginx.config_test、nginx.logs。
- site.list、site.proxy_check、site.upstream_probe。
- nginx config diff、backup、write、test、reload、rollback。
- SSL cert status、ACME issue/renew、deploy。

依赖：

- Task System。
- rollback metadata。
- file backup storage。

风险策略：

- test/log/probe 自动执行。
- write/reload/SSL deploy 需要审批。
- 每个 reload 前必须 test，失败自动 rollback。

### Wave 4: Files And Terminal

目标：提供受控文件和终端能力，但不把它变成裸 root 面板。

能力：

- file.list、file.read、file.download、file.upload。
- file.diff、file.backup、file.write、file.chmod。
- archive compress/extract。
- terminal session、command risk detect、session recording。

依赖：

- Path policy。
- Risk classifier。
- Session audit。

风险策略：

- 文件读限制在 allowed roots。
- 文件写必须先 diff/backup。
- 终端默认审计，全量录制。
- 危险命令提示审批或阻断。

不可绕过的不变量：

- 文件写：必须命中 allowed roots；必须生成 diff_ref；必须生成 backup_ref；必须经审批；写后必须校验 hash/mtime/语法检查；失败必须能通过 rollback artifact 恢复。
- 终端：命令必须经过 risk classifier；read-only 命令可执行但仍审计；mutating 命令要求审批；destructive 命令默认阻断或二次审批；session recording 必须保存命令、输出、操作者、时间；AI 只能填入建议，不得自动提交执行。

### Wave 5: Database And Cache

目标：先做诊断、备份、恢复闭环，再做管理型操作。

能力：

- MySQL/PostgreSQL/Redis connection check。
- service status、version、connection count、slow query summary。
- db backup、restore、import、export。
- user/permission inspection。

依赖：

- Secret storage。
- Backup task。
- Approval Policy。

风险策略：

- 连接状态和指标只读。
- 备份可审批后执行。
- restore/import destructive，必须高危审批和恢复点确认。

不可绕过的不变量：

- Restore/import 必须确认目标库、备份来源、备份 checksum、备份可读性和恢复点。
- destructive 恢复必须二次审批，审批 fingerprint 必须绑定 actor、session、target_db、backup_ref、restore_mode。
- 恢复前必须生成当前状态备份或明确记录无法备份的原因。
- 恢复后必须执行连接检查和基础一致性检查，失败时标记需要人工恢复。

### Wave 6: Security And Firewall

目标：把安全从“展示状态”升级为“建议、审批、执行、验证”。

能力：

- failed login、recent login、sudo log、listening ports。
- ufw/firewalld/iptables read。
- firewall rule propose/apply/rollback。
- SSH config inspect、safe hardening proposal。
- fail2ban status/install proposal。

依赖：

- Approval Policy。
- rollback plan。
- preflight checks。

风险策略：

- 安全扫描只读。
- firewall/SSH 写操作必须高危审批。
- 修改 SSH 前必须做连通性保护和回滚计划。

不可绕过的不变量：

- SSH/firewall 变更必须有 preflight：当前连接来源保护、目标端口探测、规则 dry-run、冲突检测。
- 必须创建定时自动回滚任务，只有在变更后 heartbeat 和连通性检查通过时才能取消。
- approval payload 必须展示可能失联风险、回滚计划、预计影响范围和人工恢复入口。
- 如果 agent heartbeat 中断或端口探测失败，系统必须优先执行回滚任务。

### Wave 7: Multi-Node Agent

目标：从单机平台扩展为多节点管理。

能力：

- agent enrollment。
- heartbeat。
- capability sync。
- remote task dispatch。
- mTLS or equivalent strong auth。
- node inventory、tags、health。

依赖：

- Local Agent Interface 已稳定。
- Task/Event 抽象已经不绑定单机。

风险策略：

- 远程 Agent 不使用裸 token 执行高危命令。
- 所有节点任务绑定 node_id 和 actor。

### Wave 8: App Store / Plugins / MCP / Local Models

目标：在底座稳定后扩展生态能力。

能力：

- app manifest、install plan、dry-run、rollback。
- plugin manifest、schema、capability permissions。
- MCP tool registry。
- Ollama/local model runtime。
- GPU metrics。

依赖：

- Capability Registry。
- Sandbox policy。
- Versioned install tasks。

风险策略：

- 不加载不透明二进制插件。
- 插件安装前静态检查和权限声明。

## Milestones

### Milestone 0: Architecture Contracts

交付：

- Capability metadata schema。
- Task/Event/Audit/Approval schema。
- AgentClient interface。
- Migration plan from current tables。

Done when：

- 当前工具注册能映射到新 capability schema。
- 当前 diagnosis events 能映射到 ops_events。
- 无需改前端主流程即可兼容旧 API。

### Milestone 1: Task/Event Foundation

交付：

- `ops_tasks`、`ops_subtasks`、`ops_events`。
- task create/list/detail/cancel API。
- diagnosis events 与 task events 的统一流接口。
- 任务详情 UI。
- `PathPolicy`、`TerminalSession`、command risk classifier 的最小 schema 和 contract test。

Done when：

- 一个 mock long-running task 能流式输出事件。
- 审批、执行、失败、取消都能被事件流复现。
- 文件路径策略、终端会话审计和危险命令分类已能被后续 Wave 4 复用。

### Milestone 2: Real Command Runner And Local Agent Boundary

交付：

- command runner 支持 stdout/stderr streaming、timeout、cancel、redaction。
- local agent interface。
- runner diagnostics 升级。
- 所有现有工具走 agent/capability 调用。
- read-only terminal command prototype 只通过 command runner 执行并记录 session audit。

Done when：

- 任意命令不能从 API 直接执行。
- 工具执行都能记录 task/event/audit。
- AI 生成命令建议不会自动执行；用户触发的终端命令会被审计和风险分类。

### Milestone 3: Host/Systemd Productionization

交付：

- 主机巡检和 systemd 能力产品化。
- systemd restart/reload 走审批任务。
- SSH/login/security read-only inspection。

Done when：

- “帮我巡检这台服务器”产生不少于 8 个证据点。
- “重启 nginx 服务”必须审批，审批后形成任务和审计。

### Milestone 4: Docker/Nginx/Site Repair Loop

交付：

- Docker diagnose + approved restart。
- Nginx 502 diagnose。
- Nginx config diff/test/reload/rollback。
- 站点/反代基础模型。

Done when：

- Docker 容器异常和 Nginx 502 两类问题能诊断、建议、审批、执行、复核。

### Milestone 5: Files/Terminal/Database

交付：

- 文件只读、diff、backup/write。
- 受控终端和会话审计。
- DB 连接检测、备份、恢复审批。

Done when：

- 文件写入先 diff/backup。
- 终端会话可审计。
- DB restore 被识别为 destructive 并需要高危审批。

### Milestone 6: Security Hardening And Multi-Node Prep

交付：

- firewall/SSH security proposal。
- rollback-safe SSH/firewall changes。
- agent enrollment design and local socket prototype。

Done when：

- 修改防火墙或 SSH 前必须有连通性保护、审批和回滚计划。
- remote agent API contract 可以开始实现。

## API Surfaces To Add Or Stabilize

- `GET /api/capabilities`
- `POST /api/capabilities/{id}/invoke`
- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{id}`
- `POST /api/tasks/{id}/cancel`
- `GET /api/tasks/{id}/events`
- `GET /api/events?scope=...`
- `POST /api/approvals/{id}/approve`
- `POST /api/approvals/{id}/deny`
- `GET /api/agents`
- `GET /api/agents/{id}/diagnostics`
- `POST /api/agents/{id}/heartbeat` for future remote mode

Existing endpoints may remain as compatibility wrappers during migration.

## Data Contracts To Add

- `Capability`
- `CapabilityInvocation`
- `OpsTask`
- `OpsSubtask`
- `OpsEvent`
- `ApprovalPolicy`
- `Agent`
- `AgentHeartbeat`
- `CommandExecution`
- `RollbackPlan`
- `PathPolicy`
- `TerminalSession`
- `BackupArtifact`

## UI Plan

### Overview

- 主机健康、风险、最近任务、待审批动作。
- 当前 runner/agent mode。
- 今日 AI 诊断和变更统计。

### AI 诊断台

- Ant Design X `Bubble.List`、`Sender`、`ThoughtChain`、`Actions`。
- 工具调用链和任务事件流合并展示。
- 计划、审批、执行、复核分段。

### 专业工具区

- 服务器：主机、进程、端口、服务。
- 容器：Docker/Compose。
- 网站：Nginx、站点、证书。
- 数据库：MySQL/PostgreSQL/Redis。
- 文件：受控目录与 diff。
- 终端：受控会话和审计。
- 安全：登录、端口、防火墙、SSH。

### 审计与报告

- 按任务、资源、操作者、风险级别过滤。
- 每份报告能回链到事件、任务和证据。

## ADR

### Decision

第二阶段采用“任务化执行底座优先”的能力开发路线。先统一 capability、task、event、approval、audit、command runner 和 local agent interface，再逐步接入主机、Docker、Nginx、文件、终端、数据库、安全、多节点和插件生态。

### Drivers

- 高危运维动作必须可审批、可回放、可回滚。
- AI 诊断需要 typed capability 和事件证据，而不是裸 shell。
- 后续多节点 Agent 需要当前单机能力先具备明确边界。

### Alternatives Considered

- 直接补传统面板菜单：短期可见，但会制造散落执行逻辑和重复审批。
- 立即做多节点远程 Agent：长期正确，但早期安全和部署复杂度过高。

### Why Chosen

该路线最符合项目规则和已讨论架构：规划层与执行层分离，中间依托层强制承载任务、审批、审计和事件流。

### Consequences

- 第二阶段前半段会更偏底座建设，UI 可见增量需要靠任务页、事件流和诊断台体现。
- 后续每个能力域都必须提供 schema、risk、dry-run/rollback/audit 字段，开发门槛提高。
- 一旦底座稳定，新增能力将更快、更安全，也更适合 AI 调用。

### Follow-ups

- 确认首个真实 Linux 测试环境。
- 确认 Wave 5 数据库优先级。
- 确认文件写和终端执行开放边界。
- 为 Milestone 0-2 建立执行任务清单后进入 `$ultragoal` 或 `$team`。

## Available Agent Types For Follow-up

- `planner`：把里程碑拆成可执行 issue、checkpoint 和 stop gate。
- `architect`：能力边界、Agent contract、任务模型评审。
- `executor`：后端 schema/API/runner 实现。
- `test-engineer`：任务、审批、命令执行和 UI 流程测试。
- `designer`：Ant Design / Ant Design X 信息架构和交互。
- `critic`：高危操作、安全策略、验收标准审查。
- `verifier`：本地、devbox、浏览器和安全证据收集。

## Follow-up Staffing Guidance

| Lane | Suggested roles | Reasoning level | Why this lane exists |
| --- | --- | --- | --- |
| Product/architecture checkpoint | planner + architect | high | 把 M0-M6 拆成不互相越界的执行批次，并确认控制面/Agent/任务事件契约不漂移 |
| Backend schema/API | executor + test-engineer | medium/high | 落地 capability、task、event、approval、agent diagnostics 等 API 和迁移 |
| Runner/agent/safety | executor + critic + verifier | high | command runner、AgentClient、PathPolicy、TerminalSession、risk classifier 属于安全关键路径 |
| Frontend task/diagnosis UI | designer + executor | medium | 用 Ant Design / Ant Design X 展示任务、事件流、审批和报告，不重造 UI 系统 |
| Verification and release evidence | test-engineer + verifier | high | 负责 stop gate、devbox 验证、浏览器证据和高危阻断证明 |

## Execution Guidance

- 默认后续使用 `$ultragoal`：按里程碑顺序推进，适合底座到能力域的持续交付。
- 如果并行开发 Milestone 0-2，可用 `$team`：
  - 后端模型/API lane。
  - command runner/agent lane。
  - 前端任务/事件 UI lane。
  - 测试与验证 lane。
- `$ralph` 仅作为明确选择的单负责人持久验证 fallback。

## Execution Handoff Table

| Milestone | Owner lanes | Input files | Output evidence | Stop gate |
| --- | --- | --- | --- | --- |
| M0 Architecture Contracts | architect, backend executor, test-engineer | `backend/app/models/schemas.py`, `backend/app/storage/db.py`, `backend/app/tools/registry.py`, existing Phase 1 PRD/test spec | capability/task/event/agent contract draft, migration notes, API compatibility notes, contract tests | 旧 `/api/tools`、诊断、审批、审计兼容测试未过，不得进入 M1 |
| M1 Task/Event Foundation | backend executor, frontend executor/designer, test-engineer | `backend/app/storage/db.py`, `backend/app/main.py`, `frontend/src/main.tsx`, `frontend/src/types/api.ts` | `ops_tasks`、`ops_subtasks`、`ops_events` API, task detail UI, mock long-running task event stream | task lifecycle、SSE、audit linkage、PathPolicy/TerminalSession/risk classifier contract test 未过，不得进入 M2 |
| M2 Command Runner And Local Agent | runner/agent executor, backend executor, verifier | `backend/app/runner/command.py`, `backend/app/tools/registry.py`, future `backend/app/agents/` | AgentClient interface, command streaming/cancel/redaction, runner diagnostics, terminal read-only audit prototype | 任意 shell rejection、timeout/cancel/redaction、所有现有工具经 AgentClient 的测试未过，不得进入 M3 |
| M3 Host/Systemd | backend executor, frontend executor, test-engineer | capability registry, command runner, task/event APIs | host/systemd adapters, restart/reload approval task, security read-only inspection | 巡检 8 个证据点、systemd restart 审批任务、unauthenticated/CSRF safety 未过，不得进入 M4 |
| M4 Docker/Nginx/Site | backend executor, runner/agent executor, verifier | Docker/Nginx adapters, task/event foundation | Docker inspect/logs/restart loop, Nginx 502 diagnosis, config diff/test/reload/rollback | Docker 异常和 Nginx 502 的诊断/审批/执行/复核闭环未过，不得进入 M5 |
| M5 Files/Terminal/Database | backend executor, frontend executor/designer, test-engineer, critic | PathPolicy, TerminalSession, command risk classifier, backup artifacts | 文件 diff/backup/write, terminal audit UI, DB connection/backup/restore approval | 文件写不变量、危险终端命令阻断、DB restore 二次审批和恢复点确认未过，不得进入 M6 |
| M6 Security And Multi-Node Prep | architect, backend executor, security critic, verifier | firewall/SSH adapters, AgentClient interface, diagnostics | SSH/firewall preflight/rollback, local socket prototype, remote agent API contract | 连通性保护、自动回滚、heartbeat loss simulation、remote agent contract 未过，不得进入多节点实现 |

## Goal-Mode Follow-up Suggestions

- `$ultragoal`：默认后续路径。适合按 M0 -> M6 顺序建立 durable ledger，每个里程碑以 stop gate 作为 checkpoint。
- `$team` + `$ultragoal`：适合 M0-M2 并行推进。Team 负责并行产出 backend schema/API、runner/agent、frontend task UI、test/security 证据；Ultragoal 负责主线里程碑和验收归档。
- `$performance-goal`：仅在后续出现明确性能目标时使用，例如事件流吞吐、命令输出延迟或大日志处理性能。
- `$autoresearch-goal`：本计划不是研究项目，默认不使用；若后续要做独立竞品/安全规范研究，再单独启用。
- `$ralph`：仅当用户明确选择单负责人持续推进和验证时作为 fallback，不作为默认执行路径。

## Team Launch Hints

```text
$ultragoal "按照 .omx/plans/prd-aiops-capability-roadmap-20260709T102123Z.md 和 test-spec-aiops-capability-roadmap-20260709T102123Z.md，从 M0 开始实现 AI 运维平台能力底座，每个里程碑按 stop gate 验证"
```

```text
$team "并行推进 .omx/plans/prd-aiops-capability-roadmap-20260709T102123Z.md 的 M0-M2：lane1 后端 schema/API，lane2 command runner/local agent，lane3 task/event UI，lane4 test/security verification。所有 lane 必须返回 stop gate 证据"
```

## Team Verification Path

每个里程碑完成必须提供：

- 本地 `./scripts/smoke.sh`。
- 后端 pytest 覆盖新增 API 和安全边界。
- 前端 TypeScript 检查。
- 涉及 UI 的浏览器验证。
- 涉及文档/规划/代码变更后同步开发机。
- 开发机侧 smoke 和核心功能验证。

## Applied Consensus Improvements

- 根据 Architect 审查，明确 Milestone 0-2 必须先做稳，且底层契约保持 transport-agnostic。
- 根据 Critic 第一次审查，补充 pre-mortem、高危域不可绕过不变量、Files/Terminal 安全底座提前说明，以及按里程碑拆分的 execution handoff table。
- 根据 Critic 最终审查，执行时还必须补充四个非阻塞但应纳入里程碑拆解的约束：
  - M0/M1 需要具体化“禁止 adapter 绕过 AgentClient”的静态或 contract 检查方式，例如测试扫描注册表或强制 adapter interface。
  - M5 前必须补 `BackupArtifact` 和 secret storage 的最小字段清单，避免数据库备份恢复实现时临时发散。
  - M6 前必须确认真实 Linux 破坏性测试环境；devbox 只能承担非破坏验证，除非另行确认可回滚。
  - `GET /api/tools` 与新增 `GET /api/capabilities` 必须有兼容策略，迁移期间语义不得漂移。
