"""Authentication for the agent host's HTTP API.

Same model as the Spring services: a trusted caller presents X-Service-Token and says which customer
(X-Customer-Id) and roles (X-Roles) it acts for.
"""
from __future__ import annotations

import hmac
import logging
from typing import NamedTuple

from fastapi import HTTPException, Request

from app.config import Settings
from app.observability import logctx

log = logging.getLogger("agent.security")
DEMO_TOKEN = "local-demo-token"


class Principal(NamedTuple):
    customer: str | None
    roles: frozenset[str]

    def has_role(self, *roles: str) -> bool:
        return bool(self.roles & {r.upper() for r in roles})


def check_startup(settings: Settings) -> None:
    """Warn when running with the demo/weak service token."""
    if settings.mcp_service_token == DEMO_TOKEN or len(settings.mcp_service_token) < 32:
        log.warning("SECURITY: running with the demo/weak service token. Never deploy like this.")


async def authenticate(request: Request) -> Principal:
    """FastAPI dependency: returns who is calling, or raises 401."""
    settings: Settings = request.app.state.settings
    presented = request.headers.get("x-service-token")
    if not presented or not hmac.compare_digest(presented.encode(), settings.mcp_service_token.encode()):
        raise HTTPException(401, {"code": "AUTHENTICATION_REQUIRED", "message": "missing or invalid credentials"})
    roles = frozenset(r.strip().upper() for r in request.headers.get("x-roles", "").split(",") if r.strip())
    who = Principal(request.headers.get("x-customer-id") or None, roles)
    logctx.bind(customer_id=who.customer or "service")
    return who
