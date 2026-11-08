"""Graph nodes. LLM agents investigate and propose; deterministic nodes ask for approval and execute."""
from __future__ import annotations

import asyncio
import logging

from langgraph.types import interrupt

from app.agents import decision_agent, order_agent, policy_agent, product_agent, response_agent, risk_agent, shipment_agent, supervisor
from app.graph.deps import deps_of
from app.graph.tracing import traced
from app.guardrails.input_guardrails import check_request
from app.guardrails.tool_guardrails import ToolCallError
from app.observability import metrics as m
from app.models.resolution import ExecutionResult, StepResult

log = logging.getLogger("agent.nodes")


# --------------------------------------------------------------------------- entry
@traced("input_guard")
async def input_guard(state, config) -> dict:
    deps = deps_of(config)
    check = check_request(state.customer_message, state.customer_id, state.order_id, deps.settings.max_message_length)
    if check.ok:
        return {}
    for v in check.violations:
        m.guardrail("input", v)
    await deps.workflows.audit(state.workflow_id, "input-guardrails", "GUARDRAIL_INPUT_REJECTED",
                               {"violations": check.violations}, state.customer_id)
    await deps.workflows.transition(state.workflow_id, state.customer_id, "agent-input-rejected", "ESCALATED",
                                    "input guardrails: " + ",".join(check.violations))
    return {"status": "ESCALATED", "errors": [f"INPUT_REJECTED: {','.join(check.violations)}"]}


@traced("supervisor")
async def supervisor_node(state, config) -> dict:
    deps = deps_of(config)
    await deps.workflows.transition(state.workflow_id, state.customer_id, "agent-investigating", "INVESTIGATING", "agent host started")
    update = await supervisor.run(state, deps, config)
    update["status"] = "INVESTIGATING"
    update["usage"] = deps.run(state).usage.summary()
    return update


@traced("unsupported_intent")
async def unsupported_intent(state, config) -> dict:
    deps = deps_of(config)
    await deps.workflows.audit(state.workflow_id, "supervisor", "UNSUPPORTED_INTENT", {"intent": state.intent}, state.customer_id)
    await deps.workflows.transition(state.workflow_id, state.customer_id, "agent-unsupported", "ESCALATED", f"unsupported intent {state.intent}")
    return {"status": "ESCALATED", "errors": [f"UNSUPPORTED_INTENT: {state.intent}"]}


# ------------------------------------------------------------------ investigation
@traced("order")
async def order_node(state, config) -> dict:
    return await order_agent.run(state, deps_of(config), config)


@traced("shipment")
async def shipment_node(state, config) -> dict:
    return await shipment_agent.run(state, deps_of(config), config)


async def wave2_barrier(state, config) -> dict:
    """Join point: policy/product/risk need the order and shipment facts first."""
    return {}


@traced("policy")
async def policy_node(state, config) -> dict:
    return await policy_agent.run(state, deps_of(config), config)


@traced("product")
async def product_node(state, config) -> dict:
    return await product_agent.run(state, deps_of(config), config)


@traced("risk")
async def risk_node(state, config) -> dict:
    return await risk_agent.run(state, deps_of(config), config)


@traced("decision")
async def decision_node(state, config) -> dict:
    deps = deps_of(config)
    update = await decision_agent.run(state, deps, config)
    update["usage"] = deps.run(state).usage.summary()
    d = update["resolution"]
    await deps.workflows.audit(state.workflow_id, "decision-agent", "AGENT_DECISION",
                               {"action": d.action, "refund": d.refund_amount, "replacement": d.replacement_product_id,
                                "risk": d.risk_level}, state.customer_id)
    return update


# ---------------------------------------------------------------------- approval
@traced("approval_request")
async def approval_request(state, config) -> dict:
    """Ask a human to approve. Runs once; the wait happens in the next node."""
    deps, d = deps_of(config), state.resolution
    gw = deps.run(state).gateway
    try:
        apr = await gw.call("create_approval_request", {
            "workflow_id": state.workflow_id, "requested_action": "REFUND", "amount": d.refund_amount,
            "reason": d.reason[:400], "risk_level": d.risk_level})
    except ToolCallError as e:
        return {"status": "ESCALATED", "errors": [f"APPROVAL_REQUEST_FAILED: {str(e)[:160]}"]}
    await deps.workflows.transition(state.workflow_id, state.customer_id, "agent-waiting-approval", "WAITING_FOR_APPROVAL",
                                    f"approval {apr['approvalId']} requested", {"approvalId": apr["approvalId"]})
    return {"approval_id": apr["approvalId"], "approval_status": "PENDING", "status": "WAITING_FOR_APPROVAL",
            "_detail": f"approval {apr['approvalId']} for {d.refund_amount}"}


