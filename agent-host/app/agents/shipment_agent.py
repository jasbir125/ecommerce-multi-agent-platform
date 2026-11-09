"""Shipment Agent: reads carrier tracking and identifies delays and lost parcels.

Read-only: it creates no carrier case, issues no refund and changes no order. If the carrier cannot be reached the
shipment stays unverified (`verified=False`), and the rest of the workflow treats that as "do not act".
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from app.agents.base import (DETAIL_CHARS, MODEL_ERRORS, TOOL_ERRORS, AgentInputError, AgentRun, AgentUpdate, describe,
                             run_react, tool_results)
from app.models.shipment import ShipmentSnapshot

log = logging.getLogger("agent.shipment")

# ---------------------------------------------------------------------------------------------- constants
AGENT_NAME = "shipment"
SHIPMENT_TOOL = "get_shipment_status"
NO_EXCEPTION = "NONE"
KNOWN_EXCEPTIONS = {"NONE", "DELAYED", "LOST"}
SYSTEM_PROMPT = "You are the Shipment Agent. Check the carrier tracking status for the customer's order."


# -------------------------------------------------------------------------------------------------- models
class ShipmentRequest(BaseModel):
    order_id: str


class ShipmentResult(AgentUpdate):
    shipment: ShipmentSnapshot | None = None


class ShipmentLookupError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ------------------------------------------------------------------------------------------------- run()
async def run(state, deps, config) -> dict[str, Any]:
    timer = AgentRun(AGENT_NAME, state)
    notes: list[str] = []
    try:
        request = validate_input(state)
    except AgentInputError as e:
        return timer.finish(ShipmentResult(errors=[e.code])).to_update()
    ctx = deps.run(state)
    summary = await invoke_tools_or_agent(deps, ctx, request, config, notes, timer)
    try:
        raw = await find_shipment_facts(ctx, request, notes)
        shipment = normalize_tool_results(raw, request)
        apply_business_rules(shipment)
    except ShipmentLookupError as e:
        return timer.finish(await handle_error(e, state, deps, notes, timer)).to_update()
    return timer.finish(build_result(shipment, notes, summary)).to_update()


# ---------------------------------------------------------------------------------------- validate_input()
def validate_input(state) -> ShipmentRequest:
    if not state.order_id:
        raise AgentInputError("SHIPMENT_SKIPPED_MISSING_ORDER_ID")
    return ShipmentRequest(order_id=state.order_id)


# --------------------------------------------------------------------------------------- prepare_context()
def prepare_context(request: ShipmentRequest) -> str:
    return f"Get the shipment status for order {request.order_id}."


# ------------------------------------------------------------------------------- invoke_tools_or_agent()
async def invoke_tools_or_agent(deps, ctx, request: ShipmentRequest, config, notes: list[str], timer: AgentRun) -> str:
    try:
        summary, _ = await run_react(deps, ctx, AGENT_NAME, SYSTEM_PROMPT, prepare_context(request), config)
    except MODEL_ERRORS as e:
        notes.append(f"SHIPMENT_AGENT_ERROR: {describe(e)}")
        timer.failed("SHIPMENT_AGENT_ERROR", e)
        return ""
    return summary


async def find_shipment_facts(ctx, request: ShipmentRequest, notes: list[str]) -> dict[str, Any]:
    """The tool result for this order; if the model skipped the lookup, read it directly."""
    results = [r for r in tool_results(ctx, AGENT_NAME, SHIPMENT_TOOL) if isinstance(r, dict)]
    if results:
        return results[-1]
    try:
        facts = await ctx.gateway.call(SHIPMENT_TOOL, {"order_id": request.order_id})
    except TOOL_ERRORS as e:
        raise ShipmentLookupError(str(e)) from e
    notes.append("SHIPMENT_LOOKUP_DIRECT")
    return facts


# ------------------------------------------------------------------------------- normalize_tool_results()
def normalize_tool_results(raw: dict[str, Any], request: ShipmentRequest) -> ShipmentSnapshot:
    if raw.get("orderId") != request.order_id:
        raise ShipmentLookupError(f"tool returned shipment for {raw.get('orderId')}, not {request.order_id}")
    try:
        return ShipmentSnapshot(order_id=raw["orderId"], verified=True, carrier=raw.get("carrier"), tracking_no=raw.get("trackingNo"),
                                status=raw.get("status"), delay_days=int(raw.get("delayDays") or 0),
                                exception_type=raw.get("exceptionType") or NO_EXCEPTION, last_event=raw.get("lastEvent"))
    except (TypeError, ValueError) as e:
        raise ShipmentLookupError(f"shipment data malformed: {e}") from e


# ------------------------------------------------------------------------------------ apply_business_rules()
def apply_business_rules(shipment: ShipmentSnapshot) -> None:
    """Delay days cannot be negative and the exception type must be one we understand; otherwise it is unverified."""
    if shipment.delay_days < 0:
        raise ShipmentLookupError("negative delay days")
    if shipment.exception_type not in KNOWN_EXCEPTIONS:
        raise ShipmentLookupError(f"unknown exception type {shipment.exception_type}")


# ----------------------------------------------------------------------------------------- build_evidence()
def build_evidence(shipment: ShipmentSnapshot) -> list[str]:
    return [f"shipment {shipment.status}, {shipment.delay_days} days late, exception={shipment.exception_type}"]


# --------------------------------------------------------------------------------------------- build_result()
def build_result(shipment: ShipmentSnapshot, notes: list[str], summary: str) -> ShipmentResult:
    return ShipmentResult(shipment=shipment, errors=notes, evidence=build_evidence(shipment), detail=summary[:DETAIL_CHARS])


# -------------------------------------------------------------------------------------------- handle_error()
async def handle_error(error: ShipmentLookupError, state, deps, notes: list[str], timer: AgentRun) -> ShipmentResult:
    """The carrier could not be verified: record an unverified shipment so later steps know not to act on it."""
    timer.failed("SHIPMENT_UNVERIFIED", error, tool=SHIPMENT_TOOL)
    await deps.workflows.audit(state.workflow_id, "shipment-agent", "SHIPMENT_UNVERIFIED", {"error": error.reason[:200]}, state.customer_id)
    return ShipmentResult(shipment=ShipmentSnapshot(order_id=state.order_id, verified=False),
                          errors=[f"SHIPMENT_UNVERIFIED: {error.reason[:160]}", *notes])
