from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from app.config import Settings


REQUIRED_CHECKS = (
    "feature_flag",
    "model_discovery",
    "tool_call",
    "streaming",
    "invalid_tool_rejection",
    "timeout_retry",
    "redaction_capture",
    "usage_cost",
    "quota_pricing_snapshot",
    "policy_snapshot",
    "owner_approvals",
)
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_EVIDENCE_VALIDITY = timedelta(hours=72)
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ProviderVerification:
    verified: bool
    reason_code: str | None


def _unverified(reason_code: str) -> ProviderVerification:
    return ProviderVerification(verified=False, reason_code=reason_code)


def _parse_expiry(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        return None
    return value.astimezone(UTC)


def _finite_decimal(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def provider_manifest_contract_complete(evidence: dict[str, Any]) -> bool:
    required_strings = (
        "provider_id",
        "runner_identity",
        "evidence_path",
        "account_identifier",
        "region",
        "commit_sha",
    )
    if any(not isinstance(evidence.get(name), str) or not evidence[name].strip() for name in required_strings):
        return False
    if not COMMIT_PATTERN.fullmatch(evidence["commit_sha"]):
        return False
    feature_flag = evidence.get("feature_flag")
    if not isinstance(feature_flag, dict):
        return False
    if feature_flag.get("enabled_provider_id") != evidence["provider_id"]:
        return False
    if feature_flag.get("exclusive") is not True:
        return False
    discovery = evidence.get("model_discovery")
    if not isinstance(discovery, dict):
        return False
    if discovery.get("method") not in {
        "authenticated_models_endpoint",
        "authenticated_chat_completion",
    }:
        return False
    if discovery.get("model_confirmed") is not True:
        return False
    if not isinstance(discovery.get("path"), str) or not discovery["path"].startswith("/"):
        return False
    if not DIGEST_PATTERN.fullmatch(str(discovery.get("response_sha256") or "")):
        return False
    capabilities = evidence.get("provider_capabilities")
    if not isinstance(capabilities, dict) or any(
        capabilities.get(name) is not True
        for name in ("streaming", "tool_calls", "usage_reporting")
    ):
        return False

    tool_call = evidence.get("tool_call")
    stream = evidence.get("stream")
    retry = evidence.get("timeout_retry")
    capture = evidence.get("redaction_capture")
    usage = evidence.get("usage_cost")
    quota = evidence.get("quota")
    quota_pricing = evidence.get("quota_pricing_evidence")
    policy = evidence.get("policy")
    if not all(
        isinstance(item, dict)
        for item in (
            tool_call,
            stream,
            retry,
            capture,
            usage,
            quota,
            quota_pricing,
            policy,
        )
    ):
        return False
    if (
        tool_call.get("name") != "probe_echo"
        or tool_call.get("arguments") != {"value": "r0-live-contract-probe"}
        or tool_call.get("schema_match") is not True
    ):
        return False
    if (
        stream.get("done_received") is not True
        or stream.get("contract_marker_received") is not True
        or stream.get("usage_reported") is not True
        or stream.get("content_type") != "text/event-stream"
        or not DIGEST_PATTERN.fullmatch(
            str(stream.get("normalized_events_sha256") or "")
        )
        or not isinstance(stream.get("json_chunks"), int)
        or isinstance(stream["json_chunks"], bool)
        or stream["json_chunks"] <= 0
    ):
        return False
    if retry.get("connect_timeout_seconds", 0) <= 0 or retry.get("read_timeout_seconds", 0) <= 0:
        return False
    if not isinstance(retry.get("max_retries"), int) or not 0 <= retry["max_retries"] <= 3:
        return False
    attempts = retry.get("attempts")
    if not isinstance(attempts, dict) or not attempts or any(
        not isinstance(value, int) or value <= 0 for value in attempts.values()
    ):
        return False
    invalid_tool = evidence.get("invalid_tool_rejection")
    if not isinstance(invalid_tool, dict):
        return False
    if invalid_tool.get("unknown_tool_rejected") is not True:
        return False
    if invalid_tool.get("extra_field_rejected") is not True:
        return False
    if capture.get("seeded_secret_absent") is not True or capture.get("replacement_marker_present") is not True:
        return False
    if not DIGEST_PATTERN.fullmatch(str(capture.get("capture_sha256") or "")):
        return False
    if (
        not isinstance(usage.get("input_tokens"), int)
        or isinstance(usage["input_tokens"], bool)
        or usage["input_tokens"] < 0
    ):
        return False
    if (
        not isinstance(usage.get("output_tokens"), int)
        or isinstance(usage["output_tokens"], bool)
        or usage["output_tokens"] < 0
    ):
        return False
    for token_field in (
        "cached_input_tokens",
        "cache_miss_input_tokens",
        "max_input_tokens_per_probe",
        "input_payload_byte_upper_bound",
        "tool_attempt_budget",
    ):
        value = usage.get(token_field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return False
    if usage["max_input_tokens_per_probe"] <= 0:
        return False
    if (
        usage["input_payload_byte_upper_bound"] <= 0
        or usage["input_payload_byte_upper_bound"] > usage["max_input_tokens_per_probe"]
        or usage["tool_attempt_budget"] != retry["max_retries"] + 1
    ):
        return False
    if (
        usage["cached_input_tokens"] + usage["cache_miss_input_tokens"]
        != usage["input_tokens"]
    ):
        return False
    if usage["input_tokens"] > usage["input_payload_byte_upper_bound"]:
        return False
    if not isinstance(usage.get("currency"), str) or not usage["currency"].strip():
        return False
    estimated_cost = _finite_decimal(usage.get("estimated_cost"))
    successful_response_cost = _finite_decimal(
        usage.get("successful_responses_estimated_cost")
    )
    retry_cost_upper_bound = _finite_decimal(usage.get("retry_cost_upper_bound"))
    approved_cap = _finite_decimal(usage.get("approved_cap"))
    preflight_cost = _finite_decimal(usage.get("preflight_worst_case_cost"))
    if any(
        value is None
        for value in (
            estimated_cost,
            successful_response_cost,
            retry_cost_upper_bound,
            approved_cap,
            preflight_cost,
        )
    ):
        return False
    if (
        estimated_cost < 0
        or successful_response_cost < 0
        or retry_cost_upper_bound < 0
        or approved_cap < 0
        or preflight_cost < 0
        or estimated_cost != successful_response_cost + retry_cost_upper_bound
        or estimated_cost > approved_cap
        or preflight_cost > approved_cap
    ):
        return False
    rates = usage.get("rates_per_million_tokens")
    if not isinstance(rates, dict):
        return False
    rate_values = [
        _finite_decimal(rates.get(name))
        for name in ("cache_hit_input", "cache_miss_input", "output")
    ]
    if any(value is None or value < 0 for value in rate_values):
        return False
    for name in (
        "concurrency_limit",
        "requests_per_minute",
        "tokens_per_minute",
        "tokens_per_day",
    ):
        value = quota.get(name)
        if not (
            (isinstance(value, int) and not isinstance(value, bool) and value > 0)
            or (
                isinstance(value, str)
                and value in {"unlimited", "provider-managed", "account-tier"}
            )
        ):
            return False
    balance_alert_threshold = _finite_decimal(quota.get("balance_alert_threshold"))
    if balance_alert_threshold is None or balance_alert_threshold < 0:
        return False

    for name in ("account_tier", "source_url", "currency"):
        if not isinstance(quota_pricing.get(name), str) or not quota_pricing[name].strip():
            return False
    source_url = urlparse(str(quota_pricing["source_url"]))
    if (
        source_url.scheme != "https"
        or not source_url.netloc
        or source_url.username
        or source_url.password
        or source_url.query
        or source_url.fragment
    ):
        return False
    if not DIGEST_PATTERN.fullmatch(str(quota_pricing.get("snapshot_sha256") or "")):
        return False
    if not DIGEST_PATTERN.fullmatch(str(quota_pricing.get("source_content_sha256") or "")):
        return False
    if quota_pricing.get("snapshot_schema_version") != 1:
        return False
    if quota_pricing.get("currency") != usage.get("currency"):
        return False
    snapshot_prices = quota_pricing.get("prices_per_million_tokens")
    if not isinstance(snapshot_prices, dict):
        return False
    price_bindings = {
        "cache_hit_input": "cache_hit_input",
        "cache_miss_input": "cache_miss_input",
        "output": "output",
    }
    for snapshot_name, usage_name in price_bindings.items():
        snapshot_value = _finite_decimal(snapshot_prices.get(snapshot_name))
        usage_value = _finite_decimal(rates.get(usage_name))
        if snapshot_value is None or snapshot_value < 0 or snapshot_value != usage_value:
            return False
    snapshot_quota = quota_pricing.get("quota")
    if not isinstance(snapshot_quota, dict) or set(snapshot_quota) != set(quota):
        return False
    for name in quota:
        if name == "balance_alert_threshold":
            snapshot_threshold = _finite_decimal(snapshot_quota.get(name))
            if snapshot_threshold is None or snapshot_threshold != balance_alert_threshold:
                return False
        elif snapshot_quota.get(name) != quota.get(name):
            return False
    quota_pricing_reviewed_at = _parse_expiry(quota_pricing.get("reviewed_at"))
    if quota_pricing_reviewed_at is None:
        return False

    if policy.get("training_opt_out_confirmed") is not True:
        return False
    if policy.get("redacted_operational_data_only") is not True:
        return False
    if not DIGEST_PATTERN.fullmatch(str(policy.get("provider_policy_sha256") or "")):
        return False
    provider_policy_url = urlparse(str(policy.get("provider_policy_url") or ""))
    if (
        provider_policy_url.scheme != "https"
        or not provider_policy_url.netloc
        or provider_policy_url.username
        or provider_policy_url.password
        or provider_policy_url.query
        or provider_policy_url.fragment
    ):
        return False
    provider_reviewed_at = _parse_expiry(policy.get("reviewed_at"))
    if provider_reviewed_at is None:
        return False
    for owner in ("ai_owner", "security_owner", "privacy_owner"):
        if not isinstance(policy.get(owner), str) or not policy[owner].strip():
            return False
    approvals = policy.get("owner_approvals")
    if not isinstance(approvals, dict):
        return False
    approver_identities: set[str] = set()
    for role in ("ai", "security", "privacy"):
        approval = approvals.get(role)
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            return False
        if approval.get("role") != role:
            return False
        identity = str(approval.get("approver_identity") or "").strip()
        if not identity or identity != policy.get(f"{role}_owner"):
            return False
        normalized_identity = identity.casefold()
        if normalized_identity in approver_identities:
            return False
        approver_identities.add(normalized_identity)
        if approval.get("provider_policy_sha256") != policy.get("provider_policy_sha256"):
            return False
        if approval.get("quota_pricing_snapshot_sha256") != quota_pricing.get("snapshot_sha256"):
            return False
        approved_at = _parse_expiry(approval.get("approved_at"))
        if approved_at is None:
            return False
        if approved_at < max(provider_reviewed_at, quota_pricing_reviewed_at):
            return False
    return True


def provider_manifest_validation_error(
    evidence: Any,
    *,
    current_time: datetime | None = None,
    expected_provider_id: str | None = None,
    expected_provider_origin: str | None = None,
    expected_model: str | None = None,
    expected_release_digest: str | None = None,
    expected_commit_sha: str | None = None,
) -> str | None:
    """Return a stable reason code when provider evidence is not release-ready."""
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 2:
        return "llm_evidence_schema_invalid"
    if evidence.get("result") != "pass":
        return "llm_evidence_result_not_pass"
    if not provider_manifest_contract_complete(evidence):
        return "llm_evidence_contract_incomplete"
    if expected_provider_id is not None:
        if evidence.get("provider_id") != expected_provider_id:
            return "llm_evidence_provider_id_mismatch"
        feature_flag = evidence.get("feature_flag") or {}
        if feature_flag.get("enabled_provider_id") != expected_provider_id:
            return "llm_evidence_feature_flag_mismatch"
    if (
        expected_provider_origin is not None
        and evidence.get("provider_origin") != expected_provider_origin.rstrip("/")
    ):
        return "llm_evidence_provider_mismatch"
    if expected_model is not None and evidence.get("model") != expected_model:
        return "llm_evidence_model_mismatch"
    if (
        expected_release_digest is not None
        and evidence.get("release_digest") != expected_release_digest
    ):
        return "llm_evidence_release_mismatch"
    if expected_commit_sha is not None and evidence.get("commit_sha") != expected_commit_sha:
        return "llm_evidence_commit_mismatch"

    executed_at = _parse_expiry(evidence.get("executed_at"))
    expires_at = _parse_expiry(evidence.get("expires_at"))
    quota_pricing_reviewed_at = _parse_expiry(
        (evidence.get("quota_pricing_evidence") or {}).get("reviewed_at")
    )
    policy_reviewed_at = _parse_expiry((evidence.get("policy") or {}).get("reviewed_at"))
    approval_times = [
        _parse_expiry(approval.get("approved_at"))
        for approval in ((evidence.get("policy") or {}).get("owner_approvals") or {}).values()
        if isinstance(approval, dict)
    ]
    now = (current_time or datetime.now(UTC)).astimezone(UTC)
    if (
        executed_at is None
        or expires_at is None
        or executed_at > now + timedelta(minutes=5)
        or expires_at <= now
        or expires_at <= executed_at
        or expires_at - executed_at > MAX_EVIDENCE_VALIDITY
        or quota_pricing_reviewed_at is None
        or quota_pricing_reviewed_at > executed_at + timedelta(minutes=5)
        or executed_at - quota_pricing_reviewed_at > timedelta(days=30)
        or policy_reviewed_at is None
        or policy_reviewed_at > executed_at + timedelta(minutes=5)
        or executed_at - policy_reviewed_at > timedelta(days=90)
        or len(approval_times) != 3
        or any(approved_at is None for approved_at in approval_times)
        or any(
            approved_at > executed_at + timedelta(minutes=5)
            for approved_at in approval_times
            if approved_at
        )
    ):
        return "llm_evidence_expired"
    checks = evidence.get("required_checks")
    if not isinstance(checks, dict) or any(
        checks.get(name) is not True for name in REQUIRED_CHECKS
    ):
        return "llm_evidence_checks_incomplete"
    return None


def validate_provider_evidence(
    settings: Settings,
    *,
    current_time: datetime | None = None,
) -> ProviderVerification:
    """Validate the deployment-bound provider evidence without exposing its path or data."""
    if not settings.is_production:
        return ProviderVerification(verified=True, reason_code=None)
    if settings.release_digest is None:
        return _unverified("release_digest_unconfigured")
    if settings.commit_sha is None:
        return _unverified("release_commit_unconfigured")
    if settings.llm_evidence_file is None or settings.llm_evidence_sha256 is None:
        return _unverified("llm_evidence_unconfigured")

    path = settings.llm_evidence_file
    try:
        if path.is_symlink():
            return _unverified("llm_evidence_unprotected")
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            return _unverified("llm_evidence_unprotected")
        if stat.S_IMODE(metadata.st_mode) & 0o222:
            return _unverified("llm_evidence_unprotected")
        if metadata.st_uid not in {0, os.geteuid()}:
            return _unverified("llm_evidence_unprotected")
        if metadata.st_size <= 0 or metadata.st_size > MAX_EVIDENCE_BYTES:
            return _unverified("llm_evidence_invalid")
        raw = path.read_bytes()
    except OSError:
        return _unverified("llm_evidence_unreadable")

    actual_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual_digest, settings.llm_evidence_sha256):
        return _unverified("llm_evidence_digest_mismatch")
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _unverified("llm_evidence_invalid")
    validation_error = provider_manifest_validation_error(
        evidence,
        current_time=current_time,
        expected_provider_id=settings.llm_mode,
        expected_provider_origin=settings.llm_base_url or "",
        expected_model=settings.llm_model,
        expected_release_digest=settings.release_digest,
        expected_commit_sha=settings.commit_sha,
    )
    if validation_error is not None:
        return _unverified(validation_error)
    feature_flag = evidence.get("feature_flag")
    if feature_flag.get("enabled_provider_id") not in settings.llm_enabled_providers:
        return _unverified("llm_evidence_feature_flag_mismatch")
    return ProviderVerification(verified=True, reason_code=None)