@traced("approval_wait")
async def approval_wait(state, config) -> dict:
    """Pause the graph until the approval is decided. The status is always re-read from the approval
    service; a resume request can only wake the graph, it can never claim an outcome."""
    deps = deps_of(config)
    gw = deps.run(state).gateway
    while True:
        try:
            a = await gw.call("get_approval_status", {"approval_id": state.approval_id})
        except ToolCallError as e:
            return {"approval_status": "UNKNOWN", "errors": [f"APPROVAL_STATUS_UNAVAILABLE: {str(e)[:120]}"]}
        status = a.get("status", "UNKNOWN")
        if status != "PENDING":
            _observe_approval(a, status)
            return {"approval_status": status, "_detail": f"approval {status} by {a.get('decidedBy')}"}
        interrupt({"waiting_for": "approval", "approval_id": state.approval_id})


def _observe_approval(a: dict, status: str) -> None:
    """Human decision time, from the approval service's own timestamps."""
    from datetime import datetime
    try:
        created, decided = a.get("createdAt"), a.get("decidedAt")
        if created and decided:
            secs = (datetime.fromisoformat(decided) - datetime.fromisoformat(created)).total_seconds()
            m.APPROVAL_WAIT.labels(status).observe(max(0.0, secs))
    except (ValueError, TypeError):
        pass


# ---------------------------------------------------------------------- execution
async def _wait_ready(deps, state, attempts: int = 20) -> bool:
    """workflow-service moves to READY_FOR_EXECUTION itself when the approval event arrives."""
    for _ in range(attempts):
        wf = await deps.workflows.get(state.workflow_id, state.customer_id)
        if wf["status"] == "READY_FOR_EXECUTION":
            return True
        await asyncio.sleep(0.5)
    return False


@traced("execute")
async def execute(state, config) -> dict:
    """Deterministic execution of an APPROVED resolution via the MCP write tools. No model is involved.
    Every call uses a stable idempotency key, so re-running this node never duplicates an action."""
    deps, d = deps_of(config), state.resolution
    gw, wf, cust = deps.run(state).gateway, state.workflow_id, state.customer_id
    if state.approval_status != "APPROVED":
        return {"errors": ["EXECUTION_BLOCKED_NOT_APPROVED"]}
    if not await _wait_ready(deps, state):
        return {"status": "FAILED", "errors": ["WORKFLOW_NOT_READY_FOR_EXECUTION"]}

    ex, steps = ExecutionResult(), []

    def done(step: str, ok: bool, detail: str = "", **data):
        steps.append(StepResult(step=step, ok=ok, detail=detail[:200], data=data))
        return ok

    if state.shipment and state.shipment.exception_type != "NONE":
        try:
            case = await gw.call("create_carrier_case", {"workflow_id": wf, "order_id": state.order_id,
                                                         "reason": state.shipment.exception_type})
            ex.carrier_case_id = case.get("caseId")
            done("carrier_case", True, case.get("caseId", ""))
        except ToolCallError as e:
            done("carrier_case", False, str(e))     # not fatal: the refund does not depend on it

    final, reason = "COMPLETED", "recovery executed"
    try:
        refund = await gw.call("initiate_refund", {
            "workflow_id": wf, "order_id": state.order_id, "amount": d.refund_amount,
            "approval_id": state.approval_id, "idempotency_key": f"rf-{wf}"})
        ex.refund_id, ex.refund_status = refund.get("refundId"), refund.get("status")
        done("refund", ex.refund_status in ("INITIATED", "COMPLETED"), f"{ex.refund_id} {ex.refund_status}")
    except ToolCallError as e:
        done("refund", False, str(e))
        ex.steps = steps
        await gw.call("create_support_task", {"workflow_id": wf, "summary": f"Refund failed for {state.order_id}: {str(e)[:200]}"})
        await deps.workflows.transition(wf, cust, "agent-exec-failed", "FAILED", "refund failed")
        return {"execution": ex, "status": "FAILED", "errors": [f"REFUND_FAILED: {str(e)[:160]}"]}

    if d.action == "REFUND_AND_REPLACE" and d.replacement_product_id:
        try:
            rsv = await gw.call("reserve_inventory", {"workflow_id": wf, "product_id": d.replacement_product_id,
                                                      "quantity": 1, "idempotency_key": f"res-{wf}"})
            ex.reservation_id = rsv.get("reservationId")
            done("reserve_inventory", True, ex.reservation_id or "")
            try:
                rep = await gw.call("create_replacement_order", {
                    "workflow_id": wf, "original_order_id": state.order_id,
                    "reservation_id": ex.reservation_id, "idempotency_key": f"rep-{wf}"})
                ex.replacement_order_id = rep.get("replacementOrderId")
                done("replacement_order", True, ex.replacement_order_id or "")
            except ToolCallError as e:
                # Compensation: give the stock back. The refund stays recorded.
                done("replacement_order", False, str(e))
                await deps.workflows.transition(wf, cust, "agent-compensating", "COMPENSATING", "replacement failed")
                try:
                    await gw.call("release_inventory", {"workflow_id": wf, "reservation_id": ex.reservation_id})
                    ex.compensated = True
                    done("release_inventory", True, ex.reservation_id or "")
                except ToolCallError as e2:
                    done("release_inventory", False, str(e2))
                final, reason = "PARTIALLY_COMPLETED", f"replacement failed: {str(e)[:100]}"
        except ToolCallError as e:
            done("reserve_inventory", False, str(e))
            final, reason = "PARTIALLY_COMPLETED", f"reservation failed: {str(e)[:100]}"

    if final == "PARTIALLY_COMPLETED":
        try:
            await gw.call("create_support_task", {"workflow_id": wf, "summary": f"{state.order_id}: refund done, {reason}. Manual follow-up needed."})
        except ToolCallError as e:
            log.warning("support task failed: %s", e)

    ex.steps = steps
    await deps.workflows.transition(wf, cust, "agent-exec-final", final, reason, {"execution": ex.model_dump()})
    return {"execution": ex, "status": final, "_detail": reason}


