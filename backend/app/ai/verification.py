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

from app.config import Settings


REQUIRED_CHECKS = (
    "model_discovery",
    "tool_call",
    "streaming",
    "invalid_tool_rejection",
    "timeout_retry",
    "redaction_capture",
    "usage_cost",
    "policy_snapshot",
    "owner_approvals",
)
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_EVIDENCE_VALIDITY = timedelta(hours=72)
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def _manifest_contract_complete(evidence: dict[str, Any]) -> bool:
    required_strings = (
        "provider_id",
        "runner_identity",
        "evidence_path",
        "account_identifier",
        "region",
    )
    if any(not isinstance(evidence.get(name), str) or not evidence[name].strip() for name in required_strings):
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

    tool_call = evidence.get("tool_call")
    stream = evidence.get("stream")
    retry = evidence.get("timeout_retry")
    capture = evidence.get("redaction_capture")
    usage = evidence.get("usage_cost")
    policy = evidence.get("policy")
    if not all(isinstance(item, dict) for item in (tool_call, stream, retry, capture, usage, policy)):
        return False
    if (
        tool_call.get("name") != "probe_echo"
        or tool_call.get("arguments") != {"value": "r0-live-contract-probe"}
        or tool_call.get("schema_match") is not True
    ):
        return False
    if stream.get("done_received") is not True or not isinstance(stream.get("json_chunks"), int) or stream["json_chunks"] <= 0:
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
    if not isinstance(usage.get("input_tokens"), int) or usage["input_tokens"] < 0:
        return False
    if not isinstance(usage.get("output_tokens"), int) or usage["output_tokens"] < 0:
        return False
    if not isinstance(usage.get("currency"), str) or not usage["currency"].strip():
        return False
    try:
        estimated_cost = Decimal(str(usage["estimated_cost"]))
        approved_cap = Decimal(str(usage["approved_cap"]))
    except (KeyError, InvalidOperation, ValueError):
        return False
    if estimated_cost < 0 or approved_cap < 0 or estimated_cost > approved_cap:
        return False

    if policy.get("training_opt_out_confirmed") is not True:
        return False
    if policy.get("redacted_operational_data_only") is not True:
        return False
    if not DIGEST_PATTERN.fullmatch(str(policy.get("provider_policy_sha256") or "")):
        return False
    for owner in ("ai_owner", "security_owner", "privacy_owner"):
        if not isinstance(policy.get(owner), str) or not policy[owner].strip():
            return False
    approvals = policy.get("owner_approvals")
    if not isinstance(approvals, dict):
        return False
    for role in ("ai", "security", "privacy"):
        approval = approvals.get(role)
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            return False
        if _parse_expiry(approval.get("approved_at")) is None:
            return False
    return True


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
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 2:
        return _unverified("llm_evidence_schema_invalid")
    if evidence.get("result") != "pass":
        return _unverified("llm_evidence_result_not_pass")
    if not _manifest_contract_complete(evidence):
        return _unverified("llm_evidence_contract_incomplete")

    expected_provider = (settings.llm_base_url or "").rstrip("/")
    if evidence.get("provider_id") != settings.llm_mode:
        return _unverified("llm_evidence_provider_id_mismatch")
    if evidence.get("provider_origin") != expected_provider:
        return _unverified("llm_evidence_provider_mismatch")
    if evidence.get("model") != settings.llm_model:
        return _unverified("llm_evidence_model_mismatch")
    if evidence.get("release_digest") != settings.release_digest:
        return _unverified("llm_evidence_release_mismatch")

    executed_at = _parse_expiry(evidence.get("executed_at"))
    expires_at = _parse_expiry(evidence.get("expires_at"))
    now = (current_time or datetime.now(UTC)).astimezone(UTC)
    if (
        executed_at is None
        or expires_at is None
        or executed_at > now + timedelta(minutes=5)
        or expires_at <= now
        or expires_at <= executed_at
        or expires_at - executed_at > MAX_EVIDENCE_VALIDITY
    ):
        return _unverified("llm_evidence_expired")
    checks = evidence.get("required_checks")
    if not isinstance(checks, dict) or any(checks.get(name) is not True for name in REQUIRED_CHECKS):
        return _unverified("llm_evidence_checks_incomplete")
    return ProviderVerification(verified=True, reason_code=None)
