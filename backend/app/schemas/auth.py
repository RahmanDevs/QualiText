"""Pydantic v2 schemas for authentication and workspaces."""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Email + password login payload."""

    email: EmailStr
    password: str = Field(min_length=6)


class RefreshRequest(BaseModel):
    """Refresh token payload."""

    refresh_token: str = Field(min_length=10)


class TokenResponse(BaseModel):
    """JWT access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class WorkspaceMembershipResponse(BaseModel):
    """Workspace summary with the current user's role."""

    id: str
    name: str
    slug: str
    role: str


class UserMeResponse(BaseModel):
    """Current authenticated user profile and workspace memberships."""

    id: str
    email: EmailStr
    full_name: str
    is_superadmin: bool
    workspaces: list[WorkspaceMembershipResponse]
