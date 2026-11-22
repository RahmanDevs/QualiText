"""Append-only audit logging service (Module G4)."""

from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def extract_client_ip(request: Request | None) -> str | None:
    """Extract originating client IPv4/IPv6 address from FastAPI Request headers or peer."""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first[:64]
    if request.client and request.client.host:
        return str(request.client.host)[:64]
    return None


async def record_audit_log(
    db: AsyncSession,
    *,
    action: str,
    target_type: str,
    target_id: str | None = None,
    user_id: str | None = None,
    workspace_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an immutable audit log entry. Never store raw PII or secrets in `metadata`."""
    entry = AuditLog(
        user_id=user_id,
        workspace_id=workspace_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        metadata_json=metadata or {},
    )
    db.add(entry)
    await db.flush()
    return entry
