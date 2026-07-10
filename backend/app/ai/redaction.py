from __future__ import annotations

import re
from typing import Any, Iterable

from app.config import Settings


SECRET_MARKER = "[REDACTED_SECRET]"
PII_MARKER = "[REDACTED_PII]"

PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
BASIC_AUTH_PATTERN = re.compile(r"(?i)(authorization\s*:\s*basic\s+)[A-Za-z0-9+/=]{8,}")
URL_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@"
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd)\s*[:=]\s*([^\s,;]+)"
)
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
CN_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
CN_ID_PATTERN = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
PAYMENT_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")


def configured_secrets(settings: Settings) -> tuple[str, ...]:
    values: Iterable[str | None] = (
        settings.session_secret,
        settings.bootstrap_admin_password,
        settings.setup_token,
        settings.llm_api_key,
    )
    return tuple(value for value in values if value and len(value) >= 4)


def redact_text(value: str, *, secrets: Iterable[str] = ()) -> str:
    redacted = value
    for secret in sorted(set(secrets), key=len, reverse=True):
        redacted = redacted.replace(secret, SECRET_MARKER)
    redacted = PRIVATE_KEY_PATTERN.sub(SECRET_MARKER, redacted)
    redacted = BEARER_PATTERN.sub(f"Bearer {SECRET_MARKER}", redacted)
    redacted = BASIC_AUTH_PATTERN.sub(rf"\1{SECRET_MARKER}", redacted)
    redacted = URL_CREDENTIAL_PATTERN.sub(
        rf"\1{SECRET_MARKER}:{SECRET_MARKER}@",
        redacted,
    )
    redacted = SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}={SECRET_MARKER}", redacted
    )
    redacted = EMAIL_PATTERN.sub(PII_MARKER, redacted)
    redacted = CN_PHONE_PATTERN.sub(PII_MARKER, redacted)
    redacted = CN_ID_PATTERN.sub(PII_MARKER, redacted)
    redacted = PAYMENT_CARD_PATTERN.sub(PII_MARKER, redacted)
    return redacted


def redact_for_provider(value: Any, *, settings: Settings) -> Any:
    secrets = configured_secrets(settings)

    def visit(item: Any) -> Any:
        if isinstance(item, str):
            return redact_text(item, secrets=secrets)
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, tuple):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {str(key): visit(child) for key, child in item.items()}
        return item

    return visit(value)
