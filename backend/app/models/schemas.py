from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    READ = "read"
    MUTATING = "mutating"
    DESTRUCTIVE = "destructive"


class CapabilityExecutorKind(str, Enum):
    LOCAL_PROCESS = "local_process"
    LOCAL_AGENT = "local_agent"
    REMOTE_AGENT = "remote_agent"
    MOCK = "mock"


class TaskStatus(str, Enum):
    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


class ToolStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    NO_DATA = "no_data"
    APPROVAL_REQUIRED = "approval_required"
    DISABLED = "disabled"


class Tool(BaseModel):
    name: str
    category: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.READ
    approval_required: bool = False
    timeout_seconds: int = 10
    enabled: bool = True
    disabled_reason_code: str | None = None


class Capability(BaseModel):
    id: str
    name: str
    display_name: str
    category: str
    description: str
    ai_description: str
    ui_description: str
    version: str = "0.1.0"
    enabled: bool = True
    disabled_reason_code: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.READ
    approval_required: bool = False
    supports_dry_run: bool = False
    supports_rollback: bool = False
    default_timeout_seconds: int = 10
    resource_scope: str = "local-server"
    executor_kind: CapabilityExecutorKind = CapabilityExecutorKind.LOCAL_PROCESS
    audit_fields: list[str] = Field(
        default_factory=lambda: [
            "actor_id",
            "auth_session_id",
            "capability_id",
            "resource",
            "risk_level",
            "params_hash",
            "task_id",
            "event_id",
        ]
    )


