"""Approval panel: pending approvals with the evidence behind them, and approve / reject."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from api_client import ApiError, Identity
from data import approval_rows, icon
from state import open_workflow


def render(api, who: Identity) -> None:
    st.caption("Approvals are decided by a manager. The agents cannot approve their own proposals, and a refund is never executed without one.")
    pending_tab, decided_tab = st.tabs(["⏳ Pending", "History"])
    with pending_tab:
        try:
            pending = approval_rows(api.list_approvals(who, "PENDING"))
        except ApiError as e:
            st.error(f"Could not load approvals: {e}")
            return
        if not pending:
            st.success("Nothing waiting for approval.")
        if not who.can_decide and pending:
            st.info("Sign in as a **manager** (sidebar) to approve or reject.")
        for a in pending:
            _card(api, who, a)
    with decided_tab:
        try:
            done = [a for s in ("APPROVED", "REJECTED", "EXPIRED") for a in api.list_approvals(who, s)]
        except ApiError as e:
            st.error(str(e))
            return
        if done:
            done.sort(key=lambda a: a.get("createdAt") or "", reverse=True)
            st.dataframe(pd.DataFrame([{"": icon(a["status"]), "Approval": a["approvalId"], "Workflow": a["workflowId"], "Action": a["requestedAction"],
                                        "Amount": a.get("amount"), "Risk": a.get("riskLevel"), "Status": a["status"],
                                        "Decided by": a.get("decidedBy") or "", "Reason": (a.get("reason") or "")[:60]} for a in done[:50]]),
                         hide_index=True, width="stretch")
        else:
            st.caption("No decisions yet.")


def _card(api, who: Identity, a: dict) -> None:
    with st.container(border=True):
        left, right = st.columns([3, 1])
        with left:
            st.markdown(f"#### {a['requestedAction'].title()} of {a.get('amount') or '-'}  ·  risk **{a.get('riskLevel') or '-'}**")
            st.markdown(f"Workflow `{a['workflowId']}`  ·  approval `{a['approvalId']}`")
            st.write(a.get("reason") or "")
        with right:
            st.metric("Expires in", a["expires_in"])
            if a["seconds_left"] is not None:
                st.progress(max(0.0, min(1.0, a["seconds_left"] / 1800)))
        with st.expander("Evidence from the agents"):
            _evidence(api, who, a["workflowId"])
        comment = st.text_input("Comment (optional)", key=f"c-{a['approvalId']}", disabled=not who.can_decide)
        b1, b2, b3 = st.columns([1, 1, 3])
        for col, decision, label, kind in ((b1, "APPROVED", "Approve", "primary"), (b2, "REJECTED", "Reject", "secondary")):
            if col.button(label, key=f"{decision}-{a['approvalId']}", type=kind, disabled=not who.can_decide or a["expires_in"] == "expired"):
                try:
                    api.decide(who, a["approvalId"], decision, comment)
                except ApiError as e:
                    st.error(f"Could not record the decision: {e}")
                else:
                    st.toast(f"{'Approved' if decision == 'APPROVED' else 'Rejected'} {a['approvalId']}")
                    st.rerun()
        if b3.button("Open workflow", key=f"open-{a['approvalId']}"):
            open_workflow(a["workflowId"])
            st.session_state["go_to"] = "Workflow explorer"
            st.rerun()


def _evidence(api, who: Identity, workflow_id: str) -> None:
    try:
        run = api.get_run(who, workflow_id)
    except ApiError as e:
        st.caption(f"Run details unavailable: {e}")
        return
    if not run:
        st.caption("The agent host has no run for this workflow.")
        return
    o, s, p, r, d = run.get("order"), run.get("shipment"), run.get("policy"), run.get("risk"), run.get("resolution")
    if o:
        st.write(f"**Order** {o['order_id']}: {o['status']}, paid {o['total_amount']} {o['currency']}")
    if s:
        st.write(f"**Shipment** {s['status']}, {s['delay_days']} days late ({s['exception_type']}), verified: {s['verified']}")
    if p:
        st.write(f"**Policy** {p['policy_key']} v{p['policy_version']}: refund eligible **{p['refund_eligible']}** "
                 f"(evidence confidence {p['rag_confidence']:.0%}, grounded: {p['grounded']})")
        for c in p.get("citations", [])[:3]:
            st.caption(f"cites {c['policy_key']} v{c['version']} ({c.get('source')})")
    if r:
        st.write(f"**Risk** {r['level']}: {', '.join(r['factors'])}")
    if d:
        st.write(f"**Proposal** {d['action']}: {d['reason']}")
