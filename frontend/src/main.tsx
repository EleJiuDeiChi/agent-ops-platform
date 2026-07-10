import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Alert,
  App as AntApp,
  Avatar,
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Empty,
  Form,
  Input,
  Layout,
  Menu,
  Modal,
  Progress,
  Row,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Typography
} from "antd";
import type { MenuProps, TableProps } from "antd";
import {
  AuditOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DashboardOutlined,
  FileDoneOutlined,
  LoginOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  ToolOutlined,
  WarningOutlined
} from "@ant-design/icons";
import Actions from "@ant-design/x/es/actions";
import Bubble from "@ant-design/x/es/bubble";
import type { BubbleListProps } from "@ant-design/x/es/bubble";
import Sender from "@ant-design/x/es/sender";
import ThoughtChain from "@ant-design/x/es/thought-chain";
import type { ThoughtChainItemType } from "@ant-design/x/es/thought-chain";
import XProvider from "@ant-design/x/es/x-provider";
import zhCNX from "@ant-design/x/es/locale/zh_CN";
import { XMarkdown } from "@ant-design/x-markdown";
import zhCN from "antd/locale/zh_CN";
import {
  approve,
  cancelTask,
  changePassword,
  createDiagnosis,
  createTask,
  deny,
  getAudit,
  getDiagnosisEvents,
  getHealth,
  getReports,
  getRunnerDiagnostics,
  getTaskEvents,
  getTasks,
  getTools,
  login
} from "./api/client";
import type {
  AuditRecord,
  DiagnosisEvent,
  OpsEvent,
  OpsTask,
  Report,
  RunnerDiagnostics,
  SessionIdentity,
  Tool,
  ToolResult
} from "./types/api";
import "./styles.css";

const { Header, Sider, Content } = Layout;
const { Title, Text, Paragraph } = Typography;

type BubbleItem = NonNullable<BubbleListProps["items"]>[number];
type FeedbackValue = "default" | "like" | "dislike";

const QUICK_PROMPTS = [
  "帮我巡检这台服务器",
  "网站打不开时先检查哪里？",
  "Docker 容器异常怎么办？",
  "这台机器有没有明显风险？"
];

const IS_DEV = import.meta.env.DEV;

const DEBUG_LOGIN_VALUES = IS_DEV
  ? { username: "admin", password: "BootstrapPassword123!" }
  : { username: "", password: "" };

const TOOL_LABELS: Record<string, string> = {
  "system.health": "看 CPU / 内存 / 系统状态",
  "system.disk": "看磁盘空间",
  "system.processes": "看高占用进程",
  "network.ports": "看端口和网络监听",
  "systemd.status": "看系统服务状态",
  "systemd.restart": "重启系统服务",
  "systemd.reload": "重载系统服务",
  "docker.ps": "看 Docker 容器",
  "nginx.config_test": "检查 Nginx 配置",
  "docker.restart": "重启 Docker 容器",
  "database.restore": "恢复数据库备份",
  "file.write": "写入受限文件",
  "firewall.apply": "应用防火墙变更"
};

const EVENT_LABELS: Record<string, { title: string; description: string }> = {
  plan: {
    title: "AI 先列检查计划",
    description: "把问题拆成几项基础检查，避免一上来就执行危险操作。"
  },
  tool_call: {
    title: "正在检查服务器",
    description: "调用只读工具收集证据。"
  },
  tool_result: {
    title: "拿到检查结果",
    description: "把命令结果整理成人能读懂的摘要。"
  },
  approval_required: {
    title: "需要你确认修复动作",
    description: "涉及修改或重启时，必须先由人批准。"
  },
  final_answer: {
    title: "AI 给出结论",
    description: "汇总风险、证据和下一步建议。"
  }
};

export function App() {
  const [identity, setIdentity] = useState<SessionIdentity | null>(null);
  const [passwordRequired, setPasswordRequired] = useState(false);
  const [error, setError] = useState("");

  async function handleLogin(username: string, password: string) {
    setError("");
    try {
      const body = await login(username, password);
      setIdentity(body.identity);
      setPasswordRequired(body.must_change_password);
    } catch (err) {
      setError(err instanceof Error ? normalizeApiError(err.message) : "登录失败");
    }
  }

  async function handlePasswordChange(currentPassword: string, newPassword: string) {
    setError("");
    try {
      const body = await changePassword(currentPassword, newPassword);
      setIdentity(body.identity);
      setPasswordRequired(false);
    } catch (err) {
      setError(err instanceof Error ? normalizeApiError(err.message) : "修改密码失败");
    }
  }

  if (!identity) {
    return <LoginScreen error={error} onLogin={handleLogin} />;
  }

  if (passwordRequired) {
    return (
      <PasswordScreen
        actor={identity.actor_id}
        error={error}
        onChangePassword={handlePasswordChange}
      />
    );
  }

  return <Dashboard identity={identity} />;
}

function LoginScreen({
  error,
  onLogin
}: {
  error: string;
  onLogin: (username: string, password: string) => void;
}) {
  return (
    <main className="auth-shell">
      <Card className="auth-panel" variant="outlined">
        <Space orientation="vertical" size={20} className="full-width">
          <Space size={14} align="center">
            <Avatar size={44} icon={<CloudServerOutlined />} />
            <div>
              <Text type="secondary">{IS_DEV ? "本地调试模式" : "安全登录"}</Text>
              <Title level={2}>AI 原生服务器运维面板</Title>
            </div>
          </Space>
          <Alert
            showIcon
            type="info"
            title={IS_DEV ? "本地调试账号已预填" : "使用管理员账号登录"}
            description={
              IS_DEV
                ? "账号密码仅在本地开发模式预填，用于快速验证功能。"
                : "生产环境不会预填账号密码。请使用首次初始化时创建的管理员账号。"
            }
          />
          <Form
            layout="vertical"
            initialValues={DEBUG_LOGIN_VALUES}
            onFinish={(values: { username: string; password: string }) =>
              onLogin(values.username, values.password)
            }
          >
            <Form.Item label="账号" name="username" rules={[{ required: true }]}>
              <Input autoComplete="username" size="large" />
            </Form.Item>
            <Form.Item label="密码" name="password" rules={[{ required: true }]}>
              <Input.Password autoComplete="current-password" size="large" />
            </Form.Item>
            {error ? <Alert type="error" showIcon title={error} /> : null}
            <Button
              block
              type="primary"
              htmlType="submit"
              icon={<LoginOutlined />}
              size="large"
            >
              {IS_DEV ? "直接进入" : "登录"}
            </Button>
          </Form>
        </Space>
      </Card>
    </main>
  );
}

