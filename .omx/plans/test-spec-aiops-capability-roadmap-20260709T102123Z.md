# Test Spec: AI Ops Capability Roadmap

## Scope

验证第二阶段完整能力路线图中的底层能力、执行层能力、规划层能力和 UI 闭环。测试重点不是“页面能打开”，而是“AI 规划、人类审批、任务系统执行、执行层回传、审计报告沉淀”这条链路可证明。

## Baseline Commands

所有里程碑至少运行：

```bash
./scripts/smoke.sh
```

新增后端能力后至少运行：

```bash
cd backend && pytest
```

新增前端或 UI 交互后至少运行：

```bash
cd frontend && npm test
```

涉及生产构建、依赖或 Docker 镜像后运行：

```bash
cd frontend && npm run build
docker compose config
```

## Acceptance Matrix

| Area | Scenario | Expected Result |
| --- | --- | --- |
| Capability registry | List capabilities | 每个 capability 包含 schema、risk、approval、dry_run、rollback、audit 字段 |
| Capability registry | AI requests disabled tool | 请求被拒绝并记录审计 |
| Approval policy | Mutating operation requested | 返回 approval_required，不执行真实动作 |
| Approval policy | Destructive operation requested | 需要高危审批或默认阻断 |
| Task system | Create long-running task | 任务进入 pending/running/succeeded 状态，并有事件流 |
| Task system | Cancel running task | cancel_requested 被记录，runner 尽力停止，最终状态为 cancelled 或 failed with reason |
| Event stream | Command emits stdout/stderr | UI 能实时显示输出，后端保存可审计事件 |
| Audit | Any tool executes | 审计记录 actor、resource、risk、status、summary、task_id/event_id |
| Command runner | Command timeout | 返回 structured error，任务失败但系统不崩溃 |
| Command runner | Secret-like output | 输出在入库和发给 LLM 前脱敏 |
| Command runner | Arbitrary shell | 默认无此 capability；请求被拒绝 |
| Local agent | Control plane invokes capability | 控制面通过 AgentClient，不直接调用 shell adapter |
| Host inspection | 巡检服务器 | 报告包含 CPU、内存、磁盘、端口、进程、服务、安全登录风险 |
| systemd | Restart service | 需要审批；批准后通过 task 执行并审计 |
| Docker | Inspect container | 只读执行，返回状态、日志摘要和 inspect 信息 |
| Docker | Restart container | 需要审批；批准后形成 task/event/audit |
| Docker | Delete volume | destructive，默认阻断或二次高危审批 |
| Nginx | Diagnose 502 | 检查 status、config test、error log、upstream reachability |
| Nginx | Reload config | 必须先 diff、backup、nginx -t，成功后 reload，失败 rollback |
| SSL | Renew cert | 任务化执行，失败可回滚到旧证书 |
| Files | Read allowed file | 在 allowed roots 内成功并审计 |
| Files | Write file | 先 diff/backup，审批后写入 |
| Files | Access blocked path | 被 path policy 拒绝 |
| Terminal | Open session | 创建 terminal_session，命令和输出被审计 |
| Terminal | Dangerous command | 被拦截或要求审批 |
| Database | Check connection | 只读返回连接状态、版本、连接数 |
| Database | Restore backup | 标记 destructive，需要高危审批和恢复点确认 |
| Security | Inspect firewall/SSH | 只读返回当前规则和风险 |
| Security | Apply firewall/SSH change | 必须有 preflight、审批、rollback plan |
| AI diagnosis | Ask operational question | AI 生成计划、调用 typed tools、展示证据、提出审批动作 |
| AI diagnosis | Tool fails | AI 记录失败证据并选择替代检查或给出不确定性 |
| Reports | Diagnosis completes | 报告能回链到任务、事件、审计和证据 |
| UI | Task detail page | 展示步骤、事件、输出、审批、回滚状态 |
| UI | Approval modal | 展示风险、目标、参数、原因、回滚方案 |

## Unit Test Areas

- Capability schema validation。
- Risk level classification。
- Approval policy matching。
- Task state transitions。
- Event sequence ordering。
- Audit record creation。
- Command runner timeout/cancel/redaction/truncation。
- Path policy allow/deny。
- Rollback plan required fields。
- Approval fingerprint binding actor/session/tool/params/resource。
- AgentClient interface contract。

## Integration Test Areas

- `GET /api/capabilities` returns complete metadata。
- Capability invocation writes audit and event records。
- Mutating capability creates or references an approval/task。
- Task lifecycle API supports create/list/detail/cancel。
- Task SSE emits ordered events。
- Diagnosis session can attach task events.
- Docker/Nginx fixture diagnosis generates expected plan and evidence.
- Nginx reload fixture verifies diff/test/reload/rollback order.
- File write fixture verifies diff/backup/write/audit order.
- Database restore fixture is blocked without high-risk approval.

## E2E Scenarios

