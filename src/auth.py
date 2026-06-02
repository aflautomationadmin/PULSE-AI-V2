"""
Azure AD JWT validation for FastAPI.

Strategy: ID-token-as-Bearer
─────────────────────────────
The frontend sends the MSAL **ID token** (not the Graph access token) as the
Bearer value.  ID tokens are signed RS256 by Microsoft and always carry:
  • aud  = the registered client_id  ← trivial to validate
  • iss  = https://login.microsoftonline.com/{tenant_id}/v2.0
  • preferred_username  = UPN / email
  • exp  / nbf / iat

This requires zero extra Azure portal work (no "Expose an API" needed) and the
backend can simply validate the token against Microsoft's public JWKS keys.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from src.config import get_settings

# FastAPI security scheme — tells Swagger to send Authorization: Bearer <token>
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=True)

# ── JWKS in-memory cache (refreshed every hour) ───────────────────────────────
_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600  # seconds


def _get_jwks(tenant_id: str) -> dict[str, Any]:
    """Fetch (or return cached) Microsoft's public key set for token validation."""
    global _jwks_cache, _jwks_fetched_at
    if _jwks_cache and (time.time() - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks_cache
    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    _jwks_cache = resp.json()
    _jwks_fetched_at = time.time()
    return _jwks_cache


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    """
    FastAPI dependency — validates the Bearer token and returns the user's email.

    Raises HTTP 401 if the token is missing, expired, or has an invalid signature.
    """
    settings = get_settings()

    # If auth is not configured, allow through (dev / no-auth mode)
    if not settings.azure_tenant_id or not settings.azure_client_id:
        return settings.mongo_user_id  # returns "anonymous" or whatever is set

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        jwks = _get_jwks(settings.azure_tenant_id)
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=settings.azure_client_id,
            issuer=f"https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0",
        )
        # preferred_username is the UPN/email on all AAD accounts
        email: str = (
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("upn")
            or ""
        )
        if not email:
            raise credentials_exception
        return email.lower().strip()
    except JWTError as exc:
        print("[AUTH] JWT validation failed:", exc)
        raise credentials_exception
    except Exception:
        raise credentials_exception
