#!/usr/bin/env python3
"""Run the blocking R0 live OpenAI-compatible provider contract probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.ai.provider_profiles import (  # noqa: E402
    PROVIDER_PROFILES,
    LLMProviderProfile,
    get_provider_profile,
)


RELEASE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
POLICY_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class ProbeError(RuntimeError):
    pass


def _secret_from_environment(provider_id: str) -> str:
    provider_by_name = {"AIOPS_LLM_API_KEY": None}
    provider_by_name.update(
        {
            profile.api_key_variable: profile_id
            for profile_id, profile in PROVIDER_PROFILES.items()
            if profile.api_key_variable
        }
    )
    direct_values = [
        (name, owner, os.getenv(name))
        for name, owner in provider_by_name.items()
        if os.getenv(name)
    ]
    file_values = [
        (f"{name}_FILE", owner, os.getenv(f"{name}_FILE"))
        for name, owner in provider_by_name.items()
        if os.getenv(f"{name}_FILE")
    ]
    if direct_values:
        raise ProbeError(
            "live probes require a supported *_API_KEY_FILE; "
            "direct API key environment values are forbidden"
        )
    if len(file_values) != 1:
        raise ProbeError("configure exactly one supported LLM *_API_KEY_FILE source")
    variable, owner, raw_path = file_values[0]
    if owner is not None and owner != provider_id:
        raise ProbeError(f"{variable} does not match AIOPS_LLM_MODE")
    path = Path(str(raw_path))
    try:
        if path.is_symlink():
            raise ProbeError(f"{variable} must not be a symlink")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ProbeError(f"{variable} must reference a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise ProbeError(f"{variable} must not be accessible by group or other")
        if metadata.st_uid not in {0, os.geteuid()}:
            raise ProbeError(f"{variable} must be owned by root or the probe operator")
        if metadata.st_size <= 0 or metadata.st_size > 16 * 1024:
            raise ProbeError(f"{variable} has an invalid size")
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


def _validate_provider_feature_flag(provider_id: str) -> None:
    raw = _required_environment("AIOPS_LLM_ENABLED_PROVIDERS")
    enabled = {
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    }
    if enabled != {provider_id}:
        raise ProbeError(
            "AIOPS_LLM_ENABLED_PROVIDERS must contain exactly AIOPS_LLM_MODE"
        )


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
    except (InvalidOperation, ValueError) as exc:
        raise ProbeError(f"{field} must be numeric") from exc
    if not parsed.is_finite():
        raise ProbeError(f"{field} must be finite")
    if parsed < 0 or (parsed == 0 and not allow_zero):
        raise ProbeError(f"{field} must be {'non-negative' if allow_zero else 'positive'}")
    return parsed


def _validated_https_source_url(value: Any, *, field: str) -> str:
    source_url = str(value or "").strip()
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ProbeError(
            f"{field} must be a credential-free HTTPS URL without query/fragment"
        )
    return source_url


def _load_policy(
    path: Path,
    snapshot_path: Path,
    quota_pricing_snapshot_path: Path,
    *,
    provider_id: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
        snapshot = snapshot_path.read_bytes()
        quota_pricing_snapshot_raw = quota_pricing_snapshot_path.read_bytes()
        quota_pricing_snapshot = json.loads(
            quota_pricing_snapshot_raw.decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("policy and evidence snapshot files must be readable") from exc
    if not isinstance(policy, dict):
        raise ProbeError("policy file must contain a JSON object")
    if not isinstance(quota_pricing_snapshot, dict):
        raise ProbeError("quota and pricing snapshot must contain a JSON object")
    required_strings = (
        "provider_id",
        "account_identifier",
        "region",
        "retention",
        "ai_owner",
        "security_owner",
        "privacy_owner",
        "provider_policy_url",
        "provider_policy_sha256",
        "reviewed_at",
        "quota_pricing_source_url",
        "quota_pricing_snapshot_sha256",
        "quota_pricing_reviewed_at",
        "account_tier",
        "currency",
    )
    missing = [name for name in required_strings if not str(policy.get(name) or "").strip()]
    if missing:
        raise ProbeError(f"policy file is missing required fields: {', '.join(missing)}")
    if policy["provider_id"] != provider_id:
        raise ProbeError("policy provider_id does not match AIOPS_LLM_MODE")
    if policy.get("training_opt_out_confirmed") is not True:
        raise ProbeError("policy file must confirm training opt-out")
    if policy.get("redacted_operational_data_only") is not True:
        raise ProbeError("policy file must approve redacted operational data only")

    reviewed_at = _parse_timestamp(policy["reviewed_at"], field="reviewed_at")
    quota_pricing_reviewed_at = _parse_timestamp(
        policy["quota_pricing_reviewed_at"],
        field="quota_pricing_reviewed_at",
    )
    now = datetime.now(timezone.utc)
    if reviewed_at > now + timedelta(minutes=5) or now - reviewed_at > timedelta(days=90):
        raise ProbeError("provider policy review must be current within 90 days")
    if (
        quota_pricing_reviewed_at > now + timedelta(minutes=5)
        or now - quota_pricing_reviewed_at > timedelta(days=30)
    ):
        raise ProbeError("quota and pricing review must be current within 30 days")

    expected_digest = str(policy["provider_policy_sha256"]).lower()
    if not POLICY_DIGEST_RE.fullmatch(expected_digest):
        raise ProbeError("provider_policy_sha256 must use sha256:<64 lowercase hex> format")
    actual_digest = f"sha256:{hashlib.sha256(snapshot).hexdigest()}"
    if actual_digest != expected_digest:
        raise ProbeError("provider policy snapshot does not match provider_policy_sha256")

    _validated_https_source_url(
        policy["quota_pricing_source_url"],
        field="quota_pricing_source_url",
    )
    expected_quota_pricing_digest = str(
        policy["quota_pricing_snapshot_sha256"]
    ).lower()
    if not POLICY_DIGEST_RE.fullmatch(expected_quota_pricing_digest):
        raise ProbeError(
            "quota_pricing_snapshot_sha256 must use sha256:<64 lowercase hex> format"
        )
    actual_quota_pricing_digest = (
        f"sha256:{hashlib.sha256(quota_pricing_snapshot_raw).hexdigest()}"
    )
    if actual_quota_pricing_digest != expected_quota_pricing_digest:
        raise ProbeError(
            "quota and pricing snapshot does not match quota_pricing_snapshot_sha256"
        )

    for field in (
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "cache_hit_input_cost_per_million_tokens",
        "input_cost_per_million_tokens",
        "output_cost_per_million_tokens",
        "cost_cap_per_probe",
    ):
        _positive_decimal(policy.get(field), field=field, allow_zero="cost_" in field)
    max_retries = policy.get("max_retries")
    if (
        not isinstance(max_retries, int)
        or isinstance(max_retries, bool)
        or not 0 <= max_retries <= 3
    ):
        raise ProbeError("max_retries must be an integer from 0 through 3")
    max_output_tokens = policy.get("max_output_tokens")
    if (
        not isinstance(max_output_tokens, int)
        or isinstance(max_output_tokens, bool)
        or not 1 <= max_output_tokens <= 512
    ):
        raise ProbeError("max_output_tokens must be an integer from 1 through 512")
    max_input_tokens_per_probe = policy.get("max_input_tokens_per_probe")
    if (
        not isinstance(max_input_tokens_per_probe, int)
        or isinstance(max_input_tokens_per_probe, bool)
        or not 1 <= max_input_tokens_per_probe <= 4096
    ):
        raise ProbeError(
            "max_input_tokens_per_probe must be an integer from 1 through 4096"
        )
    quota = policy.get("quota")
    if not isinstance(quota, dict):
        raise ProbeError("quota must be an object")
    for field in (
        "concurrency_limit",
        "requests_per_minute",
        "tokens_per_minute",
        "tokens_per_day",
    ):
        value = quota.get(field)
        if not (
            (isinstance(value, int) and not isinstance(value, bool) and value > 0)
            or (
                isinstance(value, str)
                and value in {"unlimited", "provider-managed", "account-tier"}
            )
        ):
            raise ProbeError(
                f"quota.{field} must be a positive integer or an approved provider limit marker"
            )
    _positive_decimal(
        quota.get("balance_alert_threshold"),
        field="quota.balance_alert_threshold",
        allow_zero=True,
    )

    snapshot_fields = {
        "schema_version",
        "provider_id",
        "account_identifier",
        "account_tier",
        "source_url",
        "source_content_sha256",
        "reviewed_at",
        "currency",
        "prices_per_million_tokens",
        "quota",
    }
    if set(quota_pricing_snapshot) != snapshot_fields:
        raise ProbeError(
            "quota and pricing snapshot must contain exactly the documented fields"
        )
    if quota_pricing_snapshot.get("schema_version") != 1:
        raise ProbeError("quota and pricing snapshot schema_version must be 1")
    string_bindings = {
        "provider_id": "provider_id",
        "account_identifier": "account_identifier",
        "account_tier": "account_tier",
        "source_url": "quota_pricing_source_url",
        "currency": "currency",
    }
    for snapshot_field, policy_field in string_bindings.items():
        snapshot_value = quota_pricing_snapshot.get(snapshot_field)
        if not isinstance(snapshot_value, str) or not snapshot_value.strip():
            raise ProbeError(
                f"quota and pricing snapshot {snapshot_field} must be a non-empty string"
            )
        if snapshot_value != policy[policy_field]:
            raise ProbeError(
                f"quota and pricing snapshot {snapshot_field} does not match policy"
            )
    _validated_https_source_url(
        quota_pricing_snapshot["source_url"],
        field="quota_pricing_snapshot.source_url",
    )
    source_content_digest = str(
        quota_pricing_snapshot.get("source_content_sha256") or ""
    ).lower()
    if not POLICY_DIGEST_RE.fullmatch(source_content_digest):
        raise ProbeError(
            "quota and pricing snapshot source_content_sha256 must use "
            "sha256:<64 lowercase hex> format"
        )
    snapshot_reviewed_at = _parse_timestamp(
        quota_pricing_snapshot.get("reviewed_at"),
        field="quota_pricing_snapshot.reviewed_at",
    )
    if snapshot_reviewed_at != quota_pricing_reviewed_at:
        raise ProbeError(
            "quota and pricing snapshot reviewed_at does not match policy"
        )

    snapshot_prices = quota_pricing_snapshot.get("prices_per_million_tokens")
    price_bindings = {
        "cache_hit_input": "cache_hit_input_cost_per_million_tokens",
        "cache_miss_input": "input_cost_per_million_tokens",
        "output": "output_cost_per_million_tokens",
    }
    if not isinstance(snapshot_prices, dict) or set(snapshot_prices) != set(
        price_bindings
    ):
        raise ProbeError(
            "quota and pricing snapshot prices_per_million_tokens is incomplete"
        )
    for snapshot_field, policy_field in price_bindings.items():
        snapshot_price = _positive_decimal(
            snapshot_prices.get(snapshot_field),
            field=(
                "quota_pricing_snapshot.prices_per_million_tokens."
                f"{snapshot_field}"
            ),
            allow_zero=True,
        )
        policy_price = _positive_decimal(
            policy.get(policy_field),
            field=policy_field,
            allow_zero=True,
        )
        if snapshot_price != policy_price:
            raise ProbeError(
                "quota and pricing snapshot prices_per_million_tokens."
                f"{snapshot_field} does not match policy"
            )

    snapshot_quota = quota_pricing_snapshot.get("quota")
    quota_fields = {
        "concurrency_limit",
        "requests_per_minute",
        "tokens_per_minute",
        "tokens_per_day",
        "balance_alert_threshold",
    }
    if not isinstance(snapshot_quota, dict) or set(snapshot_quota) != quota_fields:
        raise ProbeError("quota and pricing snapshot quota is incomplete")
    for field in quota_fields - {"balance_alert_threshold"}:
        if snapshot_quota.get(field) != quota[field]:
            raise ProbeError(
                f"quota and pricing snapshot quota.{field} does not match policy"
            )
    snapshot_balance_threshold = _positive_decimal(
        snapshot_quota.get("balance_alert_threshold"),
        field="quota_pricing_snapshot.quota.balance_alert_threshold",
        allow_zero=True,
    )
    policy_balance_threshold = _positive_decimal(
        quota.get("balance_alert_threshold"),
        field="quota.balance_alert_threshold",
        allow_zero=True,
    )
    if snapshot_balance_threshold != policy_balance_threshold:
        raise ProbeError(
            "quota and pricing snapshot quota.balance_alert_threshold does not match policy"
        )

    approvals = policy.get("owner_approvals")
    if not isinstance(approvals, dict):
        raise ProbeError("owner_approvals must be an object")
    approver_identities: set[str] = set()
    for role in ("ai", "security", "privacy"):
        approval = approvals.get(role)
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            raise ProbeError(f"{role} owner approval is required")
        if approval.get("role") != role:
            raise ProbeError(f"{role} owner approval role does not match")
        identity = str(approval.get("approver_identity") or "").strip()
        if not identity or identity != policy[f"{role}_owner"]:
            raise ProbeError(
                f"{role} owner approval identity does not match {role}_owner"
            )
        normalized_identity = identity.casefold()
        if normalized_identity in approver_identities:
            raise ProbeError("AI, security and privacy approvals require unique identities")
        approver_identities.add(normalized_identity)
        if str(approval.get("provider_policy_sha256") or "").lower() != actual_digest:
            raise ProbeError(
                f"{role} owner approval is not bound to provider policy snapshot"
            )
        if (
            str(approval.get("quota_pricing_snapshot_sha256") or "").lower()
            != actual_quota_pricing_digest
        ):
            raise ProbeError(
                f"{role} owner approval is not bound to quota and pricing snapshot"
            )
        approved_at = _parse_timestamp(
            approval.get("approved_at"),
            field=f"owner_approvals.{role}.approved_at",
        )
        if approved_at > now + timedelta(minutes=5):
            raise ProbeError(f"{role} owner approval timestamp is in the future")
        if approved_at < max(reviewed_at, quota_pricing_reviewed_at):
            raise ProbeError(
                f"{role} owner approval predates the reviewed evidence snapshots"
            )

    return policy, actual_digest, quota_pricing_snapshot


def _model_ids(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ProbeError("GET /models returned an unexpected schema")
    ids = [str(item.get("id")) for item in payload["data"] if isinstance(item, dict) and item.get("id")]
    if not ids:
        raise ProbeError("GET /models returned no model identifiers")
    return ids


def _validate_response_model(payload: Any, configured_model: str) -> None:
    if not isinstance(payload, dict) or payload.get("model") != configured_model:
        raise ProbeError("provider response model does not match configured model")


def _validate_stream_content_type(value: str) -> str:
    normalized = value.split(";", 1)[0].strip().lower()
    if normalized != "text/event-stream":
        raise ProbeError("streaming response Content-Type must be text/event-stream")
    return normalized


def _validate_stream_event(
    chunk: Any,
    configured_model: str,
    provider_id: str,
) -> tuple[str, tuple[int, int, int] | None]:
    if not isinstance(chunk, dict) or chunk.get("error") is not None:
        raise ProbeError("stream returned an error or invalid event object")
    _validate_response_model(chunk, configured_model)
    choices = chunk.get("choices")
    if not isinstance(choices, list):
        raise ProbeError("stream event is missing choices")
    content = ""
    if choices:
        first_choice = choices[0]
        delta = first_choice.get("delta") if isinstance(first_choice, dict) else None
        if not isinstance(delta, dict):
            raise ProbeError("stream event delta is invalid")
        raw_content = delta.get("content")
        if raw_content is not None and not isinstance(raw_content, str):
            raise ProbeError("stream event content is invalid")
        content = raw_content or ""
    raw_usage = chunk.get("usage")
    if raw_usage is None and choices and isinstance(choices[0], dict):
        raw_usage = choices[0].get("usage")
    usage = None
    if raw_usage is not None:
        usage = _usage({"usage": raw_usage}, provider_id=provider_id)
    if not choices and usage is None:
        raise ProbeError("stream event has neither choices nor usage")
    return content, usage


def _validate_stream_contract(
    content: str,
    usage: tuple[int, int, int] | None,
) -> None:
    if "R0_STREAM_OK" not in content:
        raise ProbeError("stream did not contain the required contract marker")
    if usage is None:
        raise ProbeError("stream did not report token usage")


def _verify_live_snapshot(
    source_url: Any,
    expected_digest: Any,
    *,
    timeout: float,
    label: str,
) -> None:
    url = _validated_https_source_url(source_url, field=f"{label}_url")
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
    except httpx.TransportError as exc:
        raise ProbeError(f"{label} URL could not be retrieved") from exc
    if response.status_code >= 400:
        raise ProbeError(f"{label} URL returned HTTP {response.status_code}")
    live_digest = f"sha256:{hashlib.sha256(response.content).hexdigest()}"
    if live_digest != str(expected_digest).lower():
        raise ProbeError(f"live {label} no longer matches the reviewed snapshot hash")


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


def _usage(
    payload: Any,
    *,
    provider_id: str,
) -> tuple[int, int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        raise ProbeError("provider response did not include token usage")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if (
        not isinstance(prompt_tokens, int)
        or isinstance(prompt_tokens, bool)
        or not isinstance(completion_tokens, int)
        or isinstance(completion_tokens, bool)
    ):
        raise ProbeError("provider token usage fields are invalid")
    profile = get_provider_profile(provider_id)
    cached_tokens: Any = 0
    if profile.cache_usage_format == "deepseek_cache_fields":
        cached_tokens = usage.get("prompt_cache_hit_tokens")
        cache_miss_tokens = usage.get("prompt_cache_miss_tokens")
        if (
            not isinstance(cached_tokens, int)
            or isinstance(cached_tokens, bool)
            or cached_tokens < 0
            or not isinstance(cache_miss_tokens, int)
            or isinstance(cache_miss_tokens, bool)
            or cache_miss_tokens < 0
        ):
            raise ProbeError("DeepSeek cache-miss token usage is invalid")
        if cached_tokens + cache_miss_tokens != prompt_tokens:
            raise ProbeError("DeepSeek cache token usage does not sum to prompt_tokens")
    elif profile.cache_usage_format == "kimi_cached_tokens":
        cached_tokens = usage.get("cached_tokens")
    elif profile.cache_usage_format == "prompt_tokens_details":
        details = usage.get("prompt_tokens_details")
        if not isinstance(details, dict):
            raise ProbeError("provider prompt token details are invalid")
        cached_tokens = details.get("cached_tokens")
    else:
        raise ProbeError("provider cache usage format is unsupported")
    if not isinstance(cached_tokens, int) or isinstance(cached_tokens, bool):
        raise ProbeError("provider cached token usage is invalid")
    if (
        prompt_tokens < 0
        or completion_tokens < 0
        or cached_tokens < 0
        or cached_tokens > prompt_tokens
    ):
        raise ProbeError("provider token usage values are invalid")
    return prompt_tokens, cached_tokens, completion_tokens


def _estimated_cost(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    input_rate: Decimal,
    cached_input_rate: Decimal,
    output_rate: Decimal,
) -> Decimal:
    cache_miss_tokens = input_tokens - cached_input_tokens
    return (
        Decimal(cache_miss_tokens) * input_rate
        + Decimal(cached_input_tokens) * cached_input_rate
        + Decimal(output_tokens) * output_rate
    ) / Decimal(1_000_000)


def _enforce_cost_cap(cost: Decimal, cap: Decimal, *, phase: str) -> None:
    if cost > cap:
        raise ProbeError(f"{phase} live-probe cost exceeds approved cap")


def _probe_payloads(
    profile: LLMProviderProfile,
    *,
    model: str,
    max_output_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tool_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Call probe_echo exactly once with value "
                    "r0-live-contract-probe."
                ),
            }
        ],
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
        "tool_choice": (
            {"type": "function", "function": {"name": "probe_echo"}}
            if profile.supports_named_tool_choice
            else "auto"
        ),
        "max_tokens": min(64, max_output_tokens),
        "stream": False,
    }
    stream_payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with R0_STREAM_OK."}],
        "max_tokens": min(32, max_output_tokens),
        "stream": True,
    }
    if profile.supports_thinking:
        tool_payload["thinking"] = {"type": "disabled"}
        stream_payload["thinking"] = {"type": "disabled"}
    if profile.supports_stream_usage_option:
        stream_payload["stream_options"] = {"include_usage": True}
    return tool_payload, stream_payload


def run_probe(
    policy_path: Path,
    snapshot_path: Path,
    quota_pricing_snapshot_path: Path,
    *,
    output_path: Path,
    validity_hours: int,
) -> dict[str, Any]:
    provider_id = _required_environment("AIOPS_LLM_MODE").lower()
    try:
        profile = get_provider_profile(provider_id)
    except ValueError as exc:
        raise ProbeError(str(exc)) from exc
    required_capabilities = {
        "streaming": profile.supports_streaming,
        "tool_calls": profile.supports_tool_calls,
        "usage_reporting": profile.reports_usage,
    }
    if not all(required_capabilities.values()):
        raise ProbeError("provider profile does not declare the complete R0 live contract")
    _validate_provider_feature_flag(provider_id)
    base_url = _required_environment("AIOPS_LLM_BASE_URL").rstrip("/")
    model = _required_environment("AIOPS_LLM_MODEL")
    release_digest = _required_environment("AIOPS_RELEASE_DIGEST").lower()
    commit_sha = _required_environment("AIOPS_COMMIT_SHA").lower()
    runner_identity = _required_environment("AIOPS_EVIDENCE_RUNNER_IDENTITY")
    if not RELEASE_DIGEST_RE.fullmatch(release_digest):
        raise ProbeError("AIOPS_RELEASE_DIGEST must use sha256:<64 lowercase hex> format")
    if not COMMIT_SHA_RE.fullmatch(commit_sha):
        raise ProbeError("AIOPS_COMMIT_SHA must be a full lowercase Git SHA")
    try:
        profile.validate_base_url(base_url, production=True)
    except ValueError as exc:
        raise ProbeError(str(exc)) from exc

    key = _secret_from_environment(provider_id)
    policy, policy_digest, quota_pricing_snapshot = _load_policy(
        policy_path,
        snapshot_path,
        quota_pricing_snapshot_path,
        provider_id=provider_id,
    )
    connect_timeout = float(policy["connect_timeout_seconds"])
    read_timeout = float(policy["read_timeout_seconds"])
    max_retries = int(policy["max_retries"])
    max_output_tokens = int(policy["max_output_tokens"])
    max_input_tokens_per_probe = int(policy["max_input_tokens_per_probe"])
    input_rate = Decimal(str(policy["input_cost_per_million_tokens"]))
    cached_input_rate = Decimal(
        str(policy["cache_hit_input_cost_per_million_tokens"])
    )
    output_rate = Decimal(str(policy["output_cost_per_million_tokens"]))
    cost_cap = Decimal(str(policy["cost_cap_per_probe"]))
    tool_payload, stream_payload = _probe_payloads(
        profile,
        model=model,
        max_output_tokens=max_output_tokens,
    )
    tool_attempt_budget = max_retries + 1
    tool_payload_byte_bound = len(
        json.dumps(tool_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    stream_payload_byte_bound = len(
        json.dumps(stream_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )
    input_payload_byte_upper_bound = (
        tool_payload_byte_bound * tool_attempt_budget + stream_payload_byte_bound
    )
    if input_payload_byte_upper_bound > max_input_tokens_per_probe:
        raise ProbeError(
            "serialized live-probe payload byte bound exceeds max_input_tokens_per_probe"
        )
    preflight_cost = _estimated_cost(
        input_tokens=input_payload_byte_upper_bound,
        cached_input_tokens=0,
        output_tokens=(
            int(tool_payload["max_tokens"]) * tool_attempt_budget
            + int(stream_payload["max_tokens"])
        ),
        input_rate=input_rate,
        cached_input_rate=cached_input_rate,
        output_rate=output_rate,
    )
    _enforce_cost_cap(preflight_cost, cost_cap, phase="worst-case preflight")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    attempts: dict[str, int] = {}
    _verify_live_snapshot(
        policy["provider_policy_url"],
        policy["provider_policy_sha256"],
        timeout=connect_timeout,
        label="provider policy",
    )
    _verify_live_snapshot(
        policy["quota_pricing_source_url"],
        quota_pricing_snapshot["source_content_sha256"],
        timeout=connect_timeout,
        label="quota and pricing source",
    )

    seed = "api_key=R0_SEEDED_SECRET_MUST_NOT_LEAVE_PROCESS"
    captured = _redact_for_capture(seed)
    if seed in captured or "R0_SEEDED_SECRET" in captured or "[REDACTED_SECRET]" not in captured:
        raise ProbeError("seeded-secret redaction capture failed")
    if not _prove_invalid_tool_rejection():
        raise ProbeError("local invalid-tool rejection proof failed")

    timeout = httpx.Timeout(connect=connect_timeout, read=read_timeout, write=read_timeout, pool=connect_timeout)
    with httpx.Client(headers=headers, timeout=timeout) as client:
        models_response: httpx.Response | None = None
        if profile.models_path:
            models_response = _request_json(
                client,
                "GET",
                f"{base_url}{profile.models_path}",
                max_retries=max_retries,
                attempts=attempts,
                request_name="model_discovery",
            )
            model_ids = _model_ids(models_response.json())
            if model not in model_ids:
                raise ProbeError("configured model is not present in authenticated model discovery")

        tool_response = _request_json(
            client,
            "POST",
            f"{base_url}{profile.chat_path}",
            max_retries=max_retries,
            attempts=attempts,
            request_name="tool_call",
            json=tool_payload,
        ).json()
        _validate_response_model(tool_response, model)
        choices = tool_response.get("choices") if isinstance(tool_response, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProbeError("chat completion returned no choices")
        name, arguments = _validate_tool_call(choices[0].get("message"))
        tool_input_tokens, tool_cached_input_tokens, tool_output_tokens = _usage(
            tool_response,
            provider_id=provider_id,
        )
        discovery_content = (
            models_response.content
            if models_response is not None
            else json.dumps(tool_response, sort_keys=True).encode("utf-8")
        )
        if models_response is None:
            attempts["model_discovery"] = attempts["tool_call"]

        stream_chunks = 0
        stream_done = False
        stream_content: list[str] = []
        stream_usage: tuple[int, int, int] | None = None
        normalized_stream_events: list[str] = []
        stream_content_type = ""
        attempts["streaming"] = 1
        with client.stream("POST", f"{base_url}{profile.chat_path}", json=stream_payload) as response:
            if response.status_code >= 400:
                raise ProbeError(f"streaming request failed with HTTP {response.status_code}")
            stream_content_type = _validate_stream_content_type(
                response.headers.get("content-type", "")
            )
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    stream_done = True
                    normalized_stream_events.append("data:[DONE]")
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProbeError("stream returned invalid JSON data") from exc
                content, event_usage = _validate_stream_event(
                    chunk,
                    model,
                    provider_id,
                )
                normalized_stream_events.append(
                    "data:"
                    + json.dumps(
                        chunk,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
                if content:
                    stream_content.append(content)
                if event_usage is not None:
                    stream_usage = event_usage
                stream_chunks += 1
        if stream_chunks == 0 or not stream_done:
            raise ProbeError("stream did not provide JSON chunks followed by [DONE]")
        _validate_stream_contract("".join(stream_content), stream_usage)

    normalized_stream_capture = "\n".join(normalized_stream_events).encode("utf-8")
    stream_capture_sha256 = f"sha256:{hashlib.sha256(normalized_stream_capture).hexdigest()}"

    stream_input_tokens, stream_cached_input_tokens, stream_output_tokens = stream_usage
    input_tokens = tool_input_tokens + stream_input_tokens
    cached_input_tokens = tool_cached_input_tokens + stream_cached_input_tokens
    output_tokens = tool_output_tokens + stream_output_tokens
    if input_tokens > input_payload_byte_upper_bound:
        raise ProbeError("actual live-probe input tokens exceed the approved preflight bound")
    successful_response_cost = _estimated_cost(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        input_rate=input_rate,
        cached_input_rate=cached_input_rate,
        output_rate=output_rate,
    )
    prior_tool_attempts = max(0, attempts["tool_call"] - 1)
    retry_cost_upper_bound = _estimated_cost(
        input_tokens=tool_payload_byte_bound * prior_tool_attempts,
        cached_input_tokens=0,
        output_tokens=int(tool_payload["max_tokens"]) * prior_tool_attempts,
        input_rate=input_rate,
        cached_input_rate=cached_input_rate,
        output_rate=output_rate,
    )
    estimated_cost = successful_response_cost + retry_cost_upper_bound
    _enforce_cost_cap(estimated_cost, cost_cap, phase="aggregate actual")

    now = datetime.now(timezone.utc)
    discovery_hash = hashlib.sha256(discovery_content).hexdigest()
    required_checks = {
        "feature_flag": True,
        "model_discovery": True,
        "tool_call": True,
        "streaming": True,
        "invalid_tool_rejection": True,
        "timeout_retry": True,
        "redaction_capture": True,
        "usage_cost": True,
        "quota_pricing_snapshot": True,
        "policy_snapshot": True,
        "owner_approvals": True,
    }
    manifest = {
        "schema_version": 2,
        "test_id": f"GA-R0-001-LLM-{provider_id}",
        "result": "pass",
        "executed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=validity_hours)).isoformat(),
        "runner_identity": runner_identity,
        "release_digest": release_digest,
        "commit_sha": commit_sha,
        "evidence_path": str(output_path),
        "provider_id": provider_id,
        "feature_flag": {
            "enabled_provider_id": provider_id,
            "exclusive": True,
        },
        "provider_origin": base_url,
        "model": model,
        "provider_capabilities": required_capabilities,
        "account_identifier": policy["account_identifier"],
        "region": policy["region"],
        "model_discovery": {
            "method": profile.discovery_method,
            "path": profile.models_path or profile.chat_path,
            "model_confirmed": True,
            "response_sha256": f"sha256:{discovery_hash}",
        },
        "tool_call": {"name": name, "arguments": arguments, "schema_match": True},
        "invalid_tool_rejection": {
            "unknown_tool_rejected": True,
            "extra_field_rejected": True,
        },
        "stream": {
            "json_chunks": stream_chunks,
            "done_received": stream_done,
            "contract_marker_received": True,
            "usage_reported": True,
            "content_type": stream_content_type,
            "normalized_events_sha256": stream_capture_sha256,
        },
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
            "cached_input_tokens": cached_input_tokens,
            "cache_miss_input_tokens": input_tokens - cached_input_tokens,
            "output_tokens": output_tokens,
            "estimated_cost": str(estimated_cost),
            "successful_responses_estimated_cost": str(successful_response_cost),
            "retry_cost_upper_bound": str(retry_cost_upper_bound),
            "preflight_worst_case_cost": str(preflight_cost),
            "max_input_tokens_per_probe": max_input_tokens_per_probe,
            "input_payload_byte_upper_bound": input_payload_byte_upper_bound,
            "tool_attempt_budget": tool_attempt_budget,
            "currency": policy["currency"],
            "approved_cap": str(cost_cap),
            "rates_per_million_tokens": {
                "cache_hit_input": str(policy["cache_hit_input_cost_per_million_tokens"]),
                "cache_miss_input": str(policy["input_cost_per_million_tokens"]),
                "output": str(policy["output_cost_per_million_tokens"]),
            },
        },
        "quota": policy["quota"],
        "quota_pricing_evidence": {
            "account_tier": policy["account_tier"],
            "source_url": policy["quota_pricing_source_url"],
            "source_content_sha256": quota_pricing_snapshot[
                "source_content_sha256"
            ],
            "snapshot_sha256": policy["quota_pricing_snapshot_sha256"],
            "snapshot_schema_version": quota_pricing_snapshot["schema_version"],
            "reviewed_at": policy["quota_pricing_reviewed_at"],
            "currency": quota_pricing_snapshot["currency"],
            "prices_per_million_tokens": quota_pricing_snapshot[
                "prices_per_million_tokens"
            ],
            "quota": quota_pricing_snapshot["quota"],
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
    parser.add_argument("--quota-pricing-snapshot-file", type=Path)
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
    if (
        args.policy_file is None
        or args.policy_snapshot_file is None
        or args.quota_pricing_snapshot_file is None
        or args.output is None
    ):
        parser.error(
            "--policy-file, --policy-snapshot-file, "
            "--quota-pricing-snapshot-file and --output are required for a live probe"
        )
    if not 1 <= args.validity_hours <= 72:
        print("LLM contract probe failed: --validity-hours must be from 1 through 72", file=sys.stderr)
        return 1
    try:
        manifest = run_probe(
            args.policy_file,
            args.policy_snapshot_file,
            args.quota_pricing_snapshot_file,
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
