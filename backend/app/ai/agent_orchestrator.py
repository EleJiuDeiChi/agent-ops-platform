from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from app.ai.providers import OpenAICompatibleChatProvider, LLMProviderError, model_name
from app.models.schemas import RiskLevel, Tool, ToolResult
from app.storage import db
from app.tools.registry import get_tool, invoke_tool, list_tools


SAFE_TOOL_NAME_SEPARATOR = "__"


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(canonical.encode()).hexdigest()


def _safe_tool_name(tool_name: str) -> str:
    return tool_name.replace(".", SAFE_TOOL_NAME_SEPARATOR)


def _original_tool_name(safe_name: str) -> str:
    return safe_name.replace(SAFE_TOOL_NAME_SEPARATOR, ".")


def _json_schema_for(tool: Tool) -> dict[str, Any]:
    properties = tool.input_schema or {}
    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _provider_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": _safe_tool_name(tool.name),
                "description": (
                    f"{tool.description}. Risk level: {tool.risk_level.value}. "
                    f"Approval required: {tool.approval_required}."
                ),
                "parameters": _json_schema_for(tool),
            },
        }
        for tool in list_tools()
        if tool.enabled
    ]


def _system_prompt() -> str:
    return (
        "你是一个面向中小团队和入门用户的服务器运维 Agent。"
        "你只能通过提供的工具了解服务器真实状态，不能编造检查结果，不能要求用户去手动执行命令。"
        "优先使用只读工具收集证据。涉及重启、修改、删除、写入配置等动作时，只能提出需要审批的动作。"
        "最后必须用中文输出一个 JSON 对象，不要 Markdown，不要代码块。"
        "JSON 字段为：summary 字符串，health_score 0-100 数字，risk_items 数组，"
        "recommended_actions 数组，confidence high/medium/low。"
        "summary 要让刚入门的用户能看懂。risk_items 每项包含 title、level、evidence、impact。"
    )


