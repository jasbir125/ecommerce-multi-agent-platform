"""Layer 3: control which tools an agent may call and with which arguments."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

# LLM agents get READ-ONLY tools only. Write tools are called by deterministic graph nodes.
AGENT_TOOLS: dict[str, set[str]] = {
    "order": {"get_order_status", "get_payment_status"},
    "shipment": {"get_shipment_status"},
    "policy": {"ask_policy", "get_policy_version"},   # eligibility is decided by code, not by a model-chosen tool call
    "product": {"search_products", "check_inventory"},
}
WRITE_TOOLS = {"create_approval_request", "initiate_refund", "reserve_inventory", "release_inventory",
               "create_replacement_order", "create_carrier_case", "notify_customer", "create_support_task"}
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
                if problem:
                    log.append({"agent": agent, "tool": name, "args": kwargs, "ok": False, "error": problem})
                    return f"ERROR {problem}"
                try:
                    result = await base.ainvoke(kwargs)
                    log.append({"agent": agent, "tool": name, "args": kwargs, "ok": True, "result": result})
                    return _as_text(result)
                except Exception as e:  # noqa: BLE001 - agent should see the failure, not crash
                    log.append({"agent": agent, "tool": name, "args": kwargs, "ok": False, "error": str(e)})
                    return f"ERROR {e}"
            return StructuredTool.from_function(coroutine=run, name=name, description=base.description,
                                                args_schema=base.args_schema)
        wrapped.append(make(base, name))
    return wrapped


def _as_text(result: Any) -> str:
    if isinstance(result, list):
        return "".join(b.get("text", "") for b in result if isinstance(b, dict))
    return str(result)
