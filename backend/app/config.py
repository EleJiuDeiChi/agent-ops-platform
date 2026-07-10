from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from app.ai.provider_profiles import (
    LIVE_PROVIDER_IDS,
    PROVIDER_PROFILES,
    get_provider_profile,
)


class ConfigurationError(RuntimeError):
    """Raised when runtime configuration is unsafe or internally inconsistent."""


class Environment(str, Enum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


DEV_TRUSTED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:5174",
    "http://localhost:5174",
    "http://127.0.0.1:8080",
)
UNSAFE_SESSION_SECRETS = {
    "dev-session-secret-change-me",
    "change-me-before-lan-exposure",
    "change-me",
}
MAX_SETUP_TTL = timedelta(hours=1)
RELEASE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")


def _parse_bool(raw: str | None, *, default: bool, name: str) -> tuple[bool, bool]:
    if raw is None:
        return default, False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True, True
    if normalized in {"0", "false", "no", "off"}:
        return False, True
    raise ConfigurationError(f"{name} must be a boolean value")


def _parse_datetime(raw: str | None, *, name: str) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None:
        raise ConfigurationError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def _read_secret_file(path_value: str, *, variable: str) -> str:
    path = Path(path_value)
    try:
        if not path.is_file():
            raise ConfigurationError(f"{variable}_FILE must reference a readable regular file")
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise ConfigurationError(f"{variable}_FILE could not be read") from exc
    if not value:
        raise ConfigurationError(f"{variable}_FILE must not be empty")
    return value


def _secret_value(environ: Mapping[str, str], variable: str) -> tuple[str | None, bool]:
    direct = environ.get(variable)
    file_path = environ.get(f"{variable}_FILE")
    if direct is not None and file_path is not None:
        raise ConfigurationError(
            f"configure only one of {variable} or {variable}_FILE"
        )
    if file_path is not None:
        return _read_secret_file(file_path, variable=variable), True
    return direct, direct is not None


def _release_identifier(
    raw: str | None,
    *,
    name: str,
    pattern: re.Pattern[str],
) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    if not pattern.fullmatch(value):
        raise ConfigurationError(f"{name} has an invalid format")
    return value


