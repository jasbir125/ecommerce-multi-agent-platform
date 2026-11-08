"""Best-effort audit events for tool calls (auditing never blocks a tool)."""
from __future__ import annotations

import logging
from typing import Any

from .security import current_identity
from .spring import SpringClient

log = logging.getLogger("mcp.audit")


class Auditor:
    def __init__(self, audit_client: SpringClient | None, agent_name: str):
        self._client = audit_client
        self._agent = agent_name

    async def record(self, event_type: str, workflow_id: str | None, payload: dict[str, Any]) -> None:
        ident = current_identity()
        if self._client is None:
            log.info("AUDIT %s workflow=%s", event_type, workflow_id)
            return
        try:
            await self._client.post("/api/audit/events", {
                "workflowId": workflow_id, "agentName": self._agent, "eventType": event_type,
                "actor": ident.customer_id, "correlationId": ident.correlation_id, "payload": payload})
        except Exception as e:  # noqa: BLE001 - auditing must never break a tool call
            log.warning("audit delivery failed for %s: %s", event_type, e)
