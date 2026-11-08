"""Shipment Agent: reads carrier tracking. If the carrier cannot be reached the status stays unverified."""
from app.agents.base import run_react, tool_results
from app.guardrails.tool_guardrails import ToolCallError
from app.models.shipment import ShipmentSnapshot

SYSTEM = "You are the Shipment Agent. Check the carrier tracking status for the customer's order."


def _snapshot(d: dict) -> ShipmentSnapshot:
    return ShipmentSnapshot(order_id=d["orderId"], verified=True, carrier=d.get("carrier"), tracking_no=d.get("trackingNo"),
                            status=d.get("status"), delay_days=int(d.get("delayDays") or 0),
                            exception_type=d.get("exceptionType") or "NONE", last_event=d.get("lastEvent"))


async def run(state, deps, config) -> dict:
    ctx = deps.run(state)
    notes, summary = [], ""
    try:
        summary, _ = await run_react(deps, ctx, "shipment", SYSTEM, f"Get the shipment status for order {state.order_id}.", config)
    except Exception as e:  # noqa: BLE001
        notes.append(f"SHIPMENT_AGENT_ERROR: {e}")
    results = tool_results(ctx, "shipment", "get_shipment_status")
    if not results:
        try:
            results = [await ctx.gateway.call("get_shipment_status", {"order_id": state.order_id})]
            notes.append("SHIPMENT_LOOKUP_DIRECT")
        except ToolCallError as e:
            await deps.workflows.audit(state.workflow_id, "shipment-agent", "SHIPMENT_UNVERIFIED", {"error": str(e)[:200]}, state.customer_id)
            return {"shipment": ShipmentSnapshot(order_id=state.order_id, verified=False),
                    "errors": [f"SHIPMENT_UNVERIFIED: {str(e)[:160]}", *notes]}
    snap = _snapshot(results[-1])
    return {"shipment": snap, "errors": notes, "_detail": summary[:300],
            "evidence": [f"shipment {snap.status}, {snap.delay_days} days late, exception={snap.exception_type}"]}
