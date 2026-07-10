# PRD / Implementation Plan: Agent 运维平台生产 GA

> 状态：Deliberate consensus approved
> 规划日期：2026-07-10
> 目标：从当前可演示 Phase 1 / M0-M6 样机推进到可长期投入使用的单节点生产 GA，并保留多节点和生态扩展路径。
> 执行约束：本计划只定义实施与验收，不在规划阶段修改业务源码。

## 1. Requirements Summary

### 1.1 上线定义

本计划把“彻底上线投入使用”定义为：

1. 一个中小团队可以在 Ubuntu 22.04/24.04 x86_64 的单台 Linux 服务器上安装并持续运行本产品。
2. Web UI / AI 诊断台只负责规划、展示、审批和报告；控制面进程不以 root 运行，也不能直接执行系统命令。
3. 所有主机、systemd、Docker、Nginx、数据库、文件、终端和安全动作均由独立 Node Agent 通过 typed capability 执行。
4. 所有耗时、变更或高危操作必须进入持久化任务系统，支持实时事件、取消、重试、超时、失败恢复和审计回链。
5. mutating/destructive 动作必须先展示目标、参数、影响、diff/dry-run、审批要求，以及 capability 真实支持的 rollback/compensation 或人工恢复方案；未审批不得执行。
6. 具备生产部署、TLS、密钥管理、RBAC、备份恢复、监控告警、版本升级、回滚和用户支持手册。
7. 在专用可破坏 Linux 环境完成真实 systemd、Docker、Nginx、文件、数据库恢复和防火墙回滚演练后，才允许进入正式 GA。

当前产品定位继续保持 local-first、单节点优先。多节点、应用商店、MCP/插件市场、GPU/本地模型不阻塞首个 GA，但架构不得阻断后续实现。该边界符合 `AGENTS.md:32-37` 的能力优先级和 `demo/底层能力研究报告.md:261-286` 的三层优先级。

### 1.2 GA 必须包含

- 生产级本地 Agent：Unix socket、最小权限、capability allowlist、版本/心跳/诊断。
- 控制面、worker、Agent 三进程边界。
- Capability Registry、Task、Subtask、Event、Approval、Audit、Report 的一致数据模型。
- 真实异步任务和实时 SSE；运行中任务可取消，服务重启后能恢复或明确终止。
- 真实主机巡检、systemd、Docker/Compose、Nginx/Site/SSL、文件、终端、MySQL/PostgreSQL/Redis、安全/防火墙能力。
- AI 诊断计划、typed tool 调用、人工审批、执行验证、报告回链。
- Ant Design 后台和 Ant Design X 诊断台；完整专业工具区。
- 生产构建、安装/升级/卸载、备份恢复、可观测性、CI/CD 和发布回滚。
- 管理员、操作员、审批人、只读审计员四类角色。

### 1.3 GA 明确不包含

- 大规模集群调度或 Kubernetes AIOps。
- 宝塔式全品类插件和运行时菜单。
- 不透明二进制插件加载。
- 默认自动执行 AI 生成的高危命令。
- 公网远程 Agent 多节点调度；该能力进入 GA+1。

### 1.4 GA Support Matrix

R0 必须生成 `docs/support-matrix.md` 并冻结精确 patch 版本。首个 GA 只承诺以下范围，超出范围显示 unsupported 而不是“尝试执行”：

- 操作系统：Ubuntu Server 22.04 LTS、24.04 LTS，x86_64，本地 ext4/xfs；SQLite 数据目录禁止 NFS/SMB。
- init/firewall：systemd + UFW；firewalld/iptables 兼容放到 GA+1。
- Web：Ubuntu 发行版 Nginx；ACME 只承诺 HTTP-01；DNS-01 放到 GA+1。
- Docker：R0 冻结时 Docker Engine 当前稳定 release line 和前一个稳定 release line；Compose v2。
- 数据库：MySQL 8.4 LTS、PostgreSQL 15/16、Redis 7；MySQL 8.0.46 仅作为存量迁移兼容 fixture，不作为新 GA 安装支持。每个正式支持版本必须在 destructive lab 有真实 backup/restore fixture。
- 浏览器：Chrome/Edge/Firefox 最近两个 major，Safari 当前 major；移动端只承诺查看和审批。
- AI：R0 必须 live probe 并冻结至少一个 DeepSeek/OpenAI-compatible provider + model + API contract；记录数据地域、retention、training-use 条款和超时/配额。mock 只用于 CI，不算 GA AI 能力。
- 单节点：一个 Control Plane、一个 Worker leader、一个本地 Agent；无 HA，宿主机关机、硬件损坏和外部网络故障不计入应用 99.5% SLO。

### 1.5 Threat Model And Trust Boundaries

- 假设浏览器、LLM 输出、服务器日志、文件内容、容器名称和任务参数均不可信。
- Control Plane 是 trusted authorization root：承担登录、审批 CAS、grant 签发和策略决策。execution grant 防护 Worker/DB/transport 旁路，不承诺在 Control Plane 完整 RCE 后阻止伪授权。
- Control Plane 完整 compromise 的控制措施是最小 attack surface、signing key systemd credential、外部审计 checkpoint、异常 grant 告警、紧急 key revoke/rotation 和全局 mutation kill switch。
- Worker 被攻破时仍需一次性 execution grant、resource fencing、Agent schema/allowlist 才能执行变更。
- Agent 是高权限边界。首个 GA 选择“小型 root Agent + 极窄 typed RPC + systemd sandbox”，而不是用宽泛 sudoers 伪装成低权限。
- Docker socket 等价 root；只允许 Agent 访问，Control Plane/Worker 不挂载 Docker socket。
- 管理面反代配置、监听端口、当前来源 IP 和 Agent rollback timer 是受保护资源，业务 capability 不得删除或覆盖。
- 许可证与来源是发布 gate：记录参考项目版本、借鉴点和独立实现证据，不复制宝塔业务代码；直接改造 1Panel 前另做 GPLv3 评估。

## 2. Current Baseline And Gap

| 当前证据 | 已有基础 | 生产缺口 |
| --- | --- | --- |
| `.omx/ultragoal/goals.json:9-93` | M0-M6 状态均为 complete | stop gate 大量验证的是合同和 placeholder，不等同真实执行 |
| `backend/app/runner/agent.py:71-83` | 有 `AgentClient` 和 argv runner | 默认仍为 `in_process`，控制面和执行面未分离 |
| `backend/app/main.py:250-354` | 有 task/event API | 任务在 HTTP 请求中同步执行，SSE 只回放已有事件 |
| `backend/app/tools/registry.py:289-297` | systemd 有审批合同 | restart/reload 返回 placeholder success |
| `backend/app/tools/registry.py:411-488` | DB/firewall/Docker 有风险模型 | restore/apply/restart 仍是假执行 |
| `backend/app/auth/security.py:18-30` | 有签名 session、CSRF、origin | 有开发默认 secret，默认跳过首次改密 |
| `backend/app/auth/security.py:50-68` | Cookie 为 HttpOnly/SameSite | `secure=False`，缺生产 profile、MFA、登录限速 |
| `backend/app/storage/db.py:57-164` | SQLite 已有核心表 | 无版本迁移、外键、WAL、任务 lease、审计完整关联 |
| `frontend/src/main.tsx:468-474` | 有概览、诊断、任务、报告 | 没有专业工具区和生产路由结构 |
| `frontend/src/api/client.ts:82-135` | 已读取 SSE 格式 | 等待 `response.text()` 完成，不是实时流 |
| `frontend/Dockerfile:1-9` | 有前端容器骨架 | 运行 Vite dev server，不是生产静态构建 |
| `backend/Dockerfile:1-9` | 有后端镜像骨架 | 容器内绑定 127.0.0.1，未使用非 root 用户 |
| `frontend/vite.config.ts:6-13` | 本地开发代理可用 | 容器内代理错误指向自身 127.0.0.1 |
| `.omx/specs/m6-security-multinode-prep.md:12-24` | 已明确破坏性测试边界 | 尚无真实防火墙失联与自动回滚验证 |

## 3. RALPLAN-DR Summary

### 3.1 Principles

1. **安全不变量先于功能覆盖**：任何真实变更能力在审批、回滚、审计和破坏性实验通过前保持 disabled。
2. **控制面不持有 root**：所有系统能力必须越过 Agent transport 和 capability policy 边界。
3. **真实证据优先于“接口成功”**：完成标准必须包含目标系统状态变化与执行后复核，禁止 placeholder success。
4. **先单节点闭环，再多节点扩展**：先交付稳定 local-first GA，再增加远程 Agent 和生态。
5. **AI 是规划与解释层，不是越权执行器**：AI 只能选择已注册工具并创建建议/审批任务。

### 3.2 Decision Drivers

