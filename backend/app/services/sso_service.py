"""Enterprise SSO service supporting OIDC Authorization Code / ID Token and SAML 2.0 ACS."""

import base64
import hashlib
import hmac
import html
import json
import secrets
import uuid
import xml.etree.ElementTree as ET
import zlib
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import (
    Role,
    create_token,
    decode_token_payload,
    hash_password,
)
from app.db.models import Membership, User, Workspace


def _resolve_role_hint(claims: dict[str, Any]) -> str:
    valid_roles = {r.value for r in Role}
    raw_role = claims.get("role")
    if isinstance(raw_role, str) and raw_role.lower() in valid_roles:
        return raw_role.lower()
    for list_key in ("roles", "groups"):
        vals = claims.get(list_key)
        if isinstance(vals, list):
            for item in vals:
                if isinstance(item, str) and item.lower() in valid_roles:
                    return item.lower()
    return Role.VIEWER.value


def build_oidc_authorize_url() -> tuple[str, str]:
    """Generate OIDC authorization redirect URL and signed CSRF state JWT."""
    settings = get_settings()
    if not settings.oidc_enabled or not settings.oidc_issuer_url or not settings.oidc_client_id:
        raise AppError(
            code="SSO_NOT_CONFIGURED",
            message="OIDC belum diaktifkan atau konfigurasi issuer/client_id belum lengkap.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    state = create_token(
        subject="oidc",
        token_type="oidc_state",
        expires_delta=timedelta(minutes=10),
        jti=str(uuid.uuid4()),
    )
    query = urlencode(
        {
            "client_id": settings.oidc_client_id,
            "response_type": "code",
            "scope": "openid profile email",
            "redirect_uri": settings.oidc_redirect_uri,
            "state": state,
        }
    )
    base = settings.oidc_issuer_url.rstrip("/")
    return f"{base}/authorize?{query}", state


