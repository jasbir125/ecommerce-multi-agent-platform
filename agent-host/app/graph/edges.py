"""Routing decisions between nodes."""
from app.models.workflow_state import WorkflowState


def after_input_guard(state: WorkflowState) -> str:
    return "response" if state.status == "ESCALATED" else "supervisor"


def after_supervisor(state: WorkflowState) -> list[str]:
    """Wave 1 runs the independent lookups in parallel; anything unsupported goes to a human."""
    if state.intent != "DELAYED_ORDER_RECOVERY":
        return ["unsupported_intent"]
    return ["order", "shipment"]


def after_decision(state: WorkflowState) -> str:
    d = state.resolution
    if d is None or d.action in ("ESCALATE",):
        return "escalate"
    if d.action == "NO_ACTION":
        return "response"
    return "approval_request"          # any refund needs a human


def after_approval_request(state: WorkflowState) -> str:
    return "approval_wait" if state.approval_id else "escalate"


def after_approval_wait(state: WorkflowState) -> str:
    return "execute" if state.approval_status == "APPROVED" else "handle_no_approval"
