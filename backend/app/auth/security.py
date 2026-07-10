from __future__ import annotations

import hmac
from datetime import UTC, datetime
from hashlib import sha256
from secrets import token_urlsafe
from typing import Annotated

from fastapi import Cookie, Header, HTTPException, Request, Response

from app.config import Settings, load_settings
from app.models.schemas import SessionIdentity
from app.storage import db


SESSION_COOKIE = "aiops_session"
CSRF_COOKIE = "aiops_csrf"
_runtime_settings: Settings | None = None


def configure_settings(settings: Settings | None) -> None:
    global _runtime_settings
    _runtime_settings = settings


def current_settings() -> Settings:
    return _runtime_settings or load_settings()


def skip_password_change() -> bool:
    return current_settings().debug_skip_password_change


def sign(value: str) -> str:
    return hmac.new(current_settings().session_secret.encode(), value.encode(), sha256).hexdigest()


def pack_session(session_id: str) -> str:
    return f"{session_id}.{sign(session_id)}"


def unpack_session(cookie_value: str | None) -> str | None:
    if not cookie_value or "." not in cookie_value:
        return None
    session_id, signature = cookie_value.rsplit(".", 1)
    if not hmac.compare_digest(signature, sign(session_id)):
        return None
    return session_id


def set_session_cookie(response: Response, session_id: str) -> None:
    settings = current_settings()
    response.set_cookie(
        SESSION_COOKIE,
        pack_session(session_id),
        httponly=True,
        samesite="strict",
        secure=settings.cookie_secure,
        max_age=8 * 60 * 60,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        samesite="strict",
        secure=current_settings().cookie_secure,
    )


def issue_csrf(response: Response) -> str:
    settings = current_settings()
    token = token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,
        samesite="strict",
        secure=settings.cookie_secure,
    )
    return token


def require_csrf(
    request: Request,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    trusted_origins = set(current_settings().trusted_origins)
    origin = request.headers.get("origin")
    if origin and origin not in trusted_origins:
        raise HTTPException(status_code=403, detail="invalid origin")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        raise HTTPException(status_code=403, detail="invalid csrf token")


def require_identity(
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> SessionIdentity:
    session_id = unpack_session(session_cookie)
    if not session_id:
        raise HTTPException(status_code=401, detail="authentication required")
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="authentication required")
    expires_at = datetime.fromisoformat(session["expires_at"])
    if expires_at < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="session expired")
    user = db.get_user_by_id(session["actor_id"])
    if not user:
        raise HTTPException(status_code=401, detail="authentication required")
    return SessionIdentity(
        actor_id=user["id"],
        session_id=session["id"],
        role=user["role"],
        created_at=datetime.fromisoformat(session["created_at"]),
        expires_at=expires_at,
        must_change_password=bool(user["must_change_password"]) and not skip_password_change(),
    )


def require_ready_identity(identity: SessionIdentity) -> SessionIdentity:
    if identity.must_change_password:
        raise HTTPException(status_code=403, detail="password change required")
    return identity
