"""Order Agent: reads the order and payment state."""
from app.agents.base import run_react, tool_errors, tool_results
from app.guardrails.tool_guardrails import ToolCallError
from app.models.order import OrderSnapshot

SYSTEM = "You are the Order Agent for an ecommerce order-recovery team. Look up the customer's order and its payment state."


def _snapshot(d: dict) -> OrderSnapshot:
    return OrderSnapshot(order_id=d["orderId"], status=d["status"], payment_status=d["paymentStatus"],
                         total_amount=float(d["totalAmount"]), currency=d.get("currency") or "USD",
                         product_id=d.get("productId"), product_name=d.get("productName"), quantity=d.get("quantity"))


async def run(state, deps, config) -> dict:
    ctx = deps.run(state)
    summary, notes = "", []
    try:
        summary, _ = await run_react(deps, ctx, "order", SYSTEM,
                                     f"Get the status and payment state of order {state.order_id}.", config)
    except Exception as e:  # noqa: BLE001
        notes.append(f"ORDER_AGENT_ERROR: {e}")
    results = tool_results(ctx, "order", "get_order_status")
    if not results:
        # Safety net: the model skipped the lookup, so read the order directly. Facts must come from the service.
        try:
            results = [await ctx.gateway.call("get_order_status", {"order_id": state.order_id})]
            notes.append("ORDER_LOOKUP_DIRECT")
        except ToolCallError as e:
            reason = str(e)
            await deps.workflows.audit(state.workflow_id, "order-agent", "ORDER_LOOKUP_FAILED", {"error": reason[:200]}, state.customer_id)
            code = "ORDER_ACCESS_DENIED" if "NOT_ORDER_OWNER" in reason else "ORDER_UNVERIFIED"
            return {"errors": [f"{code}: {reason[:160]}", *notes]}
    snap = _snapshot(results[-1])
    return {"order": snap, "errors": notes, "evidence": [f"order {snap.order_id}: {snap.status}, paid {snap.total_amount} {snap.currency}"],
            "_detail": summary[:300], "_tool_errors": tool_errors(ctx, "order", "get_order_status")}
