# Project Rules

本项目是面向中小团队的 AI 原生服务器运维面板。前端实现必须优先使用阿里系 UI 体系，避免手写一套临时 UI。

## Architecture And Capability Rules

本项目的底层能力设计以 `demo/底层能力研究报告.md` 为参考，但不得直接复制面板项目源码。

### Reference Boundaries

- 1Panel 作为主要架构参考：优先借鉴控制面与执行面分离、Agent 化、typed API、统一异步任务、Docker/Compose/AppStore/AI runtime 等设计。
- 宝塔面板只作为能力清单和运维经验参考：可借鉴其网站、数据库、文件、终端、计划任务、插件、备份、SSL、安全、日志、Docker、项目运行时等覆盖范围。
- 不得直接复制宝塔业务代码、插件体系或不透明二进制 loader。宝塔许可限制修改后源码或衍生产品的传播，项目实现必须独立编写。
- 如直接使用或改造 1Panel 代码，必须先评估 GPLv3 义务；默认只做架构和实现思路参考。

### Control Plane And Agent

- 后续底层架构必须优先采用 `Backend Control Plane -> Task/Audit/Approval/Event Stream -> Node Agent(s)` 的分层模型。
- 控制面不直接执行 root 命令；主机、Docker、Nginx、数据库、文件、SSH、监控等能力应由受控 Agent 暴露。
- Agent 暴露能力时必须声明 capability、输入输出 schema、risk_level、是否支持 dry_run、rollback 和 audit fields。
- 本地节点可以使用 unix socket；远程节点必须优先考虑 mTLS HTTPS 或等价强认证，不要依赖裸 token 暴露高危执行接口。

### Task, Command, Audit, Approval

- 所有耗时、高危或有副作用的操作必须进入统一任务系统，不允许在普通 HTTP 请求中同步执行。
- 任务模型必须包含 task、subtask、resource、status、log、operator、reason、started_at、finished_at、cancel token 和失败原因。
- 命令执行必须通过统一 command runner，支持 timeout、cwd、env、stdout/stderr streaming、取消、审计和结果结构化。
- 禁止在业务代码中散落 shell 字符串拼接；确需 shell 兼容层时必须标记高风险，并通过 allowlist/denylist 与审批策略。
- 修改系统配置前必须支持 dry-run 或 diff；例如 Nginx 配置生成必须先 diff、test，再 reload，失败时自动回滚。
- 防火墙、SSH、服务重启、文件删除、数据库恢复、容器删除等高危操作必须经过审批或明确的受控策略。

### Capability Priorities

- 第一优先级：节点注册与 heartbeat、命令执行器、统一任务系统、基础主机信息、终端/SSH 会话审计、受限文件管理。
- 第二优先级：Docker 容器/镜像/网络/卷/Compose、Nginx 站点和反代、SSL/ACME、MySQL/PostgreSQL/Redis、基础防火墙和 SSH 安全策略。
- 第三优先级：应用商店、多节点、快照/整机备份、MCP/Agent 插件市场、GPU/本地模型运行时。
- 新增能力不要追求宝塔式大而全菜单；优先闭环 AI 运维流程：诊断、计划、审批、执行、验证、报告。

### AI-Native Product Rules

- AI 诊断台是主入口，传统运维页面是工具区；不要把 AI 做成普通面板外面的一层聊天壳。
- AI 工具调用链必须可视化、可审计、可重放，事件流同时服务前端展示和后端报告生成。
- AI 生成命令只能作为建议或待审批动作，不得默认自动执行高危命令。
- UI 信息架构优先分为概览、AI 诊断台、专业工具区；专业工具区再承载服务器、容器、网站、数据库、文件、终端、审计和报告。

## Frontend UI Rules

### 普通后台页面

- 普通后台页面必须优先使用 Ant Design 体系。
- 新增或重构后台页面前，先检查当前 `frontend/package.json` 是否已有 `antd`；没有则在对应开发任务中加入依赖。
- 可使用本机已安装的 `@ant-design/cli` 辅助创建、检查或生成 Ant Design 相关页面/组件。
- 后台页面包括但不限于：
  - 登录页
  - 服务器概览
  - 工具列表
  - 审计记录
  - 巡检报告
  - 设置页
  - Runner 诊断页
- 后台页面优先使用 Ant Design 的布局与数据展示组件，例如：
  - `Layout`
  - `Menu`
  - `Card`
  - `Button`
  - `Form`
  - `Input`
  - `Table`
  - `Tabs`
  - `Tag`
  - `Alert`
  - `Modal`
  - `Drawer`
  - `Descriptions`
  - `Statistic`
  - `Timeline`
  - `Result`
