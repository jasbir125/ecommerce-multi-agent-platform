"""Layer 3: control which tools an agent may call and with which arguments."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import time

import httpx
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp.shared.exceptions import McpError

from app.observability import logctx, metrics as m
from app.observability.tracing import span

tlog = logging.getLogger("agent.tool")

# LLM agents get READ-ONLY tools only. Write tools are called by deterministic graph nodes.
AGENT_TOOLS: dict[str, set[str]] = {
    "order": {"get_order_status", "get_payment_status"},
    "shipment": {"get_shipment_status"},
    "policy": {"ask_policy", "get_policy_version"},   # eligibility is decided by code, not by a model-chosen tool call
    "product": {"search_products", "check_inventory"},
}
WRITE_TOOLS = {"create_approval_request", "initiate_refund", "reserve_inventory", "release_inventory",
               "create_replacement_order", "create_carrier_case", "notify_customer", "create_support_task"}
# What a tool call may fail with (service refusal, transport problem, bad payload). Anything else is a bug and propagates.
TOOL_FAILURES: tuple[type[BaseException], ...] = (
    ToolException, McpError, httpx.HTTPError, ExceptionGroup, ValueError, RuntimeError, OSError, TimeoutError)
PRODUCT_ID = re.compile(r"^PRD-[A-Z0-9]{2,20}$")


class ToolCallError(Exception):
    """A tool call failed (the message is the service's error code and detail)."""


@dataclass
class ToolPolicy:
    """Per-run, per-agent rules for tool arguments."""
    order_id: str
    max_calls: int = 6
    calls: dict[str, int] = field(default_factory=dict)

    def check(self, agent: str, tool: str, args: dict[str, Any]) -> str | None:
        if tool in WRITE_TOOLS or tool not in AGENT_TOOLS.get(agent, set()):
            return f"TOOL_NOT_ALLOWED: {agent} agent may not call {tool}"
        self.calls[agent] = self.calls.get(agent, 0) + 1
        if self.calls[agent] > self.max_calls:
            return "TOOL_BUDGET_EXCEEDED: too many tool calls; answer with what you have"
        for key in ("order_id", "original_order_id"):
            if key in args and args[key] != self.order_id:
                return f"ORDER_MISMATCH: you may only look at order {self.order_id}"
        if "product_id" in args and not PRODUCT_ID.match(str(args["product_id"])):
            return "INVALID_PRODUCT_ID"
        return None


def guard_tools(tools: dict[str, BaseTool], agent: str, policy: ToolPolicy, log: list[dict]) -> list[BaseTool]:
    """Wrap the allowed tools: check arguments first, turn failures into text the agent can read,
    and record every call (name, args, ok) for the trace."""
    wrapped: list[BaseTool] = []
    for name in sorted(AGENT_TOOLS[agent]):
        base = tools.get(name)
        if base is None:
            continue

        def make(base: BaseTool, name: str):
            async def run(**kwargs: Any) -> str:
                problem = policy.check(agent, name, kwargs)
                tlog.info("TOOL > %s (called by the %s agent) args=%s", name, agent, logctx.clean(kwargs))
                if problem:
                    tlog.warning("TOOL < %s BLOCKED by guardrail: %s", name, problem)
                    log.append({"agent": agent, "tool": name, "args": kwargs, "ok": False, "error": problem})
                    m.guardrail("tool", problem.split(":")[0])
                    m.TOOL_CALLS.labels(agent, name, "blocked").inc()
                    return f"ERROR {problem}"
                started = time.perf_counter()
                try:
                    with span(f"tool {name}", agent=agent):
                        result = await base.ainvoke(kwargs)
                    tlog.info("TOOL < %s ok %.0fms result=%s", name, (time.perf_counter() - started) * 1000, logctx.clean(result))
                    log.append({"agent": agent, "tool": name, "args": kwargs, "ok": True, "result": result})
                    m.TOOL_CALLS.labels(agent, name, "ok").inc()
                    return _as_text(result)
                except TOOL_FAILURES as e:       # the agent sees the failure as text; it must not crash the run
                    tlog.warning("TOOL < %s FAILED %.0fms error=%s", name, (time.perf_counter() - started) * 1000, logctx.clean(str(e), 400))
                    log.append({"agent": agent, "tool": name, "args": kwargs, "ok": False, "error": str(e)})
                    m.TOOL_CALLS.labels(agent, name, "error").inc()
                    return f"ERROR {e}"
                finally:
                    m.TOOL_LATENCY.labels(name).observe(time.perf_counter() - started)
            return StructuredTool.from_function(coroutine=run, name=name, description=base.description,
                                                args_schema=base.args_schema)
        wrapped.append(make(base, name))
    return wrapped


def _as_text(result: Any) -> str:
    if isinstance(result, list):
        return "".join(b.get("text", "") for b in result if isinstance(b, dict))
    return str(result)
