from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import ConfigurationError, load_settings


def production_environment(tmp_path: Path) -> dict[str, str]:
    session_secret_file = tmp_path / "session-secret"
    session_secret_file.write_text("s" * 48 + "\n", encoding="utf-8")
    return {
        "AIOPS_ENV": "prod",
        "AIOPS_SESSION_SECRET_FILE": str(session_secret_file),
        "AIOPS_TRUSTED_ORIGINS": "https://ops.example.test",
        "AIOPS_COOKIE_SECURE": "1",
        "AIOPS_TLS_ENABLED": "1",
        "AIOPS_DEBUG_SKIP_PASSWORD_CHANGE": "0",
        "AIOPS_LLM_MODE": "deepseek",
        "AIOPS_LLM_ENABLED_PROVIDERS": "deepseek",
        "AIOPS_LLM_BASE_URL": "https://api.deepseek.com",
        "AIOPS_LLM_MODEL": "deepseek-v4-flash",
    }


@pytest.mark.parametrize(
    "variable",
    ["AIOPS_LLM_API_KEY", "AIOPS_DEEPSEEK_API_KEY"],
)
@pytest.mark.parametrize("validate", [False, True])
def test_production_rejects_direct_llm_api_key_environment_values(
    tmp_path: Path,
    variable: str,
    validate: bool,
) -> None:
    env = production_environment(tmp_path)
    env[variable] = "direct-secret-is-forbidden"

    with pytest.raises(ConfigurationError, match=r"\*_API_KEY_FILE"):
        load_settings(env, validate=validate, current_time=datetime.now(UTC))


def test_production_accepts_llm_api_key_only_from_file(tmp_path: Path) -> None:
    env = production_environment(tmp_path)
    key_file = tmp_path / "deepseek-api-key"
    key_file.write_text("file-secret-is-accepted\n", encoding="utf-8")
    key_file.chmod(0o600)
    env["AIOPS_DEEPSEEK_API_KEY_FILE"] = str(key_file)

    settings = load_settings(
        env,
        validate=True,
        current_time=datetime.now(UTC),
    )

    assert settings.llm_api_key_configured is True
    assert settings.llm_api_key_from_file is True


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_production_rejects_llm_key_file_readable_by_other_users(
    tmp_path: Path,
    mode: int,
) -> None:
    env = production_environment(tmp_path)
    key_file = tmp_path / "deepseek-api-key"
    key_file.write_text("secret\n", encoding="utf-8")
    key_file.chmod(mode)
    env["AIOPS_DEEPSEEK_API_KEY_FILE"] = str(key_file)

    with pytest.raises(ConfigurationError, match="group or other users"):
        load_settings(env, validate=True, current_time=datetime.now(UTC))


def test_production_rejects_llm_key_file_symlink(tmp_path: Path) -> None:
    env = production_environment(tmp_path)
    target = tmp_path / "key-target"
    target.write_text("secret\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "key-link"
    link.symlink_to(target)
    env["AIOPS_DEEPSEEK_API_KEY_FILE"] = str(link)

    with pytest.raises(ConfigurationError, match="could not be read"):
        load_settings(env, validate=True, current_time=datetime.now(UTC))


def test_production_rejects_oversized_llm_key_file(tmp_path: Path) -> None:
    env = production_environment(tmp_path)
    key_file = tmp_path / "deepseek-api-key"
    key_file.write_text("x" * (16 * 1024 + 1), encoding="utf-8")
    key_file.chmod(0o600)
    env["AIOPS_DEEPSEEK_API_KEY_FILE"] = str(key_file)

    with pytest.raises(ConfigurationError, match="between 1 byte and 16 KiB"):
        load_settings(env, validate=True, current_time=datetime.now(UTC))