- 不要为常见后台控件手写重复组件，除非 Ant Design 没有合适组件或项目已有明确封装。

### AI 对话页面

- AI 对话、诊断台、Agent 事件流、工具调用链路展示必须优先使用 Ant Design X。
- 编写或重构 AI 对话页面前，必须使用本机已安装的 Ant Design X 相关 skills：
  - `x-components`
  - `use-x-chat`
  - `x-chat-provider`
  - `x-request`
  - `x-markdown`
  - `x-card`
- 本机已确认存在：
  - `@ant-design/cli`
  - `@ant-design/x-skill`
  - `/Users/a007/.codex/skills/x-components/SKILL.md`
  - `/Users/a007/.codex/skills/use-x-chat/SKILL.md`
  - `/Users/a007/.codex/skills/x-chat-provider/SKILL.md`
  - `/Users/a007/.codex/skills/x-request/SKILL.md`
  - `/Users/a007/.codex/skills/x-markdown/SKILL.md`
  - `/Users/a007/.codex/skills/x-card/SKILL.md`
- AI 对话页必须用 `XProvider` 包裹相关区域或应用根节点。
- 对话消息列表必须优先使用 `Bubble.List`，不要手写 `map` 一堆普通气泡。
- 用户输入必须优先使用 `Sender`。
- 多步工具调用、诊断计划、Agent 执行链路必须优先使用 `ThoughtChain` 或 `Think`。
- 消息操作必须优先使用 `Actions` 体系，例如复制、反馈、重新生成等。
- 如果消息包含 Markdown，必须使用 `x-markdown` 规则处理，不要直接把 Markdown 当普通字符串渲染。
- 如果 AI 输出结构化卡片，优先使用 `x-card` 规则，不要把复杂 JSON 直接展示给用户。

## Dependency Rules

- 普通后台 UI 任务需要时加入：

```bash
npm install antd
```

- AI 对话 UI 任务需要时加入：

```bash
npm install @ant-design/x @ant-design/x-sdk @ant-design/x-markdown
```

- 增加依赖后必须运行：

```bash
cd frontend && npm test
```

必要时还要运行：

```bash
cd frontend && npm run build
```

## Current Refactor Direction

当前 `frontend/src/main.tsx` 还是第一阶段调试型 UI。后续前端重构方向：

- 把整体后台布局迁移到 Ant Design `Layout` / `Menu` / `Card` / `Table`。
- 把 AI 诊断台迁移到 Ant Design X：
  - `XProvider`
  - `Bubble.List`
  - `Sender`
  - `ThoughtChain`
  - `Actions`
- 保留现有后端 API 契约，不为了 UI 重构破坏认证、CSRF、审批、审计链路。

## Verification Rules

前端页面变更完成后至少运行：

```bash
./scripts/smoke.sh
```

如果改动 AI 诊断台或对话体验，还必须人工打开：

```text
http://127.0.0.1:5173/
```

并确认：

- 默认账号密码能登录。
- 概览卡片能加载。
- AI 诊断能发起。
- 诊断事件能显示。
- 审计和报告区域不报错。

## Dev Machine Sync Rules

每次开发工作完成后，必须同步到开发机。这里的“完成”包括代码修改、依赖调整、配置修改、前端页面变更、后端 API 变更、脚本变更和文档规则变更。

### 同步前要求

- 先在本地完成必要验证。
- 默认至少运行：

```bash
./scripts/smoke.sh
```

- 如果改动前端生产构建相关内容，还要运行：

```bash
cd frontend && npm run build
```

- 如果验证失败，不允许同步到开发机；必须先修复失败项。

### 同步方式

- 优先使用项目内已有的同步脚本或部署脚本。
- 如果项目后续新增 `scripts/sync-dev.sh`、`scripts/deploy-dev.sh` 或类似脚本，应统一通过该脚本同步。
- 如果当前任务需要同步但项目尚未配置开发机地址、账号或同步脚本，不要猜测目标机器；需要先补齐明确配置或让用户提供开发机信息。
- 不要把 `.env`、密钥、数据库文件、缓存、`node_modules`、虚拟环境等本地临时文件同步到开发机。

### 同步后要求

- 同步完成后必须做开发机侧验证。
- 至少确认：
  - 服务能启动或已热更新。
  - 前端页面能打开。
  - 后端健康接口可访问。
  - 本次改动涉及的核心功能在开发机可用。
- 最终回复必须说明：
  - 本地验证结果。
  - 是否已同步到开发机。
  - 开发机验证结果。
  - 如果未同步，必须说明阻塞原因。
