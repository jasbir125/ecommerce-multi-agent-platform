"""Policy Agent: RAG over policy documents (agent) + deterministic eligibility check (code).

The agent explains policy with citations. Whether a refund is allowed is decided by the
policy-service validation endpoint, which we call directly with the verified facts so a
model can never pass different numbers.
"""
from app.agents.base import run_react, tool_results
from app.guardrails.tool_guardrails import ToolCallError
from app.models.policy import Citation, PolicyDecision

SYSTEM = "You are the Policy Agent. Find the company policy that applies to the customer's situation, with citations."
QUERY = "Is a delayed or lost shipment eligible for a refund or replacement? What approvals are needed?"


async def run(state, deps, config) -> dict:
    ctx = deps.run(state)
    if state.order is None or state.shipment is None:
        return {"errors": ["POLICY_SKIPPED_MISSING_FACTS"]}
    notes, summary = [], ""
    try:
        summary, _ = await run_react(
            deps, ctx, "policy", SYSTEM,
            f"Search the policies to answer: {QUERY} Situation: shipment {state.shipment.exception_type}, "
            f"{state.shipment.delay_days} days late.", config)
    except Exception as e:  # noqa: BLE001
        notes.append(f"POLICY_AGENT_ERROR: {e}")
    searches = tool_results(ctx, "policy", "search_policy")
    if not searches:
        try:
            searches = [await ctx.gateway.call("search_policy", {"query": QUERY})]
            notes.append("POLICY_SEARCH_DIRECT")
        except ToolCallError as e:
            notes.append(f"POLICY_SEARCH_FAILED: {str(e)[:120]}")
    rag = searches[-1] if searches else {}
    try:
        v = await ctx.gateway.call("validate_refund_eligibility", {
            "order_id": state.order_id, "order_amount": state.order.total_amount,
            "refund_amount": state.order.total_amount, "delay_days": state.shipment.delay_days,
            "shipment_status": state.shipment.status or "", "order_status": state.order.status})
    except ToolCallError as e:
        return {"errors": [f"POLICY_VALIDATION_FAILED: {str(e)[:160]}", *notes]}
    sources = [Citation(policy_key=s["policyKey"], version=s["version"], source=s.get("source"), score=float(s.get("rerankScore") or 0))
               for s in rag.get("sources", []) if not s.get("injectionSuspected")]
    injection = bool(rag.get("injection_suspected"))
    if injection:
        await deps.workflows.audit(state.workflow_id, "policy-agent", "PROMPT_INJECTION_SUSPECTED_IN_POLICY",
                                   {"note": "retrieved policy text treated as data"}, state.customer_id)
    decision = PolicyDecision(
        refund_eligible=bool(v.get("refundEligible")), approval_required=bool(v.get("approvalRequired", True)),
        reasons=v.get("reasons", []), policy_key=v.get("policyKey"), policy_version=v.get("policyVersion"),
        rag_confidence=float(rag.get("confidence") or 0), grounded=bool(rag.get("grounded")),
        insufficient_evidence=bool(rag.get("insufficientEvidence", True)), citations=sources,
        summary=(rag.get("answer") or summary)[:600], injection_suspected=injection)
    return {"policy": decision, "errors": notes, "_detail": summary[:300],
            "evidence": [f"policy {decision.policy_key} v{decision.policy_version}: refund_eligible={decision.refund_eligible} {decision.reasons}"]}
