#!/usr/bin/env python3
"""Run the blocking R0 live OpenAI-compatible provider contract probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


RELEASE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
POLICY_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class ProbeError(RuntimeError):
    pass


def _secret_from_environment() -> str:
    direct_names = ("AIOPS_LLM_API_KEY", "AIOPS_DEEPSEEK_API_KEY")
    file_names = ("AIOPS_LLM_API_KEY_FILE", "AIOPS_DEEPSEEK_API_KEY_FILE")
    direct_values = [(name, os.getenv(name)) for name in direct_names if os.getenv(name)]
    file_values = [(name, os.getenv(name)) for name in file_names if os.getenv(name)]
    if len(direct_values) + len(file_values) != 1:
        raise ProbeError("configure exactly one supported LLM API key source")
    if direct_values:
        return str(direct_values[0][1])
    variable, raw_path = file_values[0]
    path = Path(str(raw_path))
    try:
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ProbeError(f"{variable} is not a readable file") from exc
    if not value:
        raise ProbeError(f"{variable} is empty")
    return value


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ProbeError(f"{name} is required")
    return value


def _parse_timestamp(value: Any, *, field: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProbeError(f"{field} must be an ISO-8601 timestamp") from exc
    if timestamp.tzinfo is None:
        raise ProbeError(f"{field} must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _positive_decimal(value: Any, *, field: str, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:  # Decimal exposes multiple conversion exceptions.
        raise ProbeError(f"{field} must be numeric") from exc
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise ProbeError(f"{field} must be {'non-negative' if allow_zero else 'positive'}")
    return parsed


def _load_policy(path: Path, snapshot_path: Path) -> tuple[dict[str, Any], str]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        snapshot = snapshot_path.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("policy and snapshot files must be readable") from exc
    if not isinstance(policy, dict):
        raise ProbeError("policy file must contain a JSON object")
    required_strings = (
        "account_identifier",
        "region",
        "retention",
        "ai_owner",
        "security_owner",
        "privacy_owner",
        "provider_policy_url",
        "provider_policy_sha256",
        "reviewed_at",
        "currency",
    )
    missing = [name for name in required_strings if not str(policy.get(name) or "").strip()]
    if missing:
        raise ProbeError(f"policy file is missing required fields: {', '.join(missing)}")
    if policy.get("training_opt_out_confirmed") is not True:
        raise ProbeError("policy file must confirm training opt-out")
    if policy.get("redacted_operational_data_only") is not True:
        raise ProbeError("policy file must approve redacted operational data only")

    reviewed_at = _parse_timestamp(policy["reviewed_at"], field="reviewed_at")
    now = datetime.now(timezone.utc)
    if reviewed_at > now + timedelta(minutes=5) or now - reviewed_at > timedelta(days=90):
        raise ProbeError("provider policy review must be current within 90 days")

    approvals = policy.get("owner_approvals")
    if not isinstance(approvals, dict):
        raise ProbeError("owner_approvals must be an object")
    for role in ("ai", "security", "privacy"):
        approval = approvals.get(role)
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            raise ProbeError(f"{role} owner approval is required")
        _parse_timestamp(approval.get("approved_at"), field=f"owner_approvals.{role}.approved_at")

    expected_digest = str(policy["provider_policy_sha256"]).lower()
    if not POLICY_DIGEST_RE.fullmatch(expected_digest):
        raise ProbeError("provider_policy_sha256 must use sha256:<64 lowercase hex> format")
    actual_digest = f"sha256:{hashlib.sha256(snapshot).hexdigest()}"
    if actual_digest != expected_digest:
        raise ProbeError("provider policy snapshot does not match provider_policy_sha256")

    for field in (
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "input_cost_per_million_tokens",
        "output_cost_per_million_tokens",
        "cost_cap_per_probe",
    ):
        _positive_decimal(policy.get(field), field=field, allow_zero="cost_" in field)
    max_retries = policy.get("max_retries")
    if not isinstance(max_retries, int) or not 0 <= max_retries <= 3:
        raise ProbeError("max_retries must be an integer from 0 through 3")
    max_output_tokens = policy.get("max_output_tokens")
    if not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 512:
        raise ProbeError("max_output_tokens must be an integer from 1 through 512")
    return policy, actual_digest


def _model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ProbeError("GET /models returned an unexpected schema")
    ids = [str(item.get("id")) for item in payload["data"] if isinstance(item, dict) and item.get("id")]
    if not ids:
        raise ProbeError("GET /models returned no model identifiers")
    return ids


def _verify_live_policy_snapshot(policy: dict[str, Any], *, timeout: float) -> None:
    policy_url = str(policy["provider_policy_url"])
    parsed = urlparse(policy_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProbeError("provider_policy_url must be a credential-free HTTPS URL without query/fragment")
    try:
        response = httpx.get(policy_url, timeout=timeout, follow_redirects=True)
    except httpx.TransportError as exc:
        raise ProbeError("provider policy URL could not be retrieved") from exc
    if response.status_code >= 400:
        raise ProbeError(f"provider policy URL returned HTTP {response.status_code}")
    live_digest = f"sha256:{hashlib.sha256(response.content).hexdigest()}"
    if live_digest != str(policy["provider_policy_sha256"]).lower():
        raise ProbeError("live provider policy no longer matches the reviewed snapshot hash")


def _validate_tool_call(message: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(message, dict) or not isinstance(message.get("tool_calls"), list):
        raise ProbeError("chat completion did not return a tool call")
    calls = message["tool_calls"]
    if len(calls) != 1 or not isinstance(calls[0], dict):
        raise ProbeError("chat completion returned an unexpected tool-call count")
    function = calls[0].get("function")
    if not isinstance(function, dict):
        raise ProbeError("tool call is missing its function object")
    name = str(function.get("name") or "")
    try:
        arguments = json.loads(str(function.get("arguments") or "{}"))
    except json.JSONDecodeError as exc:
        raise ProbeError("tool-call arguments are not valid JSON") from exc
    if name != "probe_echo" or arguments != {"value": "r0-live-contract-probe"}:
        raise ProbeError("tool-call name or arguments did not match the frozen allowlist schema")
    return name, arguments


def _prove_invalid_tool_rejection() -> bool:
    invalid_messages = (
        {"tool_calls": [{"function": {"name": "unknown_tool", "arguments": "{}"}}]},
        {
            "tool_calls": [
                {
                    "function": {
                        "name": "probe_echo",
                        "arguments": '{"value":"r0-live-contract-probe","extra":true}',
                    }
                }
            ]
        },
    )
    for message in invalid_messages:
        try:
            _validate_tool_call(message)
        except ProbeError:
            continue
        return False
    return True


def _redact_for_capture(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED_SECRET]", redacted)
    return redacted


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int,
    attempts: dict[str, int],
    request_name: str,
    **kwargs: Any,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        attempts[request_name] = attempt + 1
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt < max_retries:
                continue
            raise ProbeError(f"{request_name} failed after bounded retries") from exc
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < max_retries:
                continue
        if response.status_code >= 400:
            raise ProbeError(f"{request_name} failed with HTTP {response.status_code}")
        try:
            response.json()
        except json.JSONDecodeError as exc:
            raise ProbeError(f"{request_name} returned invalid JSON") from exc
        return response
    raise ProbeError(f"{request_name} failed") from last_error


def _usage(payload: Any) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise ProbeError("provider response did not include token usage")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        raise ProbeError("provider token usage fields are invalid")
    return prompt_tokens, completion_tokens


def run_probe(
    policy_path: Path,
    snapshot_path: Path,
    *,
    output_path: Path,
    validity_hours: int,
) -> dict[str, Any]:
    base_url = _required_environment("AIOPS_LLM_BASE_URL").rstrip("/")
    model = _required_environment("AIOPS_LLM_MODEL")
    release_digest = _required_environment("AIOPS_RELEASE_DIGEST").lower()
    runner_identity = _required_environment("AIOPS_EVIDENCE_RUNNER_IDENTITY")
    if not RELEASE_DIGEST_RE.fullmatch(release_digest):
        raise ProbeError("AIOPS_RELEASE_DIGEST must use sha256:<64 lowercase hex> format")
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProbeError("AIOPS_LLM_BASE_URL must be a credential-free HTTPS URL without query/fragment")

    key = _secret_from_environment()
    policy, policy_digest = _load_policy(policy_path, snapshot_path)
    connect_timeout = float(policy["connect_timeout_seconds"])
    read_timeout = float(policy["read_timeout_seconds"])
    max_retries = int(policy["max_retries"])
    max_output_tokens = int(policy["max_output_tokens"])
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    attempts: dict[str, int] = {}
    _verify_live_policy_snapshot(policy, timeout=connect_timeout)

    seed = "api_key=R0_SEEDED_SECRET_MUST_NOT_LEAVE_PROCESS"
    captured = _redact_for_capture(seed)
    if seed in captured or "R0_SEEDED_SECRET" in captured or "[REDACTED_SECRET]" not in captured:
        raise ProbeError("seeded-secret redaction capture failed")
    if not _prove_invalid_tool_rejection():
        raise ProbeError("local invalid-tool rejection proof failed")

    timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
    with httpx.Client(headers=headers, timeout=timeout) as client:
        models_response = _request_json(
            client,
            "GET",
            f"{base_url}/models",
            max_retries=max_retries,
            attempts=attempts,
            request_name="models",
        )
        model_ids = _model_ids(models_response.json())
        if model not in model_ids:
            raise ProbeError("configured model is not present in authenticated GET /models")

        probe_value = "r0-live-contract-probe"
        tool_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Call probe_echo exactly once with value r0-live-contract-probe."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "probe_echo",
                        "description": "Return a fixed contract probe value.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "probe_echo"}},
            "max_tokens": min(64, max_output_tokens),
            "stream": False,
        }
        tool_response = _request_json(
            client,
            "POST",
            f"{base_url}/chat/completions",
            max_retries=max_retries,
            attempts=attempts,
            request_name="tool_call",
            json=tool_payload,
        ).json()
        choices = tool_response.get("choices") if isinstance(tool_response, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProbeError("chat completion returned no choices")
        name, arguments = _validate_tool_call(choices[0].get("message"))
        input_tokens, output_tokens = _usage(tool_response)

        stream_payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with R0_STREAM_OK."}],
            "max_tokens": min(32, max_output_tokens),
            "stream": True,
        }
        stream_chunks = 0
        stream_done = False
        attempts["streaming"] = 1
        with client.stream("POST", f"{base_url}/chat/completions", json=stream_payload) as response:
            if response.status_code >= 400:
                raise ProbeError(f"streaming request failed with HTTP {response.status_code}")
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    stream_done = True
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProbeError("stream returned invalid JSON data") from exc
                if isinstance(chunk, dict):
                    stream_chunks += 1
        if stream_chunks == 0 or not stream_done:
            raise ProbeError("stream did not provide JSON chunks followed by [DONE]")

    input_rate = Decimal(str(policy["input_cost_per_million_tokens"]))
    output_rate = Decimal(str(policy["output_cost_per_million_tokens"]))
    estimated_cost = (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate) / Decimal(1_000_000)
    cost_cap = Decimal(str(policy["cost_cap_per_probe"]))
    if estimated_cost > cost_cap:
        raise ProbeError("estimated live-probe cost exceeds approved cap")

    now = datetime.now(timezone.utc)
    models_hash = hashlib.sha256(models_response.content).hexdigest()
    required_checks = {
        "models": True,
        "tool_call": True,
        "streaming": True,
        "invalid_tool_rejection": True,
        "timeout_retry": True,
        "redaction_capture": True,
        "usage_cost": True,
        "policy_snapshot": True,
        "owner_approvals": True,
    }
    manifest = {
        "schema_version": 1,
        "test_id": "GA-R0-001-LLM",
        "result": "pass",
        "executed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=validity_hours)).isoformat(),
        "runner_identity": runner_identity,
        "release_digest": release_digest,
        "evidence_path": str(output_path),
        "provider_origin": base_url,
        "model": model,
        "account_identifier": policy["account_identifier"],
        "region": policy["region"],
        "models_response_sha256": f"sha256:{models_hash}",
        "tool_call": {"name": name, "arguments": arguments, "schema_match": True},
        "invalid_tool_rejection": {
            "unknown_tool_rejected": True,
            "extra_field_rejected": True,
        },
        "stream": {"json_chunks": stream_chunks, "done_received": stream_done},
        "timeout_retry": {
            "connect_timeout_seconds": connect_timeout,
            "read_timeout_seconds": read_timeout,
            "max_retries": max_retries,
            "attempts": attempts,
        },
        "redaction_capture": {
            "seeded_secret_absent": True,
            "replacement_marker_present": True,
            "capture_sha256": f"sha256:{hashlib.sha256(captured.encode('utf-8')).hexdigest()}",
        },
        "usage_cost": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": str(estimated_cost),
            "currency": policy["currency"],
            "approved_cap": str(cost_cap),
        },
        "policy": {
            "region": policy["region"],
            "retention": policy["retention"],
            "training_opt_out_confirmed": True,
            "redacted_operational_data_only": True,
            "ai_owner": policy["ai_owner"],
            "security_owner": policy["security_owner"],
            "privacy_owner": policy["privacy_owner"],
            "owner_approvals": policy["owner_approvals"],
            "provider_policy_url": policy["provider_policy_url"],
            "provider_policy_sha256": policy_digest,
            "reviewed_at": policy["reviewed_at"],
        },
        "required_checks": required_checks,
    }
    serialized = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    if key in serialized or "R0_SEEDED_SECRET" in serialized:
        raise ProbeError("secret material leaked into probe evidence")
    return manifest


def _self_test() -> None:
    if not _prove_invalid_tool_rejection():
        raise ProbeError("invalid-tool rejection self-test failed")
    seed = "api_key=R0_SEEDED_SECRET_MUST_NOT_LEAVE_PROCESS"
    if _redact_for_capture(seed) != "[REDACTED_SECRET]":
        raise ProbeError("redaction self-test failed")

    responses = iter((httpx.Response(503, json={"error": "retry"}), httpx.Response(200, json={"data": []})))

    def handler(request: httpx.Request) -> httpx.Response:
        response = next(responses)
        response.request = request
        return response

    attempts: dict[str, int] = {}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = _request_json(
            client,
            "GET",
            "https://provider.invalid/models",
            max_retries=1,
            attempts=attempts,
            request_name="retry_self_test",
        )
    if response.status_code != 200 or attempts != {"retry_self_test": 2}:
        raise ProbeError("bounded-retry self-test failed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the live R0 provider, local enforcement and policy contract."
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--policy-snapshot-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validity-hours", type=int, default=24)
    args = parser.parse_args()
    if args.self_test:
        try:
            _self_test()
        except ProbeError as exc:
            print(f"LLM contract probe self-test failed: {exc}", file=sys.stderr)
            return 1
        print("LLM contract probe self-test passed")
        return 0
    if args.policy_file is None or args.policy_snapshot_file is None or args.output is None:
        parser.error("--policy-file, --policy-snapshot-file and --output are required for a live probe")
    if not 1 <= args.validity_hours <= 72:
        print("LLM contract probe failed: --validity-hours must be from 1 through 72", file=sys.stderr)
        return 1
    try:
        manifest = run_probe(
            args.policy_file,
            args.policy_snapshot_file,
            output_path=args.output,
            validity_hours=args.validity_hours,
        )
    except ProbeError as exc:
        print(f"LLM contract probe failed: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_name(f".{args.output.name}.tmp-{os.getpid()}")
    try:
        temporary_output.write_text(rendered, encoding="utf-8")
        temporary_output.chmod(0o444)
        temporary_output.replace(args.output)
    finally:
        temporary_output.unlink(missing_ok=True)
    evidence_digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    print(f"AIOPS_LLM_EVIDENCE_SHA256=sha256:{evidence_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
