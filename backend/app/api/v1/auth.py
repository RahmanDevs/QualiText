"""Authentication and Enterprise SSO endpoints (`/api/v1/auth`)."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Body, Depends, Header, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_current_user
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.rate_limit import login_rate_limiter
from app.core.security import (
    create_access_token,
    create_refresh_token_with_jti,
    decode_token,
    decode_token_payload,
    verify_password,
)
from app.db.models import Membership, RefreshTokenSession, User, Workspace
from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    OidcCallbackRequest,
    RefreshRequest,
    SamlAcsRequest,
    SsoProvidersResponse,
    TokenResponse,
    UserMeResponse,
    WorkspaceMembershipResponse,
)
from app.services import sso_service
from app.services.audit_service import extract_client_ip, record_audit_log

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)
_optional_bearer = HTTPBearer(auto_error=False)


async def _issue_session_tokens(
    db: AsyncSession,
    *,
    user_id: str,
    family_id: str | None = None,
) -> TokenResponse:
    refresh_jwt, jti, expires_at = create_refresh_token_with_jti(user_id)
    fam = family_id or str(uuid.uuid4())
    db.add(
        RefreshTokenSession(
            user_id=user_id,
            token_jti=jti,
            family_id=fam,
            expires_at=expires_at,
            is_revoked=False,
        )
    )
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=refresh_jwt,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Authenticate user with rate-limiting protection and create tracked refresh session."""
    settings = get_settings()
    client_ip = extract_client_ip(request) or "unknown"
    norm_email = payload.email.lower()

    blocked, retry_after = login_rate_limiter.is_blocked(
        ip=client_ip,
        email=norm_email,
        max_attempts=settings.login_rate_limit_max_attempts,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    if blocked:
        raise AppError(
            code="RATE_LIMIT_EXCEEDED",
            message="Terlalu banyak percobaan login gagal. Coba lagi beberapa saat lagi.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"retry_after_seconds": retry_after},
        )

    result = await db.execute(select(User).where(User.email == norm_email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        login_rate_limiter.record_failure(
            ip=client_ip,
            email=norm_email,
            window_seconds=settings.login_rate_limit_window_seconds,
        )
        logger.warning("auth_login_failed", email_domain=norm_email.split("@")[-1])
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

    login_rate_limiter.clear_failures(ip=client_ip, email=norm_email)
    tokens = await _issue_session_tokens(db, user_id=user.id)
    await record_audit_log(
        db,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        ip_address=client_ip,
    )
    await db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Rotate refresh token with reuse-detection family revocation (SEC-05)."""
    claims = decode_token_payload(payload.refresh_token, expected_type="refresh")
    if claims is None or not claims.get("jti"):
        raise AppError(
            code="INVALID_REFRESH_TOKEN",
            message="Token penyegaran tidak valid atau telah kedaluwarsa.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_id = str(claims["sub"])
    jti = str(claims["jti"])
    session = (
        await db.execute(select(RefreshTokenSession).where(RefreshTokenSession.token_jti == jti))
    ).scalar_one_or_none()
    if session is None or session.user_id != user_id:
        raise AppError(
            code="INVALID_REFRESH_TOKEN",
            message="Sesi penyegaran tidak ditemukan atau telah kedaluwarsa.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if session.is_revoked:
        await db.execute(
            update(RefreshTokenSession)
            .where(RefreshTokenSession.family_id == session.family_id)
            .values(is_revoked=True)
        )
        await record_audit_log(
            db,
            action="auth.refresh_reuse_detected",
            target_type="user",
            target_id=session.user_id,
            user_id=session.user_id,
            ip_address=extract_client_ip(request),
            metadata={"family_id": session.family_id},
        )
        await db.commit()
        raise AppError(
            code="REFRESH_TOKEN_REVOKED",
            message="Token penyegaran telah dicabut. Silakan masuk ulang.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    expires_at = (
        session.expires_at
        if session.expires_at.tzinfo is not None
        else session.expires_at.replace(tzinfo=UTC)
    )
    if expires_at <= datetime.now(UTC):
        session.is_revoked = True
        await db.commit()
        raise AppError(
            code="INVALID_REFRESH_TOKEN",
            message="Token penyegaran telah kedaluwarsa.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise AppError(
            code="USER_INACTIVE",
            message="Akun tidak ditemukan atau tidak aktif.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    new_tokens = await _issue_session_tokens(db, user_id=user.id, family_id=session.family_id)
    new_claims = decode_token_payload(new_tokens.refresh_token, expected_type="refresh")
    session.is_revoked = True
    session.replaced_by_jti = (
        str(new_claims["jti"]) if new_claims and new_claims.get("jti") else None
    )
    await db.commit()
    return new_tokens


@router.post("/logout")
async def logout(
    request: Request,
    payload: LogoutRequest | None = Body(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Revoke active refresh token family and log out cleanly."""
    revoked_user_id: str | None = None
    revoked_via_refresh = False

    if payload and payload.refresh_token:
        claims = decode_token_payload(payload.refresh_token, expected_type="refresh")
        if claims and claims.get("jti"):
            sess = (
                await db.execute(
                    select(RefreshTokenSession).where(
                        RefreshTokenSession.token_jti == str(claims["jti"])
                    )
                )
            ).scalar_one_or_none()
            if sess is not None:
                revoked_user_id = sess.user_id
                revoked_via_refresh = True
                await db.execute(
                    update(RefreshTokenSession)
                    .where(RefreshTokenSession.family_id == sess.family_id)
                    .values(is_revoked=True)
                )

    if credentials and credentials.credentials and not credentials.credentials.startswith("qt_"):
        uid = decode_token(credentials.credentials, expected_type="access")
        if uid:
            revoked_user_id = revoked_user_id or uid
            if not revoked_via_refresh:
                await db.execute(
                    update(RefreshTokenSession)
                    .where(RefreshTokenSession.user_id == uid)
                    .values(is_revoked=True)
                )

    if revoked_user_id:
        await record_audit_log(
            db,
            action="auth.logout",
            target_type="user",
            target_id=revoked_user_id,
            user_id=revoked_user_id,
            ip_address=extract_client_ip(request),
        )
        await db.commit()

    return {"status": "logged_out"}


@router.get("/sso/providers", response_model=SsoProvidersResponse)
async def get_sso_providers() -> SsoProvidersResponse:
    """Return active OIDC / SAML SSO provider flags for login screen."""
    settings = get_settings()
    return SsoProvidersResponse(
        oidc_enabled=settings.oidc_enabled,
        oidc_provider_name=settings.oidc_provider_name,
        saml_enabled=settings.saml_enabled,
    )


@router.get("/oidc/login")
async def oidc_login() -> dict[str, str]:
    """Generate OIDC authorization redirect URL and signed state."""
    url, state = sso_service.build_oidc_authorize_url()
    return {"authorization_url": url, "state": state}


@router.post("/oidc/callback", response_model=TokenResponse)
async def oidc_callback(
    payload: OidcCallbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Complete OIDC callback, JIT-provision user if needed, and issue QualiText tokens."""
    identity = await sso_service.verify_oidc_callback(
        code=payload.code,
        state=payload.state,
        id_token=payload.id_token,
    )
    user = await sso_service.provision_or_get_sso_user(
        db,
        email=identity["email"],
        full_name=identity["full_name"],
        role_hint=identity["role_hint"],
    )
    tokens = await _issue_session_tokens(db, user_id=user.id)
    await record_audit_log(
        db,
        action="auth.sso_login",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        ip_address=extract_client_ip(request),
        metadata={"provider": "oidc"},
    )
    await db.commit()
    return tokens


@router.get("/saml/metadata")
async def saml_metadata() -> Response:
    """Serve SAML 2.0 Service Provider XML metadata."""
    xml_doc = sso_service.build_saml_sp_metadata()
    return Response(content=xml_doc, media_type="application/samlmetadata+xml")


@router.get("/saml/login")
async def saml_login() -> dict[str, str]:
    """Generate SAML 2.0 AuthnRequest redirect URL and signed RelayState."""
    url, relay_state = sso_service.build_saml_login_url()
    return {"redirect_url": url, "relay_state": relay_state}


@router.post("/saml/acs", response_model=TokenResponse)
async def saml_acs(
    payload: SamlAcsRequest,
    request: Request,
    x_saml_signature: str | None = Header(default=None, alias="X-SAML-Signature"),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Validate signed SAML 2.0 assertion, provision user, and issue QualiText tokens."""
    identity = sso_service.verify_saml_response(
        saml_response_b64=payload.saml_response,
        signature_header=x_saml_signature,
        relay_state=payload.relay_state,
    )
    user = await sso_service.provision_or_get_sso_user(
        db,
        email=identity["email"],
        full_name=identity["full_name"],
        role_hint=identity["role_hint"],
    )
    tokens = await _issue_session_tokens(db, user_id=user.id)
    await record_audit_log(
        db,
        action="auth.sso_login",
        target_type="user",
        target_id=user.id,
        user_id=user.id,
        ip_address=extract_client_ip(request),
        metadata={"provider": "saml"},
    )
    await db.commit()
    return tokens


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
