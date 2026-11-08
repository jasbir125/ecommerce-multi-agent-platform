"""Customer chat panel: enter customer, order and request, submit, and watch the agents work."""
from __future__ import annotations

import streamlit as st

from api_client import ApiError, Identity
from components import workflow_view
from data import icon, label
from state import open_workflow, selected_workflow

DEFAULT_MESSAGE = ("My order ORD-7842 has been delayed by five days. Cancel the order, refund me, and suggest a cheaper replacement.")


def render(api, who: Identity) -> None:
    st.caption("Submit a delayed-shipment request as a customer. Agents investigate, ask a manager to approve any refund, then execute.")
    with st.form("request"):
        c1, c2, c3 = st.columns([1, 1, 2])
        customer = c1.text_input("Customer ID", value=who.customer_id or "CUST-1001")
        order = c2.text_input("Order ID", value="ORD-7842")
        mode = c3.radio("How to run", ["Live (streamed)", "Queued (via Kafka)"], horizontal=True,
                        help="Live streams each agent step as it happens. Queued creates the workflow and lets the agent host pick it up from Kafka.")
        message = st.text_area("Customer request", value=DEFAULT_MESSAGE, height=90)
        submitted = st.form_submit_button("Submit request", type="primary")

    if submitted:
        customer, order, message = customer.strip(), order.strip(), message.strip()
        if not (customer and order and message):
            st.error("Customer ID, order ID and a request are all required.")
        else:
            customer_who = Identity("customer", customer, who.name)
            try:
                _submit(api, customer_who, order, message, live=mode.startswith("Live"))
            except ApiError as e:
                st.error(f"Request failed: {e}")

    wf = selected_workflow()
    if wf:
        st.divider()
        st.subheader(f"Workflow {wf}")
        workflow_view.render(api, who, wf, key="chat")


def _submit(api, who: Identity, order: str, message: str, live: bool) -> None:
    if not live:
        wf = api.create_workflow(who, order, message)
        open_workflow(wf["workflowId"])
        st.session_state.pop("chat-status", None)
        st.toast(f"Created {wf['workflowId']}")
        return
    with st.status("Agents are working…", expanded=True) as box:
        for ev in api.stream_run(who, order, message):
            kind = ev.get("event")
            if kind == "started":
                open_workflow(ev["workflowId"])
                st.session_state.pop("chat-status", None)
                st.write(f"🚀 Workflow `{ev['workflowId']}` created")
            elif kind == "node":
                t = ev.get("trace") or {}
                took = f" · {t['latency_ms']} ms" if t.get("latency_ms") is not None else ""
                tools = f" · tools: {', '.join(c['tool'] for c in t.get('tool_calls', []))}" if t.get("tool_calls") else ""
                st.write(f"{icon(t.get('status', 'ok'))} {label(ev['node'])}{took}{tools}")
            elif kind == "interrupt":
                st.write("⏳ Paused: waiting for a manager to approve (open **Approvals**)")
            elif kind == "final":
                run = ev["run"]
                box.update(label="Waiting for approval" if run.get("waitingForApproval") else f"Finished: {run.get('status')}",
                           state="running" if run.get("waitingForApproval") else "complete", expanded=False)
            elif kind == "error":
                box.update(label="Failed", state="error")
                st.error(ev.get("message", "unknown error"))