1. 高危运维动作的安全性、可回滚性和责任可追溯性。
2. 复用现有 FastAPI、React、SQLite、Ant Design X 成果，控制重写风险和交付周期。
3. 可安装、可升级、可监控、可恢复的长期运维成本。

### 3.3 Viable Options

#### Option A: 保留现有技术栈，拆出独立 Agent 与 Worker（选择）

做法：保留 FastAPI/React/SQLite，对 `main.py`、runner、registry 和 storage 做边界拆分；新增独立 Agent systemd 服务、Unix socket transport 和 DB-backed worker。

优点：最大化复用当前 API、测试和 UI；单节点部署简单；6 人团队主路径 P50/P90 为 33/44 周，增加集成与供应链缓冲后的对外承诺区间为 36-49 周。
缺点：需要谨慎治理 SQLite 并发和 Python 进程边界；后续多节点需要再引入 mTLS transport。

#### Option B: 用 Go 重写 Control Plane、Worker、Agent 和 task runtime

做法：只保留 React UI 和外部 API 语义，Control Plane、Worker、Agent、task/event/approval runtime 全部用 Go 重写。

优点：统一语言覆盖执行、并发、静态二进制和长期 Agent 分发，避免 Python/Go 双栈。
缺点：需要重做当前 44 条 API、审批/任务/AI/存储实现；短期验证面和回归风险显著放大。

#### Option C: 先发布只读诊断产品，长期关闭真实变更

做法：只开放主机/Docker/Nginx/安全只读检查，审批只生成工单，不执行修复。

优点：3-5 周可形成低风险试点，安全面最小。
缺点：不能兑现“诊断、审批、执行、验证、报告”的 AI 运维闭环，不满足本次“彻底上线投入使用”。

#### Option D: FastAPI 控制面 + Go 静态 Agent

做法：保留现有 FastAPI Control Plane、Worker、React、API 和数据模型，只将高权限宿主 Agent 实现为 Go 静态二进制。

优点：不重写 Web/审批/AI；Agent 分发、systemd 管理、进程组/cgroup、协议版本和依赖供应链更可控。
缺点：需要维护 Python/Go 双语言合同和构建链；团队需承担 Go Agent 测试与发布能力。

R1 前 3-5 天必须做 Python Agent 与 Go Agent spike，使用相同 UDS protocol 测量安装体积、冷启动、升级、CVE 面、stream/cancel/cgroup、sudo/root boundary 和开发成本。若 Go 明显降低高权限边界风险且不使 R1 增加超过 2 周，选择 Option D；否则保留 Python Agent。Control Plane/Worker 决策不受 spike 影响。

### 3.4 Decision

选择 Option A 作为当前基线，并吸收 Option C 的发布策略；R1 保留基于 spike 切换到 Option D 的明确 gate。所有真实变更能力按域逐个解锁；未通过真实场景 gate 的 capability 保持 disabled，而不是用 placeholder 返回成功。

## 4. Target Architecture

```text
Browser
  -> TLS Reverse Proxy / Static Frontend
      -> FastAPI Control Plane (non-root)
          -> SQLite WAL / versioned migrations
          -> Approval / Audit / Report / SSE
          -> DB-backed Task Queue
              -> Worker (non-root, sole execution consumer)
                  -> Unix Domain Socket
                      -> Node Agent systemd service
                      -> Capability Registry
                      -> Command Runner / SDK wrappers
                      -> Host / systemd / Docker / Nginx / SSL / DB / Files / Terminal / Security
```

### 4.1 Process Boundaries

| Process | 权限 | 职责 | 禁止事项 |
| --- | --- | --- | --- |
| Web / reverse proxy | 非 root 或仅绑定 80/443 的受控权限 | TLS、静态文件、反代、安全响应头 | 不访问 Agent socket、不访问系统资源 |
| Control Plane | 非 root、trusted authorization root | Auth、RBAC、AI 规划、审批 CAS、grant 签发、API、审计、报告 | 不挂载 Agent socket，不调用 subprocess，不执行 root 命令 |
| Worker | 非 root；primary/effective GID 必须为 `aiops-exec` | 领取持久任务、驱动 Agent、写事件、协调取消/复核 | 不持有 signing key，不直接访问 Docker socket/host files，不绕过 AgentClient |
| Node Agent | 首个 GA 为小型 root systemd 服务，强 sandbox | 系统事实采集和受控执行 | 无网络 listener、无动态插件、无通用 shell RPC、不自行做业务审批 |

UDS 运行约束：

- `/run/aiops` owner 为 `aiops-agent:aiops-exec`、mode 0750；socket mode 0660。
- Worker 的 primary/effective GID 固定为 `aiops-exec`；不能只依赖 supplementary group。Control Plane 容器不挂载 socket。
- Agent 使用 `SO_PEERCRED` 校验 peer UID/GID，并校验 protocol version。
- 每个 mutating 请求必须携带 Control Plane 使用独立 execution-signing key 签发的一次性 grant，绑定 task、capability、resource、params hash、approval、expiry、fencing token 和 `key_id`。Worker 只持有已签发 grant，不持有签发密钥；rotation 允许短时 N/N-1 key overlap。
- Agent 持久化 nonce/replay cache 与 execution journal；不能只检查请求“有 nonce”。
- Agent/Worker/Control Plane 支持 N/N-1 protocol compatibility，并按 Agent -> Worker -> Control Plane 的兼容顺序升级；回滚反向执行。

Privilege ADR：首个 GA 接受 Agent 是高权限 root boundary，因为 Docker socket 本身 root-equivalent，且 systemd、受控文件与防火墙能力需要跨域权限。安全性依靠无公网监听、UDS peer check、一次性 grant、typed allowlist、systemd sandbox、资源保护和破坏性 lab；禁止 `systemctl *`、`docker *` 等带用户可控尾参数的宽泛 sudoers。

### 4.2 Data Strategy

- GA 继续使用 SQLite，开启 WAL、foreign_keys、busy_timeout，并限制 worker 并发；原因是 local-first 单节点部署和当前数据规模。
- 引入 `schema_migrations` 和顺序 SQL migration，不再依赖启动时无版本 `CREATE TABLE IF NOT EXISTS`；采用 forward-only expand/contract 和 N/N-1 应用兼容，禁止把自动 down migration 当作普通回滚。
- 所有 mutating task 必须具有 `idempotency_key`、`lease_owner`、`lease_expires_at`、`attempt`、`cancel_token`、`approval_id`、`rollback_task_id`、`resource_lock_key`、`fencing_token`。
- 新增 `resource_locks`；同一主机服务、容器、Nginx 配置、文件、数据库或防火墙策略同一时刻只允许一个 mutating task。Agent 拒绝旧 fencing token。
- task claim 必须原子化；`(scope, scope_id, sequence)` 建唯一约束；mutating task 默认不自动 retry，只能在 operation-specific reconciliation 证明未产生效果后重试。
- 调度语义是 at-least-once + reconciliation，不承诺通用 exactly-once。Agent 崩溃或 Worker lease 过期后，先根据 execution journal 和目标状态复核，再进入 canonical `succeeded`、`failed`、`effect_unknown` 或 `manual_recovery_required`。
- 取消相关状态严格使用 §4.3 canonical 词表，不新增近义状态。
- 审计记录必须包含 `actor_id`、`auth_session_id`、`capability_id`、`task_id`、`event_id`、`resource`、`params_hash`、`result_hash` 和时间戳。
- GA 后当多节点并发或 HA 需求出现时，才迁移 PostgreSQL；本次不提前引入数据库集群。

### 4.3 Canonical Task State Machine

唯一状态词表如下；`interrupted`、`verification_failed`、`failed_unverified` 不作为状态，分别映射到 `failed`、`effect_unknown` 或 `manual_recovery_required`，具体由 effect certainty 决定。

