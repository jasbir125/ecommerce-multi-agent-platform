"""Builds the LangGraph workflow.

START -> input_guard -> supervisor -> [order || shipment] -> barrier -> [policy || product || risk]
      -> decision -> approval_request -> approval_wait (pauses) -> execute -> response -> END
Escalation, rejection and unsupported requests skip execution and go straight to response.
"""
from langgraph.graph import END, START, StateGraph

from app.graph import edges, nodes
from app.models.workflow_state import WorkflowState


def build_graph(checkpointer=None):
    g = StateGraph(WorkflowState)
    g.add_node("input_guard", nodes.input_guard)
    g.add_node("supervisor", nodes.supervisor_node)
    g.add_node("unsupported_intent", nodes.unsupported_intent)
    g.add_node("order", nodes.order_node)
    g.add_node("shipment", nodes.shipment_node)
    g.add_node("barrier", nodes.wave2_barrier)
    g.add_node("policy", nodes.policy_node)
    g.add_node("product", nodes.product_node)
    g.add_node("risk", nodes.risk_node)
    g.add_node("decision", nodes.decision_node)
    g.add_node("approval_request", nodes.approval_request)
    g.add_node("approval_wait", nodes.approval_wait)
    g.add_node("execute", nodes.execute)
    g.add_node("handle_no_approval", nodes.handle_no_approval)
    g.add_node("escalate", nodes.escalate)
    g.add_node("response", nodes.response_node)

    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", edges.after_input_guard, ["supervisor", "response"])
    g.add_conditional_edges("supervisor", edges.after_supervisor, ["order", "shipment", "unsupported_intent"])
    g.add_edge("unsupported_intent", "response")
    g.add_edge(["order", "shipment"], "barrier")           # wait for both lookups
    g.add_edge("barrier", "policy")
    g.add_edge("barrier", "product")
    g.add_edge("barrier", "risk")
    g.add_edge(["policy", "product", "risk"], "decision")   # wait for all three
    g.add_conditional_edges("decision", edges.after_decision, ["approval_request", "escalate", "response"])
    g.add_conditional_edges("approval_request", edges.after_approval_request, ["approval_wait", "escalate"])
    g.add_conditional_edges("approval_wait", edges.after_approval_wait, ["execute", "handle_no_approval"])
    g.add_edge("execute", "response")
    g.add_edge("handle_no_approval", "response")
    g.add_edge("escalate", "response")
    g.add_edge("response", END)
    return g.compile(checkpointer=checkpointer)
