"""Webhook subscription validation, HMAC signing, and async delivery service (Module G5)."""

import hashlib
import hmac
import ipaddress
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import WebhookDelivery, WebhookSubscription

logger = get_logger(__name__)
VALID_WEBHOOK_EVENTS = frozenset(
    {"run.completed", "run.failed", "export.completed", "webhook.test", "*"}
)


def is_safe_webhook_url(url: str, *, allow_private: bool = False) -> bool:
    """Validate webhook URL scheme and guard against SSRF targets."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    hostname = parsed.hostname.lower().rstrip(".")
    if allow_private:
        return True

    if hostname == "localhost" or hostname.endswith((".local", ".localhost", ".internal")):
        return False

    try:
        ip_obj = ipaddress.ip_address(hostname)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            return False
    except ValueError:
        # Domain name (not an IP literal)
        pass
    return True


def sign_webhook_payload(secret: str, body: bytes) -> str:
    """Compute SHA-256 HMAC signature header value for webhook delivery verification."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def dispatch_workspace_webhooks(
    db: AsyncSession,
    *,
    workspace_id: str,
    event_type: str,
    payload: dict[str, Any],
    subscription_id: str | None = None,
) -> list[WebhookDelivery]:
    """Send signed HTTP POST payloads to matching active workspace webhook subscriptions."""
    stmt = select(WebhookSubscription).where(
        WebhookSubscription.workspace_id == workspace_id,
        WebhookSubscription.is_active.is_(True),
    )
    if subscription_id is not None:
        stmt = stmt.where(WebhookSubscription.id == subscription_id)

    subs = (await db.execute(stmt)).scalars().all()
    if not subs:
        return []

    settings = get_settings()
    deliveries: list[WebhookDelivery] = []

    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        for sub in subs:
            subscribed_events = {e.strip() for e in sub.events.split(",") if e.strip()}
            if (
                event_type != "webhook.test"
                and event_type not in subscribed_events
                and "*" not in subscribed_events
            ):
                continue

            if not is_safe_webhook_url(
                sub.url, allow_private=settings.webhook_allow_private_targets
            ):
                delivery = WebhookDelivery(
                    subscription_id=sub.id,
                    event_type=event_type,
                    payload_json=payload,
                    status_code=None,
                    success=False,
                    error_message="URL tujuan ditolak oleh kebijakan anti-SSRF.",
                )
                db.add(delivery)
                deliveries.append(delivery)
                continue

            delivery_id = str(uuid.uuid4())
            envelope = {
                "delivery_id": delivery_id,
                "event": event_type,
                "workspace_id": workspace_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": payload,
            }
            body_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            signature = sign_webhook_payload(sub.signing_secret, body_bytes)
            headers = {
                "Content-Type": "application/json",
                "X-QualiText-Event": event_type,
                "X-QualiText-Delivery": delivery_id,
                "X-QualiText-Signature": signature,
            }

            status_code: int | None = None
            success = False
            error_message: str | None = None
            try:
                resp = await client.post(sub.url, content=body_bytes, headers=headers)
                status_code = resp.status_code
                success = 200 <= resp.status_code < 300
                if not success:
                    error_message = f"HTTP {resp.status_code}"
            except Exception as exc:
                error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
                logger.warning(
                    "webhook_delivery_failed",
                    subscription_id=sub.id,
                    event_type=event_type,
                    error_type=type(exc).__name__,
                )

            delivery = WebhookDelivery(
                subscription_id=sub.id,
                event_type=event_type,
                payload_json=envelope,
                status_code=status_code,
                success=success,
                error_message=error_message[:500] if error_message else None,
            )
            db.add(delivery)
            deliveries.append(delivery)

    if deliveries:
        await db.commit()
    return deliveries
