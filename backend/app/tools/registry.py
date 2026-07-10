from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Callable

from fastapi import HTTPException

from app.models.schemas import Capability, CapabilityExecutorKind, RiskLevel, Tool, ToolResult, ToolStatus
from app.runner.command import run_argv


Adapter = Callable[[dict], ToolResult]
NOT_IMPLEMENTED = "not_implemented"


def disabled_tool_result(tool_name: str) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        status=ToolStatus.DISABLED,
        started_at=datetime.now(UTC),
        duration_ms=0,
        output_summary="capability is disabled because its production adapter is not implemented",
        error="capability disabled",
        reason_code=NOT_IMPLEMENTED,
        data={"enabled": False, "reason_code": NOT_IMPLEMENTED},
    )


def _round_gib(bytes_value: int | float | None) -> float | None:
    if bytes_value is None:
        return None
    return round(float(bytes_value) / (1024**3), 2)


def _memory_info() -> dict[str, float | None]:
    total: int | None = None
    available: int | None = None

    if platform.system() == "Darwin":
        try:
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            total = None
    elif os.path.exists("/proc/meminfo"):
        values: dict[str, int] = {}
        try:
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, raw_value = line.split(":", 1)
                    number = raw_value.strip().split(" ", 1)[0]
                    values[key] = int(number) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
        except (OSError, ValueError):
            total = None
            available = None

    if total is None and hasattr(os, "sysconf"):
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            page_count = os.sysconf("SC_PHYS_PAGES")
            total = int(page_size * page_count)
        except (OSError, ValueError):
            total = None

    used_percent = None
    if total and available is not None:
        used_percent = round((total - available) / total * 100, 1)

    return {
        "total_gib": _round_gib(total),
        "available_gib": _round_gib(available),
        "used_percent": used_percent,
    }


def _python_health(_: dict) -> ToolResult:
    started = datetime.now(UTC)
    begin = monotonic()
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    disk = shutil.disk_usage("/")
    disk_percent = int(disk.used / disk.total * 100)
    memory = _memory_info()
    summary = (
        f"load={load[0]:.2f}/{load[1]:.2f}/{load[2]:.2f}, "
        f"root_disk={disk_percent}%, "
        f"system={platform.system()} {platform.release()}"
    )
    return ToolResult(
        tool_name="system.health",
        status=ToolStatus.SUCCESS,
        started_at=started,
        duration_ms=int((monotonic() - begin) * 1000),
        output_summary=summary,
        data={
            "load": load,
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "root_disk_percent": disk_percent,
            "root_disk_total_gib": _round_gib(disk.total),
            "root_disk_used_gib": _round_gib(disk.used),
            "root_disk_free_gib": _round_gib(disk.free),
            "memory_total_gib": memory["total_gib"],
            "memory_available_gib": memory["available_gib"],
            "memory_used_percent": memory["used_percent"],
        },
    )


def _disk(_: dict) -> ToolResult:
    return run_argv("system.disk", ["df", "-h"], timeout=8)


def _processes(_: dict) -> ToolResult:
    return run_argv("system.processes", ["ps", "aux"], timeout=8)


def _ports(_: dict) -> ToolResult:
    if platform.system() == "Darwin":
        return run_argv("network.ports", ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], timeout=8)
    return run_argv("network.ports", ["ss", "-tulnp"], timeout=8)


def _systemd_status(params: dict) -> ToolResult:
    service = str(params.get("service") or "nginx")
    safe_service = "".join(ch for ch in service if ch.isalnum() or ch in ("-", "_", ".", "@"))
    return run_argv("systemd.status", ["systemctl", "status", safe_service, "--no-pager"], timeout=8)


def _docker_ps(_: dict) -> ToolResult:
    return run_argv("docker.ps", ["docker", "ps", "-a"], timeout=8)


def _docker_inspect(params: dict) -> ToolResult:
    container = _safe_container(params.get("container") or "unknown")
    return run_argv("docker.inspect", ["docker", "inspect", container], timeout=8)