| State | 允许前驱 | 允许后继 | Scheduler terminal | Effect certainty | Lock/lease | Retry |
| --- | --- | --- | --- | --- | --- | --- |
| `created` | 无 | `waiting_approval`,`queued`,`failed`,`cancelled_before_effect` | 否 | no effect | 未持有 | read-only 可入队；mutation 等审批 |
| `waiting_approval` | `created` | `queued`,`failed`,`cancelled_before_effect` | 否 | no effect | 未持有 | 不重试，等审批/过期 |
| `queued` | `created`,`waiting_approval` | `running`,`cancelled_before_effect`,`failed` | 否 | no effect | claim 时取 lease/lock/fencing | read-only 可限次；mutation 仅 pre-effect |
| `running` | `queued` | `verifying`,`cancel_requested`,`rollback_running`,`failed`,`effect_unknown`,`manual_recovery_required` | 否 | possible effect | 持有 | mutation 不自动重试 |
| `cancel_requested` | `running` | `cancelled_before_effect`,`verifying`,`completed_after_cancel`,`effect_unknown`,`manual_recovery_required` | 否 | unknown until reconcile | 持有 | 不重试 |
| `verifying` | `running`,`cancel_requested` | `succeeded`,`failed`,`rollback_running`,`completed_after_cancel`,`effect_unknown`,`manual_recovery_required` | 否 | being established | 持有 | 不重试 |
| `rollback_running` | `running`,`verifying` | `rolled_back`,`effect_unknown`,`manual_recovery_required` | 否 | compensating | 持有 | 仅 capability policy 允许 |
| `succeeded` | `verifying` | 无 | 是 | effect verified | 释放 | 无 |
| `failed` | `created`,`waiting_approval`,`queued`,`running`,`verifying` | 无 | 是 | no effect 或 failure verified | 释放 | read-only/明确 pre-effect 可新 attempt |
| `cancelled_before_effect` | `created`,`waiting_approval`,`queued`,`cancel_requested` | 无 | 是 | no effect | 释放 | 无 |
| `completed_after_cancel` | `cancel_requested`,`verifying` | 无 | 是 | effect verified after cancel | 释放 | 无 |
| `rolled_back` | `rollback_running` | 无 | 是 | compensation verified | 释放 | 无 |
| `effect_unknown` | `running`,`cancel_requested`,`verifying`,`rollback_running` | 无 | 是（自动调度） | unknown | 保留 safety lock，人工确认后释放 | 禁止自动重试 |
| `manual_recovery_required` | `running`,`cancel_requested`,`verifying`,`rollback_running` | 无 | 是（自动调度） | known unresolved effect | 保留 safety lock，runbook 完成后释放 | 禁止自动重试 |

read-only task 可由 `created -> queued` 绕过 approval；mutating/destructive 必须经 `waiting_approval`。资源锁只能在 terminal 且 effect certainty 足以安全释放时释放；unknown/manual 状态由人工 resolution 事件释放。

`running -> failed` 和 `verifying -> failed` 只允许在 guard `effect_proven_none_or_failure_verified` 成立时发生。只要已经出现部分副作用或无法证明无副作用，就必须继续进入 `verifying`，或落入 `effect_unknown` / `manual_recovery_required`，不得直接以 `failed` 释放安全锁。

前端“请求取消”只 POST cancel 并继续订阅直到 canonical terminal state；“停止查看”才 Abort/关闭本地事件流，且不改变任务状态。

### 4.4 Artifact Store

- stdout/stderr 大输出、diff、backup、数据库 dump、证书旧版本和终端录像不写入 SQLite payload；SQLite 只保存 metadata/ref/hash/size/owner/task/retention。
- 本地 artifact root 为 `/var/lib/aiops/artifacts`，mode 0700，配额、保留期和磁盘水位受监控；敏感 artifact 加密。
- 写入采用临时文件 + fsync + 原子 rename，生成 SHA-256；读取按 actor/resource policy 授权。
- 平台 DB、审计 checkpoint 和关键 artifact 必须复制到异机或对象存储；RPO 15 分钟包含宿主永久丢失场景时，不能只保留同机副本。
- restore 前校验 checksum、版本、目标和可读性；恢复演练记录进入审计与报告。

### 4.5 AI And Untrusted Evidence Governance

- 工具输出、日志和文件内容视为不可信数据，放入明确 data boundary，不能覆盖 system/tool policy。
- 在送入 LLM 前做分块 secret redaction、长度限制和 prompt-injection marker；模型不能生成 execution grant。
- 记录 provider、model/version、prompt policy version、tool schema version、token/cost、timeout 和 request id，但不保存 secret。
- 供应商不可用、超时或配额耗尽时，任务降级为 deterministic read-only 检查和“结论不完整”，不得自动放宽工具权限。

### 4.6 Key Hierarchy, Escrow And Recovery

| Key | 用途 | 是否异地 escrow | 丢失/轮换语义 |
| --- | --- | --- | --- |
| Session secret | Web session 签名 | 否 | 可重新生成，所有 session 失效 |
| Execution signing key | grant 签名 | 私钥不进普通 backup；离线加密 recovery copy 或外部 KMS | revoke 后 Agent 拒绝旧 `key_id`；rotation 有短 overlap |
| Secret KEK | 包装 secret DEK | 必须 | 双人恢复；无 KEK 不得宣称 secret 可恢复 |
| Artifact KEK | 包装每个敏感 artifact DEK | 必须 | DEK 随 artifact metadata 备份但保持 wrapped |
| Audit checkpoint key | 外部 checkpoint 签名 | 验证公钥长期保留；私钥由外部 sink/KMS 托管 | rotation 保留历史 public key chain |

- Recovery bundle 只含加密/封装后的 key material、key ids、KDF/algorithm metadata 和恢复说明，不含明文私钥/KEK。
- Escrow 位于与被管宿主不同故障域，访问需要两个不同恢复身份；每次读取和恢复均写外部审计。
- 原宿主磁盘完全不可用时，必须在 clean replacement VM 完成双人 key recovery、DB、secret 和 critical encrypted artifact restore；整个流程计入 RTO <= 60 分钟。
- execution key compromise 触发 mutation kill switch、key revoke、Agent denylist 更新、未消费 grant 作废、外部 checkpoint 复核和安全事件流程。

## 5. Release Milestones

### 5.1 Release Dependency And Duration

| Release | Predecessor | 可并行 lane | 核心人员 | P50 | P90 | 是否可真实 mutation |
| --- | --- | --- | --- | ---: | ---: | --- |
| R0 Production Baseline | 当前 baseline | 无 | lead、security、DevOps | 2 周 | 3 周 | 否 |
| R1 Agent Boundary | R0 | R6A 安装骨架 | architect、2 Agent/backend、DevOps | 4 周 | 5 周 | destructive lab only |
| R2 Durable Runtime | R1 | R5A 前端 session/SSE shell、R6A observability skeleton | 2 backend、QA | 4 周 | 5 周 | destructive lab only |
| R3 Security/Data | R2 | R5A、R6A | backend、security、DevOps | 4 周 | 5 周 | controlled test |
| R4A-G Domain Gates | R3 | 最多 3 个领域 lane + 对应 UI/E2E | 3 backend/Agent、frontend、QA/security | 10 周 | 14 周 | per-gate lab enablement |
| R5 Product UI Platform | R2；领域页依赖对应 R4 API contract | R3/R4/R6A | frontend、designer、QA | 4 周 | 6 周 | controlled pilot |
| R6A Ops Foundation | R0 开始，最终 hardening 依赖 R4/R5 | R1-R5 | DevOps、backend、QA | 8 人周工作量 | 11 人周工作量 | controlled pilot；并行投入，不直接增加关键路径日历周 |
| R6B Release Hardening | R4 + R5 + R6A | 文档、安全复核 | DevOps、verifier、security | 2 周 | 3 周 | RC only |
| R7 Pilot / RC / GA | R6B | 无；阶段严格顺序 | 全队、support、verifier | 7 周 | 9 周 | production after GO |

### 5.2 Critical Path And Calendar Estimate

关键路径为 `R0(2/3) -> R1(4/5) -> R2(4/5) -> R3(4/5) -> R4(10/14) -> R6B(2/3) -> R7(7/9)`；R5 和 R6A 在主路径旁并行，但其 gate 必须在 R6B 前完成。

- 6 人推荐团队（lead 1、backend/Agent 3、frontend 1、QA/security/DevOps 1）：P50 33 周，P90 44 周。
- 增加 10% 集成/供应链缓冲后，对外承诺区间为 **36-49 个日历周**。
- 4-5 人团队因 R4 最多只能维持 2 个领域 lane，预计 42-55 周。
- 单人顺序实施预计 12-18 个月。
- 若 R1 选择 Go Agent，spike 已含在 R1；若 Go 实现使 R1/R4 增加超过 2/4 周，必须更新 critical path 并重新审批排期。

任何 P0 gate 未通过，后续时间表自动顺延，不以日期覆盖安全门禁。

## 6. Implementation Steps

### Step 0 — 冻结生产范围与建立可审计基线（R0，P50 2 周 / P90 3 周）

实施：

- 将当前目录纳入正式 Git 版本控制，定义 `main`、release branch、tag 和 Lore commit protocol。
- 固化 §1.4 支持矩阵、§1.5 威胁模型、管理面受保护资源和 production SLO 排除项。
- 保留 `docker-compose.yml` 作为开发配置；新增 `deploy/compose.prod.yml`、`.dockerignore`、`.env.production.example`。
- 增加 dev/test/prod 配置对象；prod 缺少 session secret、trusted origin、TLS 设置时必须启动失败。首次未初始化实例必须使用短 TTL setup enrollment；已初始化实例如果仍配置 bootstrap password，启动失败并产生 P0 配置错误。
- 增加 `/health/live`、`/health/ready`、`/version`，与需要登录的 `/api/health-snapshot` 分离。
- 所有 registry 中的 placeholder mutating capability 统一标记 `enabled=false`，且 API 不得返回 success。
- 建立 `docs/architecture/`、`docs/runbooks/`、`docs/security/` 的生产文档真源。
- 建立 `docs/provenance.md`，记录 1Panel/宝塔参考版本、借鉴点、独立实现说明与许可证边界。
- 冻结一个 live DeepSeek/OpenAI-compatible provider/model/API contract 及数据地域/retention/training-use 条款；mock 只保留为 CI fixture。
- 锁定 pytest、Vitest/RTL、Playwright 和负载工具版本，建立 Test ID manifest/evidence 目录。

