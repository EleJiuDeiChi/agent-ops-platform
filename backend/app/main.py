from __future__ import annotations

import json
import hmac
import os
import shutil
import difflib
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.ai.agent_orchestrator import run_llm_diagnosis
from app.ai.mock_orchestrator import run_mock_diagnosis
from app.ai.providers import llm_enabled, model_name
from app.ai.verification import validate_provider_evidence
from app.auth.security import (
    clear_session_cookie,
    configure_settings,
    issue_csrf,
    require_csrf,
    require_identity,
    require_ready_identity,
    set_session_cookie,
    skip_password_change,
)
from app.config import Environment, Settings, load_settings
from app.models.schemas import (
    AgentHeartbeat,
    DiagnosisSessionCreate,
    DockerDiagnoseRequest,
    DockerRestartRequest,
    FileDiffRequest,
    FileWriteRequest,
    FirewallApplyRequest,
    FirewallPreflightRequest,
    HeartbeatLossSimulationRequest,
    DatabaseBackupRequest,
    DatabaseRestoreRequest,
    LoginRequest,
    LoginResponse,
    NginxDiagnoseRequest,
    NginxReloadRequest,
    OpsTaskCreate,
    PasswordChangeRequest,
    RunnerDiagnostics,
    SecretCreateRequest,
    SessionIdentity,
    SetupEnrollmentRequest,
    SystemdActionRequest,
    TerminalCommandRequest,
    RemoteAgentRegisterRequest,
)
from app.runner.agent import AgentCommandRejected, CommandEvent, default_agent_client
from app.runner.command import redact, run_argv
from app.storage import db
from app.support import evaluate_support
from app.tools.registry import get_tool, invoke_tool, list_capabilities, list_tools


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    settings = load_settings(validate=True)
    db.configure(settings.db_path, settings.bootstrap_password_path)
    db.init_db(create_bootstrap_admin=not settings.is_production)
    settings.validate_initialization(initialized=db.has_users())
    application.state.settings = settings
    configure_settings(settings)
    try:
        yield
    finally:
        configure_settings(None)