class ToolResult(BaseModel):
    tool_name: str
    status: ToolStatus
    started_at: datetime
    duration_ms: int
    output_summary: str
    output_ref: str | None = None
    redacted: bool = True
    error: str | None = None
    reason_code: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SessionIdentity(BaseModel):
    actor_id: str
    session_id: str
    role: Literal["admin"] = "admin"
    created_at: datetime
    expires_at: datetime
    must_change_password: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    identity: SessionIdentity
    must_change_password: bool


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class SetupEnrollmentRequest(BaseModel):
    token: str = Field(min_length=1)
    username: str = Field(default="admin", min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class DiagnosisSession(BaseModel):
    id: str
    status: Literal["running", "completed", "error"]
    user_question: str
    created_at: datetime
    updated_at: datetime


class DiagnosisSessionCreate(BaseModel):
    question: str


class DiagnosisEvent(BaseModel):
    type: Literal[
        "plan",
        "tool_call",
        "tool_result",
        "approval_required",
        "final_answer",
        "error",
    ]
    session_id: str
    sequence: int
    payload: dict[str, Any]
    created_at: datetime


class OpsSubtask(BaseModel):
    id: str
    task_id: str
    title: str
    status: TaskStatus
    sequence: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rollback_step: bool = False
    output_ref: str | None = None
    failure_reason: str | None = None


class OpsTask(BaseModel):
    id: str
    type: str
    title: str
    status: TaskStatus
    actor_id: str
    resource: str
    risk_level: RiskLevel
    reason: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    failure_reason: str | None = None
    approval_id: str | None = None


class OpsTaskCreate(BaseModel):
    type: str = "mock.long_running"
    title: str = "模拟长任务"
    resource: str = "local-server"
    risk_level: RiskLevel = RiskLevel.READ
    reason: str = "验证任务事件底座"
    simulate: bool = False


class OpsEvent(BaseModel):
    id: str
    scope: Literal["diagnosis", "task", "approval", "agent"]
    scope_id: str
    sequence: int
    type: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalPolicy(BaseModel):
    id: str
    capability_id: str
    risk_level: RiskLevel
    requires_approval: bool
    requires_second_approval: bool = False
    reason: str


class Approval(BaseModel):
    id: str
    auth_session_id: str
    diagnosis_session_id: str | None
    actor_id: str
    nonce: str
    action_fingerprint: str
    tool_name: str
    target: str
    canonical_params: dict[str, Any]
    risk_level: RiskLevel
    expires_at: datetime
    status: Literal["pending", "executing", "approved", "denied", "expired", "failed"]


class AuditRecord(BaseModel):
    id: str
    actor_id: str
    event_type: str
    resource: str
    risk_level: RiskLevel
    status: str
    summary: str
    created_at: datetime


class Report(BaseModel):
    id: str
    type: Literal["inspection", "diagnosis"]
    health_score: int
    risk_items: list[dict[str, Any]]
    evidence_refs: list[str]
    recommended_actions: list[str]
    created_at: datetime


class RunnerDiagnostics(BaseModel):
    runner_user: str
    effective_uid: int
    bind_host: str
    privileged_adapters_enabled: list[str]
    sudo_enabled: bool
    tool_count: int
    llm_mode: str
    agent_id: str = "agent_local"
    agent_transport: Literal["in_process", "unix_socket", "mtls_https"] = "in_process"
    agent_status: Literal["online", "offline", "degraded"] = "online"
    command_runner: str = "LocalAgentClient"
    streaming_enabled: bool = True
    cancel_supported: bool = True
    redaction_enabled: bool = True
    shell_enabled: bool = False


class TerminalCommandRequest(BaseModel):
    argv: list[str]
    reason: str = "只读终端检查"
    timeout_seconds: int = Field(default=10, ge=1, le=30)


class SystemdActionRequest(BaseModel):
    service: str = "nginx"
    action: Literal["restart", "reload"] = "restart"
    reason: str = "需要人工审批的 systemd 动作"


class DockerDiagnoseRequest(BaseModel):
    container: str = "unknown"
    reason: str = "Docker 异常诊断"
    log_tail: int = Field(default=80, ge=1, le=500)


class DockerRestartRequest(BaseModel):
    container: str
    reason: str = "需要人工审批的 Docker 重启动作"


class NginxDiagnoseRequest(BaseModel):
    site_url: str = "http://localhost"
    upstream: str = "default"
    reason: str = "Nginx 502 诊断"


class NginxReloadRequest(BaseModel):
    site: str = "default"
    reason: str = "Nginx 配置 reload 审批"


class SiteProxy(BaseModel):
    id: str
    domain: str
    upstream: str
    nginx_server_name: str
    status: Literal["unknown", "healthy", "degraded", "down"] = "unknown"
    last_checked_at: datetime | None = None


class FileDiffRequest(BaseModel):
    path: str
    content: str
    reason: str = "文件差异预览"


class FileWriteRequest(BaseModel):
    path: str
    content: str
    diff_hash: str
    reason: str = "带备份的文件写入"


class DatabaseBackupRequest(BaseModel):
    name: str = "manual"
    reason: str = "数据库备份"


class DatabaseRestoreRequest(BaseModel):
    backup_id: str = Field(pattern=r"^backup_[0-9a-f]{32}$")
    reason: str = "数据库恢复审批"
    confirm_restore_point: bool = False
    second_approval: bool = False


class BackupArtifact(BaseModel):
    id: str
    type: Literal["file", "database"]
    source_path: str
    backup_path: str
    created_at: datetime
    restore_requires_second_approval: bool = True


class SecretCreateRequest(BaseModel):
    name: str
    value: str
    reason: str = "创建密钥引用"


class FirewallPreflightRequest(BaseModel):
    port: int = Field(ge=1, le=65535)
    protocol: Literal["tcp", "udp"] = "tcp"
    reason: str = "防火墙变更前置检查"


class FirewallApplyRequest(FirewallPreflightRequest):
    confirm_connectivity_guard: bool = False
    confirm_rollback_plan: bool = False


class RemoteAgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    endpoint: str
    transport: Literal["mtls_https", "unix_socket"] = "mtls_https"
    capability_ids: list[str] = Field(default_factory=list)


class HeartbeatLossSimulationRequest(BaseModel):
    agent_id: str
    seconds_since_last_heartbeat: int = Field(default=120, ge=1)


class Agent(BaseModel):
    id: str
    name: str
    transport: Literal["in_process", "unix_socket", "mtls_https"]
    status: Literal["online", "offline", "degraded"]
    capability_ids: list[str]
    last_heartbeat_at: datetime | None = None


class AgentHeartbeat(BaseModel):
    agent_id: str
    status: Literal["online", "degraded"]
    capability_ids: list[str]
    runner_user: str
    effective_uid: int
    created_at: datetime


class PathPolicy(BaseModel):
    id: str
    name: str
    allowed_roots: list[str]
    denied_patterns: list[str]
    write_requires_diff: bool = True
    write_requires_backup: bool = True


class TerminalSession(BaseModel):
    id: str
    actor_id: str
    status: Literal["open", "closed"]
    recording_enabled: bool = True
    command_risk_classifier: str = "default"
    created_at: datetime
    closed_at: datetime | None = None


class CommandRiskClassification(BaseModel):
    command: str
    risk_level: RiskLevel
    action: Literal["allow", "approval_required", "block"]
    reason: str