主要文件：

- 修改：`backend/app/auth/security.py`、`backend/app/main.py`、`backend/app/tools/registry.py`。
- 新增：`backend/app/config.py`、`deploy/compose.prod.yml`、`.dockerignore`、`.env.production.example`。

Stop gate：

- prod profile 使用任何默认 secret 时启动失败。
- 支持矩阵和威胁模型由 architect/security reviewer 签字；超出矩阵的环境明确返回 unsupported。
- 18 个 capability 中所有 placeholder 不可执行且返回结构化 `disabled/not_implemented`，不得写 succeeded task。
- `docker compose -f deploy/compose.prod.yml config` 通过。
- CI 可执行后端测试、前端类型检查、生产构建和依赖漏洞检查。
- live provider contract probe 通过；不满足数据边界或 tool-calling contract 时不得宣称 GA AI 能力。

### Step 1 — 决策并拆出真正的 Node Agent（R1，P50 4 周 / P90 5 周）

实施：

- 先完成 3-5 天 Python-Agent / Go-Agent spike，按 Option D gate 形成独立 ADR，再冻结 Agent 语言。
- 将 `AgentClient` 接口与 `LocalAgentClient` 实现分离；Control Plane 不保留执行客户端，只有 Worker 持有。
- 新建独立 `agent/` 工程和入口，支持 Unix socket typed HTTP/RPC、protocol version 与 N/N-1 compatibility。
- Agent socket 按 §4.1 的 owner/group/mode 配置；请求包含一次性 execution grant、task/capability/resource/params hash/approval/expiry/fencing token。
- Agent 自己维护 capability registry；Worker 从 Agent 同步 capability/version/health 到数据库，Control Plane 只读取数据库/API projection，不连接 UDS。
- 建立 root Agent systemd unit、`ProtectSystem`/`PrivateTmp`/`NoNewPrivileges` 等适用 sandbox；禁止公网 listener 和通用 shell endpoint。
- runner 增加 cwd allowlist、env allowlist、max output bytes、process group cancel、dry-run 和 structured exit metadata。
- 禁止控制面/worker 模块 import Agent adapter 或 `subprocess`；用静态 contract test 扫描违规引用。
- 在 R1 建立生产同构骨架：Control Plane/Worker 容器、宿主 Agent、只有 Worker mount UDS；R6 只做制品化和加固，不能首次验证边界。

主要文件：

- 拆分：`backend/app/runner/agent.py`、`backend/app/runner/command.py`、`backend/app/tools/registry.py`。
- 新增：`agent/app.py`、`agent/transport/uds.py`、`agent/capabilities/`、`agent/runner.py`、`deploy/systemd/aiops-agent.service`。
- 新增 Worker-side AgentClient：`backend/app/agents/client.py`、`backend/app/agents/service.py`；Control Plane 进程禁止 import/instantiate AgentClient。

Stop gate：

- Control Plane 和 Worker 以非 root 运行，测试证明其不能直接 spawn 命令。
- 100% capability 调用经 Unix socket Agent；Agent 停止时任务明确失败为 `agent_unavailable`。
- socket peer mismatch、伪造 grant、nonce replay、旧 fencing token、过期 deadline、未知 capability 和参数 schema 错误均被拒绝并审计。
- Agent restart 后重新上报 heartbeat 和 capability inventory。

### Step 2 — 建设持久异步任务、资源协调、事件和制品（R2，P50 4 周 / P90 5 周）

实施：

- 将 `main.py` 中同步执行逻辑迁到独立 Worker；HTTP 只创建 task/approval 或读取状态。
- 为 task 增加 lease、attempt、retry policy、idempotency key、cancel token、heartbeat、failure category、resource lock 和 fencing token。
- Worker 使用原子 claim；mutating 默认不自动 retry。过期 lease 进入 operation-specific reconciliation，再根据 Agent journal/目标状态决定恢复、完成、effect_unknown 或 manual recovery。
- 新增 Artifact Store，承载大输出、diff、backup、dump、证书旧版本和 recording；SQLite 只存 metadata/ref/hash。
- SSE 改为真正增量输出，支持 `Last-Event-ID`、心跳、断线续传和事件顺序保证。
- 将 diagnosis event 和 task event 收敛为统一 `ops_events` 语义；兼容旧 endpoint 直到前端切换完成。
- API cancel 连接 runner cancel token、process group/cgroup 终止和领域复核；按 §4.3 的细分取消状态记录最终效果。
- 审批 approve 只把 waiting task 放入可执行队列，不在审批 HTTP 请求内执行工具。
- 审批状态 CAS、immutable approval snapshot、grant 签名结果入库和 task queued 必须在一个原子 DB transaction 中提交；签名或 commit 失败时不能暴露部分 grant/queued task。

主要文件：

- 重构：`backend/app/main.py:250-354`、`backend/app/main.py:1022-1179`、`backend/app/storage/db.py:386-616`。
- 新增：`backend/app/tasks/repository.py`、`backend/app/tasks/worker.py`、`backend/app/tasks/state_machine.py`、`backend/app/events/stream.py`。

Stop gate：

- 30 秒任务在两个浏览器中均能持续看到事件，event visible latency p95 < 1 秒；Agent、Control Plane 与浏览器测试节点的 NTP 绝对偏差必须 < 50 ms，并把校时证据与原始时间戳纳入测试报告，否则延迟结论无效。
- 运行中取消在 5 秒内得到 Agent acknowledgement，并经领域复核进入 `cancelled_before_effect`、`completed_after_cancel`、`effect_unknown` 或 `manual_recovery_required`，不得泛化为 cancelled。
- Worker lease 过期但旧 Agent command 仍在运行时，新 Worker 因 resource lock/fencing 不得并发执行；reconciliation 给出明确效果状态。
- 重复提交同一 idempotency key 不直接触发第二次系统变更；对于无法证明 exactly-once 的领域，进入人工复核而不是自动 retry。
- SQLite/artifact 磁盘满、artifact checksum 错误和 event sequence 冲突均有结构化失败与告警。
- forged DB approval、未批准 task 或非 CAS 状态不能获得 grant；审批/grant/queued task 不存在部分提交。

### Step 3 — 安全、身份、审批和数据可靠性（R3，P50 4 周 / P90 5 周）

实施：

- 角色：Admin、Operator、Approver、Auditor；资源和动作级 authorization 在后端强制执行。
- prod Cookie 强制 Secure/HttpOnly/SameSite，登录后 session rotation；增加登录限速、失败锁定、登录审计和管理员恢复 CLI。首次安装使用短 TTL setup token/CLI enrollment，完成后销毁，不长期保存 bootstrap password 环境变量或明文文件。
- 对公网部署要求管理员/审批人 MFA；LAN-only 可由明确策略关闭，但必须产生配置风险提示。
- 审批时重新计算 actor/session/capability/resource/params fingerprint；nonce 一次性消费。
- destructive 动作采用两个不同身份的二人审批；客户端 boolean 不再视为第二审批。小团队 break-glass 需 MFA、step-up、理由、短 TTL、独立告警和事后复核。
- Secret value 使用主密钥加密后持久化；主密钥由 systemd credential/OS keyring 或等价机制托管，定义启动解锁、轮换、备份恢复和向 Agent 下发短 TTL scoped secret 的协议。
- 按 §4.6 实现 Secret/Artifact/Execution/Audit key hierarchy、异地 escrow、双人 clean-host recovery、revoke/rotation 和 mutation kill switch。
- 审计增加 task/event/approval 关联、params/result hash和 hash-chain；定期签名 checkpoint 推送到异地 append-only sink。若未配置外部 sink，只承诺“同机范围内篡改可检测”，不宣称不可篡改。
- SQLite 增加 versioned forward-only migration、WAL、foreign keys、indexes、online backup API、checksum 和 restore verification；应用回滚保持 N/N-1 schema 兼容，不自动执行 down migration。
- 实现 §4.5 的 LLM 数据治理、恶意日志/prompt injection 隔离、供应商降级和模型审计字段。

主要文件：

- 重构：`backend/app/auth/security.py`、`backend/app/storage/db.py`、`backend/app/models/schemas.py`、`backend/app/main.py:1050-1228`。
- 新增：`backend/app/auth/authorization.py`、`backend/app/auth/rate_limit.py`、`backend/app/approvals/service.py`、`backend/app/secrets/store.py`、`backend/migrations/`。