def _docker_logs(params: dict) -> ToolResult:
    container = _safe_container(params.get("container") or "unknown")
    tail = str(params.get("tail") or "80")
    if not tail.isdigit():
        tail = "80"
    return run_argv("docker.logs", ["docker", "logs", "--tail", tail, container], timeout=8)


def _nginx_test(_: dict) -> ToolResult:
    return run_argv("nginx.config_test", ["nginx", "-t"], timeout=8)


def _nginx_error_log(_: dict) -> ToolResult:
    log_path = Path("/var/log/nginx/error.log")
    if not log_path.exists():
        return ToolResult(
            tool_name="nginx.error_log",
            status=ToolStatus.NO_DATA,
            started_at=datetime.now(UTC),
            duration_ms=0,
            output_summary="nginx error log not found",
            data={"path": str(log_path), "present": False},
        )
    return run_argv("nginx.error_log", ["tail", "-n", "80", str(log_path)], timeout=8)


def _host_inspection(_: dict) -> ToolResult:
    started = datetime.now(UTC)
    begin = monotonic()
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    disk = shutil.disk_usage("/")
    memory = _memory_info()
    systemd_present = Path("/run/systemd/system").exists() or shutil.which("systemctl") is not None
    sshd_config_present = Path("/etc/ssh/sshd_config").exists()
    evidence = [
        {"id": "hostname", "title": "主机名", "value": socket.gethostname(), "source": "socket"},
        {
            "id": "platform",
            "title": "操作系统",
            "value": f"{platform.system()} {platform.release()}",
            "source": "platform",
        },
        {"id": "machine", "title": "硬件架构", "value": platform.machine(), "source": "platform"},
        {"id": "cpu_count", "title": "CPU 核心", "value": os.cpu_count(), "source": "os"},
        {
            "id": "load_avg",
            "title": "系统负载",
            "value": [round(item, 2) for item in load],
            "source": "os.getloadavg",
        },
        {
            "id": "root_disk",
            "title": "根磁盘",
            "value": {
                "used_percent": int(disk.used / disk.total * 100),
                "total_gib": _round_gib(disk.total),
                "free_gib": _round_gib(disk.free),
            },
            "source": "shutil.disk_usage",
        },
        {"id": "memory", "title": "内存", "value": memory, "source": "procfs/sysconf"},
        {
            "id": "systemd",
            "title": "systemd 可用性",
            "value": {"available": systemd_present},
            "source": "/run/systemd/system",
        },
        {
            "id": "ssh_config",
            "title": "SSH 配置文件",
            "value": {"present": sshd_config_present},
            "source": "/etc/ssh/sshd_config",
        },
    ]
    return ToolResult(
        tool_name="host.inspection",
        status=ToolStatus.SUCCESS,
        started_at=started,
        duration_ms=int((monotonic() - begin) * 1000),
        output_summary=f"host inspection collected {len(evidence)} evidence points",
        data={"evidence": evidence, "evidence_count": len(evidence)},
    )


def _security_ssh_login(_: dict) -> ToolResult:
    started = datetime.now(UTC)
    begin = monotonic()
    config_path = Path("/etc/ssh/sshd_config")
    settings = {
        "PermitRootLogin": "unknown",
        "PasswordAuthentication": "unknown",
        "PubkeyAuthentication": "unknown",
    }
    read_error = None
    if config_path.exists():
        try:
            for line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or " " not in stripped:
                    continue
                key, value = stripped.split(None, 1)
                if key in settings:
                    settings[key] = value.strip()
        except OSError as exc:
            read_error = str(exc)
    evidence = [
        {
            "id": "sshd_config_present",
            "title": "sshd_config",
            "value": config_path.exists(),
            "source": str(config_path),
        },
        {
            "id": "permit_root_login",
            "title": "Root 登录策略",
            "value": settings["PermitRootLogin"],
            "source": str(config_path),
        },
        {
            "id": "password_authentication",
            "title": "密码登录策略",
            "value": settings["PasswordAuthentication"],
            "source": str(config_path),
        },
        {
            "id": "pubkey_authentication",
            "title": "密钥登录策略",
            "value": settings["PubkeyAuthentication"],
            "source": str(config_path),
        },
    ]
    if read_error:
        evidence.append(
            {
                "id": "sshd_config_read_error",
                "title": "SSH 配置读取错误",
                "value": read_error,
                "source": str(config_path),
            }
        )
    return ToolResult(
        tool_name="security.ssh_login",
        status=ToolStatus.SUCCESS,
        started_at=started,
        duration_ms=int((monotonic() - begin) * 1000),
        output_summary="ssh/login security summary collected",
        data={"evidence": evidence, "settings": settings, "read_error": read_error},
    )


