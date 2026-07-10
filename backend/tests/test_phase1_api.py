from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient


def make_client(tmp_path: Path) -> TestClient:
    os.environ["AIOPS_ENV"] = "test"
    os.environ.pop("AIOPS_SESSION_SECRET", None)
    os.environ["AIOPS_DB_PATH"] = str(tmp_path / "test.sqlite")
    os.environ["AIOPS_BOOTSTRAP_ADMIN_PASSWORD"] = "BootstrapPassword123!"
    os.environ.pop("AIOPS_BOOTSTRAP_ADMIN_PASSWORD_FILE", None)
    os.environ["AIOPS_DEBUG_SKIP_PASSWORD_CHANGE"] = "0"
    os.environ["AIOPS_LLM_MODE"] = "mock"
    os.environ["AIOPS_FILE_ALLOWED_ROOTS"] = str(tmp_path)
    os.environ["AIOPS_BACKUP_DIR"] = str(tmp_path / "backups")
    os.environ.pop("AIOPS_DEEPSEEK_API_KEY", None)
    os.environ.pop("AIOPS_LLM_API_KEY", None)
    os.environ.pop("AIOPS_LLM_API_KEY_FILE", None)
    os.environ.pop("AIOPS_SESSION_SECRET_FILE", None)
    os.environ.pop("AIOPS_SETUP_TOKEN", None)
    os.environ.pop("AIOPS_SETUP_TOKEN_FILE", None)
    os.environ.pop("AIOPS_SETUP_TOKEN_EXPIRES_AT", None)
    os.environ.pop("AIOPS_COOKIE_SECURE", None)
    os.environ.pop("AIOPS_TLS_ENABLED", None)
    from app.storage import db

    db.DB_PATH = tmp_path / "test.sqlite"
    db.BOOTSTRAP_PATH = tmp_path / "bootstrap_password.txt"
    db.init_db()
    import app.main as main

    importlib.reload(main)
    return TestClient(main.app)


def csrf(client: TestClient) -> str:
    response = client.get("/api/csrf")
    assert response.status_code == 200
    return response.json()["csrf_token"]


