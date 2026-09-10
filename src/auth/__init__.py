"""Supabase JWT authentication helpers (JWKS-first)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from src.config import get_env
from src.db import get_repo

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# Used only when AUTH_TEST_MODE=true (unit tests). Never a production secret.
_TEST_JWT_SECRET = "test-jwt-secret-do-not-use-in-prod"
_JWKS_ALGORITHMS = ("RS256", "ES256", "ES384", "EdDSA")


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None = None


@dataclass(frozen=True)
class WorkspaceContext:
    user: AuthUser
    workspace_id: str
    role: str


def auth_test_mode() -> bool:
    return get_env("AUTH_TEST_MODE", "").lower() in {"1", "true", "yes"}


def supabase_url() -> str:
    return get_env("SUPABASE_URL").rstrip("/")


def supabase_issuer() -> str:
    base = supabase_url()
    if not base:
        return ""
    return f"{base}/auth/v1"


def auth_configured() -> bool:
    """Backend can verify tokens via JWKS (SUPABASE_URL) or AUTH_TEST_MODE."""
    if auth_test_mode():
        return True
    return bool(supabase_url())


def is_production() -> bool:
    return get_env("ENVIRONMENT", get_env("ENV", "")).lower() in {
        "production",
        "prod",
    } or get_env("RENDER", "").lower() in {"true", "1"}


def allow_legacy_data_import() -> bool:
    return get_env("ALLOW_LEGACY_DATA_IMPORT", "").lower() in {"1", "true", "yes"}


@lru_cache(maxsize=4)
def _jwks_client_for(url: str) -> PyJWKClient:
    jwks_url = f"{url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    # cache_keys=True is default in recent PyJWT; lifespan keeps keys fresh enough
    return PyJWKClient(jwks_url, cache_keys=True)


def _user_from_payload(payload: dict[str, Any]) -> AuthUser:
    user_id = str(payload.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")
    email = payload.get("email")
    return AuthUser(id=user_id, email=str(email) if email else None)


def _verify_with_jwks(token: str) -> AuthUser:
    base = supabase_url()
    if not base:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured (missing SUPABASE_URL)",
        )
    try:
        client = _jwks_client_for(base)
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(_JWKS_ALGORITHMS),
            audience="authenticated",
            issuer=supabase_issuer(),
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    except Exception as exc:
        # Network / JWKS fetch failures should not look like a valid-but-unauthorized token
        logger.warning("JWKS verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
        ) from exc
    return _user_from_payload(payload)


def _verify_legacy_hs256(token: str) -> AuthUser | None:
    """
    Optional fallback for older Supabase projects that still issue HS256 JWTs
    signed with the project JWT secret.

    Prefer JWKS for new projects. Only used when SUPABASE_JWT_SECRET is set and
    the token header declares alg=HS256.
    """
    secret = get_env("SUPABASE_JWT_SECRET")
    if not secret:
        return None
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        return None
    if str(header.get("alg") or "").upper() != "HS256":
        return None
    try:
        kwargs: dict[str, Any] = {
            "algorithms": ["HS256"],
            "audience": "authenticated",
            "options": {"require": ["exp", "sub", "aud"]},
        }
        issuer = supabase_issuer()
        if issuer:
            kwargs["issuer"] = issuer
        payload = jwt.decode(token, secret, **kwargs)
    except jwt.PyJWTError:
        return None
    return _user_from_payload(payload)


def _verify_test_hs256(token: str) -> AuthUser:
    try:
        payload = jwt.decode(
            token,
            _TEST_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc
    return _user_from_payload(payload)


def verify_supabase_jwt(token: str) -> AuthUser:
    """
    Validate a Supabase user access token.

    Preferred (new projects): asymmetric signature via JWKS at
    {SUPABASE_URL}/auth/v1/.well-known/jwks.json

    AUTH_TEST_MODE: local HS256 test tokens (no network).

    Optional legacy: HS256 + SUPABASE_JWT_SECRET when token alg is HS256
    (older Supabase projects only).
    """
    if auth_test_mode():
        return _verify_test_hs256(token)

    # Prefer JWKS; if that fails and a legacy JWT secret exists for HS256, try it.
    try:
        return _verify_with_jwks(token)
    except HTTPException as jwks_exc:
        legacy = _verify_legacy_hs256(token)
        if legacy is not None:
            return legacy
        raise jwks_exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Authentication required")
    return verify_supabase_jwt(credentials.credentials)


def _workspace_id_from_request(request: Request) -> str | None:
    header = request.headers.get("X-Workspace-Id") or request.headers.get("x-workspace-id")
    if header and header.strip():
        return header.strip()
    return None


async def get_workspace_context(
    request: Request,
    user: AuthUser = Depends(get_current_user),
) -> WorkspaceContext:
    """Resolve the active workspace for the authenticated user."""
    repo = get_repo()
    memberships = repo.list_memberships(user.id)
    if not memberships:
        # Auto-bootstrap first workspace for brand-new users
        name = (user.email or "My workspace").split("@")[0] or "My workspace"
        created = repo.create_workspace_with_owner(user.id, f"{name}'s workspace")
        memberships = [
            {
                **created["membership"],
                "workspace_name": created["workspace"]["name"],
            }
        ]

    requested = _workspace_id_from_request(request)
    if requested:
        membership = repo.get_membership(requested, user.id)
        if not membership:
            raise HTTPException(status_code=403, detail="Not a member of this workspace")
        return WorkspaceContext(
            user=user,
            workspace_id=requested,
            role=str(membership["role"]),
        )

    primary = memberships[0]
    return WorkspaceContext(
        user=user,
        workspace_id=str(primary["workspace_id"]),
        role=str(primary["role"]),
    )


def issue_test_token(user_id: str, email: str = "test@example.com") -> str:
    """HS256 token for unit tests (AUTH_TEST_MODE only)."""
    import time

    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "aud": "authenticated",
            "role": "authenticated",
            "iss": supabase_issuer() or "https://example.supabase.co/auth/v1",
            "iat": now,
            "exp": now + 60 * 60,
        },
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )


def clear_jwks_cache() -> None:
    """Tests / key rotation helper."""
    _jwks_client_for.cache_clear()