@dataclass(frozen=True)
class Settings:
    environment: Environment
    db_path: Path
    bootstrap_password_path: Path
    bind_host: str
    release_digest: str | None
    commit_sha: str | None
    session_secret: str = field(repr=False)
    session_secret_configured: bool
    trusted_origins: tuple[str, ...]
    trusted_origins_configured: bool
    cookie_secure: bool
    cookie_secure_configured: bool
    tls_enabled: bool
    tls_enabled_configured: bool
    debug_skip_password_change: bool
    bootstrap_admin_password: str | None = field(repr=False)
    bootstrap_admin_password_configured: bool
    setup_token: str | None = field(repr=False)
    setup_token_configured: bool
    setup_token_expires_at: datetime | None
    llm_mode: str
    llm_mode_configured: bool
    llm_base_url: str | None
    llm_base_url_configured: bool
    llm_model: str | None
    llm_model_configured: bool
    llm_enabled_providers: frozenset[str]
    llm_enabled_providers_configured: bool
    llm_api_key: str | None = field(repr=False)
    llm_api_key_configured: bool = False
    llm_evidence_file: Path | None = None
    llm_evidence_sha256: str | None = None

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PROD

    def validate_static(self, *, current_time: datetime | None = None) -> None:
        if not self.is_production:
            return

        now = (current_time or datetime.now(UTC)).astimezone(UTC)
        if not self.session_secret_configured:
            raise ConfigurationError(
                "production requires AIOPS_SESSION_SECRET or AIOPS_SESSION_SECRET_FILE"
            )
        if len(self.session_secret) < 32 or self.session_secret in UNSAFE_SESSION_SECRETS:
            raise ConfigurationError("production session secret is missing or unsafe")
        if not self.trusted_origins_configured or not self.trusted_origins:
            raise ConfigurationError("production requires explicit AIOPS_TRUSTED_ORIGINS")
        for origin in self.trusted_origins:
            parsed = urlparse(origin)
            if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
                raise ConfigurationError(
                    "production trusted origins must be HTTPS origins without paths"
                )
            if "*" in origin:
                raise ConfigurationError("production trusted origins must not contain wildcards")
        if not self.cookie_secure_configured or not self.cookie_secure:
            raise ConfigurationError("production requires AIOPS_COOKIE_SECURE=1")
        if not self.tls_enabled_configured or not self.tls_enabled:
            raise ConfigurationError("production requires AIOPS_TLS_ENABLED=1")
        if self.debug_skip_password_change:
            raise ConfigurationError(
                "AIOPS_DEBUG_SKIP_PASSWORD_CHANGE is forbidden in production"
            )
        if self.bootstrap_admin_password_configured:
            raise ConfigurationError(
                "AIOPS_BOOTSTRAP_ADMIN_PASSWORD is forbidden in production; use setup enrollment"
            )
        if self.setup_token_configured:
            if not self.setup_token or len(self.setup_token) < 32:
                raise ConfigurationError("production setup token must contain at least 32 characters")
            if self.setup_token_expires_at is None:
                raise ConfigurationError(
                    "AIOPS_SETUP_TOKEN_EXPIRES_AT is required with a setup token"
                )
            if self.setup_token_expires_at <= now:
                raise ConfigurationError("production setup token has expired")
            if self.setup_token_expires_at - now > MAX_SETUP_TTL:
                raise ConfigurationError("production setup token TTL must not exceed one hour")
        elif self.setup_token_expires_at is not None:
            raise ConfigurationError(
                "AIOPS_SETUP_TOKEN_EXPIRES_AT requires AIOPS_SETUP_TOKEN or AIOPS_SETUP_TOKEN_FILE"
            )
        if not self.llm_mode_configured or self.llm_mode not in LIVE_PROVIDER_IDS:
            raise ConfigurationError(
                "production requires explicit AIOPS_LLM_MODE=deepseek, moonshot, zhipu, or openai_compatible"
            )
        if not self.llm_base_url_configured or not self.llm_base_url:
            raise ConfigurationError("production requires explicit AIOPS_LLM_BASE_URL")
        try:
            get_provider_profile(self.llm_mode).validate_base_url(
                self.llm_base_url,
                production=True,
            )
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        if not self.llm_model_configured or not self.llm_model:
            raise ConfigurationError("production requires explicit AIOPS_LLM_MODEL")
        if not self.llm_enabled_providers_configured:
            raise ConfigurationError(
                "production requires explicit AIOPS_LLM_ENABLED_PROVIDERS"
            )
        if self.llm_enabled_providers != frozenset({self.llm_mode}):
            raise ConfigurationError(
                "production AIOPS_LLM_ENABLED_PROVIDERS must contain exactly the selected AIOPS_LLM_MODE"
            )
        if not self.llm_api_key_configured or not self.llm_api_key:
            raise ConfigurationError(
                "production requires AIOPS_LLM_API_KEY or AIOPS_LLM_API_KEY_FILE"
            )
        if (self.llm_evidence_file is None) != (self.llm_evidence_sha256 is None):
            raise ConfigurationError(
                "AIOPS_LLM_EVIDENCE_FILE and AIOPS_LLM_EVIDENCE_SHA256 must be configured together"
            )

    def validate_initialization(self, *, initialized: bool) -> None:
        if not self.is_production:
            return
        if initialized and self.setup_token_configured:
            raise ConfigurationError(
                "remove setup token configuration after production enrollment"
            )
        if not initialized and not self.setup_token_configured:
            raise ConfigurationError(
                "an uninitialized production instance requires short-lived setup enrollment"
            )


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    validate: bool = False,
    current_time: datetime | None = None,
) -> Settings:
    env = os.environ if environ is None else environ
    raw_environment = env.get("AIOPS_ENV", "dev").strip().lower()
    try:
        environment = Environment(raw_environment)
    except ValueError as exc:
        raise ConfigurationError("AIOPS_ENV must be dev, test, or prod") from exc

    llm_mode = env.get("AIOPS_LLM_MODE", "mock").strip().lower() or "mock"
    enabled_providers_configured = "AIOPS_LLM_ENABLED_PROVIDERS" in env
    enabled_providers_raw = env.get("AIOPS_LLM_ENABLED_PROVIDERS")
    if enabled_providers_raw is None:
        enabled_providers = (
            frozenset({llm_mode}) if llm_mode in LIVE_PROVIDER_IDS else frozenset()
        )
    else:
        enabled_providers = frozenset(
            item.strip().lower()
            for item in enabled_providers_raw.split(",")
            if item.strip()
        )
        unknown_providers = enabled_providers - LIVE_PROVIDER_IDS
        if unknown_providers:
            unknown = ", ".join(sorted(unknown_providers))
            raise ConfigurationError(
                f"AIOPS_LLM_ENABLED_PROVIDERS contains unsupported providers: {unknown}"
            )
    session_secret, session_secret_configured = _secret_value(env, "AIOPS_SESSION_SECRET")
    if session_secret is None:
        session_secret = (
            "test-session-secret-change-me"
            if environment is Environment.TEST
            else "dev-session-secret-change-me"
        )

    bootstrap_password, bootstrap_password_configured = _secret_value(
        env, "AIOPS_BOOTSTRAP_ADMIN_PASSWORD"
    )
    setup_token, setup_token_configured = _secret_value(env, "AIOPS_SETUP_TOKEN")
    key_sources = []
    provider_key_sources = [
        (str(profile.api_key_variable), provider_id)
        for provider_id, profile in PROVIDER_PROFILES.items()
        if profile.api_key_variable
    ]
    for variable, provider_id in [("AIOPS_LLM_API_KEY", None), *provider_key_sources]:
        value, configured = _secret_value(env, variable)
        if configured:
            key_sources.append((variable, provider_id, value))
    if len(key_sources) > 1:
        raise ConfigurationError("configure exactly one supported LLM API key source")
    llm_api_key = None
    llm_api_key_configured = False
    if key_sources:
        variable, provider_id, llm_api_key = key_sources[0]
        if provider_id is not None and llm_mode != provider_id:
            raise ConfigurationError(
                f"{variable} can only be used with AIOPS_LLM_MODE={provider_id}"
            )
        llm_api_key_configured = True

    trusted_origins_configured = "AIOPS_TRUSTED_ORIGINS" in env
    origins_value = env.get("AIOPS_TRUSTED_ORIGINS")
    trusted_origins = tuple(
        item.strip()
        for item in (
            origins_value.split(",")
            if origins_value is not None
            else DEV_TRUSTED_ORIGINS
        )
        if item.strip()
    )
    cookie_secure, cookie_secure_configured = _parse_bool(
        env.get("AIOPS_COOKIE_SECURE"),
        default=False,
        name="AIOPS_COOKIE_SECURE",
    )
    tls_enabled, tls_enabled_configured = _parse_bool(
        env.get("AIOPS_TLS_ENABLED"),
        default=False,
        name="AIOPS_TLS_ENABLED",
    )
    debug_skip_password_change, _ = _parse_bool(
        env.get("AIOPS_DEBUG_SKIP_PASSWORD_CHANGE"),
        default=environment is Environment.DEV,
        name="AIOPS_DEBUG_SKIP_PASSWORD_CHANGE",
    )

    settings = Settings(
        environment=environment,
        db_path=Path(env.get("AIOPS_DB_PATH", "data/dev.sqlite")),
        bootstrap_password_path=Path(
            env.get("AIOPS_BOOTSTRAP_PASSWORD_PATH", "data/bootstrap_password.txt")
        ),
        bind_host=env.get("AIOPS_BIND_HOST", "127.0.0.1"),
        release_digest=_release_identifier(
            env.get("AIOPS_RELEASE_DIGEST"),
            name="AIOPS_RELEASE_DIGEST",
            pattern=RELEASE_DIGEST_PATTERN,
        ),
        commit_sha=_release_identifier(
            env.get("AIOPS_COMMIT_SHA"),
            name="AIOPS_COMMIT_SHA",
            pattern=COMMIT_SHA_PATTERN,
        ),
        session_secret=session_secret,
        session_secret_configured=session_secret_configured,
        trusted_origins=trusted_origins,
        trusted_origins_configured=trusted_origins_configured,
        cookie_secure=cookie_secure,
        cookie_secure_configured=cookie_secure_configured,
        tls_enabled=tls_enabled,
        tls_enabled_configured=tls_enabled_configured,
        debug_skip_password_change=debug_skip_password_change,
        bootstrap_admin_password=bootstrap_password,
        bootstrap_admin_password_configured=bootstrap_password_configured,
        setup_token=setup_token,
        setup_token_configured=setup_token_configured,
        setup_token_expires_at=_parse_datetime(
            env.get("AIOPS_SETUP_TOKEN_EXPIRES_AT"),
            name="AIOPS_SETUP_TOKEN_EXPIRES_AT",
        ),
        llm_mode=llm_mode,
        llm_mode_configured="AIOPS_LLM_MODE" in env,
        llm_base_url=env.get("AIOPS_LLM_BASE_URL"),
        llm_base_url_configured="AIOPS_LLM_BASE_URL" in env,
        llm_model=env.get("AIOPS_LLM_MODEL"),
        llm_model_configured="AIOPS_LLM_MODEL" in env,
        llm_enabled_providers=enabled_providers,
        llm_enabled_providers_configured=enabled_providers_configured,
        llm_api_key=llm_api_key,
        llm_api_key_configured=llm_api_key_configured,
        llm_evidence_file=(
            Path(env["AIOPS_LLM_EVIDENCE_FILE"])
            if env.get("AIOPS_LLM_EVIDENCE_FILE")
            else None
        ),
        llm_evidence_sha256=_release_identifier(
            env.get("AIOPS_LLM_EVIDENCE_SHA256"),
            name="AIOPS_LLM_EVIDENCE_SHA256",
            pattern=RELEASE_DIGEST_PATTERN,
        ),
    )
    if validate:
        settings.validate_static(current_time=current_time)
    return settings
