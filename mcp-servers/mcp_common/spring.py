"""Async HTTP client for the Spring Boot domain services.

Every call carries the platform service token plus the end customer's identity
and correlation id. Reads are retried a couple of times on transient failures;
writes are never retried automatically (they rely on idempotency keys instead).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from .security import current_identity

log = logging.getLogger("mcp.spring")


class SpringClient:
    def __init__(self, base_url: str, token: str, timeout: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=httpx.Timeout(timeout, connect=2.0), transport=transport)
        self._token = token

    def _headers(self) -> dict[str, str]:
        ident = current_identity()
        h = {"X-Service-Token": self._token}
        if ident.customer_id:
            h["X-Customer-Id"] = ident.customer_id
        if ident.correlation_id:
            h["X-Correlation-Id"] = ident.correlation_id
        if ident.roles:
            h["X-Roles"] = ",".join(sorted(ident.roles))
        return h

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = await self._client.get(path, params=params, headers=self._headers())
                if r.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(0.2 * 2 ** attempt)
                    continue
                return self._unwrap(r)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                last = e
                await asyncio.sleep(0.2 * 2 ** attempt)
        log.warning("GET %s failed after retries: %s", path, last)
        raise ToolError("UPSTREAM_UNAVAILABLE: the service did not respond. Do not guess the result; "
                        "tell the user it could not be verified and escalate to support.")

    async def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            r = await self._client.post(path, json=json or {}, headers=self._headers())
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            log.warning("POST %s failed: %s", path, e)
            raise ToolError("UPSTREAM_UNAVAILABLE: the action may not have completed. Do not claim success; "
                            "verify with a read tool or escalate to support.") from e
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: httpx.Response) -> Any:
        if r.status_code < 400:
            return r.json() if r.content else {}
        try:
            body = r.json()
            code, detail = body.get("code", f"HTTP_{r.status_code}"), body.get("detail", "")
        except ValueError:
            code, detail = f"HTTP_{r.status_code}", ""
        raise ToolError(f"{code}: {detail}".strip(": "))

    async def aclose(self) -> None:
        await self._client.aclose()
