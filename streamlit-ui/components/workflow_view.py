"""Everything about one workflow in tabs: timeline, agent trace, RAG evidence, Kafka events, audit trail."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from api_client import ApiError, Identity, SessionExpired
from components import kafka_event_viewer, rag_evidence, trace_viewer, workflow_timeline
from data import audit_rows, icon, is_terminal, timeline_rows


def load(api, who: Identity, workflow_id: str) -> dict:
    """Fetch every view of the workflow. Each call can fail on its own (e.g. no agent run yet); failures become None."""
    out: dict = {"workflow": api.get_workflow(who, workflow_id), "run": None, "agent_trace": None, "wf_trace": None}
    for key, fn in (("run", api.get_run), ("agent_trace", api.run_trace), ("wf_trace", api.workflow_trace)):
        try:
            out[key] = fn(who, workflow_id)
        except ApiError as e:
            out[f"{key}_error"] = str(e)
    return out


def render(api, who: Identity, workflow_id: str, key: str = "wf") -> None:
    """Auto-refreshing view: polls every 3 seconds until the workflow reaches a final state."""
    status = st.session_state.get(f"{key}-status")
    body = st.fragment(run_every=None if is_terminal(status) else 3)(_body)
    body(api, who, workflow_id, key)


def _body(api, who: Identity, workflow_id: str, key: str) -> None:
    try:
        data = load(api, who, workflow_id)
    except SessionExpired:
        # This fragment refreshes on its own timer, outside the page's error handling: sign the user out here.
        st.session_state.pop("auth", None)
        st.session_state["login_notice"] = "Your session expired. Please sign in again."
        st.rerun(scope="app")
        return
    except ApiError as e:
        st.error(f"Cannot open {workflow_id}: {e}")
        return
    wf, run = data["workflow"], data["run"]
    state = (run or {}).get("status") or wf["status"]
    previous = st.session_state.get(f"{key}-status")
    st.session_state[f"{key}-status"] = state
    if previous and previous != state and is_terminal(state):
        st.rerun()                                   # stop polling and show the final state

    _header(wf, run, state)
    trace = (data["agent_trace"] or {}).get("trace", [])
    t_time, t_trace, t_rag, t_kafka, t_audit = st.tabs(["⏱ Timeline", "🧭 Agent trace", "📚 RAG evidence", "📨 Kafka events", "🧾 Audit trail"])
    with t_time:
        workflow_timeline.render(timeline_rows(trace))
        history = (data["wf_trace"] or {}).get("history", [])
        if history:
            with st.expander("Workflow state history (system of record)"):
                st.dataframe(pd.DataFrame([{"status": h.get("status"), "at": h.get("at"), "reason": h.get("reason")} for h in history]),
                             hide_index=True, width="stretch")
    with t_trace:
        if run:
            trace_viewer.render(run, trace)
        else:
            st.info("The agent host has not started this workflow yet.")
    with t_rag:
        rag_evidence.render(((run or {}).get("policy") or {}).get("rag"), key=f"{key}-rag")
    with t_kafka:
        events = (data["wf_trace"] or {}).get("kafkaEvents", [])
        kafka_event_viewer.render(events, key=f"{key}-kafka")
    with t_audit:
        audit = (data["wf_trace"] or {}).get("auditEvents", [])
        rows = audit_rows(audit)
        if rows:
            st.dataframe(pd.DataFrame(rows).drop(columns=["payload"]), hide_index=True, width="stretch")
            with st.expander("Event details"):
                for a in audit:
                    st.caption(f"{a.get('eventType')}: {a.get('payload')}")
        else:
            st.info("No audit events.")


def _header(wf: dict, run: dict | None, state: str) -> None:
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    c1.markdown(f"### {icon(state)} {state.replace('_', ' ').title()}")
    c1.caption(f"Workflow `{wf['workflowId']}`  ·  order `{wf['orderId']}`  ·  customer `{wf['customerId']}`")
    if not run:
        st.info("Queued: waiting for the agent host to pick this request up from Kafka…")
        return
    if run.get("waitingForApproval"):
        st.warning(f"⏳ Waiting for a manager to approve approval `{run.get('approvalId')}`. Nothing has been executed.")
    ex = run.get("execution") or {}
    c2.metric("Refund", ex.get("refund_status") or "-", help=ex.get("refund_id") or None)
    c3.metric("Replacement", "created" if ex.get("replacement_order_id") else "-", help=ex.get("replacement_order_id") or None)
    risk = (run.get("risk") or {}).get("level")
    c4.metric("Risk", risk or "-")
    if run.get("customerResponse"):
        st.chat_message("assistant").write(run["customerResponse"])
    for e in run.get("errors") or []:
        if e.startswith(("GUARDRAIL", "INPUT_REJECTED", "UNSUPPORTED", "SHIPMENT_UNVERIFIED", "DECISION_ESCALATED", "APPROVAL_")):
            st.warning(e)
