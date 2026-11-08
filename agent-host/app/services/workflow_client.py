"""Client for workflow-service and audit-service (the system of record for workflow status)."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import Settings
from app.observability import logctx

log = logging.getLogger("agent.workflow")


async def _log_request(request: httpx.Request) -> None:
    request.extensions["t0"] = time.perf_counter()


async def _log_response(response: httpx.Response) -> None:
    req = response.request
    await response.aread()
    ms = (time.perf_counter() - req.extensions.get("t0", time.perf_counter())) * 1000
    (log.warning if response.status_code >= 400 else log.info)(
        "=> %s %s %s %.0fms req=%s resp=%s", req.method, req.url.path, response.status_code, ms, logctx.clean(req.content), logctx.clean(response.content, 600))


class WorkflowClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._s = settings
        hooks = {"request": [_log_request], "response": [_log_response]}
        self._http = httpx.AsyncClient(base_url=settings.workflow_service_url, timeout=10, transport=transport, event_hooks=hooks)
        self._audit = httpx.AsyncClient(base_url=settings.audit_service_url, timeout=5, transport=transport, event_hooks=hooks)

    def _headers(self, customer_id: str | None, roles: str = "AGENT") -> dict[str, str]:
        h = {"X-Service-Token": self._s.mcp_service_token, "X-Roles": roles}
        if logctx.correlation_id.get():
            h["X-Correlation-Id"] = logctx.correlation_id.get()
        if logctx.workflow_id.get():
            h["X-Workflow-Id"] = logctx.workflow_id.get()
        if customer_id:
            h["X-Customer-Id"] = customer_id
        return h

    async def create(self, customer_id: str, order_id: str, message: str, request_id: str | None = None) -> dict[str, Any]:
        r = await self._http.post("/api/workflows", headers=self._headers(customer_id),
                                  json={"orderId": order_id, "message": message, "requestId": request_id})
        r.raise_for_status()
        return r.json()

    async def get(self, workflow_id: str, customer_id: str | None = None) -> dict[str, Any]:
        r = await self._http.get(f"/api/workflows/{workflow_id}", headers=self._headers(customer_id))
        r.raise_for_status()
        return r.json()

    async def transition(self, workflow_id: str, customer_id: str, key: str, target: str,
                         reason: str = "", patch: dict | None = None) -> bool:
        """Move the workflow to `target`. Repeating a key is a no-op. A refused move is logged, not raised:
        another component (e.g. the approval listener) may already have moved it."""
        r = await self._http.post(f"/api/workflows/{workflow_id}/resume", headers=self._headers(customer_id),
                                  json={"idempotencyKey": key, "targetStatus": target, "reason": reason, "statePatch": patch or {}})
        if r.status_code >= 400:
            log.warning("workflow %s -> %s refused: %s %s", workflow_id, target, r.status_code, r.text[:150])
            return False
        return True

    async def audit(self, workflow_id: str, agent: str, event_type: str, payload: dict, customer_id: str | None = None) -> None:
        try:
            await self._audit.post("/api/audit/events", headers=self._headers(customer_id), json={
                "workflowId": workflow_id, "agentName": agent, "eventType": event_type,
                "actor": "agent-host", "payload": payload})
        except httpx.HTTPError as e:
            log.warning("audit failed for %s: %s", event_type, e)

    async def aclose(self) -> None:
        await self._http.aclose()
        await self._audit.aclose()