def login(client: TestClient, password: str = "BootstrapPassword123!") -> dict:
    token = csrf(client)
    response = client.post(
        "/api/login",
        json={"username": "admin", "password": password},
        headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def change_password(client: TestClient, current: str = "BootstrapPassword123!") -> None:
    token = csrf(client)
    response = client.post(
        "/api/me/password",
        json={"current_password": current, "new_password": "ChangedPassword123!"},
        headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["must_change_password"] is False


def assert_not_implemented(response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "disabled"
    assert body["reason_code"] == "not_implemented"
    assert body["result"]["status"] == "disabled"
    assert body["result"]["reason_code"] == "not_implemented"
    assert body["result"]["data"] == {
        "enabled": False,
        "reason_code": "not_implemented",
    }
    return body


def test_bootstrap_login_requires_password_change(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    body = login(client)
    assert body["must_change_password"] is True
    response = client.get("/api/tools")
    assert response.status_code == 403
    change_password(client)
    response = client.get("/api/tools")
    assert response.status_code == 200
    assert any(tool["name"] == "system.health" for tool in response.json())
    health = client.get("/api/health-snapshot")
    assert health.status_code == 200
    health_data = health.json()["data"]
    assert health_data["hostname"]
    assert health_data["cpu_count"] >= 1
    assert "root_disk_total_gib" in health_data
    runner = client.get("/api/diagnostics/runner")
    assert runner.status_code == 200
    runner_body = runner.json()
    assert runner_body["agent_id"] == "agent_local"
    assert runner_body["agent_transport"] == "in_process"
    assert runner_body["streaming_enabled"] is True
    assert runner_body["cancel_supported"] is True
    assert runner_body["redaction_enabled"] is True
    assert runner_body["shell_enabled"] is False


def test_unauthenticated_sensitive_endpoints_are_rejected(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    assert client.get("/api/tools").status_code == 401
    assert client.get("/api/capabilities").status_code == 401
    assert client.get("/api/safety/contracts").status_code == 401
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/diagnostics/runner").status_code == 401
    assert client.post("/api/tools/system.health/invoke", json={}).status_code == 401


def test_login_requires_csrf_and_valid_origin(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    missing = client.post(
        "/api/login", json={"username": "admin", "password": "BootstrapPassword123!"}
    )
    assert missing.status_code == 403
    token = csrf(client)
    invalid_origin = client.post(
        "/api/login",
        json={"username": "admin", "password": "BootstrapPassword123!"},
        headers={"X-CSRF-Token": token, "Origin": "http://evil.example"},
    )
    assert invalid_origin.status_code == 403


def test_diagnosis_stream_and_audit_after_password_change(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    created = client.post(
        "/api/diagnosis/sessions",
        json={"question": "帮我巡检这台服务器"},
        headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"},
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    events = client.get(f"/api/diagnosis/sessions/{session_id}/events")
    assert events.status_code == 200
    assert "event: plan" in events.text
    assert "event: final_answer" in events.text
    audit = client.get("/api/audit")
    assert audit.status_code == 200
    assert len(audit.json()) >= 5


def test_approval_is_bound_to_auth_session_and_one_time(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    created = client.post(
        "/api/diagnosis/sessions",
        json={"question": "这个 Docker 容器为什么挂了"},
        headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]
    events = client.get(f"/api/diagnosis/sessions/{session_id}/events").text
    marker = '"id": "appr_'
    assert marker in events
    approval_id = "appr_" + events.split(marker, 1)[1].split('"', 1)[0]
    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"},
    )
    assert approved.status_code == 200, approved.text
    replay = client.post(
        f"/api/approvals/{approval_id}/approve",
        headers={"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"},
    )
    assert replay.status_code == 404


def test_capability_contracts_are_compatible_with_tools(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    assert client.get("/api/capabilities").status_code == 403
    change_password(client)

    tools = client.get("/api/tools")
    capabilities = client.get("/api/capabilities")
    assert tools.status_code == 200
    assert capabilities.status_code == 200

    tool_rows = tools.json()
    capability_rows = capabilities.json()
    assert {tool["name"] for tool in tool_rows} == {capability["id"] for capability in capability_rows}

    by_id = {capability["id"]: capability for capability in capability_rows}
    health = by_id["system.health"]
    assert health["name"] == "system.health"
    assert health["risk_level"] == "read"
    assert health["approval_required"] is False
    assert health["executor_kind"] == "local_process"
    assert health["supports_dry_run"] is False
    assert health["supports_rollback"] is False
    assert "output_summary" in health["output_schema"]["properties"]
    assert {"actor_id", "capability_id", "task_id", "event_id"}.issubset(set(health["audit_fields"]))

    docker_restart = by_id["docker.restart"]
    assert docker_restart["risk_level"] == "mutating"
    assert docker_restart["approval_required"] is True
    assert docker_restart["enabled"] is False
    assert docker_restart["disabled_reason_code"] == "not_implemented"
    assert docker_restart["input_schema"]["container"]["type"] == "string"

    host_inspection = by_id["host.inspection"]
    assert host_inspection["risk_level"] == "read"
    assert host_inspection["approval_required"] is False
    assert by_id["systemd.restart"]["approval_required"] is True
    assert by_id["systemd.reload"]["approval_required"] is True
    disabled_ids = {
        "systemd.restart",
        "systemd.reload",
        "database.restore",
        "file.write",
        "firewall.apply",
        "docker.restart",
    }
    assert all(by_id[name]["enabled"] is False for name in disabled_ids)
    assert all(by_id[name]["disabled_reason_code"] == "not_implemented" for name in disabled_ids)

    direct_disabled = client.post(
        "/api/tools/systemd.restart/invoke",
        json={"service": "nginx"},
        headers={"X-CSRF-Token": csrf(client), "Origin": "http://127.0.0.1:5173"},
    )
    assert direct_disabled.status_code == 200
    assert direct_disabled.json()["status"] == "disabled"
    assert direct_disabled.json()["reason_code"] == "not_implemented"
    assert direct_disabled.json()["data"]["reason_code"] == "not_implemented"

    disk_result = client.post(
        "/api/tools/system.disk/invoke",
        json={},
        headers={"X-CSRF-Token": csrf(client), "Origin": "http://127.0.0.1:5173"},
    )
    assert disk_result.status_code == 200, disk_result.text
    disk_body = disk_result.json()
    assert disk_body["data"]["agent_id"] == "agent_local"
    assert disk_body["data"]["agent_transport"] == "in_process"
    assert disk_body["data"]["shell"] is False


def test_task_lifecycle_event_stream_and_safety_contracts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"}

    pending = client.post(
        "/api/tasks",
        json={
            "type": "mock.pending",
            "title": "等待取消的任务",
            "resource": "local-server",
            "risk_level": "read",
            "reason": "验证取消路径",
            "simulate": False,
        },
        headers=headers,
    )
    assert pending.status_code == 200, pending.text
    pending_task = pending.json()
    assert pending_task["status"] == "pending"
    assert pending_task["cancel_requested"] is False
    assert [event["type"] for event in pending_task["events"]] == ["task_created"]

    cancelled = client.post(f"/api/tasks/{pending_task['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    cancelled_task = cancelled.json()
    assert cancelled_task["status"] == "cancelled"
    assert cancelled_task["cancel_requested"] is True
    assert "cancel_requested" in [event["type"] for event in cancelled_task["events"]]

    simulated = client.post(
        "/api/tasks",
        json={
            "type": "mock.long_running",
            "title": "模拟任务事件流",
            "resource": "local-server",
            "risk_level": "read",
            "reason": "验证任务事件底座",
            "simulate": True,
        },
        headers=headers,
    )
    assert simulated.status_code == 200, simulated.text
    simulated_task = simulated.json()
    assert simulated_task["status"] == "succeeded"
    assert simulated_task["cancel_requested"] is False
    assert len(simulated_task["subtasks"]) >= 2
    event_types = [event["type"] for event in simulated_task["events"]]
    assert event_types == [
        "task_created",
        "command_started",
        "stdout",
        "command_finished",
        "final_report",
    ]

    stream = client.get(f"/api/tasks/{simulated_task['id']}/events")
    assert stream.status_code == 200
    assert "event: final_report" in stream.text
    assert "模拟长任务已完成" in stream.text

    event_rows = client.get(
        "/api/events", params={"scope": "task", "scope_id": simulated_task["id"]}
    )
    assert event_rows.status_code == 200
    assert [event["type"] for event in event_rows.json()] == event_types

    task_rows = client.get("/api/tasks")
    assert task_rows.status_code == 200
    assert {task["id"] for task in task_rows.json()} >= {pending_task["id"], simulated_task["id"]}

    audit = client.get("/api/audit")
    assert audit.status_code == 200
    audit_rows = audit.json()
    assert any(
        row["event_type"] == "task_created" and row["resource"] == simulated_task["id"]
        for row in audit_rows
    )
    assert any(row["event_type"] == "task_cancel_requested" for row in audit_rows)

    safety = client.get("/api/safety/contracts")
    assert safety.status_code == 200
    body = safety.json()
    assert body["path_policies"][0]["write_requires_backup"] is True
    assert body["terminal_session_contract"]["ai_auto_execute"] is False
    classifier = {row["command"]: row for row in body["command_risk_classifier"]}
    assert classifier["df -h"]["action"] == "allow"
    assert classifier["systemctl restart nginx"]["action"] == "approval_required"
    assert classifier["rm -rf /var/www"]["action"] == "block"


def test_host_inspection_and_systemd_approval_task(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"}

    inspection = client.get("/api/host/inspection")
    assert inspection.status_code == 200, inspection.text
    inspection_body = inspection.json()
    assert inspection_body["tool_name"] == "host.inspection"
    assert inspection_body["data"]["evidence_count"] >= 8
    evidence_ids = {item["id"] for item in inspection_body["data"]["evidence"]}
    assert {"hostname", "platform", "root_disk", "memory", "systemd", "ssh_config"}.issubset(
        evidence_ids
    )

    security = client.post(
        "/api/tools/security.ssh_login/invoke",
        json={},
        headers=headers,
    )
    assert security.status_code == 200, security.text
    assert security.json()["tool_name"] == "security.ssh_login"

    created = client.post(
        "/api/systemd/actions",
        json={
            "service": "nginx",
            "action": "restart",
            "reason": "验证 nginx restart 审批任务",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    task = created.json()
    approval_id = task["approval"]["id"]
    assert task["status"] == "waiting_approval"
    assert task["approval_id"] == approval_id
    assert task["resource"] == "systemd:nginx"
    assert any(event["type"] == "approval_required" for event in task["events"])

    approved = client.post(f"/api/approvals/{approval_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    replay = client.post(f"/api/approvals/{approval_id}/approve", headers=headers)
    assert replay.status_code == 404
    result = assert_not_implemented(approved)["result"]
    assert result["tool_name"] == "systemd.restart"

    detail = client.get(f"/api/tasks/{task['id']}")
    assert detail.status_code == 200, detail.text
    completed = detail.json()
    assert completed["status"] == "failed"
    event_types = [event["type"] for event in completed["events"]]
    assert "approval_failed" in event_types
    assert "approval_approved" not in event_types
    assert "command_finished" not in event_types
    assert "final_report" in event_types

    audit = client.get("/api/audit")
    assert audit.status_code == 200
    audit_types = [row["event_type"] for row in audit.json()]
    assert "host_inspection" in audit_types
    assert "approval_requested" in audit_types
    assert "approval_failed" in audit_types


def test_docker_and_nginx_repair_loops(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"}

    sites = client.get("/api/sites")
    assert sites.status_code == 200
    assert sites.json()[0]["upstream"].startswith("http://")

    docker_diag = client.post(
        "/api/docker/diagnose",
        json={
            "container": "missing-container",
            "reason": "验证 Docker 异常诊断",
            "log_tail": 20,
        },
        headers=headers,
    )
    assert docker_diag.status_code == 200, docker_diag.text
    docker_task = docker_diag.json()
    assert docker_task["status"] == "succeeded"
    assert len(docker_task["subtasks"]) == 3
    docker_event_types = [event["type"] for event in docker_task["events"]]
    assert docker_event_types.count("tool_call") == 3
    assert docker_event_types.count("tool_result") == 3
    assert "final_report" in docker_event_types

    docker_restart = client.post(
        "/api/docker/restart-tasks",
        json={"container": "missing-container", "reason": "验证 Docker 重启审批"},
        headers=headers,
    )
    assert docker_restart.status_code == 200, docker_restart.text
    docker_restart_task = docker_restart.json()
    docker_approval_id = docker_restart_task["approval"]["id"]
    assert docker_restart_task["status"] == "waiting_approval"
    assert docker_restart_task["resource"] == "docker:missing-container"
    docker_approved = client.post(f"/api/approvals/{docker_approval_id}/approve", headers=headers)
    assert_not_implemented(docker_approved)
    docker_detail = client.get(f"/api/tasks/{docker_restart_task['id']}")
    assert docker_detail.status_code == 200
    assert docker_detail.json()["status"] == "failed"

    nginx_diag = client.post(
        "/api/nginx/diagnose-502",
        json={
            "site_url": "http://example.test",
            "upstream": "http://127.0.0.1:9000",
            "reason": "验证 Nginx 502 诊断",
        },
        headers=headers,
    )
    assert nginx_diag.status_code == 200, nginx_diag.text
    nginx_task = nginx_diag.json()
    assert nginx_task["status"] == "succeeded"
    nginx_event_types = [event["type"] for event in nginx_task["events"]]
    assert "config_diff" in nginx_event_types
    assert "rollback_plan" in nginx_event_types
    assert "final_report" in nginx_event_types

    nginx_reload = client.post(
        "/api/nginx/reload-tasks",
        json={"site": "example.test", "reason": "验证 Nginx reload 审批"},
        headers=headers,
    )
    assert nginx_reload.status_code == 200, nginx_reload.text
    reload_task = nginx_reload.json()
    reload_approval_id = reload_task["approval"]["id"]
    assert reload_task["status"] == "waiting_approval"
    assert any(event["type"] == "rollback_plan" for event in reload_task["events"])
    reload_approved = client.post(f"/api/approvals/{reload_approval_id}/approve", headers=headers)
    assert_not_implemented(reload_approved)
    reload_detail = client.get(f"/api/tasks/{reload_task['id']}")
    assert reload_detail.status_code == 200
    reload_completed = reload_detail.json()
    assert reload_completed["status"] == "failed"
    reload_events = [event["type"] for event in reload_completed["events"]]
    assert "approval_failed" in reload_events
    assert "approval_approved" not in reload_events
    assert "command_finished" not in reload_events
    assert "recheck" not in reload_events
    assert "final_report" in reload_events

    audit = client.get("/api/audit")
    assert audit.status_code == 200
    audit_types = [row["event_type"] for row in audit.json()]
    assert "docker_diagnosis" in audit_types
    assert "nginx_502_diagnosis" in audit_types
    assert audit_types.count("approval_requested") >= 2
    assert audit_types.count("approval_failed") >= 2


def test_files_terminal_database_and_secret_contracts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"}

    editable = tmp_path / "editable.conf"
    editable.write_text("version=1\n", encoding="utf-8")
    diff = client.post(
        "/api/files/diff",
        json={"path": str(editable), "content": "version=2\n", "reason": "验证 diff"},
        headers=headers,
    )
    assert diff.status_code == 200, diff.text
    diff_body = diff.json()
    assert diff_body["write_requires_backup"] is True
    assert diff_body["write_requires_approval"] is True
    assert diff_body["diff_hash"].startswith("sha256:")
    assert "-version=1" in diff_body["diff"]
    assert "+version=2" in diff_body["diff"]

    blocked = client.post(
        "/api/files/diff",
        json={"path": "/etc/passwd", "content": "x", "reason": "越界路径"},
        headers=headers,
    )
    assert blocked.status_code == 403

    denied_pattern = tmp_path / ".ssh" / "id_rsa"
    denied_pattern.parent.mkdir()
    denied = client.post(
        "/api/files/diff",
        json={"path": str(denied_pattern), "content": "x", "reason": "deny pattern"},
        headers=headers,
    )
    assert denied.status_code == 403

    missing_diff_hash = client.post(
        "/api/files/write",
        json={"path": str(editable), "content": "version=2\n", "reason": "缺少 diff hash"},
        headers=headers,
    )
    assert missing_diff_hash.status_code == 422

    written = client.post(
        "/api/files/write",
        json={
            "path": str(editable),
            "content": "version=2\n",
            "diff_hash": diff_body["diff_hash"],
            "reason": "验证备份写入",
        },
        headers=headers,
    )
    assert written.status_code == 200, written.text
    write_task = written.json()
    assert write_task["status"] == "waiting_approval"
    assert editable.read_text(encoding="utf-8") == "version=1\n"
    approved_write = client.post(f"/api/approvals/{write_task['approval']['id']}/approve", headers=headers)
    assert_not_implemented(approved_write)
    assert editable.read_text(encoding="utf-8") == "version=1\n"
    written_detail = client.get(f"/api/tasks/{write_task['id']}")
    assert written_detail.status_code == 200
    assert written_detail.json()["status"] == "failed"
    write_event_types = [event["type"] for event in written_detail.json()["events"]]
    assert "approval_failed" in write_event_types
    assert "backup_created" not in write_event_types
    assert "command_finished" not in write_event_types

    stale = tmp_path / "stale.conf"
    stale.write_text("before\n", encoding="utf-8")
    stale_diff = client.post(
        "/api/files/diff",
        json={"path": str(stale), "content": "after\n", "reason": "stale diff"},
        headers=headers,
    )
    stale_task = client.post(
        "/api/files/write",
        json={
            "path": str(stale),
            "content": "after\n",
            "diff_hash": stale_diff.json()["diff_hash"],
            "reason": "验证 stale diff 失败状态",
        },
        headers=headers,
    )
    assert stale_task.status_code == 200, stale_task.text
    stale.write_text("drift\n", encoding="utf-8")
    stale_approval = stale_task.json()["approval"]["id"]
    stale_approved = client.post(f"/api/approvals/{stale_approval}/approve", headers=headers)
    assert_not_implemented(stale_approved)
    stale_detail = client.get(f"/api/tasks/{stale_task.json()['id']}")
    assert stale_detail.status_code == 200
    assert stale_detail.json()["status"] == "failed"
    assert any(event["type"] == "approval_failed" for event in stale_detail.json()["events"])
    stale_replay = client.post(f"/api/approvals/{stale_approval}/approve", headers=headers)
    assert stale_replay.status_code == 404
    assert stale.read_text(encoding="utf-8") == "drift\n"

    deny_file = tmp_path / "deny.conf"
    deny_file.write_text("before\n", encoding="utf-8")
    deny_diff = client.post(
        "/api/files/diff",
        json={"path": str(deny_file), "content": "after\n", "reason": "deny diff"},
        headers=headers,
    )
    deny_task = client.post(
        "/api/files/write",
        json={
            "path": str(deny_file),
            "content": "after\n",
            "diff_hash": deny_diff.json()["diff_hash"],
            "reason": "验证拒绝审批",
        },
        headers=headers,
    )
    assert deny_task.status_code == 200, deny_task.text
    denied_approval = client.post(f"/api/approvals/{deny_task.json()['approval']['id']}/deny", headers=headers)
    assert denied_approval.status_code == 200, denied_approval.text
    denied_detail = client.get(f"/api/tasks/{deny_task.json()['id']}")
    assert denied_detail.status_code == 200
    assert denied_detail.json()["status"] == "cancelled"
    assert deny_file.read_text(encoding="utf-8") == "before\n"

    terminal = client.post(
        "/api/terminal/run",
        json={
            "argv": ["echo", "audit terminal"],
            "reason": "验证终端审计",
        },
        headers=headers,
    )
    assert terminal.status_code == 200, terminal.text
    terminal_audit = client.get("/api/terminal/audit")
    assert terminal_audit.status_code == 200
    assert terminal_audit.json()["tasks"]
    assert terminal_audit.json()["audit"]

    backup = client.post(
        "/api/database/backups",
        json={"name": "m5", "reason": "验证数据库备份"},
        headers=headers,
    )
    assert backup.status_code == 200, backup.text
    backup_artifact = backup.json()["backup_artifact"]
    assert backup_artifact["type"] == "database"
    assert Path(backup_artifact["backup_path"]).exists()

    restore_without_confirmation = client.post(
        "/api/database/restores",
        json={
            "backup_id": backup_artifact["id"],
            "reason": "缺少确认",
            "confirm_restore_point": True,
            "second_approval": False,
        },
        headers=headers,
    )
    assert restore_without_confirmation.status_code == 409
    assert restore_without_confirmation.json()["detail"]["second_approval"] is False

    wildcard_restore = client.post(
        "/api/database/restores",
        json={
            "backup_id": "backup_*",
            "reason": "通配符必须拒绝",
            "confirm_restore_point": True,
            "second_approval": True,
        },
        headers=headers,
    )
    assert wildcard_restore.status_code == 422

    restore = client.post(
        "/api/database/restores",
        json={
            "backup_id": backup_artifact["id"],
            "reason": "验证数据库恢复审批",
            "confirm_restore_point": True,
            "second_approval": True,
        },
        headers=headers,
    )
    assert restore.status_code == 200, restore.text
    restore_task = restore.json()
    assert restore_task["status"] == "waiting_approval"
    assert any(event["type"] == "restore_point_confirmed" for event in restore_task["events"])
    restore_approval = restore_task["approval"]["id"]
    approved = client.post(f"/api/approvals/{restore_approval}/approve", headers=headers)
    assert_not_implemented(approved)
    restore_detail = client.get(f"/api/tasks/{restore_task['id']}")
    assert restore_detail.status_code == 200
    assert restore_detail.json()["status"] == "failed"
    assert "command_finished" not in [event["type"] for event in restore_detail.json()["events"]]

    secret = client.post(
        "/api/secrets",
        json={"name": "deepseek", "value": "secret-token-123", "reason": "验证 secret contract"},
        headers=headers,
    )
    assert secret.status_code == 200, secret.text
    secret_text = json.dumps(secret.json(), ensure_ascii=False)
    assert "secret-token-123" not in secret_text
    assert secret.json()["value_ref"].startswith("secret://")

    audit = client.get("/api/audit")
    assert audit.status_code == 200
    audit_text = json.dumps(audit.json(), ensure_ascii=False)
    assert "secret-token-123" not in audit_text
    audit_types = [row["event_type"] for row in audit.json()]
    assert "file_write" not in audit_types
    assert "database_backup" in audit_types
    assert "secret_created" in audit_types


def test_security_multinode_firewall_and_agent_contracts(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"}

    preflight = client.post(
        "/api/security/firewall/preflight",
        json={"port": 8443, "protocol": "tcp", "reason": "验证防火墙前置检查"},
        headers=headers,
    )
    assert preflight.status_code == 200, preflight.text
    preflight_body = preflight.json()
    assert preflight_body["connectivity_guard"]["enabled"] is False
    assert preflight_body["rollback_plan"]["available"] is False
    assert preflight_body["rollback_plan"]["automatic"] is False
    assert preflight_body["safe_to_request_approval"] is False
    assert preflight_body["reason_code"] == "not_implemented"

    blocked = client.post(
        "/api/security/firewall/apply",
        json={
            "port": 8443,
            "protocol": "tcp",
            "reason": "缺少回滚确认",
            "confirm_connectivity_guard": True,
            "confirm_rollback_plan": False,
        },
        headers=headers,
    )
    assert blocked.status_code == 501
    assert blocked.json()["detail"]["reason_code"] == "not_implemented"

    apply_task = client.post(
        "/api/security/firewall/apply",
        json={
            "port": 8443,
            "protocol": "tcp",
            "reason": "验证防火墙审批任务",
            "confirm_connectivity_guard": True,
            "confirm_rollback_plan": True,
        },
        headers=headers,
    )
    assert apply_task.status_code == 501, apply_task.text
    assert apply_task.json()["detail"] == {
        "reason_code": "not_implemented",
        "capability": "firewall.apply",
        "connectivity_guard_available": False,
        "automatic_rollback_available": False,
    }

    local_socket = client.get("/api/agents/local-socket")
    assert local_socket.status_code == 200
    assert local_socket.json()["transport"] == "unix_socket"

    remote_contract = client.get("/api/agents/remote-contract")
    assert remote_contract.status_code == 200
    assert remote_contract.json()["transport"] == "mtls_https"
    assert "agent_id" in remote_contract.json()["required_fields"]

    registered = client.post(
        "/api/agents/register",
        json={
            "agent_id": "agent_remote_1",
            "name": "远端节点 1",
            "endpoint": "https://agent-1.example.test",
            "transport": "mtls_https",
            "capability_ids": ["host.inspection"],
        },
        headers=headers,
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["status"] == "offline"

    heartbeat = client.post(
        "/api/agents/heartbeat",
        json={
            "agent_id": "agent_remote_1",
            "status": "online",
            "capability_ids": ["host.inspection"],
            "runner_user": "agent",
            "effective_uid": 1000,
            "created_at": datetime.now(UTC).isoformat(),
        },
        headers=headers,
    )
    assert heartbeat.status_code == 200, heartbeat.text
    assert heartbeat.json()["status"] == "online"

    loss = client.post(
        "/api/agents/simulate-heartbeat-loss",
        json={"agent_id": "agent_remote_1", "seconds_since_last_heartbeat": 120},
        headers=headers,
    )
    assert loss.status_code == 200, loss.text
    assert loss.json()["status"] == "offline"
    assert loss.json()["heartbeat_lost"] is True


def test_terminal_readonly_runner_creates_task_events_and_blocks_unsafe_shell(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    login(client)
    change_password(client)
    token = csrf(client)
    headers = {"X-CSRF-Token": token, "Origin": "http://127.0.0.1:5173"}

    shell_string = client.post(
        "/api/terminal/run",
        json={"argv": "df -h", "reason": "字符串 shell 不允许"},
        headers=headers,
    )
    assert shell_string.status_code == 422

    blocked_commands = [
        ["rm", "-r", "-f", "/tmp/aiops-danger"],
        ["sh", "-c", "touch /tmp/aiops-danger"],
        [sys.executable, "-c", "open('/tmp/aiops-danger', 'w').write('x')"],
        ["sed", "-i", "s/a/b/", "/tmp/example"],
        ["tee", "/tmp/example"],
    ]
    for argv in blocked_commands:
        destructive = client.post(
            "/api/terminal/run",
            json={"argv": argv, "reason": "危险命令必须阻断"},
            headers=headers,
        )
        assert destructive.status_code == 409, argv
        assert destructive.json()["detail"]["action"] == "block"

    created = client.post(
        "/api/terminal/run",
        json={
            "argv": ["printf", "terminal ok\ntoken=abc123\n"],
            "reason": "验证只读终端任务事件",
            "timeout_seconds": 5,
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    task = created.json()
    assert task["type"] == "terminal.read"
    assert task["status"] == "succeeded"
    assert len(task["subtasks"]) == 1
    event_types = [event["type"] for event in task["events"]]
    assert "command_started" in event_types
    assert "stdout" in event_types
    assert "command_finished" in event_types
    assert "final_report" in event_types
    assert any(event["payload"].get("line") == "terminal ok" for event in task["events"])

    stream = client.get(f"/api/tasks/{task['id']}/events")
    assert stream.status_code == 200
    assert "event: stdout" in stream.text
    assert "terminal ok" in stream.text
    assert "token=abc123" not in stream.text
    assert "[redacted secret-like line]" in stream.text

    audit = client.get("/api/audit")
    assert audit.status_code == 200
    audit_rows = audit.json()
    assert any(row["event_type"] == "terminal_run" for row in audit_rows)
    assert "token=abc123" not in json.dumps(audit_rows, ensure_ascii=False)