Stop gate：

- 未认证、错误角色、跨会话、过期、参数替换、重放审批全部被拒绝。
- destructive task 必须由两个不同 approver 批准；任一审批撤销或过期后不得执行。
- 数据库从空版本逐步升级到当前版本，N/N-1 应用都能读取 expand 阶段 schema；应用回滚不执行 down migration。
- 在线备份 + 异地副本恢复演练满足 RPO <= 15 分钟、RTO <= 60 分钟；checksum 和一致性检查通过。
- 日志、事件、报告和 LLM payload 中无 password/token/private key/DSN 明文。
- 使用 test spec seeded canary values 扫描所有 sink，精确命中为 0；不使用不可穷举的绝对“无 secret-like 数据”自证。
- setup token 过期/重放、MFA recovery、break-glass 和 scoped Agent secret 全部可审计。
- 冻结的 live provider/model 在 staging/canary 完成真实 contract、timeout/quota、prompt-injection、redaction、cost/audit E2E；mock 结果不计入 GA AI gate。

### Step 4 — 真实领域能力纵向闭环（R4，P50 10 周 / P90 14 周，最多 3 lane）

所有领域统一遵循：`inspect -> plan/diff -> approval -> task -> Agent execute -> verify -> rollback/compensate when supported -> otherwise manual recovery -> report`。每个 capability 必须声明 `supports_rollback`、`rollback_kind: automatic|compensating|manual|none`、`irreversible_effects` 和 `manual_recovery_runbook`。每个领域必须同时交付 Agent adapter、Control Plane API、专业工具 UI、E2E、observability 和 destructive-lab evidence；不得把 UI 统一拖到 R4 之后。

#### 4A Host / systemd

- 真实 service status/log/restart/reload；限定 service allowlist。
- restart 前采集状态，执行后验证 active 和关键端口；失败执行恢复或明确人工恢复。
- 禁止管理本产品自身的 Agent/Worker/Control Plane unit，除非进入独立 break-glass 流程。

#### 4B Docker / Compose

- Docker SDK typed adapter：containers/images/networks/volumes/Compose list/inspect/logs/stats。
- start/stop/restart/update 需要审批；delete/volume prune 默认关闭或二人审批。
- 变更后检查 health/status/log；失败按操作类型回滚。
- Docker socket 只对 root Agent 可见；容器名、compose path 和 label 均按不可信参数做 schema/资源校验。

#### 4C Nginx / Site / SSL

- 站点、域名、upstream、反代模型持久化。
- 所有配置变更先生成 diff 和 backup，执行 `nginx -t`，成功后 reload，失败自动恢复原文件并再次 test。
- ACME issue/renew/deploy 进入任务；证书部署前保存旧证书，失败恢复。
- 管理面 server block、监听端口、当前来源 IP 和反代配置列入 protected resources；业务站点 capability 不能覆盖。

#### 4D Files

- 文件 list/read/upload/download/diff/backup/write/chmod/archive；全部受 PathPolicy、大小限制和敏感路径规则约束。
- 文件打开使用 no-follow/openat 或等价安全方式，防 symlink/TOCTOU；写入采用临时文件、fsync、原子替换并保留 owner/mode，执行前检查磁盘空间。

#### 4E Terminal

- 常规 Terminal 只提供 typed/argv-only 受控命令；AI 只能填充建议，不自动提交。
- 完整 PTY/shell 作为默认 disabled 的 break-glass terminal：MFA、step-up approval、短 TTL、强制录制、独立告警，并明确不满足普通 typed/no-shell 不变量。
- 所有命令、输出、操作者和时间形成 session recording；ANSI/log injection 清洗，敏感输出在入库前脱敏。

#### 4F Database / Cache

- MySQL、PostgreSQL、Redis：连接检测、版本、状态、连接数、账号权限只读检查。
- 备份使用数据库原生一致性工具或协议；记录 checksum、target、version、size、encryption、restore point。
- restore/import 要求二人审批，在隔离库验证可读性后再执行；恢复后做连接和最小一致性检查。
- Redis GA 支持 INFO/health 与 RDB snapshot backup；RDB restore 需要停止/重启服务，默认为 disabled，只有在专用实例完成二人审批、版本匹配、备份当前状态和恢复复核后才可 enabled。

#### 4G Security / Firewall / SSH

- 读取 SSH、failed login、sudo log、listening ports、UFW 状态；firewalld/iptables 在 GA 只检测并返回 unsupported，真实兼容进入 GA+1。
- SSH/firewall 写入必须在专用可破坏 VM 中验证 preflight、当前连接保护、目标端口探测、定时自动回滚和 heartbeat confirmation。
- 只有真实回滚演练通过的规则类型才能在生产 enabled。
- 自动回滚由 Agent 创建并由 systemd timer/独立本地机制持有；即使 Control Plane 和 Worker 离线也必须执行。

#### 4A-G Release Gate Manifest

| Gate | Owner | 前置 | P50/P90 | Capability manifest | 必需 evidence | Enablement flag |
| --- | --- | --- | --- | --- | --- | --- |
| 4A Host/systemd | backend/Agent-1 | R3、service allowlist | 2/3 周 | host read、systemd status/log/restart/reload | API/UI/E2E、before/after、self-unit protection | `AIOPS_ENABLE_SYSTEMD_MUTATION` |
| 4B Docker/Compose | backend/Agent-2 | R3、Docker support matrix | 3/4 周 | container/image/network/volume/compose read；start/stop/restart | SDK contract、health/log verify、daemon failure | `AIOPS_ENABLE_DOCKER_MUTATION` |
| 4C Nginx/Site/SSL | backend/Agent-3 + frontend | R3、protected resources、artifact | 4/5 周 | site/proxy/diff/test/reload、ACME HTTP-01 | invalid config rollback、management protection、cert restore | `AIOPS_ENABLE_NGINX_MUTATION` |
| 4D Files | backend/Agent-1 + frontend | R3、PathPolicy、artifact | 3/4 周 | list/read/upload/download/diff/write/chmod/archive | TOCTOU/symlink、atomic write、stale diff、restore | `AIOPS_ENABLE_FILE_MUTATION` |
| 4E Terminal | backend/Agent-2 + security | R3、recording、MFA | 3/4 周 | typed argv；break-glass PTY separate | shell escape/ANSI recording、step-up、kill switch | `AIOPS_ENABLE_BREAK_GLASS_TERMINAL`（默认 0） |
| 4F DB/Cache | backend/Agent-3 + DB QA | R3、secret/artifact、isolated DB fixtures | 5/7 周 | MySQL/PG/Redis inspect/backup；approved restore | native backup、isolated verify、crash/manual recovery | per-engine restore flags |
| 4G UFW/SSH | backend/Agent-1 + security | 4A、OOB destructive VM、timer | 4/6 周 | SSH inspect、UFW read/propose/apply | worker/CP offline rollback、source/port protection | `AIOPS_ENABLE_UFW_MUTATION` |

每个 manifest 固定 capability/version、input/output schema、risk、approval policy、rollback_kind、manual runbook、resource lock key、observability fields 和对应 Test IDs。一个 gate 的 evidence 不得替另一个 gate 解锁 mutation。

Stop gate：

- 每个领域至少保存一条成功、一条失败和一条审批拒绝证据；支持 automatic/compensating rollback 的 capability 必须有真实回滚证据，不支持的必须展示不可逆影响并在失败时进入 `manual_recovery_required`，禁止伪 rollback。
- systemd、Docker restart、Nginx reload、文件写、DB restore、防火墙变更的最终报告都包含执行前后状态。
- registry 中不存在 `executed=placeholder`；未实现能力只能 disabled，不得返回 success。
- 真实破坏性测试必须在有 out-of-band console 的一次性 Linux VM 进行。
- R4 按 4A-G 七个独立 release gate 验收；前一 gate 未通过不阻塞其他只读开发，但对应 mutation flag 必须保持 disabled。

### Step 5 — 前端平台层与领域纵向交付（R5，P50 4 周 / P90 6 周，与 R2-R4 并行）

实施：

- 将 `frontend/src/main.tsx` 拆为 App shell、routes、features、shared components；引入路由和模块级错误边界。
- 启动时调用 `/api/me` 恢复会话；增加 logout、session expired、权限隐藏与 403 页面。
- 使用真实增量 SSE/stream client 和 AbortController；诊断、任务、命令输出可断线续传。
- 信息架构固定为：概览、AI 诊断台、任务/审批、服务器、容器、网站/SSL、数据库、文件、终端、安全、审计/报告、设置。
- 审批 Drawer/Modal 必须展示风险、目标、结构化参数、diff、影响范围、回滚、审批人要求、过期时间。
- AI 诊断继续使用 Ant Design X；事件链用 ThoughtChain，结构化结果使用 XCard，Markdown 使用 XMarkdown。
- 大表格服务端分页；移动端只提供安全查看和审批，危险终端/文件编辑默认桌面限定。
- R5 只先交付会话、路由、SSE、权限、统一审批和共享表格/表单/错误边界；各领域页面必须跟随 Step 4 对应 API contract 和 adapter 同一纵向 slice 完成。

