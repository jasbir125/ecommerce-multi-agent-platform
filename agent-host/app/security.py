"""Authentication for the agent host's HTTP API.

Two kinds of caller, same as the Spring services:
- an end user with "Authorization: Bearer <JWT>": identity and roles come only from the verified token, never from
  X-Customer-Id / X-Roles headers (so a user cannot claim to be someone else);
- a trusted platform component with X-Service-Token, which supplies the identity headers on a user's behalf.
"""
from __future__ import annotations

import hmac
import logging
from typing import NamedTuple

import jwt
from fastapi import HTTPException, Request
from jwt import PyJWKClient

from app.config import Settings

log = logging.getLogger("agent.security")
DEMO_TOKEN = "local-demo-token"


class Principal(NamedTuple):
    customer: str | None
    roles: frozenset[str]
    via_jwt: bool = False
    subject: str | None = None

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles & {r.upper() for r in roles})


class JwtVerifier:
    """Verifies RS256 access tokens against the issuer's JWKS (cached; refreshed when an unknown key id appears)."""

    def __init__(self, jwks_url: str, issuer: str, audience: str, client: PyJWKClient | None = None):
        self.issuer, self.audience = issuer, audience
        self._keys = client or PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=3)

    def verify(self, token: str) -> Principal:
        key = self._keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                            options={"require": ["exp", "iat", "sub"]})
        roles = frozenset(str(r).upper() for r in claims.get("roles", []))
        return Principal(claims.get("customer_id"), roles, True, claims.get("sub"))


def check_startup(settings: Settings) -> None:
    """Production mode refuses to start with the demo/weak service token or without user-token verification."""
    weak = settings.mcp_service_token == DEMO_TOKEN or len(settings.mcp_service_token) < 32
    if settings.platform_env.lower() == "production":
        if weak:
            raise RuntimeError("platform_env=production but the service token is the demo value or shorter than 32 characters")
        if not settings.auth_jwks_url:
            raise RuntimeError("platform_env=production requires AUTH_JWKS_URL (end-user token verification)")
    elif weak:
        log.warning("SECURITY: running with the demo/weak service token (platform_env=%s). Never deploy like this.", settings.platform_env)


def authenticate(request: Request) -> Principal:
    """FastAPI dependency: returns who is calling, or raises 401."""
    settings: Settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        verifier: JwtVerifier | None = getattr(request.app.state, "jwt", None)
        if verifier is None:
            raise HTTPException(401, {"code": "INVALID_TOKEN", "message": "bearer tokens are not accepted by this deployment"})
        try:
            return verifier.verify(auth[7:].strip())
        except Exception as e:  # noqa: BLE001 - expired, forged, wrong audience, unknown key...
            log.warning("rejected bearer token: %s", e)
            raise HTTPException(401, {"code": "INVALID_TOKEN", "message": "invalid or expired token"}, headers={"WWW-Authenticate": "Bearer"})
    presented = request.headers.get("x-service-token")
    if presented and hmac.compare_digest(presented.encode(), settings.mcp_service_token.encode()):
        roles = frozenset(r.strip().upper() for r in request.headers.get("x-roles", "").split(",") if r.strip())
        return Principal(request.headers.get("x-customer-id") or None, roles, False)
    raise HTTPException(401, {"code": "AUTHENTICATION_REQUIRED", "message": "missing or invalid credentials"}, headers={"WWW-Authenticate": "Bearer"})