@traced("handle_no_approval")
async def handle_no_approval(state, config) -> dict:
    """Approval was rejected, expired or could not be read: nothing is executed."""
    deps = deps_of(config)
    status = state.approval_status or "UNKNOWN"
    target = "REJECTED" if status == "REJECTED" else "ESCALATED"
    await deps.workflows.transition(state.workflow_id, state.customer_id, f"agent-approval-{status.lower()}", target, f"approval {status}")
    try:
        await deps.run(state).gateway.call("create_support_task", {
            "workflow_id": state.workflow_id, "summary": f"{state.order_id}: approval {status}; customer needs manual follow-up."})
    except ToolCallError:
        pass
    return {"status": target, "errors": [f"APPROVAL_{status}"]}


@traced("escalate")
async def escalate(state, config) -> dict:
    """The agents could not safely propose an action: hand the case to a human."""
    deps = deps_of(config)
    why = state.resolution.reason if state.resolution else "unknown"
    await deps.workflows.transition(state.workflow_id, state.customer_id, "agent-escalated", "ESCALATED", why[:200])
    try:
        await deps.run(state).gateway.call("create_support_task", {
            "workflow_id": state.workflow_id, "summary": f"{state.order_id} escalated: {why[:300]}"})
    except ToolCallError:
        pass
    return {"status": "ESCALATED"}


# ----------------------------------------------------------------------- response
@traced("response")
async def response_node(state, config) -> dict:
    deps = deps_of(config)
    gw = deps.run(state).gateway
    text, notes = await response_agent.run(state, deps, config)
    ex, wf = state.execution, state.workflow_id
    sent: list[str] = []

    async def notify(template: str, **params):
        try:
            await gw.call("notify_customer", {"workflow_id": wf, "template": template, "params": params})
            sent.append(template)
        except ToolCallError as e:
            notes.append(f"NOTIFY_FAILED_{template}: {str(e)[:100]}")

    if state.status in ("COMPLETED", "PARTIALLY_COMPLETED") and ex and ex.refund_id:
        await notify("REFUND_INITIATED", refundId=ex.refund_id, orderId=state.order_id)
        if ex.replacement_order_id:
            await notify("REPLACEMENT_CREATED", replacementOrderId=ex.replacement_order_id)
    elif state.shipment is not None and not state.shipment.verified:
        await notify("SHIPMENT_VERIFICATION_FAILED", orderId=state.order_id)
    elif state.status in ("ESCALATED", "REJECTED", "FAILED"):
        await notify("MANUAL_REVIEW", orderId=state.order_id)
    await deps.workflows.audit(wf, "response-agent", "CUSTOMER_RESPONSE", {"status": state.status, "notifications": sent}, state.customer_id)
    return {"customer_response": text, "errors": notes, "usage": deps.run(state).usage.summary(), "_detail": f"sent {sent}"}
