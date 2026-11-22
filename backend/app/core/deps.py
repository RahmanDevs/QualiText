"""FastAPI dependencies for authentication (JWT & scoped API tokens) and workspace RBAC."""

import hashlib
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import Permission, decode_token, has_permission
from app.db.models import ApiToken, Membership, User
from app.db.session import get_db

_bearer_scheme = HTTPBearer(auto_error=False)
_current_api_token_scopes: ContextVar[frozenset[str] | None] = ContextVar(
    "api_token_scopes", default=None
)


def reset_api_token_scopes() -> None:
    """Reset the per-request API token scope ContextVar."""
    _current_api_token_scopes.set(None)


def has_api_token_scope(permission: Permission) -> bool:
    """Return True if current auth context allows `permission`."""
    scopes = _current_api_token_scopes.get()
    if scopes is None:
        return True
    return "*" in scopes or permission.value in scopes


def check_api_token_scope(permission: Permission) -> None:
    """Raise 403 INSUFFICIENT_TOKEN_SCOPE if active API token lacks `permission`."""
    if not has_api_token_scope(permission):
        raise AppError(
            code="INSUFFICIENT_TOKEN_SCOPE",
            message=f"Token API tidak memiliki scope `{permission.value}`.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


async def _authenticate_api_token(raw_token: str, db: AsyncSession) -> User:
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    rec = (
        await db.execute(select(ApiToken).where(ApiToken.token_hash == token_hash))
    ).scalar_one_or_none()
    if rec is None or rec.is_revoked:
        raise AppError(
            code="INVALID_TOKEN",
            message="Token API tidak valid atau telah dicabut.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if rec.expires_at is not None:
        expires = (
            rec.expires_at
            if rec.expires_at.tzinfo is not None
            else rec.expires_at.replace(tzinfo=UTC)
        )
        if expires <= datetime.now(UTC):
            raise AppError(
                code="TOKEN_EXPIRED",
                message="Token API telah kedaluwarsa.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

    user = (await db.execute(select(User).where(User.id == rec.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AppError(
            code="USER_INACTIVE",
            message="Akun pemilik token tidak ditemukan atau tidak aktif.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    scopes = frozenset(s.strip() for s in rec.scopes.split(",") if s.strip())
    _current_api_token_scopes.set(scopes)
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate Bearer JWT or `qt_...` API Token and return the active User."""
    if credentials is None:
        raise AppError(
            code="UNAUTHORIZED",
            message="Autentikasi diperlukan. Silakan masuk terlebih dahulu.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    raw_token = credentials.credentials
    if raw_token.startswith("qt_"):
        return await _authenticate_api_token(raw_token, db)

    _current_api_token_scopes.set(None)
    user_id = decode_token(raw_token, expected_type="access")
    if user_id is None:
        raise AppError(
            code="INVALID_TOKEN",
            message="Sesi tidak valid atau telah kedaluwarsa. Silakan masuk kembali.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AppError(
            code="USER_INACTIVE",
            message="Akun tidak ditemukan atau tidak aktif.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return user


def require_workspace_permission(
    permission: Permission,
) -> Callable[..., Coroutine[Any, Any, User]]:
    """Return a dependency enforcing `permission` on `workspace_id` path parameter."""

    async def _checker(
        workspace_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        check_api_token_scope(permission)
        if user.is_superadmin:
            return user

        result = await db.execute(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.workspace_id == workspace_id,
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None or not has_permission(membership.role, permission):
            raise AppError(
                code="FORBIDDEN",
                message=(
                    "Anda tidak memiliki izin untuk melakukan aksi ini pada workspace tersebut."
                ),
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    return _checker
