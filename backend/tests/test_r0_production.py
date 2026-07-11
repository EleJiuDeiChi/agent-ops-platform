from __future__ import annotations

import importlib
import copy
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigurationError, load_settings
from app.support import evaluate_support


def production_environment(tmp_path: Path, now: datetime) -> dict[str, str]:
    session_secret_file = tmp_path / "session_secret"
    session_secret_file.write_text("s" * 48 + "\n", encoding="utf-8")
    setup_token_file = tmp_path / "setup_token"
    setup_token_file.write_text("t" * 48 + "\n", encoding="utf-8")
    llm_key_file = tmp_path / "llm_api_key"
    llm_key_file.write_text("k" * 48 + "\n", encoding="utf-8")
    llm_key_file.chmod(0o600)
    return {
        "AIOPS_ENV": "prod",
        "AIOPS_DB_PATH": str(tmp_path / "production.sqlite"),
        "AIOPS_SESSION_SECRET_FILE": str(session_secret_file),
        "AIOPS_TRUSTED_ORIGINS": "https://ops.example.test",
        "AIOPS_COOKIE_SECURE": "1",
        "AIOPS_TLS_ENABLED": "1",
        "AIOPS_DEBUG_SKIP_PASSWORD_CHANGE": "0",
        "AIOPS_SETUP_TOKEN_FILE": str(setup_token_file),
        "AIOPS_SETUP_TOKEN_EXPIRES_AT": (now + timedelta(minutes=30)).isoformat(),
        "AIOPS_LLM_MODE": "openai_compatible",
        "AIOPS_LLM_ENABLED_PROVIDERS": "openai_compatible",
        "AIOPS_LLM_BASE_URL": "https://llm.example.test/v1",
        "AIOPS_LLM_MODEL": "frozen-production-model",
        "AIOPS_LLM_API_KEY_FILE": str(llm_key_file),
    }


