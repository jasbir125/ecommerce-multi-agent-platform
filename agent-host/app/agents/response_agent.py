"""Response Agent: writes the customer-facing message. It has no business tools."""
from langchain_core.messages import HumanMessage, SystemMessage

from app.guardrails.output_guardrails import response_violations
from app.observability import metrics as m

SYSTEM = """You write a short, friendly message (max 3 sentences) to an ecommerce customer about their order request.
State ONLY the outcomes listed under "Verified outcomes", and include every listed refund and replacement id. Never mention a refund or replacement as done unless it is listed.
If everything completed, do not say that a human will follow up.
Do not invent ids, dates or amounts, and never promise a delivery or arrival date or time. If the request needs a human, say a support agent will follow up."""


def template_reply(state) -> str:
    ex, o = state.execution, state.order_id
    if state.status == "COMPLETED" and ex and ex.refund_id:
        extra = f" Your replacement order {ex.replacement_order_id} has been created." if ex.replacement_order_id else ""
        return f"Your refund {ex.refund_id} for order {o} has been initiated.{extra}"
    if state.status == "PARTIALLY_COMPLETED" and ex and ex.refund_id:
        return (f"Your refund {ex.refund_id} for order {o} has been initiated. We could not finish the replacement, "
                "and our support team will contact you.")
    if state.shipment is not None and not state.shipment.verified:
        return f"We could not verify the shipment status for order {o} right now. Our support team will follow up."
    return f"Your request for order {o} needs a manual review. Our support team will contact you."


def outcomes(state) -> str:
    ex = state.execution
    lines = [f"status: {state.status}"]
    if ex:
        if ex.refund_id:
            lines.append(f"refund {ex.refund_id} status {ex.refund_status} for order {state.order_id}")
        if ex.replacement_order_id:
            lines.append(f"replacement order {ex.replacement_order_id} created")
        if ex.compensated:
            lines.append("replacement could not be completed; reserved stock was released")
    if state.status in ("ESCALATED", "REJECTED"):
        lines.append("a human support agent will review the request")
    return "\n".join(lines)


async def run(state, deps, config) -> tuple[str, list[str]]:
    """Returns (message, notes). Falls back to a fixed template if the model's text makes unverified claims."""
    notes = []
    try:
        msg = await deps.models.structured("response", _Reply, [
            SystemMessage(SYSTEM), HumanMessage(f"Verified outcomes:\n{outcomes(state)}")], config)
        text = msg.message.strip()
        problems = response_violations(text, state.execution, state.order_id, state.status, state.approval_status)
        if not problems:
            return text, notes
        notes.append(f"GUARDRAIL_RESPONSE_REJECTED: {', '.join(problems)}")
        for code in problems:
            m.guardrail("output", code.split("_ID_")[0] if code.startswith("FABRICATED_ID") else code)
    except Exception as e:  # noqa: BLE001
        if "BudgetExceeded" in type(e).__name__:
            raise
        notes.append(f"RESPONSE_MODEL_ERROR: {str(e)[:100]}")
    return template_reply(state), notes


from pydantic import BaseModel  # noqa: E402


class _Reply(BaseModel):
    message: str