app = FastAPI(title="AI Native Server Ops Panel", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(load_settings().trusted_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AGENT_HEARTBEATS: dict[str, dict] = {}


def settings_for(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or load_settings()


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


@app.get("/health/live")
def health_live(response: Response) -> dict[str, str]:
    no_store(response)
    return {"status": "alive", "service": "aiops-control-plane", "version": app.version}


@app.get("/version")
def version(request: Request, response: Response) -> dict:
    no_store(response)
    settings = settings_for(request)
    release_declared = settings.release_digest is not None
    return {
        "service": "aiops-control-plane",
        "version": app.version,
        "environment": settings.environment.value,
        "release_digest": settings.release_digest,
        "commit_sha": settings.commit_sha,
        "provenance": {
            "status": "declared" if release_declared else "unverified",
            "reason_code": None if release_declared else "release_digest_unconfigured",
        },
    }


@app.get("/preflight")
def support_preflight(response: Response) -> dict:
    no_store(response)
    return evaluate_support()


@app.get("/health/ready")
def health_ready(request: Request, response: Response) -> dict:
    no_store(response)
    settings = settings_for(request)
    database_ready = db.ping()
    initialized = db.has_users() if database_ready else False
    setup_required = settings.is_production and not initialized
    llm_verification = validate_provider_evidence(settings)
    llm_verified = llm_verification.verified
    ready = database_ready and not setup_required and llm_verified
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not_ready",
        "reasons": [
            reason
            for reason, present in (
                ("database_unavailable", not database_ready),
                ("setup_required", setup_required),
                ("llm_unverified", not llm_verified),
            )
            if present
        ],
        "dependencies": {
            "database": {"status": "ok" if database_ready else "unavailable"},
            "worker": {"status": "disabled_by_release_gate", "required": False},
            "agent": {
                "status": "disabled_by_release_gate",
                "required": False,
            },
            "setup": {
                "status": "required" if setup_required else "complete",
            },
            "llm": {
                "status": "verified" if llm_verified else "unverified",
                "reason_code": None if llm_verified else "llm_unverified",
                "verification_reason_code": llm_verification.reason_code,
            },
        },
    }


@app.get("/api/setup/status")
def setup_status(request: Request, response: Response) -> dict[str, str | bool]:
    no_store(response)
    settings = settings_for(request)
    initialized = db.has_users()
    return {
        "environment": settings.environment.value,
        "initialized": initialized,
        "setup_required": settings.is_production and not initialized,
    }


@app.post("/api/setup/enroll")
def setup_enroll(
    payload: SetupEnrollmentRequest,
    request: Request,
    response: Response,
    _: None = Depends(require_csrf),
) -> dict[str, str]:
    no_store(response)
    settings = settings_for(request)
    if settings.environment is not Environment.PROD:
        raise HTTPException(status_code=409, detail={"reason_code": "setup_not_available"})
    if db.has_users() or db.setup_consumed():
        raise HTTPException(status_code=409, detail={"reason_code": "already_initialized"})
    if not settings.setup_token or settings.setup_token_expires_at is None:
        raise HTTPException(status_code=503, detail={"reason_code": "setup_not_configured"})
    if settings.setup_token_expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail={"reason_code": "setup_token_expired"})
    if not hmac.compare_digest(payload.token, settings.setup_token):
        raise HTTPException(status_code=403, detail={"reason_code": "invalid_setup_token"})
    if not db.complete_initial_setup(payload.username, payload.password):
        raise HTTPException(status_code=409, detail={"reason_code": "already_initialized"})
    return {"status": "initialized"}


@app.get("/api/csrf")
def csrf(response: Response) -> dict[str, str]:
    return {"csrf_token": issue_csrf(response)}


@app.post("/api/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    _: None = Depends(require_csrf),
) -> LoginResponse:
    user = db.get_user(payload.username)
    if not user or not db.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    session = db.create_session(user["id"])
    set_session_cookie(response, session["session_id"])
    identity = SessionIdentity(
        actor_id=user["id"],
        session_id=session["session_id"],
        role=user["role"],
        created_at=session["created_at"],
        expires_at=session["expires_at"],
        must_change_password=bool(user["must_change_password"]) and not skip_password_change(),
    )
    return LoginResponse(identity=identity, must_change_password=identity.must_change_password)


@app.post("/api/logout")
def logout(
    response: Response,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict[str, str]:
    db.revoke_session(identity.session_id)
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/api/me", response_model=SessionIdentity)
def me(identity: SessionIdentity = Depends(require_identity)) -> SessionIdentity:
    return identity


@app.post("/api/me/password", response_model=LoginResponse)
def change_password(
    payload: PasswordChangeRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> LoginResponse:
    user = db.get_user_by_id(identity.actor_id)
    if not user or not db.verify_password(payload.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid credentials")
    if len(payload.new_password) < 12:
        raise HTTPException(status_code=422, detail="new password must be at least 12 characters")
    db.set_password(identity.actor_id, payload.new_password)
    refreshed = SessionIdentity(
        actor_id=identity.actor_id,
        session_id=identity.session_id,
        role=identity.role,
        created_at=identity.created_at,
        expires_at=identity.expires_at,
        must_change_password=False,
    )
    return LoginResponse(identity=refreshed, must_change_password=False)


@app.get("/api/health-snapshot")
def health_snapshot(identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    result = invoke_tool("system.health", {})
    return result.model_dump(mode="json")


@app.get("/api/host/inspection")
def host_inspection(identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    result = invoke_tool("host.inspection", {})
    output_ref = db.store_tool_result(identity.actor_id, result.model_dump(mode="json"))
    result.output_ref = output_ref
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "host_inspection",
            "resource": "local-server",
            "risk_level": "read",
            "status": result.status.value,
            "summary": result.output_summary,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return result.model_dump(mode="json")


@app.get("/api/tools")
def tools(identity: SessionIdentity = Depends(require_identity)) -> list[dict]:
    require_ready_identity(identity)
    return [tool.model_dump(mode="json") for tool in list_tools()]


@app.get("/api/capabilities")
def capabilities(identity: SessionIdentity = Depends(require_identity)) -> list[dict]:
    require_ready_identity(identity)
    return [capability.model_dump(mode="json") for capability in list_capabilities()]


@app.get("/api/safety/contracts")
def safety_contracts(identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    return {
        "path_policies": [
            {
                "id": "default-local-safe-paths",
                "name": "默认本机受控路径",
                "allowed_roots": ["/etc/nginx", "/var/log", "/tmp/aiops"],
                "denied_patterns": ["**/.ssh/**", "**/id_rsa", "**/shadow", "**/*.key"],
                "write_requires_diff": True,
                "write_requires_backup": True,
            }
        ],
        "terminal_session_contract": {
            "recording_enabled": True,
            "ai_auto_execute": False,
            "mutating_requires_approval": True,
            "destructive_default_action": "block",
        },
        "command_risk_classifier": [
            classify_command("df -h"),
            classify_command("systemctl restart nginx"),
            classify_command("rm -rf /var/www"),
        ],
    }


@app.post("/api/tools/{tool_name}/invoke")
async def invoke(
    tool_name: str,
    request: Request,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    tool = get_tool(tool_name)
    if not tool.enabled:
        result = invoke_tool(tool_name, {})
        output_ref = db.store_tool_result(identity.actor_id, result.model_dump(mode="json"))
        result.output_ref = output_ref
        db.insert_audit(
            {
                "id": db.new_id("audit"),
                "actor_id": identity.actor_id,
                "event_type": "capability_invocation_blocked",
                "resource": tool_name,
                "risk_level": tool.risk_level.value,
                "status": result.status.value,
                "summary": result.output_summary,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return result.model_dump(mode="json")
    if tool.approval_required:
        raise HTTPException(status_code=409, detail="approval required")
    params = {}
    if request.headers.get("content-length") not in (None, "0"):
        params = await request.json()
    result = invoke_tool(tool_name, params)
    output_ref = db.store_tool_result(identity.actor_id, result.model_dump(mode="json"))
    result.output_ref = output_ref
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "tool_invocation",
            "resource": tool_name,
            "risk_level": tool.risk_level.value,
            "status": result.status.value,
            "summary": result.output_summary,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return result.model_dump(mode="json")


@app.post("/api/tasks")
def create_task(
    payload: OpsTaskCreate,
    request: Request,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    if settings_for(request).is_production and payload.simulate:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "simulation_disabled_in_production"},
        )
    task = db.create_ops_task(
        identity.actor_id,
        {
            "type": payload.type,
            "title": payload.title,
            "resource": payload.resource,
            "risk_level": payload.risk_level.value,
            "reason": payload.reason,
        },
    )
    if payload.simulate:
        started_at = datetime.now(UTC).isoformat()
        db.update_ops_task_status(task["id"], "running", started_at=started_at)
        db.add_ops_subtask(task["id"], "创建任务上下文", "succeeded", 1)
        db.add_ops_event("task", task["id"], "command_started", {"command": "mock-long-running"})
        db.add_ops_event("task", task["id"], "stdout", {"line": "模拟任务正在输出事件"})
        db.add_ops_event("task", task["id"], "command_finished", {"exit_code": 0})
        db.add_ops_subtask(task["id"], "生成任务报告", "succeeded", 2)
        db.add_ops_event("task", task["id"], "final_report", {"summary": "模拟长任务已完成"})
        db.update_ops_task_status(task["id"], "succeeded", finished_at=datetime.now(UTC).isoformat())
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "task_created",
            "resource": task["id"],
            "risk_level": payload.risk_level.value,
            "status": "succeeded" if payload.simulate else "pending",
            "summary": payload.title,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return task_detail_payload(task["id"])


@app.get("/api/tasks")
def tasks(identity: SessionIdentity = Depends(require_identity)) -> list[dict]:
    require_ready_identity(identity)
    return db.list_ops_tasks()


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: str, identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    task = db.get_ops_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return task_detail_payload(task_id)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    task = db.request_cancel_ops_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "task_cancel_requested",
            "resource": task_id,
            "risk_level": task["risk_level"],
            "status": task["status"],
            "summary": f"取消任务：{task['title']}",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return task_detail_payload(task_id)


@app.get("/api/tasks/{task_id}/events")
def task_events(task_id: str, identity: SessionIdentity = Depends(require_identity)) -> StreamingResponse:
    require_ready_identity(identity)
    if not db.get_ops_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    events = db.list_ops_events(scope="task", scope_id=task_id)

    def stream():
        for event in events:
            yield f"event: {event['type']}\n"
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/events")
def ops_events(
    scope: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    identity: SessionIdentity = Depends(require_identity),
) -> list[dict]:
    require_ready_identity(identity)
    return db.list_ops_events(scope=scope, scope_id=scope_id)


@app.get("/api/sites")
def sites(identity: SessionIdentity = Depends(require_identity)) -> list[dict]:
    require_ready_identity(identity)
    return [
        {
            "id": "site_default",
            "domain": "localhost",
            "upstream": "http://127.0.0.1:8080",
            "nginx_server_name": "localhost",
            "status": "unknown",
            "last_checked_at": None,
        }
    ]


@app.post("/api/security/firewall/preflight")
def firewall_preflight(
    payload: FirewallPreflightRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    return {
        "port": payload.port,
        "protocol": payload.protocol,
        "connectivity_guard": {
            "enabled": False,
            "checks": [],
            "reason_code": "not_implemented",
        },
        "rollback_plan": {
            "available": False,
            "automatic": False,
            "strategy": None,
            "reason_code": "not_implemented",
        },
        "safe_to_request_approval": False,
        "capability_status": "disabled",
        "reason_code": "not_implemented",
        "reason": payload.reason,
    }


@app.post("/api/security/firewall/apply")
def firewall_apply(
    payload: FirewallApplyRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    raise HTTPException(
        status_code=501,
        detail={
            "reason_code": "not_implemented",
            "capability": "firewall.apply",
            "connectivity_guard_available": False,
            "automatic_rollback_available": False,
        },
    )


@app.get("/api/agents/local-socket")
def local_socket_contract(identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    return {
        "transport": "unix_socket",
        "socket_path": "/tmp/aiops-agent.sock",
        "status": "prototype",
        "auth": "local filesystem permissions + future signed nonce",
    }


@app.get("/api/agents/remote-contract")
def remote_agent_contract(identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    return {
        "transport": "mtls_https",
        "required_fields": ["agent_id", "name", "endpoint", "capability_ids"],
        "heartbeat_interval_seconds": 30,
        "offline_after_seconds": 90,
        "auth": "mTLS client certificate plus signed heartbeat payload",
    }


@app.post("/api/agents/register")
def remote_agent_register(
    payload: RemoteAgentRegisterRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    AGENT_HEARTBEATS[payload.agent_id] = {
        "agent_id": payload.agent_id,
        "name": payload.name,
        "endpoint": payload.endpoint,
        "transport": payload.transport,
        "status": "offline",
        "capability_ids": payload.capability_ids,
        "last_heartbeat_at": None,
    }
    return AGENT_HEARTBEATS[payload.agent_id]


@app.post("/api/agents/heartbeat")
def remote_agent_heartbeat(
    payload: AgentHeartbeat,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    AGENT_HEARTBEATS[payload.agent_id] = {
        "agent_id": payload.agent_id,
        "name": payload.agent_id,
        "endpoint": "registered-or-local",
        "transport": "mtls_https",
        "status": payload.status,
        "capability_ids": payload.capability_ids,
        "last_heartbeat_at": payload.created_at.isoformat(),
        "runner_user": payload.runner_user,
        "effective_uid": payload.effective_uid,
    }
    return AGENT_HEARTBEATS[payload.agent_id]


@app.post("/api/agents/simulate-heartbeat-loss")
def simulate_heartbeat_loss(
    payload: HeartbeatLossSimulationRequest,
    request: Request,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    if settings_for(request).is_production:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "simulation_disabled_in_production"},
        )
    status = "offline" if payload.seconds_since_last_heartbeat >= 90 else "degraded"
    row = AGENT_HEARTBEATS.get(payload.agent_id, {"agent_id": payload.agent_id})
    row.update(
        {
            "status": status,
            "last_heartbeat_age_seconds": payload.seconds_since_last_heartbeat,
            "heartbeat_lost": True,
        }
    )
    AGENT_HEARTBEATS[payload.agent_id] = row
    return row


@app.post("/api/files/diff")
def file_diff(
    payload: FileDiffRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    path = allowed_file_path(payload.path)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    diff = "\n".join(
        difflib.unified_diff(
            current.splitlines(),
            payload.content.splitlines(),
            fromfile=str(path),
            tofile=f"{path} (proposed)",
            lineterm="",
        )
    )
    diff_hash = file_diff_hash(path, current, payload.content)
    return {
        "path": str(path),
        "exists": path.exists(),
        "diff": diff,
        "diff_hash": diff_hash,
        "write_requires_backup": True,
        "write_requires_approval": True,
        "reason": payload.reason,
    }


@app.post("/api/files/write")
def file_write(
    payload: FileWriteRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    path = allowed_file_path(payload.path)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    expected_hash = file_diff_hash(path, current, payload.content)
    if not payload.diff_hash or payload.diff_hash != expected_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "file write requires current diff_hash from /api/files/diff",
                "expected_diff_hash": expected_hash,
            },
        )
    task = create_approval_task(
        identity,
        "file.write",
        {"path": str(path), "content": payload.content, "diff_hash": payload.diff_hash},
        target=f"file:{path}",
        task_type="file.write",
        title=f"文件写入审批：{path.name}",
        resource=str(path),
        reason=payload.reason,
    )
    db.add_ops_event(
        "task",
        task["id"],
        "diff_verified",
        {"path": str(path), "diff_hash": payload.diff_hash, "write_requires_backup": True},
    )
    return task_detail_payload(task["id"]) | {"approval": task["approval"]}


@app.get("/api/terminal/audit")
def terminal_audit(identity: SessionIdentity = Depends(require_identity)) -> dict:
    require_ready_identity(identity)
    return {
        "tasks": [task for task in db.list_ops_tasks() if task["type"] == "terminal.read"],
        "audit": [row for row in db.list_audit() if row["event_type"] == "terminal_run"],
    }


@app.post("/api/database/backups")
def database_backup(
    payload: DatabaseBackupRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    source = db.DB_PATH.resolve()
    source.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        db.init_db()
    artifact = create_backup_artifact(source, "database", label=payload.name)
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "database_backup",
            "resource": str(source),
            "risk_level": "read",
            "status": "succeeded",
            "summary": f"数据库备份 {artifact['id']}",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return {"status": "succeeded", "backup_artifact": artifact, "reason": payload.reason}


@app.post("/api/database/restores")
def database_restore(
    payload: DatabaseRestoreRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    if not payload.confirm_restore_point or not payload.second_approval:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "database restore requires restore-point confirmation and second approval",
                "confirm_restore_point": payload.confirm_restore_point,
                "second_approval": payload.second_approval,
            },
        )
    artifact = find_backup_artifact(payload.backup_id)
    task = create_approval_task(
        identity,
        "database.restore",
        {"backup_id": payload.backup_id},
        target=f"database-backup:{payload.backup_id}",
        task_type="database.restore",
        title=f"数据库恢复审批：{payload.backup_id}",
        resource=f"database:{db.DB_PATH}",
        reason=payload.reason,
    )
    db.add_ops_event(
        "task",
        task["id"],
        "restore_point_confirmed",
        {
            "backup_id": payload.backup_id,
            "backup_path": artifact["backup_path"],
            "second_approval": True,
        },
    )
    return task_detail_payload(task["id"]) | {"approval": task["approval"], "backup_artifact": artifact}


@app.post("/api/secrets")
def secret_create(
    payload: SecretCreateRequest,
    request: Request,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    if settings_for(request).is_production:
        raise HTTPException(
            status_code=501,
            detail={"reason_code": "secret_store_not_implemented"},
        )
    secret_id = db.new_id("secret")
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "secret_created",
            "resource": payload.name,
            "risk_level": "mutating",
            "status": "succeeded",
            "summary": "创建密钥引用，原文未写入审计",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return {
        "id": secret_id,
        "name": payload.name,
        "value_ref": f"secret://local/{secret_id}",
        "redacted": True,
        "implementation_status": "development_stub",
        "reason": payload.reason,
    }


@app.post("/api/docker/diagnose")
def docker_diagnose(
    payload: DockerDiagnoseRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    task = db.create_ops_task(
        identity.actor_id,
        {
            "type": "docker.diagnosis",
            "title": f"Docker 异常诊断：{payload.container}",
            "resource": f"docker:{payload.container}",
            "risk_level": "read",
            "reason": payload.reason,
        },
    )
    db.update_ops_task_status(task["id"], "running", started_at=datetime.now(UTC).isoformat())
    results = [
        record_task_tool(task["id"], identity.actor_id, "docker.ps", {}, "列出 Docker 容器", 1),
        record_task_tool(
            task["id"],
            identity.actor_id,
            "docker.inspect",
            {"container": payload.container},
            "读取容器 inspect",
            2,
        ),
        record_task_tool(
            task["id"],
            identity.actor_id,
            "docker.logs",
            {"container": payload.container, "tail": payload.log_tail},
            "读取容器日志",
            3,
        ),
    ]
    db.add_ops_event(
        "task",
        task["id"],
        "final_report",
        {
            "summary": "Docker 诊断证据已收集",
            "statuses": [result.status.value for result in results],
        },
    )
    db.update_ops_task_status(task["id"], "succeeded", finished_at=datetime.now(UTC).isoformat())
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "docker_diagnosis",
            "resource": f"docker:{payload.container}",
            "risk_level": "read",
            "status": "succeeded",
            "summary": "Docker 诊断证据已收集",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return task_detail_payload(task["id"])


@app.post("/api/docker/restart-tasks")
def docker_restart_task(
    payload: DockerRestartRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    return create_approval_task(
        identity,
        "docker.restart",
        {"container": payload.container},
        target=f"container:{payload.container}",
        task_type="docker.restart",
        title=f"Docker 重启审批：{payload.container}",
        resource=f"docker:{payload.container}",
        reason=payload.reason,
    )


@app.post("/api/nginx/diagnose-502")
def nginx_diagnose_502(
    payload: NginxDiagnoseRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    task = db.create_ops_task(
        identity.actor_id,
        {
            "type": "nginx.502_diagnosis",
            "title": f"Nginx 502 诊断：{payload.site_url}",
            "resource": f"nginx:{payload.site_url}",
            "risk_level": "read",
            "reason": payload.reason,
        },
    )
    db.update_ops_task_status(task["id"], "running", started_at=datetime.now(UTC).isoformat())
    config_result = record_task_tool(
        task["id"], identity.actor_id, "nginx.config_test", {}, "执行 nginx -t", 1
    )
    log_result = record_task_tool(
        task["id"], identity.actor_id, "nginx.error_log", {}, "读取 Nginx 错误日志", 2
    )
    db.add_ops_event(
        "task",
        task["id"],
        "config_diff",
        {
            "site_url": payload.site_url,
            "upstream": payload.upstream,
            "status": "not_applied",
            "requires_backup": True,
        },
    )
    db.add_ops_event(
        "task",
        task["id"],
        "rollback_plan",
        {
            "strategy": "backup current nginx config before reload and rollback on failed recheck",
            "available": True,
        },
    )
    db.add_ops_event(
        "task",
        task["id"],
        "final_report",
        {
            "summary": "Nginx 502 诊断证据已收集",
            "config_status": config_result.status.value,
            "log_status": log_result.status.value,
        },
    )
    db.update_ops_task_status(task["id"], "succeeded", finished_at=datetime.now(UTC).isoformat())
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "nginx_502_diagnosis",
            "resource": f"nginx:{payload.site_url}",
            "risk_level": "read",
            "status": "succeeded",
            "summary": "Nginx 502 诊断证据已收集",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return task_detail_payload(task["id"])


@app.post("/api/nginx/reload-tasks")
def nginx_reload_task(
    payload: NginxReloadRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    task = create_approval_task(
        identity,
        "systemd.reload",
        {"service": "nginx"},
        target="systemd:nginx",
        task_type="nginx.reload",
        title=f"Nginx reload 审批：{payload.site}",
        resource=f"nginx:{payload.site}",
        reason=payload.reason,
    )
    db.add_ops_event(
        "task",
        task["id"],
        "rollback_plan",
        {
            "strategy": "restore previous config and skip reload if recheck fails",
            "available": True,
        },
    )
    return task_detail_payload(task["id"]) | {"approval": task["approval"]}


@app.post("/api/systemd/actions")
def systemd_action(
    payload: SystemdActionRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    service = safe_service_name(payload.service)
    tool_name = f"systemd.{payload.action}"
    tool = get_tool(tool_name)
    params = {"service": service}
    approval = {
        "id": db.new_id("appr"),
        "auth_session_id": identity.session_id,
        "diagnosis_session_id": None,
        "actor_id": identity.actor_id,
        "nonce": db.new_id("nonce"),
        "action_fingerprint": fingerprint(
            {
                "actor_id": identity.actor_id,
                "auth_session_id": identity.session_id,
                "tool_name": tool_name,
                "target": f"systemd:{service}",
                "canonical_params": params,
            }
        ),
        "tool_name": tool_name,
        "target": f"systemd:{service}",
        "canonical_params": params,
        "risk_level": tool.risk_level.value,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "status": "pending",
    }
    db.create_approval(approval)
    task = db.create_ops_task(
        identity.actor_id,
        {
            "type": "systemd.approval",
            "title": f"{service} {payload.action} 审批任务",
            "status": "waiting_approval",
            "resource": f"systemd:{service}",
            "risk_level": tool.risk_level.value,
            "reason": payload.reason,
            "approval_id": approval["id"],
        },
    )
    db.add_ops_subtask(task["id"], f"等待审批 {tool_name}", "waiting_approval", 1)
    db.add_ops_event("task", task["id"], "approval_required", approval)
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "approval_requested",
            "resource": f"systemd:{service}",
            "risk_level": tool.risk_level.value,
            "status": "pending",
            "summary": f"等待审批：{tool_name}",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return {**task_detail_payload(task["id"]), "approval": approval}


@app.post("/api/terminal/run")
def terminal_run(
    payload: TerminalCommandRequest,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    command_text = " ".join(payload.argv)
    safe_command_text = redact(command_text)
    classification = classify_terminal_argv(payload.argv)
    if classification["action"] != "allow":
        raise HTTPException(status_code=409, detail=classification)

    task = db.create_ops_task(
        identity.actor_id,
        {
            "type": "terminal.read",
            "title": f"只读终端：{payload.argv[0] if payload.argv else 'empty'}",
            "resource": "local-terminal",
            "risk_level": classification["risk_level"],
            "reason": payload.reason,
        },
    )
    db.update_ops_task_status(task["id"], "running", started_at=datetime.now(UTC).isoformat())

    def on_event(event: CommandEvent) -> None:
        db.add_ops_event("task", task["id"], event.type, event.payload)

    try:
        result = run_argv(
            "terminal.read",
            payload.argv,
            timeout=payload.timeout_seconds,
            on_event=on_event,
        )
    except AgentCommandRejected as exc:
        db.add_ops_event("task", task["id"], "command_rejected", {"message": str(exc)})
        db.update_ops_task_status(
            task["id"],
            "failed",
            finished_at=datetime.now(UTC).isoformat(),
            failure_reason=str(exc),
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    output_ref = db.store_tool_result(identity.actor_id, result.model_dump(mode="json"))
    result.output_ref = output_ref
    final_status = "succeeded" if result.status.value in ("success", "no_data") else "failed"
    db.add_ops_subtask(
        task["id"],
        "执行只读终端命令",
        "succeeded" if final_status == "succeeded" else "failed",
        1,
        output_ref=output_ref,
        failure_reason=result.error,
    )
    db.add_ops_event(
        "task",
        task["id"],
        "final_report",
        {
            "summary": result.output_summary,
            "status": result.status.value,
            "output_ref": output_ref,
        },
    )
    db.update_ops_task_status(
        task["id"],
        final_status,
        finished_at=datetime.now(UTC).isoformat(),
        failure_reason=result.error,
    )
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "terminal_run",
            "resource": safe_command_text,
            "risk_level": classification["risk_level"],
            "status": final_status,
            "summary": result.output_summary,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return task_detail_payload(task["id"])


@app.post("/api/diagnosis/sessions")
def create_diagnosis(
    payload: DiagnosisSessionCreate,
    request: Request,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    settings = settings_for(request)
    verification = validate_provider_evidence(settings)
    if settings.is_production and not verification.verified:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "llm_unverified",
                "verification_reason_code": verification.reason_code,
            },
        )
    if llm_enabled():
        return run_llm_diagnosis(identity.actor_id, identity.session_id, payload.question)
    return run_mock_diagnosis(identity.actor_id, identity.session_id, payload.question)


@app.get("/api/diagnosis/sessions/{session_id}/events")
def diagnosis_events(
    session_id: str,
    identity: SessionIdentity = Depends(require_identity),
) -> StreamingResponse:
    require_ready_identity(identity)
    events = db.list_events(session_id)

    def stream():
        for event in events:
            yield f"event: {event['type']}\n"
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/approvals/{approval_id}/approve")
def approve(
    approval_id: str,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    approval = db.get_approval(approval_id)
    if not approval or approval["status"] != "pending":
        raise HTTPException(status_code=404, detail="pending approval not found")
    if approval["actor_id"] != identity.actor_id or approval["auth_session_id"] != identity.session_id:
        raise HTTPException(status_code=403, detail="approval does not match current session")
    if datetime.fromisoformat(approval["expires_at"]) < datetime.now(UTC):
        db.update_approval_status(approval_id, "expired")
        raise HTTPException(status_code=410, detail="approval expired")
    if not db.transition_approval_status(approval_id, "pending", "executing"):
        raise HTTPException(status_code=404, detail="pending approval not found")
    linked_task = db.get_ops_task_by_approval_id(approval_id)
    try:
        result = invoke_tool(
            approval["tool_name"],
            json.loads(approval["canonical_params"]),
            approved=True,
        )
        output_ref = db.store_tool_result(identity.actor_id, result.model_dump(mode="json"))
        result.output_ref = output_ref
        if result.status.value == "disabled":
            reason_code = str(result.data.get("reason_code") or "not_implemented")
            mark_approval_failed(
                approval_id,
                approval,
                identity.actor_id,
                linked_task,
                reason_code,
            )
            return {
                "status": "disabled",
                "reason_code": reason_code,
                "result": result.model_dump(mode="json"),
            }
        if linked_task:
            final_status = "succeeded" if result.status.value == "success" else "failed"
            if linked_task["type"] == "file.write":
                params = json.loads(approval["canonical_params"])
                artifact = execute_approved_file_write(params)
                db.add_ops_event(
                    "task",
                    linked_task["id"],
                    "backup_created",
                    artifact,
                )
                db.insert_audit(
                    {
                        "id": db.new_id("audit"),
                        "actor_id": identity.actor_id,
                        "event_type": "file_write",
                        "resource": params["path"],
                        "risk_level": "mutating",
                        "status": "succeeded",
                        "summary": f"写入文件并创建备份 {artifact['id']}",
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
            db.add_ops_event(
                "task",
                linked_task["id"],
                "approval_approved",
                {"approval_id": approval_id, "tool_name": approval["tool_name"]},
            )
            db.add_ops_event(
                "task",
                linked_task["id"],
                "command_finished",
                {
                    "tool_name": approval["tool_name"],
                    "status": result.status.value,
                    "output_ref": output_ref,
                },
            )
            db.add_ops_subtask(
                linked_task["id"],
                f"执行 {approval['tool_name']}",
                "succeeded" if final_status == "succeeded" else "failed",
                2,
                output_ref=output_ref,
                failure_reason=result.error,
            )
            if linked_task["type"] == "nginx.reload":
                recheck = invoke_tool("nginx.config_test", {})
                recheck_ref = db.store_tool_result(identity.actor_id, recheck.model_dump(mode="json"))
                recheck.output_ref = recheck_ref
                db.add_ops_event(
                    "task",
                    linked_task["id"],
                    "recheck",
                    {
                        "tool_name": "nginx.config_test",
                        "status": recheck.status.value,
                        "summary": recheck.output_summary,
                        "output_ref": recheck_ref,
                    },
                )
            if linked_task["type"] == "firewall.apply":
                db.add_ops_event(
                    "task",
                    linked_task["id"],
                    "automatic_rollback",
                    {
                        "armed": True,
                        "strategy": "rollback firewall placeholder if connectivity recheck fails",
                    },
                )
            db.add_ops_event(
                "task",
                linked_task["id"],
                "final_report",
                {"summary": result.output_summary, "status": result.status.value},
            )
            db.update_ops_task_status(
                linked_task["id"],
                final_status,
                finished_at=datetime.now(UTC).isoformat(),
                failure_reason=result.error,
            )
        db.update_approval_status(approval_id, "approved")
        db.insert_audit(
            {
                "id": db.new_id("audit"),
                "actor_id": identity.actor_id,
                "event_type": "approval_approved",
                "resource": approval["tool_name"],
                "risk_level": approval["risk_level"],
                "status": result.status.value,
                "summary": result.output_summary,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return {"status": "approved", "result": result.model_dump(mode="json")}
    except HTTPException as exc:
        mark_approval_failed(approval_id, approval, identity.actor_id, linked_task, str(exc.detail))
        raise
    except Exception as exc:
        mark_approval_failed(approval_id, approval, identity.actor_id, linked_task, str(exc))
        raise HTTPException(status_code=500, detail="approval execution failed") from exc


@app.post("/api/approvals/{approval_id}/deny")
def deny(
    approval_id: str,
    identity: SessionIdentity = Depends(require_identity),
    _: None = Depends(require_csrf),
) -> dict:
    require_ready_identity(identity)
    approval = db.get_approval(approval_id)
    if not approval or approval["status"] != "pending":
        raise HTTPException(status_code=404, detail="pending approval not found")
    if approval["actor_id"] != identity.actor_id or approval["auth_session_id"] != identity.session_id:
        raise HTTPException(status_code=403, detail="approval does not match current session")
    db.update_approval_status(approval_id, "denied")
    linked_task = db.get_ops_task_by_approval_id(approval_id)
    if linked_task:
        db.add_ops_event(
            "task",
            linked_task["id"],
            "approval_denied",
            {"approval_id": approval_id, "tool_name": approval["tool_name"]},
        )
        db.add_ops_event(
            "task",
            linked_task["id"],
            "final_report",
            {"summary": "审批已拒绝，任务未执行", "status": "denied"},
        )
        db.update_ops_task_status(
            linked_task["id"],
            "cancelled",
            finished_at=datetime.now(UTC).isoformat(),
            failure_reason="approval denied",
        )
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "approval_denied",
            "resource": approval["tool_name"],
            "risk_level": approval["risk_level"],
            "status": "denied",
            "summary": "审批已拒绝，任务未执行",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return {"status": "denied"}


@app.get("/api/audit")
def audit(identity: SessionIdentity = Depends(require_identity)) -> list[dict]:
    require_ready_identity(identity)
    return db.list_audit()


@app.get("/api/reports")
def reports(identity: SessionIdentity = Depends(require_identity)) -> list[dict]:
    require_ready_identity(identity)
    return db.list_reports()


@app.get("/api/diagnostics/runner", response_model=RunnerDiagnostics)
def runner_diagnostics(identity: SessionIdentity = Depends(require_identity)) -> RunnerDiagnostics:
    require_ready_identity(identity)
    agent = default_agent_client()
    return RunnerDiagnostics(
        runner_user=os.getenv("AIOPS_RUNNER_USER", "current-process"),
        effective_uid=os.geteuid() if hasattr(os, "geteuid") else 0,
        bind_host=os.getenv("AIOPS_BIND_HOST", "127.0.0.1"),
        privileged_adapters_enabled=[],
        sudo_enabled=False,
        tool_count=len(list_tools()),
        llm_mode=model_name(),
        agent_id=agent.agent_id,
        agent_transport=agent.transport,
    )


def allowed_file_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    parts = set(path.parts)
    if ".ssh" in parts or path.name in {"id_rsa", "shadow"} or path.suffix == ".key":
        raise HTTPException(status_code=403, detail={"message": "path matches denied pattern"})
    roots = [
        Path(item).expanduser().resolve()
        for item in os.getenv("AIOPS_FILE_ALLOWED_ROOTS", "/tmp/aiops").split(",")
        if item.strip()
    ]
    for root in roots:
        try:
            path.relative_to(root)
            return path
        except ValueError:
            continue
    raise HTTPException(
        status_code=403,
        detail={
            "message": "path is outside allowed roots",
            "allowed_roots": [str(root) for root in roots],
        },
    )


def file_diff_hash(path: Path, current: str, proposed: str) -> str:
    return fingerprint(
        {
            "path": str(path),
            "current_sha256": sha256(current.encode()).hexdigest(),
            "proposed_sha256": sha256(proposed.encode()).hexdigest(),
        }
    )


def execute_approved_file_write(params: dict) -> dict:
    path = allowed_file_path(str(params["path"]))
    content = str(params["content"])
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    expected_hash = file_diff_hash(path, current, content)
    if params.get("diff_hash") != expected_hash:
        raise HTTPException(status_code=409, detail="file diff hash no longer matches current content")
    artifact = create_backup_artifact(path, "file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return artifact


def create_backup_artifact(source: Path, artifact_type: str, label: str | None = None) -> dict:
    backup_dir = Path(os.getenv("AIOPS_BACKUP_DIR", "data/backups")).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_id = db.new_id("backup")
    safe_name = "".join(ch for ch in (label or source.name or artifact_type) if ch.isalnum() or ch in ("-", "_", "."))
    backup_path = backup_dir / f"{backup_id}-{safe_name or artifact_type}.bak"
    if source.exists():
        shutil.copy2(source, backup_path)
    else:
        backup_path.write_text("", encoding="utf-8")
    return {
        "id": backup_id,
        "type": artifact_type,
        "source_path": str(source),
        "backup_path": str(backup_path),
        "created_at": datetime.now(UTC).isoformat(),
        "restore_requires_second_approval": True,
    }


def find_backup_artifact(backup_id: str) -> dict:
    backup_dir = Path(os.getenv("AIOPS_BACKUP_DIR", "data/backups")).resolve()
    matches = [
        item
        for item in backup_dir.iterdir()
        if item.is_file() and item.name.startswith(f"{backup_id}-") and item.name.endswith(".bak")
    ] if backup_dir.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail="backup artifact not found")
    backup_path = matches[0]
    return {
        "id": backup_id,
        "type": "database",
        "source_path": str(db.DB_PATH.resolve()),
        "backup_path": str(backup_path),
        "created_at": datetime.fromtimestamp(backup_path.stat().st_mtime, UTC).isoformat(),
        "restore_requires_second_approval": True,
    }


def record_task_tool(
    task_id: str,
    actor_id: str,
    tool_name: str,
    params: dict,
    subtask_title: str,
    sequence: int,
):
    db.add_ops_event("task", task_id, "tool_call", {"tool_name": tool_name, "params": params})
    result = invoke_tool(tool_name, params)
    output_ref = db.store_tool_result(actor_id, result.model_dump(mode="json"))
    result.output_ref = output_ref
    success_like = result.status.value in ("success", "no_data")
    db.add_ops_subtask(
        task_id,
        subtask_title,
        "succeeded" if success_like else "failed",
        sequence,
        output_ref=output_ref,
        failure_reason=result.error,
    )
    db.add_ops_event("task", task_id, "tool_result", result.model_dump(mode="json"))
    return result


def create_approval_task(
    identity: SessionIdentity,
    tool_name: str,
    params: dict,
    *,
    target: str,
    task_type: str,
    title: str,
    resource: str,
    reason: str,
) -> dict:
    tool = get_tool(tool_name)
    approval = {
        "id": db.new_id("appr"),
        "auth_session_id": identity.session_id,
        "diagnosis_session_id": None,
        "actor_id": identity.actor_id,
        "nonce": db.new_id("nonce"),
        "action_fingerprint": fingerprint(
            {
                "actor_id": identity.actor_id,
                "auth_session_id": identity.session_id,
                "tool_name": tool_name,
                "target": target,
                "canonical_params": params,
            }
        ),
        "tool_name": tool_name,
        "target": target,
        "canonical_params": params,
        "risk_level": tool.risk_level.value,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "status": "pending",
    }
    db.create_approval(approval)
    task = db.create_ops_task(
        identity.actor_id,
        {
            "type": task_type,
            "title": title,
            "status": "waiting_approval",
            "resource": resource,
            "risk_level": tool.risk_level.value,
            "reason": reason,
            "approval_id": approval["id"],
        },
    )
    db.add_ops_subtask(task["id"], f"等待审批 {tool_name}", "waiting_approval", 1)
    db.add_ops_event("task", task["id"], "approval_required", approval)
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": identity.actor_id,
            "event_type": "approval_requested",
            "resource": resource,
            "risk_level": tool.risk_level.value,
            "status": "pending",
            "summary": f"等待审批：{tool_name}",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return {**task_detail_payload(task["id"]), "approval": approval}


def mark_approval_failed(
    approval_id: str,
    approval,
    actor_id: str,
    linked_task: dict | None,
    reason: str,
) -> None:
    db.update_approval_status(approval_id, "failed")
    if linked_task:
        db.add_ops_event(
            "task",
            linked_task["id"],
            "approval_failed",
            {"approval_id": approval_id, "tool_name": approval["tool_name"], "reason": reason},
        )
        db.add_ops_event(
            "task",
            linked_task["id"],
            "final_report",
            {"summary": reason, "status": "failed"},
        )
        db.update_ops_task_status(
            linked_task["id"],
            "failed",
            finished_at=datetime.now(UTC).isoformat(),
            failure_reason=reason,
        )
    db.insert_audit(
        {
            "id": db.new_id("audit"),
            "actor_id": actor_id,
            "event_type": "approval_failed",
            "resource": approval["tool_name"],
            "risk_level": approval["risk_level"],
            "status": "failed",
            "summary": reason,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )


def fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(canonical.encode()).hexdigest()


def safe_service_name(service: str) -> str:
    safe = "".join(ch for ch in service if ch.isalnum() or ch in ("-", "_", ".", "@"))
    return safe or "nginx"


def classify_command(command: str) -> dict:
    lower = command.lower()
    destructive_markers = ("rm -rf", "mkfs", "dd if=", "drop database", "truncate table")
    mutating_markers = (
        "systemctl restart",
        "systemctl reload",
        "chmod",
        "chown",
        "iptables",
        "ufw",
        "firewall-cmd",
        "docker restart",
        "docker stop",
    )
    if any(marker in lower for marker in destructive_markers):
        return {
            "command": command,
            "risk_level": "destructive",
            "action": "block",
            "reason": "命令可能删除、格式化或破坏数据，默认阻断。",
        }
    if any(marker in lower for marker in mutating_markers):
        return {
            "command": command,
            "risk_level": "mutating",
            "action": "approval_required",
            "reason": "命令会改变服务、权限或网络状态，需要审批。",
        }
    return {
        "command": command,
        "risk_level": "read",
        "action": "allow",
        "reason": "命令按当前规则归类为只读检查。",
    }


def classify_terminal_argv(argv: list[str]) -> dict:
    command = " ".join(argv)
    if not argv:
        return {
            "command": command,
            "risk_level": "destructive",
            "action": "block",
            "reason": "命令不能为空。",
        }
    executable = Path(argv[0]).name.lower()
    blocked_executables = {
        "sh",
        "bash",
        "zsh",
        "fish",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "php",
        "tee",
        "sed",
        "awk",
        "rm",
        "mv",
        "cp",
        "kill",
        "pkill",
        "chmod",
        "chown",
        "systemctl",
        "docker",
        "iptables",
        "ufw",
        "firewall-cmd",
    }
    if executable in blocked_executables:
        return {
            "command": command,
            "risk_level": "destructive" if executable in {"rm", "iptables", "ufw", "firewall-cmd"} else "mutating",
            "action": "block",
            "reason": "终端只读原型只允许固定只读命令；shell、解释器和变更类命令默认阻断。",
        }
    read_only_allowlist = {
        "df",
        "ps",
        "ss",
        "lsof",
        "hostname",
        "uname",
        "uptime",
        "whoami",
        "id",
        "printf",
        "echo",
    }
    if executable not in read_only_allowlist:
        return {
            "command": command,
            "risk_level": "mutating",
            "action": "block",
            "reason": "未知命令默认不允许在只读终端执行。",
        }
    return {
        "command": command,
        "risk_level": "read",
        "action": "allow",
        "reason": "命令匹配只读终端 allowlist。",
    }


def task_detail_payload(task_id: str) -> dict:
    task = db.get_ops_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        **task,
        "subtasks": db.list_ops_subtasks(task_id),
        "events": db.list_ops_events(scope="task", scope_id=task_id),
    }