async def verify_oidc_callback(
    *,
    code: str | None,
    state: str,
    id_token: str | None,
) -> dict[str, Any]:
    """Verify OIDC state CSRF token, exchange code if needed, and validate ID token signature."""
    settings = get_settings()
    if not settings.oidc_enabled:
        raise AppError(
            code="SSO_NOT_CONFIGURED",
            message="OIDC belum diaktifkan pada server ini.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if decode_token_payload(state, expected_type="oidc_state") is None:
        raise AppError(
            code="INVALID_SSO_STATE",
            message="State OIDC tidak valid atau sudah kedaluwarsa.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    raw_id_token = id_token
    if not raw_id_token and code:
        token_endpoint = f"{settings.oidc_issuer_url.rstrip('/')}/token"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": settings.oidc_redirect_uri,
                        "client_id": settings.oidc_client_id,
                        "client_secret": settings.oidc_client_secret,
                    },
                )
                resp.raise_for_status()
                raw_id_token = str(resp.json().get("id_token") or "")
        except Exception as exc:
            raise AppError(
                code="OIDC_TOKEN_EXCHANGE_FAILED",
                message="Gagal menukar kode OIDC dengan provider identitas.",
                status_code=status.HTTP_502_BAD_GATEWAY,
            ) from exc

    if not raw_id_token:
        raise AppError(
            code="MISSING_ID_TOKEN",
            message="ID token OIDC tidak tersedia dalam respons callback.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    decode_kwargs: dict[str, Any] = {}
    if settings.oidc_client_id:
        decode_kwargs["audience"] = settings.oidc_client_id
    else:
        decode_kwargs["options"] = {"verify_aud": False}
    if settings.oidc_issuer_url:
        decode_kwargs["issuer"] = settings.oidc_issuer_url

    def _decode_with_jwks(jwks_dict: dict[str, Any]) -> dict[str, Any]:
        jwks = jwt.PyJWKSet.from_dict(jwks_dict)
        if not jwks.keys:
            raise ValueError("Empty JWKS")
        header = jwt.get_unverified_header(raw_id_token)
        kid = header.get("kid")
        known_kids = {k.key_id for k in jwks.keys if k.key_id}
        if kid is not None:
            if kid not in known_kids:
                raise ValueError("Unknown JWK kid")
            signing_key = jwks[kid]
        else:
            signing_key = jwks.keys[0]
        return jwt.decode(
            raw_id_token,
            signing_key.key,
            algorithms=["RS256", "ES256", "PS256"],
            **decode_kwargs,
        )

    claims: dict[str, Any]
    try:
        if settings.oidc_jwks_json:
            claims = _decode_with_jwks(json.loads(settings.oidc_jwks_json))
        elif settings.oidc_hs256_secret:
            claims = jwt.decode(
                raw_id_token,
                settings.oidc_hs256_secret,
                algorithms=["HS256"],
                **decode_kwargs,
            )
        elif settings.oidc_issuer_url:
            disc_url = f"{settings.oidc_issuer_url.rstrip('/')}/.well-known/openid-configuration"
            async with httpx.AsyncClient(timeout=5.0) as client:
                disc = (await client.get(disc_url)).json()
                jwks_resp = (await client.get(str(disc["jwks_uri"]))).json()
            claims = _decode_with_jwks(jwks_resp)
        else:
            raise ValueError("No OIDC key material configured")
    except Exception as exc:
        raise AppError(
            code="INVALID_ID_TOKEN",
            message="Verifikasi tanda tangan atau klaim OIDC ID token gagal.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if claims.get("email_verified") is False:
        raise AppError(
            code="OIDC_EMAIL_UNVERIFIED",
            message="Alamat email SSO OIDC belum terverifikasi.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    email = str(claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise AppError(
            code="OIDC_EMAIL_MISSING",
            message="Klaim email wajib tersedia pada ID token OIDC.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    full_name = str(claims.get("name") or claims.get("preferred_username") or email.split("@")[0])
    return {
        "email": email,
        "full_name": full_name,
        "role_hint": _resolve_role_hint(claims),
        "provider": "oidc",
    }


def build_saml_sp_metadata() -> str:
    """Return SAML 2.0 Service Provider EntityDescriptor XML metadata."""
    settings = get_settings()
    entity_id = html.escape(settings.saml_entity_id, quote=True)
    acs_url = html.escape(settings.saml_acs_url, quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{entity_id}">\n'
        '  <SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">\n'
        "    <NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</NameIDFormat>\n"
        f'    <AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{acs_url}" index="0" isDefault="true"/>\n'
        "  </SPSSODescriptor>\n"
        "</EntityDescriptor>\n"
    )


def build_saml_login_url() -> tuple[str, str]:
    """Generate SAML AuthnRequest HTTP-Redirect URL and signed RelayState."""
    settings = get_settings()
    if not settings.saml_enabled or not settings.saml_idp_sso_url:
        raise AppError(
            code="SSO_NOT_CONFIGURED",
            message="SAML 2.0 belum diaktifkan atau IdP SSO URL belum dikonfigurasi.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    req_id = f"_qt_{uuid.uuid4().hex}"
    acs_escaped = html.escape(settings.saml_acs_url, quote=True)
    issuer_escaped = html.escape(settings.saml_entity_id)
    authn_xml = (
        '<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="{req_id}" Version="2.0" AssertionConsumerServiceURL="{acs_escaped}">'
        f"<saml:Issuer>{issuer_escaped}</saml:Issuer>"
        "</samlp:AuthnRequest>"
    ).encode()
    deflated = zlib.compress(authn_xml)[2:-4]
    saml_request_b64 = base64.b64encode(deflated).decode("ascii")
    relay_state = create_token(
        subject="saml",
        token_type="saml_state",
        expires_delta=timedelta(minutes=10),
        jti=str(uuid.uuid4()),
    )
    query = urlencode({"SAMLRequest": saml_request_b64, "RelayState": relay_state})
    return f"{settings.saml_idp_sso_url}?{query}", relay_state


def _find_first_text(root: ET.Element, local_name: str) -> str:
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        if tag == local_name and elem.text:
            return elem.text.strip()
    return ""


def verify_saml_response(
    *,
    saml_response_b64: str,
    signature_header: str | None = None,
    relay_state: str | None = None,
) -> dict[str, Any]:
    """Parse and HMAC-verify a SAML 2.0 assertion (with XXE protection)."""
    settings = get_settings()
    if not settings.saml_enabled or not settings.saml_shared_secret:
        raise AppError(
            code="SSO_NOT_CONFIGURED",
            message="SAML 2.0 atau SAML shared secret belum dikonfigurasi.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if (
        relay_state is not None
        and decode_token_payload(relay_state, expected_type="saml_state") is None
    ):
        raise AppError(
            code="INVALID_SSO_STATE",
            message="RelayState SAML tidak valid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        xml_bytes = base64.b64decode(saml_response_b64, validate=True)
    except Exception as exc:
        raise AppError(
            code="INVALID_SAML_XML",
            message="Payload SAMLResponse bukan base64 valid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    upper_xml = xml_bytes.upper()
    if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
        raise AppError(
            code="INVALID_SAML_XML",
            message="XML SAML tidak boleh memuat deklarasi DOCTYPE atau ENTITY.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AppError(
            code="INVALID_SAML_XML",
            message="Dokumen XML SAML tidak valid.",
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    email = _find_first_text(root, "NameID").lower()
    if not email or "@" not in email:
        raise AppError(
            code="SAML_NAMEID_MISSING",
            message="Atribut NameID (email) tidak ditemukan pada assertion SAML.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    secret_bytes = settings.saml_shared_secret.encode("utf-8")
    sig_value = _find_first_text(root, "SignatureValue")
    issue_instant = root.attrib.get("IssueInstant", "")

    expected_b64_sig = hmac.new(
        secret_bytes, saml_response_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    expected_canonical_sig = hmac.new(
        secret_bytes, f"{email}:{issue_instant}".encode(), hashlib.sha256
    ).hexdigest()

    header_valid = bool(signature_header) and hmac.compare_digest(
        signature_header.strip(), expected_b64_sig
    )
    xml_sig_valid = bool(sig_value) and (
        hmac.compare_digest(sig_value.strip(), expected_b64_sig)
        or hmac.compare_digest(sig_value.strip(), expected_canonical_sig)
    )
    if not header_valid and not xml_sig_valid:
        raise AppError(
            code="INVALID_SAML_SIGNATURE",
            message="Tanda tangan assertion SAML tidak valid atau tidak ditemukan.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    now_utc = datetime.now(UTC)
    for elem in root.iter():
        if elem.tag.split("}", 1)[-1] == "Conditions":
            not_on_or_after = elem.attrib.get("NotOnOrAfter")
            if not_on_or_after:
                try:
                    exp_dt = datetime.fromisoformat(not_on_or_after.replace("Z", "+00:00"))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                except ValueError as exc:
                    raise AppError(
                        code="INVALID_SAML_XML",
                        message="Atribut NotOnOrAfter pada assertion SAML tidak valid.",
                        status_code=status.HTTP_400_BAD_REQUEST,
                    ) from exc
                if exp_dt <= now_utc:
                    raise AppError(
                        code="SAML_ASSERTION_EXPIRED",
                        message="Assertion SAML telah kedaluwarsa.",
                        status_code=status.HTTP_401_UNAUTHORIZED,
                    )

    attrs: dict[str, str] = {}
    for elem in root.iter():
        if elem.tag.split("}", 1)[-1] == "Attribute":
            attr_name = elem.attrib.get("Name", "")
            val = _find_first_text(elem, "AttributeValue")
            if attr_name and val:
                attrs[attr_name.lower()] = val

    full_name = attrs.get("full_name") or attrs.get("name") or email.split("@")[0]
    role_hint = _resolve_role_hint({"role": attrs.get("role")})
    return {
        "email": email,
        "full_name": full_name,
        "role_hint": role_hint,
        "provider": "saml",
    }


async def provision_or_get_sso_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    role_hint: str | None = None,
) -> User:
    """Look up existing SSO user or JIT-provision a workspace user with least privilege."""
    norm_email = email.strip().lower()
    user = (await db.execute(select(User).where(User.email == norm_email))).scalar_one_or_none()
    if user is not None:
        if not user.is_active:
            raise AppError(
                code="USER_INACTIVE",
                message="Akun Anda sedang dinonaktifkan. Hubungi Admin.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return user

    user = User(
        email=norm_email,
        full_name=full_name[:200],
        password_hash=hash_password(secrets.token_urlsafe(32)),
        is_active=True,
        is_superadmin=False,
    )
    db.add(user)
    await db.flush()

    settings = get_settings()
    ws = (
        await db.execute(
            select(Workspace).where(Workspace.slug == settings.oidc_default_workspace_slug)
        )
    ).scalar_one_or_none()
    if ws is None:
        ws = (await db.execute(select(Workspace).limit(1))).scalar_one_or_none()

    if ws is not None:
        valid_non_admin = {r.value for r in Role if r != Role.ADMIN}
        assigned_role = (
            role_hint.lower()
            if role_hint and role_hint.lower() in valid_non_admin
            else Role.VIEWER.value
        )
        db.add(Membership(user_id=user.id, workspace_id=ws.id, role=assigned_role))
        await db.flush()

    return user
