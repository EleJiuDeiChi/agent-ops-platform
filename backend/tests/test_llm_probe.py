from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "probe-llm-contract.py"
SPEC = importlib.util.spec_from_file_location("aiops_llm_contract_probe", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def structured_quota_snapshot(policy: dict, source_content: bytes) -> dict:
    return {
        "schema_version": 1,
        "provider_id": policy["provider_id"],
        "account_identifier": policy["account_identifier"],
        "account_tier": policy["account_tier"],
        "source_url": policy["quota_pricing_source_url"],
        "source_content_sha256": (
            "sha256:" + hashlib.sha256(source_content).hexdigest()
        ),
        "reviewed_at": policy["quota_pricing_reviewed_at"],
        "currency": policy["currency"],
        "prices_per_million_tokens": {
            "cache_hit_input": policy[
                "cache_hit_input_cost_per_million_tokens"
            ],
            "cache_miss_input": policy["input_cost_per_million_tokens"],
            "output": policy["output_cost_per_million_tokens"],
        },
        "quota": dict(policy["quota"]),
    }


def bind_policy_evidence(
    policy: dict,
    provider_policy_snapshot: bytes,
    quota_pricing_snapshot: dict,
) -> bytes:
    quota_pricing_raw = json.dumps(
        quota_pricing_snapshot,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    provider_policy_digest = (
        "sha256:" + hashlib.sha256(provider_policy_snapshot).hexdigest()
    )
    quota_pricing_digest = "sha256:" + hashlib.sha256(quota_pricing_raw).hexdigest()
    policy["provider_policy_sha256"] = provider_policy_digest
    policy["quota_pricing_snapshot_sha256"] = quota_pricing_digest
    policy["owner_approvals"] = {
        role: {
            "role": role,
            "approver_identity": policy[f"{role}_owner"],
            "approved": True,
            "approved_at": policy["quota_pricing_reviewed_at"],
            "provider_policy_sha256": provider_policy_digest,
            "quota_pricing_snapshot_sha256": quota_pricing_digest,
        }
        for role in ("ai", "security", "privacy")
    }
    return quota_pricing_raw


def valid_policy(now: str) -> dict:
    return {
        "provider_id": "deepseek",
        "account_identifier": "test-deepseek-account",
        "region": "test-region",
        "retention": "reviewed-test-retention",
        "training_opt_out_confirmed": True,
        "redacted_operational_data_only": True,
        "ai_owner": "ai-owner",
        "security_owner": "security-owner",
        "privacy_owner": "privacy-owner",
        "provider_policy_url": "https://policy.example.test/provider",
        "reviewed_at": now,
        "quota_pricing_source_url": "https://policy.example.test/account-pricing",
        "quota_pricing_reviewed_at": now,
        "account_tier": "test-approved-tier",
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 2,
        "max_retries": 1,
        "max_output_tokens": 64,
        "max_input_tokens_per_probe": 4096,
        "cache_hit_input_cost_per_million_tokens": "0.1",
        "input_cost_per_million_tokens": "1",
        "output_cost_per_million_tokens": "2",
        "cost_cap_per_probe": "0.01",
        "currency": "CNY",
        "quota": {
            "concurrency_limit": 10,
            "requests_per_minute": "account-tier",
            "tokens_per_minute": "provider-managed",
            "tokens_per_day": "unlimited",
            "balance_alert_threshold": "1",
        },
    }


def write_policy_evidence(
    tmp_path: Path,
    policy: dict,
    quota_pricing_snapshot: dict,
) -> tuple[Path, Path, Path]:
    provider_policy_snapshot = b"reviewed-provider-policy-snapshot"
    quota_pricing_raw = bind_policy_evidence(
        policy,
        provider_policy_snapshot,
        quota_pricing_snapshot,
    )
    policy_path = tmp_path / "policy.json"
    provider_policy_path = tmp_path / "provider-policy.snapshot"
    quota_pricing_path = tmp_path / "quota-pricing.snapshot.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    provider_policy_path.write_bytes(provider_policy_snapshot)
    quota_pricing_path.write_bytes(quota_pricing_raw)
    return policy_path, provider_policy_path, quota_pricing_path


class FakeStreamResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream; charset=utf-8"}

    def __init__(self, model: str, provider_id: str) -> None:
        self.model = model
        self.provider_id = provider_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def iter_lines(self):
        yield "data: " + json.dumps(
            {
                "model": self.model,
                "choices": [{"delta": {"content": "R0_STREAM_OK"}}],
            }
        )
        yield "data: " + json.dumps(
            {
                "model": self.model,
                "choices": [],
                "usage": FakeProviderClient.usage(
                    self.provider_id,
                    prompt_tokens=6,
                    cached_tokens=1,
                    completion_tokens=2,
                ),
            }
        )
        yield "data: [DONE]"


class FakeProviderClient:
    def __init__(self, *, model: str, provider_id: str) -> None:
        self.model = model
        self.provider_id = provider_id
        self.requests: list[tuple[str, str]] = []
        self.payloads: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    @staticmethod
    def usage(
        provider_id: str,
        *,
        prompt_tokens: int,
        cached_tokens: int,
        completion_tokens: int,
    ) -> dict:
        payload = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if provider_id == "deepseek":
            payload["prompt_cache_hit_tokens"] = cached_tokens
            payload["prompt_cache_miss_tokens"] = prompt_tokens - cached_tokens
        elif provider_id == "moonshot":
            payload["cached_tokens"] = cached_tokens
        else:
            payload["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
        return payload

    def request(self, method: str, url: str, **kwargs):
        self.requests.append((method, url))
        if isinstance(kwargs.get("json"), dict):
            self.payloads.append(kwargs["json"])
        if method == "GET":
            return probe.httpx.Response(
                200,
                json={"object": "list", "data": [{"id": self.model}]},
            )
        return probe.httpx.Response(
            200,
            json={
                "model": self.model,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_probe",
                                    "type": "function",
                                    "function": {
                                        "name": "probe_echo",
                                        "arguments": json.dumps(
                                            {"value": "r0-live-contract-probe"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": self.usage(
                    self.provider_id,
                    prompt_tokens=12,
                    cached_tokens=2,
                    completion_tokens=4,
                ),
            },
        )

    def stream(self, method: str, url: str, **kwargs):
        self.requests.append((method, url))
        if isinstance(kwargs.get("json"), dict):
            self.payloads.append(kwargs["json"])
        return FakeStreamResponse(self.model, self.provider_id)


@pytest.mark.parametrize(
    ("provider_id", "base_url", "model", "expected_discovery"),
    [
        (
            "deepseek",
            "https://api.deepseek.com",
            "deepseek-v4-flash",
            "authenticated_models_endpoint",
        ),
        (
            "moonshot",
            "https://api.moonshot.ai/v1",
            "kimi-k2.6",
            "authenticated_models_endpoint",
        ),
        (
            "zhipu",
            "https://open.bigmodel.cn/api/paas/v4",
            "glm-5.2",
            "authenticated_chat_completion",
        ),
    ],
)
def test_live_probe_contract_covers_each_built_in_provider_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_id: str,
    base_url: str,
    model: str,
    expected_discovery: str,
) -> None:
    snapshot = b"reviewed-provider-policy-snapshot"
    quota_pricing_source = b"reviewed-account-quota-and-pricing-source"
    now = datetime.now(UTC).isoformat()
    policy = {
        "provider_id": provider_id,
        "account_identifier": f"test-{provider_id}-account",
        "region": "test-region",
        "retention": "reviewed-test-retention",
        "training_opt_out_confirmed": True,
        "redacted_operational_data_only": True,
        "ai_owner": "ai-owner",
        "security_owner": "security-owner",
        "privacy_owner": "privacy-owner",
        "provider_policy_url": "https://policy.example.test/provider",
        "reviewed_at": now,
        "quota_pricing_source_url": "https://policy.example.test/account-pricing",
        "quota_pricing_reviewed_at": now,
        "account_tier": "test-approved-tier",
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 2,
        "max_retries": 1,
        "max_output_tokens": 64,
        "max_input_tokens_per_probe": 4096,
        "cache_hit_input_cost_per_million_tokens": "0.1",
        "input_cost_per_million_tokens": "1",
        "output_cost_per_million_tokens": "2",
        "cost_cap_per_probe": "0.01",
        "currency": "CNY",
        "quota": {
            "concurrency_limit": 10,
            "requests_per_minute": "account-tier",
            "tokens_per_minute": "provider-managed",
            "tokens_per_day": "unlimited",
            "balance_alert_threshold": "1",
        },
    }
    quota_pricing_snapshot = structured_quota_snapshot(
        policy,
        quota_pricing_source,
    )
    quota_pricing_raw = bind_policy_evidence(
        policy,
        snapshot,
        quota_pricing_snapshot,
    )
    policy_path = tmp_path / f"{provider_id}-policy.json"
    snapshot_path = tmp_path / f"{provider_id}-policy.snapshot"
    quota_pricing_snapshot_path = tmp_path / f"{provider_id}-quota-pricing.snapshot"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    snapshot_path.write_bytes(snapshot)
    quota_pricing_snapshot_path.write_bytes(quota_pricing_raw)

    for name in (
        "AIOPS_LLM_API_KEY",
        "AIOPS_LLM_API_KEY_FILE",
        "AIOPS_DEEPSEEK_API_KEY",
        "AIOPS_DEEPSEEK_API_KEY_FILE",
        "AIOPS_MOONSHOT_API_KEY",
        "AIOPS_MOONSHOT_API_KEY_FILE",
        "AIOPS_ZHIPU_API_KEY",
        "AIOPS_ZHIPU_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AIOPS_LLM_MODE", provider_id)
    monkeypatch.setenv("AIOPS_LLM_ENABLED_PROVIDERS", provider_id)
    monkeypatch.setenv("AIOPS_LLM_BASE_URL", base_url)
    monkeypatch.setenv("AIOPS_LLM_MODEL", model)
    key_path = tmp_path / f"{provider_id}-api-key"
    key_path.write_text("test-only-provider-key\n", encoding="utf-8")
    key_path.chmod(0o600)
    monkeypatch.setenv("AIOPS_LLM_API_KEY_FILE", str(key_path))
    monkeypatch.setenv("AIOPS_RELEASE_DIGEST", "sha256:" + "a" * 64)
    monkeypatch.setenv("AIOPS_COMMIT_SHA", "b" * 40)
    monkeypatch.setenv("AIOPS_EVIDENCE_RUNNER_IDENTITY", "pytest-runner")

    client = FakeProviderClient(model=model, provider_id=provider_id)
    monkeypatch.setattr(probe.httpx, "Client", lambda **kwargs: client)
    def fake_snapshot_get(url: str, **kwargs):
        content = (
            quota_pricing_source
            if url == policy["quota_pricing_source_url"]
            else snapshot
        )
        return probe.httpx.Response(200, content=content)

    monkeypatch.setattr(probe.httpx, "get", fake_snapshot_get)

    manifest = probe.run_probe(
        policy_path,
        snapshot_path,
        quota_pricing_snapshot_path,
        output_path=tmp_path / f"{provider_id}-evidence.json",
        validity_hours=1,
    )

    assert manifest["provider_id"] == provider_id
    assert manifest["commit_sha"] == "b" * 40
    assert manifest["provider_capabilities"] == {
        "streaming": True,
        "tool_calls": True,
        "usage_reporting": True,
    }
    assert manifest["feature_flag"] == {
        "enabled_provider_id": provider_id,
        "exclusive": True,
    }
    assert manifest["model_discovery"]["method"] == expected_discovery
    assert manifest["required_checks"]["feature_flag"] is True
    assert manifest["required_checks"]["streaming"] is True
    assert manifest["required_checks"]["usage_cost"] is True
    assert manifest["required_checks"]["quota_pricing_snapshot"] is True
    assert manifest["stream"]["contract_marker_received"] is True
    assert manifest["stream"]["usage_reported"] is True
    assert manifest["stream"]["content_type"] == "text/event-stream"
    assert manifest["stream"]["normalized_events_sha256"].startswith("sha256:")
    assert manifest["usage_cost"]["input_tokens"] == 18
    assert manifest["usage_cost"]["cached_input_tokens"] == 3
    assert manifest["usage_cost"]["output_tokens"] == 6
    assert manifest["usage_cost"]["tool_attempt_budget"] == 2
    assert (
        manifest["usage_cost"]["input_payload_byte_upper_bound"]
        <= policy["max_input_tokens_per_probe"]
    )
    assert manifest["quota"] == policy["quota"]
    assert manifest["quota_pricing_evidence"] == {
        "account_tier": "test-approved-tier",
        "source_url": policy["quota_pricing_source_url"],
        "source_content_sha256": quota_pricing_snapshot[
            "source_content_sha256"
        ],
        "snapshot_sha256": policy["quota_pricing_snapshot_sha256"],
        "snapshot_schema_version": 1,
        "reviewed_at": now,
        "currency": "CNY",
        "prices_per_million_tokens": quota_pricing_snapshot[
            "prices_per_million_tokens"
        ],
        "quota": quota_pricing_snapshot["quota"],
    }
    get_requests = [item for item in client.requests if item[0] == "GET"]
    assert bool(get_requests) is (provider_id != "zhipu")
    post_payloads = [item for item in client.payloads if item.get("stream") is False]
    assert post_payloads[0]["tool_choice"] == (
        "auto"
        if provider_id in {"moonshot", "zhipu"}
        else {"type": "function", "function": {"name": "probe_echo"}}
    )


@pytest.mark.parametrize("payload", [{}, {"model": "different-model"}, []])
def test_probe_rejects_missing_or_mismatched_response_model(payload) -> None:
    with pytest.raises(probe.ProbeError, match="response model"):
        probe._validate_response_model(payload, "configured-model")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_probe_rejects_non_finite_pricing_values(value: str) -> None:
    with pytest.raises(probe.ProbeError, match="must be finite"):
        probe._positive_decimal(value, field="price", allow_zero=True)


def test_live_probe_rejects_direct_api_key_environment_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AIOPS_LLM_API_KEY",
        "AIOPS_LLM_API_KEY_FILE",
        "AIOPS_DEEPSEEK_API_KEY",
        "AIOPS_DEEPSEEK_API_KEY_FILE",
        "AIOPS_MOONSHOT_API_KEY",
        "AIOPS_MOONSHOT_API_KEY_FILE",
        "AIOPS_ZHIPU_API_KEY",
        "AIOPS_ZHIPU_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    key_path = tmp_path / "api-key"
    key_path.write_text("file-secret\n", encoding="utf-8")
    key_path.chmod(0o600)
    monkeypatch.setenv("AIOPS_LLM_API_KEY", "direct-secret")
    monkeypatch.setenv("AIOPS_LLM_API_KEY_FILE", str(key_path))

    with pytest.raises(probe.ProbeError, match="direct API key environment"):
        probe._secret_from_environment("deepseek")


def test_live_probe_rejects_group_readable_key_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "AIOPS_LLM_API_KEY",
        "AIOPS_LLM_API_KEY_FILE",
        "AIOPS_DEEPSEEK_API_KEY",
        "AIOPS_DEEPSEEK_API_KEY_FILE",
        "AIOPS_MOONSHOT_API_KEY",
        "AIOPS_MOONSHOT_API_KEY_FILE",
        "AIOPS_ZHIPU_API_KEY",
        "AIOPS_ZHIPU_API_KEY_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    key_path = tmp_path / "insecure-api-key"
    key_path.write_text("file-secret\n", encoding="utf-8")
    key_path.chmod(0o640)
    monkeypatch.setenv("AIOPS_LLM_API_KEY_FILE", str(key_path))

    with pytest.raises(probe.ProbeError, match="group or other"):
        probe._secret_from_environment("deepseek")


@pytest.mark.parametrize(
    ("field_path", "replacement"),
    [
        (("provider_id",), "moonshot"),
        (("account_identifier",), "different-account"),
        (("account_tier",), "different-tier"),
        (("source_url",), "https://policy.example.test/different-source"),
        (("reviewed_at",), "future-review"),
        (("currency",), "USD"),
        (("prices_per_million_tokens", "cache_miss_input"), "1.1"),
        (("quota", "concurrency_limit"), 11),
    ],
)
def test_probe_rejects_structured_quota_snapshot_semantic_mismatch(
    tmp_path: Path,
    field_path: tuple[str, ...],
    replacement: object,
) -> None:
    reviewed_at = datetime.now(UTC)
    policy = valid_policy(reviewed_at.isoformat())
    snapshot = structured_quota_snapshot(policy, b"quota-pricing-source")
    if replacement == "future-review":
        replacement = (reviewed_at + timedelta(seconds=1)).isoformat()
    target = snapshot
    for part in field_path[:-1]:
        target = target[part]
    target[field_path[-1]] = replacement
    paths = write_policy_evidence(tmp_path, policy, snapshot)

    with pytest.raises(probe.ProbeError, match="does not match policy"):
        probe._load_policy(*paths, provider_id="deepseek")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("duplicate_identity", "unique identities"),
        ("policy_digest", "not bound to provider policy snapshot"),
        ("quota_digest", "not bound to quota and pricing snapshot"),
        ("role", "approval role does not match"),
    ],
)
def test_probe_requires_unique_role_bound_owner_approvals(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    policy = valid_policy(now)
    if mutation == "duplicate_identity":
        policy["privacy_owner"] = policy["security_owner"].upper()
    snapshot = structured_quota_snapshot(policy, b"quota-pricing-source")
    paths = write_policy_evidence(tmp_path, policy, snapshot)
    if mutation == "policy_digest":
        policy["owner_approvals"]["ai"]["provider_policy_sha256"] = (
            "sha256:" + "a" * 64
        )
    elif mutation == "quota_digest":
        policy["owner_approvals"]["security"][
            "quota_pricing_snapshot_sha256"
        ] = "sha256:" + "b" * 64
    elif mutation == "role":
        policy["owner_approvals"]["privacy"]["role"] = "security"
    paths[0].write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(probe.ProbeError, match=message):
        probe._load_policy(*paths, provider_id="deepseek")


def test_probe_rejects_boolean_quota_marker(tmp_path: Path) -> None:
    snapshot = b"provider-policy"
    quota_pricing_source = b"quota-pricing-source"
    now = datetime.now(UTC).isoformat()
    policy = {
        "provider_id": "deepseek",
        "account_identifier": "test-account",
        "region": "test-region",
        "retention": "reviewed",
        "training_opt_out_confirmed": True,
        "redacted_operational_data_only": True,
        "ai_owner": "ai-owner",
        "security_owner": "security-owner",
        "privacy_owner": "privacy-owner",
        "provider_policy_url": "https://policy.example.test/provider",
        "reviewed_at": now,
        "quota_pricing_source_url": "https://policy.example.test/pricing",
        "quota_pricing_reviewed_at": now,
        "account_tier": "test-tier",
        "connect_timeout_seconds": 1,
        "read_timeout_seconds": 2,
        "max_retries": 1,
        "max_output_tokens": 64,
        "max_input_tokens_per_probe": 4096,
        "cache_hit_input_cost_per_million_tokens": "0",
        "input_cost_per_million_tokens": "1",
        "output_cost_per_million_tokens": "2",
        "cost_cap_per_probe": "0.01",
        "currency": "CNY",
        "quota": {
            "concurrency_limit": True,
            "requests_per_minute": 1,
            "tokens_per_minute": 1,
            "tokens_per_day": 1,
            "balance_alert_threshold": "0",
        },
    }
    quota_pricing_snapshot = structured_quota_snapshot(
        policy,
        quota_pricing_source,
    )
    quota_pricing_raw = bind_policy_evidence(
        policy,
        snapshot,
        quota_pricing_snapshot,
    )
    policy_path = tmp_path / "policy.json"
    policy_snapshot_path = tmp_path / "policy.snapshot"
    quota_pricing_snapshot_path = tmp_path / "quota-pricing.snapshot"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    policy_snapshot_path.write_bytes(snapshot)
    quota_pricing_snapshot_path.write_bytes(quota_pricing_raw)

    with pytest.raises(probe.ProbeError, match="quota.concurrency_limit"):
        probe._load_policy(
            policy_path,
            policy_snapshot_path,
            quota_pricing_snapshot_path,
            provider_id="deepseek",
        )


def test_probe_preflight_cost_accounts_for_both_output_budgets() -> None:
    cost = probe._estimated_cost(
        input_tokens=100,
        cached_input_tokens=20,
        output_tokens=96,
        input_rate=probe.Decimal("1"),
        cached_input_rate=probe.Decimal("0.1"),
        output_rate=probe.Decimal("2"),
    )
    assert cost == probe.Decimal("0.000274")
    with pytest.raises(probe.ProbeError, match="aggregate actual"):
        probe._enforce_cost_cap(
            cost,
            probe.Decimal("0.000273"),
            phase="aggregate actual",
        )


@pytest.mark.parametrize(
    "event",
    [
        {"model": "configured-model", "error": {"message": "denied"}},
        {"model": "configured-model", "choices": []},
        {
            "model": "configured-model",
            "choices": [{"delta": {"content": {"not": "text"}}}],
        },
    ],
)
def test_probe_rejects_error_or_malformed_stream_events(event) -> None:
    with pytest.raises(probe.ProbeError, match="stream"):
        probe._validate_stream_event(event, "configured-model", "zhipu")


@pytest.mark.parametrize(
    ("content", "usage", "message"),
    [
        ("not-the-marker", (1, 0, 1), "contract marker"),
        ("R0_STREAM_OK", None, "token usage"),
    ],
)
def test_probe_rejects_incomplete_stream_contract(content, usage, message) -> None:
    with pytest.raises(probe.ProbeError, match=message):
        probe._validate_stream_contract(content, usage)


@pytest.mark.parametrize(
    ("provider_id", "usage"),
    [
        (
            "deepseek",
            {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 3,
                "prompt_cache_miss_tokens": 7,
            },
        ),
        (
            "moonshot",
            {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 3},
        ),
        (
            "zhipu",
            {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        ),
    ],
)
def test_probe_parses_provider_specific_cache_usage(provider_id, usage) -> None:
    assert probe._usage({"usage": usage}, provider_id=provider_id) == (10, 3, 2)


def test_probe_parses_kimi_choice_level_stream_usage() -> None:
    content, usage = probe._validate_stream_event(
        {
            "model": "kimi-k2.6",
            "choices": [
                {
                    "delta": {"content": "R0_STREAM_OK"},
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "cached_tokens": 3,
                    },
                }
            ],
        },
        "kimi-k2.6",
        "moonshot",
    )
    assert content == "R0_STREAM_OK"
    assert usage == (10, 3, 2)


@pytest.mark.parametrize("content_type", ["application/json", "", "text/plain"])
def test_probe_rejects_non_sse_content_type(content_type: str) -> None:
    with pytest.raises(probe.ProbeError, match="Content-Type"):
        probe._validate_stream_content_type(content_type)
