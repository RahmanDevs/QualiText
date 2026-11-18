"""Authentication endpoints (`/api/v1/auth`)."""

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.db.models import Membership, User, Workspace
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserMeResponse,
    WorkspaceMembershipResponse,
)
from app.services.audit_service import record_audit_log

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate user with email and password and record login in audit log."""
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        logger.warning("auth_login_failed", email_domain=payload.email.split("@")[-1])
        raise AppError(
            code="INVALID_CREDENTIALS",
            message="Email atau kata sandi salah.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_active:
        raise AppError(
            code="USER_INACTIVE",
            message="Akun Anda sedang dinonaktifkan. Hubungi Admin.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    client_ip = request.client.host if request.client else None
    await record_audit_log(
        db,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        ip_address=client_ip,
    )
    await db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Rotate access and refresh tokens using a valid refresh token."""
    user_id = decode_token(payload.refresh_token, expected_type="refresh")
    if user_id is None:
        raise AppError(
            code="INVALID_REFRESH_TOKEN",
            message="Token penyegaran tidak valid atau telah kedaluwarsa.",
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

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserMeResponse)
async def read_current_user(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserMeResponse:
    """Return current authenticated user profile and workspace memberships."""
    stmt = (
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
    )
    rows = (await db.execute(stmt)).all()
    workspaces = [
        WorkspaceMembershipResponse(id=ws.id, name=ws.name, slug=ws.slug, role=role)
        for ws, role in rows
    ]
    return UserMeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_superadmin=user.is_superadmin,
        workspaces=workspaces,
    )