function PasswordScreen({
  actor,
  error,
  onChangePassword
}: {
  actor: string;
  error: string;
  onChangePassword: (currentPassword: string, newPassword: string) => void;
}) {
  return (
    <main className="auth-shell">
      <Card className="auth-panel" variant="outlined">
        <Space orientation="vertical" size={18} className="full-width">
          <Alert
            type="warning"
            showIcon
            title="后端仍要求修改初始密码"
            description={`当前身份：${actor}。如果是调试环境，请确认后端 AIOPS_DEBUG_SKIP_PASSWORD_CHANGE 已开启。`}
          />
          <Form
            layout="vertical"
            onFinish={(values: { currentPassword: string; newPassword: string }) =>
              onChangePassword(values.currentPassword, values.newPassword)
            }
          >
            <Form.Item label="当前密码" name="currentPassword" rules={[{ required: true }]}>
              <Input.Password size="large" />
            </Form.Item>
            <Form.Item label="新密码" name="newPassword" rules={[{ required: true, min: 12 }]}>
              <Input.Password size="large" />
            </Form.Item>
            {error ? <Alert type="error" showIcon title={error} /> : null}
            <Button type="primary" htmlType="submit" block size="large">
              保存并进入面板
            </Button>
          </Form>
        </Space>
      </Card>
    </main>
  );
}

