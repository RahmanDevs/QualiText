"""Workspace webhook management endpoints (`/api/v1/workspaces/{id}/webhooks`, Module G5)."""

import secrets
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import require_workspace_permission
from app.core.errors import AppError
from app.core.security import Permission
from app.db.models import User, WebhookDelivery, WebhookSubscription
from app.db.session import get_db
from app.services.audit_service import extract_client_ip, record_audit_log
from app.services.webhook_service import (
    VALID_WEBHOOK_EVENTS,
    dispatch_workspace_webhooks,
    is_safe_webhook_url,
)

router = APIRouter(tags=["webhooks"])


class WebhookCreateRequest(BaseModel):
    """Create a workspace webhook subscription."""

    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=512)
    events: list[str] = Field(
        default_factory=lambda: ["run.completed", "run.failed", "export.completed"]
    )


class WebhookCreatedResponse(BaseModel):
    """Returned once upon webhook creation with plaintext HMAC signing secret."""

    id: str
    workspace_id: str
    name: str
    url: str
    signing_secret: str
    events: list[str]
    is_active: bool
    created_at: datetime


class WebhookSummary(BaseModel):
    """Webhook metadata without signing secret."""

    id: str
    workspace_id: str
    name: str
    url: str
    events: list[str]
    is_active: bool
    created_at: datetime


class WebhookDeliverySummary(BaseModel):
    """Webhook delivery history record."""

    id: str
    created_at: datetime
    event_type: str
    status_code: int | None
    success: bool
    error_message: str | None


@router.get("/workspaces/{workspace_id}/webhooks", response_model=list[WebhookSummary])
async def list_webhooks(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[WebhookSummary]:
    """List configured webhook subscriptions in a workspace."""
    subs = (
        (
            await db.execute(
                select(WebhookSubscription)
                .where(WebhookSubscription.workspace_id == workspace_id)
                .order_by(WebhookSubscription.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        WebhookSummary(
            id=s.id,
            workspace_id=s.workspace_id,
            name=s.name,
            url=s.url,
            events=[e for e in s.events.split(",") if e],
            is_active=s.is_active,
            created_at=s.created_at,
        )
        for s in subs
    ]


@router.post(
    "/workspaces/{workspace_id}/webhooks",
    response_model=WebhookCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_webhook(
    workspace_id: str,
    payload: WebhookCreateRequest,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> WebhookCreatedResponse:
    """Create a signed webhook subscription with anti-SSRF URL validation."""
    settings = get_settings()
    if not is_safe_webhook_url(payload.url, allow_private=settings.webhook_allow_private_targets):
        raise AppError(
            code="UNSAFE_WEBHOOK_URL",
            message=(
                "URL webhook harus memakai http/https publik dan tidak mengarah ke alamat privat."
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    cleaned_events = [e.strip() for e in payload.events if e.strip()]
    if not cleaned_events or any(e not in VALID_WEBHOOK_EVENTS for e in cleaned_events):
        raise AppError(
            code="INVALID_WEBHOOK_EVENTS",
            message="Daftar event webhook tidak valid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    secret = f"whsec_{secrets.token_urlsafe(24)}"
    sub = WebhookSubscription(
        workspace_id=workspace_id,
        name=payload.name,
        url=payload.url.strip(),
        signing_secret=secret,
        events=",".join(cleaned_events),
        is_active=True,
        created_by=user.id,
    )
    db.add(sub)
    await db.flush()
    await record_audit_log(
        db,
        action="webhook.create",
        target_type="webhook_subscription",
        target_id=sub.id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={"name": sub.name, "events": cleaned_events},
    )
    await db.commit()
    await db.refresh(sub)

    return WebhookCreatedResponse(
        id=sub.id,
        workspace_id=sub.workspace_id,
        name=sub.name,
        url=sub.url,
        signing_secret=secret,
        events=cleaned_events,
        is_active=sub.is_active,
        created_at=sub.created_at,
    )


@router.delete(
    "/workspaces/{workspace_id}/webhooks/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_webhook(
    workspace_id: str,
    webhook_id: str,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a webhook subscription from a workspace."""
    sub = (
        await db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == webhook_id,
                WebhookSubscription.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise AppError(
            code="WEBHOOK_NOT_FOUND",
            message="Webhook tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    sub_name = sub.name
    await db.delete(sub)
    await record_audit_log(
        db,
        action="webhook.delete",
        target_type="webhook_subscription",
        target_id=webhook_id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={"name": sub_name},
    )
    await db.commit()


@router.get(
    "/workspaces/{workspace_id}/webhooks/{webhook_id}/deliveries",
    response_model=list[WebhookDeliverySummary],
)
async def list_webhook_deliveries(
    workspace_id: str,
    webhook_id: str,
    _user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[WebhookDeliverySummary]:
    """List recent delivery attempts for a webhook subscription."""
    sub = (
        await db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == webhook_id,
                WebhookSubscription.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise AppError(
            code="WEBHOOK_NOT_FOUND",
            message="Webhook tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    rows = (
        (
            await db.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.subscription_id == webhook_id)
                .order_by(WebhookDelivery.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return [
        WebhookDeliverySummary(
            id=d.id,
            created_at=d.created_at,
            event_type=d.event_type,
            status_code=d.status_code,
            success=d.success,
            error_message=d.error_message,
        )
        for d in rows
    ]


@router.post("/workspaces/{workspace_id}/webhooks/{webhook_id}/test")
async def test_webhook(
    workspace_id: str,
    webhook_id: str,
    _user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Dispatch a signed `webhook.test` event to verify webhook connectivity."""
    sub = (
        await db.execute(
            select(WebhookSubscription).where(
                WebhookSubscription.id == webhook_id,
                WebhookSubscription.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if sub is None:
        raise AppError(
            code="WEBHOOK_NOT_FOUND",
            message="Webhook tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    deliveries = await dispatch_workspace_webhooks(
        db,
        workspace_id=workspace_id,
        event_type="webhook.test",
        payload={"message": "QualiText webhook connectivity test"},
        subscription_id=webhook_id,
    )
    if not deliveries:
        return {"delivered": False, "status_code": None, "error_message": "Webhook nonaktif."}
    last = deliveries[0]
    return {
        "delivered": last.success,
        "status_code": last.status_code,
        "error_message": last.error_message,
    }
