from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from app.models.schemas import RiskLevel, ToolResult
from app.storage import db
from app.tools.registry import invoke_tool


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(canonical.encode()).hexdigest()


def run_mock_diagnosis(actor_id: str, auth_session_id: str, question: str) -> dict:
    session = db.create_diagnosis(actor_id, question)
    session_id = session["id"]
    sequence = 1
    plan = [
        "Collect host health",
        "Inspect disk usage",
        "Inspect processes",
        "Inspect listening ports",
        "Check common service state",
    ]
    db.add_event(session_id, sequence, "plan", {"steps": plan})
    sequence += 1

    tool_names = ["system.health", "system.disk", "system.processes", "network.ports", "systemd.status"]
    summaries: list[str] = []
    evidence_refs: list[str] = []
    for tool_name in tool_names:
        db.add_event(session_id, sequence, "tool_call", {"tool_name": tool_name})
        sequence += 1
        result: ToolResult = invoke_tool(tool_name, {})
        output_ref = db.store_tool_result(actor_id, result.model_dump(mode="json"))
        result.output_ref = output_ref
        summaries.append(f"{tool_name}: {result.output_summary}")
        evidence_refs.append(output_ref)
        db.insert_audit(
            {
                "id": db.new_id("audit"),
                "actor_id": actor_id,
                "event_type": "tool_invocation",
                "resource": tool_name,
                "risk_level": "read",
                "status": result.status.value,
                "summary": result.output_summary,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        db.add_event(session_id, sequence, "tool_result", result.model_dump(mode="json"))
        sequence += 1

    if "docker" in question.lower() or "容器" in question:
        params = {"container": "selected-container"}
        fingerprint_payload = {
            "actor_id": actor_id,
            "auth_session_id": auth_session_id,
            "diagnosis_session_id": session_id,
            "tool_name": "docker.restart",
            "target": "container:selected-container",
            "canonical_params": params,
        }
        approval = {
            "id": db.new_id("appr"),
            "auth_session_id": auth_session_id,
            "diagnosis_session_id": session_id,
            "actor_id": actor_id,
            "nonce": db.new_id("nonce"),
            "action_fingerprint": _fingerprint(fingerprint_payload),
            "tool_name": "docker.restart",
            "target": "container:selected-container",
            "canonical_params": params,
            "risk_level": RiskLevel.MUTATING.value,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "status": "pending",
        }
        db.create_approval(approval)
        db.add_event(session_id, sequence, "approval_required", approval)
        sequence += 1

    report = {
        "id": db.new_id("report"),
        "type": "inspection",
        "health_score": 86,
        "risk_items": [],
        "evidence_refs": evidence_refs,
        "recommended_actions": [
            "Review service-specific logs for any warnings in the last 30 minutes",
            "Keep mutating repairs behind approval",
        ],
        "created_at": datetime.now(UTC).isoformat(),
    }
    db.create_report(report)
    final = {
        "summary": "服务器基础巡检完成。未发现需要立即自动修复的阻塞项。",
        "evidence": summaries,
        "confidence": "medium",
        "report_id": report["id"],
    }
    db.add_event(session_id, sequence, "final_answer", final)
    db.finish_diagnosis(session_id)
    session["status"] = "completed"
    session["updated_at"] = datetime.now(UTC).isoformat()
    return session
