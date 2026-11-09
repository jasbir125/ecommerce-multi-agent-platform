"""Order Agent: reads the order and payment state. It never cancels an order or issues a refund.

Every fact in the snapshot comes from the order tool; the model only writes a short summary for the trace.
Customer ownership is enforced by the order service (NOT_ORDER_OWNER), and we also check that the order the tool
returned is the one that was asked for.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.agents.base import (DETAIL_CHARS, MODEL_ERRORS, TOOL_ERRORS, AgentInputError, AgentRun, AgentUpdate, describe,
                             run_react, tool_results)
from app.models.money import DEFAULT_CURRENCY, to_money
from app.models.order import OrderSnapshot

log = logging.getLogger("agent.order")

# ---------------------------------------------------------------------------------------------- constants
AGENT_NAME = "order"
ORDER_TOOL = "get_order_status"
OWNERSHIP_ERROR_MARKER = "NOT_ORDER_OWNER"
REQUIRED_FIELDS = ("orderId", "status", "paymentStatus", "totalAmount")
SYSTEM_PROMPT = "You are the Order Agent for an ecommerce order-recovery team. Look up the customer's order and its payment state."


# -------------------------------------------------------------------------------------------------- models
class OrderRequest(BaseModel):
    order_id: str
    customer_id: str


class OrderResult(AgentUpdate):
    order: OrderSnapshot | None = None
    tool_errors: list[str] = []           # failed order-tool calls, for the trace only

    def to_update(self) -> dict[str, Any]:
        update = super().to_update()
        update.pop("tool_errors", None)
        update["_tool_errors"] = self.tool_errors
        return update


class OrderLookupError(Exception):
    """The order could not be verified; `code` goes to the workflow errors list."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


# ------------------------------------------------------------------------------------------------- run()
async def run(state, deps, config) -> dict[str, Any]:
    timer = AgentRun(AGENT_NAME, state)
    notes: list[str] = []
    try:
        request = validate_input(state)
    except AgentInputError as e:
        return timer.finish(OrderResult(errors=[e.code])).to_update()
    ctx = deps.run(state)
    summary = await invoke_tools_or_agent(deps, ctx, request, config, notes, timer)
    try:
        raw = await find_order_facts(ctx, request, notes)
        order = normalize_tool_results(raw)
        apply_business_rules(order, request)
    except OrderLookupError as e:
        return timer.finish(await handle_error(e, state, deps, notes, timer)).to_update()
    return timer.finish(build_result(order, notes, summary, ctx)).to_update()


# ---------------------------------------------------------------------------------------- validate_input()
def validate_input(state) -> OrderRequest:
    if not state.order_id or not state.customer_id:
        raise AgentInputError("ORDER_SKIPPED_MISSING_IDS")
    return OrderRequest(order_id=state.order_id, customer_id=state.customer_id)


# --------------------------------------------------------------------------------------- prepare_context()
def prepare_context(request: OrderRequest) -> str:
    return f"Get the status and payment state of order {request.order_id}."


# ------------------------------------------------------------------------------- invoke_tools_or_agent()
async def invoke_tools_or_agent(deps, ctx, request: OrderRequest, config, notes: list[str], timer: AgentRun) -> str:
    """Let the model look the order up. Returns its summary; a model failure is recorded, not fatal."""
    try:
        summary, _ = await run_react(deps, ctx, AGENT_NAME, SYSTEM_PROMPT, prepare_context(request), config)
    except MODEL_ERRORS as e:
        notes.append(f"ORDER_AGENT_ERROR: {describe(e)}")
        timer.failed("ORDER_AGENT_ERROR", e)
        return ""
    return summary


async def find_order_facts(ctx, request: OrderRequest, notes: list[str]) -> dict[str, Any]:
    """The tool result for this order. If the model skipped the lookup, read it directly: facts must come from the service."""
    results = [r for r in tool_results(ctx, AGENT_NAME, ORDER_TOOL) if isinstance(r, dict)]
    if results:
        return results[-1]
    try:
        facts = await ctx.gateway.call(ORDER_TOOL, {"order_id": request.order_id})
    except TOOL_ERRORS as e:
        raise OrderLookupError(classify_lookup_failure(str(e)), str(e)) from e
    notes.append("ORDER_LOOKUP_DIRECT")
    return facts


def classify_lookup_failure(reason: str) -> str:
    return "ORDER_ACCESS_DENIED" if OWNERSHIP_ERROR_MARKER in reason else "ORDER_UNVERIFIED"


# ------------------------------------------------------------------------------- normalize_tool_results()
def normalize_tool_results(raw: dict[str, Any]) -> OrderSnapshot:
    """The order tool's JSON as a typed snapshot. Missing or malformed fields mean the order is unverified."""
    missing = [name for name in REQUIRED_FIELDS if raw.get(name) is None]
    if missing:
        raise OrderLookupError("ORDER_UNVERIFIED", f"order data missing {', '.join(missing)}")
    try:
        return OrderSnapshot(order_id=raw["orderId"], status=raw["status"], payment_status=raw["paymentStatus"],
                             total_amount=to_money(raw["totalAmount"]), currency=raw.get("currency") or DEFAULT_CURRENCY,
                             product_id=raw.get("productId"), product_name=raw.get("productName"), quantity=raw.get("quantity"))
    except ValueError as e:
        raise OrderLookupError("ORDER_UNVERIFIED", f"order data malformed: {e}") from e


# ------------------------------------------------------------------------------------ apply_business_rules()
def apply_business_rules(order: OrderSnapshot, request: OrderRequest) -> None:
    """The returned order must be the requested one, with a positive amount."""
    if order.order_id != request.order_id:
        raise OrderLookupError("ORDER_UNVERIFIED", f"tool returned order {order.order_id}, not {request.order_id}")
    if order.total_amount <= 0:
        raise OrderLookupError("ORDER_UNVERIFIED", "order amount is not positive")


# ----------------------------------------------------------------------------------------- build_evidence()
def build_evidence(order: OrderSnapshot) -> list[str]:
    return [f"order {order.order_id}: {order.status}, paid {order.total_amount} {order.currency}"]


# --------------------------------------------------------------------------------------------- build_result()
def build_result(order: OrderSnapshot, notes: list[str], summary: str, ctx) -> OrderResult:
    return OrderResult(order=order, errors=notes, evidence=build_evidence(order), detail=summary[:DETAIL_CHARS],
                       tool_errors=[e.get("error", "") for e in ctx.tool_log
                                    if e["agent"] == AGENT_NAME and e["tool"] == ORDER_TOOL and not e.get("ok")])


# -------------------------------------------------------------------------------------------- handle_error()
async def handle_error(error: OrderLookupError, state, deps, notes: list[str], timer: AgentRun) -> OrderResult:
    """No verified order: audit the failure, log the original reason, and return a coded error (no order is written)."""
    timer.failed(error.code, error, tool=ORDER_TOOL)
    await deps.workflows.audit(state.workflow_id, "order-agent", "ORDER_LOOKUP_FAILED", {"error": error.reason[:200]}, state.customer_id)
    return OrderResult(errors=[f"{error.code}: {error.reason[:160]}", *notes])
