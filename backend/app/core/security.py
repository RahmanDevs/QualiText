"""Security utilities: password hashing, JWT tokens, URL validation, and RBAC matrix."""

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings


class Role(StrEnum):
    """Workspace-level RBAC roles defined in CLAUDE.md Module G2."""

    ADMIN = "admin"
    GOVERNANCE = "governance"
    CURATOR = "curator"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(StrEnum):
    """Granular permissions defined in CLAUDE.md Section 2.9."""

    DATASET_READ = "dataset:read"
    DATASET_WRITE = "dataset:write"
    RUN_EXECUTE = "run:execute"
    PII_VIEW = "pii:view"
    EXPORT_CREATE = "export:create"
    POLICY_MANAGE = "policy:manage"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.GOVERNANCE: frozenset(
        {
            Permission.DATASET_READ,
            Permission.PII_VIEW,
            Permission.POLICY_MANAGE,
            Permission.AUDIT_READ,
        }
    ),
    Role.CURATOR: frozenset(
        {
            Permission.DATASET_READ,
            Permission.DATASET_WRITE,
            Permission.RUN_EXECUTE,
            Permission.EXPORT_CREATE,
        }
    ),
    Role.ANALYST: frozenset(
        {
            Permission.DATASET_READ,
            Permission.DATASET_WRITE,
            Permission.RUN_EXECUTE,
            Permission.EXPORT_CREATE,
        }
    ),
    Role.VIEWER: frozenset({Permission.DATASET_READ}),
}

_password_hash = PasswordHash.recommended()


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password using Argon2."""
    return _password_hash.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Verify a plaintext password against its stored Argon2 hash."""
    return _password_hash.verify(plain_password, password_hash)


def has_permission(role_name: str, permission: Permission) -> bool:
    """Check whether a role string grants the requested permission."""
    try:
        role = Role(role_name.lower())
    except ValueError:
        return False
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


_LOCAL_LLM_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_llm_url(url: str) -> bool:
    """Return True strictly when `url` resolves to a local loopback host."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in _LOCAL_LLM_HOSTS


def create_token(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    *,
    jti: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT token."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if jti is not None:
        payload["jti"] = jti
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str) -> str:
    """Create a short-lived access JWT."""
    settings = get_settings()
    return create_token(
        subject=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token_with_jti(user_id: str) -> tuple[str, str, datetime]:
    """Create a refresh JWT with unique JTI and expiration timestamp for rotation tracking."""
    settings = get_settings()
    jti = str(uuid.uuid4())
    delta = timedelta(days=settings.refresh_token_expire_days)
    expires_at = datetime.now(UTC) + delta
    token = create_token(
        subject=user_id,
        token_type="refresh",
        expires_delta=delta,
        jti=jti,
    )
    return token, jti, expires_at


def decode_token_payload(token: str, expected_type: str = "access") -> dict[str, Any] | None:
    """Decode and validate a JWT payload, returning None when invalid or mismatched type."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != expected_type:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    return dict(payload)


def decode_token(token: str, expected_type: str = "access") -> str | None:
    """Decode and validate a JWT, returning the subject (`user_id`) or None if invalid."""
    payload = decode_token_payload(token, expected_type=expected_type)
    if payload is None:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None