主要文件：

- 重构：`frontend/src/main.tsx`、`frontend/src/api/client.ts`、`frontend/src/types/api.ts`。
- 新增：`frontend/src/app/`、`frontend/src/features/diagnosis/`、`tasks/`、`servers/`、`docker/`、`sites/`、`databases/`、`files/`、`terminal/`、`security/`、`audit/`。

Stop gate：

- 刷新页面保持登录；退出后敏感 API 立即 401。
- SSE 增量事件可见延迟 p95 < 1 秒；“请求取消”后继续订阅直到 canonical terminal state，“停止查看”才关闭本地流。
- 所有 GA capability 有入口、详情、风险说明和成功/失败反馈；disabled 能力不可误导为可执行。
- Chrome/Edge/Firefox 最近两个版本和 Safari 当前版本完成关键路径；桌面、平板和移动审批路径通过。
- axe 严重/高等级无障碍问题为 0；键盘可完成登录、诊断、审批和查看报告。

### Step 6 — 生产部署、可观测性和发布工程（R6A 8/11 人周并行工作量 + R6B 2/3 日历周）

实施：

- 前端多阶段构建，生产镜像只含静态文件；反代通过 service name 访问 backend，不使用容器内 127.0.0.1。
- Control Plane/Worker 容器使用非 root 用户、read-only filesystem、最小 mount、cap_drop 和资源限制。
- Agent 作为宿主机 root systemd 服务安装并强 sandbox；生产 compose 只有 Worker 挂载 Agent socket，Control Plane 不挂载；任何容器都不挂宿主 Docker socket或任意 root 文件系统。
- 提供 `install.sh`、`upgrade.sh`、`rollback.sh`、`uninstall.sh`、`backup.sh`、`restore.sh`，全部幂等并有 dry-run。
- 增加 structured JSON log、request/task/capability trace id、metrics、health/readiness、审计导出和告警。
- 默认支持 S3-compatible off-host backup/audit checkpoint 和 generic webhook external alert；GA 安装必须至少配置一个异地 target 和一个平台外 alert target。
- CI/CD：lint、typecheck、unit、integration、E2E、image build、SBOM、dependency/container scan、签名 release artifact。
- 版本升级前自动备份；应用按 Agent -> Worker -> Control Plane 顺序升级并验证 N/N-1 protocol。健康检查失败回滚应用版本，但数据库保持 forward-only schema；只有灾备流程可以恢复验证过的 DB snapshot。
- 定义外部 heartbeat/alert target，避免平台自身宕机时无法发出告警。
- 安装器验证 release signature、digest、version floor 和 downgrade policy；篡改、未签名、未知 key_id 或禁止 downgrade 必须拒绝。

主要文件：

- 替换：`backend/Dockerfile`、`frontend/Dockerfile`。
- 新增：`deploy/compose.prod.yml`、`deploy/reverse-proxy/`、`scripts/install.sh`、`scripts/upgrade.sh`、`scripts/rollback.sh`、`scripts/backup.sh`、`scripts/restore.sh`、CI 配置。

Stop gate：

- 在全新 Ubuntu VM 上从 release artifact 安装，30 分钟内完成并通过 E2E。
- liveness/readiness 可区分进程存活、DB、Worker、Agent 状态。
- Control Plane/Worker 容器内 UID 非 0，无法直接执行 host command。
- 升级失败演练在 15 分钟内回滚到 N-1 应用版本；forward schema 下数据和审计仍可用。
- 漏洞按 §7.6 分级；P0/P1 为 0，只有独立安全复核后正式归类为 P2 的问题可 waiver。

### Step 7 — Production Readiness、Pilot、RC 和 GA（R7，P50 7 周 / P90 9 周）

阶段：

1. **Alpha**：开发机真实只读能力，所有 mutating capability disabled。
2. **Destructive Lab**：一次性 Linux VM，完成 systemd/Docker/Nginx/file/DB/firewall 故障与回滚。
3. **Beta**：1-3 台非关键服务器，逐域启用真实 mutating capability；每日复盘任务与审计。
4. **Staging Soak**：冻结 API/schema，连续运行 14 天，期间完成升级/回滚、备份恢复、权限和安全扫描。
5. **RC**：soak 无 P0/P1 后发布签名 RC 制品并冻结 release candidate。
6. **GA Canary**：一台真实低风险服务器运行 7 天，无 P0/P1 后扩大。
7. **GA**：发布签名制品、安装文档、管理员手册、Runbook、SLA/SLO、支持入口和已知限制。

Go / No-Go gate：

- 所有 P0/P1 缺陷关闭；P2 有 owner 和上线后计划。
- 14 天 staging soak 后再进行 7 天 canary；两阶段均无任务永久 running、无审计断链、无未解释 Agent 离线。
- 备份恢复、Agent 重启、Worker 崩溃、磁盘满、证书续签失败、Nginx 配置错误均有演练记录。
- 关键 E2E 100% 通过；高危操作未审批执行次数为 0。
- 用户文档能让未参与开发的运维人员完成安装、升级、恢复和卸载。

## 7. Testable Acceptance Criteria

### 7.1 Functional

- AI 诊断至少支持主机巡检、Docker 异常、Nginx 502、磁盘告警、服务异常和登录风险六类真实场景。
- 每个 mutating task 都能从报告回链到 approval、task、events、audit 和 evidence。
- 任务取消、重试、失败、回滚均有明确最终状态；无法确认副作用时必须为 effect_unknown/manual_recovery，不允许永久 running 或误标 cancelled。
- 未实现或禁用 capability 不出现在 AI 可调用工具列表中。

### 7.2 Security

- Control Plane/Worker 不以 root 运行；Control Plane 不能 import/instantiate AgentClient，Worker 只能通过 AgentClient 调用 UDS，代码/运行测试证明不存在旁路。
- Control Plane 无 Agent socket；只有符合 peer UID/GID 的 Worker 能连接，且 mutating execution grant 只能消费一次。
- 任一 mutating/destructive 调用没有有效 approval 时系统状态不变化。
- destructive 操作必须两个不同审批身份；同一用户两次提交不满足条件。
- production 不存在默认密码、默认 session secret、Secure cookie 关闭或 debug skip password change。
- test spec 的 seeded canary secret 在 log、DB audit、event、report、artifact、browser payload、LLM request 中精确命中为 0。
- break-glass terminal 默认 disabled；启用时必须 MFA、step-up、录制、独立告警和事后复核。

### 7.3 Reliability And Recovery

- 可用性按每分钟关键 synthetic probe 统计：`Availability = good eligible minutes / eligible minutes`。一个 eligible minute 只有在 readiness、认证概览和任务状态读取均在阈值内成功时才是 good；经批准并提前公告的维护窗口可排除，应用 5xx/超时、错误 readiness、Worker/Agent 软件故障均计 bad。月度目标 99.5%，30 天 error budget 为 216 分钟；7 天 canary 只证明发布稳定性，不单独证明月度 SLO。
- 平台数据和关键 artifact 通过异地副本满足 RPO <= 15 分钟、RTO <= 60 分钟；只做同机副本不得宣称满足。
- Worker/Agent 重启后 60 秒内恢复心跳并处理可恢复任务，或按 §4.3 标记 `failed`、`effect_unknown`、`manual_recovery_required`。
- release rollback 在 15 分钟内完成，旧版本健康检查通过。

### 7.4 Performance

- 单节点、20 个并发 UI 用户下，普通读取 API p95 < 300 ms。
- 任务事件从 Agent 产生到 UI 可见 p95 < 1 秒；跨进程/跨主机采样要求 NTP 绝对偏差 < 50 ms 并保留校时证据与原始时间戳。
- 8,000 字节以上命令输出按策略截断或外部存储，不阻塞 event stream。
- 10 万条 audit/event 数据下列表查询 p95 < 500 ms，必须使用分页和索引。

### 7.5 Release Quality

- 后端 unit/integration、前端 unit/component、E2E、security、migration、backup/restore、destructive lab 全部通过。
- 生产镜像可复现构建，有 SBOM、版本、commit SHA 和签名。
- 篡改/未签名 release、未知 signing key 和被禁止 downgrade 安装全部拒绝并审计。
- 运行文档、恢复文档、权限矩阵、capability 风险清单与代码版本一致。
- 生产 GA test spec `test-spec-agent-ops-production-ga-20260710.md` 全部 blocking gate 通过。

### 7.6 Severity And Waiver Policy