def apply_environment(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    managed_names = {
        "AIOPS_ENV",
        "AIOPS_DB_PATH",
        "AIOPS_RELEASE_DIGEST",
        "AIOPS_COMMIT_SHA",
        "AIOPS_SESSION_SECRET",
        "AIOPS_SESSION_SECRET_FILE",
        "AIOPS_TRUSTED_ORIGINS",
        "AIOPS_COOKIE_SECURE",
        "AIOPS_TLS_ENABLED",
        "AIOPS_DEBUG_SKIP_PASSWORD_CHANGE",
        "AIOPS_BOOTSTRAP_ADMIN_PASSWORD",
        "AIOPS_BOOTSTRAP_ADMIN_PASSWORD_FILE",
        "AIOPS_SETUP_TOKEN",
        "AIOPS_SETUP_TOKEN_FILE",
        "AIOPS_SETUP_TOKEN_EXPIRES_AT",
        "AIOPS_LLM_MODE",
        "AIOPS_LLM_ENABLED_PROVIDERS",
        "AIOPS_LLM_BASE_URL",
        "AIOPS_LLM_MODEL",
        "AIOPS_LLM_API_KEY",
        "AIOPS_LLM_API_KEY_FILE",
        "AIOPS_LLM_EVIDENCE_FILE",
        "AIOPS_LLM_EVIDENCE_SHA256",
        "AIOPS_DEEPSEEK_API_KEY",
        "AIOPS_DEEPSEEK_API_KEY_FILE",
        "AIOPS_MOONSHOT_API_KEY",
        "AIOPS_MOONSHOT_API_KEY_FILE",
        "AIOPS_ZHIPU_API_KEY",
        "AIOPS_ZHIPU_API_KEY_FILE",
    }
    for name in managed_names:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize(
    ("remove", "override", "message"),
    [
        ("AIOPS_SESSION_SECRET_FILE", {}, "SESSION_SECRET"),
        ("AIOPS_TRUSTED_ORIGINS", {}, "TRUSTED_ORIGINS"),
        (None, {"AIOPS_TRUSTED_ORIGINS": "http://ops.example.test"}, "HTTPS origins"),
        ("AIOPS_COOKIE_SECURE", {}, "COOKIE_SECURE"),
        (None, {"AIOPS_COOKIE_SECURE": "0"}, "COOKIE_SECURE"),
        ("AIOPS_TLS_ENABLED", {}, "TLS_ENABLED"),
        (None, {"AIOPS_DEBUG_SKIP_PASSWORD_CHANGE": "1"}, "DEBUG_SKIP"),
        ("AIOPS_LLM_MODE", {}, "LLM_MODE"),
        ("AIOPS_LLM_ENABLED_PROVIDERS", {}, "LLM_ENABLED_PROVIDERS"),
        ("AIOPS_LLM_BASE_URL", {}, "LLM_BASE_URL"),
        (None, {"AIOPS_LLM_BASE_URL": "http://llm.example.test/v1"}, "credential-free HTTPS"),
        (
            None,
            {"AIOPS_LLM_BASE_URL": "https://user:password@llm.example.test/v1"},
            "credential-free HTTPS",
        ),
        (
            None,
            {"AIOPS_LLM_BASE_URL": "https://llm.example.test/v1?tenant=secret"},
            "credential-free HTTPS",
        ),
        (
            None,
            {"AIOPS_LLM_BASE_URL": "https://llm.example.test/v1#fragment"},
            "credential-free HTTPS",
        ),
        ("AIOPS_LLM_MODEL", {}, "LLM_MODEL"),
        ("AIOPS_LLM_API_KEY_FILE", {}, "LLM_API_KEY"),
    ],
)
def test_production_configuration_fails_closed(
    tmp_path: Path,
    remove: str | None,
    override: dict[str, str],
    message: str,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    if remove:
        env.pop(remove)
    env.update(override)
    with pytest.raises(ConfigurationError, match=message):
        load_settings(env, validate=True, current_time=now)


@pytest.mark.parametrize(
    ("provider_id", "base_url", "candidate_model"),
    [
        ("deepseek", "https://api.deepseek.com", "deepseek-v4-flash"),
        ("moonshot", "https://api.moonshot.ai/v1", "kimi-k2.6"),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4", "glm-5.2"),
    ],
)
def test_production_accepts_each_built_in_provider_contract(
    tmp_path: Path,
    provider_id: str,
    base_url: str,
    candidate_model: str,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env.update(
        {
            "AIOPS_LLM_MODE": provider_id,
            "AIOPS_LLM_ENABLED_PROVIDERS": provider_id,
            "AIOPS_LLM_BASE_URL": base_url,
            "AIOPS_LLM_MODEL": candidate_model,
        }
    )
    settings = load_settings(env, validate=True, current_time=now)
    assert settings.llm_mode == provider_id
    assert settings.llm_base_url == base_url
    assert settings.llm_model == candidate_model


def test_production_rejects_provider_identity_and_origin_mismatch(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env.update(
        {
            "AIOPS_LLM_MODE": "moonshot",
            "AIOPS_LLM_BASE_URL": "https://api.deepseek.com",
            "AIOPS_LLM_MODEL": "kimi-k2.6",
        }
    )
    with pytest.raises(ConfigurationError, match="Moonshot Kimi production base URL"):
        load_settings(env, validate=True, current_time=now)


@pytest.mark.parametrize(
    "enabled_providers",
    ["", "deepseek", "moonshot,zhipu", "unknown-provider"],
)
def test_production_requires_exclusive_selected_provider_feature_flag(
    tmp_path: Path,
    enabled_providers: str,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env["AIOPS_LLM_ENABLED_PROVIDERS"] = enabled_providers
    expected = (
        "unsupported providers"
        if enabled_providers == "unknown-provider"
        else "must contain exactly"
    )
    with pytest.raises(ConfigurationError, match=expected):
        load_settings(env, validate=True, current_time=now)


def test_provider_specific_key_alias_must_match_selected_provider(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env.pop("AIOPS_LLM_API_KEY_FILE")
    env.update(
        {
            "AIOPS_LLM_MODE": "zhipu",
            "AIOPS_LLM_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
            "AIOPS_LLM_MODEL": "glm-5.2",
            "AIOPS_MOONSHOT_API_KEY": "wrong-provider-key",
        }
    )
    with pytest.raises(ConfigurationError, match="only be used with AIOPS_LLM_MODE=moonshot"):
        load_settings(env, validate=True, current_time=now)


def test_production_rejects_bootstrap_password_and_ambiguous_secret_sources(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env["AIOPS_BOOTSTRAP_ADMIN_PASSWORD"] = "unsafe-bootstrap"
    with pytest.raises(ConfigurationError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        load_settings(env, validate=True, current_time=now)

    bootstrap_file = tmp_path / "bootstrap_password"
    bootstrap_file.write_text("unsafe-bootstrap\n", encoding="utf-8")
    env = production_environment(tmp_path, now)
    env["AIOPS_BOOTSTRAP_ADMIN_PASSWORD_FILE"] = str(bootstrap_file)
    with pytest.raises(ConfigurationError, match="BOOTSTRAP_ADMIN_PASSWORD"):
        load_settings(env, validate=True, current_time=now)

    env = production_environment(tmp_path, now)
    env["AIOPS_SESSION_SECRET"] = "x" * 48
    with pytest.raises(ConfigurationError, match="only one"):
        load_settings(env, validate=True, current_time=now)


def test_setup_enrollment_configuration_and_expiry_are_fail_closed(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env.pop("AIOPS_SETUP_TOKEN_FILE")
    env.pop("AIOPS_SETUP_TOKEN_EXPIRES_AT")
    settings = load_settings(env, validate=True, current_time=now)
    with pytest.raises(ConfigurationError, match="requires short-lived setup enrollment"):
        settings.validate_initialization(initialized=False)

    env = production_environment(tmp_path, now)
    settings = load_settings(env, validate=True, current_time=now)
    with pytest.raises(ConfigurationError, match="remove setup token"):
        settings.validate_initialization(initialized=True)

    env = production_environment(tmp_path, now)
    env["AIOPS_SETUP_TOKEN_EXPIRES_AT"] = (now - timedelta(seconds=1)).isoformat()
    with pytest.raises(ConfigurationError, match="expired"):
        load_settings(env, validate=True, current_time=now)


def test_production_lifespan_rejects_uninitialized_instance_without_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    env.pop("AIOPS_SETUP_TOKEN_FILE")
    env.pop("AIOPS_SETUP_TOKEN_EXPIRES_AT")
    apply_environment(monkeypatch, env)
    import app.main as main

    importlib.reload(main)
    with pytest.raises(ConfigurationError, match="requires short-lived setup enrollment"):
        with TestClient(main.app, base_url="https://ops.example.test"):
            pass


def test_setup_enrollment_secure_cookies_and_public_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    apply_environment(monkeypatch, env)
    import app.support as support

    monkeypatch.setattr(
        support,
        "read_mountinfo",
        lambda: "25 1 8:1 / / rw,relatime - xfs /dev/root rw",
    )
    import app.main as main

    importlib.reload(main)
    session_secret = (tmp_path / "session_secret").read_text(encoding="utf-8").strip()
    setup_token = (tmp_path / "setup_token").read_text(encoding="utf-8").strip()
    with TestClient(main.app, base_url="https://ops.example.test") as client:
        live = client.get("/health/live")
        assert live.status_code == 200
        assert live.json() == {
            "status": "alive",
            "service": "aiops-control-plane",
            "version": "0.1.0",
        }
        assert live.headers["cache-control"] == "no-store"

        version = client.get("/version")
        assert version.status_code == 200
        assert version.headers["cache-control"] == "no-store"
        assert version.json() == {
            "service": "aiops-control-plane",
            "version": "0.1.0",
            "environment": "prod",
            "release_digest": None,
            "commit_sha": None,
            "provenance": {
                "status": "unverified",
                "reason_code": "release_digest_unconfigured",
            },
        }

        preflight = client.get("/preflight")
        assert preflight.status_code == 200
        assert preflight.json()["status"] in {"supported", "unsupported_for_mutation"}
        assert preflight.json()["detected"]["data_filesystem"] == "xfs"
        assert preflight.json()["supported"]["data_filesystems"] == ["ext4", "xfs"]

        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["reasons"] == ["setup_required", "llm_unverified"]
        assert ready.json()["dependencies"]["worker"]["status"] == "disabled_by_release_gate"
        assert ready.json()["dependencies"]["agent"]["status"] == "disabled_by_release_gate"
        assert ready.json()["dependencies"]["llm"] == {
            "status": "unverified",
            "reason_code": "llm_unverified",
            "verification_reason_code": "release_digest_unconfigured",
        }

        csrf_response = client.get("/api/csrf")
        token = csrf_response.json()["csrf_token"]
        assert "Secure" in csrf_response.headers["set-cookie"]
        invalid = client.post(
            "/api/setup/enroll",
            json={"token": "wrong", "username": "admin", "password": "ProductionPassword123!"},
            headers={"X-CSRF-Token": token, "Origin": "https://ops.example.test"},
        )
        assert invalid.status_code == 403

        enrolled = client.post(
            "/api/setup/enroll",
            json={
                "token": setup_token,
                "username": "admin",
                "password": "ProductionPassword123!",
            },
            headers={"X-CSRF-Token": token, "Origin": "https://ops.example.test"},
        )
        assert enrolled.status_code == 200, enrolled.text
        assert enrolled.json() == {"status": "initialized"}
        assert client.post(
            "/api/setup/enroll",
            json={
                "token": setup_token,
                "username": "admin",
                "password": "ProductionPassword123!",
            },
            headers={"X-CSRF-Token": token, "Origin": "https://ops.example.test"},
        ).status_code == 409

        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"
        assert ready.json()["reasons"] == ["llm_unverified"]
        assert ready.json()["dependencies"]["setup"]["status"] == "complete"

        csrf_response = client.get("/api/csrf")
        login_token = csrf_response.json()["csrf_token"]
        logged_in = client.post(
            "/api/login",
            json={"username": "admin", "password": "ProductionPassword123!"},
            headers={"X-CSRF-Token": login_token, "Origin": "https://ops.example.test"},
        )
        assert logged_in.status_code == 200, logged_in.text
        cookie = logged_in.headers["set-cookie"]
        assert "Secure" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie

        diagnosis = client.post(
            "/api/diagnosis/sessions",
            json={"question": "production diagnosis must remain gated"},
            headers={"X-CSRF-Token": login_token, "Origin": "https://ops.example.test"},
        )
        assert diagnosis.status_code == 503
        assert diagnosis.json()["detail"] == {
            "reason_code": "llm_unverified",
            "verification_reason_code": "release_digest_unconfigured",
        }

        simulated = client.post(
            "/api/tasks",
            json={"title": "must not fake success", "simulate": True},
            headers={"X-CSRF-Token": login_token, "Origin": "https://ops.example.test"},
        )
        assert simulated.status_code == 409
        assert simulated.json()["detail"]["reason_code"] == "simulation_disabled_in_production"

        secret = client.post(
            "/api/secrets",
            json={"name": "fake", "value": "must-never-persist"},
            headers={"X-CSRF-Token": login_token, "Origin": "https://ops.example.test"},
        )
        assert secret.status_code == 501
        assert secret.json()["detail"]["reason_code"] == "secret_store_not_implemented"

        firewall = client.post(
            "/api/security/firewall/preflight",
            json={"port": 443, "protocol": "tcp"},
            headers={"X-CSRF-Token": login_token, "Origin": "https://ops.example.test"},
        )
        assert firewall.status_code == 200
        assert firewall.json()["rollback_plan"] == {
            "available": False,
            "automatic": False,
            "strategy": None,
            "reason_code": "not_implemented",
        }
        assert firewall.json()["safe_to_request_approval"] is False

        audit = client.get("/api/audit")
        assert audit.status_code == 200
        assert [row["event_type"] for row in audit.json()].count(
            "setup_enrollment_completed"
        ) == 1
        assert "secret_created" not in [row["event_type"] for row in audit.json()]

        combined = " ".join(
            [live.text, version.text, preflight.text, ready.text, enrolled.text, logged_in.text]
        )
        assert session_secret not in combined
        assert setup_token not in combined
        assert str(tmp_path) not in combined


def test_support_preflight_matrix() -> None:
    supported = evaluate_support(
        system="Linux",
        machine="x86_64",
        os_release={"ID": "ubuntu", "VERSION_ID": "24.04"},
    )
    assert supported["status"] == "supported"
    assert supported["mutation_supported"] is True

    darwin = evaluate_support(system="Darwin", machine="arm64", os_release={})
    assert darwin["status"] == "unsupported_for_mutation"
    assert darwin["mutation_supported"] is False
    assert "unsupported_operating_system" in darwin["reason_codes"]

    unsupported_ubuntu = evaluate_support(
        system="Linux",
        machine="aarch64",
        os_release={"ID": "ubuntu", "VERSION_ID": "20.04"},
    )
    assert unsupported_ubuntu["status"] == "unsupported_for_mutation"
    assert set(unsupported_ubuntu["reason_codes"]) >= {
        "unsupported_ubuntu_version",
        "unsupported_architecture",
    }


@pytest.mark.parametrize("filesystem", ["ext4", "xfs"])
def test_support_preflight_accepts_supported_database_filesystems(
    filesystem: str,
) -> None:
    mountinfo = "\n".join(
        [
            "25 1 8:1 / / rw,relatime - ext4 /dev/root rw",
            f"36 25 8:2 / /srv/aiops rw,relatime - {filesystem} /dev/data rw",
        ]
    )

    result = evaluate_support(
        system="Linux",
        machine="x86_64",
        os_release={"ID": "ubuntu", "VERSION_ID": "24.04"},
        database_path=Path("/srv/aiops/data/control.sqlite"),
        mountinfo_text=mountinfo,
    )

    assert result["status"] == "supported"
    assert result["mutation_supported"] is True
    assert result["detected"]["data_filesystem"] == filesystem
    assert result["supported"]["data_filesystems"] == ["ext4", "xfs"]
    assert result["reason_codes"] == []


@pytest.mark.parametrize("filesystem", ["nfs", "nfs4", "cifs", "smb", "smb3"])
def test_support_preflight_rejects_network_database_filesystems(
    filesystem: str,
) -> None:
    result = evaluate_support(
        system="Linux",
        machine="x86_64",
        os_release={"ID": "ubuntu", "VERSION_ID": "24.04"},
        database_path=Path("/srv/aiops/control.sqlite"),
        mountinfo_text=f"36 25 0:42 / /srv/aiops rw - {filesystem} server:/data rw",
    )

    assert result["status"] == "unsupported_for_mutation"
    assert result["mutation_supported"] is False
    assert result["detected"]["data_filesystem"] == filesystem
    assert set(result["reason_codes"]) >= {
        "network_data_filesystem",
        "unsupported_data_filesystem",
    }


@pytest.mark.parametrize(
    ("mountinfo", "expected_filesystem", "expected_reason"),
    [
        (
            "36 25 0:42 / /srv/aiops rw - btrfs /dev/data rw",
            "btrfs",
            "unsupported_data_filesystem",
        ),
        ("malformed mountinfo", "unknown", "data_filesystem_unknown"),
        ("", "unknown", "data_filesystem_unknown"),
    ],
)
def test_support_preflight_fails_closed_for_other_or_unknown_filesystems(
    mountinfo: str,
    expected_filesystem: str,
    expected_reason: str,
) -> None:
    result = evaluate_support(
        system="Linux",
        machine="x86_64",
        os_release={"ID": "ubuntu", "VERSION_ID": "24.04"},
        database_path=Path("/srv/aiops/control.sqlite"),
        mountinfo_text=mountinfo,
    )

    assert result["status"] == "unsupported_for_mutation"
    assert result["mutation_supported"] is False
    assert result["detected"]["data_filesystem"] == expected_filesystem
    assert expected_reason in result["reason_codes"]
    assert "unsupported_data_filesystem" in result["reason_codes"]


def test_support_preflight_decodes_mountinfo_paths_and_uses_deepest_mount() -> None:
    result = evaluate_support(
        system="Linux",
        machine="x86_64",
        os_release={"ID": "ubuntu", "VERSION_ID": "22.04"},
        database_path=Path("/srv/aiops data/sqlite/control.sqlite"),
        mountinfo_text="\n".join(
            [
                "25 1 8:1 / / rw,relatime - ext4 /dev/root rw",
                r"36 25 8:2 / /srv/aiops\040data rw,relatime - xfs /dev/data rw",
            ]
        ),
    )

    assert result["status"] == "supported"
    assert result["detected"]["data_filesystem"] == "xfs"


def test_provider_uses_canonical_key_file_and_explicit_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    env = production_environment(tmp_path, now)
    apply_environment(monkeypatch, env)

    from app.ai.providers import OpenAICompatibleChatProvider, llm_enabled, model_name

    provider = OpenAICompatibleChatProvider()
    assert provider.api_key == "k" * 48
    assert provider.base_url == "https://llm.example.test/v1"
    assert provider.model == "frozen-production-model"
    assert llm_enabled() is True
    assert model_name() == "frozen-production-model"


def test_test_environment_keeps_mock_provider_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_environment(
        monkeypatch,
        {"AIOPS_ENV": "test", "AIOPS_DB_PATH": str(tmp_path / "test.sqlite")},
    )

    from app.ai.providers import llm_enabled, llm_mode, model_name

    assert llm_mode() == "mock"
    assert model_name() == "mock"
    assert llm_enabled() is False


def test_non_production_provider_can_be_explicitly_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_environment(
        monkeypatch,
        {
            "AIOPS_ENV": "test",
            "AIOPS_DB_PATH": str(tmp_path / "disabled-provider.sqlite"),
            "AIOPS_LLM_MODE": "moonshot",
            "AIOPS_LLM_ENABLED_PROVIDERS": "",
            "AIOPS_LLM_BASE_URL": "https://api.moonshot.ai/v1",
            "AIOPS_LLM_MODEL": "kimi-k2.6",
            "AIOPS_LLM_API_KEY": "test-provider-key",
        },
    )

    from app.ai.providers import llm_enabled

    assert llm_enabled() is False


def test_version_reports_declared_release_identity_without_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "sha256:" + "a" * 64
    commit_sha = "b" * 40
    apply_environment(
        monkeypatch,
        {
            "AIOPS_ENV": "test",
            "AIOPS_DB_PATH": str(tmp_path / "version.sqlite"),
            "AIOPS_RELEASE_DIGEST": digest,
            "AIOPS_COMMIT_SHA": commit_sha,
        },
    )
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        response = client.get("/version")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "service": "aiops-control-plane",
        "version": "0.1.0",
        "environment": "test",
        "release_digest": digest,
        "commit_sha": commit_sha,
        "provenance": {"status": "declared", "reason_code": None},
    }


def provider_evidence_environment(
    tmp_path: Path,
    now: datetime,
    *,
    overrides: dict | None = None,
    writable: bool = False,
) -> tuple[dict[str, str], dict]:
    from app.ai.verification import REQUIRED_CHECKS

    env = production_environment(tmp_path, now)
    release_digest = "sha256:" + "a" * 64
    commit_sha = "f" * 40
    evidence = {
        "schema_version": 2,
        "result": "pass",
        "executed_at": now.isoformat(),
        "provider_id": env["AIOPS_LLM_MODE"],
        "feature_flag": {
            "enabled_provider_id": env["AIOPS_LLM_MODE"],
            "exclusive": True,
        },
        "provider_origin": env["AIOPS_LLM_BASE_URL"],
        "model": env["AIOPS_LLM_MODEL"],
        "release_digest": release_digest,
        "commit_sha": commit_sha,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "runner_identity": "test-runner",
        "evidence_path": "test-evidence/llm-evidence.json",
        "account_identifier": "test-account",
        "region": "PRC",
        "provider_capabilities": {
            "streaming": True,
            "tool_calls": True,
            "usage_reporting": True,
        },
        "model_discovery": {
            "method": "authenticated_models_endpoint",
            "path": "/models",
            "model_confirmed": True,
            "response_sha256": "sha256:" + "c" * 64,
        },
        "tool_call": {
            "name": "probe_echo",
            "arguments": {"value": "r0-live-contract-probe"},
            "schema_match": True,
        },
        "invalid_tool_rejection": {
            "unknown_tool_rejected": True,
            "extra_field_rejected": True,
        },
        "stream": {
            "json_chunks": 2,
            "done_received": True,
            "contract_marker_received": True,
            "usage_reported": True,
            "content_type": "text/event-stream",
            "normalized_events_sha256": "sha256:" + "f" * 64,
        },
        "timeout_retry": {
            "connect_timeout_seconds": 10,
            "read_timeout_seconds": 60,
            "max_retries": 1,
            "attempts": {"model_discovery": 1, "tool_call": 1, "streaming": 1},
        },
        "redaction_capture": {
            "seeded_secret_absent": True,
            "replacement_marker_present": True,
            "capture_sha256": "sha256:" + "e" * 64,
        },
        "usage_cost": {
            "input_tokens": 10,
            "cached_input_tokens": 2,
            "cache_miss_input_tokens": 8,
            "output_tokens": 5,
            "estimated_cost": "0.0001",
            "successful_responses_estimated_cost": "0.0001",
            "retry_cost_upper_bound": "0",
            "preflight_worst_case_cost": "0.001",
            "max_input_tokens_per_probe": 512,
            "input_payload_byte_upper_bound": 400,
            "tool_attempt_budget": 2,
            "approved_cap": "0.01",
            "currency": "CNY",
            "rates_per_million_tokens": {
                "cache_hit_input": "0.02",
                "cache_miss_input": "1",
                "output": "2",
            },
        },
        "quota": {
            "concurrency_limit": 2500,
            "requests_per_minute": "provider-managed",
            "tokens_per_minute": "provider-managed",
            "tokens_per_day": "unlimited",
            "balance_alert_threshold": "10",
        },
        "quota_pricing_evidence": {
            "account_tier": "test-approved-tier",
            "source_url": "https://provider.example.test/pricing",
            "source_content_sha256": "sha256:" + "c" * 64,
            "snapshot_sha256": "sha256:" + "b" * 64,
            "snapshot_schema_version": 1,
            "reviewed_at": now.isoformat(),
            "currency": "CNY",
            "prices_per_million_tokens": {
                "cache_hit_input": "0.02",
                "cache_miss_input": "1",
                "output": "2",
            },
            "quota": {
                "concurrency_limit": 2500,
                "requests_per_minute": "provider-managed",
                "tokens_per_minute": "provider-managed",
                "tokens_per_day": "unlimited",
                "balance_alert_threshold": "10",
            },
        },
        "policy": {
            "training_opt_out_confirmed": True,
            "redacted_operational_data_only": True,
            "provider_policy_sha256": "sha256:" + "d" * 64,
            "provider_policy_url": "https://provider.example.test/privacy",
            "reviewed_at": now.isoformat(),
            "ai_owner": "ai-owner",
            "security_owner": "security-owner",
            "privacy_owner": "privacy-owner",
            "owner_approvals": {
                role: {
                    "role": role,
                    "approver_identity": f"{role}-owner",
                    "approved": True,
                    "approved_at": now.isoformat(),
                    "provider_policy_sha256": "sha256:" + "d" * 64,
                    "quota_pricing_snapshot_sha256": "sha256:" + "b" * 64,
                }
                for role in ("ai", "security", "privacy")
            },
        },
        "required_checks": {name: True for name in REQUIRED_CHECKS},
    }
    if overrides:
        evidence.update(overrides)
    evidence_file = tmp_path / "llm-evidence.json"
    raw = json.dumps(evidence, sort_keys=True).encode()
    evidence_file.write_bytes(raw)
    evidence_file.chmod(0o600 if writable else 0o400)
    env.update(
        {
            "AIOPS_RELEASE_DIGEST": release_digest,
            "AIOPS_COMMIT_SHA": commit_sha,
            "AIOPS_LLM_EVIDENCE_FILE": str(evidence_file),
            "AIOPS_LLM_EVIDENCE_SHA256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
    )
    return env, evidence


@pytest.mark.parametrize(
    ("overrides", "reason_code"),
    [
        (
            {
                "provider_id": "deepseek",
                "feature_flag": {"enabled_provider_id": "deepseek", "exclusive": True},
            },
            "llm_evidence_provider_id_mismatch",
        ),
        ({"provider_origin": "https://different.example.test/v1"}, "llm_evidence_provider_mismatch"),
        ({"model": "different-model"}, "llm_evidence_model_mismatch"),
        ({"release_digest": "sha256:" + "b" * 64}, "llm_evidence_release_mismatch"),
        ({"commit_sha": "e" * 40}, "llm_evidence_commit_mismatch"),
        ({"expires_at": "2000-01-01T00:00:00+00:00"}, "llm_evidence_expired"),
        ({"runner_identity": ""}, "llm_evidence_contract_incomplete"),
        (
            {"tool_call": {"name": "probe_echo", "schema_match": True}},
            "llm_evidence_contract_incomplete",
        ),
        (
            {
                "feature_flag": {
                    "enabled_provider_id": "openai_compatible",
                    "exclusive": False,
                }
            },
            "llm_evidence_contract_incomplete",
        ),
        ({"quota": {}}, "llm_evidence_contract_incomplete"),
        (
            {"quota_pricing_evidence": {"account_tier": "test"}},
            "llm_evidence_contract_incomplete",
        ),
        (
            {
                "usage_cost": {
                    "input_tokens": 1,
                    "cached_input_tokens": 0,
                    "cache_miss_input_tokens": 1,
                    "output_tokens": 1,
                    "estimated_cost": "Infinity",
                    "successful_responses_estimated_cost": "Infinity",
                    "retry_cost_upper_bound": "0",
                    "preflight_worst_case_cost": "Infinity",
                    "max_input_tokens_per_probe": 512,
                    "input_payload_byte_upper_bound": 400,
                    "tool_attempt_budget": 2,
                    "approved_cap": "Infinity",
                    "currency": "CNY",
                    "rates_per_million_tokens": {
                        "cache_hit_input": "0",
                        "cache_miss_input": "1",
                        "output": "1",
                    },
                }
            },
            "llm_evidence_contract_incomplete",
        ),
        ({"required_checks": {}}, "llm_evidence_checks_incomplete"),
    ],
)
def test_provider_evidence_must_match_deployment(
    tmp_path: Path,
    overrides: dict,
    reason_code: str,
) -> None:
    from app.ai.verification import validate_provider_evidence

    now = datetime.now(UTC)
    env, _ = provider_evidence_environment(tmp_path, now, overrides=overrides)
    settings = load_settings(env, validate=True, current_time=now)
    result = validate_provider_evidence(settings, current_time=now)
    assert result.verified is False
    assert result.reason_code == reason_code


@pytest.mark.parametrize(
    "mutation",
    ["price_mismatch", "duplicate_approver", "approval_digest_mismatch"],
)
def test_provider_evidence_rejects_forged_governance_bindings(
    tmp_path: Path,
    mutation: str,
) -> None:
    from app.ai.verification import validate_provider_evidence

    now = datetime.now(UTC)
    env, original = provider_evidence_environment(tmp_path, now)
    evidence = copy.deepcopy(original)
    if mutation == "price_mismatch":
        evidence["quota_pricing_evidence"]["prices_per_million_tokens"]["output"] = "999"
    elif mutation == "duplicate_approver":
        evidence["policy"]["security_owner"] = "ai-owner"
        evidence["policy"]["owner_approvals"]["security"]["approver_identity"] = "ai-owner"
    else:
        evidence["policy"]["owner_approvals"]["privacy"][
            "quota_pricing_snapshot_sha256"
        ] = "sha256:" + "e" * 64
    raw = json.dumps(evidence, sort_keys=True).encode()
    evidence_file = Path(env["AIOPS_LLM_EVIDENCE_FILE"])
    evidence_file.chmod(0o600)
    evidence_file.write_bytes(raw)
    evidence_file.chmod(0o400)
    env["AIOPS_LLM_EVIDENCE_SHA256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    settings = load_settings(env, validate=True, current_time=now)
    result = validate_provider_evidence(settings, current_time=now)
    assert result.verified is False
    assert result.reason_code == "llm_evidence_contract_incomplete"


def test_provider_evidence_requires_protected_file_and_matching_digest(tmp_path: Path) -> None:
    from app.ai.verification import validate_provider_evidence

    now = datetime.now(UTC)
    env, _ = provider_evidence_environment(tmp_path, now, writable=True)
    settings = load_settings(env, validate=True, current_time=now)
    assert validate_provider_evidence(settings, current_time=now).reason_code == (
        "llm_evidence_unprotected"
    )

    evidence_file = Path(env["AIOPS_LLM_EVIDENCE_FILE"])
    evidence_file.chmod(0o400)
    env["AIOPS_LLM_EVIDENCE_SHA256"] = "sha256:" + "f" * 64
    settings = load_settings(env, validate=True, current_time=now)
    assert validate_provider_evidence(settings, current_time=now).reason_code == (
        "llm_evidence_digest_mismatch"
    )


def test_provider_evidence_is_bound_to_the_enabled_provider_flag(tmp_path: Path) -> None:
    from app.ai.verification import validate_provider_evidence

    now = datetime.now(UTC)
    env, _ = provider_evidence_environment(tmp_path, now)
    settings = load_settings(env, validate=True, current_time=now)
    mismatched = replace(
        settings,
        llm_enabled_providers=frozenset({"deepseek"}),
    )
    result = validate_provider_evidence(mismatched, current_time=now)
    assert result.reason_code == "llm_evidence_feature_flag_mismatch"


def test_verified_provider_evidence_unlocks_readiness_and_diagnosis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.storage import db

    now = datetime.now(UTC)
    env, _ = provider_evidence_environment(tmp_path, now)
    db.configure(Path(env["AIOPS_DB_PATH"]), tmp_path / "bootstrap.txt")
    db.init_db(create_bootstrap_admin=False)
    assert db.complete_initial_setup("admin", "ProductionPassword123!") is True
    env.pop("AIOPS_SETUP_TOKEN_FILE")
    env.pop("AIOPS_SETUP_TOKEN_EXPIRES_AT")
    apply_environment(monkeypatch, env)
    import app.main as main

    importlib.reload(main)
    monkeypatch.setattr(
        main,
        "run_llm_diagnosis",
        lambda actor_id, session_id, question: {
            "status": "verified-provider-used",
            "question": question,
        },
    )
    with TestClient(main.app, base_url="https://ops.example.test") as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["dependencies"]["llm"] == {
            "status": "verified",
            "reason_code": None,
            "verification_reason_code": None,
        }
        token = client.get("/api/csrf").json()["csrf_token"]
        login = client.post(
            "/api/login",
            json={"username": "admin", "password": "ProductionPassword123!"},
            headers={"X-CSRF-Token": token, "Origin": "https://ops.example.test"},
        )
        assert login.status_code == 200
        token = client.get("/api/csrf").json()["csrf_token"]
        diagnosis = client.post(
            "/api/diagnosis/sessions",
            json={"question": "safe question"},
            headers={"X-CSRF-Token": token, "Origin": "https://ops.example.test"},
        )
        assert diagnosis.status_code == 200
        assert diagnosis.json()["status"] == "verified-provider-used"


@pytest.mark.parametrize("failure_stage", ["after_admin", "after_marker", "after_audit"])
def test_initial_setup_transaction_rolls_back_every_stage(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    from app.storage import db

    db.configure(tmp_path / "atomic.sqlite", tmp_path / "bootstrap.txt")
    db.init_db(create_bootstrap_admin=False)

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError("injected setup failure")

    with pytest.raises(RuntimeError, match="injected setup failure"):
        db.complete_initial_setup("admin", "ProductionPassword123!", failure_hook=fail)

    assert db.has_users() is False
    assert db.setup_consumed() is False
    assert db.list_audit() == []


def test_initial_setup_commits_admin_marker_and_audit_once(tmp_path: Path) -> None:
    from app.storage import db

    db.configure(tmp_path / "atomic-success.sqlite", tmp_path / "bootstrap.txt")
    db.init_db(create_bootstrap_admin=False)
    assert db.complete_initial_setup("admin", "ProductionPassword123!") is True
    assert db.has_users() is True
    assert db.setup_consumed() is True
    assert [row["event_type"] for row in db.list_audit()] == ["setup_enrollment_completed"]
    assert db.complete_initial_setup("other", "AnotherPassword123!") is False
    assert [row["event_type"] for row in db.list_audit()] == ["setup_enrollment_completed"]
