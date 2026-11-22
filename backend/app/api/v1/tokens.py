"""API Token management endpoints (`/api/v1/api-tokens`, Module G5)."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.security import Permission
from app.db.models import ApiToken, User
from app.db.session import get_db
from app.services.audit_service import extract_client_ip, record_audit_log

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])
_VALID_SCOPES = frozenset(p.value for p in Permission) | {"*"}


class ApiTokenCreateRequest(BaseModel):
    """Create a scoped API token."""

    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["dataset:read", "export:create"])
    expires_in_days: int = Field(default=90, ge=1, le=365)


class ApiTokenCreatedResponse(BaseModel):
    """Returned once upon creation; plaintext token is never retrievable again."""

    id: str
    name: str
    token: str
    scopes: list[str]
    expires_at: datetime | None


class ApiTokenSummary(BaseModel):
    """Token metadata without secret."""

    id: str
    name: str
    scopes: list[str]
    expires_at: datetime | None
    is_revoked: bool
    created_at: datetime


@router.post("", response_model=ApiTokenCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_token(
    payload: ApiTokenCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ApiTokenCreatedResponse:
    """Issue a new scoped API token stored as a SHA-256 hash."""
    cleaned_scopes = [s.strip() for s in payload.scopes if s.strip()]
    if not cleaned_scopes:
        raise AppError(
            code="INVALID_SCOPE",
            message="Minimal satu scope izin harus dipilih.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    for scope in cleaned_scopes:
        if scope not in _VALID_SCOPES or (scope == "*" and not user.is_superadmin):
            raise AppError(
                code="INVALID_SCOPE",
                message=f"Scope token tidak valid: {scope}",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    raw_secret = f"qt_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)

    rec = ApiToken(
        user_id=user.id,
        name=payload.name,
        token_hash=token_hash,
        scopes=",".join(cleaned_scopes),
        expires_at=expires_at,
    )
    db.add(rec)
    await db.flush()
    await record_audit_log(
        db,
        action="api_token.create",
        target_type="api_token",
        target_id=rec.id,
        user_id=user.id,
        ip_address=extract_client_ip(request),
        metadata={"name": payload.name, "scopes": cleaned_scopes},
    )
    await db.commit()
    await db.refresh(rec)

    return ApiTokenCreatedResponse(
        id=rec.id,
        name=rec.name,
        token=raw_secret,
        scopes=cleaned_scopes,
        expires_at=rec.expires_at,
    )


@router.get("", response_model=list[ApiTokenSummary])
async def list_api_tokens(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ApiTokenSummary]:
    """List API tokens owned by the authenticated user."""
    rows = (
        (
            await db.execute(
                select(ApiToken)
                .where(ApiToken.user_id == user.id)
                .order_by(ApiToken.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        ApiTokenSummary(
            id=r.id,
            name=r.name,
            scopes=[s for s in r.scopes.split(",") if s],
            expires_at=r.expires_at,
            is_revoked=r.is_revoked,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke an existing API token and append immutable audit log entry."""
    rec = (
        await db.execute(
            select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == user.id)
        )
    ).scalar_one_or_none()
    if rec is None:
        raise AppError("TOKEN_NOT_FOUND", "Token API tidak ditemukan.", status.HTTP_404_NOT_FOUND)
    rec.is_revoked = True
    await record_audit_log(
        db,
        action="api_token.revoke",
        target_type="api_token",
        target_id=rec.id,
        user_id=user.id,
        ip_address=extract_client_ip(request),
        metadata={"name": rec.name},
    )
    await db.commit()