| Release severity | 默认映射 | Waiver |
| --- | --- | --- |
| P0 | CVSS >= 9、known exploited、未审批 mutation、Agent boundary 绕过、secret 明文、管理面锁死、DR 不可恢复、签名制品绕过 | 永不允许 |
| P1 | deployed-path CVSS 7.0-8.9、可导致错误系统变更/审计断链/任务重复副作用的缺陷 | GA 前必须清零 |
| P2 | Medium/Low，或 High 经独立 security reviewer 证明不在 deployed path、不可达且有补偿控制后正式降级 | 可书面 waiver，必须有 owner、deadline、风险接受 |

Agent boundary、未审批 mutation、key/secret leakage、异地恢复、管理面保护、release signature/downgrade 永远不能降级或 waiver。

## 8. Expanded Test Plan

### 8.1 Unit

- Capability schema、参数校验、risk policy、dry-run、`rollback_kind`、irreversible effects 和 manual recovery metadata。
- Task 状态机、atomic claim、lease、resource lock、fencing、idempotency、reconciliation、cancel、event ordering。
- Approval fingerprint、nonce、two-person approval、expiry、RBAC。
- Execution grant 签名、SO_PEERCRED identity、nonce replay cache、protocol N/N-1。
- Runner cwd/env/argv allowlist、timeout、process-group/cgroup cancel、redaction、truncation。
- PathPolicy symlink/TOCTOU、atomic write、secret encryption/key rotation、artifact quota/checksum、forward migration。

### 8.2 Integration

- Control Plane -> Worker -> UDS Agent -> capability -> event/audit/report 全链路。
- forged grant、nonce replay、socket peer mismatch、旧 fencing token 和同资源并发变更。
- Worker lease 过期但旧 Agent command 仍运行；Worker/Agent/Control Plane 分别重启后的 journal reconciliation。
- Docker SDK、Nginx test/reload/rollback、systemd restart、DB backup/restore fixture。
- SSE resume、Last-Event-ID、断线重连、cancel acknowledgement。
- prod configuration fail-closed、setup token、TLS、secure cookie、MFA、RBAC、break-glass。
- N/N-1 Agent/Worker/Control Plane 升级与应用回滚；forward schema 不执行 down migration。
- 同机 DB/artifact 磁盘满与异地备份恢复。

### 8.3 E2E

- 登录/MFA/会话恢复/退出。
- AI 巡检 -> 工具证据 -> 报告。
- Docker 异常 -> 审批 -> restart -> 复核。
- Nginx 502 -> diff -> test -> reload -> 失败 rollback。
- Nginx 管理面配置/端口保护，业务 site mutation 不能切断平台入口。
- 文件修改 -> diff -> 审批 -> backup -> write -> verify -> rollback。
- DB restore -> 两人审批 -> restore point -> 恢复 -> consistency check。
- 防火墙错误规则 -> heartbeat loss -> 自动回滚 -> 连接恢复。
- Control Plane/Worker 离线时，Agent/systemd timer 仍完成防火墙自动回滚。
- Break-glass PTY shell escape/编码命令/ANSI injection 被完整录制、告警和事后复核，而不是被误判为 typed safe command。
- 恶意日志/prompt injection 不能改变 tool policy；LLM 失败时降级且不外泄 secret。

### 8.4 Observability / Chaos

- 指标：HTTP latency/error、task counts/duration/stuck、worker lease、agent heartbeat、command failures、approval age、SSE clients、DB size/lock、backup age。
- 告警：Agent offline > 90 秒、waiting_approval 过期、running 超时、备份超过 RPO、磁盘 > 85%、证书 < 14 天、审计写入失败。
- Chaos：kill Worker、kill Agent、DB locked、磁盘满、网络中断、LLM 超时、Nginx config error、Docker daemon unavailable。
- Artifact Store 额外验证 quota、retention、checksum mismatch、对象存储不可用、off-host restore。

## 9. Pre-mortem

### Failure 1: Placeholder 或旁路执行被当成真实成功

- Owner：backend lead；early signal：task 无 before/after evidence、adapter 旁路 AgentClient。
- 早期信号：task succeeded 但没有 before/after evidence，或 registry adapter 未经过 AgentClient。
- 预防：R0 禁用全部 placeholder；静态 contract test 禁止控制面 subprocess；每个 mutation 强制 verify event。
- Kill switch / recovery：全局或逐 capability disable；无 effect 进入 `failed`，effect 无法确认进入 `effect_unknown`，需要人工处理进入 `manual_recovery_required`。

### Failure 2: 任务/Agent 故障导致重复执行或永久 running

- Owner：task-runtime lead；early signal：lease 过期、旧 fencing command 仍活跃、重复 command_started。
- 早期信号：lease 过期、同一 idempotency key 出现多个 command_started、SSE 长期无 heartbeat。
- 预防：持久 lease、resource fencing、Agent journal、cgroup/process-group、operation-specific reconciliation 和 restart recovery test。
- Kill switch / recovery：暂停 mutating queue，隔离重复任务，按 journal/audit/evidence 人工确认系统状态后恢复。

### Failure 3: 升级或备份失败造成平台数据与审计丢失

- Owner：DevOps/data lead；early signal：backup age 超 RPO、checksum/restore drill 失败、migration 无兼容证明。
- 早期信号：migration 无 dry-run、备份 checksum 缺失、restore 从未演练、磁盘增长无告警。
- 预防：版本 migration、online backup、异地副本、恢复演练、升级前备份和自动 rollback。
- Kill switch / recovery：冻结写入，回滚 N-1 应用，使用最近验证的 off-host bundle 和 key escrow 恢复并保留事故审计。

### Failure 4: Execution/master key compromise

- Owner：security lead；early signal：异常 key_id、非预期 grant spike、外部 checkpoint 不一致。
- 预防：systemd credential、key hierarchy、双人 escrow、rotation/revoke test、Worker 无 signing key。
- Kill switch / recovery：立即停止 mutation、revoke key、Agent denylist、作废未消费 grant、轮换密钥并复核全部相关任务。

### Failure 5: Root Agent 或 release supply-chain 被篡改

- Owner：release/security lead；early signal：签名/digest/SBOM 不一致、Agent binary 漂移、异常 capability inventory。
- 预防：签名制品、pinned digest、provenance、启动完整性检查、无动态插件。
- Kill switch / recovery：拒绝安装/启动/降级，隔离 Agent，回滚到最后可信签名版本并重放外部审计。

### Failure 6: 管理面被 Nginx/防火墙变更锁死且 timer 未回滚

- Owner：security/domain lead；early signal：management probe/heartbeat 丢失、rollback timer 未 armed。
- 预防：protected resources、OOB console、Agent-local systemd timer、CP/Worker offline lab。
- Kill switch / recovery：冻结 Nginx/UFW mutation，由 OOB console 执行版本化 manual runbook，复核 timer/service 状态后再启用。

### Failure 7: SQLite/Artifact corruption 或磁盘耗尽

- Owner：data/DevOps lead；early signal：DB integrity/lock latency、artifact quota、disk > 85%、checksum mismatch。
- 预防：WAL、integrity check、quota/retention、off-host copy、disk-full chaos。
- Kill switch / recovery：停止新任务和 artifact 写入，切只读，恢复 DB/artifact/key bundle并做一致性验证。

### Failure 8: R4 scope 与 destructive-lab 成为交付瓶颈

- Owner：program lead；early signal：任一 4A-G gate P50 超期 20%、OOB/fixture 排队、QA evidence backlog。
- 预防：七个独立 gate、3 lane 上限、固定 VM pool、每周 capacity/evidence burn-down。
- Kill switch / recovery：冻结新领域，优先关闭已开始 gate；未通过 mutation 保持 disabled，重新基线排期而不压缩 soak/canary。

## 10. Risks And Mitigations

| 风险 | 等级 | 缓解措施 |
| --- | --- | --- |
| Python/SQLite 在任务并发下锁竞争 | 高 | WAL、短事务、单写 worker、lease/index、压力测试；多节点前迁 PostgreSQL |
| Agent root 权限扩大攻击面 | 高 | 无网络 listener、极窄 typed RPC、systemd sandbox、UDS peer、一次性 grant、replay journal、无动态插件 |
| Worker lease 过期后旧命令继续产生副作用 | 高 | resource lock、fencing、Agent journal、cgroup、operation-specific reconciliation、mutating 默认不重试 |
| Nginx/DB/firewall 真实回滚不可靠 | 高 | 专用破坏性 VM、操作类型白名单、定时 rollback、OOB console、逐域解锁 |
| AI 选择错误工具 | 高 | 仅 enabled typed tools；高危只创建审批；执行前展示完整 payload |
| 前后端并行导致 API 漂移 | 中 | OpenAPI contract、生成/校验 types、兼容期、consumer tests |
| 单体 `main.py` 重构产生回归 | 中 | 先锁现有 14 tests，再逐 router/service 迁移，不做一次性重写 |
| 生产安装复杂导致用户失败 | 中 | 单一安装器、preflight、dry-run、clean VM E2E、升级/卸载/恢复文档 |
| 同机 DB 与备份同时丢失 | 高 | 加密异地副本、checksum、restore drill；无异地副本不得承诺 RPO 15 分钟 |
| Python Agent 高权限供应链/分发复杂 | 中 | R1 Python/Go spike；若 Go 显著降低风险则只替换 Agent，不重写控制面 |