def _compact_tool_result(result: ToolResult) -> str:
    payload = {
        "tool_name": result.tool_name,
        "status": result.status.value,
        "output_summary": result.output_summary,
        "output_ref": result.output_ref,
        "error": result.error,
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return text[:2500]


def _parse_tool_args(raw: str | None, tool: Tool) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    allowed = set(tool.input_schema.keys())
    if not allowed:
        return {}
    return {key: value for key, value in parsed.items() if key in allowed}


def _approval_for(
    actor_id: str,
    auth_session_id: str,
    session_id: str,
    tool_name: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    target = "server"
    if tool_name.startswith("docker.") and params.get("container"):
        target = f"container:{params['container']}"
    fingerprint_payload = {
        "actor_id": actor_id,
        "auth_session_id": auth_session_id,
        "diagnosis_session_id": session_id,
        "tool_name": tool_name,
        "target": target,
        "canonical_params": params,
    }
    approval = {
        "id": db.new_id("appr"),
        "auth_session_id": auth_session_id,
        "diagnosis_session_id": session_id,
        "actor_id": actor_id,
        "nonce": db.new_id("nonce"),
        "action_fingerprint": _fingerprint(fingerprint_payload),
        "tool_name": tool_name,
        "target": target,
        "canonical_params": params,
        "risk_level": get_tool(tool_name).risk_level.value,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "status": "pending",
    }
    db.create_approval(approval)
    return approval


def _assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices") or []
    if not choices:
        raise LLMProviderError("LLM response did not include choices")
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        raise LLMProviderError("LLM response message is invalid")
    return message


def _tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls = message.get("tool_calls") or []
    return tool_calls if isinstance(tool_calls, list) else []


def _final_payload(content: str, evidence_refs: list[str]) -> dict[str, Any]:
    raw = content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {
            "summary": raw or "真实 AI 诊断完成，但模型没有返回结构化结论。",
            "health_score": 75,
            "risk_items": [],
            "recommended_actions": ["查看工具证据后再决定是否执行修复动作。"],
            "confidence": "medium",
        }
    if not isinstance(parsed, dict):
        parsed = {"summary": str(parsed)}
    summary = str(parsed.get("summary") or "真实 AI 诊断完成。")
    risk_items = parsed.get("risk_items")
    recommended_actions = parsed.get("recommended_actions")
    health_score = parsed.get("health_score", 75)
    try:
        health_score_int = max(0, min(100, int(health_score)))
    except (TypeError, ValueError):
        health_score_int = 75
    normalized_risks = risk_items if isinstance(risk_items, list) else []
    evidence_summaries = [
        str(item.get("evidence"))
        for item in normalized_risks
        if isinstance(item, dict) and item.get("evidence")
    ][:4]
    return {
        "summary": summary,
        "evidence": evidence_summaries or evidence_refs,
        "confidence": str(parsed.get("confidence") or "medium"),
        "health_score": health_score_int,
        "risk_items": normalized_risks,
        "recommended_actions": recommended_actions if isinstance(recommended_actions, list) else [],
    }


def _is_json_object(content: str) -> bool:
    try:
        return isinstance(json.loads(content.strip()), dict)
    except json.JSONDecodeError:
        return False


def _structured_final_content(
    provider: OpenAICompatibleChatProvider,
    messages: list[dict[str, Any]],
    fallback_content: str,
) -> str:
    if _is_json_object(fallback_content):
        return fallback_content
    response = provider.chat_completion(
        messages
        + [
            {
                "role": "user",
                "content": (
                    "请把上面的诊断过程整理成严格 JSON 对象，只返回 JSON，不要 Markdown。"
                    "字段：summary 字符串，health_score 0-100 数字，risk_items 数组，"
                    "recommended_actions 数组，confidence high/medium/low。"
                    "risk_items 每项包含 title、level、evidence、impact。"
                ),
            }
        ],
        tools=None,
        tool_choice="none",
        response_format={"type": "json_object"},
    )
    return str(_assistant_message(response).get("content") or fallback_content)


def _error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "summary": f"真实 AI 诊断失败：{exc}",
        "evidence": [],
        "confidence": "low",
        "health_score": 0,
        "risk_items": [{"title": "AI 模型调用失败", "level": "error", "evidence": str(exc), "impact": "无法生成真实 AI 结论"}],
        "recommended_actions": ["检查模型 key、余额、网络和 AIOPS_LLM_* 配置。"],
    }


def run_llm_diagnosis(actor_id: str, auth_session_id: str, question: str) -> dict:
    session = db.create_diagnosis(actor_id, question)
    session_id = session["id"]
    sequence = 1
    provider = OpenAICompatibleChatProvider()
    evidence_refs: list[str] = []

    db.add_event(
        session_id,
        sequence,
        "plan",
        {
            "steps": [
                f"把问题交给真实模型 {provider.model}",
                "由模型选择需要调用的白名单工具",
                "把真实工具结果交回模型生成结论",
                "涉及修改或重启动作时进入人工审批",
            ],
        },
    )
    sequence += 1

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": question},
    ]
    tools = _provider_tools()
    max_turns = int(os.getenv("AIOPS_AGENT_MAX_TURNS", "6"))

    try:
        for _ in range(max_turns):
            response = provider.chat_completion(messages, tools=tools, tool_choice="auto")
            assistant_message = _assistant_message(response)
            messages.append(assistant_message)
            calls = _tool_calls(assistant_message)
            if not calls:
                final_content = _structured_final_content(
                    provider,
                    messages,
                    str(assistant_message.get("content") or ""),
                )
                final = _final_payload(final_content, evidence_refs)
                report_id = db.new_id("report")
                final["report_id"] = report_id
                db.create_report(
                    {
                        "id": report_id,
                        "type": "diagnosis",
                        "health_score": final["health_score"],
                        "risk_items": final["risk_items"],
                        "evidence_refs": evidence_refs,
                        "recommended_actions": final["recommended_actions"],
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                db.add_event(session_id, sequence, "final_answer", final)
                db.finish_diagnosis(session_id)
                session["status"] = "completed"
                session["updated_at"] = datetime.now(UTC).isoformat()
                return session

            for call in calls:
                function = call.get("function") or {}
                safe_name = str(function.get("name") or "")
                tool_name = _original_tool_name(safe_name)
                tool = get_tool(tool_name)
                params = _parse_tool_args(function.get("arguments"), tool)
                db.add_event(session_id, sequence, "tool_call", {"tool_name": tool_name, "params": params})
                sequence += 1

                if tool.approval_required or tool.risk_level != RiskLevel.READ:
                    approval = _approval_for(actor_id, auth_session_id, session_id, tool_name, params)
                    db.add_event(session_id, sequence, "approval_required", approval)
                    sequence += 1
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id"),
                            "content": json.dumps(
                                {
                                    "status": "approval_required",
                                    "approval_id": approval["id"],
                                    "message": "This action requires human approval and has not been executed.",
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                    continue

                result = invoke_tool(tool_name, params)
                output_ref = db.store_tool_result(actor_id, result.model_dump(mode="json"))
                result.output_ref = output_ref
                evidence_refs.append(output_ref)
                db.insert_audit(
                    {
                        "id": db.new_id("audit"),
                        "actor_id": actor_id,
                        "event_type": "tool_invocation",
                        "resource": tool_name,
                        "risk_level": tool.risk_level.value,
                        "status": result.status.value,
                        "summary": result.output_summary,
                        "created_at": datetime.now(UTC).isoformat(),
                    }
                )
                db.add_event(session_id, sequence, "tool_result", result.model_dump(mode="json"))
                sequence += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "content": _compact_tool_result(result),
                    }
                )

        raise LLMProviderError("Agent reached max turns before final answer")
    except Exception as exc:
        final = _error_payload(exc)
        report_id = db.new_id("report")
        final["report_id"] = report_id
        db.create_report(
            {
                "id": report_id,
                "type": "diagnosis",
                "health_score": final["health_score"],
                "risk_items": final["risk_items"],
                "evidence_refs": evidence_refs,
                "recommended_actions": final["recommended_actions"],
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        db.add_event(session_id, sequence, "error", {"message": str(exc), "model": model_name()})
        db.add_event(session_id, sequence + 1, "final_answer", final)
        db.finish_diagnosis(session_id, "error")
        session["status"] = "error"
        session["updated_at"] = datetime.now(UTC).isoformat()
        return session


def run_deepseek_diagnosis(actor_id: str, auth_session_id: str, question: str) -> dict:
    """Backward-compatible entrypoint for callers deployed before multi-provider support."""
    return run_llm_diagnosis(actor_id, auth_session_id, question)
