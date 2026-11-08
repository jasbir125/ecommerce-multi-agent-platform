"""Inbound authentication and caller identity for MCP servers.

The agent host connects with a bearer token plus headers saying who the end
customer is. Identity is read from those headers, never from tool arguments,
so a model cannot ask a tool to act as a different customer.
"""
from __future__ import annotations

import hmac
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastmcp.server.dependencies import get_http_headers

H_CUSTOMER = "x-customer-id"
H_WORKFLOW = "x-workflow-id"
H_CORRELATION = "x-correlation-id"
H_ROLES = "x-roles"


class TokenAuthMiddleware:
    """Pure-ASGI middleware: rejects HTTP requests without the right bearer token."""

    def __init__(self, app, token: str, exempt_paths: tuple[str, ...] = ("/health",)):
        self.app = app
        self._token = token.encode()
        self._exempt = exempt_paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self._exempt:
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        presented = headers.get("authorization", "")
        if presented.lower().startswith("bearer "):
            presented = presented[7:]
        if not hmac.compare_digest(presented.encode(), self._token):
            body = json.dumps({"error": "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


@dataclass(frozen=True)
class Identity:
    customer_id: str | None
    workflow_id: str | None
    correlation_id: str | None
    roles: frozenset[str]


def current_identity() -> Identity:
    """Identity of the caller for the request being handled (empty outside HTTP)."""
    h = get_http_headers()
    roles = frozenset(r.strip().upper() for r in h.get(H_ROLES, "").split(",") if r.strip())
    return Identity(h.get(H_CUSTOMER) or None, h.get(H_WORKFLOW) or None, h.get(H_CORRELATION) or None, roles)


class RateLimiter:
    """Sliding-window limiter, per key (here: per customer), in memory."""

    def __init__(self, max_calls: int, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._calls[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_calls:
            return False
        q.append(now)
        return True