## 11. Delivery Organization

### 11.1 Recommended Team

| Lane | 人数 | 主要责任 |
| --- | ---: | --- |
| Tech lead / architect | 1 | 边界、Agent spike ADR、schema、risk gate、集成决策 |
| Backend / Agent | 3 | Agent、worker、task/event、auth、3 个领域并行 lane |
| Frontend | 1 | App 拆分、专业工具区、流式 UI、审批 UX |
| QA / Security / DevOps | 1 | CI、E2E、破坏性实验、部署、监控、灾备、release |

推荐团队共 6 人。4-5 人团队需将 R4 降为 2 lane，按 §5.2 使用 42-55 周；最小 3 人团队只能顺序交付，必须重新审批排期且不得压缩 destructive lab、soak 或 canary。

### 11.2 Available Agent-Types Roster

- `architect`：控制面/Agent/worker/data 边界与 ADR。
- `executor`：后端、Agent、前端和部署实现。
- `debugger`：任务恢复、网络、SQLite lock、系统命令故障。
- `test-engineer`：unit/integration/E2E/destructive lab。
- `verifier`：stop gate 和发布证据核验。
- `code-reviewer`：安全、并发、兼容和可维护性审查。
- `designer`：AI 诊断台与专业工具区信息架构。
- `security critic`：审批、权限、Agent privilege、secret 和防火墙场景。

### 11.3 Follow-up Staffing Guidance

- R0-R2：architect(high) 1、backend/agent executor(high) 2、test-engineer(high) 1；若选 Go Agent，至少一名 executor 具备 Go/systemd 经验。
- R3：security critic(high) 1、backend executor(high) 1、verifier(high) 1。
- R4：按 4A-G 拆最多 3 个 backend/agent lane；Docker/Nginx 与 Files/DB/Security 不共享写作用文件或 destructive VM。
- R5：designer(high) 1、frontend executor(medium/high) 1、E2E test-engineer(medium) 1。
- R6-R7：DevOps executor(high) 1、security/code reviewer(high) 1、verifier(high) 1。

## 12. Goal-Mode Follow-up Suggestions

- 默认使用 `$ultragoal`：以 R0-R7 为 durable goals，记录 stop gate、devbox、destructive lab、pilot 和 GA 证据。
- 可并行阶段使用 Team + Ultragoal：Team 执行 Agent/Backend、Frontend、QA/Deploy 多 lane；Ultragoal 由 leader 保存唯一 ledger 和 milestone 完成状态。
- `$performance-goal` 仅用于明确性能目标，例如 SSE p95、audit 10 万条查询或 worker throughput。
- 本项目不是研究交付，不建议把 `$autoresearch-goal` 作为主执行方式；需要核验安全规范或第三方 SDK 时，先做 bounded best-practice research。
- `$ralph` 只作为明确选择的单 owner 持续验证/fix 兜底，不是默认执行路线。

### 12.1 OMX CLI Launch Hints

以下命令只在 OMX CLI/tmux runtime 中执行；Codex App 中使用原生 subagents 代替 Team runtime。

```text
$ultragoal "按照 .omx/plans/prd-agent-ops-production-ga-20260710.md 执行 R0-R7；每个 release 以 stop gate、测试、开发机/实验环境和发布证据为完成条件"

$team "并行执行生产 GA 当前 release：lane1 backend/agent，lane2 frontend，lane3 test/security/deploy；所有 lane 返回可由 .omx/plans/prd-agent-ops-production-ga-20260710.md 对应 stop gate 验证的证据"
```

### 12.2 Team Verification Path

1. Team lane 返回代码、测试、真实运行证据和未验证项。
2. Verifier 对照当前 release stop gate 给出 PASS/FAIL，不接受自述完成。
3. Leader 将证据写入 Ultragoal ledger；只有全部 gate PASS 才推进下一 release。
4. Team shutdown 前确认工作区无冲突、服务/临时 Agent/测试容器已清理。
5. R4-R7 必须额外保留破坏性 VM、pilot、升级/恢复演练证据。

## 13. ADR

### Decision

保留 FastAPI Control Plane/Worker、React、Ant Design X 和单节点 SQLite，通过独立 Unix-socket Node Agent、一次性 execution grant、资源 fencing、Artifact Store、生产安全/部署和逐域真实 adapter，把现有样机演进为 local-first 单节点 GA。Agent 语言由 R1 Python/Go spike 决定。

### Drivers

- 真实高危运维需要控制面/执行面分离和可证明回滚。
- 当前已有 API、任务、审批、AI 和 UI 基础，重写会放大周期与回归。
- 中小团队单节点部署需要低依赖和可恢复性。

### Alternatives Considered

- 立即 Go 重写 Agent/控制面。
- 保留 FastAPI，只用 Go 实现高权限 Agent。
- 只读诊断产品长期不开放修复。
- 直接扩菜单并保留 in-process runner。

### Why Chosen

Option A 同时满足安全边界、复用现有资产和可控交付周期；逐域启用吸收只读方案的风险控制优点。Option D 对高权限 Agent 有真实竞争力，因此保留证据驱动 spike gate，而不是预先锁定 Python。保留 in-process runner 会直接违反 `AGENTS.md:18-30` 和研究报告 `demo/底层能力研究报告.md:69-110` 的核心结论，因此无效。

### Consequences

- 首个 GA 仍是单节点，远程多节点推迟到 GA+1。
- 部署采用 Control Plane/Worker 容器 + 宿主 root Agent systemd 的混合形态；只有 Worker 挂载 UDS。
- SQLite 需要严格并发边界、迁移、备份和恢复；多节点前需重新评估 PostgreSQL。
- 调度为 at-least-once + reconciliation，不承诺通用 exactly-once；取消后可能进入 effect_unknown/manual recovery。
- 完整 PTY 是 break-glass，而不是普通安全 capability。
- placeholder 不能再用于“完成”真实能力，短期里程碑通过率会下降但证据质量提高。

### Follow-ups

- GA+1：远程 Agent enrollment、mTLS、证书轮换、多节点 inventory/tag/dispatch。
- GA+2：应用商店、版本化安装任务、快照/整机备份、插件/MCP capability permissions。
- GA+3：GPU、Ollama/本地模型、模型运行时和高级监控。

## 14. Applied Consensus Improvements

Consensus revisions 已依次吸收 Architect 与 Critic 的 P0/P1 建议：

- 增加 FastAPI + Go Agent 混合选项与 R1 spike gate。
- 修正执行链为 Control Plane -> DB Task -> Worker -> UDS Agent；只有 Worker 挂载 socket。
- 明确 root Agent trust boundary、UDS group/SO_PEERCRED、一次性 execution grant、nonce replay 和协议兼容。
- 增加 resource lock、fencing、Agent journal、reconciliation 和细分取消状态。
- 增加 Artifact Store、异地副本、forward-only migration 和 N/N-1 应用回滚。
- 将完整 PTY 改为默认 disabled 的 break-glass terminal。
- 增加管理面自保护、Agent 独立 rollback timer、文件 TOCTOU/原子写和 LLM 数据治理。
- 将 R4 拆为 4A-G 七个纵向领域 gate（P50 10 / P90 14 周并行窗口），R7 调整为 14 天 soak + 7 天 canary 的 P50 7 / P90 9 周阶段。
- 新增独立生产 GA test spec，并补齐 UDS/fencing/crash/upgrade/off-host backup/prompt-injection gates。
- 第二轮修订统一了 setup enrollment、Worker-only AgentClient、execution-signing key、capability-specific rollback/manual recovery、root 自研组件断言、canonical 状态机和可复算的 36-49 周承诺区间。
- 最终修订明确 33/44 周主路径与 36-49 周缓冲承诺的区别，统一 R6A 人周口径，增加 `effect_proven_none_or_failure_verified` guard、good-minutes availability SLI、SSE 时钟偏差证据和部分副作用负例。
- R0 执行证据确认 MySQL 8.0.46 已于 2026 年 4 月 EOL，因此把 R4F 正式支持线调整为 MySQL 8.4 LTS，并保留 8.0.46 作为迁移兼容 fixture；该 steering 已记录在 Ultragoal ledger。
- Architect 最终结论：`APPROVE`；Critic 最终结论：`APPROVE`。本计划可进入执行 handoff，但任何 R0-R7 blocking gate 仍不得以评审结论替代实际证据。