function Dashboard({ identity }: { identity: SessionIdentity }) {
  const { message } = AntApp.useApp();
  const [health, setHealth] = useState<ToolResult | null>(null);
  const [tools, setTools] = useState<Tool[]>([]);
  const [runner, setRunner] = useState<RunnerDiagnostics | null>(null);
  const [events, setEvents] = useState<DiagnosisEvent[]>([]);
  const [audit, setAudit] = useState<AuditRecord[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [tasks, setTasks] = useState<OpsTask[]>([]);
  const [taskEvents, setTaskEvents] = useState<OpsEvent[]>([]);
  const [question, setQuestion] = useState(QUICK_PROMPTS[0]);
  const [busy, setBusy] = useState(false);
  const [taskBusy, setTaskBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [apiError, setApiError] = useState("");
  const [feedback, setFeedback] = useState<FeedbackValue>("default");

  async function refresh(showToast = false) {
    setApiError("");
    try {
      const [healthResult, toolList, runnerInfo, auditRows, reportRows, taskRows] = await Promise.all([
        getHealth(),
        getTools(),
        getRunnerDiagnostics(),
        getAudit(),
        getReports(),
        getTasks()
      ]);
      setHealth(healthResult);
      setTools(toolList);
      setRunner(runnerInfo);
      setAudit(auditRows);
      setReports(reportRows);
      setTasks(taskRows);
      if (showToast) message.success("已刷新当前服务器状态");
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function runDiagnosis(nextQuestion = question) {
    const trimmed = nextQuestion.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setQuestion(trimmed);
    setEvents([]);
    setApiError("");
    setFeedback("default");
    try {
      const session = await createDiagnosis(trimmed);
      const sessionEvents = await getDiagnosisEvents(session.id);
      setEvents(sessionEvents);
      await refresh();
      message.success("诊断完成，已生成一份巡检报告");
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "诊断失败");
    } finally {
      setBusy(false);
    }
  }

  const latestActiveTask = useMemo(
    () => tasks.find((task) => ["pending", "running", "waiting_approval"].includes(task.status)),
    [tasks]
  );

  async function showTaskEvents(task: OpsTask) {
    setApiError("");
    try {
      const rows = task.events?.length ? task.events : await getTaskEvents(task.id);
      setTaskEvents(rows);
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "读取任务事件失败");
    }
  }

  async function runMockTask() {
    if (taskBusy) return;
    setTaskBusy(true);
    setApiError("");
    try {
      const task = await createTask({
        type: "mock.long_running",
        title: "模拟任务事件流",
        resource: "local-server",
        risk_level: "read",
        reason: "验证任务事件底座",
        simulate: true
      });
      setTasks((current) => [task, ...current.filter((row) => row.id !== task.id)]);
      setTaskEvents(task.events || []);
      await refresh();
      message.success("模拟任务已完成，事件已写入统一事件流");
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "创建任务失败");
    } finally {
      setTaskBusy(false);
    }
  }

  async function cancelLatestTask() {
    if (!latestActiveTask || taskBusy) return;
    setTaskBusy(true);
    setApiError("");
    try {
      const task = await cancelTask(latestActiveTask.id);
      setTaskEvents(task.events || []);
      await refresh();
      message.success("已记录任务取消请求");
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "取消任务失败");
    } finally {
      setTaskBusy(false);
    }
  }

  async function approveTaskApproval(task: OpsTask) {
    if (!task.approval_id || taskBusy) return;
    setTaskBusy(true);
    setApiError("");
    try {
      await approve(task.approval_id);
      const rows = await getTaskEvents(task.id);
      setTaskEvents(rows);
      await refresh();
      message.success("任务审批已批准");
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "批准任务失败");
    } finally {
      setTaskBusy(false);
    }
  }

  async function denyTaskApproval(task: OpsTask) {
    if (!task.approval_id || taskBusy) return;
    setTaskBusy(true);
    setApiError("");
    try {
      await deny(task.approval_id);
      const rows = await getTaskEvents(task.id);
      setTaskEvents(rows);
      await refresh();
      message.success("任务审批已拒绝");
    } catch (err) {
      setApiError(err instanceof Error ? normalizeApiError(err.message) : "拒绝任务失败");
    } finally {
      setTaskBusy(false);
    }
  }

  const approvalEvent = useMemo(
    () => events.find((event) => event.type === "approval_required"),
    [events]
  );

  const finalAnswer = useMemo(() => {
    const event = lastEventOfType(events, "final_answer");
    return typeof event?.payload.summary === "string"
      ? event.payload.summary
      : "还没有诊断结论。先点击“一键体检服务器”。";
  }, [events]);

  const assistantMarkdown = useMemo(() => buildAssistantMarkdown(events), [events]);
  const thoughtItems = useMemo(() => buildThoughtItems(events, busy), [events, busy]);
  const groupedTools = useMemo(() => groupTools(tools), [tools]);
  const healthScore = reports[0]?.health_score ?? (health?.status === "success" ? 86 : 0);
  const approvalToolName = String(approvalEvent?.payload.tool_name || "");
  const approvalTool = tools.find((tool) => tool.name === approvalToolName);
  const approvalDisabledReason =
    approvalTool && !approvalTool.enabled
      ? disabledReasonLabel(approvalTool.disabled_reason_code)
      : "";

  async function approveCurrent() {
    const approvalId = approvalEvent?.payload.id;
    if (typeof approvalId !== "string") return;
    if (approvalDisabledReason) {
      message.warning(`该动作当前不可执行：${approvalDisabledReason}`);
      return;
    }
    await approve(approvalId);
    await refresh();
    message.success("已批准并记录到审计日志");
  }

  function confirmApproval() {
    const toolName = String(approvalEvent?.payload.tool_name || "修复动作");
    if (approvalDisabledReason) {
      message.warning(`该动作当前不可执行：${approvalDisabledReason}`);
      return;
    }
    Modal.confirm({
      title: "确认执行这个修复动作？",
      content: `即将批准：${readableTool(toolName)}。这类动作会改变服务器状态，所以需要你手动确认。`,
      okText: "批准执行",
      cancelText: "先不处理",
      icon: <WarningOutlined />,
      onOk: approveCurrent
    });
  }

  const menuItems: MenuProps["items"] = [
    { key: "start", icon: <PlayCircleOutlined />, label: "快速上手" },
    { key: "overview", icon: <DashboardOutlined />, label: "服务器概览" },
    { key: "diagnosis", icon: <RobotOutlined />, label: "AI 诊断台" },
    { key: "tasks", icon: <ThunderboltOutlined />, label: "任务事件" },
    { key: "reports", icon: <FileDoneOutlined />, label: "报告与审计" }
  ];

  return (
    <Layout className="app-shell">
      <Sider width={248} className="app-sider" breakpoint="lg" collapsedWidth={0}>
        <div className="brand-block">
          <Avatar size={40} icon={<RobotOutlined />} />
          <div>
            <Text className="brand-kicker">Beginner Mode</Text>
            <Title level={4}>AI Ops</Title>
          </div>
        </div>
        <Menu
          mode="inline"
          defaultSelectedKeys={["start"]}
          items={menuItems}
          onClick={({ key }) => document.getElementById(key)?.scrollIntoView({ behavior: "smooth" })}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <div>
            <Text type="secondary">面向刚入门用户的服务器运维面板</Text>
            <Title level={3}>先点“一键体检”，再看 AI 给你的下一步</Title>
          </div>
          <Space size={12}>
            <Badge status={health?.status === "success" ? "success" : "processing"} text="本机模式" />
            <Tag icon={<SafetyCertificateOutlined />} color="blue">
              {identity.actor_id}
            </Tag>
          </Space>
        </Header>
        <Content className="workspace">
          {apiError ? (
            <Alert
              className="section"
              type="error"
              showIcon
              title="接口请求失败"
              description={apiError}
            />
          ) : null}

          <section id="start" className="section">
            <Card variant="outlined" className="starter-panel">
              <Row gutter={[24, 24]} align="middle">
                <Col xs={24} xl={11}>
                  <Space orientation="vertical" size={18}>
                    <Tag color="green" icon={<CheckCircleOutlined />}>
                      新手展示策略
                    </Tag>
                    <div>
                      <Title level={1}>不用懂命令，先让 AI 看一遍服务器</Title>
                      <Paragraph type="secondary">
                        这个页面把运维动作拆成三件事：体检服务器、解释风险、人工确认修复。
                        你先看结论，再决定要不要深入到工具和审计记录。
                      </Paragraph>
                    </div>
                    <Space wrap>
                      <Button
                        type="primary"
                        size="large"
                        icon={<ThunderboltOutlined />}
                        loading={busy}
                        onClick={() => void runDiagnosis(QUICK_PROMPTS[0])}
                      >
                        一键体检服务器
                      </Button>
                      <Button icon={<ReloadOutlined />} onClick={() => void refresh(true)}>
                        刷新状态
                      </Button>
                    </Space>
                  </Space>
                </Col>
                <Col xs={24} xl={13}>
                  <Steps
                    orientation="vertical"
                    current={events.length > 0 ? 3 : 0}
                    items={[
                      {
                        title: "先登录",
                        content: IS_DEV
                          ? "本地调试账号已填好，可直接进入。"
                          : "使用首次初始化时创建的管理员账号安全登录。"
                      },
                      {
                        title: "点一键体检",
                        content: "AI 会调用只读工具，不会自动改服务器。"
                      },
                      {
                        title: "看一句话结论",
                        content: "先判断是否紧急，再看证据。"
                      },
                      {
                        title: "需要修复再确认",
                        content: "重启、修改配置等动作必须人工批准。"
                      }
                    ]}
                  />
                </Col>
              </Row>
            </Card>
          </section>

          <section id="overview" className="section">
            <SectionTitle
              icon={<DashboardOutlined />}
              title="服务器概览"
              subtitle="先给新手看能不能用、哪里可能有风险，再把细节放到下面。"
            />
            <Row gutter={[16, 16]}>
              <Col xs={24} md={12} xl={6}>
                <MetricCard
                  loading={loading}
                  title="当前健康度"
                  value={`${healthScore || "--"} 分`}
                  icon={<SafetyCertificateOutlined />}
                  tone="green"
                  footer={
                    <Progress
                      percent={healthScore}
                      showInfo={false}
                      status={healthScore >= 80 ? "success" : "active"}
                    />
                  }
                />
              </Col>
              <Col xs={24} md={12} xl={6}>
                <MetricCard
                  loading={loading}
                  title="已接入工具"
                  value={`${tools.length || "--"} 个`}
                  icon={<ToolOutlined />}
                  tone="blue"
                  footer="包括系统、磁盘、端口、服务和 Docker 检查。"
                />
              </Col>
              <Col xs={24} md={12} xl={6}>
                <MetricCard
                  loading={loading}
                  title="执行用户"
                  value={runner ? runner.runner_user : "--"}
                  icon={<CloudServerOutlined />}
                  tone="purple"
                  footer={runner ? `uid ${runner.effective_uid}，绑定 ${runner.bind_host}` : "等待加载"}
                />
              </Col>
              <Col xs={24} md={12} xl={6}>
                <MetricCard
                  loading={loading}
                  title="AI 模式"
                  value={runner?.llm_mode || "mock"}
                  icon={<RobotOutlined />}
                  tone="orange"
                  footer={
                    runner?.llm_mode && runner.llm_mode !== "mock"
                      ? "已接入真实大模型，诊断结论由模型结合工具结果生成。"
                      : "当前先用模拟诊断链路跑通产品。"
                  }
                />
              </Col>
            </Row>

            <ServerBasicInfoCard health={health} runner={runner} loading={loading} />

            <Row gutter={[16, 16]} className="section-inner">
              <Col xs={24} lg={10}>
                <Card title="小白版解释" variant="outlined" className="fill-card">
                  <div className="explain-list">
                    {[
                      {
                        icon: <CloudServerOutlined />,
                        title: "服务器",
                        text: "就是你的网站、接口、数据库运行的那台机器。"
                      },
                      {
                        icon: <ToolOutlined />,
                        title: "工具",
                        text: "就是 AI 能调用的检查命令，比如看磁盘、端口、服务状态。"
                      },
                      {
                        icon: <AuditOutlined />,
                        title: "审计",
                        text: "记录谁在什么时间做了什么，方便追溯。"
                      }
                    ].map((item) => (
                      <div className="explain-item" key={item.title}>
                        <Avatar icon={item.icon} />
                        <div>
                          <Text strong>{item.title}</Text>
                          <div>
                            <Text type="secondary">{item.text}</Text>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </Card>
              </Col>
              <Col xs={24} lg={14}>
                <Card title="当前可检查的能力" variant="outlined" className="fill-card">
                  {tools.length ? (
                    <Space orientation="vertical" size={12} className="full-width">
                      {Object.entries(groupedTools).map(([category, rows]) => (
                        <div className="tool-group" key={category}>
                          <Text strong>{category}</Text>
                          <Space wrap>
                            {rows.map((tool) => (
                              <Tag
                                key={tool.name}
                                color={
                                  !tool.enabled
                                    ? "default"
                                    : tool.approval_required
                                      ? "orange"
                                      : "processing"
                                }
                                title={
                                  tool.enabled
                                    ? undefined
                                    : disabledReasonLabel(tool.disabled_reason_code)
                                }
                              >
                                {readableTool(tool.name)}
                                {!tool.enabled
                                  ? ` · 未开放：${disabledReasonLabel(tool.disabled_reason_code)}`
                                  : ""}
                              </Tag>
                            ))}
                          </Space>
                        </div>
                      ))}
                    </Space>
                  ) : (
                    <Empty description="还没有加载到工具" />
                  )}
                </Card>
              </Col>
            </Row>
          </section>

          <section id="diagnosis" className="section">
            <SectionTitle
              icon={<RobotOutlined />}
              title="AI 诊断台"
              subtitle="像聊天一样描述问题，AI 会把检查步骤和证据展示出来。"
            />
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={14}>
                <Card variant="outlined" className="diagnosis-card">
                  <Space orientation="vertical" size={16} className="full-width">
                    <Space wrap>
                      {QUICK_PROMPTS.map((prompt) => (
                        <Button
                          key={prompt}
                          icon={<PlayCircleOutlined />}
                          onClick={() => {
                            setQuestion(prompt);
                            void runDiagnosis(prompt);
                          }}
                        >
                          {prompt}
                        </Button>
                      ))}
                    </Space>
                    <Bubble.List
                      className="chat-list"
                      autoScroll
                      role={{
                        user: {
                          placement: "end",
                          avatar: <Avatar icon={<CloudServerOutlined />} />,
                          variant: "filled"
                        },
                        ai: {
                          placement: "start",
                          avatar: <Avatar icon={<RobotOutlined />} />,
                          variant: "outlined"
                        }
                      }}
                      items={buildBubbleItems({
                        question,
                        busy,
                        assistantMarkdown,
                        finalAnswer,
                        feedback,
                        onFeedback: setFeedback,
                        onRetry: () => void runDiagnosis(question)
                      })}
                    />
                    <Sender
                      value={question}
                      loading={busy}
                      placeholder="例如：网站打不开、磁盘是不是满了、Docker 容器异常"
                      submitType="enter"
                      autoSize={{ minRows: 2, maxRows: 5 }}
                      onChange={(value) => setQuestion(value)}
                      onSubmit={(value) => void runDiagnosis(value)}
                      onCancel={() => setBusy(false)}
                    />
                  </Space>
                </Card>
              </Col>
              <Col xs={24} xl={10}>
                  <Card
                    variant="outlined"
                    title="AI 检查过程"
                    extra={
                      <Tag color={busy ? "processing" : events.length ? "success" : "default"}>
                        {busy ? "运行中" : events.length ? "已完成" : "未开始"}
                      </Tag>
                    }
                    className="fill-card"
                  >
                  {thoughtItems.length ? (
                    <ThoughtChain
                      items={thoughtItems}
                      defaultExpandedKeys={thoughtItems.map((item) => String(item.key))}
                    />
                  ) : (
                    <Empty description="点击一键体检后，这里会显示 AI 调用了哪些检查工具。" />
                  )}
                  {approvalEvent ? (
                    <>
                      <Divider />
                      <Alert
                        type="warning"
                        showIcon
                        title={approvalDisabledReason ? "建议动作当前未开放" : "有动作需要你确认"}
                        description={
                          approvalDisabledReason
                            ? `AI 建议：${readableTool(approvalToolName)}。当前不可执行：${approvalDisabledReason}。`
                            : `AI 建议：${readableTool(approvalToolName)}。确认前不会执行。`
                        }
                        action={
                          <Button
                            type="primary"
                            danger
                            disabled={Boolean(approvalDisabledReason)}
                            title={approvalDisabledReason || undefined}
                            onClick={confirmApproval}
                          >
                            {approvalDisabledReason ? "当前不可执行" : "批准执行"}
                          </Button>
                        }
                      />
                    </>
                  ) : null}
                </Card>
              </Col>
            </Row>
          </section>

          <section id="tasks" className="section">
            <SectionTitle
              icon={<ThunderboltOutlined />}
              title="任务事件"
              subtitle="把长任务拆成任务、子步骤和事件流，后续执行层都挂到这里。"
            />
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={14}>
                <Card
                  title="任务列表"
                  variant="outlined"
                  className="fill-card"
                  extra={
                    <Space wrap>
                      {IS_DEV ? (
                        <Button
                          type="primary"
                          icon={<PlayCircleOutlined />}
                          loading={taskBusy}
                          onClick={() => void runMockTask()}
                        >
                          生成模拟任务
                        </Button>
                      ) : null}
                      <Button
                        icon={<WarningOutlined />}
                        disabled={!latestActiveTask}
                        loading={taskBusy}
                        onClick={() => void cancelLatestTask()}
                      >
                        取消当前任务
                      </Button>
                    </Space>
                  }
                >
                  <Table<OpsTask>
                    rowKey="id"
                    size="middle"
                    pagination={{ pageSize: 6 }}
                    columns={buildTaskColumns({
                      tools,
                      onApprove: approveTaskApproval,
                      onDeny: denyTaskApproval,
                      onShowEvents: showTaskEvents
                    })}
                    dataSource={tasks}
                    locale={{
                      emptyText: IS_DEV
                        ? "还没有任务，先生成一个模拟任务。"
                        : "还没有任务。执行运维流程后，任务会显示在这里。"
                    }}
                    onRow={(record) => ({
                      onClick: () => void showTaskEvents(record)
                    })}
                  />
                </Card>
              </Col>
              <Col xs={24} xl={10}>
                <Card
                  title="事件流明细"
                  variant="outlined"
                  className="fill-card"
                  extra={<Tag color={taskEvents.length ? "processing" : "default"}>{taskEvents.length} 条</Tag>}
                >
                  {taskEvents.length ? (
                    <div className="event-stream">
                      {taskEvents.map((event) => (
                        <div className="event-row" key={event.id}>
                          <div className="event-row-header">
                            <Space size={8} wrap>
                              <Tag color={taskEventColor(event.type)}>#{event.sequence}</Tag>
                              <Text strong>{taskEventLabel(event.type)}</Text>
                            </Space>
                            <Text type="secondary">{formatTime(event.created_at)}</Text>
                          </div>
                          <Text type="secondary">{summarizePayload(event.payload)}</Text>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty description="点击任务行后，这里会显示事件流。" />
                  )}
                </Card>
              </Col>
            </Row>
          </section>

          <section id="reports" className="section">
            <SectionTitle
              icon={<FileDoneOutlined />}
              title="报告与审计"
              subtitle="报告给你看结果，审计给团队看过程。"
            />
            <Row gutter={[16, 16]}>
              <Col xs={24} xl={12}>
                <Card title="巡检报告" variant="outlined" className="fill-card">
                  <Table<Report>
                    rowKey="id"
                    size="middle"
                    pagination={{ pageSize: 5 }}
                    columns={reportColumns}
                    dataSource={reports}
                    locale={{ emptyText: "还没有报告，先跑一次一键体检。" }}
                  />
                </Card>
              </Col>
              <Col xs={24} xl={12}>
                <Card title="审计记录" variant="outlined" className="fill-card">
                  <Table<AuditRecord>
                    rowKey="id"
                    size="middle"
                    pagination={{ pageSize: 6 }}
                    columns={auditColumns}
                    dataSource={audit}
                    locale={{ emptyText: "还没有审计记录。" }}
                  />
                </Card>
              </Col>
            </Row>
          </section>
        </Content>
      </Layout>
    </Layout>
  );
}

function SectionTitle({
  icon,
  title,
  subtitle
}: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="section-title">
      <Space size={12} align="start">
        <Avatar icon={icon} />
        <div>
          <Title level={3}>{title}</Title>
          <Text type="secondary">{subtitle}</Text>
        </div>
      </Space>
    </div>
  );
}

function MetricCard({
  loading,
  title,
  value,
  icon,
  tone,
  footer
}: {
  loading: boolean;
  title: string;
  value: string;
  icon: React.ReactNode;
  tone: "green" | "blue" | "purple" | "orange";
  footer: React.ReactNode;
}) {
  return (
    <Card loading={loading} variant="outlined" className={`metric-card metric-${tone}`}>
      <Statistic title={title} value={value} prefix={icon} />
      <div className="metric-footer">{footer}</div>
    </Card>
  );
}

function ServerBasicInfoCard({
  health,
  runner,
  loading
}: {
  health: ToolResult | null;
  runner: RunnerDiagnostics | null;
  loading: boolean;
}) {
  const data = health?.data || {};
  const load = readNumberList(data.load);
  const diskPercent = readNumber(data, "root_disk_percent");
  const memoryPercent = readNumber(data, "memory_used_percent");

  return (
    <Card
      loading={loading}
      variant="outlined"
      className="server-info-card"
      title="服务器基本信息"
      extra={<Tag color="processing">实时数据</Tag>}
    >
      <Descriptions
        size="small"
        column={{ xs: 1, sm: 2, lg: 3 }}
        items={[
          { key: "hostname", label: "主机名", children: readText(data, "hostname") },
          {
            key: "system",
            label: "操作系统",
            children: `${readText(data, "system")} ${readText(data, "release")}`
          },
          { key: "platform", label: "平台", children: readText(data, "platform") },
          { key: "machine", label: "架构", children: readText(data, "machine") },
          { key: "cpu", label: "CPU 核心", children: `${readNumber(data, "cpu_count") ?? "--"} 核` },
          {
            key: "load",
            label: "系统负载",
            children: load.length ? load.map((item) => item.toFixed(2)).join(" / ") : "--"
          },
          {
            key: "disk",
            label: "根磁盘",
            children: `${formatGib(readNumber(data, "root_disk_used_gib"))} / ${formatGib(
              readNumber(data, "root_disk_total_gib")
            )}`
          },
          {
            key: "memory",
            label: "内存",
            children:
              readNumber(data, "memory_total_gib") === null
                ? "当前系统未提供"
                : `${formatGib(readNumber(data, "memory_available_gib"))} 可用 / ${formatGib(
                    readNumber(data, "memory_total_gib")
                  )}`
          },
          {
            key: "runner",
            label: "运行身份",
            children: runner ? `${runner.runner_user} / uid ${runner.effective_uid}` : "--"
          },
          { key: "bind", label: "监听地址", children: runner?.bind_host || "--" },
          {
            key: "agent",
            label: "执行代理",
            children: runner ? `${runner.agent_id} / ${runner.agent_transport}` : "--"
          },
          {
            key: "command",
            label: "命令边界",
            children: runner
              ? `${runner.shell_enabled ? "允许 shell" : "仅 argv"}，${runner.redaction_enabled ? "已启用脱敏" : "未脱敏"}`
              : "--"
          },
          { key: "python", label: "Python", children: readText(data, "python_version") },
          {
            key: "updated",
            label: "更新时间",
            children: health?.started_at ? formatTime(health.started_at) : "--"
          }
        ]}
      />
      <Row gutter={[12, 12]} className="server-health-bars">
        <Col xs={24} md={12}>
          <div className="server-bar">
            <Space>
              <CloudServerOutlined />
              <Text strong>根磁盘使用率</Text>
            </Space>
            <Progress
              percent={diskPercent ?? 0}
              status={diskPercent !== null && diskPercent >= 85 ? "exception" : "active"}
            />
          </div>
        </Col>
        <Col xs={24} md={12}>
          <div className="server-bar">
            <Space>
              <DashboardOutlined />
              <Text strong>内存使用率</Text>
            </Space>
            {memoryPercent === null ? (
              <Text type="secondary">当前系统暂未提供实时可用内存</Text>
            ) : (
              <Progress
                percent={memoryPercent}
                status={memoryPercent >= 85 ? "exception" : "active"}
              />
            )}
          </div>
        </Col>
      </Row>
    </Card>
  );
}

function buildBubbleItems({
  question,
  busy,
  assistantMarkdown,
  finalAnswer,
  feedback,
  onFeedback,
  onRetry
}: {
  question: string;
  busy: boolean;
  assistantMarkdown: string;
  finalAnswer: string;
  feedback: FeedbackValue;
  onFeedback: (value: FeedbackValue) => void;
  onRetry: () => void;
}): BubbleItem[] {
  return [
    {
      key: "user-question",
      role: "user",
      content: question || "帮我巡检这台服务器"
    },
    {
      key: "assistant-answer",
      role: "ai",
      loading: busy,
      content: <XMarkdown content={assistantMarkdown} />,
      footer: () => (
        <Actions
          variant="borderless"
          items={[
            {
              key: "copy",
              actionRender: () => <Actions.Copy text={finalAnswer} />
            },
            {
              key: "feedback",
              actionRender: () => <Actions.Feedback value={feedback} onChange={onFeedback} />
            },
            {
              key: "retry",
              icon: <ReloadOutlined />,
              label: "重新诊断",
              onItemClick: onRetry
            }
          ]}
        />
      )
    }
  ];
}

function buildAssistantMarkdown(events: DiagnosisEvent[]) {
  if (!events.length) {
    return [
      "### 我会怎么帮你看",
      "",
      "1. 先做基础体检：CPU、内存、磁盘、进程、端口和系统服务。",
      "2. 只读检查会直接执行，不会修改服务器。",
      "3. 如果需要重启容器或修改配置，我会先让你确认。",
      "",
      "**下一步：**点击“一键体检服务器”。"
    ].join("\n");
  }

  const finalEvent = lastEventOfType(events, "final_answer");
  const summary =
    typeof finalEvent?.payload.summary === "string"
      ? finalEvent.payload.summary
      : "诊断已完成。";
  const evidence = Array.isArray(finalEvent?.payload.evidence)
    ? finalEvent.payload.evidence.slice(0, 4).map((item: unknown) => `- ${String(item)}`)
    : [];
  const actions = Array.isArray(finalEvent?.payload.recommended_actions)
    ? finalEvent.payload.recommended_actions
        .slice(0, 4)
        .map((item: unknown) => `- ${formatRecommendedAction(item)}`)
    : [];

  return [
    "### 结论",
    "",
    summary,
    "",
    evidence.length ? "### 主要证据" : "",
    ...evidence,
    "",
    "### 新手建议",
    "",
    ...(actions.length
      ? actions
      : [
          "- 先看有没有红色或橙色风险提示。",
          "- 只读检查可以放心查看；修复动作需要人工批准。",
          "- 需要给同事同步时，直接看下方巡检报告和审计记录。"
        ])
  ]
    .filter(Boolean)
    .join("\n");
}

function buildThoughtItems(events: DiagnosisEvent[], busy: boolean): ThoughtChainItemType[] {
  const items: ThoughtChainItemType[] = events.map((event) => {
    const label = EVENT_LABELS[event.type] || {
      title: event.type,
      description: "系统事件"
    };
    return {
      key: `${event.sequence}-${event.type}`,
      title: label.title,
      description: buildEventDescription(event, label.description),
      status: event.type === "approval_required" ? "error" : "success",
      content: <EventPayload event={event} />,
      collapsible: true
    } satisfies ThoughtChainItemType;
  });

  if (busy) {
    items.push({
      key: "running",
      title: "AI 正在继续检查",
      description: "请稍等，诊断事件会陆续出现。",
      status: "loading",
      blink: true
    });
  }

  return items;
}

function lastEventOfType(events: DiagnosisEvent[], type: string) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === type) return events[index];
  }
  return undefined;
}

function EventPayload({ event }: { event: DiagnosisEvent }) {
  if (event.type === "plan" && Array.isArray(event.payload.steps)) {
    return (
      <div className="mini-list">
        {event.payload.steps.map((step) => (
          <div className="mini-list-item" key={String(step)}>
            {translatePlanStep(String(step))}
          </div>
        ))}
      </div>
    );
  }

  if (event.type === "tool_result") {
    return (
      <Space orientation="vertical" size={4}>
        <Text strong>{readableTool(String(event.payload.tool_name || "检查工具"))}</Text>
        <Text type="secondary">{String(event.payload.output_summary || "已完成检查")}</Text>
        {typeof event.payload.output_ref === "string" ? (
          <Tag color="default">{event.payload.output_ref}</Tag>
        ) : null}
      </Space>
    );
  }

  if (event.type === "approval_required") {
    return (
      <Space orientation="vertical" size={4}>
        <Text strong>{readableTool(String(event.payload.tool_name || "修复动作"))}</Text>
        <Text type="secondary">目标：{String(event.payload.target || "当前服务器")}</Text>
        <Tag color="orange">需要人工批准</Tag>
      </Space>
    );
  }

  if (event.type === "final_answer") {
    return <Text>{String(event.payload.summary || "已给出诊断结论")}</Text>;
  }

  return <Text type="secondary">{summarizePayload(event.payload)}</Text>;
}

function buildEventDescription(event: DiagnosisEvent, fallback: string) {
  if (event.type === "tool_call") {
    return readableTool(String(event.payload.tool_name || ""));
  }
  if (event.type === "tool_result") {
    return String(event.payload.output_summary || fallback);
  }
  if (event.type === "approval_required") {
    return `需要确认：${readableTool(String(event.payload.tool_name || ""))}`;
  }
  return fallback;
}

function groupTools(tools: Tool[]) {
  return tools.reduce<Record<string, Tool[]>>((groups, tool) => {
    const key = categoryLabel(tool.category);
    groups[key] = groups[key] || [];
    groups[key].push(tool);
    return groups;
  }, {});
}

function categoryLabel(category: string) {
  const labels: Record<string, string> = {
    host: "主机巡检",
    system: "系统基础",
    network: "网络端口",
    docker: "容器",
    nginx: "Nginx",
    systemd: "系统服务",
    security: "安全登录"
  };
  return labels[category] || category || "其他";
}

function readableTool(name: string) {
  return TOOL_LABELS[name] || name;
}

function disabledReasonLabel(reasonCode?: string | null) {
  if (reasonCode === "not_implemented") {
    return "生产执行适配器尚未实现（not_implemented）";
  }
  return reasonCode ? `原因代码：${reasonCode}` : "后端未提供可用原因";
}

function taskToolName(task: OpsTask) {
  if (task.type === "systemd.approval") {
    return task.title.toLowerCase().includes("reload") ? "systemd.reload" : "systemd.restart";
  }

  const directMappings: Record<string, string> = {
    "database.restore": "database.restore",
    "docker.restart": "docker.restart",
    "file.write": "file.write",
    "firewall.apply": "firewall.apply",
    "nginx.reload": "systemd.reload"
  };
  return directMappings[task.type] || "";
}

function translatePlanStep(step: string) {
  const map: Record<string, string> = {
    "Collect host health": "查看服务器整体健康状态",
    "Inspect disk usage": "检查磁盘是否快满",
    "Inspect processes": "检查有没有异常高占用进程",
    "Inspect listening ports": "检查哪些端口正在对外服务",
    "Check common service state": "检查常见系统服务状态"
  };
  return map[step] || step;
}

function summarizePayload(payload: Record<string, unknown>) {
  const entries = Object.entries(payload)
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${Array.isArray(value) ? `${value.length} 项` : String(value)}`);
  return entries.join("，") || "已记录事件";
}

function readText(data: Record<string, unknown>, key: string) {
  const value = data[key];
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number") return String(value);
  return "--";
}

function readNumber(data: Record<string, unknown>, key: string) {
  const value = data[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readNumberList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is number => typeof item === "number" && Number.isFinite(item))
    : [];
}

function formatGib(value: number | null) {
  return value === null ? "--" : `${value.toFixed(value >= 10 ? 1 : 2)} GB`;
}

function normalizeApiError(raw: string) {
  try {
    const body = JSON.parse(raw) as { detail?: string };
    return body.detail || raw;
  } catch {
    return raw;
  }
}

function formatTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function formatRecommendedAction(action: unknown) {
  if (typeof action === "string") return action;
  if (action && typeof action === "object") {
    const item = action as Record<string, unknown>;
    const title = item.action || item.title || item.name || "建议";
    const detail = item.detail || item.description || item.reason;
    return detail ? `${String(title)}：${String(detail)}` : String(title);
  }
  return String(action || "暂无建议");
}

function taskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "等待中",
    waiting_approval: "待审批",
    running: "运行中",
    succeeded: "已成功",
    failed: "已失败",
    cancelled: "已取消",
    rolled_back: "已回滚"
  };
  return labels[status] || status;
}

function taskStatusColor(status: string) {
  const colors: Record<string, string> = {
    pending: "default",
    waiting_approval: "orange",
    running: "processing",
    succeeded: "success",
    failed: "error",
    cancelled: "warning",
    rolled_back: "purple"
  };
  return colors[status] || "default";
}

function riskLabel(risk: string) {
  const labels: Record<string, string> = {
    read: "只读",
    mutating: "变更",
    destructive: "高危"
  };
  return labels[risk] || risk;
}

function riskColor(risk: string) {
  if (risk === "destructive") return "red";
  if (risk === "mutating") return "orange";
  return "green";
}

function taskEventLabel(type: string) {
  const labels: Record<string, string> = {
    task_created: "任务已创建",
    command_started: "命令开始",
    stdout: "执行输出",
    command_finished: "命令结束",
    final_report: "最终报告",
    cancel_requested: "取消请求"
  };
  return labels[type] || type;
}

function taskEventColor(type: string) {
  const colors: Record<string, string> = {
    task_created: "blue",
    command_started: "processing",
    stdout: "default",
    command_finished: "green",
    final_report: "success",
    cancel_requested: "orange"
  };
  return colors[type] || "default";
}

function buildTaskColumns({
  tools,
  onApprove,
  onDeny,
  onShowEvents
}: {
  tools: Tool[];
  onApprove: (task: OpsTask) => void;
  onDeny: (task: OpsTask) => void;
  onShowEvents: (task: OpsTask) => void;
}): TableProps<OpsTask>["columns"] {
  return [
    {
      title: "任务",
      dataIndex: "title",
      render: (value: string, record) => (
        <Space orientation="vertical" size={2}>
          <Text strong>{value}</Text>
          <Text type="secondary">{record.reason}</Text>
        </Space>
      )
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (value: string) => <Tag color={taskStatusColor(value)}>{taskStatusLabel(value)}</Tag>
    },
    {
      title: "对象",
      dataIndex: "resource"
    },
    {
      title: "风险",
      dataIndex: "risk_level",
      render: (value: string) => <Tag color={riskColor(value)}>{riskLabel(value)}</Tag>
    },
    {
      title: "时间",
      dataIndex: "created_at",
      render: formatTime
    },
    {
      title: "操作",
      key: "action",
      render: (_: unknown, record) => {
        const toolName = taskToolName(record);
        const tool = tools.find((row) => row.name === toolName);
        const disabledReason = tool && !tool.enabled
          ? disabledReasonLabel(tool.disabled_reason_code)
          : "";

        return record.status === "waiting_approval" && record.approval_id ? (
          <Space wrap>
            <Button
              size="small"
              type="primary"
              disabled={Boolean(disabledReason)}
              title={disabledReason || undefined}
              onClick={() => onApprove(record)}
            >
              {disabledReason ? "当前不可执行" : "批准"}
            </Button>
            <Button size="small" danger onClick={() => onDeny(record)}>
              拒绝
            </Button>
          </Space>
        ) : (
          <Button size="small" onClick={() => onShowEvents(record)}>
            查看事件
          </Button>
        );
      }
    }
  ];
}

const reportColumns: TableProps<Report>["columns"] = [
  {
    title: "报告类型",
    dataIndex: "type",
    render: (value: string) => (
      <Tag color="blue">{value === "inspection" ? "巡检" : value === "diagnosis" ? "诊断" : value}</Tag>
    )
  },
  {
    title: "健康分",
    dataIndex: "health_score",
    render: (value: number) => <Progress percent={value} size="small" />
  },
  {
    title: "建议",
    dataIndex: "recommended_actions",
    render: (actions: unknown[]) =>
      Array.isArray(actions) && actions.length ? formatRecommendedAction(actions[0]) : "暂无建议"
  },
  {
    title: "时间",
    dataIndex: "created_at",
    render: formatTime
  }
];

const auditColumns: TableProps<AuditRecord>["columns"] = [
  {
    title: "动作",
    dataIndex: "event_type",
    render: (value: string) => <Text strong>{value}</Text>
  },
  {
    title: "对象",
    dataIndex: "resource",
    render: (value: string) => readableTool(value)
  },
  {
    title: "风险",
    dataIndex: "risk_level",
    render: (value: string) => (
      <Tag color={value === "read" ? "green" : value === "mutating" ? "orange" : "red"}>
        {value === "read" ? "只读" : value}
      </Tag>
    )
  },
  {
    title: "时间",
    dataIndex: "created_at",
    render: formatTime
  }
];

export function Application() {
  return (
    <XProvider
      locale={{ ...zhCN, ...zhCNX }}
      theme={{
        token: {
          colorPrimary: "#1d4ed8",
          borderRadius: 8,
          fontFamily:
            'Inter, "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif'
        },
        components: {
          Card: { borderRadiusLG: 8 },
          Button: { borderRadius: 8 }
        }
      }}
    >
      <AntApp>
        <App />
      </AntApp>
    </XProvider>
  );
}

const rootElement = document.getElementById("root");
if (rootElement) {
  createRoot(rootElement).render(<Application />);
}
