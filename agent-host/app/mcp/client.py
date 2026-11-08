"""Connection to the two MCP servers on behalf of one customer and workflow.

Identity travels in headers (never in tool arguments): the MCP servers read the customer from
X-Customer-Id and refuse write tools for any other workflow than X-Workflow-Id.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.config import Settings
from app.guardrails.tool_guardrails import ToolCallError
from app.mcp.tool_loader import parse_tool_result
from app.observability import metrics as m
from app.observability import logctx
from app.observability.tracing import span


log = logging.getLogger("agent.tool")


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
        self.call_log: list[dict[str, Any]] = []      # every call made by graph steps (node, tool, ok), for the run trace

    async def tools(self) -> dict[str, BaseTool]:
        if self._tools is None:
            self._tools = {t.name: t for t in await self._client.get_tools()}
        return self._tools

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Deterministic tool call (used by graph nodes). Raises ToolCallError with the service's reason."""
        tool = (await self.tools()).get(name)
        if tool is None:
            raise ToolCallError(f"UNKNOWN_TOOL: {name}")
        started = time.perf_counter()
        node = logctx.CURRENT_NODE.get() or "-"
        log.info("TOOL > %s (by graph step %s) args=%s", name, node, logctx.clean(args))
        for attempt in range(1, self.RATE_LIMIT_ATTEMPTS + 1):
            try:
                with span(f"tool {name}", agent="code"):
                    out = parse_tool_result(await tool.ainvoke(args))
                log.info("TOOL < %s ok %.0fms result=%s", name, (time.perf_counter() - started) * 1000, logctx.clean(out))
                self.call_log.append({"node": logctx.CURRENT_NODE.get(), "tool": name, "ok": True, "error": None})
                m.TOOL_CALLS.labels("code", name, "ok").inc()
                m.TOOL_LATENCY.labels(name).observe(time.perf_counter() - started)
                return out
            except Exception as e:  # noqa: BLE001
                # Only a rate limit is retried here: it is transient and the call never reached a service.
                if "RATE_LIMITED" in str(e) and attempt < self.RATE_LIMIT_ATTEMPTS:
                    m.RETRIES.labels("rate_limit").inc()
                    await asyncio.sleep(self.RATE_LIMIT_WAIT * attempt)
                    continue
                log.warning("TOOL < %s FAILED %.0fms error=%s", name, (time.perf_counter() - started) * 1000, logctx.clean(str(e), 400))
                self.call_log.append({"node": logctx.CURRENT_NODE.get(), "tool": name, "ok": False, "error": str(e)[:160]})
                m.TOOL_CALLS.labels("code", name, "error").inc()
                code = str(e).split(":")[0].strip()
                if code.isupper() and len(code) > 3:                    # a domain rule or guardrail said no
                    m.guardrail("business", code)
                m.TOOL_LATENCY.labels(name).observe(time.perf_counter() - started)
                raise ToolCallError(str(e)) from e