def _systemd_action(action: str, params: dict) -> ToolResult:
    return disabled_tool_result(f"systemd.{action}")


def _safe_service(service: object) -> str:
    raw = str(service)
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_", ".", "@"))
    return safe or "nginx"


def _safe_container(container: object) -> str:
    raw = str(container)
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in ("-", "_", ".", ":", "/"))
    return safe or "unknown"


TOOLS: dict[str, tuple[Tool, Adapter]] = {
    "host.inspection": (
        Tool(
            name="host.inspection",
            category="host",
            description="Collect production host evidence for server inspection",
        ),
        _host_inspection,
    ),
    "system.health": (
        Tool(
            name="system.health",
            category="system",
            description="Collect load, platform, and root disk summary",
        ),
        _python_health,
    ),
    "system.disk": (
        Tool(name="system.disk", category="system", description="Show disk usage"),
        _disk,
    ),
    "system.processes": (
        Tool(name="system.processes", category="system", description="List running processes"),
        _processes,
    ),
    "network.ports": (
        Tool(name="network.ports", category="network", description="List listening TCP ports"),
        _ports,
    ),
    "systemd.status": (
        Tool(
            name="systemd.status",
            category="systemd",
            description="Read a systemd service status",
            input_schema={"service": {"type": "string"}},
        ),
        _systemd_status,
    ),
    "systemd.restart": (
        Tool(
            name="systemd.restart",
            category="systemd",
            description="Restart a systemd service after approval",
            input_schema={"service": {"type": "string"}},
            risk_level=RiskLevel.MUTATING,
            approval_required=True,
            enabled=False,
            disabled_reason_code=NOT_IMPLEMENTED,
        ),
        lambda params: _systemd_action("restart", params),
    ),
    "systemd.reload": (
        Tool(
            name="systemd.reload",
            category="systemd",
            description="Reload a systemd service after approval",
            input_schema={"service": {"type": "string"}},
            risk_level=RiskLevel.MUTATING,
            approval_required=True,
            enabled=False,
            disabled_reason_code=NOT_IMPLEMENTED,
        ),
        lambda params: _systemd_action("reload", params),
    ),
    "docker.ps": (
        Tool(name="docker.ps", category="docker", description="List Docker containers"),
        _docker_ps,
    ),
    "docker.inspect": (
        Tool(
            name="docker.inspect",
            category="docker",
            description="Inspect one Docker container",
            input_schema={"container": {"type": "string"}},
        ),
        _docker_inspect,
    ),
    "docker.logs": (
        Tool(
            name="docker.logs",
            category="docker",
            description="Read recent Docker container logs",
            input_schema={"container": {"type": "string"}, "tail": {"type": "integer"}},
        ),
        _docker_logs,
    ),
    "nginx.config_test": (
        Tool(name="nginx.config_test", category="nginx", description="Run nginx config test"),
        _nginx_test,
    ),
    "nginx.error_log": (
        Tool(name="nginx.error_log", category="nginx", description="Read recent nginx error log"),
        _nginx_error_log,
    ),
    "security.ssh_login": (
        Tool(
            name="security.ssh_login",
            category="security",
            description="Read SSH and login security posture",
        ),
        _security_ssh_login,
    ),
    "database.restore": (
        Tool(
            name="database.restore",
            category="database",
            description="Restore database from a backup after second approval",
            input_schema={"backup_id": {"type": "string"}},
            risk_level=RiskLevel.DESTRUCTIVE,
            approval_required=True,
            enabled=False,
            disabled_reason_code=NOT_IMPLEMENTED,
        ),
        lambda _: disabled_tool_result("database.restore"),
    ),
    "file.write": (
        Tool(
            name="file.write",
            category="file",
            description="Write an allowed file after diff-bound approval and backup",
            input_schema={"path": {"type": "string"}, "diff_hash": {"type": "string"}},
            risk_level=RiskLevel.MUTATING,
            approval_required=True,
            enabled=False,
            disabled_reason_code=NOT_IMPLEMENTED,
        ),
        lambda _: disabled_tool_result("file.write"),
    ),
    "firewall.apply": (
        Tool(
            name="firewall.apply",
            category="security",
            description="Apply firewall change after connectivity guard and approval",
            input_schema={"port": {"type": "integer"}, "protocol": {"type": "string"}},
            risk_level=RiskLevel.MUTATING,
            approval_required=True,
            enabled=False,
            disabled_reason_code=NOT_IMPLEMENTED,
        ),
        lambda _: disabled_tool_result("firewall.apply"),
    ),
    "docker.restart": (
        Tool(
            name="docker.restart",
            category="docker",
            description="Restart a Docker container after approval",
            input_schema={"container": {"type": "string"}},
            risk_level=RiskLevel.MUTATING,
            approval_required=True,
            enabled=False,
            disabled_reason_code=NOT_IMPLEMENTED,
        ),
        lambda _: disabled_tool_result("docker.restart"),
    ),
}


