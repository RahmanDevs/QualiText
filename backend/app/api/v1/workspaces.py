"""Workspace endpoints (`/api/v1/workspaces`)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_workspace_permission
from app.core.security import Permission
from app.db.models import Membership, User, Workspace
from app.db.session import get_db
from app.schemas.auth import WorkspaceMembershipResponse

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=list[WorkspaceMembershipResponse])
async def list_my_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceMembershipResponse]:
    """List workspaces accessible by the authenticated user."""
    stmt = (
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
        .order_by(Workspace.name)
    )
    rows = (await db.execute(stmt)).all()
    return [
        WorkspaceMembershipResponse(id=ws.id, name=ws.name, slug=ws.slug, role=role)
        for ws, role in rows
    ]


@router.get("/{workspace_id}/ping")
async def workspace_rbac_check(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
) -> dict[str, str]:
    """Verify workspace RBAC permission enforcement."""
    return {"status": "ok", "workspace_id": workspace_id}