1. **全量服务器巡检**
   - 登录。
   - 在 AI 诊断台输入“帮我巡检这台服务器”。
   - 验证计划、工具调用、事件、报告均出现。
   - 验证报告包含证据引用。

2. **Docker 容器异常**
   - 使用 fixture 或测试容器模拟异常。
   - 输入“这个容器为什么挂了”。
   - 验证 inspect/logs/stats 被调用。
   - 验证 restart 只作为审批动作出现。

3. **Nginx 502 修复**
   - 使用 fixture 模拟 upstream 失败。
   - 输入“Nginx 为什么 502”。
   - 验证 status/config_test/error_log/upstream_probe。
   - 批准 reload 前必须显示 diff、test 和 rollback。

4. **文件配置修改**
   - 打开 allowed root 内配置文件。
   - 提交修改。
   - 验证 diff、backup、approval、write、audit。
   - 模拟失败时验证 rollback。

5. **高危数据库恢复**
   - 选择 restore backup。
   - 验证 destructive 风险、二次确认、恢复点信息。
   - 未审批不得执行。

6. **终端命令审计**
   - 打开终端会话。
   - 运行安全只读命令。
   - 尝试危险命令。
   - 验证安全命令记录、危险命令阻断或审批。

7. **防火墙/SSH 安全变更**
   - 生成 SSH hardening 建议。
   - 验证 preflight、连通性保护、审批、rollback plan。

## Security And Safety Tests

- 无认证不能访问 tools、tasks、events、audit、reports、agent diagnostics。
- 缺少 CSRF token 的 mutating 请求被拒绝。
- 伪造 approval id 或更换 session 被拒绝。
- 过期 approval 不能执行。
- 参数替换不能复用原 approval。
- 任意 shell 默认不存在。
- sudo 默认不可用。
- destructive capability 默认禁用或二次审批。
- Secret redaction 覆盖 password、token、api_key、authorization、private key、DSN。

## Pre-mortem Regression Gates

| Failure Scenario | Required Regression Proof |
| --- | --- |
| 高危命令绕过审批或任务系统执行 | mutating adapter 不能在没有 task_id、approval_id、ops_event 的情况下返回 success；静态或单元测试覆盖所有 registered mutating capability |
| SSH/firewall 变更导致机器失联 | 写操作必须创建 preflight result、rollback task、heartbeat check；heartbeat loss simulation 必须触发 rollback |
| 文件写或 DB restore 破坏数据且无法回滚 | file.write 必须有 diff_ref、backup_ref、verification_result；db.restore 必须有 backup_checksum、target_db、restore_point、second_approval |

## Observability Evidence Required

每个能力域完成时必须保存或能生成：

- 一条成功任务事件流。
- 一条失败任务事件流。
- 一条审批记录。
- 一条审计记录。
- 一个报告样例。
- 一个拒绝高危/非法操作的证据。
- runner/agent diagnostics 输出。

## Milestone Completion Gates

### Milestone 0

- Schema 和 API contract 测试通过。
- 旧 Phase 1 endpoints 兼容。
- `/api/tools` 与 `/api/capabilities` 的兼容语义有 contract test。

### Milestone 1

- Task/Event/Audit 单元和集成测试通过。
- UI 能展示任务事件流。
- PathPolicy、TerminalSession 元数据、command risk classifier contract test 通过。
- 注册的 mutating/destructive adapter 必须通过 AgentClient/task/approval contract 检查，不能直接绕过执行。

### Milestone 2

- Command runner 的 timeout、cancel、redaction、streaming 测试通过。
- 所有工具走 AgentClient。
- AI command suggestion 不自动执行；用户触发的终端命令进入 session audit。

### Milestone 3

- Host/systemd 巡检和审批 restart 流程通过。

### Milestone 4

- Docker 和 Nginx 诊断/审批/执行/复核闭环通过。

### Milestone 5

- 文件、终端、数据库各至少一条完整安全闭环通过。
- 文件写必须证明 allowed roots、diff、backup、approval、write verification、rollback artifact。
- DB restore 必须证明目标库确认、备份校验、恢复点确认、destructive 二次审批。
- `BackupArtifact` 和 secret storage 最小字段 contract test 通过。

### Milestone 6

- SSH/firewall 高危变更 safety test 通过。
- remote agent contract 测试通过。
- SSH/firewall 变更必须证明 preflight、连通性保护、定时自动回滚和 heartbeat loss rollback。
- 真实 Linux 破坏性测试环境已确认，或 destructive case 只能以 fixture/simulation 验证并在报告中标明缺口。

## Final Verification Before Declaring Roadmap Implementation Complete

- 本地 `./scripts/smoke.sh` 通过。
- 开发机同步完成。
- 开发机 `./scripts/smoke.sh` 通过。
- 浏览器验证 AI 诊断台、任务详情、审批弹窗、审计和报告。
- 至少 3 个真实或 fixture 运维场景完成：巡检、Docker 异常、Nginx 502。
- 高危操作阻断证据齐全。