def list_tools() -> list[Tool]:
    return [entry[0] for entry in TOOLS.values()]


def capability_from_tool(tool: Tool) -> Capability:
    output_schema = {
        "type": "object",
        "properties": {
            "tool_name": {"type": "string"},
            "status": {"type": "string"},
            "started_at": {"type": "string", "format": "date-time"},
            "duration_ms": {"type": "integer"},
            "output_summary": {"type": "string"},
            "output_ref": {"type": ["string", "null"]},
            "redacted": {"type": "boolean"},
            "error": {"type": ["string", "null"]},
            "reason_code": {"type": ["string", "null"]},
            "data": {"type": "object"},
        },
        "required": ["tool_name", "status", "output_summary", "data"],
    }
    return Capability(
        id=tool.name,
        name=tool.name,
        display_name=tool.description,
        category=tool.category,
        description=tool.description,
        ai_description=(
            f"{tool.description}. Risk level: {tool.risk_level.value}. "
            f"Approval required: {tool.approval_required}."
        ),
        ui_description=tool.description,
        enabled=tool.enabled,
        disabled_reason_code=tool.disabled_reason_code,
        input_schema=tool.input_schema,
        output_schema=output_schema,
        risk_level=tool.risk_level,
        approval_required=tool.approval_required,
        supports_dry_run=False,
        supports_rollback=False,
        default_timeout_seconds=tool.timeout_seconds,
        executor_kind=CapabilityExecutorKind.LOCAL_PROCESS,
    )


def list_capabilities() -> list[Capability]:
    return [capability_from_tool(tool) for tool in list_tools()]


def get_tool(name: str) -> Tool:
    if name not in TOOLS:
        raise HTTPException(status_code=404, detail="tool not found")
    return TOOLS[name][0]


def invoke_tool(name: str, params: dict, *, approved: bool = False) -> ToolResult:
    if name not in TOOLS:
        raise HTTPException(status_code=404, detail="tool not found")
    tool, adapter = TOOLS[name]
    if not tool.enabled:
        return disabled_tool_result(name)
    if tool.approval_required and not approved:
        return ToolResult(
            tool_name=name,
            status=ToolStatus.APPROVAL_REQUIRED,
            started_at=datetime.now(UTC),
            duration_ms=0,
            output_summary="approval required",
            data={"params": params},
        )
    return adapter(params)
