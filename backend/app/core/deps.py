"""FastAPI dependencies for authentication and workspace-scoped RBAC."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import Permission, decode_token, has_permission
from app.db.models import Membership, User
from app.db.session import get_db

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Validate Bearer JWT and return the active User."""
    if credentials is None:
        raise AppError(
            code="UNAUTHORIZED",
            message="Autentikasi diperlukan. Silakan masuk terlebih dahulu.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_id = decode_token(credentials.credentials, expected_type="access")
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
