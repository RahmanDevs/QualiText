"""Pydantic v2 schemas for authentication, SSO, and workspaces."""

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Email + password login payload."""

    email: EmailStr
    password: str = Field(min_length=6)


class RefreshRequest(BaseModel):
    """Refresh token payload."""

    refresh_token: str = Field(min_length=10)


class LogoutRequest(BaseModel):
    """Optional logout payload containing the refresh token to revoke."""

    refresh_token: str | None = None


class TokenResponse(BaseModel):
    """JWT access + refresh token pair."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class OidcCallbackRequest(BaseModel):
    """OIDC callback payload carrying authorization code or ID token plus CSRF state."""

    code: str | None = None
    state: str = Field(min_length=10)
    id_token: str | None = None


class SamlAcsRequest(BaseModel):
    """SAML 2.0 Assertion Consumer Service payload."""

    saml_response: str = Field(min_length=8)
    relay_state: str | None = None


class SsoProvidersResponse(BaseModel):
    """Advertised corporate SSO capabilities for login screen."""

    oidc_enabled: bool
    oidc_provider_name: str
    saml_enabled: bool


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
