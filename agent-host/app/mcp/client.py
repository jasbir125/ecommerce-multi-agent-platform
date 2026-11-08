"""Connection to the two MCP servers on behalf of one customer and workflow.

Identity travels in headers (never in tool arguments): the MCP servers read the customer from
X-Customer-Id and refuse write tools for any other workflow than X-Workflow-Id.
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import Settings
from app.guardrails.tool_guardrails import ToolCallError
from app.mcp.tool_loader import parse_tool_result


class McpGateway:
    RATE_LIMIT_ATTEMPTS = 3
    RATE_LIMIT_WAIT = 3.0       # seconds, multiplied by the attempt number

    def __init__(self, settings: Settings, customer_id: str, workflow_id: str, correlation_id: str | None = None):
        headers = {"Authorization": f"Bearer {settings.mcp_service_token}", "X-Customer-Id": customer_id,
                   "X-Workflow-Id": workflow_id, "X-Correlation-Id": correlation_id or workflow_id}
        self._client = MultiServerMCPClient({
            "ecommerce": {"transport": "streamable_http", "url": settings.ecommerce_mcp_url, "headers": headers},
            "policy": {"transport": "streamable_http", "url": settings.policy_mcp_url, "headers": headers},
        }, handle_tool_errors=False)
        self._tools: dict[str, BaseTool] | None = None

    async def tools(self) -> dict[str, BaseTool]:
        if self._tools is None:
            self._tools = {t.name: t for t in await self._client.get_tools()}
        return self._tools

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Deterministic tool call (used by graph nodes). Raises ToolCallError with the service's reason."""
        tool = (await self.tools()).get(name)
        if tool is None:
            raise ToolCallError(f"UNKNOWN_TOOL: {name}")
        for attempt in range(1, self.RATE_LIMIT_ATTEMPTS + 1):
            try:
                return parse_tool_result(await tool.ainvoke(args))
            except Exception as e:  # noqa: BLE001
                # Only a rate limit is retried here: it is transient and the call never reached a service.
                if "RATE_LIMITED" in str(e) and attempt < self.RATE_LIMIT_ATTEMPTS:
                    await asyncio.sleep(self.RATE_LIMIT_WAIT * attempt)
                    continue
                raise ToolCallError(str(e)) from e
